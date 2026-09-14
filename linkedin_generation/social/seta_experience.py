"""Real Seta deal experience for a post — anonymised before it leaves this file.

Twelve years of mailbox turned into 778 deals, 2,384 organisations and 1,419
agreed fee terms. That is the firm's actual experience and it is far better
material than another think-piece. It is also the single most dangerous thing in
this pipeline to hand to a language model, because most of it is covered by NDA.

THE DESIGN RULE: no identity ever leaves this module. Not "the model is told not
to name them" - the names are never in the prompt. What crosses the boundary is
shape only: a sector, a side, a country grouping, a year span, counts, and a
status distribution. `deal_name` and `counterparty_org` are never selected.

`social.confidentiality` is the backstop on the way out, checking the finished
post against the real counterparty list. Two independent layers, because the
consequence of a miss is a lawsuit rather than a bad post.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Below this a "pattern" is one or two deals, which is an anecdote about
# identifiable parties however it is worded.
MIN_DEALS_FOR_A_PATTERN = 4


def _query(sql: str) -> List[List[str]]:
    try:
        out = subprocess.run(
            ["mysql", "TNT_Db", "-N", "-B", "-e", sql],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            logger.warning("experience query failed: %s", out.stderr[:200])
            return []
        return [line.split("\t") for line in out.stdout.splitlines() if line.strip()]
    except Exception as exc:
        logger.warning("experience query error: %s", exc)
        return []


def sector_patterns(limit: int = 6) -> List[Dict[str, object]]:
    """Aggregate, identity-free shapes across the deal record.

    Deliberately selects COUNTS and CATEGORIES only. Adding deal_name or
    counterparty_org to this query would defeat the entire design.
    """
    rows = _query(
        "SELECT sector, "
        "       COUNT(*) AS deals, "
        "       SUM(status='closed')  AS closed, "
        "       SUM(status='stalled') AS stalled, "
        "       SUM(status='dead')    AS dead, "
        "       SUM(side='buy')       AS buyside, "
        "       SUM(side='sell')      AS sellside, "
        "       MIN(YEAR(first_seen)) AS y0, "
        "       MAX(YEAR(last_seen))  AS y1, "
        "       COUNT(DISTINCT country) AS countries "
        "FROM seta_kb_deal "
        "WHERE sector IS NOT NULL AND sector <> '' "
        "GROUP BY sector "
        f"HAVING deals >= {MIN_DEALS_FOR_A_PATTERN} "
        "ORDER BY deals DESC "
        f"LIMIT {int(limit)}"
    )
    out: List[Dict[str, object]] = []
    for r in rows:
        if len(r) < 10:
            continue
        def n(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0
        out.append({
            "sector": r[0][:80], "deals": n(r[1]), "closed": n(r[2]),
            "stalled": n(r[3]), "dead": n(r[4]), "buyside": n(r[5]),
            "sellside": n(r[6]), "from_year": n(r[7]), "to_year": n(r[8]),
            "countries": n(r[9]),
        })
    return out


def build_experience_context(pattern: Optional[Dict[str, object]] = None) -> str:
    """The prompt block. Shape only - no party is named, because none is loaded."""
    patterns = [pattern] if pattern else sector_patterns()
    patterns = [p for p in patterns if p and p.get("deals", 0) >= MIN_DEALS_FOR_A_PATTERN]
    if not patterns:
        return ""

    lines = [
        "SETA'S OWN DEAL RECORD — real, and the reason this post is worth reading.",
        "THIS IS THE POST'S ANCHOR when no news block appears above: open on what "
        "the firm has seen across these mandates, not on a theme and not on "
        "someone else's article. Twelve years of watching the same thing happen "
        "is the one thing a competitor cannot copy.",
        "These are aggregates from twelve years of live mandates:",
        "",
    ]
    # COUNTS ARE FOR CHOOSING THE SUBJECT, NOT FOR PUBLICATION (2026-09-13).
    # A generated post said "5 sell-side deals in automotive (2017-2025) and 5 in
    # robotics" - true, drawn from the record, and a bad idea. Tom: "numbering the
    # mandates of Seta Capital does not sound like a good idea because not always
    # flattering the relative small number." A boutique's strength is what it has
    # SEEN, not how many times. The counts still decide which sectors are worth
    # writing about; they just never leave this function.
    for p in patterns[:3]:
        span = (f"{p['from_year']}-{p['to_year']}"
                if p["from_year"] and p["to_year"] and p["from_year"] != p["to_year"]
                else "")
        bits = ["mandates run" + (f" between {span}" if span else "")]
        if p["countries"] > 1:
            bits.append("in more than one country")
        # Direction as a tendency, never as a tally.
        if p["sellside"] > p["buyside"] * 2:
            bits.append("mostly sell-side")
        elif p["buyside"] > p["sellside"] * 2:
            bits.append("mostly buy-side")
        lines.append(f"- {p['sector']}: " + ", ".join(bits) + ".")
    lines += [
        "",
        "NEVER STATE HOW MANY. Do not write a number of mandates, deals or "
        "transactions - not per sector, not per year, not in total. The firm is a "
        "boutique and a count invites the reader to weigh it against a bulge "
        "bracket, which is the wrong comparison and not the point. Write what the "
        "record SHOWS: 'across the industrial mandates we have run', 'this comes "
        "up in almost every family-owned sale we see', 'we have watched this "
        "happen since 2014'. The only figures that may be published are the ones "
        "already on the firm's own materials: 10+ closed transactions and EUR "
        "150M+ in aggregate value.",
        "",
        "HOW TO USE IT — and the first rule is absolute:",
        "1. NAME NOBODY. No company, no person, no deal. Not once, not as an "
        "example, not 'a company we will call X'. Describe parties only by what "
        "they are: 'a German heat-treatment business', 'a Chinese strategic "
        "buyer', 'a family-owned components maker in northern Italy'. Most of "
        "this record is under NDA and naming a party is a lawsuit, not a "
        "style problem.",
        "2. Quote NO deal value, multiple, fee or percentage from any specific "
        "transaction. The aggregate counts above are the only numbers you have.",
        "3. Write from the PATTERN: what repeats across these mandates, what "
        "makes one stall, what a seller learns too late. That is the value - "
        "twelve years of seeing the same thing happen is worth more than any "
        "single story, and it is safe to tell.",
        "4. Say plainly that this comes from the firm's own mandates. 'Across "
        "the industrial mandates we have run' is honest and specific enough.",
    ]
    return "\n".join(lines)


__all__ = ["MIN_DEALS_FOR_A_PATTERN", "sector_patterns", "build_experience_context"]
