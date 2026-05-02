"""Holiday-aware orchestration for LinkedIn posts."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional

from zoneinfo import ZoneInfo

from linkedin_generation.holiday.calendars import HolidayCalendar, HolidayEvent
from linkedin_generation.social import CampaignConfig, LinkedInPostGenerator, PostPillar


logger = logging.getLogger(__name__)


@dataclass
class HolidayDecision:
    """Outcome of evaluating a potential post day."""

    should_post: bool
    reason: str
    holiday: Optional[HolidayEvent] = None
    pillar: Optional[PostPillar] = None


class HolidayAwareScheduler:
    """Decide whether to publish a normal or holiday post on a given day."""

    def __init__(
        self,
        *,
        campaign: CampaignConfig,
        generator: LinkedInPostGenerator,
        calendars: Dict[str, HolidayCalendar],
        holiday_pillar: PostPillar,
        state_path: Path,
    ) -> None:
        self.campaign = campaign
        self.generator = generator
        self.calendars = calendars
        self.holiday_pillar = holiday_pillar
        self.state_path = state_path
        self._regular_days = self._compute_regular_days()

    def evaluate_day(self, day: date) -> HolidayDecision:
        holiday = self._holiday_tomorrow(day)
        if holiday:
            logger.info(
                "Holiday detected (%s) starting %s; scheduling holiday post",
                holiday.name,
                holiday.start_date,
            )
            return HolidayDecision(should_post=True, reason="holiday", holiday=holiday)

        if not self._is_regular_post_day(day):
            logger.info("No scheduled post for %s", day)
            return HolidayDecision(should_post=False, reason="no-slot")

        return HolidayDecision(should_post=True, reason="regular")

    def create_post(self, *, decision: HolidayDecision, when: datetime) -> Optional[dict]:
        if not decision.should_post:
            return None

        pillar: PostPillar
        if decision.reason == "holiday" and decision.holiday:
            pillar = self.holiday_pillar
        else:
            pillar = self._next_regular_pillar()

        post = self.generator.generate(
            pillar=pillar,
            scheduled_for=when,
            post_type="holiday" if decision.reason == "holiday" else "promotional",
            image_mode="photo",
            holiday=decision.holiday if decision.reason == "holiday" else None,
        )
        return {
            "post": post,
            "pillar": pillar,
            "decision": decision,
        }

    def _holiday_tomorrow(self, day: date) -> Optional[HolidayEvent]:
        for calendar in self.calendars.values():
            event = calendar.day_before_holiday(day)
            if event:
                return event
        return None

    def _compute_regular_days(self) -> set[int]:
        day_map = {
            "monday": 0,
            "mon": 0,
            "tuesday": 1,
            "tue": 1,
            "wednesday": 2,
            "wed": 2,
            "thursday": 3,
            "thu": 3,
            "friday": 4,
            "fri": 4,
            "saturday": 5,
            "sat": 5,
            "sunday": 6,
            "sun": 6,
        }
        result: set[int] = set()
        for slot in self.campaign.schedule_slots:
            key = slot.day.strip().lower()
            if key not in day_map:
                logger.warning("Unknown schedule day '%s' in campaign config", slot.day)
                continue
            result.add(day_map[key])
        return result

    def _is_regular_post_day(self, day: date) -> bool:
        return day.weekday() in self._regular_days

    def _next_regular_pillar(self) -> PostPillar:
        # reuse CampaignState rotation by leveraging the standard state file
        from linkedin_generation.linkedin_post_scheduler import CampaignState

        state = CampaignState.load(self.state_path)
        idx = state.next_index(len(self.campaign.pillars))
        state.save(self.state_path)
        return self.campaign.pillars[idx]
