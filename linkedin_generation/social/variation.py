"""Post variation engine - every post a different shape (2026-10-23).

WHY. Tom, 2026-10-23: reposting Seta posts reached almost nobody. Measured the
same day in LinkedIn analytics: page posts reach 54-80 of 1,490 followers, his
reposts with a comment 10-118 of 4,115, zero real comments in a month. Every
post had the same skeleton - "X reported on <date>...", three paragraphs,
"Over more than a decade of Europe-China mandates, Seta Capital has watched...",
a closing question, four hashtags. Readers learn a template and scroll past it;
the feed's classifier demotes near-duplicates. Tom: "implement some randomizing
engine that can be extended to the social media part of bolla".

WHAT VARIES. The FORM of a post: its shape, how it opens, its length, how it
ends, and how many hashtags it carries. Each dimension is a weighted list of
options; the engine draws one option per dimension.

WHAT NEVER VARIES. The rules. Sourcing, confidentiality, plain English, the
decade-of-experience line, no promotion - every gate in post_quality.py and
confidentiality.py runs on every variant exactly as before. A variant can only
relax the one thing that IS a form choice: whether the post must end on a
question (a "takeaway" ending ends on a statement instead).

HOW IT DRAWS.
- Deterministic per brand and day (seeded on "<brand>:<date>"), so a dry run
  and the publish run of the same morning produce the same choices, and a test
  can pin them.
- Anti-repetition: an option used in any of the brand's last RECENT_WINDOW posts
  has its weight multiplied by REPEAT_PENALTY. Demoted, never banned - with few
  options a ban would turn variety into a fixed rotation, which is a template
  again.
- History comes from the artefacts (what was actually written), never a side
  file, the same reason recent_post_history() reads them.

EXTENDING (Bolla). Every brand on the shared generator gets the defaults below
with nothing to configure. A brand may tune weights, or switch an option off
with weight 0, in its campaign config:

    variation:
      enabled: true
      weights:
        closing: {question: 3, takeaway: 2}
        shape: {numbered: 0}

Unknown dimensions or options in a config are ignored with a warning, so a
typo in a tenant's JSON can never stop a scheduled run.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

RECENT_WINDOW = 3
REPEAT_PENALTY = 0.2


@dataclass(frozen=True)
class Option:
    key: str
    weight: float
    directive: str


# Brand-neutral by construction: no company name, no sector. A test asserts it.
DIMENSIONS: Dict[str, Sequence[Option]] = {
    "shape": (
        Option("event_read", 3,
               "SHAPE: open on the reported development (who, what, when, the outlet), then "
               "spend the rest on what it means for the reader - the read the reporting does "
               "not give."),
        Option("single_insight", 2,
               "SHAPE: ONE sharp observation, argued in 2 short paragraphs. No background "
               "section, no list. The whole post makes a single point well."),
        Option("numbered", 2,
               "SHAPE: one opening line, then exactly three numbered points ('1.', '2.', "
               "'3.'), each its own paragraph of one or two sentences. Each point is a "
               "concrete consequence or a thing to check - not a restatement."),
        Option("contrast", 2,
               "SHAPE: state the common reading of this subject in one or two sentences, then "
               "say where it is wrong or incomplete and why. The tension is the post."),
        Option("practical", 1,
               "SHAPE: write it as practical advice to the person who has to act on this - "
               "an owner, a buyer, a CFO: what to do or check first, and what goes wrong if "
               "they do not. Describe situations generically; invent no case."),
    ),
    "opening": (
        Option("statement", 3,
               "OPENING: the first sentence is a plain, confident statement of the point - "
               "it must make sense on its own, because LinkedIn shows only the first two "
               "lines."),
        Option("figure", 2,
               "OPENING: the first sentence leads with ONE concrete figure taken from the "
               "material supplied above. If no figure is supplied, open with a plain "
               "statement instead - never invent one."),
        Option("question", 1,
               "OPENING: the first sentence is a short, direct question the reader would "
               "actually ask themselves about this subject (not a yes/no question)."),
        Option("scene", 1,
               "OPENING: the first sentence describes the concrete situation in one line - a "
               "plant, a negotiation, a shipment, a board meeting - generically, naming no "
               "party, before the point is made."),
    ),
    "length": (
        Option("short", 2, "LENGTH: 70-120 words in total across headline, body and closing."),
        Option("medium", 3, "LENGTH: 120-190 words in total across headline, body and closing."),
        Option("long", 1, "LENGTH: 190-250 words in total across headline, body and closing."),
    ),
    "closing": (
        Option("question", 3,
               "CLOSING: the closing paragraph ends with ONE genuine open question a "
               "practitioner would answer from their own work - a value they measure, a "
               "choice they made, something they have seen go wrong. Never yes/no, never "
               "'what are your thoughts' or 'what challenges are you facing'."),
        Option("takeaway", 2,
               "CLOSING: the closing paragraph ends on a clear one-sentence takeaway - "
               "what the reader should do or watch next. NO question at the end."),
    ),
    "hashtags": (
        Option("2", 2, "HASHTAGS: return exactly 1 hashtag besides the brand tag."),
        Option("3", 2, "HASHTAGS: return exactly 2 hashtags besides the brand tag."),
        Option("4", 1, "HASHTAGS: return exactly 3 hashtags besides the brand tag."),
    ),
}


@dataclass(frozen=True)
class Variation:
    choices: Mapping[str, str] = field(default_factory=dict)
    directives: Sequence[str] = ()

    @property
    def requires_closing_question(self) -> bool:
        return self.choices.get("closing", "question") == "question"

    @property
    def max_hashtags(self) -> Optional[int]:
        try:
            return int(self.choices["hashtags"])
        except (KeyError, ValueError):
            return None

    def prompt_block(self) -> str:
        if not self.directives:
            return ""
        return ("FORM OF THIS POST (chosen so posts do not all read alike - follow every "
                "line; the rules above still apply in full):\n"
                + "\n".join(f"- {d}" for d in self.directives) + "\n")

    def as_metadata(self) -> Dict[str, str]:
        return {f"variation_{k}": v for k, v in self.choices.items()}


def _weights(config: Mapping) -> Dict[str, Dict[str, float]]:
    out = {dim: {o.key: float(o.weight) for o in opts} for dim, opts in DIMENSIONS.items()}
    for dim, table in dict((config or {}).get("weights") or {}).items():
        if dim not in out or not isinstance(table, Mapping):
            logger.warning("VARIATION: unknown dimension '%s' in config - ignored", dim)
            continue
        for key, w in table.items():
            key = str(key)
            if key not in out[dim]:
                logger.warning("VARIATION: unknown option '%s.%s' in config - ignored", dim, key)
                continue
            try:
                out[dim][key] = max(0.0, float(w))
            except (TypeError, ValueError):
                logger.warning("VARIATION: weight for '%s.%s' is not a number - ignored", dim, key)
    return out


def recent_choices(history: Iterable[Mapping], window: int = RECENT_WINDOW) -> Dict[str, List[str]]:
    """Options used in the last `window` posts, per dimension, newest last."""
    posts = list(history)[-window:]
    used: Dict[str, List[str]] = {dim: [] for dim in DIMENSIONS}
    for post in posts:
        for dim in DIMENSIONS:
            v = post.get(f"variation_{dim}")
            if v:
                used[dim].append(str(v))
    return used


def choose(
    brand_key: str,
    day: date,
    history: Iterable[Mapping] = (),
    config: Optional[Mapping] = None,
    post_type: str = "",
) -> Variation:
    """Draw this post's form. Empty Variation when disabled or for holiday posts."""
    config = config or {}
    if config.get("enabled", True) is False or post_type == "holiday":
        return Variation()
    weights = _weights(config)
    used = recent_choices(history)
    rng = random.Random(f"{brand_key}:{day.isoformat()}")
    choices: Dict[str, str] = {}
    directives: List[str] = []
    for dim, opts in DIMENSIONS.items():
        table = []
        for o in opts:
            w = weights[dim][o.key]
            if o.key in used[dim]:
                w *= REPEAT_PENALTY
            table.append((o, w))
        if sum(w for _, w in table) <= 0:
            # every option switched off: fall back to the defaults rather than stop
            table = [(o, float(o.weight)) for o in opts]
        pick = rng.choices([o for o, _ in table], weights=[w for _, w in table], k=1)[0]
        choices[dim] = pick.key
        directives.append(pick.directive)
    return Variation(choices=choices, directives=tuple(directives))


__all__ = ["DIMENSIONS", "Option", "Variation", "choose", "recent_choices",
           "RECENT_WINDOW", "REPEAT_PENALTY"]


# ── Checking the form was followed ───────────────────────────────────────────────
# A dry run on 2026-10-23 showed the model treats the FORM block as a suggestion: a
# "practical" post came back as the usual news recap, a "numbered" one with no numbers.
# Same lesson as every other rule in this pipeline - a prompt is a request, so the
# result is checked. These are COSMETIC (prefix "form:"): they buy one retry and never
# cost a post. Wording deliberately avoids every BLOCKING_MARKERS phrase.
import re as _re

WORD_RANGES = {"short": (60, 140), "medium": (110, 210), "long": (170, 275)}
_NUMBERED_LINE = _re.compile(r"(?m)^\s*([1-3])[.)]\s+\S")


def variation_issues(payload: Mapping, variation: Optional[Variation]) -> List[str]:
    if variation is None or not variation.choices:
        return []
    ch = variation.choices
    headline = str(payload.get("headline", "") or "")
    body = str(payload.get("body", "") or "")
    cta = str(payload.get("cta", "") or "")
    issues: List[str] = []

    lo, hi = WORD_RANGES.get(ch.get("length", ""), (0, 10_000))
    words = len(f"{headline} {body} {cta}".split())
    if not lo <= words <= hi:
        issues.append(f"form: the post runs {words} words - this one must be "
                      f"{lo}-{hi} words in total")

    paragraphs = [p for p in body.split("\n\n") if p.strip()]
    shape = ch.get("shape")
    if shape == "numbered" and len(set(_NUMBERED_LINE.findall(body))) < 3:
        issues.append("form: this post must carry exactly three numbered points "
                      "('1.', '2.', '3.'), each its own paragraph")
    if shape == "single_insight" and len(paragraphs) > 3:
        issues.append(f"form: this post makes ONE point in 2 short paragraphs - "
                      f"it has {len(paragraphs)}")

    first = (paragraphs[0] if paragraphs else "").strip()
    first_sentence = _re.split(r"(?<=[.!?])\s+", first, maxsplit=1)[0] if first else ""
    # the headline is the first line the feed shows, so a question there counts too
    if ch.get("opening") == "question" and not (first_sentence.endswith("?")
                                                or headline.strip().endswith("?")):
        issues.append("form: the body must open with a short direct question")

    if ch.get("closing") == "takeaway" and cta.rstrip().endswith("?"):
        issues.append("form: the closing paragraph must end on a takeaway sentence, "
                      "not a question")
    return issues


__all__ += ["variation_issues", "WORD_RANGES"]


_INLINE_MARKER = _re.compile(r"(?<=[.!?:])\s+([1-3][.)])\s+(?=\S)")
_DANGLING_MARKER = _re.compile(r"\s+([1-3][.)])\s*\n\n")


def apply_form_fixes(payload: Mapping, variation: Optional[Variation]) -> Dict[str, object]:
    """Mechanical repairs for the form - run AFTER post_quality.apply_fixes.

    Numbered points: the model often writes "1. ... 2. ..." inline, and
    reflow_paragraphs() reads "2." as a sentence end, leaving the marker dangling at
    the end of the previous paragraph (seen 2026-10-23). Each point goes on its own
    paragraph, marker first.
    """
    out = dict(payload)
    if variation is None or variation.choices.get("shape") != "numbered":
        return out
    body = str(out.get("body", "") or "")
    body = _DANGLING_MARKER.sub(lambda m: "\n\n" + m.group(1) + " ", body)
    body = _INLINE_MARKER.sub(lambda m: "\n\n" + m.group(1) + " ", body)
    body = _re.sub(r"(?m)^([1-3][.)])\s*\n\n", r"\1 ", body)
    out["body"] = _re.sub(r"\n{3,}", "\n\n", body).strip()
    return out


__all__ += ["apply_form_fixes"]
