#!/usr/bin/env python3
"""Generate LinkedIn posts for TNT Motion on a twice-weekly cadence."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from commonlib.llm_clients import QuotaExceededError, create_llm_client
from linkedin_generation.social import (
    CampaignConfig,
    LinkedInPostGenerator,
    LinkedInPublisher,
    PostPillar,
)
from linkedin_generation.social.image_providers import (
    CuratedLibraryImageProvider,
    ImagePayload,
    ImageProviderConfig,
    OpenAIImageProvider,
    GoogleImagenProvider,
    create_image_provider,
)
from linkedin_generation.social.linkedin_client import LinkedInPublisherConfig
from linkedin_generation.holiday.calendars import load_calendars
from linkedin_generation.holiday.scheduler import HolidayAwareScheduler
from linkedin_generation.social.logo_overlay import add_logo_to_image
# CRITICAL: Token auto-renewal - DO NOT REMOVE unless expressly commanded
# This ensures LinkedIn tokens are automatically refreshed before expiration
from linkedin_generation.token_manager import ensure_valid_token

import yaml

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAMPAIGN_CONFIG = PROJECT_ROOT / "config" / "linkedin_campaign.yaml"
DEFAULT_STRATEGY_PATH = PROJECT_ROOT / "config" / "strategy.txt"

STATE_FILENAME = "campaign_state.json"
ROTATION_STATE_FILENAME = "scheduler_state.json"
HOLIDAY_CONFIG = PROJECT_ROOT / "config" / "holiday_campaign.yaml"
SITE_UPDATE_STATE = PROJECT_ROOT / "logs" / "site_update_state.json"
SITE_UPDATE_INTERVAL = timedelta(days=14)

PROMOTIONAL_PILLAR_NAME = "Promotional Impact"
IMAGE_MODE_VIDEO = "video"
IMAGE_MODE_PHOTO = "photo"
IMAGE_MODE_HOLIDAY = "holiday"  # Static branded image for holiday posts


@dataclass
class RotationState:
    last_post_type: str = "technical"
    last_image_mode: str = IMAGE_MODE_PHOTO

    @classmethod
    def load(cls, path: Path) -> "RotationState":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            logging.warning("Rotation state file %s invalid; resetting", path)
            return cls()
        return cls(
            last_post_type=data.get("last_post_type", "technical"),
            last_image_mode=data.get("last_image_mode", IMAGE_MODE_PHOTO),
        )

    def save(self, path: Path) -> None:
        payload = {
            "last_post_type": self.last_post_type,
            "last_image_mode": self.last_image_mode,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))

    def plan_post_type(self) -> str:
        return "promotional" if self.last_post_type != "promotional" else "technical"

    def plan_image_mode(self) -> str:
        # Always use video for posts
        return IMAGE_MODE_PHOTO

    def record(self, *, post_type: str, image_mode: str) -> None:
        self.last_post_type = post_type
        self.last_image_mode = image_mode


@dataclass
class CampaignState:
    """Rolling state for pillar rotation."""

    last_pillar_index: int = -1

    @classmethod
    def load(cls, path: Path) -> "CampaignState":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            return cls(last_pillar_index=int(data.get("last_pillar_index", -1)))
        except json.JSONDecodeError:
            logging.warning("State file %s was invalid JSON, resetting", path)
            return cls()

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"last_pillar_index": self.last_pillar_index}))

    def next_index(self, total: int) -> int:
        if total <= 0:
            raise ValueError("Total number of pillars must be positive")
        self.last_pillar_index = (self.last_pillar_index + 1) % total
        return self.last_pillar_index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate scheduled LinkedIn posts for TNT Motion")
    parser.add_argument(
        "--campaign-config",
        type=Path,
        default=Path(os.getenv("LINKEDIN_CAMPAIGN_CONFIG", DEFAULT_CAMPAIGN_CONFIG)),
        help="Path to the LinkedIn campaign YAML",
    )
    parser.add_argument(
        "--strategy-file",
        type=Path,
        default=Path(os.getenv("STRATEGY_FILE", str(DEFAULT_STRATEGY_PATH))),
        help="Path to the strategy text file",
    )
    parser.add_argument(
        "--strategy-text",
        type=str,
        default=os.getenv("STRATEGY_TEXT"),
        help="Override strategy text via CLI",
    )
    parser.add_argument(
        "--llm-provider",
        type=str,
        default=os.getenv("LLM_PROVIDER", "gemini"),
        help="LLM provider identifier (gemini preferred; openrouter as fallback)",
    )
    parser.add_argument(
        "--llm-model",
        type=str,
        default=os.getenv("LLM_MODEL", "gemini-2.5-flash"),
        help="LLM model identifier for the primary provider",
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Generate a single post immediately instead of running the scheduler",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish generated posts directly to LinkedIn",
    )
    parser.add_argument(
        "--linkedin-owner",
        type=str,
        default=os.getenv("LINKEDIN_OWNER_URN"),
        help="LinkedIn organisation/member URN (e.g. urn:li:organization:123456)",
    )
    parser.add_argument(
        "--linkedin-access-token",
        type=str,
        default=os.getenv("LINKEDIN_ACCESS_TOKEN"),
        help="LinkedIn API access token",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )
    parser.add_argument(
        "--holiday-config",
        type=Path,
        default=HOLIDAY_CONFIG,
        help="Path to holiday-aware configuration",
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Run holiday-aware daily logic (ignores built-in schedule)",
    )
    return parser.parse_args()


def load_strategy(strategy_file: Path, override_text: Optional[str]) -> str:
    if override_text and override_text.strip():
        return override_text.strip()
    if strategy_file.exists():
        text = strategy_file.read_text().strip()
        if text:
            return text
    raise FileNotFoundError(
        "Strategy text missing. Provide STRATEGY_TEXT, --strategy-text, or ensure strategy file exists."
    )


def maybe_run_biweekly_site_updates() -> None:
    now = datetime.now(timezone.utc)
    try:
        if SITE_UPDATE_STATE.exists():
            data = json.loads(SITE_UPDATE_STATE.read_text())
            last_run_str = data.get("last_run")
            if last_run_str:
                last_run = datetime.fromisoformat(last_run_str)
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=timezone.utc)
                if now - last_run < SITE_UPDATE_INTERVAL:
                    return
    except json.JSONDecodeError:
        logging.warning("Site update state file %s was invalid JSON; recreating", SITE_UPDATE_STATE)

    logging.info("Biweekly maintenance window: running site update pipeline")
    env = os.environ.copy()
    script_path = PROJECT_ROOT / "bin" / "run_site_updates_and_deploy.sh"
    try:
        subprocess.run(
            ["bash", str(script_path), "--apply", "--auto-approve"],
            check=True,
            cwd=PROJECT_ROOT,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        logging.error("Site update pipeline failed with exit code %s", exc.returncode)
        return
    except Exception as exc:
        logging.error("Site update pipeline failed: %s", exc)
        return

    SITE_UPDATE_STATE.parent.mkdir(parents=True, exist_ok=True)
    SITE_UPDATE_STATE.write_text(json.dumps({"last_run": now.isoformat()}))
    logging.info("Site update pipeline completed successfully")


def ensure_image_provider(config: CampaignConfig):
    try:
        return create_image_provider(config.image_provider)
    except Exception as exc:
        logging.warning("Primary image provider failed: %s", exc)
        curated_entries = list(config.image_provider.curated_library)
        if curated_entries:
            fallback_config = ImageProviderConfig(
                provider="curated",
                curated_library=curated_entries,
            )
            logging.info("Falling back to curated image library")
            return create_image_provider(fallback_config)
        raise


def choose_pillar(
    *,
    campaign: CampaignConfig,
    state_path: Path,
    planned_post_type: str,
) -> PostPillar:
    state = CampaignState.load(state_path)
    total = len(campaign.pillars)

    if planned_post_type == "promotional":
        for idx, pillar in enumerate(campaign.pillars):
            if pillar.name == PROMOTIONAL_PILLAR_NAME:
                state.last_pillar_index = idx
                state.save(state_path)
                return pillar
        # fallback to first pillar if promotional not present
        state.last_pillar_index = 0
        state.save(state_path)
        return campaign.pillars[0]

    # technical rotation: skip promotional pillar
    for _ in range(total):
        idx = state.next_index(total)
        candidate = campaign.pillars[idx]
        if candidate.name != PROMOTIONAL_PILLAR_NAME:
            state.save(state_path)
            return candidate

    # fallback
    state.last_pillar_index = 0
    state.save(state_path)
    return campaign.pillars[0]


def generate_image_with_fallback(
    *,
    campaign: CampaignConfig,
    base_provider,
    prompt: str,
    image_mode: str,
    alt_text: str | None,
    target_dir: Path,
    video_target_dir: Path | None = None,
) -> tuple[ImagePayload, Path | None]:
    if image_mode == IMAGE_MODE_VIDEO:
        from linkedin_generation.social.image_providers import ReplicateVideoProvider
        replicate_config = ImageProviderConfig(
            provider="replicate",
            model=campaign.image_provider.model,
            size=campaign.image_provider.size,
            curated_library=campaign.image_provider.curated_library,
            style_hint=campaign.image_provider.style_hint,
        )
        replicate_provider = ReplicateVideoProvider(replicate_config)
        try:
            video_dir = video_target_dir or target_dir
            video_path = replicate_provider.get_video(prompt=prompt, target_dir=video_dir)
            # Also generate a still thumbnail for the copy deck
            image_payload = base_provider.get_image(
                prompt=prompt,
                target_dir=target_dir,
                alt_text=alt_text,
            )
            return image_payload, video_path
        except Exception as exc:
            logging.warning(
                "Replicate Veo 2 unavailable (%s); falling back to %s",
                exc,
                campaign.image_provider.provider,
            )

    # CRITICAL: Holiday posts use static branded images - DO NOT REMOVE unless expressly commanded
    # Holiday posts skip animated GIF and generate a branded holiday-themed static image
    if image_mode == IMAGE_MODE_HOLIDAY:
        logging.info("Holiday mode - generating static branded holiday image (no GIF)")
        # Generate static image with holiday-specific prompt enhancement
        holiday_prompt = f"{prompt}. Professional branded image suitable for holiday greeting, incorporating TNT Motion brand identity with festive elements."
        image_payload = base_provider.get_image(
            prompt=holiday_prompt,
            target_dir=target_dir,
            alt_text=alt_text,
        )
        return image_payload, None

    # CRITICAL: Animated GIF generation - DO NOT REMOVE unless expressly commanded
    # This generates multi-frame animated GIFs instead of static images
    # Check if animated GIF mode is enabled
    use_animated_gif = os.getenv("USE_ANIMATED_GIF", "false").lower() in ("true", "1", "yes")

    if use_animated_gif:
        from linkedin_generation.social.image_providers import AnimatedGIFProvider
        logging.info("Animated GIF mode enabled - generating multi-frame animation")

        # Wrap base provider with AnimatedGIFProvider
        num_frames = int(os.getenv("GIF_NUM_FRAMES", "6"))
        frame_duration = int(os.getenv("GIF_FRAME_DURATION", "1000"))

        gif_provider = AnimatedGIFProvider(
            base_provider=base_provider,
            num_frames=num_frames,
            frame_duration=frame_duration
        )

        try:
            image_payload = gif_provider.get_image(
                prompt=prompt,
                target_dir=target_dir,
                alt_text=alt_text,
            )
            logging.info("Animated GIF generated successfully")
            return image_payload, None
        except Exception as exc:
            logging.warning(
                "Animated GIF generation failed (%s); falling back to static image",
                exc,
            )
            # Fall through to static image below

    # Default: generate static image
    image_payload = base_provider.get_image(
        prompt=prompt,
        target_dir=target_dir,
        alt_text=alt_text,
    )
    return image_payload, None

def slugify(text: str) -> str:
    clean = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    while "--" in clean:
        clean = clean.replace("--", "-")
    return clean.strip("-") or "post"


def save_artifacts(
    *,
    post_output_dir: Path,
    post,
    image: ImagePayload,
    extra_metadata: Optional[Dict[str, str]] = None,
) -> Path:
    timestamp = post.created_at.strftime("%Y%m%d_%H%M")
    slug = slugify(post.pillar_name)
    base_path = post_output_dir / f"{timestamp}_{slug}"
    post_output_dir.mkdir(parents=True, exist_ok=True)

    copy_path = base_path.with_suffix(".txt")
    copy_path.write_text(post.as_text)

    metadata = post.as_mapping()
    metadata["copy_file"] = copy_path.name

    if image.path:
        metadata["image_file"] = image.path.name
    if image.url:
        metadata["image_url"] = image.url
    if image.provider:
        metadata["image_provider"] = image.provider
    if image.prompt:
        metadata["image_prompt"] = image.prompt
    if image.alt_text:
        metadata["image_alt_text"] = image.alt_text

    if extra_metadata:
        metadata.update(extra_metadata)

    metadata_path = base_path.with_suffix(".json")
    metadata_path.write_text(json.dumps(metadata, indent=2))

    return metadata_path


def daily_runner(
    *,
    campaign: CampaignConfig,
    generator: LinkedInPostGenerator,
    image_provider,
    publisher: LinkedInPublisher | None,
    publish_image_dir: Path,
    state_path: Path,
    holiday_config: Path,
) -> None:
    logging.info("Starting daily holiday-aware run")
    maybe_run_biweekly_site_updates()
    config = load_holiday_config(holiday_config)
    calendars = load_calendars(config["calendars"])
    tz = ZoneInfo(campaign.timezone)
    today = datetime.now(tz=tz).date()

    holiday_pillar = config["holiday_pillar"]
    scheduler = HolidayAwareScheduler(
        campaign=campaign,
        generator=generator,
        calendars=calendars,
        holiday_pillar=holiday_pillar,
        state_path=state_path,
    )

    decision = scheduler.evaluate_day(today)
    logging.info("Decision for %s: %s", today, decision)

    if not decision.should_post:
        logging.info("Skipping post: %s", decision.reason)
        return

    rotation_path = campaign.output_dir / ROTATION_STATE_FILENAME
    rotation_state = RotationState.load(rotation_path)
    next_post_type = rotation_state.plan_post_type()
    next_image_mode = rotation_state.plan_image_mode()

    campaign_state_path = campaign.output_dir / STATE_FILENAME

    scheduled_for = datetime.now(tz=tz)
    if decision.reason == "holiday" and decision.holiday:
        pillar = holiday_pillar
        post_type = "holiday"
        # CRITICAL: Holiday posts use static branded images - DO NOT REMOVE unless expressly commanded
        # Holiday posts should NOT use animated GIFs, but branded holiday-themed static images
        image_mode = IMAGE_MODE_HOLIDAY
    else:
        post_type = next_post_type
        image_mode = next_image_mode
        pillar = choose_pillar(
            campaign=campaign,
            state_path=campaign_state_path,
            planned_post_type=post_type,
        )

    logging.info("Selected pillar %s (post_type=%s image_mode=%s)", pillar.name, post_type, image_mode)

    post = generator.generate(
        pillar=pillar,
        scheduled_for=scheduled_for,
        post_type=post_type,
        image_mode=image_mode,
        holiday=decision.holiday if decision.reason == "holiday" else None,
    )

    try:
        image_payload, video_path = generate_image_with_fallback(
            campaign=campaign,
            base_provider=image_provider,
            prompt=post.image_prompt,
            image_mode=image_mode,
            alt_text=post.alt_text,
            target_dir=campaign.output_dir / "images",
            video_target_dir=campaign.output_dir / "videos",
        )
    except Exception as exc:
        logging.error("Image generation failed after fallback: %s", exc)
        image_payload = _fallback_image(
            exc=exc,
            campaign=campaign,
            target_dir=campaign.output_dir / "images",
            post=post,
        )
        video_path = None

    local_image_path = ensure_local_image_file(
        image=image_payload,
        fallback_dir=publish_image_dir,
        timestamp=scheduled_for,
    )

    extra_metadata: Dict[str, str] = {
        "decision_reason": decision.reason,
        "pillar": pillar.name,
        "post_type": post.metadata.get("post_type", post_type),
        "image_mode": post.metadata.get("image_mode", image_mode),
    }
    if video_path:
        extra_metadata["video_file"] = video_path.name
    if decision.holiday:
        extra_metadata["holiday_name"] = decision.holiday.name
        extra_metadata["holiday_start_date"] = decision.holiday.start_date.isoformat()
        extra_metadata["holiday_end_date"] = decision.holiday.end_date.isoformat()

    if publisher:
        publish_result = publisher.publish_post(
            text=post.as_text,
            headline=post.headline,
            alt_text=post.alt_text,
            image_path=local_image_path,
                    
        )
        if publish_result.get("share_urn"):
            extra_metadata["linkedin_share_urn"] = publish_result["share_urn"]
        if publish_result.get("permalink"):
            extra_metadata["linkedin_permalink"] = publish_result["permalink"]
        extra_metadata["linkedin_asset"] = publish_result.get("asset", "")

    metadata_path = save_artifacts(
        post_output_dir=campaign.output_dir,
        post=post,
        image=image_payload,
        extra_metadata=extra_metadata,
    )
    logging.info("Saved daily artefacts to %s", metadata_path)

    if decision.reason != "holiday":
        rotation_state.record(post_type=post_type, image_mode=image_mode)
        rotation_state.save(rotation_path)


def load_holiday_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Holiday configuration not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    calendars = {}
    for key, value in (data.get("calendars") or {}).items():
        calendars[key] = (PROJECT_ROOT / value).resolve()

    template = data.get("holiday_post_template", {})
    pillar = PostPillar(
        name=template.get("name", "Holiday Greeting"),
        target_client=template.get("target_client", "Global partners"),
        angle=template.get("headline", "Holiday greetings from TNT Motion"),
        proof_points=[template.get("body", "")],
        ctas=[template.get("cta", "")],
        hashtags=data.get("defaults", {}).get("hashtags", ["#TNTMotion", "#HolidayGreetings"]),
        image_prompt=template.get("image_prompt"),
    )

    return {
        "calendars": calendars,
        "holiday_pillar": pillar,
    }


def build_job(
    *,
    campaign: CampaignConfig,
    generator: LinkedInPostGenerator,
    image_provider,
    state_path: Path,
    publisher: LinkedInPublisher | None,
    publish_image_dir: Path,
) -> callable:
    images_dir = campaign.output_dir / "images"
    rotation_path = campaign.output_dir / ROTATION_STATE_FILENAME
    rotation_state = RotationState.load(rotation_path)

    def job() -> None:
        nonlocal rotation_state
        logging.info("Starting LinkedIn post generation job")
        maybe_run_biweekly_site_updates()
        tz = ZoneInfo(campaign.timezone)
        scheduled_for = datetime.now(tz=tz)
        next_post_type = rotation_state.plan_post_type()
        next_image_mode = rotation_state.plan_image_mode()

        pillar = choose_pillar(
            campaign=campaign,
            state_path=state_path,
            planned_post_type=next_post_type,
        )
        logging.info(
            "Selected pillar %s (post_type=%s image_mode=%s)",
            pillar.name,
            next_post_type,
            next_image_mode,
        )

        primary_llm = generator.llm_client
        try:
            post = generator.generate(
                pillar=pillar,
                scheduled_for=scheduled_for,
                post_type=next_post_type,
                image_mode=next_image_mode,
            )
        except QuotaExceededError as exc:
            logging.warning("Gemini quota reached (%s). Falling back to OpenRouter", exc)
            fallback_model = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")
            fallback_client = create_llm_client(provider="openrouter", model=fallback_model)
            generator.llm_client = fallback_client
            try:
                post = generator.generate(
                    pillar=pillar,
                    scheduled_for=scheduled_for,
                    post_type=next_post_type,
                    image_mode=next_image_mode,
                )
                logging.info(
                    "Generated post using OpenRouter fallback model %s", fallback_model
                )
            finally:
                generator.llm_client = primary_llm

        try:
            image_payload, video_path = generate_image_with_fallback(
                campaign=campaign,
                base_provider=image_provider,
                prompt=post.image_prompt,
                target_dir=images_dir,
                alt_text=post.alt_text,
                image_mode=next_image_mode,
                video_target_dir=campaign.output_dir / "videos",
            )
        except Exception as exc:
            logging.error("Image generation failed: %s", exc)
            image_payload = _fallback_image(
                exc=exc,
                campaign=campaign,
                target_dir=images_dir,
                post=post,
            )
            video_path = None

        local_image_path = ensure_local_image_file(
            image=image_payload,
            fallback_dir=publish_image_dir,
            timestamp=scheduled_for,
        )

        extra_metadata: Dict[str, str] = {
            "video_prompt": post.video_prompt,
            "post_type": post.metadata.get("post_type", next_post_type),
            "image_mode": post.metadata.get("image_mode", next_image_mode),
        }
        if video_path:
            extra_metadata["video_file"] = video_path.name
        if publisher:
            try:
                publish_result = publisher.publish_post(
                    text=post.as_text,
                    headline=post.headline,
                    alt_text=post.alt_text,
                    image_path=local_image_path,
                    
                )
            except Exception as exc:
                logging.error("LinkedIn publishing failed: %s", exc)
                raise
            if publish_result.get("share_urn"):
                extra_metadata["linkedin_share_urn"] = publish_result["share_urn"]
            if publish_result.get("permalink"):
                extra_metadata["linkedin_permalink"] = publish_result["permalink"]
            extra_metadata["linkedin_asset"] = publish_result.get("asset", "")

        metadata_path = save_artifacts(
            post_output_dir=campaign.output_dir,
            post=post,
            image=image_payload,
            extra_metadata=extra_metadata,
        )
        rotation_state.record(
            post_type=post.metadata.get("post_type", next_post_type),
            image_mode=post.metadata.get("image_mode", next_image_mode),
        )
        rotation_state.save(rotation_path)
        logging.info("Saved post artefacts to %s", metadata_path)

    return job


def ensure_local_image_file(
    *,
    image: ImagePayload,
    fallback_dir: Path,
    timestamp: datetime,
) -> Path:
    """Ensure we have a local image file and return its path."""
    if image.path and image.path.exists():
        local_path = image.path
    elif not image.url:
        raise RuntimeError("Image payload did not include a local file or URL")
    else:
        fallback_dir.mkdir(parents=True, exist_ok=True)
        slug = timestamp.strftime("%Y%m%d_%H%M%S")
        filename = f"linkedin_image_{slug}.jpg"
        local_path = fallback_dir / filename

        logging.info("Downloading curated image from %s", image.url)
        response = requests.get(image.url, timeout=30)
        response.raise_for_status()
        local_path.write_bytes(response.content)

    # CRITICAL: TNT Motion logo overlay - DO NOT REMOVE unless expressly commanded
    # This adds the TNT Motion logo to all generated images
    # Add logo overlay to all images (skip GIFs - they already have logo from AnimatedGIFProvider)
    if local_path.suffix.lower() not in ['.gif']:
        logo_path = Path(__file__).parent / "social" / "logo_overlay.py"
        assets_logo = Path(__file__).parent.parent / "assets" / "tnt_motion_logo.png"
        if assets_logo.exists():
            try:
                add_logo_to_image(
                    local_path,
                    assets_logo,
                    position="bottom-right",
                    logo_width_percent=0.12,
                    margin_percent=0.02
                )
                logging.info("Added TNT Motion logo overlay to %s", local_path.name)
            except Exception as exc:
                logging.warning("Failed to add logo overlay: %s", exc)
        else:
            logging.warning("Logo file not found at %s", assets_logo)

    return local_path


def _fallback_image(
    *,
    exc: Exception,
    campaign: CampaignConfig,
    target_dir: Path,
    post,
) -> ImagePayload:
    curated_entries = list(campaign.image_provider.curated_library)
    if not curated_entries:
        raise exc
    logging.info("Falling back to curated image after failure: %s", exc)
    fallback_provider = CuratedLibraryImageProvider(
        ImageProviderConfig(provider="curated", curated_library=curated_entries)
    )
    alt_text = getattr(post, "alt_text", None)
    return fallback_provider.get_image(
        prompt=post.image_prompt,
        target_dir=target_dir,
        alt_text=alt_text,
    )


def configure_scheduler(
    *,
    campaign: CampaignConfig,
    job_callable,
) -> BlockingScheduler:
    tz = ZoneInfo(campaign.timezone)
    scheduler = BlockingScheduler(timezone=tz)
    if not campaign.schedule_slots:
        raise ValueError("No schedule slots defined in campaign config")

    day_map = {
        "monday": "mon",
        "tuesday": "tue",
        "wednesday": "wed",
        "thursday": "thu",
        "friday": "fri",
        "saturday": "sat",
        "sunday": "sun",
    }

    for slot in campaign.schedule_slots:
        day_key = slot.day.strip().lower()
        if day_key not in day_map:
            raise ValueError(f"Unsupported day in schedule: {slot.day}")
        try:
            hour_str, minute_str = slot.time.split(":", maxsplit=1)
            hour = int(hour_str)
            minute = int(minute_str)
        except ValueError as exc:
            raise ValueError(f"Invalid time format for slot {slot}") from exc

        trigger = CronTrigger(
            day_of_week=day_map[day_key],
            hour=hour,
            minute=minute,
            timezone=tz,
        )
        scheduler.add_job(
            job_callable,
            trigger=trigger,
            name=f"linkedin_post_{day_map[day_key]}_{hour:02d}{minute:02d}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        logging.info(
            "Scheduled LinkedIn post for %s at %s (%s)", slot.day, slot.time, campaign.timezone
        )

    return scheduler


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    campaign = CampaignConfig.from_yaml(args.campaign_config)
    campaign.output_dir.mkdir(parents=True, exist_ok=True)

    strategy_text = load_strategy(args.strategy_file, args.strategy_text)

    llm_client = create_llm_client(provider=args.llm_provider, model=args.llm_model)
    generator = LinkedInPostGenerator(
        campaign=campaign,
        llm_client=llm_client,
        strategy_text=strategy_text,
    )

    image_provider = ensure_image_provider(campaign)

    publisher: LinkedInPublisher | None = None
    if args.publish:
        if not args.linkedin_owner:
            raise ValueError(
                "--publish requires --linkedin-owner or LINKEDIN_OWNER_URN environment variable"
            )
        # CRITICAL: Token auto-renewal - DO NOT REMOVE unless expressly commanded
        # This call refreshes the LinkedIn token automatically before it expires
        # Ensure token is valid and refresh if needed before publishing
        args.linkedin_access_token = ensure_valid_token() or args.linkedin_access_token
        if not args.linkedin_access_token:
            raise ValueError(
                "--publish requires --linkedin-access-token or LINKEDIN_ACCESS_TOKEN environment variable"
            )
        publisher_config = LinkedInPublisherConfig(
            access_token=args.linkedin_access_token,
            owner_urn=args.linkedin_owner,
        )
        publisher = LinkedInPublisher(publisher_config)

    state_path = campaign.output_dir / STATE_FILENAME
    publish_image_dir = campaign.output_dir / "publish" / "images"
    if args.daily:
        logging.info("Running holiday-aware daily decision")
        daily_runner(
            campaign=campaign,
            generator=generator,
            image_provider=image_provider,
            publisher=publisher,
            publish_image_dir=publish_image_dir,
            state_path=state_path,
            holiday_config=args.holiday_config,
        )
        return

    job_callable = build_job(
        campaign=campaign,
        generator=generator,
        image_provider=image_provider,
        state_path=state_path,
        publisher=publisher,
        publish_image_dir=publish_image_dir,
    )

    if args.run_once:
        logging.info("Running single LinkedIn post generation")
        job_callable()
        return

    scheduler = configure_scheduler(campaign=campaign, job_callable=job_callable)
    logging.info("Starting scheduler with %d slots", len(campaign.schedule_slots))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Scheduler stopped by user")


if __name__ == "__main__":
    main()
