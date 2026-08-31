"""Follower analytics for the LinkedIn pipeline.

Reads a brand's organisation follower statistics (lifetime totals and
per-interval organic/paid gains) and its published-post dates, so follower
growth can be compared against posting cadence instead of eyeballed off the
LinkedIn dashboard chart.

Permissions: the follower-statistics finder needs `rw_organization_admin` on
top of `r_organization_social`. A token carrying only `r_organization_social`
is refused with 403 ACCESS_DENIED (serviceErrorCode 100) — that is a scope
problem, not a bad URN, so it is surfaced as `MissingAnalyticsScope`.
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

import requests

from .brand import Brand

REST_BASE = "https://api.linkedin.com/rest"
# networkSizes is only served on the unversioned v2 surface.
V2_BASE = "https://api.linkedin.com/v2"
LINKEDIN_VERSION = "202601"
PAGE_SIZE = 50
# LinkedIn caps the q=author post listing at roughly this many results.
POST_LISTING_CAP = 365


class MissingAnalyticsScope(RuntimeError):
    """Raised when the brand's token lacks rw_organization_admin."""


@dataclass(frozen=True)
class FollowerGain:
    start: datetime
    organic: int
    paid: int

    @property
    def total(self) -> int:
        return self.organic + self.paid


def _credentials(brand: Brand) -> tuple[str, str]:
    token = os.getenv(brand.token_env, "")
    owner = os.getenv(brand.owner_env, "")
    if not token:
        raise ValueError(f"{brand.token_env} is not set")
    if not owner:
        raise ValueError(f"{brand.owner_env} is not set")
    return token, owner


def _headers(token: str, versioned: bool = True) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if versioned:
        headers["LinkedIn-Version"] = LINKEDIN_VERSION
    return headers


def _get(
    url: str,
    token: str,
    params: Optional[Dict[str, str]] = None,
    raw_query: str = "",
    versioned: bool = True,
) -> dict:
    # Rest.li query values such as timeIntervals=(timeRange:(start:…)) must reach
    # LinkedIn with their parentheses and colons intact; requests would escape
    # them, so those are appended verbatim via raw_query.
    if raw_query:
        url = f"{url}?{raw_query}" if "?" not in url else f"{url}&{raw_query}"
    response = requests.get(
        url, headers=_headers(token, versioned), params=params, timeout=45
    )
    if response.status_code == 403:
        raise MissingAnalyticsScope(
            "LinkedIn refused the follower-statistics call (403). The token needs "
            "rw_organization_admin as well as r_organization_social; re-authorise "
            "the app in the LinkedIn developer portal. Response: "
            f"{response.text[:200]}"
        )
    response.raise_for_status()
    return response.json()


def _ms(moment: datetime) -> int:
    return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000)


def fetch_lifetime_followers(brand: Brand) -> int:
    """Total current follower count for the brand's page."""
    token, owner = _credentials(brand)
    payload = _get(
        f"{V2_BASE}/networkSizes/{urllib.parse.quote(owner, safe='')}",
        token,
        {"edgeType": "CompanyFollowedByMember"},
        versioned=False,
    )
    return int(payload.get("firstDegreeSize", 0))


def fetch_follower_gains(
    brand: Brand,
    start: datetime,
    end: datetime,
    granularity: str = "DAY",
) -> List[FollowerGain]:
    """Organic/paid follower gains per interval between start and end.

    LinkedIn retains roughly 12 months of interval statistics; older ranges come
    back empty rather than as an error.
    """
    if granularity not in ("DAY", "MONTH"):
        raise ValueError("granularity must be DAY or MONTH")
    token, owner = _credentials(brand)
    time_intervals = (
        f"(timeRange:(start:{_ms(start)},end:{_ms(end)}),"
        f"timeGranularityType:{granularity})"
    )
    payload = _get(
        f"{REST_BASE}/organizationalEntityFollowerStatistics",
        token,
        {"q": "organizationalEntity", "organizationalEntity": owner},
        raw_query=f"timeIntervals={time_intervals}",
    )
    gains: List[FollowerGain] = []
    for element in payload.get("elements", []):
        window = element.get("timeRange", {})
        counts = element.get("followerGains", {})
        if not window.get("start"):
            continue
        gains.append(
            FollowerGain(
                start=datetime.fromtimestamp(window["start"] / 1000, tz=timezone.utc),
                organic=int(counts.get("organicFollowerGain", 0) or 0),
                paid=int(counts.get("paidFollowerGain", 0) or 0),
            )
        )
    return sorted(gains, key=lambda gain: gain.start)


def fetch_post_dates(brand: Brand, since: Optional[datetime] = None) -> List[datetime]:
    """Publication timestamps of the page's posts, newest first.

    Only needs r_organization_social, so this works for every brand today. The
    listing is capped by LinkedIn at ~365 results and excludes deleted posts —
    absence from it is not proof a post was never published.
    """
    token, owner = _credentials(brand)
    dates: List[datetime] = []
    for offset in range(0, POST_LISTING_CAP + PAGE_SIZE, PAGE_SIZE):
        payload = _get(
            f"{REST_BASE}/posts",
            token,
            {
                "q": "author",
                "author": owner,
                "count": str(PAGE_SIZE),
                "start": str(offset),
                "sortBy": "LAST_MODIFIED",
            },
        )
        elements = payload.get("elements", [])
        if not elements:
            break
        for post in elements:
            stamp = post.get("publishedAt") or post.get("createdAt")
            if stamp:
                dates.append(datetime.fromtimestamp(stamp / 1000, tz=timezone.utc))
    if since:
        dates = [date for date in dates if date >= since]
    return sorted(dates, reverse=True)


@dataclass(frozen=True)
class MonthRow:
    month: str
    posts: int
    organic: int
    paid: int
    cumulative: int


def monthly_report(brand: Brand, months: int = 12) -> List[MonthRow]:
    """Per-month posts published alongside follower gains, oldest first."""
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=31 * months)
    gains = fetch_follower_gains(brand, start, end, granularity="MONTH")
    posts = fetch_post_dates(brand, since=start)

    posts_by_month: Dict[str, int] = {}
    for date in posts:
        posts_by_month[date.strftime("%Y-%m")] = posts_by_month.get(date.strftime("%Y-%m"), 0) + 1

    rows: List[MonthRow] = []
    running = 0
    for gain in gains:
        month = gain.start.strftime("%Y-%m")
        running += gain.total
        rows.append(
            MonthRow(
                month=month,
                posts=posts_by_month.get(month, 0),
                organic=gain.organic,
                paid=gain.paid,
                cumulative=running,
            )
        )
    return rows


__all__ = [
    "FollowerGain",
    "MissingAnalyticsScope",
    "MonthRow",
    "fetch_follower_gains",
    "fetch_lifetime_followers",
    "fetch_post_dates",
    "monthly_report",
]
