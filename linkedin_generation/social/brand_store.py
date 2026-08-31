"""Brand definitions edited from the CRM, loaded into the pipeline at runtime.

The registry in `brand.py` is the code-level truth for brands that ship with the
pipeline (TNT, Seta). This module is the data-level layer on top: one JSON file
per brand under `config/brands/`, written by the CRM admin page
(`/social-standalone/`), read here.

Two things it can do:

  * **Override** a brand that already exists in code - its voice (tone preset,
    banned vocabulary, filler limit) and its training material.
  * **Add** a brand that exists nowhere in code, by cloning the pipeline
    behaviour of a template brand ("seta" for data/news/chart posts, "tnt" for
    product/application posts) and pointing it at its own LinkedIn page,
    config and credentials.

JSON rather than YAML because the CRM runs PHP 8.3 without the yaml extension
but with json; the pipeline reads either happily. Files are world-readable and
group-writable by www-data, so the web UI can save without running as root.

Nothing here fails loudly: a malformed or half-written brand file is logged and
skipped, because a scheduler run at 08:00 must not die because someone was
editing a form.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional

from .brand import BRANDS, Brand, register_brand
from .post_quality import BrandVoice

logger = logging.getLogger(__name__)

BRAND_DIR = Path(os.getenv("LINKEDIN_BRAND_DIR", "/opt/linkedin/config/brands"))

# Tone presets. These are the switch settings behind the three choices offered
# in the CRM, so the UI never has to know about the individual flags.
TONE_PRESETS: Dict[str, Dict[str, bool]] = {
    # Worth resharing from a personal profile: no sales register, brand named
    # once at the end, always ends on a question.
    "reshareable": {
        "ban_promotional": True,
        "brand_in_closing_only": True,
        "require_closing_question": True,
    },
    # A sales channel: direct CTA wanted, brand named in the copy, still ends on
    # a question because that is what the feed amplifies.
    "promotional": {
        "ban_promotional": False,
        "brand_in_closing_only": False,
        "require_closing_question": True,
    },
    # Announcements only - no conversation expected.
    "announcement": {
        "ban_promotional": False,
        "brand_in_closing_only": False,
        "require_closing_question": False,
    },
}

DEFAULT_TONE = "reshareable"


def _config_files() -> List[Path]:
    if not BRAND_DIR.is_dir():
        return []
    return sorted(p for p in BRAND_DIR.glob("*.json") if p.is_file())


def load_configs() -> Dict[str, dict]:
    """Every brand config on disk, keyed by brand key. Bad files are skipped."""
    configs: Dict[str, dict] = {}
    for path in _config_files():
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipping unreadable brand config %s: %s", path.name, exc)
            continue
        key = str(data.get("key") or path.stem).strip()
        if not key:
            logger.warning("Skipping brand config %s: no key", path.name)
            continue
        configs[key] = data
    return configs


def voice_from_config(config: dict, fallback: Optional[BrandVoice] = None) -> BrandVoice:
    """Build a BrandVoice from a stored config, applying the tone preset."""
    voice_cfg = config.get("voice") or {}
    tone = str(config.get("tone") or DEFAULT_TONE)
    preset = TONE_PRESETS.get(tone, TONE_PRESETS[DEFAULT_TONE])

    name = str(config.get("display_name") or voice_cfg.get("name") or "").strip()
    if not name and fallback:
        name = fallback.name

    terms = voice_cfg.get("overused_headline_terms")
    if terms is None:
        terms = list(fallback.overused_headline_terms) if fallback else []
    terms = tuple(str(t).strip().lower() for t in terms if str(t).strip())

    # "custom" keeps whatever switches were saved; a named preset wins over them.
    if tone == "custom":
        switches = {
            flag: bool(voice_cfg.get(flag, getattr(fallback, flag) if fallback else True))
            for flag in ("ban_promotional", "brand_in_closing_only", "require_closing_question")
        }
    else:
        switches = dict(preset)

    try:
        max_filler = float(voice_cfg.get("max_filler_per_100_words", 2.5))
    except (TypeError, ValueError):
        max_filler = 2.5

    return BrandVoice(
        name=name or "Company",
        overused_headline_terms=terms,
        max_filler_per_100_words=max_filler,
        **switches,
    )


def extra_context(brand_key: str, max_chars: int = 6000) -> str:
    """Training material for a brand, formatted for the prompt.

    Ordered by how strongly each kind steers an LLM: example posts first (a
    style anchor beats any amount of description), then positioning, then
    proof points, then uploaded documents - which are truncated last because
    they are the bulkiest and the least specific.
    """
    config = load_configs().get(brand_key)
    if not config:
        return ""

    blocks: List[str] = []

    examples = [str(p).strip() for p in (config.get("example_posts") or []) if str(p).strip()]
    if examples:
        shown = "\n\n---\n\n".join(examples[:5])
        blocks.append(
            "POSTS THIS COMPANY CONSIDERS GOOD - match this voice, structure and "
            f"level of concreteness (do NOT copy their content):\n{shown}"
        )

    strategy = str(config.get("strategy") or "").strip()
    if strategy:
        blocks.append(f"POSITIONING AND STRATEGY:\n{strategy}")

    points = [str(p).strip() for p in (config.get("proof_points") or []) if str(p).strip()]
    if points:
        blocks.append("PROOF POINTS:\n" + "\n".join(f"- {p}" for p in points))

    website = str(config.get("website") or "").strip()
    if website:
        blocks.append(f"COMPANY WEBSITE: {website}")

    material = config.get("material") or []
    texts = []
    for item in material:
        text = str((item or {}).get("text") or "").strip()
        if text:
            texts.append(f"[{(item or {}).get('name', 'document')}]\n{text}")
    if texts:
        blocks.append("BACKGROUND MATERIAL:\n" + "\n\n".join(texts))

    joined = "\n\n".join(blocks)
    if len(joined) > max_chars:
        joined = joined[:max_chars].rsplit(" ", 1)[0] + "\n[...truncated]"
    return joined


def apply_configs() -> List[str]:
    """Merge stored configs into the in-memory registry. Returns keys applied.

    Called at import of `social`, so every entry point - schedulers, tests, the
    CRM preview - sees the same brands without anyone remembering to call it.
    """
    applied: List[str] = []
    for key, config in load_configs().items():
        if not config.get("enabled", True):
            continue
        existing = BRANDS.get(key)
        try:
            if existing:
                register_brand(replace(existing, voice=voice_from_config(config, existing.voice)))
            else:
                template_key = str(config.get("template") or "seta")
                template = BRANDS.get(template_key)
                if not template:
                    logger.warning(
                        "Brand '%s' names unknown template '%s' - skipped", key, template_key
                    )
                    continue
                register_brand(
                    replace(
                        template,
                        key=key,
                        display_name=str(config.get("display_name") or key),
                        voice=voice_from_config(config, template.voice),
                        owner_env=str(config.get("owner_env") or template.owner_env),
                        token_env=str(config.get("token_env") or template.token_env),
                        campaign_config_env=str(
                            config.get("campaign_config_env") or template.campaign_config_env
                        ),
                        default_campaign_config=str(
                            config.get("campaign_config") or template.default_campaign_config
                        ),
                        output_dir=f"linkedin_generation/{key}_posts",
                        campaign_state_file=f"{key}_campaign_state.json",
                        rotation_state_file=f"{key}_scheduler_state.json",
                        sentinel_log=f"{key}_linkedin_daily.log",
                    )
                )
            applied.append(key)
        except Exception as exc:  # a bad config must never stop a scheduled run
            logger.warning("Could not apply brand config '%s': %s", key, exc)
    return applied


__all__ = [
    "BRAND_DIR",
    "DEFAULT_TONE",
    "TONE_PRESETS",
    "apply_configs",
    "extra_context",
    "load_configs",
    "voice_from_config",
]
