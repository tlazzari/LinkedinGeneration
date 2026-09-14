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

# Consultant filler. Measured across Seta's 32-post archive: EVERY post exceeded
# 3 of these per 100 words (median 5.40, max 8.33), with "strategic" alone
# averaging 3.19 uses per post. Abstraction is what makes the posts
# interchangeable, and interchangeable posts are what the feed ignores.
FILLER_TERMS = [
    "strategic", "complex", "dynamic", "landscape", "significant", "evolving",
    "robust", "unlock", "leverage", "intricate", "nuanced", "underscore",
    "crucial", "synerg", "optimis", "optimiz", "unparalleled", "meticulous",
    "cutting-edge", "seamless", "holistic", "paramount", "pivotal",
    "comprehensive", "sophisticated", "transformational", "value-add",
    "game-changer", "fast-paced", "discerning", "vibrant", "profound",
]


# ── PLAIN ENGLISH FOR NON-NATIVE READERS (2026-09-13) ───────────────────────
# Both audiences read English as a second language: Seta's is Chinese buy-side
# and Italian owners, TNT's is Chinese and European industrial buyers. Tom,
# 2026-09-13: "they are mainly aimed at non native english speakers, the language
# used is too complicated, it should use some sort of simpler language but still
# in a professional tone."
#
# Simple does NOT mean shorter or less expert. It means the Latinate verb gives
# way to the ordinary one, the sentence stops at one idea, and no reader has to
# decode an idiom. The industry nouns stay: "acquisition", "due diligence" and
# "valuation" are the vocabulary of the audience's own job, and replacing them
# would be condescending, not clear.
HARD_WORDS = {
    "utilise": "use", "utilize": "use", "endeavour": "try", "endeavor": "try",
    "commence": "start", "terminate": "end", "ascertain": "find out",
    "elucidate": "explain", "facilitate": "help", "disseminate": "spread",
    "ameliorate": "improve", "exacerbate": "make worse", "mitigate": "reduce",
    "predicated on": "based on", "notwithstanding": "despite",
    "heretofore": "until now", "albeit": "although", "inasmuch": "since",
    "vis-a-vis": "compared with", "vis-à-vis": "compared with",
    "requisite": "needed", "myriad": "many", "plethora": "many",
    "juxtaposition": "contrast", "proliferation": "spread",
    "subsequently": "then", "furthermore": "also", "moreover": "also",
    "consequently": "so", "henceforth": "from now on", "thereby": "so",
    "whereby": "where", "aforementioned": "this", "prior to": "before",
    "in order to": "to", "with regard to": "about", "in the event that": "if",
    "a significant number of": "many", "at this juncture": "now",
    "trajectory": "path", "paradigm": "model", "granular": "detailed",
    "scrutinise": "check closely", "scrutinize": "check closely",
    "verifiable": "provable", "pitfall": "risk", "nuance": "detail",
    "underpin": "support", "delineate": "set out", "expedite": "speed up",
    "leverage": "use", "incentivise": "encourage", "incentivize": "encourage",
}

# Idioms and metaphors are the single worst barrier for a second-language
# reader: each one is a phrase whose meaning cannot be looked up word by word.
IDIOMS = {
    "headwinds": "problems", "tailwinds": "support",
    "move the needle": "make a real difference", "circle back": "come back to",
    "low-hanging fruit": "easy wins", "double down": "commit further",
    "boil the ocean": "try to do everything", "bandwidth": "time",
    "in the weeds": "in the detail", "north star": "main goal",
    "boil down to": "come down to", "the elephant in the room": "the obvious problem",
    "raise the bar": "set a higher standard", "a game of inches": "a slow process",
    "punch above": "do better than", "kick the tyres": "check carefully",
    "kick the tires": "check carefully", "on the table": "available",
    "run the numbers": "do the maths", "moving parts": "separate pieces",
}

# Set phrases that CONTAIN a flagged word but are the correct technical term.
# Found live 2026-09-13: a Market Intelligence post was told to replace
# "leverage" with "use" inside "leveraged buyouts", which is the name of the
# instrument. Flagging real terminology trains the model to write vaguer copy,
# which is the opposite of the goal.
PROTECTED_PHRASES = [
    "leveraged buyout", "leveraged finance", "leveraged loan",
    "leveraged recapitalisation", "leveraged recapitalization",
    "mitigating circumstances", "granular data",
]

# A sentence with more than one idea in it is where non-native comprehension
# actually breaks, well before vocabulary does.
MAX_AVG_SENTENCE_WORDS = 22
MAX_SINGLE_SENTENCE_WORDS = 34


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
    return [p.strip() for p in parts if p.strip()]


def _inflections(term: str) -> str:
    """Regex for a term and its ordinary inflections.

    Needed because the dictionary holds base forms while posts carry inflected
    ones: the first live check found "scrutinizing" and "underscores" sailing
    past an exact-match lookup for "scrutinize". Multi-word phrases are matched
    literally - "predicated on" has no inflections worth chasing.
    """
    if " " in term or "-" in term:
        return re.escape(term)
    stem = term[:-1] if term.endswith("e") else term
    return re.escape(stem) + r"(?:e|es|ed|ing|ation|ations|s|ly)?"


def hard_word_hits(text: str) -> Dict[str, str]:
    """Complex words present, mapped to the simpler word to use instead."""
    lowered = text.lower()
    # Blank out the protected phrases first so a flagged word sitting inside a
    # real technical term cannot match.
    for phrase in PROTECTED_PHRASES:
        lowered = lowered.replace(phrase, " " * len(phrase))
    found: Dict[str, str] = {}
    for term, simpler in {**HARD_WORDS, **IDIOMS}.items():
        # Word-bounded so a match inside a longer unrelated word or a URL cannot
        # fire, while ordinary inflections still do.
        if re.search(r"(?<!\w)" + _inflections(term) + r"(?!\w)", lowered):
            found[term] = simpler
    return found


def long_sentences(text: str) -> List[int]:
    return [len(s.split()) for s in split_sentences(text)
            if len(s.split()) > MAX_SINGLE_SENTENCE_WORDS]


def avg_sentence_words(text: str) -> float:
    sentences = split_sentences(text)
    if not sentences:
        return 0.0
    return sum(len(s.split()) for s in sentences) / len(sentences)


# The same rule as prose, for the prompt. Kept next to the checks that enforce
# it so the ask and the gate can never drift apart.
PLAIN_ENGLISH_DIRECTIVE = (
    "WRITE FOR A READER WHOSE FIRST LANGUAGE IS NOT ENGLISH. Most of this "
    "audience reads English as a second or third language.\n"
    "- One idea per sentence. Average under 20 words, never more than 34.\n"
    "- Use the ordinary word, not the formal one: use (not utilise), start "
    "(not commence), help (not facilitate), before (not prior to), also (not "
    "furthermore), so (not consequently), shows (not underscores), risks (not "
    "pitfalls), details (not nuances), reduce (not mitigate).\n"
    "- NO idioms or metaphors. No 'headwinds', 'move the needle', 'low-hanging "
    "fruit', 'double down', 'circle back'. A second-language reader cannot look "
    "these up word by word.\n"
    "- KEEP the industry terms: acquisition, due diligence, valuation, EBITDA, "
    "joint venture. These are the vocabulary of the reader's own job — "
    "simplifying them is condescending, not clear.\n"
    "- Plain does NOT mean shallow or shorter. The analysis stays expert and "
    "the tone stays professional; only the sentences get easier to read.\n"
)


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
    # Filler terms per 100 words. 2.5 is roughly half the archive median, so it
    # forces concrete nouns without being unreachable in one retry.
    max_filler_per_100_words: float = 2.5
    # Both brands write for readers whose first language is not English, so the
    # plain-English checks are ON by default for any new brand too.
    plain_english: bool = True
    # How often the company may be named. One is right for a post meant to be
    # reshared from a personal profile - more reads as an advert. It is WRONG for
    # a brand that is openly a sales channel: TNT's own prompt asks for the name
    # in the copy and a direct CTA, so a correct TNT post was being marked down
    # for naming TNT twice (seen 2026-09-13 on a pull-stud post that was
    # otherwise exactly right).
    max_brand_mentions: int = 1
    # Companies this brand must never advertise. Building posts on real news made
    # this urgent (2026-09-13): bearing and toolholding news is very often ABOUT a
    # competitor - a new SKF product, a Schaeffler result, a Haimer chuck - and a
    # post that opens on one is free advertising for them, published from TNT's
    # own page. The rule is not "never mention": a competitor's move is often the
    # story. It is "never promote" - report the development, then say what it
    # means for the reader, and never carry their marketing language.
    competitors: Sequence[str] = ()


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

# Bearings, toolholding and workholding names that dominate this trade press.
# A TNT post opening on one of these, in praising terms, is an advert for them.
TNT_COMPETITORS = (
    # Western and Japanese
    "SKF", "Schaeffler", "FAG", "INA", "NSK", "NTN", "Timken", "Koyo", "JTEKT",
    "NACHI", "THK", "IKO", "RBC Bearings", "Rexnord", "Moog",
    "Haimer", "Sandvik", "Kennametal", "Big Daishowa", "Rego-Fix", "Schunk",
    "Hainbuch", "Lyndex", "Nikken", "Emuge", "Guhring", "Walter Tools",
    "Seco Tools", "Iscar", "Mitsubishi Materials", "Kyocera",
    # CHINESE MAKERS - added 2026-09-13 after the list missed the obvious case.
    # The first list was Western and Japanese only, which is absurd when the news
    # search is deliberately Chinese-first: a dry run opened a TNT post on Luoyang
    # Bearing Group's Shenzhen listing and the guard reported "no competitor
    # promoted", because 洛阳/LYC was nowhere in it. Two of the three articles
    # that run cited were Chinese bearing makers.
    "Luoyang Bearing", "LYC", "洛阳轴承", "洛轴",
    "Wafangdian", "ZWZ", "瓦房店轴承", "瓦轴",
    "Harbin Bearing", "HRB", "哈尔滨轴承", "哈轴",
    "C&U", "人本", "人本集团",
    "Wanxiang", "万向钱潮",
    "Wuzhou Xinchun", "五洲新春",
    "Tianma Bearing", "天马轴承",
    "Cixing", "慈兴",
    "Xiangyang Bearing", "襄阳轴承",
    "Changshan Beiming", "常山北明",
    "Zhejiang Sanhua", "三花",
    "NRB Bearings", "Tata Bearings",
)

TNT_VOICE = BrandVoice(
    name="TNT Motion",
    ban_promotional=False,
    # TNT is a sales channel by design: the prompt asks for the name in the copy
    # and a direct CTA, so naming it in the body and again in the close is the
    # intended shape, not a defect.
    max_brand_mentions=3,
    competitors=TNT_COMPETITORS,
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

# STATE TALKING POINTS (2026-09-13). Searching Chinese sources first is right -
# they carry Europe-China deal flow the Western wires never run - but several of
# the highest-ranked outlets are state or party organs, and their FRAMING is
# policy. A generated post had already adopted "pan-securitisation erodes market
# logic" as its own analysis; that is an official formulation, not a finding.
#
# These are not banned words. They are words that must be ATTRIBUTED - "Yicai
# reported that the chamber's president described..." - never asserted in the
# firm's own voice. A Europe-China advisory publishing Beijing's line unattributed
# damages it with exactly the European owners it is trying to reach.
OFFICIAL_FRAMING = [
    r"pan[- ]securitis?z?ation", r"泛安全化",
    r"win[- ]win", r"合作共赢", r"互利共赢",
    r"cold war mentality", r"冷战思维",
    r"hegemon\w*", r"霸权",
    r"containment of china", r"遏制中国",
    r"long[- ]arm jurisdiction", r"长臂管辖",
    r"unilateralis\w+", r"单边主义",
    r"decoupling (?:harms|hurts)", r"脱钩断链",
    r"protectionis\w+ (?:by|of) (?:the )?(?:eu|europe|brussels|washington)",
    r"de[- ]risking is (?:really )?protectionism",
    r"market logic (?:is )?(?:erod\w+|undermin\w+)",
    r"weaponis?z?ing (?:trade|interdependence)",
]

# Words that mark a claim as someone else's rather than the post's own.
_ATTRIBUTION_RE = re.compile(
    r"\b(reported|report|said|says|according to|noted|argued|described|"
    r"told|claims?|stated|wrote|quoted|per\s+\w+|in its view|"
    r"官方|表示|称)\b",
    re.IGNORECASE,
)


def unattributed_official_framing(text: str) -> List[str]:
    """Official talking points the post states as its own analysis.

    Attribution is checked in the SAME sentence and the one before it, which is
    where a reader looks to see whose claim it is.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text)
    hits: List[str] = []
    for i, sentence in enumerate(sentences):
        for pattern in OFFICIAL_FRAMING:
            m = re.search(pattern, sentence, re.IGNORECASE)
            if not m:
                continue
            window = sentence + " " + (sentences[i - 1] if i else "")
            if not _ATTRIBUTION_RE.search(window):
                hits.append(m.group(0))
    return sorted(set(hits))


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


def filler_hits(text: str) -> Dict[str, int]:
    """Consultant filler present in text, term -> count."""
    low = text.lower()
    return {term: low.count(term) for term in FILLER_TERMS if low.count(term)}


def filler_density(text: str) -> float:
    """Filler terms per 100 words."""
    words = len(text.split())
    if not words:
        return 0.0
    return sum(filler_hits(text).values()) / words * 100


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


# Arithmetic shown in the post itself. Tom, 2026-09-13: "something you can
# calculate even if not fully backed by existing data but backed by solid
# calculation is fine". A worked figure is not an invented one - the reader can
# check the step and disagree with the assumption, which a plucked statistic
# never allows. So a number is also supported when the sentence around it shows
# where it came from.
_WORKING_RE = re.compile(
    r"(?:×|\bx\b|\*|/|÷|per\b|each\b|times\b|÷|=|equals\b|"
    r"gives\b|works out\b|that is\b|i\.e\.|roughly\b|about\b|"
    r"assum\w+|if you\b|at \d)",
    re.IGNORECASE,
)


def _has_working(sentence: str) -> bool:
    """True when the sentence shows how its number was arrived at."""
    numbers = re.findall(r"\d[\d,.]*", sentence)
    return len(numbers) >= 2 and bool(_WORKING_RE.search(sentence))


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
    # Sentences that show their own arithmetic: a figure inside one is worked,
    # not invented, and the reader can check the step.
    worked: List[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if _has_working(sentence):
            worked.extend(re.findall(r"\d[\d,.]*", sentence))

    unsupported: List[str] = []
    for claim in statistic_values(text):
        raw = re.sub(r"[^\d.]", "", claim)
        try:
            value = float(raw)
        except ValueError:
            continue
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if any(round(source, decimals) == value for source in available):
            continue
        if any(raw == re.sub(r"[^\d.]", "", w) for w in worked):
            continue          # shown working - allowed, see _has_working
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


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


# Words that turn a mention into an endorsement.
_PRAISE_RE = (
    r"(?:leading|market[- ]leading|best|superior|premium|innovative|"
    r"advanced|breakthrough|world[- ]class|top|trusted|renowned|excellen\w*|"
    r"outstanding|unrivalled|unrivaled|sets the standard|gold standard|"
    r"years of history|heritage|pioneer\w*|flagship|state[- ]of[- ]the[- ]art|"
    # Chinese praise vocabulary. Absent at first, so a CN-sourced post could
    # praise a competitor in Chinese terms and pass clean.
    r"领先|优质|高端|一流|权威|知名|龙头|标杆|实力雄厚|首屈一指)"
)


def competitor_promotion(text: str, competitors: Sequence[str]) -> List[str]:
    """Competitors the post appears to be selling FOR, not merely reporting on.

    A competitor named in the ANCHOR - the headline or the opening sentence - is
    promotion whatever the wording. That is the part the feed shows and the part
    the whole post is built on, so "Luoyang Bearing Group listed on the Shenzhen
    exchange" as an opener is TNT amplifying a competitor's corporate news even
    though no adjective is attached. Widened from headline-only on 2026-09-13,
    when exactly that post was generated and reported clean.

    Deeper in the body it takes praise vocabulary in the same sentence, because a
    competitor's move is often legitimately the story - "Schaeffler reported a 4%
    drop in orders, which matters for lead times" is reporting, not promotion.
    """
    hits: List[str] = []
    lines = text.split("\n", 1)
    body = lines[1] if len(lines) > 1 else ""
    first_sentence = re.split(r"(?<=[.!?。])\s*", body.strip(), maxsplit=1)[0] if body else ""
    anchor = (lines[0] if lines else "") + " " + first_sentence
    headline = anchor
    for name in competitors:
        # A Chinese name has no word boundary - every CJK character is \w, so
        # "(?<!\w)洛阳" can never match mid-sentence, which is exactly how 洛阳
        # slipped past. Match those as plain substrings.
        if _has_cjk(name):
            in_headline = name in headline
            sentence_hit = lambda sentence, n=name: n in sentence
        else:
            pattern = r"(?<!\w)" + re.escape(name) + r"(?!\w)"
            in_headline = bool(re.search(pattern, headline, re.IGNORECASE))
            sentence_hit = lambda sentence, p=pattern: bool(
                re.search(p, sentence, re.IGNORECASE))
        if in_headline:
            hits.append(name)
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if sentence_hit(sentence) and re.search(_PRAISE_RE, sentence, re.IGNORECASE):
                hits.append(name)
                break
    return sorted(set(hits))


def post_issues(
    payload: Dict[str, object],
    voice: BrandVoice,
    sources: Optional[str] = None,
    post_type: str = "",
) -> List[str]:
    """Every reason this post is not repost-worthy. Empty list == publishable.

    `sources` is the data actually fetched for this post (chart figures, news
    context, curated proof points). Pass it to check that every statistic the
    post asserts traces back to real data; omit it to skip that check.

    `post_type` lets holiday greetings off the rules written for analysis posts:
    a Ferragosto message does not need a discussion question and is allowed to
    say "festive" without being accused of house cliches.
    """
    is_holiday = post_type == "holiday"
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
    if brand_count > voice.max_brand_mentions:
        _limit = voice.max_brand_mentions
        issues.append(
            f"{voice.name} named {brand_count} times - name it at most "
            + ("once" if _limit == 1 else f"{_limit} times")
        )
    if voice.brand_in_closing_only and re.search(
        brand, f"{headline} {body}", flags=re.IGNORECASE
    ):
        issues.append(f"{voice.name} appears in the headline or body - closing paragraph only")

    if voice.require_closing_question and not is_holiday and "?" not in cta:
        issues.append("closing paragraph asks no question - end on a genuine open question")

    for term in (() if is_holiday else voice.overused_headline_terms):
        if term in headline.lower():
            issues.append(f"headline reuses the house cliche '{term}'")

    if not is_holiday:
        density = filler_density(f"{headline} {body} {cta}")
        if density > voice.max_filler_per_100_words:
            worst = sorted(
                filler_hits(f"{headline} {body} {cta}").items(),
                key=lambda kv: -kv[1],
            )[:4]
            issues.append(
                f"too much abstract filler ({density:.1f} per 100 words, limit "
                f"{voice.max_filler_per_100_words}) — replace "
                + ", ".join(f"'{term}' x{count}" for term, count in worst)
                + " with concrete nouns, names and specifics"
            )

    if not is_holiday:
        framing = unattributed_official_framing(whole)
        if framing:
            issues.append(
                "states a government talking point as your own analysis - "
                + ", ".join(f"'{f}'" for f in framing)
                + ". Attribute it to the outlet that said it and give the other "
                "side, or drop it"
            )

    if voice.competitors and not is_holiday:
        # Flagged for the retry, never stripped: deleting the sentence would gut
        # a legitimate "what this means for you" piece. The model is told to keep
        # the development and drop the endorsement.
        promoted = competitor_promotion(whole, voice.competitors)
        if promoted:
            issues.append(
                "reads as an advert for " + ", ".join(promoted)
                + " - report what happened and what it means for the reader, never "
                "their product claims or marketing language"
            )

    if voice.plain_english and not is_holiday:
        prose = f"{headline} {body} {cta}"
        hard = hard_word_hits(prose)
        if hard:
            worst = sorted(hard.items())[:5]
            issues.append(
                "language too complex for second-language readers - replace "
                + ", ".join(f"'{term}' with '{simpler}'" for term, simpler in worst)
            )
        avg = avg_sentence_words(body)
        if avg > MAX_AVG_SENTENCE_WORDS:
            issues.append(
                f"sentences average {avg:.0f} words - keep the average under "
                f"{MAX_AVG_SENTENCE_WORDS}; one idea per sentence"
            )
        overlong = long_sentences(body)
        if overlong:
            issues.append(
                f"{len(overlong)} sentence(s) run to {max(overlong)} words - split "
                f"anything over {MAX_SINGLE_SENTENCE_WORDS} into two"
            )

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
    "FILLER_TERMS",
    "filler_density",
    "filler_hits",
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
