"""LLM-powered LinkedIn post generator."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Sequence, TYPE_CHECKING

from .campaign_config import CampaignConfig, PostPillar
import random

from .manual_knowledge import build_lubrication_installation_context, build_case_study_context
from .base_content import GeneratedPost, BaseContentGenerator
from .media_prompts import compose_media_prompt
from .news_search import NewsArticle, build_news_context, recent_post_history, search_news_for_pillar
from .post_quality import (
    PLAIN_ENGLISH_DIRECTIVE,
    TNT_VOICE,
    apply_fixes,
    blocking_issues,
    cosmetic_issues,
    post_issues,
)

from .seta_content_generation import QualityBlocked  # noqa: E402  (shared exception)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from linkedin_generation.holiday.calendars import HolidayEvent


TNT_LOGO_URL = "https://tntbearings.com/wp-content/uploads/2025/09/TNT-M%E6%9C%89%E5%8F%98%E5%8C%96-edited-300x169.jpg"


class LinkedInPostGenerator(BaseContentGenerator):

    brand_key = "tnt"
    """Delegate that orchestrates prompt building and parsing.

    Shared GeneratedPost / __init__ / _parse_response / _merge_hashtags live in
    social.base_content.BaseContentGenerator.
    """


    def _recent_history(self) -> dict:
        """What this brand has already published, for de-duplication.

        Each brand reads its OWN output directory: a tenant must not inherit
        Seta's archive, or its first post would be judged a repeat of ours.
        """
        try:
            from . import BRANDS
            brand = BRANDS.get(getattr(self, "brand_key", "") or "")
            out = getattr(brand, "output_dir", None) if brand else None
        except Exception:
            out = None
        base = out or "linkedin_generation/linkedin_posts"
        if not str(base).startswith("/"):
            base = "/opt/linkedin/" + str(base).lstrip("/")
        return recent_post_history(base, days=45)

    def generate(
        self,
        *,
        pillar: PostPillar,
        scheduled_for: datetime,
        post_type: str,
        image_mode: str,
        holiday: "HolidayEvent" | None = None,
    ) -> GeneratedPost:
        # TNT was the last brand writing from nothing (2026-09-13). Every post was
        # invented from the pillar angle, which is why they read interchangeably.
        # It was left out because industrial parts were assumed to have no press;
        # measuring it disproved that - "bearing manufacturer industry" returns 18
        # fresh articles and Modern Machine Shop covers collets - so long as the
        # phrases aim at the industry rather than the part. See rules_seta.md.
        news_articles: List[NewsArticle] = []
        news_context = ""
        if pillar.use_news_search and post_type != "holiday":
            logger.info("Searching for news for TNT pillar: %s", pillar.name)
            _hist = self._recent_history()
            news_articles = search_news_for_pillar(
                pillar.name,
                num_articles=3,
                queries=list(getattr(pillar, "news_queries", []) or []),
                exclude_urls=_hist["urls"],
                # A subject covered in the last few posts is demoted at ranking
                # time - steered, never filtered, so a quiet week still produces
                # a post rather than nothing.
                recent_themes_used=recent_themes(_hist["posts"], limit=4),
                # TNT sells components, so an IPO or a results call gives it
                # nothing to say, and a story about a competitor must never be the
                # anchor. Both are excluded before the model ever sees them.
                avoid_finance=True,
                avoid_companies=TNT_VOICE.competitors,
            )
            news_context = build_news_context(news_articles)
            if not news_articles:
                logger.warning("No news found for TNT pillar '%s'", pillar.name)

        raw = self.llm_client.complete(
            self._build_prompt(
                pillar=pillar,
                post_type=post_type,
                image_mode=image_mode,
                holiday=holiday,
                news_context=news_context,
            ),
            temperature=0.8,
            max_tokens=650,
        )
        payload = self._strip_urls(self._parse_response(raw))

        # Same gate as Seta, with TNT's own policy: its direct CTA is wanted, so
        # only the conversation, formatting, vocabulary and sourcing rules bite.
        # Everything the post is allowed to cite. Without news_context here the
        # unsupported-statistics check would flag figures that came from the very
        # articles we handed the model.
        # What the post may draw on, and what actually PROVES anything. The
        # pillar's proof_points are angle material written in TNT's own voice -
        # they tell the model what to write about, they do not establish that any
        # of it happened. Passing them as evidence let a post repeat "cut
        # vibration by 70%" and be judged sourced by the marketing copy that
        # invented it.
        sources = "\n".join(filter(None, ["\n".join(pillar.proof_points or []), news_context]))
        # Catalogue specifications ARE evidence: ER runout grades, DIN69872,
        # Si3N4 ball density are checkable facts about what TNT sells. Narrated
        # engagements are not, whatever they claim. The pillar says which it has.
        verified_parts = [news_context or ""]
        if getattr(pillar, "proof_points_are_specs", False):
            verified_parts.append("\n".join(pillar.proof_points or []))
        verified = "\n".join(x for x in verified_parts if x)
        issues = post_issues(payload, TNT_VOICE, sources=sources, post_type=post_type,
                    verified=verified)
        if issues:
            logger.warning(
                "TNT post failed the quality gate (%s) - regenerating once",
                "; ".join(issues),
            )
            retry_raw = self.llm_client.complete(
                self._build_prompt(
                    pillar=pillar,
                    post_type=post_type,
                    image_mode=image_mode,
                    holiday=holiday,
                    news_context=news_context,
                    quality_feedback=issues,
                ),
                temperature=0.8,
                max_tokens=650,
            )
            retry_payload = self._strip_urls(self._parse_response(retry_raw))
            if len(post_issues(retry_payload, TNT_VOICE, sources=sources,
                           post_type=post_type, verified=verified)) < len(issues):
                payload = retry_payload

        payload = apply_fixes(payload, TNT_VOICE)
        remaining = post_issues(payload, TNT_VOICE, sources=sources, post_type=post_type,
                    verified=verified)

        # INVENTED ENGINEERING FIGURES GET THEIR OWN RETRY (2026-09-13).
        # The prompt has told the model since day one that every number must come
        # from the supplied material. It ignores that anyway: a dry run produced
        # "0.04 mm misalignment can cut bearing operating life by 40%" and
        # "improving alignment to 0.01 mm extends life by 70%" - plausible,
        # checkable, and completely invented. TNT's readers are maintenance
        # engineers who WILL check, so a fabricated tolerance costs more
        # credibility than a vaguer sentence. The general rule stays "report, do
        # not strip" (deleting every sentence with a number would gut the post);
        # this is one focused re-ask naming the exact figures.
        stat_issues = [i for i in remaining if i.startswith("statistics with no source")]
        if stat_issues:
            logger.warning("TNT post carries invented figures - re-asking: %s", stat_issues[0])
            fix_raw = self.llm_client.complete(
                self._build_prompt(
                    pillar=pillar, post_type=post_type, image_mode=image_mode,
                    holiday=holiday, news_context=news_context,
                    quality_feedback=stat_issues + [
                        "Every one of those figures is invented. Rewrite the post keeping the "
                        "same argument but WITHOUT them: say 'shortens bearing life' rather "
                        "than inventing a percentage, 'tighter alignment' rather than "
                        "inventing a tolerance. A qualitative claim an engineer cannot "
                        "falsify is worth more than a precise one they can."
                    ],
                ),
                temperature=0.6,
                max_tokens=650,
            )
            fix_payload = apply_fixes(self._strip_urls(self._parse_response(fix_raw)), TNT_VOICE)
            fix_remaining = post_issues(fix_payload, TNT_VOICE, sources=sources,
                             post_type=post_type, verified=verified)
            if not [i for i in fix_remaining if i.startswith("statistics with no source")]:
                payload, remaining = fix_payload, fix_remaining
                logger.info("TNT post: invented figures removed on the focused retry")
            else:
                logger.error(
                    "INVENTED_FIGURES: TNT post still carries unsourced numbers after a "
                    "focused retry - %s", fix_remaining[0] if fix_remaining else "",
                )

        # Same rule as Seta, and this is the generator every Bolla tenant on the
        # "tnt" template runs, so tenants inherit the refusal without configuring
        # anything. A tenant should not have to know that a model invents figures
        # in order to be protected from publishing them.
        # A story already told is not a new post. The URL exclusion above stops
        # the common case (the same article still ranking top a week later);
        # this catches the same story reached through a different article.
        # Blocking, not cosmetic: republishing last week's post under a new
        # headline is exactly the "they always say the same things" complaint.
        try:
            _prior = self._recent_history()["posts"]
            _repeat = repeats_recent_story(
                str(payload.get("headline", "")), str(payload.get("body", "")),
                [q for q in _prior
                 if q.get("headline") != str(payload.get("headline", ""))],
            )
        except Exception:      # history is an optimisation, never a hard dependency
            _repeat = None
        if _repeat:
            remaining = list(remaining) + [
                f"retells a post from the last 45 days - \"{_repeat}\" - "
                f"find a different story"
            ]

        blocking = blocking_issues(remaining)
        if blocking:
            logger.error(
                "QUALITY_BLOCKED: refusing to publish - %s", "; ".join(blocking)
            )
            raise QualityBlocked("; ".join(blocking))
        cosmetic = cosmetic_issues(remaining)
        if cosmetic:
            logger.warning(
                "TNT post published with unresolved STYLE issues: %s",
                "; ".join(cosmetic),
            )

        hashtags = payload.get("hashtags") or []
        if isinstance(hashtags, str):
            hashtags = [tag.strip() for tag in hashtags.split() if tag.strip()]

        all_hashtags = self._merge_hashtags(list(hashtags), pillar)
        # Media has to show what the post is actually about (2026-09-13). TNT
        # took the model's prompt raw, with the pillar's fixed prompt as fallback
        # and no guardrail either way - so nothing stopped a stock skyline, and
        # nothing tied the picture to the post. Same composer Seta uses: the model
        # supplies the subject, the pillar supplies the house look, and the
        # mandate (real people at work, no text, no logos, no skyline) is appended
        # in code where no model output can drop it.
        image_prompt = compose_media_prompt(
            subject=payload.get("image_prompt"),
            house_prompt=pillar.image_prompt or pillar.angle,
            kind="image",
        )
        video_prompt = compose_media_prompt(
            subject=payload.get("video_prompt"),
            fallback_subject=payload.get("image_prompt"),
            house_prompt=pillar.video_prompt
                         or f"Slow-motion footage showing {pillar.angle.lower()} in operation",
            kind="video",
        )
        alt_text = payload.get("alt_text") or f"Industrial bearings solution for {pillar.target_client}"

        metadata: Dict[str, str] = {
            "post_type": post_type,
            "image_mode": image_mode,
        }
        if holiday:
            metadata["holiday_name"] = holiday.name
            metadata["holiday_locale"] = holiday.locale

        return GeneratedPost(
            pillar_name=pillar.name,
            target_client=pillar.target_client,
            headline=payload.get("headline", "TNT Motion Bearing Solutions"),
            body=payload.get("body", ""),
            cta=payload.get("cta", "Talk to TNT Motion's engineering team for a tailored proposal."),
            hashtags=all_hashtags,
            news_articles=news_articles,
            image_prompt=image_prompt,
            video_prompt=video_prompt,
            alt_text=alt_text,
            created_at=scheduled_for,
            metadata=metadata,
        )

    @staticmethod
    def _strip_urls(payload: Dict[str, Any]) -> Dict[str, Any]:
        """No URL ever reaches the body — LinkedIn demotes posts carrying one."""
        import re as _re
        url_pattern = _re.compile(r'\[?(https?://[^\s\]\)]+)\]?(?:\([^\)]+\))?')
        for name in ("body", "headline", "cta"):
            if name in payload and isinstance(payload[name], str):
                payload[name] = _re.sub(r'\s{2,}', ' ', url_pattern.sub('', payload[name])).strip()
        return payload

    def _build_prompt(
        self,
        *,
        pillar: PostPillar,
        post_type: str,
        image_mode: str,
        holiday: "HolidayEvent" | None = None,
        news_context: str = "",
            quality_feedback: Optional[list] = None,
    ) -> str:
        # Randomly select a subset of proof points to ensure variety across posts
        available_points = list(pillar.proof_points) if pillar.proof_points else []
        if len(available_points) > 3:
            selected_points = random.sample(available_points, 3)
        else:
            selected_points = available_points
        from .brand_store import extra_context

        brand_material = extra_context(self.brand_key)
        proof_points = "\n".join(f"- {item}" for item in selected_points) or "- Reliable delivery timelines\n- Application engineering support"
        ctas = ", ".join(pillar.ctas or ["Book a technical call", "Request a tailored quotation", "Send us your bearing list for cross-reference"])
        hashtag_pool = " ".join(self._merge_hashtags([], pillar))
        image_hint = pillar.image_prompt or "Industrial bearing systems in action"

        holiday_name = holiday.name if holiday else "the holiday"
        holiday_locale = holiday.locale if holiday else "our markets"

        if post_type == "promotional":
            post_directives = (
                "- Highlight measurable customer outcomes or business value (uptime, cost, reliability).\n"
                "- Mention TNT Motion's human support (engineers, service team, response time).\n"
                "- Close with a conversational CTA inviting direct contact (call, WhatsApp, email)."
            )
        elif post_type == "technical":
            post_directives = (
                "- Explain key concepts or terminology in plain language before diving deeper.\n"
                "- Provide a step-by-step practice or troubleshooting routine with specific parameters.\n"
                "- Reference real maintenance/design procedures (torque checks, lubrication schedules, tolerances).\n"
                "- Ground each step in numerical detail (load ratings, clearances, lubrication intervals) using the manual excerpts below.\n"
                "- Finish with a forward-looking insight on how TNT Motion is evolving the practice."
            )
        else:  # holiday
            image_hint = f"authentic celebrations of {holiday_name} with cultural detail"
            post_directives = (
                f"- Headline must be exactly 'Happy {holiday_name}!' (with an exclamation mark).\n"
                f"- Mention {holiday_name} by name in the body and acknowledge partners in {holiday_locale}.\n"
                "- Keep copy warm and celebratory, with only one sentence referencing TNT Motion.\n"
                "- Include a light engineering or reliability nod without overt selling."
            )

        if post_type == "holiday":
            if image_mode == "video":
                image_requirements = (
                    f"- Image_prompt must capture {holiday_name} celebrations with authentic cultural symbols and human moments,"
                    " cinematic lighting, and only subtle nods to industrial settings."
                    "\n"
                    "  ABSOLUTE REQUIREMENTS: ZERO text, words, logos, brand names, or signage anywhere in the image."
                    " The TNT Motion logo will be added in post-processing - do NOT include it."
                )
                video_prompt_override = (
                    f"15-second cinematic montage of {holiday_name} festivities with ambient city or family scenes and light industrial touches"
                )
            else:
                image_requirements = (
                    f"- Image_prompt must depict real-world {holiday_name} celebrations (parades, landmarks, gatherings)"
                    " with genuine cultural details, rich colour, and candid photography."
                    "\n"
                    "  ABSOLUTE REQUIREMENTS: ZERO text, words, logos, brand names, or signage anywhere in the image."
                    " The TNT Motion logo will be added in post-processing - do NOT include it."
                )
                video_prompt_override = (
                    f"Slow-motion footage of {holiday_name} celebrations blending cultural scenes with subtle engineering references"
                )
        else:
            if image_mode == "video":
                image_requirements = (
                    "- Image_prompt must describe the key cinematic frame from a short LinkedIn-ready video,"
                    " highlighting industrial machinery, PPE, or instrumentation with dynamic lighting."
                    " Emphasise motion through framing (e.g. motion blur, dramatic angles) while keeping the frame photo-realistic."
                    "\n"
                    "  ABSOLUTE REQUIREMENTS FOR IMAGE GENERATION:\n"
                    "  1. ZERO TEXT: No text, words, letters, numbers, labels, or signage of ANY kind anywhere in the image.\n"
                    "  2. ZERO LOGOS: No logos, brand marks, company names, or emblems on machinery, uniforms, walls, equipment, or anywhere.\n"
                    "  3. ZERO BEARINGS: Do NOT show bearings, ball bearings, roller bearings, races, cages, or any precision mechanical components.\n"
                    "     AI cannot render these accurately - they always look fake and unrealistic.\n"
                    "  4. WHAT TO SHOW INSTEAD: Engineers in generic work uniforms (no text/logos), industrial environments like factories,\n"
                    "     warehouses, control rooms, conveyor systems, large machinery from WIDE angles, turbines, pumps, motors (exterior only).\n"
                    "  5. PEOPLE FOCUS: Show human workers, technicians, or engineers as the main subject - their faces, hands, actions.\n"
                    "  The TNT Motion logo will be added separately in post-processing - do NOT attempt to include it."
                )
                video_prompt_override = (
                    "10-15 second cinematic industrial video showing the same scene,"
                    " subtle camera movement, and clear depiction of the engineered solution in action."
                )
            else:
                image_requirements = (
                    "- Image_prompt must describe a high-resolution, photo-realistic scene captured on location,"
                    " featuring engineers or equipment in an industrial environment."
                    " Mention realistic photography cues (natural lighting, 35mm lens, shallow depth of field)."
                    "\n"
                    "  ABSOLUTE REQUIREMENTS FOR IMAGE GENERATION:\n"
                    "  1. ZERO TEXT: No text, words, letters, numbers, labels, or signage of ANY kind anywhere in the image.\n"
                    "  2. ZERO LOGOS: No logos, brand marks, company names, or emblems on machinery, uniforms, walls, equipment, or anywhere.\n"
                    "     Uniforms must be plain solid colors with no patches, embroidery, or printed text.\n"
                    "  3. ZERO BEARINGS: Do NOT show bearings, ball bearings, roller bearings, races, cages, or any precision mechanical components.\n"
                    "     AI cannot render these accurately - they always look cartoonish and unrealistic.\n"
                    "  4. WHAT TO SHOW INSTEAD: Focus on one of these scenes:\n"
                    "     - Engineer reviewing documents or tablet in a factory setting\n"
                    "     - Technician working on large industrial equipment (motors, pumps, conveyors) from medium/wide angle\n"
                    "     - Team meeting in an industrial control room or office\n"
                    "     - Warehouse with shipping containers and logistics operations\n"
                    "     - Maintenance technician inspecting machinery with flashlight or tools\n"
                    "  5. PEOPLE ARE THE FOCUS: Human workers should be the main subject, not mechanical parts.\n"
                    "  The TNT Motion logo will be added separately in post-processing - do NOT attempt to include it."
                )
                video_prompt_override = "Slow-motion footage capturing the same real-world industrial scene."

        manual_context = ""
        no_invented_numbers_constraint = ""
        if pillar.name == "Lubrication & Installation Mastery":
            context = build_lubrication_installation_context()
            if context:
                manual_context = f"Reference these TNT Motion manual insights (quote values when relevant):\n{context}\n"
        elif pillar.name == "Real Application Stories":
            context = build_case_study_context(limit=2)
            if context:
                manual_context = (
                    f"Use ONLY metrics from these real TNT Motion case studies (do not invent numbers):\n{context}\n"
                )
                no_invented_numbers_constraint = (
                    "- CRITICAL: Only use specific numbers, percentages, or timeframes that appear in the case studies above. "
                    "Do NOT invent metrics like '200,000 hours' or similar. If you need to mention results, "
                    "paraphrase the actual data provided. Vague qualitative improvements are better than fabricated statistics.\n"
                )
            else:
                no_invented_numbers_constraint = (
                    "- CRITICAL: Do NOT invent specific numbers, percentages, hours, or timeframes. "
                    "Use qualitative descriptions (e.g., 'significant improvement', 'extended service life', "
                    "'reduced downtime') rather than fabricated statistics like '200,000 hours'.\n"
                )

        return (
            "You are the LinkedIn marketing voice for TNT Motion, a European-engineered bearing brand "
            "supplying distributors and OEMs across Eastern Europe, the Middle East, South America, and Africa.\n"
            f"Strategy focus: {self.strategy_text}\n\n"
            f"Content pillar: {pillar.name}\n"
            f"Primary target client: {pillar.target_client}\n"
            f"Angle to emphasise: {pillar.angle}\n"
            f"Proof points to weave in:\n{proof_points}\n\n"
            f"Tone guidance: {self.campaign.tone}.\n"
            f"Apply these directives:\n{post_directives}\n"
            f"\n{PLAIN_ENGLISH_DIRECTIVE}\n"
            + (
                "\nREAL NEWS FETCHED TODAY — build the post on it:\n"
                f"{news_context}\n"
                "TNT-specific rules for using it:\n"
                "1. Open on the actual development — the company, the machine, the failure, "
                "the market move. Not a theme.\n"
                "2. Name the outlet and when it reported. Never 'recent reports' or "
                "'industry data'.\n"
                "3. Carry over one concrete figure from the reporting. Invent nothing: if a "
                "number is not in the material above, it does not go in the post.\n"
                "4. THEN connect it to what TNT actually supplies — the bearing, collet, "
                "toolholder or service that bears on what just happened, and why it matters "
                "to the reader's machine. The news earns the attention; the product answers "
                "it. A post that only summarises the news is a wasted post for TNT.\n"
                "5. Do NOT paste any URL — the link is published as the first comment.\n"
                "6. THE CONNECTION MUST BE REAL. The link between the story and the "
                "component must be CAUSAL and specific - the story changes something "
                "about how the reader's machine is specified, bought, maintained or "
                "runs. If the only link you can write is 'this shows the market values "
                "quality, and quality is what we sell', that is decoration, not a "
                "connection: pick a DIFFERENT article from the list, or write the post "
                "from the pillar alone and mention no news at all. A post that uses a "
                "story as an opening flourish and then ignores it is worse than one "
                "with no news in it.\n"
                "7. NEVER ADVERTISE A COMPETITOR. Much of this trade press is about "
                + ", ".join(TNT_VOICE.competitors[:12]) + " and others like them. "
                "If the story is about one of them, that is fine as a FACT — report what "
                "happened and what it means for the reader's machine — but never put their "
                "name in the headline, never repeat their product claims or marketing "
                "language, and never call them leading, premium, innovative or best. The "
                "post is published from TNT Motion's page; a post praising a competitor is "
                "an advert TNT paid for.\n"
                if news_context else ""
            )
            + "Output must be JSON with keys headline, body, cta, hashtags (list), image_prompt, video_prompt, alt_text.\n"
            + (
                f"\nCOMPANY-SPECIFIC MATERIAL (supplied by the account owner - treat as "
                f"authoritative for facts about this company):\n{brand_material}\n"
                if brand_material
                else ""
            )
            +             "Constraints:\n"
            "- Keep total post length strictly under 150 words across headline + body + CTA combined. Be punchy and concise — LinkedIn readers scroll fast.\n"
            "- Open with an attention-grabbing hook line (uppercase allowed).\n"
            "- WRITE CONCRETELY. Banned as padding: strategic, complex, dynamic, landscape, evolving, robust, leverage, nuanced, intricate, crucial, comprehensive, seamless, cutting-edge, unparalleled, paramount, holistic, value-add. Across this page's archive every post averaged 5 such words per 100 - which is why they all read the same. Name the country, the sector, the component, the situation. A sentence that would still be true for a different company in a different industry is padding: cut it.\n"
            "- Use European spelling (eg, optimise, organisation).\n"
            "- Mention TNT Motion explicitly once.\n"
            "- Reference the relevant industries or scenarios the pillar covers.\n"
            "- CTA must invite direct conversation (call, WhatsApp, email) or catalogue download, "
            "AND end with a genuine open question an engineer would answer from experience "
            "(across 107 posts this page drew 0 comments - a question is what the feed amplifies). "
            "Never the same question twice.\n"
            "- DESCRIBE THE MECHANISM, DO NOT INVENT THE CUSTOMER. These pillars are "
            "called stories, and a story is the right shape - but the part that is TRUE "
            "and useful is how the failure works, not who it happened to. Write the "
            "conditions, the progression and the consequence in general terms:\n"
            "    GOOD: 'In a washdown environment, moisture works past a worn lip seal, "
            "the race corrodes, and the bearing seizes without warning.'\n"
            "    GOOD: 'Above roughly 15,000 rpm a steel ball's own mass becomes the "
            "limiting factor, which is what hybrid ceramics are for.'\n"
            "    NOT ALLOWED: a named colleague ('our engineer David'), an invented "
            "customer ('a steel mill in Alexandria', 'an Eastern European food plant', "
            "'South American water treatment plants'), a specific engagement ('we were "
            "called at dawn', 'we replaced the unit, they were back online in 14 hours') "
            "or a cost attributed to one ('it cost them EUR 18,000').\n"
            "  Those read as customer references for work that was never done. If you "
            "have a real case in the material above, use it exactly as given; otherwise "
            "the mechanism alone is a better post and claims nothing.\n"
            "- NUMBERS: two kinds are allowed and one is not.\n"
            "  (a) A figure from the proof points or the fetched news above - use it as given.\n"
            "  (b) A figure you WORK OUT, where you show the working in the post so a reader "
            "can check it: 'a 20 mm bore at 3000 rpm gives roughly 3 m/s surface speed', "
            "'replacing a EUR 40 bearing three times a year is EUR 120 plus three shutdowns'. "
            "State the inputs and the step. An engineer can then agree or disagree with your "
            "assumption, which is a conversation worth having.\n"
            "  (c) NOT ALLOWED: a precise-sounding figure with no source and no working - "
            "'cuts bearing life by 40%', 'improves uptime 3x', 'saves EUR 200,000 a year'. "
            "Those read as data and are not. If you cannot source it or work it out, make "
            "the point qualitatively: 'shortens bearing life', 'costs more in downtime than "
            "the part ever saved'.\n"
            "  Never write 'studies show' or 'industry data indicates'. Buyers here will check.\n"
            "- Do not reference or link to any external files, websites, or resources unless they appear directly in the post copy.\n"
            "- Finish with 3-5 hashtags chosen from this pool and/or relevant variants: "
            f"{hashtag_pool}.\n"
            f"- Propose an image_prompt describing {image_hint} with cinematic industrial detail.\n"
            "- image_prompt and video_prompt must show the SUBJECT OF THIS POST - the actual "
            "component, machine or failure you wrote about, and the people working on it - not "
            "generic industrial imagery. Both are required and must describe the same scene: "
            "image_prompt as a still, video_prompt as that scene in motion.\n"
            f"{image_requirements}\n"
            f"- Also provide video_prompt describing {video_prompt_override}\n"
            "- Provide alt_text suitable for LinkedIn accessibility, 15-25 words.\n"
            f"{no_invented_numbers_constraint}"
            f"{manual_context}"
            "- VARIATION: Pick ONE proof point from the list above as your starting inspiration. Do NOT reuse the exact same scenario, numbers, or industry from previous posts. Vary the industry (automotive, mining, food processing, energy, HVAC, paper, cement, marine, etc.), the failure mode, and all specific figures each time. Invent plausible but DIFFERENT numbers for each post (vary cost figures, temperatures, timeframes, percentages).\n"
            + (
                "\nYour previous attempt was REJECTED for these reasons - fix every one:\n"
                + "\n".join(f"- {issue}" for issue in quality_feedback)
                + "\n"
                if quality_feedback
                else ""
            )
            + "Return JSON only, no extra text."
        )

__all__ = ["LinkedInPostGenerator", "GeneratedPost"]
