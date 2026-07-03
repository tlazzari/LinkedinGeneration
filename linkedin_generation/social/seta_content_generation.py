"""LLM-powered LinkedIn post generator for Seta Capital with news integration."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, TYPE_CHECKING

from .campaign_config import CampaignConfig, PostPillar
from .news_search import NewsArticle, search_news_for_pillar, build_news_context
from .base_content import GeneratedPost, BaseContentGenerator

if TYPE_CHECKING:
    from linkedin_generation.holiday.calendars import HolidayEvent

logger = logging.getLogger(__name__)


class SetaLinkedInPostGenerator(BaseContentGenerator):
    """Delegate that orchestrates prompt building and parsing for Seta Capital.

    Shared GeneratedPost / __init__ / _parse_response / _merge_hashtags live in
    social.base_content.BaseContentGenerator; GeneratedPost carries the optional
    news_articles list this brand populates.
    """

    def generate(
        self,
        *,
        pillar: PostPillar,
        scheduled_for: datetime,
        post_type: str,
        image_mode: str,
        holiday: "HolidayEvent" | None = None,
        chart_data: str = "",
    ) -> GeneratedPost:
        # Search for news if pillar requires it
        news_articles: List[NewsArticle] = []
        news_context = ""
        if pillar.use_news_search and post_type != "holiday":
            logger.info(f"Searching for news articles for pillar: {pillar.name}")
            news_articles = search_news_for_pillar(pillar.name, num_articles=3)
            news_context = build_news_context(news_articles)
            if news_articles:
                logger.info(f"Found {len(news_articles)} news articles to reference")
            else:
                logger.warning(f"No news articles found for pillar: {pillar.name}")

        raw = self.llm_client.complete(
            self._build_prompt(
                pillar=pillar,
                post_type=post_type,
                image_mode=image_mode,
                holiday=holiday,
                news_context=news_context,
                chart_data=chart_data,
            ),
            temperature=0.8,
            max_tokens=800,
        )
        payload = self._parse_response(raw)

        # Strip hallucinated URLs when no real news sources were provided
        if not news_articles and post_type != "holiday":
            import re
            url_pattern = re.compile(r'\[?(https?://[^\s\]\)]+)\]?(?:\([^\)]+\))?')
            for field in ("body", "headline", "cta"):
                if field in payload and isinstance(payload[field], str):
                    cleaned = url_pattern.sub('', payload[field])
                    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
                    payload[field] = cleaned
        hashtags = payload.get("hashtags") or []
        if isinstance(hashtags, str):
            hashtags = [tag.strip() for tag in hashtags.split() if tag.strip()]

        all_hashtags = self._merge_hashtags(list(hashtags), pillar)

        # YAML pillar.image_prompt is authoritative (human-reviewed, people-centric).
        # LLM image_prompt is only used if the YAML has nothing set.
        base_image_prompt = pillar.image_prompt or payload.get("image_prompt") or pillar.angle
        if news_articles and pillar.use_news_search:
            # Enhance image prompt with news context
            news_summary = news_articles[0].title if news_articles else ""
            image_prompt = f"{base_image_prompt} Inspired by this news: {news_summary}"
        else:
            image_prompt = base_image_prompt

        video_prompt = payload.get("video_prompt") or f"Professional footage related to {pillar.angle.lower()}"
        alt_text = payload.get("alt_text") or f"Seta Capital insights on {pillar.name}"

        metadata: Dict[str, str] = {
            "post_type": post_type,
            "image_mode": image_mode,
            "campaign": "seta_capital",
        }
        if holiday:
            metadata["holiday_name"] = holiday.name
            metadata["holiday_locale"] = holiday.locale
        if news_articles:
            metadata["news_sources_count"] = str(len(news_articles))

        return GeneratedPost(
            pillar_name=pillar.name,
            target_client=pillar.target_client,
            headline=payload.get("headline", "Seta Capital Insights"),
            body=payload.get("body", ""),
            cta=payload.get("cta", "Connect with Seta Capital to explore opportunities."),
            hashtags=all_hashtags,
            image_prompt=image_prompt,
            video_prompt=video_prompt,
            alt_text=alt_text,
            created_at=scheduled_for,
            metadata=metadata,
            news_articles=news_articles,
        )

    def _build_prompt(
        self,
        *,
        pillar: PostPillar,
        post_type: str,
        image_mode: str,
        holiday: "HolidayEvent" | None = None,
        news_context: str = "",
        chart_data: str = "",
    ) -> str:
        proof_points = "\n".join(f"- {item}" for item in pillar.proof_points) or "- Strategic M&A advisory\n- Cross-border expertise"
        ctas = ", ".join(pillar.ctas or ["Connect with our advisory team", "Request a strategic briefing"])
        hashtag_pool = " ".join(self._merge_hashtags([], pillar))
        image_hint = pillar.image_prompt or "Professional corporate imagery"

        holiday_name = holiday.name if holiday else "the holiday"
        holiday_locale = holiday.locale if holiday else "our markets"

        if post_type == "promotional":
            post_directives = (
                "- BODY: deliver an authoritative strategic insight about cross-border M&A, "
                "deal-making, or a specific industry vertical. Do NOT mention Seta Capital in the body.\n"
                "- Speak as an expert operator sharing a point of view, not as a firm advertising itself.\n"
                "- The Seta Capital connection belongs ONLY in the final paragraph (cta field)."
            )
        elif post_type == "technical":
            if news_context and pillar.use_news_search:
                url_directive = "- Include the FULL URL from the provided news sources (see below).\n"
            else:
                url_directive = "- Do NOT include any external links or URLs in the post body — Seta Capital's expertise should speak for itself.\n"
            post_directives = (
                "- BODY: provide substantive analysis with specific data points. Do NOT mention "
                "Seta Capital in the body — let the analysis itself demonstrate the expertise.\n"
                "- Reference actual market trends, deals, or economic indicators.\n"
                + url_directive
                + "- Write in a knowledgeable, authoritative thought-leader voice.\n"
                + "- The Seta Capital connection belongs ONLY in the final paragraph (cta field)."
            )
        else:  # holiday
            image_hint = f"authentic celebrations of {holiday_name}"
            post_directives = (
                f"- Headline must include '{holiday_name}' with a warm greeting.\n"
                f"- BODY: acknowledge partners and connections in {holiday_locale}; keep it warm and "
                "celebratory. Do NOT mention Seta Capital in the body.\n"
                "- Avoid overt business messaging.\n"
                "- Sign off as Seta Capital ONLY in the final paragraph (cta field)."
            )

        # News-enhanced requirements
        if news_context and pillar.use_news_search:
            news_requirements = (
                "\n\nCRITICAL NEWS REQUIREMENTS:\n"
                f"{news_context}\n"
                "\n"
                "You MUST:\n"
                "1. Reference at least ONE specific news item from above in your post\n"
                "2. Include the FULL URL (e.g., https://www.bloomberg.com/...) in the post body\n"
                "3. Provide expert commentary on the news implications (in the body, WITHOUT naming Seta Capital — the firm is introduced only in the final paragraph)\n"
                "4. Make the post feel timely and connected to current events\n"
                "5. Format the link naturally in the text (e.g., 'Read more: [URL]' or 'Source: [URL]')\n"
            )
        else:
            news_requirements = ""

        # Chart data requirements — Market Intelligence pillar
        if chart_data:
            chart_requirements = (
                "\n\nLIVE MARKET DATA — YOU MUST USE THESE EXACT FIGURES:\n"
                f"{chart_data}\n"
                "\n"
                "MANDATORY RULES for Market Intelligence posts:\n"
                "1. Quote at least TWO specific numbers from the data above (e.g. EUR/CNY rate, GDP %, yield)\n"
                "2. Explain what the movement means for cross-border M&A deal valuations or timing\n"
                "3. Keep the BODY brand-free — do NOT mention Seta Capital in the analysis; the Seta connection goes in the final paragraph (cta) only\n"
                "4. Do NOT invent or estimate numbers — only use the figures provided above\n"
                "5. Keep the tone analytical and authoritative — this is for CFOs and PE partners\n"
            )
        else:
            chart_requirements = ""

        # Image requirements
        if post_type == "holiday":
            image_requirements = (
                f"- Image_prompt must capture {holiday_name} celebrations with professional aesthetic.\n"
                "  NO text, NO logos, NO branding visible in the image."
            )
        else:
            if news_context and pillar.use_news_search:
                image_requirements = (
                    "- Image_prompt MUST feature real human professionals relevant to the news topic.\n"
                    "  If about tech/EV deals: show engineers or executives examining EV components.\n"
                    "  If about manufacturing: show workers or managers on a modern factory floor.\n"
                    "  If about market data: show a financial analyst reviewing charts at a desk.\n"
                    "  If about deals/M&A: show professionals shaking hands in a meeting room.\n"
                    "  NO empty buildings, NO city skylines without people.\n"
                    "  NO text, NO logos, NO branding visible in the image."
                )
            else:
                image_requirements = (
                    "- Image_prompt MUST feature real human professionals "
                    "(e.g. advisor shaking hands, executives in meeting, engineer on factory floor).\n"
                    "  NO empty buildings, NO city skylines without people, NO generic glass offices.\n"
                    "  NO text, NO logos, NO branding visible in the image."
                )

        from datetime import datetime
        current_date = datetime.now().strftime("%B %Y")
        current_year = datetime.now().year

        return (
            "You are the LinkedIn marketing voice for Seta Capital, a boutique M&A advisory firm "
            "specializing in cross-border transactions between Europe and China.\n"
            f"Strategy focus: {self.strategy_text}\n\n"
            f"IMPORTANT DATE CONTEXT: Today is {current_date}. The current year is {current_year}.\n"
            f"- Only reference news, data, and reports from the last 6 months ({current_year} or late {current_year - 1})\n"
            f"- If you must reference older data, you MUST add context like: 'While this {current_year - 2} data shows X, current trends suggest...'\n"
            f"- NEVER present old data as current without acknowledging the date\n"
            f"- Prefer news from {current_year}\n\n"
            f"Content pillar: {pillar.name}\n"
            f"Primary target audience: {pillar.target_client}\n"
            f"Angle to emphasise: {pillar.angle}\n"
            f"Key points to incorporate:\n{proof_points}\n\n"
            f"Tone guidance: {self.campaign.tone}.\n"
            f"Apply these directives:\n{post_directives}\n"
            f"{news_requirements}"
            f"{chart_requirements}"
            "\nOutput must be JSON with keys headline, body, cta, hashtags (list), image_prompt, video_prompt, alt_text.\n"
            "POST STRUCTURE (mandatory, applies to every pillar):\n"
            "- The post has TWO parts: (1) the BODY and (2) a single final paragraph in the 'cta' field.\n"
            "- BODY = authoritative, expert analysis in a thought-leadership voice. It must NOT mention "
            "Seta Capital, 'our firm', 'we', or any first-person brand reference. Pure insight only.\n"
            "- cta = EXACTLY ONE short paragraph (2-3 sentences) — the ONLY place Seta Capital is named. "
            "It ties the insight to Seta Capital's Europe-China M&A advisory and invites engagement.\n"
            "Constraints:\n"
            "- Keep total length 150-250 words across headline + body + cta.\n"
            "- Open with an attention-grabbing hook that feels timely and relevant.\n"
            "- Use professional, analytical language appropriate for C-suite readers.\n"
            "- Seta Capital must appear ONLY in the cta paragraph, nowhere in headline or body.\n"
            "- NEVER include political commentary or negative remarks about any country.\n"
            "- Finish with 3-5 hashtags from this pool: "
            f"{hashtag_pool}.\n"
            f"{image_requirements}\n"
            "- Provide alt_text suitable for LinkedIn accessibility, 15-25 words.\n"
            "Return JSON only, no extra text."
        )



__all__ = ["SetaLinkedInPostGenerator", "GeneratedPost"]
