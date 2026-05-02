#!/usr/bin/env python3
"""Refresh holiday calendars used by the LinkedIn scheduler."""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import requests


LOGGER = logging.getLogger(__name__)

DEFAULT_COUNTRIES = ("IT", "CN")
COUNTRY_SCOPES = {
    "IT": "italy",
    "CN": "china",
}
NAGER_DATE_ENDPOINT = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"


@dataclass(frozen=True)
class HolidaySpan:
    name: str
    start: date
    end: date
    locale: str

    def as_mapping(self) -> dict:
        return {
            "name": self.name,
            "start_date": self.start.isoformat(),
            "end_date": self.end.isoformat(),
            "locale": self.locale,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and regenerate holiday JSON calendars")
    parser.add_argument(
        "--year",
        type=int,
        default=datetime.now().year + 1,
        help="Year to fetch (defaults to next year)",
    )
    parser.add_argument(
        "--countries",
        nargs="+",
        default=list(DEFAULT_COUNTRIES),
        help="ISO country codes to fetch (default: IT CN)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("config/holidays"),
        help="Directory where JSON files will be written",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show changes without writing files",
    )
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="COUNTRY=PATH",
        help="Append extra holiday spans from a JSON file for the specified country",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level (default: INFO)",
    )
    return parser.parse_args()


def load_extras(specs: Sequence[str]) -> Dict[str, List[HolidaySpan]]:
    results: Dict[str, List[HolidaySpan]] = defaultdict(list)
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"Invalid --extra format '{spec}'. Expected COUNTRY=PATH")
        country, path = spec.split("=", 1)
        country = country.strip().upper()
        file_path = Path(path.strip()).expanduser().resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"Extra holiday file not found: {file_path}")
        data = json.loads(file_path.read_text())
        for entry in data:
            try:
                name = str(entry["name"])
                start = datetime.strptime(entry["start_date"], "%Y-%m-%d").date()
                end = datetime.strptime(entry["end_date"], "%Y-%m-%d").date()
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Invalid holiday entry in {file_path}: {entry}") from exc
            locale = entry.get("locale") or COUNTRY_SCOPES.get(country, country.lower())
            results[country].append(HolidaySpan(name=name, start=start, end=end, locale=locale))
    return results


def fetch_public_holidays(*, country: str, year: int, session: requests.Session) -> List[dict]:
    url = NAGER_DATE_ENDPOINT.format(year=year, country=country)
    LOGGER.debug("Fetching %s", url)
    response = session.get(url, timeout=30)
    if response.status_code == 404:
        raise RuntimeError(f"No holiday data available for {country} {year} ({url})")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected response shape for {country} {year}: {payload}")
    return payload


def aggregate_spans(records: Iterable[dict], *, country: str) -> List[HolidaySpan]:
    spans: List[HolidaySpan] = []
    sorted_records = sorted(
        (
            (
                datetime.strptime(entry["date"], "%Y-%m-%d").date(),
                entry.get("name") or entry.get("localName") or "Unnamed Holiday",
            )
            for entry in records
        ),
        key=lambda item: item[0],
    )

    if not sorted_records:
        return spans

    locale = COUNTRY_SCOPES.get(country.upper(), country.lower())
    current_name = None
    current_start = None
    current_end = None

    for holiday_date, holiday_name in sorted_records:
        if current_name is None:
            current_name = holiday_name
            current_start = holiday_date
            current_end = holiday_date
            continue

        if holiday_name == current_name and holiday_date == current_end + timedelta(days=1):
            current_end = holiday_date
        else:
            spans.append(HolidaySpan(name=current_name, start=current_start, end=current_end, locale=locale))
            current_name = holiday_name
            current_start = holiday_date
            current_end = holiday_date

    spans.append(HolidaySpan(name=current_name, start=current_start, end=current_end, locale=locale))
    return spans


def merge_and_sort(spans: Iterable[HolidaySpan]) -> List[HolidaySpan]:
    merged = sorted(spans, key=lambda span: (span.start, span.end, span.name))
    return merged


def write_calendar(*, path: Path, spans: Sequence[HolidaySpan], dry_run: bool) -> None:
    if dry_run:
        LOGGER.info("[dry-run] Would write %s (%d entries)", path, len(spans))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [span.as_mapping() for span in spans]
    path.write_text(json.dumps(payload, indent=2))
    LOGGER.info("Wrote %s (%d entries)", path, len(payload))


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    extras = load_extras(args.extra)

    session = requests.Session()
    for country in args.countries:
        country_code = country.upper()
        LOGGER.info("Fetching holidays for %s %s", country_code, args.year)
        records = fetch_public_holidays(country=country_code, year=args.year, session=session)
        spans = aggregate_spans(records, country=country_code)

        if country_code in extras:
            LOGGER.info("Merging %d extra spans for %s", len(extras[country_code]), country_code)
            spans.extend(extras[country_code])

        merged = merge_and_sort(spans)
        locale = COUNTRY_SCOPES.get(country_code, country_code.lower())
        output_file = args.output_dir / f"{locale}_{args.year}.json"
        write_calendar(path=output_file, spans=merged, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
