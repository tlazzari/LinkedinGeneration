"""Campaign configuration loaders for LinkedIn content automation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Sequence

import yaml

from .image_providers import ImageProviderConfig


@dataclass(frozen=True)
class ScheduleSlot:
    """Represents a weekly publishing slot (day + 24h time)."""

    day: str
    time: str  # HH:MM in 24h format


@dataclass(frozen=True)
class PostPillar:
    """Content pillar describing angle, proof points and CTA choices."""

    name: str
    target_client: str
    angle: str
    proof_points: Sequence[str] = field(default_factory=list)
    ctas: Sequence[str] = field(default_factory=list)
    hashtags: Sequence[str] = field(default_factory=list)
    image_prompt: str | None = None
    use_news_search: bool = False


@dataclass(frozen=True)
class CampaignConfig:
    """Top level campaign settings for LinkedIn post generation."""

    pillars: Sequence[PostPillar]
    tone: str
    default_hashtags: Sequence[str]
    schedule_slots: Sequence[ScheduleSlot]
    timezone: str
    output_dir: Path
    image_provider: ImageProviderConfig

    @classmethod
    def from_yaml(cls, path: Path) -> "CampaignConfig":
        data = yaml.safe_load(path.read_text()) or {}

        defaults = data.get("defaults", {})
        tone = defaults.get(
            "tone",
            "Confident, engineering-driven voice that stays accessible to distributors and OEM partners.",
        )
        default_hashtags: List[str] = list(defaults.get("hashtags", ["#TNTMotion", "#BearingExperts", "#EngineeringSupport"]))

        schedule_cfg = data.get("schedule", {})
        timezone = schedule_cfg.get("timezone", "Europe/Rome")
        schedule_slots: List[ScheduleSlot] = []
        for slot in schedule_cfg.get("slots", []):
            day = str(slot.get("day", "Tuesday")).strip()
            time = str(slot.get("time", "09:00")).strip()
            schedule_slots.append(ScheduleSlot(day=day, time=time))

        pillars: List[PostPillar] = []
        for entry in data.get("content_pillars", []):
            pillars.append(
                PostPillar(
                    name=entry["name"],
                    target_client=entry.get("target_client", ""),
                    angle=entry.get("angle", ""),
                    proof_points=list(entry.get("proof_points", [])),
                    ctas=list(entry.get("ctas", [])),
                    hashtags=list(entry.get("hashtags", [])),
                    image_prompt=entry.get("image_prompt"),
                    use_news_search=entry.get("use_news_search", False),
                )
            )

        if not pillars:
            raise ValueError("At least one content_pillar must be defined in the campaign config")

        output_dir_cfg = data.get("output", {})
        default_output = os.getenv("LINKEDIN_OUTPUT_DIR", "linkedin_generation/linkedin_posts")
        output_dir = Path(output_dir_cfg.get("directory", default_output))

        image_provider_cfg = ImageProviderConfig.from_mapping(data.get("image_provider", {}))

        return cls(
            pillars=pillars,
            tone=tone,
            default_hashtags=default_hashtags,
            schedule_slots=schedule_slots,
            timezone=timezone,
            output_dir=output_dir,
            image_provider=image_provider_cfg,
        )


__all__ = ["CampaignConfig", "PostPillar", "ScheduleSlot"]
