"""Holiday calendar management for LinkedIn scheduling."""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional


logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class HolidayEvent:
    """Represents an observed holiday period."""

    name: str
    start_date: date
    end_date: date
    locale: str

    def includes(self, day: date) -> bool:
        return self.start_date <= day <= self.end_date

    def day_before_starts(self, day: date) -> bool:
        return self.start_date - timedelta(days=1) == day


class HolidayCalendar:
    """Loads and queries holiday events from JSON files."""

    def __init__(self, *, events: Iterable[HolidayEvent]) -> None:
        self._events: List[HolidayEvent] = sorted(events, key=lambda ev: ev.start_date)

    @classmethod
    def from_json(cls, path: Path, *, locale: Optional[str] = None) -> "HolidayCalendar":
        if not path.exists():
            raise FileNotFoundError(f"Holiday calendar file not found: {path}")
        data = json.loads(path.read_text())
        events: List[HolidayEvent] = []
        for entry in data:
            try:
                start = datetime.strptime(entry["start_date"], "%Y-%m-%d").date()
                end = datetime.strptime(entry["end_date"], "%Y-%m-%d").date()
                name = str(entry["name"])
                loc = str(entry.get("locale", locale) or "generic")
            except (KeyError, ValueError) as exc:
                logger.warning("Skipping invalid holiday entry %s: %s", entry, exc)
                continue
            events.append(HolidayEvent(name=name, start_date=start, end_date=end, locale=loc))
        return cls(events=events)

    def upcoming(self, *, on_or_after: date) -> List[HolidayEvent]:
        return [event for event in self._events if event.end_date >= on_or_after]

    def next_holiday(self, *, on_or_after: date) -> Optional[HolidayEvent]:
        for event in self._events:
            if event.end_date >= on_or_after:
                return event
        return None

    def is_holiday(self, day: date) -> Optional[HolidayEvent]:
        for event in self._events:
            if event.includes(day):
                return event
        return None

    def day_before_holiday(self, day: date) -> Optional[HolidayEvent]:
        for event in self._events:
            if event.day_before_starts(day):
                return event
        return None


def load_calendars(mapping: Dict[str, Path]) -> Dict[str, HolidayCalendar]:
    result: Dict[str, HolidayCalendar] = {}
    for locale, path in mapping.items():
        try:
            result[locale] = HolidayCalendar.from_json(path, locale=locale)
        except FileNotFoundError:
            logger.warning("Holiday calendar missing for %s: %s", locale, path)
    return result
