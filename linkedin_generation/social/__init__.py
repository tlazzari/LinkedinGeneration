"""Utilities for TNT Motion social content generation."""

from .campaign_config import CampaignConfig, PostPillar  # noqa: F401
from .content_generation import LinkedInPostGenerator, GeneratedPost  # noqa: F401
from .image_providers import ImageProviderConfig, ImagePayload  # noqa: F401
from .linkedin_client import LinkedInPublisher  # noqa: F401
from .base_content import BaseContentGenerator  # noqa: F401
from .brand import Brand, BRANDS, get_brand, register_brand  # noqa: F401
from .brand_store import apply_configs, extra_context  # noqa: F401

# Brands edited in the CRM are merged over the code-level registry here, so
# every entry point (schedulers, tests, the CRM preview) sees the same set.
apply_configs()

__all__ = [
    "apply_configs",
    "extra_context",
    "CampaignConfig",
    "PostPillar",
    "LinkedInPostGenerator",
    "GeneratedPost",
    "ImageProviderConfig",
    "ImagePayload",
    "LinkedInPublisher",
    "BaseContentGenerator",
    "Brand",
    "BRANDS",
    "get_brand",
    "register_brand",
]
