"""Composing image and video prompts that match the post that was written.

THE PROBLEM THIS SOLVES (2026-09-13). The Seta scheduler resolved a video prompt
as `pillar.video_prompt or post.video_prompt`, and the pillar's prompt is a fixed
string in the campaign YAML. So every M&A Insights post got the same boardroom
handshake no matter what the post said. The live post that day analysed Chinese
mining losses abroad and country-level environmental risk in due diligence, and
was illustrated with two people signing a term sheet in Frankfurt. Tom:
"the video has nothing or very little to do with the article."

WHY IT WAS BUILT THAT WAY, AND WHY THAT REASON STILL MATTERS. The YAML prompt was
made authoritative because the model kept proposing empty city skylines and
glass-tower stock imagery, which is exactly what Seta's image mandate forbids.
That was a real failure; freeing the model completely would bring it straight
back.

SO: the model supplies the SUBJECT (who is on screen, where, doing what, drawn
from the actual story), the YAML supplies the HOUSE LOOK, and the non-negotiable
rules are appended here in code and cannot be dropped or argued away by a model.
A subject that trips the ban list is discarded and the YAML prompt is used
unchanged - the old behaviour, but only in the case it was written for.
"""

from __future__ import annotations

import re
from typing import Optional

# Never acceptable in Seta media: the image mandate is people at work, because a
# skyline says nothing about a deal and every firm on LinkedIn posts one.
BANNED_MEDIA_TERMS = [
    "skyline", "cityscape", "city view", "aerial view", "drone shot",
    "empty office", "empty building", "glass tower", "skyscraper",
    "abstract", "geometric", "logo", "text overlay", "infographic",
    "chart on screen", "stock photo", "generic business",
]

HOUSE_RULES_IMAGE = (
    "Real human professionals clearly visible and doing the work described. "
    "Photorealistic, warm cinematic light, documentary grade. "
    "No text, no logos, no writing of any kind visible. "
    "No city skyline, no empty building, no abstract graphics."
)

HOUSE_RULES_VIDEO = (
    "16:9 cinematic footage. Real human professionals on camera doing the work "
    "described. Documentary grade, warm professional lighting, natural motion. "
    "No text, no captions, no logos, no on-screen writing. "
    "No city skyline, no empty building, no abstract graphics, no drinks."
)


def is_safe_subject(subject: str) -> bool:
    """False if the model reached for the stock imagery the mandate bans."""
    if not subject or len(subject.strip()) < 25:
        return False
    lowered = subject.lower()
    return not any(term in lowered for term in BANNED_MEDIA_TERMS)


def strip_house_boilerplate(subject: str) -> str:
    """Drop any rules the model restated, so they are not duplicated below."""
    cleaned = re.sub(
        r"(?i)\b(no text[^.]*\.|no logos?[^.]*\.|16:9[^.]*\.|photorealistic[^.]*\.)",
        "",
        subject,
    )
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def compose_media_prompt(
    *,
    subject: Optional[str],
    house_prompt: Optional[str],
    kind: str = "image",
    fallback_subject: Optional[str] = None,
) -> str:
    """Subject from the post + house look from YAML + rules that cannot be dropped.

    `subject` is what the model proposed for THIS post; `house_prompt` is the
    pillar's YAML prompt, used as the look-and-feel reference and as the whole
    prompt when the subject is missing or unsafe.
    """
    rules = HOUSE_RULES_VIDEO if kind == "video" else HOUSE_RULES_IMAGE
    house = (house_prompt or "").strip()

    chosen = subject if is_safe_subject(subject or "") else None
    if chosen is None and is_safe_subject(fallback_subject or ""):
        # The model's video_prompt was unusable but its image_prompt describes the
        # same post. Reuse that rather than dropping to the YAML - found on a dry
        # run 2026-09-14, where M&A Insights got the fixed boardroom clip while its
        # IMAGE was a story-specific logistics warehouse. Falling all the way back
        # when a good subject was sitting right there is the worst of both.
        chosen = fallback_subject
    if chosen is None:
        # Nothing usable: the vetted YAML prompt, with the rules appended in case
        # the YAML predates them.
        base = house or "Professionals at work in an industrial or advisory setting."
        return f"{base} {rules}".strip()

    cleaned = strip_house_boilerplate(chosen)
    return f"{cleaned} {rules}".strip()


__all__ = [
    "BANNED_MEDIA_TERMS",
    "HOUSE_RULES_IMAGE",
    "HOUSE_RULES_VIDEO",
    "is_safe_subject",
    "strip_house_boilerplate",
    "compose_media_prompt",
]
