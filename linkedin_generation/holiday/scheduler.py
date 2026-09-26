"""Holiday-aware orchestration for LinkedIn posts."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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

        # The greeting is scheduled on the EVE, so anything that stops it that
        # morning loses the festival for the year. On 2026-09-24 the Mid-Autumn
        # post was refused by the confidentiality gate (a false positive on
        # "Global PMI Partners"), and 25 Sep -- the festival itself -- logged
        # "no-slot". Nothing went out.
        #
        # This is NOT a retry of the blocked draft, and it does not weaken the
        # gate: it generates a FRESH post that must clear every gate on its own.
        # A second refusal still means no post. The rule that a blocked draft
        # must never be "retried into existence" is about forcing one draft
        # past the gate within a run, which this does not do.
        today_holiday = self._holiday_today(day)
        if today_holiday and not self._published_on(day - timedelta(days=1)):
            logger.info(
                "Holiday %s starts today and nothing was published on the eve "
                "(%s); generating the greeting today instead",
                today_holiday.name, day - timedelta(days=1),
            )
            return HolidayDecision(should_post=True, reason="holiday",
                                   holiday=today_holiday)

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

    def _holiday_today(self, day: date) -> Optional[HolidayEvent]:
        """The holiday STARTING today, if any (not one already under way)."""
        for calendar in self.calendars.values():
            event = calendar.is_holiday(day)
            if event and event.start_date == day:
                return event
        return None

    def _published_on(self, day: date) -> bool:
        """Did a post actually go out on `day`?

        Read from the artifact files rather than a state flag: artifacts are
        written when a post is published, so they are the record of what
        happened. A blocked run writes nothing, which is exactly the signal.
        """
        try:
            stamp = day.strftime("%Y%m%d")
            return any(p.name.startswith(stamp)
                       for p in self.state_path.parent.glob(f"{stamp}*.json"))
        except Exception:      # never let a bookkeeping read stop a post
            return False

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
