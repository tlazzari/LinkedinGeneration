"""Brand registry for the LinkedIn pipeline.

Each brand (TNT Motion, Seta Capital, …) is declared once here as a `Brand`
descriptor: its content-generator class, config/state file names, LinkedIn
publishing env keys, and capability flags. The shared pipeline
(`base_content`, `artifacts`, the schedulers) reuses everything else.

To add a new brand later:
  1. Write a content generator that subclasses
     `social.base_content.BaseContentGenerator` (implement generate() /
     _build_prompt() with the brand's voice and enrichment).
  2. Add a `Brand(...)` entry to `BRANDS` below (or call `register_brand()`
     at runtime) with the brand's env keys, config paths and capability set.
  3. Add its campaign + holiday YAML under config/ and a cron entry.
No changes to the shared modules are needed.

Capability flags describe which enrichment a brand's pipeline uses, so a future
unified runner can branch on them instead of on the brand name:
  news, charts, video, logo_overlay, biweekly_site_update, animated_gif, holiday
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Type

from .base_content import BaseContentGenerator
from .content_generation import LinkedInPostGenerator
from .seta_content_generation import SetaLinkedInPostGenerator


@dataclass(frozen=True)
class Brand:
    key: str
    display_name: str
    generator: Type[BaseContentGenerator]

    # config resolution (env var name, then default relative path)
    campaign_config_env: str
    default_campaign_config: str
    strategy_file_env: str
    default_strategy_file: str
    strategy_text_env: str
    holiday_config_env: str
    default_holiday_config: str

    # runtime state / output artifacts
    output_dir: str
    campaign_state_file: str
    rotation_state_file: str
    sentinel_log: str

    # LinkedIn publishing credentials (env var names)
    owner_env: str
    token_env: str

    # which enrichment this brand's pipeline uses
    capabilities: FrozenSet[str]

    def has(self, capability: str) -> bool:
        return capability in self.capabilities


BRANDS: Dict[str, Brand] = {
    "tnt": Brand(
        key="tnt",
        display_name="TNT Motion",
        generator=LinkedInPostGenerator,
        campaign_config_env="LINKEDIN_CAMPAIGN_CONFIG",
        default_campaign_config="config/linkedin_campaign.yaml",
        strategy_file_env="STRATEGY_FILE",
        default_strategy_file="config/strategy.txt",
        strategy_text_env="STRATEGY_TEXT",
        holiday_config_env="",  # TNT holiday config is CLI-default only, no env override
        default_holiday_config="config/holiday_campaign.yaml",
        output_dir="linkedin_generation/linkedin_posts",
        campaign_state_file="campaign_state.json",
        rotation_state_file="scheduler_state.json",
        sentinel_log="tnt_linkedin_daily.log",
        owner_env="LINKEDIN_OWNER_URN",
        token_env="LINKEDIN_ACCESS_TOKEN",
        capabilities=frozenset({"logo_overlay", "biweekly_site_update", "animated_gif", "holiday"}),
    ),
    "seta": Brand(
        key="seta",
        display_name="Seta Capital",
        generator=SetaLinkedInPostGenerator,
        # Seta's live config is driven by SETA_CAMPAIGN_CONFIG (points into the SEO
        # repo, /opt/seo/config/seta_capital_linkedin.yaml); the ~/… path is the
        # code-level fallback default.
        campaign_config_env="SETA_CAMPAIGN_CONFIG",
        default_campaign_config="~/seta_linkedin_campaign.yaml",
        strategy_file_env="SETA_STRATEGY_FILE",
        default_strategy_file="~/seta_strategy.txt",
        strategy_text_env="SETA_STRATEGY_TEXT",
        holiday_config_env="SETA_HOLIDAY_CONFIG",
        default_holiday_config="config/seta_holiday_campaign.yaml",
        output_dir="linkedin_generation/seta_posts",
        campaign_state_file="seta_campaign_state.json",
        rotation_state_file="seta_scheduler_state.json",
        sentinel_log="seta_linkedin_daily.log",
        owner_env="SETA_LINKEDIN_OWNER_URN",
        token_env="SETA_LINKEDIN_ACCESS_TOKEN",
        capabilities=frozenset({"news", "charts", "video", "holiday"}),
    ),
}


def get_brand(key: str) -> Brand:
    try:
        return BRANDS[key]
    except KeyError:
        raise KeyError(f"Unknown brand '{key}'. Known brands: {', '.join(sorted(BRANDS))}")


def register_brand(brand: Brand) -> None:
    """Register a new brand at runtime (extension hook for future brands)."""
    BRANDS[brand.key] = brand


__all__ = ["Brand", "BRANDS", "get_brand", "register_brand"]
