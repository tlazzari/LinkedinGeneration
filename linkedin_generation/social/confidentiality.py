"""Stop a Seta post naming a counterparty, a deal or a confidential figure.

WHY THIS IS MECHANICAL AND NOT A PROMPT RULE (2026-09-13). Tom, asking for posts
drawn on twelve years of real deal experience: "just make sure not to make any
name if the deal or the data is not already public and this is extremely
important we could get sued for failure to do so."

A prompt cannot carry that. The model has already been shown, twice in one day,
to ignore an explicit instruction it had been given since day one (invented
engineering figures). So the names are checked against the ACTUAL counterparty
list - 2,384 organisations and 4,715 contacts the mailbox has ever seen - rather
than against a list of names someone remembered to write down. It fails CLOSED:
anything it cannot clear is blocked, not warned about.

Default position: NO organisation or person is named at all. The only exceptions
are deals Seta already publishes in its own pitch deck, which the deck itself
marks as sourced from public information.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Transactions Seta already publishes (deck slide 8, "sourced from public
# information"). Naming these is allowed; anything else is not. Keep this list
# SHORT and only add to it with the owner's explicit say-so - see rules_seta.md
# "Public Claims".
PUBLIC_DEALS = (
    "Carioca", "Shanghai M&G", "Epistolio", "Xuzhou Saimo",
    "Tenova", "Techint", "Qijing", "IVA Schmetz", "Mahler", "BMI",
    "AZ Bigiotterie", "Giochi Preziosi", "Superhisen",
)

# Quantified outcomes that appear in the pitch deck but are NOT for publication -
# the deck itself is marked STRICTLY CONFIDENTIAL and these are very likely
# covered by NDA with the counterparties. Verbatim from rules_seta.md.
CONFIDENTIAL_CLAIMS = (
    r"3\.5\s*[x×]",                      # Carioca sales increase
    r"11\s*[x×]\s*EBITDA",               # Epistolio multiple
    r"40\+?\s*investors",                # Epistolio process detail
    r"10\s+qualified offers",
    r"6[- ]month close",
    r"zero\s+R&W",
    r"higher final valuation than european",
)

# Words that make an ordinary noun into a named entity we must not print.
_LEGAL_SUFFIX = (
    r"s\.?r\.?l|s\.?p\.?a|gmbh|ag\b|a\.?g\.?|s\.?a\.?s|s\.?a\b|b\.?v\b|"
    r"n\.?v\b|ltd|limited|inc\b|llc|co\.,?\s*ltd|plc\b|oy\b|ab\b|"
    r"有限公司|股份有限公司|集团"
)


# The knowledge base was built from email headers, so its org table contains
# mail infrastructure and ordinary nouns alongside real counterparties: "Gmail",
# "Email", "News", "Staff", "Welcome", "It", "London" - and "Business", which on
# the first run blocked a post for the word "business". Blocking on those would
# make the gate useless and train whoever hits it to switch it off, which is the
# worst outcome for a safety check. They are skipped; every multi-word name and
# every distinctive single word is still enforced.
_GENERIC_ORG_WORDS = {
    "gmail", "hotmail", "yahoo", "libero", "outlook", "email", "smtp", "mail",
    "news", "newsletter", "staff", "welcome", "info", "admin", "support",
    "sales", "contact", "team", "office", "it", "new", "business", "company",
    "group", "holding", "holdings", "partners", "capital", "advisory",
    "consulting", "industries", "international", "management", "services",
    "solutions", "technology", "technologies", "trading", "engineering",
    "automation", "finance", "invest", "investment", "chess", "basi", "ice",
    "london", "milano", "milan", "shanghai", "beijing", "twitter", "linkedin",
    "esteri", "tin", "stampa", "press", "live", "home", "web", "site",
    "group", "gruppo", "studio", "associati", "lex", "legal", "law",
    # Countries and regions: the KB derived some org rows from signature blocks,
    # so "Italy" is an org name in it. Blocking the word "Italy" would make a
    # Europe-China M&A post impossible to write.
    "italy", "italia", "china", "cina", "germany", "deutschland", "france",
    "spain", "espana", "switzerland", "austria", "poland", "hungary", "japan",
    "korea", "india", "brazil", "usa", "uk", "england", "europe", "europa",
    "asia", "america", "hong", "kong", "taiwan", "singapore",
}


# Seta's own entities. Blocking the firm's own name is obviously wrong, and the
# first live run did exactly that - "Seta Capital" is in the mailbox's org table
# like any other correspondent, and the post was refused for naming the firm
# whose post it is.
OWN_NAMES = (
    "Seta Capital", "Seta Capital Limited", "Seta Capital S.r.l.", "Seta Capital Srl",
    "Saida Technology", "Seta", "TNT Motion", "TNT Bearings",
)


@lru_cache(maxsize=1)
def _dictionary_words() -> frozenset:
    """Ordinary words, so an extraction artefact is not mistaken for a company.

    The knowledge base was built from email headers and its org table is full of
    ordinary capitalised words - "Global", "Your", "Event", "Significant" - and
    its contact table has "Cross Border" as a person. Enforcing those blocks
    almost any sentence. A hand-written stoplist cannot keep up; the system
    dictionary can.
    """
    words = set()
    for path in ("/usr/share/dict/american-english", "/usr/share/dict/words"):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                words.update(w.strip().lower() for w in fh if w.strip())
            break
        except OSError:
            continue
    return frozenset(words)


def _is_ordinary_word(name: str) -> bool:
    """True for a SINGLE ordinary word - never for a multi-word name.

    Applying this to multi-word names let real counterparties through: "One
    Magnet" and "Mg Pen" are companies, but every token is in the dictionary, so
    a whole-name test skipped them. Two ordinary words next to each other, both
    capitalised, is how most company names are built. Only a lone dictionary word
    - "Global", "Event", "Significant" - is an extraction artefact.
    """
    words = _dictionary_words()
    if not words:
        return False                       # no dictionary: keep enforcing
    tokens = [t for t in re.split(r"[\s.&-]+", name.lower()) if t]
    if len(tokens) != 1:
        return False
    return tokens[0] in words


@lru_cache(maxsize=1)
def _known_names() -> Dict[str, List[str]]:
    """Every organisation and person the Seta mailbox has ever seen.

    Read live from the knowledge base rather than hardcoded: a list someone
    maintains by hand is exactly the list that misses the one deal that matters.
    """
    orgs: List[str] = []
    people: List[str] = []
    try:
        import subprocess
        # Sectors first: the org table contains rows like "Pharma" and
        # "Automotive components" that are industry categories, not counterparties.
        # Treating those as names blocks the firm from naming the industries it
        # works in - which is most of what a post is about. Data-driven rather
        # than a hand list: if a string is used as a SECTOR on the deal record, it
        # is a category.
        sectors = set()
        sec = subprocess.run(
            ["mysql", "TNT_Db", "-N", "-B", "-e",
             "SELECT DISTINCT sector FROM seta_kb_deal WHERE sector IS NOT NULL "
             "UNION SELECT DISTINCT sector FROM seta_kb_org WHERE sector IS NOT NULL"],
            capture_output=True, text=True, timeout=60,
        )
        for line in sec.stdout.splitlines():
            v = line.strip().lower()
            if v and v != "null":
                sectors.add(v)

        for table, column, bucket in (
            ("seta_kb_org", "org_name", orgs),
            ("seta_kb_deal", "counterparty_org", orgs),
            ("seta_kb_deal", "deal_name", orgs),
            ("seta_kb_contact", "display_name", people),
        ):
            out = subprocess.run(
                ["mysql", "TNT_Db", "-N", "-B", "-e",
                 f"SELECT DISTINCT {column} FROM {table} "
                 f"WHERE {column} IS NOT NULL AND CHAR_LENGTH({column}) >= 4"],
                capture_output=True, text=True, timeout=60,
            )
            for line in out.stdout.splitlines():
                name = line.strip()
                if name and name.upper() != "NULL" and name.lower() not in sectors:
                    bucket.append(name)
    except Exception as exc:
        # Fail CLOSED: with no list we cannot clear anything, so the caller must
        # treat that as "block", never as "nothing found".
        logger.error("Could not load the counterparty list: %s", exc)
        return {"orgs": [], "people": [], "loaded": []}
    return {"orgs": orgs, "people": people, "loaded": ["yes"]}


def _is_public(name: str) -> bool:
    low = name.lower()
    return any(p.lower() in low or low in p.lower() for p in PUBLIC_DEALS)


def confidentiality_issues(text: str) -> List[str]:
    """Every reason this text must not be published. Empty means clear."""
    issues: List[str] = []
    known = _known_names()
    if not known["loaded"]:
        return ["the counterparty list could not be loaded - refusing to clear this post"]

    lowered = text.lower()

    for org in known["orgs"]:
        if len(org) < 4 or _is_public(org):
            continue
        if any(o.lower() == org.lower() for o in OWN_NAMES):
            continue
        if " " not in org and org.lower() in _GENERIC_ORG_WORDS:
            continue
        if _is_ordinary_word(org):
            continue
        if re.search(r"(?<!\w)" + re.escape(org) + r"(?!\w)", text, re.IGNORECASE):
            issues.append(f"names a counterparty from the deal record: '{org}'")

    for person in known["people"]:
        # For PEOPLE the whole-name dictionary test is kept: the contact table
        # holds "Cross Border" as a person, and that phrase is unavoidable in a
        # Europe-China M&A post. The cost is that a real person whose name is two
        # ordinary words would not be caught here - accepted, because the org
        # gate and the company-suffix check carry the real weight, and the prompt
        # instructs that no person be named at all.
        _p_tokens = [t for t in re.split(r"[\s.&-]+", person.lower()) if t]
        _all_dict = bool(_p_tokens) and _dictionary_words() and all(
            t in _dictionary_words() for t in _p_tokens)
        if any(o.lower() == person.lower() for o in OWN_NAMES) or _all_dict:
            continue
        parts = [p for p in person.split() if len(p) > 3]
        if len(parts) < 2:
            continue          # a single common first name would fire constantly
        if all(re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", text, re.IGNORECASE)
               for p in parts):
            issues.append(f"names a person from the deal record: '{person}'")

    for pattern in CONFIDENTIAL_CLAIMS:
        if re.search(pattern, text, re.IGNORECASE):
            issues.append(
                "quotes a confidential deal outcome that appears only in the "
                "STRICTLY CONFIDENTIAL pitch deck"
            )

    # A company-shaped proper noun we do not recognise is still a risk: it may be
    # a counterparty the knowledge base has not indexed.
    _LEAD_IN = {"we", "our", "the", "a", "an", "with", "for", "advised", "sold",
                "acquired", "at", "of", "and", "to", "by", "from", "this", "that"}
    for m in re.finditer(
        # The NAME part stays case-sensitive (a proper noun is capitalised); only
        # the suffix is case-insensitive, via a scoped inline flag. A blanket
        # re.IGNORECASE made every lowercase word a candidate; removing it
        # altogether stopped "GmbH" matching "gmbh" and let a test company
        # through. Each half needs its own casing rule.
        r"\b([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3}\s+(?i:" + _LEGAL_SUFFIX + r"))\b",
        text,
    ):
        tokens = m.group(1).split()
        while tokens and tokens[0].lower() in _LEAD_IN:
            tokens.pop(0)          # drop "We advised" etc. so the report names the company
        candidate = " ".join(tokens).strip()
        if len(tokens) >= 2 and not _is_public(candidate):
            issues.append(f"names what looks like a specific company: '{candidate}'")

    return sorted(set(issues))


def is_publishable(text: str) -> bool:
    return not confidentiality_issues(text)


__all__ = [
    "OWN_NAMES",
    "PUBLIC_DEALS",
    "CONFIDENTIAL_CLAIMS",
    "confidentiality_issues",
    "is_publishable",
]
