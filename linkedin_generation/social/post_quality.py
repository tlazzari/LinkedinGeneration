"""Deterministic quality gate for LinkedIn posts, shared by every brand.

The prompt asks for insight rather than marketing copy, but a prompt is a
request, not a guarantee. The properties a post must have to be worth resharing
from a personal profile are enforced here, in code, so model drift cannot
quietly reintroduce a sales pitch:

  * no promotional register ("Seta Capital specializes in...", "Connect with us")
  * Seta named at most once, never in the headline or body
  * the post closes on a genuine open question - under the old closed CTA
    ("Connect with us to discuss your strategic objectives") 67 posts drew
    exactly 1 comment and 0 reposts between them
  * the body is short paragraphs, not one ~250-word block behind "see more"
  * the headline avoids the house cliches - "cross-border" appeared in 61 of 67
    posts, "strategic" in 62, "navigating" in 40

`post_issues()` is pure: it takes the parsed payload and returns human-readable
problems. The same list is fed back to the model as retry instructions and
asserted by the test suite, so the contract has exactly one definition.

Everything brand-specific - the company name and the worn-out vocabulary that
brand has overused - is passed in by the caller, so a new brand needs a
`BrandVoice` entry and nothing else. The evidence for each brand's tired
vocabulary is its own post archive, measured 2026-08-31:
Seta had "cross-border" in 61 of 67 headlines; TNT had "myth" in 19 of 107 and
"European quality" in 16. Note TNT's all-caps headlines are NOT penalised - its
two best-reaching posts ever were "20C OVERHEAT: The Silent Killer" (847
impressions) and "THE EUR200,000 NIGHTMARE" (648), so shouting works there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

# Sales register. Checked across the whole post; the closing paragraph may name
# Seta once, but never sell.
PROMOTIONAL_PATTERNS = [
    r"\bspecial(?:is|iz)es? in\b",
    r"\bconnect with us\b",
    r"\bcontact us\b",
    r"\breach out\b",
    r"\bget in touch\b",
    r"\bour (?:firm|team|advisory|expertise|clients)\b",
    r"\bwe (?:advise|specialis\w*|specializ\w*|help|partner|offer|provide)\b",
    r"\blet us\b",
    r"\btrusted (?:partner|advisor)\b",
    r"\bdiscuss your\b",
    r"\byour strategic objectives\b",
    r"\bexplore opportunities\b",
    r"\brequest a (?:strategic )?briefing\b",
]

@dataclass(frozen=True)
class BrandVoice:
    """What the gate needs to know about one brand's copy."""

    name: str                       # as written in posts, e.g. "Seta Capital"
    overused_headline_terms: Sequence[str]
    # Seta posts exist to be reshared from a personal profile, so a sales
    # register disqualifies them. TNT posts are a sales channel whose prompt
    # deliberately asks for a direct CTA (call, WhatsApp, email, catalogue), so
    # the promotional check is off there by design - do not "fix" that.
    ban_promotional: bool = True
    # Seta keeps the body brand-free so the analysis stands on its own; TNT's
    # prompt asks for the brand by name in the copy, so placement is free there.
    brand_in_closing_only: bool = True
    # Both brands publish into a feed that amplifies conversation: 0 comments
    # across TNT's 107 posts and 1 across Seta's 67 (measured 2026-08-31).
    require_closing_question: bool = True


# Worn-out headline vocabulary, measured over each brand's own archive.
SETA_VOICE = BrandVoice(
    name="Seta Capital",
    ban_promotional=True,
    overused_headline_terms=(
        "cross-border",
        "navigating",
        "unlocking",
        "precision",
        "reshaping",
        "strategic value",
    ),
)

TNT_VOICE = BrandVoice(
    name="TNT Motion",
    ban_promotional=False,
    brand_in_closing_only=False,
    overused_headline_terms=(
        "myth",
        "european quality",
        "costing millions",
        "reliability reimagined",
        "silent killer",
    ),
)

VOICES = {"seta": SETA_VOICE, "tnt": TNT_VOICE}

# Vague attributions that imply a source the pipeline never fetched.
VAGUE_SOURCE_PATTERNS = [
    r"\brecent (?:reports?|data|studies|analysis)\b",
    r"\b(?:leading |industry |market )?reports? (?:indicate|show|suggest|confirm)\b",
    r"\bdata (?:from|indicates?|shows?|suggests?)\b",
    r"\bstudies show\b",
    r"\banalysts? (?:estimate|expect|predict)\b",
    r"\baccording to (?:recent|leading|industry|market)\b",
    r"\bsurveys? (?:indicate|show)\b",
]

# A statistic worth checking: a percentage, or a number carried to two or more
# decimals (an FX rate). Bare integers and years are left alone - "H1 2026" and
# "10-Year Treasury" are labels, not claims.
# A statistic worth checking: a percentage, a number carried to two or more
# decimals (an FX rate), or a currency amount - TNT's archive is full of
# invented failure costs ("THE EUR200,000 NIGHTMARE").
_STATISTIC = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*%"
    r"|[€$£]\s?\d[\d,]*(?:\.\d+)?"
    r"|\d[\d,]*(?:\.\d+)?\s*(?:euros?|EUR|USD|dollars?|RMB|CNY|GBP|pounds?)\b"
    r"|\d+\.\d{2,}",
    re.IGNORECASE,
)
_ANY_NUMBER = re.compile(r"\d+(?:\.\d+)?")

MAX_PARAGRAPH_SENTENCES = 3
MAX_BODY_WORDS_PER_PARAGRAPH = 60

# Split on a sentence end followed by a capital, without breaking decimals
# ("7.8422") or initialisms ("U.S. Treasury").
_SENTENCE_SPLIT = re.compile(r"(?<![A-Z])(?<=[.!?])\s+(?=[A-Z])")


def promotional_hits(text: str) -> List[str]:
    """Promotional phrases present in text, as the matched substrings."""
    hits: List[str] = []
    for pattern in PROMOTIONAL_PATTERNS:
        found = re.search(pattern, text, flags=re.IGNORECASE)
        if found:
            hits.append(found.group(0))
    return hits


def statistic_values(text: str) -> List[str]:
    """Percentages and precise decimals asserted in text, as written."""
    return [m.group(0).strip() for m in _STATISTIC.finditer(text)]


def unsupported_statistics(text: str, sources: str) -> List[str]:
    """Statistics in text that do not trace back to the fetched data.

    A claim counts as supported when some number in the sources equals it at the
    claim's own precision, so quoting 7.84 from a fetched 7.8422 is fine while
    inventing "a 12% increase" is not.
    """
    available = []
    for match in _ANY_NUMBER.finditer(sources or ""):
        try:
            available.append(float(match.group(0)))
        except ValueError:
            continue
    unsupported: List[str] = []
    for claim in statistic_values(text):
        raw = re.sub(r"[^\d.]", "", claim)
        try:
            value = float(raw)
        except ValueError:
            continue
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if not any(round(source, decimals) == value for source in available):
            unsupported.append(claim)
    return unsupported


def vague_source_hits(text: str) -> List[str]:
    """Phrases implying a source the pipeline cannot have fetched."""
    hits: List[str] = []
    for pattern in VAGUE_SOURCE_PATTERNS:
        found = re.search(pattern, text, flags=re.IGNORECASE)
        if found:
            hits.append(found.group(0))
    return hits


def post_issues(
    payload: Dict[str, object],
    voice: BrandVoice,
    sources: Optional[str] = None,
) -> List[str]:
    """Every reason this post is not repost-worthy. Empty list == publishable.

    `sources` is the data actually fetched for this post (chart figures, news
    context, curated proof points). Pass it to check that every statistic the
    post asserts traces back to real data; omit it to skip that check.
    """
    issues: List[str] = []
    headline = str(payload.get("headline", "") or "")
    body = str(payload.get("body", "") or "")
    cta = str(payload.get("cta", "") or "")
    whole = f"{headline} {body} {cta}"

    if voice.ban_promotional:
        hits = promotional_hits(whole)
        if hits:
            issues.append("promotional phrasing - remove " + ", ".join(f"'{h}'" for h in hits))

    brand = re.escape(voice.name)
    brand_count = len(re.findall(brand, whole, flags=re.IGNORECASE))
    if brand_count > 1:
        issues.append(f"{voice.name} named {brand_count} times - name it at most once")
    if voice.brand_in_closing_only and re.search(
        brand, f"{headline} {body}", flags=re.IGNORECASE
    ):
        issues.append(f"{voice.name} appears in the headline or body - closing paragraph only")

    if voice.require_closing_question and "?" not in cta:
        issues.append("closing paragraph asks no question - end on a genuine open question")

    for term in voice.overused_headline_terms:
        if term in headline.lower():
            issues.append(f"headline reuses the house cliche '{term}'")

    paragraphs = [p for p in body.split("\n\n") if p.strip()]
    if len(paragraphs) < 2:
        issues.append("body is a single block - break it into 3-4 short paragraphs")
    for paragraph in paragraphs:
        if len(paragraph.split()) > MAX_BODY_WORDS_PER_PARAGRAPH:
            issues.append(
                f"a paragraph runs {len(paragraph.split())} words - keep each under "
                f"{MAX_BODY_WORDS_PER_PARAGRAPH}"
            )
            break

    if sources is not None:
        whole_text = f"{headline} {body} {cta}"
        invented = unsupported_statistics(whole_text, sources)
        if invented:
            issues.append(
                "statistics with no source in the fetched data — remove or replace "
                + ", ".join(f"'{claim}'" for claim in invented)
            )
        vague = vague_source_hits(whole_text)
        if vague:
            issues.append(
                "vague attribution to sources the pipeline never fetched — remove "
                + ", ".join(f"'{hit}'" for hit in vague)
            )
    return issues


def reflow_paragraphs(body: str) -> str:
    """Break a wall of text into paragraphs of at most three sentences."""
    if not body.strip():
        return body
    out: List[str] = []
    for block in [p.strip() for p in body.split("\n\n") if p.strip()]:
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(block) if s.strip()]
        for start in range(0, len(sentences), MAX_PARAGRAPH_SENTENCES):
            out.append(" ".join(sentences[start : start + MAX_PARAGRAPH_SENTENCES]))
    return "\n\n".join(out)


def strip_promotional_sentences(text: str) -> str:
    """Drop whole sentences carrying a sales pitch, keeping the rest intact."""
    if not text.strip():
        return text
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    kept = [s for s in sentences if not promotional_hits(s)]
    return " ".join(kept) if kept else ""


def apply_fixes(payload: Dict[str, object], voice: BrandVoice) -> Dict[str, object]:
    """Repair mechanically what the model got wrong.

    Promotional sentences and wall-of-text bodies are fixed outright. A missing
    closing question cannot be invented without reintroducing a template, so
    that one is left to the retry and merely reported.
    """
    fixed = dict(payload)
    if voice.ban_promotional:
        for name in ("body", "cta"):
            value = str(fixed.get(name, "") or "")
            if value:
                fixed[name] = strip_promotional_sentences(value)
    fixed["body"] = reflow_paragraphs(str(fixed.get("body", "") or ""))
    return fixed


__all__ = [
    "BrandVoice",
    "SETA_VOICE",
    "TNT_VOICE",
    "VOICES",
    "PROMOTIONAL_PATTERNS",
    "VAGUE_SOURCE_PATTERNS",
    "statistic_values",
    "unsupported_statistics",
    "vague_source_hits",
    "apply_fixes",
    "post_issues",
    "promotional_hits",
    "reflow_paragraphs",
    "strip_promotional_sentences",
]
