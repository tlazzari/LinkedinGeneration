#!/usr/bin/env python3
"""Report a brand's LinkedIn follower growth against its posting cadence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from linkedin_generation.social import follower_analytics as analytics  # noqa: E402
from linkedin_generation.social.brand import BRANDS, get_brand  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand", default="seta", choices=sorted(BRANDS))
    parser.add_argument("--months", type=int, default=12, help="months of history (default 12)")
    parser.add_argument("--env", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(args.env)
    brand = get_brand(args.brand)

    try:
        rows = analytics.monthly_report(brand, months=args.months)
        total = analytics.fetch_lifetime_followers(brand)
    except analytics.MissingAnalyticsScope as exc:
        print(f"{brand.display_name}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"brand": brand.key, "followers": total, "months": [row.__dict__ for row in rows]}, indent=2))
        return 0

    print(f"{brand.display_name} — {total} followers today")
    print(f"{'month':<9}{'posts':>7}{'organic':>9}{'paid':>7}{'cumulative':>12}")
    for row in rows:
        print(f"{row.month:<9}{row.posts:>7}{row.organic:>9}{row.paid:>7}{row.cumulative:>12}")
    gained = rows[-1].cumulative if rows else 0
    posted = sum(row.posts for row in rows)
    print(f"\n{gained} followers gained over {len(rows)} months, {posted} posts published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
