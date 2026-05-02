"""LLM-powered LinkedIn post generator."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Sequence, TYPE_CHECKING

from .campaign_config import CampaignConfig, PostPillar
import random

from .manual_knowledge import build_lubrication_installation_context, build_case_study_context

if TYPE_CHECKING:  # pragma: no cover - typing only
    from linkedin_generation.holiday.calendars import HolidayEvent


TNT_LOGO_URL = "https://tntbearings.com/wp-content/uploads/2025/09/TNT-M%E6%9C%89%E5%8F%98%E5%8C%96-edited-300x169.jpg"


@dataclass
class GeneratedPost:
    """Structured representation of a LinkedIn post."""

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
        return payload


class LinkedInPostGenerator:
    """Delegate that orchestrates prompt building and parsing."""

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

    def generate(
        self,
        *,
        pillar: PostPillar,
        scheduled_for: datetime,
        post_type: str,
        image_mode: str,
        holiday: "HolidayEvent" | None = None,
    ) -> GeneratedPost:
        raw = self.llm_client.complete(
            self._build_prompt(
                pillar=pillar,
                post_type=post_type,
                image_mode=image_mode,
                holiday=holiday,
            ),
            temperature=0.8,
            max_tokens=650,
        )
        payload = self._parse_response(raw)
        hashtags = payload.get("hashtags") or []
        if isinstance(hashtags, str):
            hashtags = [tag.strip() for tag in hashtags.split() if tag.strip()]

        all_hashtags = self._merge_hashtags(list(hashtags), pillar)
        image_prompt = payload.get("image_prompt") or pillar.image_prompt or pillar.angle
        video_prompt = payload.get("video_prompt") or f"Slow-motion footage showing {pillar.angle.lower()} in operation"
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
            image_prompt=image_prompt,
            video_prompt=video_prompt,
            alt_text=alt_text,
            created_at=scheduled_for,
            metadata=metadata,
        )

    def _build_prompt(
        self,
        *,
        pillar: PostPillar,
        post_type: str,
        image_mode: str,
        holiday: "HolidayEvent" | None = None,
    ) -> str:
        # Randomly select a subset of proof points to ensure variety across posts
        available_points = list(pillar.proof_points) if pillar.proof_points else []
        if len(available_points) > 3:
            selected_points = random.sample(available_points, 3)
        else:
            selected_points = available_points
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
            "Output must be JSON with keys headline, body, cta, hashtags (list), image_prompt, video_prompt, alt_text.\n"
            "Constraints:\n"
            "- Keep total post length strictly under 150 words across headline + body + CTA combined. Be punchy and concise — LinkedIn readers scroll fast.\n"
            "- Open with an attention-grabbing hook line (uppercase allowed).\n"
            "- Use European spelling (eg, optimise, organisation).\n"
            "- Mention TNT Motion explicitly once.\n"
            "- Reference the relevant industries or scenarios the pillar covers.\n"
            "- CTA must invite direct conversation (call, WhatsApp, email) or catalogue download.\n"
            "- Do not reference or link to any external files, websites, or resources unless they appear directly in the post copy.\n"
            "- Finish with 3-5 hashtags chosen from this pool and/or relevant variants: "
            f"{hashtag_pool}.\n"
            f"- Propose an image_prompt describing {image_hint} with cinematic industrial detail.\n"
            f"{image_requirements}\n"
            f"- Also provide video_prompt describing {video_prompt_override}\n"
            "- Provide alt_text suitable for LinkedIn accessibility, 15-25 words.\n"
            f"{no_invented_numbers_constraint}"
            f"{manual_context}"
            "- VARIATION: Pick ONE proof point from the list above as your starting inspiration. Do NOT reuse the exact same scenario, numbers, or industry from previous posts. Vary the industry (automotive, mining, food processing, energy, HVAC, paper, cement, marine, etc.), the failure mode, and all specific figures each time. Invent plausible but DIFFERENT numbers for each post (vary cost figures, temperatures, timeframes, percentages).\n"
            "Return JSON only, no extra text."
        )

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


__all__ = ["LinkedInPostGenerator", "GeneratedPost"]
