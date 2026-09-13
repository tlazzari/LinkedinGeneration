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


# House rules every brand inherits no matter what it sells. These live in the
# pipeline, not in a pillar, so a tenant cannot switch them off and does not have
# to know they exist: media composed from the post's own subject
# (media_prompts.compose_media_prompt), posts built on real fetched news
# (news_search), and plain English for second-language readers
# (post_quality.PLAIN_ENGLISH_DIRECTIVE + the gate).
INHERITED_RULES = ("coherent_media", "real_news", "plain_english")


def default_env_names(brand_key: str) -> tuple[str, str]:
    """(owner_env, token_env) derived from the brand key, never inherited.

    THE LEAK THIS CLOSES (found 2026-09-13 by creating a test tenant): a new brand
    cloned owner_env/token_env from its template, so a tenant that had not been
    given its own credentials inherited LINKEDIN_OWNER_URN and
    LINKEDIN_ACCESS_TOKEN - TNT Motion's, and both are set in /opt/linkedin/.env.
    Running that tenant with --publish would have posted its content to TNT's own
    LinkedIn page. Deriving the names from the key means an unconfigured tenant
    resolves to variables that do not exist, so publishing fails closed.
    """
    stem = "".join(ch if ch.isalnum() else "_" for ch in brand_key).upper().strip("_")
    return f"{stem}_LINKEDIN_OWNER_URN", f"{stem}_LINKEDIN_ACCESS_TOKEN"


def campaign_for(brand_key: str) -> Optional["object"]:
    """A CampaignConfig built from the tenant's OWN pillars, or None.

    Before 2026-09-13 a tenant brand inherited `default_campaign_config` from the
    template it cloned - i.e. Seta's or TNT's campaign YAML in full. A furniture
    maker cloning the Seta template therefore posted about cross-border
    China-Europe M&A, and searched Chinese M&A news to do it. Pillars are now the
    tenant's own; only the RULES above are inherited.

    Returns None when the brand has no pillars of its own, which is the case for
    TNT and Seta themselves - they keep their YAML.
    """
    from .campaign_config import CampaignConfig

    config = load_configs().get(brand_key) or {}
    pillars = config.get("pillars") or []
    if not pillars:
        return None

    entries = []
    for p in pillars:
        if not isinstance(p, dict) or not str(p.get("name") or "").strip():
            continue
        entries.append({
            "name": str(p["name"]).strip(),
            "target_client": str(p.get("target_client") or "").strip(),
            "angle": str(p.get("angle") or "").strip(),
            "proof_points": [str(x) for x in (p.get("proof_points") or []) if str(x).strip()],
            "ctas": [str(x) for x in (p.get("ctas") or []) if str(x).strip()],
            "hashtags": [str(x) for x in (p.get("hashtags") or []) if str(x).strip()],
            "image_prompt": p.get("image_prompt") or None,
            # News is ON unless the tenant explicitly turned it off: a post built
            # on something that actually happened is the whole point.
            "use_news_search": bool(p.get("use_news_search", True)),
            "news_queries": [str(q) for q in (p.get("news_queries") or []) if str(q).strip()],
            # Video is opt-in per pillar and needs a prompt; the subject is
            # composed per post, so this is only the house look.
            "use_veo": bool(p.get("use_veo", False)),
            "video_prompt": p.get("video_prompt") or None,
            "use_chart": bool(p.get("use_chart", False)),
        })
    if not entries:
        return None

    data = {
        "defaults": {
            "tone": str(config.get("tone_text") or config.get("strategy") or "").strip()
                    or "Professional, concrete and readable.",
            "hashtags": [str(h) for h in (config.get("default_hashtags") or []) if str(h).strip()],
        },
        "schedule": config.get("schedule") or {},
        "content_pillars": entries,
        "output": {"directory": f"linkedin_generation/{brand_key}_posts"},
        # Without this the tenant inherits ImageProviderConfig's dataclass
        # defaults - provider "openai", model "gpt-image-1" - which the pipeline's
        # Google client then 404s on ("models/gpt-image-1 is not found"). The
        # first tenant run produced a post with no image at all for exactly this
        # reason. Same settings the built-in brands use; a brand config may
        # override the block wholesale.
        "image_provider": config.get("image_provider") or {
            "provider": "google-imagen",
            "model": "gemini-3.1-flash-image",
            "size": "1080x1080",
            "style_hint": (
                "Photorealistic professional photography. Warm natural lighting. "
                "Real people doing the work described. No text, no logos, no "
                "branding in frame."
            ),
            "use_animated_gif": True,
            "gif_num_frames": 5,
            "gif_frame_duration": 900,
            "curated_library": [],
            "aspect_ratio": "1:1",
        },
    }
    try:
        return CampaignConfig.from_mapping(data)
    except Exception as exc:
        logger.warning("Brand '%s' has unusable pillars: %s", brand_key, exc)
        return None


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
                # The template picks the VOICE. The GENERATOR is always Seta's,
                # because that is the one carrying the three rules every brand
                # inherits (see INHERITED_RULES): it fetches real news, composes
                # media from the post's own subject, and runs the plain-English
                # gate. TNT's generator has no news path and does not compose
                # media, so cloning it would silently drop two of the three.
                template_key = str(config.get("template") or "seta")
                template = BRANDS.get(template_key)
                if not template:
                    logger.warning(
                        "Brand '%s' names unknown template '%s' - skipped", key, template_key
                    )
                    continue
                rules_source = BRANDS.get("seta") or template
                default_owner, default_token = default_env_names(key)
                register_brand(
                    replace(
                        template,
                        key=key,
                        generator=rules_source.generator,
                        capabilities=rules_source.capabilities,
                        display_name=str(config.get("display_name") or key),
                        voice=voice_from_config(config, template.voice),
                        owner_env=str(config.get("owner_env") or default_owner),
                        token_env=str(config.get("token_env") or default_token),
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
    "INHERITED_RULES",
    "campaign_for",
    "default_env_names",
    "DEFAULT_TONE",
    "TONE_PRESETS",
    "apply_configs",
    "extra_context",
    "load_configs",
    "voice_from_config",
]
