#!/usr/bin/env python3
"""Generate LinkedIn posts for Seta Capital with real-time news integration."""

from __future__ import annotations

import argparse
import json
import logging
import os
import requests
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from commonlib.llm_clients import QuotaExceededError, create_llm_client
from linkedin_generation.social import CampaignConfig, PostPillar
from linkedin_generation.social.seta_content_generation import SetaLinkedInPostGenerator, GeneratedPost
from linkedin_generation.social.linkedin_client import LinkedInPublisher, LinkedInPublisherConfig
from linkedin_generation.social.image_providers import (
    ImagePayload,
    ImageProviderConfig,
    GoogleImagenProvider,
    ReplicateVideoProvider,
    create_image_provider,
)
from linkedin_generation.social.news_search import fetch_article_preview_image
from linkedin_generation.social.seta_chart_generator import generate_market_chart, CHART_TYPES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAMPAIGN_CONFIG = Path(os.path.expanduser("~/seta_linkedin_campaign.yaml"))
DEFAULT_STRATEGY_PATH = Path(os.path.expanduser("~/seta_strategy.txt"))

STATE_FILENAME = "seta_campaign_state.json"
ROTATION_STATE_FILENAME = "seta_scheduler_state.json"

# Seta Capital logo for overlay
SETA_LOGO_PATH = PROJECT_ROOT / "assets" / "seta_capital_logo.png"


@dataclass
class RotationState:
    last_post_type: str = "technical"
    last_pillar_index: int = -1
    last_chart_type_index: int = -1

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
            last_pillar_index=data.get("last_pillar_index", -1),
            last_chart_type_index=data.get("last_chart_type_index", -1),
        )

    def save(self, path: Path) -> None:
        payload = {
            "last_post_type": self.last_post_type,
            "last_pillar_index": self.last_pillar_index,
            "last_chart_type_index": self.last_chart_type_index,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))

    def next_pillar_index(self, total: int) -> int:
        self.last_pillar_index = (self.last_pillar_index + 1) % total
        return self.last_pillar_index

    def next_chart_type_index(self) -> int:
        self.last_chart_type_index = (self.last_chart_type_index + 1) % len(CHART_TYPES)
        return self.last_chart_type_index

    def plan_post_type(self) -> str:
        # Alternate between promotional and technical
        return "promotional" if self.last_post_type != "promotional" else "technical"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate scheduled LinkedIn posts for Seta Capital")
    parser.add_argument(
        "--campaign-config",
        type=Path,
        default=Path(os.getenv("SETA_CAMPAIGN_CONFIG", DEFAULT_CAMPAIGN_CONFIG)),
        help="Path to the Seta Capital campaign YAML",
    )
    parser.add_argument(
        "--strategy-file",
        type=Path,
        default=Path(os.getenv("SETA_STRATEGY_FILE", str(DEFAULT_STRATEGY_PATH))),
        help="Path to the strategy text file",
    )
    parser.add_argument(
        "--strategy-text",
        type=str,
        default=os.getenv("SETA_STRATEGY_TEXT"),
        help="Override strategy text via CLI",
    )
    parser.add_argument(
        "--llm-provider",
        type=str,
        default=os.getenv("LLM_PROVIDER", "gemini"),
        help="LLM provider identifier",
    )
    parser.add_argument(
        "--llm-model",
        type=str,
        default=os.getenv("LLM_MODEL", "gemini-2.5-flash"),
        help="LLM model identifier",
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
        default=os.getenv("SETA_LINKEDIN_OWNER_URN"),
        help="LinkedIn organisation/member URN for Seta Capital",
    )
    parser.add_argument(
        "--linkedin-access-token",
        type=str,
        default=os.getenv("SETA_LINKEDIN_ACCESS_TOKEN"),
        help="LinkedIn API access token for Seta Capital",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.getenv("SETA_OUTPUT_DIR", "linkedin_generation/seta_posts")),
        help="Output directory for generated posts",
    )
    return parser.parse_args()


def load_strategy(strategy_file: Path, override_text: Optional[str]) -> str:
    if override_text and override_text.strip():
        return override_text.strip()
    if strategy_file.exists():
        text = strategy_file.read_text().strip()
        if text:
            return text
    # Default Seta Capital strategy
    return (
        "Position Seta Capital as a trusted cross-border M&A advisor with deep expertise "
        "in China-Europe transactions. Focus on timely market insights, actual deal flow, "
        "and thought leadership that demonstrates real value to C-suite decision makers."
    )


def download_article_image(url: str, target_dir: Path, filename: str) -> Optional[Path]:
    """Download an article preview image."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; SetaCapitalBot/1.0)"}
        response = requests.get(url, headers=headers, timeout=15, stream=True)
        response.raise_for_status()

        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / filename

        with open(target_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logging.info(f"Downloaded article image to {target_path}")
        return target_path
    except Exception as e:
        logging.warning(f"Failed to download article image: {e}")
        return None


def generate_image_for_post(
    *,
    campaign: CampaignConfig,
    post: GeneratedPost,
    target_dir: Path,
) -> ImagePayload:
    """Generate an AI image for the post."""
    target_dir.mkdir(parents=True, exist_ok=True)

    # Always use AI-generated images (article previews often have logos/branding)
    try:
        provider_config = ImageProviderConfig(
            provider="google",
            model=campaign.image_provider.model or "imagen-4.0-generate-001",
            size=campaign.image_provider.size or "1024x1024",
            style_hint=campaign.image_provider.style_hint,
            use_animated_gif=campaign.image_provider.use_animated_gif,
            gif_num_frames=campaign.image_provider.gif_num_frames,
            gif_frame_duration=campaign.image_provider.gif_frame_duration,
        )
        provider = create_image_provider(provider_config)

        # Enhance prompt with news context if available
        prompt = post.image_prompt
        if post.news_articles:
            news_topic = post.news_articles[0].title
            prompt = f"{prompt} Topic inspiration: {news_topic}"

        return provider.get_image(
            prompt=prompt,
            target_dir=target_dir,
            alt_text=post.alt_text,
        )
    except Exception as e:
        logging.warning(f"AI image generation failed: {e}")

        # Try curated library as final fallback
        if campaign.image_provider.curated_library:
            import random
            entry = random.choice(list(campaign.image_provider.curated_library))
            return ImagePayload(
                url=entry.get("url"),
                provider="curated",
                alt_text=entry.get("alt_text", post.alt_text),
            )

        raise


def generate_video_for_post(
    *,
    campaign: CampaignConfig,
    post: GeneratedPost,
    pillar,
    target_dir: Path,
) -> Optional[Path]:
    """Generate a Veo 2 video for pillars with use_veo=True.

    Tries Replicate (Veo 2) first, then Google Veo REST, returns None on full failure
    so the caller can fall back to a static Imagen image.
    16:9 aspect ratio is the only one Veo accepts.
    """
    video_prompt = pillar.video_prompt or post.video_prompt or (
        f"Cinematic 16:9 professional footage evoking {pillar.angle.lower()[:80]}. "
        "Atmospheric, documentary style, warm professional colour grade."
    )

    replicate_token = os.getenv("REPLICATE_API_TOKEN")
    if replicate_token:
        try:
            provider = ReplicateVideoProvider(campaign.image_provider)
            video_path = provider.get_video(prompt=video_prompt, target_dir=target_dir)
            logging.info("Veo 2 (Replicate) video generated: %s", video_path)
            return video_path
        except Exception as exc:
            logging.warning("Replicate Veo 2 failed, trying Google Veo REST: %s", exc)

    try:
        provider_cfg = ImageProviderConfig(
            provider="google",
            model="veo-2.0-generate-001",
            size="1920x1080",
        )
        google_provider = GoogleImagenProvider(provider_cfg)
        video_path = google_provider.get_video(prompt=video_prompt, target_dir=target_dir)
        logging.info("Google Veo video generated: %s", video_path)
        return video_path
    except Exception as exc:
        logging.warning("Google Veo also failed — falling back to static image: %s", exc)

    return None


def slugify(text: str) -> str:
    clean = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    while "--" in clean:
        clean = clean.replace("--", "-")
    return clean.strip("-") or "post"


def save_artifacts(
    *,
    output_dir: Path,
    post: GeneratedPost,
    image: ImagePayload,
    extra_metadata: Optional[Dict[str, str]] = None,
) -> Path:
    timestamp = post.created_at.strftime("%Y%m%d_%H%M")
    slug = slugify(post.pillar_name)
    base_path = output_dir / f"{timestamp}_{slug}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save post text
    copy_path = base_path.with_suffix(".txt")
    copy_path.write_text(post.as_text)

    # Build metadata
    metadata = post.as_mapping()
    metadata["copy_file"] = copy_path.name

    if image.path:
        metadata["image_file"] = image.path.name
    if image.url:
        metadata["image_url"] = image.url
    if image.provider:
        metadata["image_provider"] = image.provider
    if image.alt_text:
        metadata["image_alt_text"] = image.alt_text

    if extra_metadata:
        metadata.update(extra_metadata)

    metadata_path = base_path.with_suffix(".json")
    metadata_path.write_text(json.dumps(metadata, indent=2))

    return metadata_path


def ensure_local_image_file(
    image: ImagePayload,
    fallback_dir: Path,
    timestamp: datetime,
) -> Optional[Path]:
    """Ensure we have a local image file for publishing."""
    if image.path and image.path.exists():
        return image.path

    if image.url:
        # Download the image
        try:
            response = requests.get(image.url, timeout=30)
            response.raise_for_status()

            fallback_dir.mkdir(parents=True, exist_ok=True)
            filename = f"seta_image_{timestamp.strftime('%Y%m%d_%H%M%S')}.jpg"
            local_path = fallback_dir / filename
            local_path.write_bytes(response.content)
            return local_path
        except Exception as e:
            logging.warning(f"Failed to download image from URL: {e}")

    return None


def run_single_generation(
    *,
    campaign: CampaignConfig,
    generator: SetaLinkedInPostGenerator,
    output_dir: Path,
    publisher: Optional[LinkedInPublisher],
) -> None:
    """Run a single post generation."""
    logging.info("Running single Seta Capital post generation")

    tz = ZoneInfo(campaign.timezone)
    scheduled_for = datetime.now(tz=tz)

    # Load rotation state
    rotation_path = output_dir / ROTATION_STATE_FILENAME
    rotation_state = RotationState.load(rotation_path)

    # Select pillar (rotate through all pillars)
    pillar_idx = rotation_state.next_pillar_index(len(campaign.pillars))
    pillar = campaign.pillars[pillar_idx]
    post_type = "technical"  # All Seta posts are analytical/technical

    logging.info(f"Selected pillar: {pillar.name} (news_search={pillar.use_news_search})")

    # Generate video (for Veo pillars) or image
    images_dir = output_dir / "images"
    videos_dir = output_dir / "videos"

    video_path: Optional[Path] = None
    image_payload: Optional[ImagePayload] = None
    chart_data_summary: str = ""

    if pillar.use_chart:
        # Market Intelligence pillar — fetch data and render chart FIRST so the
        # LLM can reference the actual numbers in the post copy.
        chart_type_idx = rotation_state.next_chart_type_index()
        logging.info("Pillar '%s' uses chart (type index %d)", pillar.name, chart_type_idx)
        chart_gif_path, chart_data_summary = generate_market_chart(
            target_dir=images_dir,
            chart_type_index=chart_type_idx,
            n_frames=25,
            frame_ms=120,
        )
        if not chart_gif_path:
            logging.error(
                "Chart data fetch failed for pillar '%s': %s — skipping post.",
                pillar.name, chart_data_summary,
            )
            return

    # Generate post (chart pillars pass live data summary so LLM references real numbers)
    post = generator.generate(
        pillar=pillar,
        scheduled_for=scheduled_for,
        post_type=post_type,
        image_mode="photo",
        chart_data=chart_data_summary,
    )

    if pillar.use_chart:
        image_payload = ImagePayload(
            prompt=pillar.image_prompt or post.image_prompt or f"Market data chart for {pillar.name}",
            provider="seta_chart_generator",
            path=chart_gif_path,
            alt_text=post.alt_text,
        )

    elif pillar.use_veo:
        logging.info("Pillar '%s' uses Veo — attempting video generation", pillar.name)
        try:
            video_path = generate_video_for_post(
                campaign=campaign,
                post=post,
                pillar=pillar,
                target_dir=videos_dir,
            )
        except Exception as e:
            logging.error("Video generation raised unexpectedly: %s", e)

        if video_path is None:
            # Veo failed — fall back to Imagen static image so the post still goes out
            logging.warning(
                "Veo generation failed for pillar '%s' — falling back to Imagen image.",
                pillar.name,
            )
            try:
                image_payload = generate_image_for_post(
                    campaign=campaign,
                    post=post,
                    target_dir=images_dir,
                )
            except Exception as _img_err:
                logging.error("Imagen fallback also failed for '%s': %s — skipping.", pillar.name, _img_err)
                return

    else:
        # Non-Veo, non-chart pillar (e.g. Industry Expertise) — animated GIF image
        try:
            image_payload = generate_image_for_post(
                campaign=campaign,
                post=post,
                target_dir=images_dir,
            )
        except Exception as e:
            logging.error(f"Image generation failed: {e}")
            image_payload = ImagePayload(provider="none", alt_text=post.alt_text)

    # Publish if configured
    extra_metadata: Dict[str, str] = {
        "pillar": pillar.name,
        "post_type": post_type,
        "news_enabled": str(pillar.use_news_search),
        "media_type": "video" if video_path else "image",
    }

    if post.news_articles:
        extra_metadata["news_sources"] = ", ".join(a.source for a in post.news_articles)

    if publisher:
        if video_path:
            publish_result = publisher.publish_video_post(
                text=post.as_text,
                headline=post.headline,
                alt_text=post.alt_text,
                video_path=video_path,
            )
        else:
            local_image_path = ensure_local_image_file(
                image=image_payload,
                fallback_dir=images_dir,
                timestamp=scheduled_for,
            )
            publish_result = publisher.publish_post(
                text=post.as_text,
                headline=post.headline,
                alt_text=post.alt_text,
                image_path=local_image_path,
            )

        if publish_result.get("share_urn"):
            extra_metadata["linkedin_share_urn"] = publish_result["share_urn"]
            logging.info(f"Published to LinkedIn: {publish_result['share_urn']}")

    # Save artifacts
    used_image = image_payload or ImagePayload(provider="none", alt_text=post.alt_text)
    if video_path:
        extra_metadata["video_file"] = video_path.name
    metadata_path = save_artifacts(
        output_dir=output_dir,
        post=post,
        image=used_image,
        extra_metadata=extra_metadata,
    )
    logging.info(f"Saved post artefacts to {metadata_path}")

    # Update rotation state
    rotation_state.last_post_type = post_type
    rotation_state.save(rotation_path)

    # Print the generated post
    print("\n" + "=" * 60)
    print("GENERATED SETA CAPITAL POST")
    print("=" * 60)
    print(post.as_text)
    print("=" * 60)
    if post.news_articles:
        print("\nNews sources used:")
        for article in post.news_articles:
            print(f"  - {article.title}")
            print(f"    {article.url}")
    print()


def build_scheduled_job(
    *,
    campaign: CampaignConfig,
    generator: SetaLinkedInPostGenerator,
    output_dir: Path,
    publisher: Optional[LinkedInPublisher],
) -> callable:
    """Build the scheduled job function."""
    def job():
        run_single_generation(
            campaign=campaign,
            generator=generator,
            output_dir=output_dir,
            publisher=publisher,
        )
    return job


def main() -> None:
    load_dotenv()
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Load campaign configuration
    if not args.campaign_config.exists():
        logging.error(f"Campaign config not found: {args.campaign_config}")
        logging.info("Creating default Seta Capital campaign config...")
        # The config should already exist at ~/seta_linkedin_campaign.yaml
        raise FileNotFoundError(f"Please ensure {args.campaign_config} exists")

    campaign = CampaignConfig.from_yaml(args.campaign_config)
    logging.info(f"Loaded campaign with {len(campaign.pillars)} pillars")

    # Load strategy
    strategy = load_strategy(args.strategy_file, args.strategy_text)

    # Create LLM client
    llm_client = create_llm_client(provider=args.llm_provider, model=args.llm_model)

    # Create generator
    generator = SetaLinkedInPostGenerator(
        campaign=campaign,
        llm_client=llm_client,
        strategy_text=strategy,
    )

    # Create publisher if configured
    publisher = None
    if args.publish and args.linkedin_owner and args.linkedin_access_token:
        publisher_config = LinkedInPublisherConfig(
            owner_urn=args.linkedin_owner,
            access_token=args.linkedin_access_token,
        )
        publisher = LinkedInPublisher(publisher_config)
        logging.info("LinkedIn publishing enabled")

    # Ensure output directory
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.run_once:
        run_single_generation(
            campaign=campaign,
            generator=generator,
            output_dir=output_dir,
            publisher=publisher,
        )
        return

    # Run scheduled
    scheduler = BlockingScheduler()
    job = build_scheduled_job(
        campaign=campaign,
        generator=generator,
        output_dir=output_dir,
        publisher=publisher,
    )

    tz = ZoneInfo(campaign.timezone)
    for slot in campaign.schedule_slots:
        hour, minute = map(int, slot.time.split(":"))
        trigger = CronTrigger(
            day_of_week=slot.day.lower()[:3],
            hour=hour,
            minute=minute,
            timezone=tz,
        )
        scheduler.add_job(job, trigger)
        logging.info(f"Scheduled post for {slot.day} at {slot.time} ({campaign.timezone})")

    logging.info("Seta Capital LinkedIn scheduler started. Press Ctrl+C to exit.")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        logging.info("Scheduler stopped by user")


if __name__ == "__main__":
    main()
