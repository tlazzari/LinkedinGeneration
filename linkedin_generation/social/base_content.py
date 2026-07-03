"""Shared base for per-brand LinkedIn content generators (TNT, Seta, …).

Holds the pieces that are identical across every brand: the GeneratedPost
artifact model, LLM JSON-response parsing, and hashtag merging. A brand's
generator subclasses BaseContentGenerator and implements generate() /
_build_prompt() with its own voice, pillars and enrichment (news, charts, …).

Adding a brand = add a Brand descriptor in social.brand + a subclass here-style
generator; the shared plumbing below is reused unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Sequence

from .campaign_config import CampaignConfig, PostPillar


@dataclass
class GeneratedPost:
    """Structured representation of a LinkedIn post (brand-agnostic superset).

    `news_articles` is populated only by news-aware brands (e.g. Seta); brands
    that don't use news leave it empty and as_mapping() omits the news block.
    """

    pillar_name: str
    target_client: str
    headline: str
    body: str
    cta: str
    hashtags: Sequence[str]
    image_prompt: str
    video_prompt: str
    alt_text: str
    created_at: datetime
    metadata: Dict[str, str] = field(default_factory=dict)
    news_articles: List[Any] = field(default_factory=list)

    @property
    def as_text(self) -> str:
        hashtags_block = " ".join(self.hashtags)
        return "\n\n".join(
            part
            for part in (
                self.headline.strip(),
                self.body.strip(),
                self.cta.strip(),
                hashtags_block.strip(),
            )
            if part
        )

    def as_mapping(self) -> Dict[str, Any]:
        payload = {
            "pillar": self.pillar_name,
            "target_client": self.target_client,
            "headline": self.headline,
            "body": self.body,
            "cta": self.cta,
            "hashtags": list(self.hashtags),
            "image_prompt": self.image_prompt,
            "video_prompt": self.video_prompt,
            "alt_text": self.alt_text,
            "created_at": self.created_at.isoformat(),
        }
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        if self.news_articles:
            payload["news_sources"] = [
                {"title": a.title, "url": a.url, "source": a.source}
                for a in self.news_articles
            ]
        return payload


class BaseContentGenerator:
    """Shared constructor + JSON parsing + hashtag merge for brand generators."""

    def __init__(
        self,
        *,
        campaign: CampaignConfig,
        llm_client: Any,
        strategy_text: str,
    ) -> None:
        self.campaign = campaign
        self.llm_client = llm_client
        self.strategy_text = strategy_text.strip()
        if not self.strategy_text:
            raise ValueError("Strategy text must not be empty")

    def _parse_response(self, raw: str) -> Dict[str, Any]:
        sanitized = raw.strip()

        def try_parse(candidate: str) -> Dict[str, Any] | None:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                return None

        parsed = try_parse(sanitized)
        if parsed is not None:
            return parsed

        if sanitized.startswith("```"):
            lines = sanitized.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            while lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            sanitized = "\n".join(lines).strip()
            parsed = try_parse(sanitized)
            if parsed is not None:
                return parsed

        for opener, closer in (("{", "}"), ("[", "]")):
            start = sanitized.find(opener)
            end = sanitized.rfind(closer)
            if start != -1 and end != -1 and end > start:
                candidate = sanitized[start : end + 1]
                parsed = try_parse(candidate)
                if parsed is not None:
                    return parsed

        raise ValueError(f"LLM response was not valid JSON: {raw}")

    def _merge_hashtags(self, existing: list[str], pillar: PostPillar) -> list[str]:
        seen: set[str] = set()
        merged: list[str] = []
        for tag in [*existing, *pillar.hashtags, *self.campaign.default_hashtags]:
            norm = tag.strip()
            if not norm:
                continue
            if not norm.startswith("#"):
                norm = f"#{norm.replace(' ', '')}"
            upper = norm.upper()
            if upper in seen:
                continue
            seen.add(upper)
            merged.append(norm)
        return merged


__all__ = ["GeneratedPost", "BaseContentGenerator"]
