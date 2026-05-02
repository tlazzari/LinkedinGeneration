"""Utility helpers for leveraging TNT manual content."""

from __future__ import annotations

import json
import random
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable


SECTIONS_PATH = Path("data/manual_sections.json")
CASE_STUDIES_PATH = Path("data/case_studies.json")


@lru_cache(maxsize=1)
def _load_sections() -> list[dict[str, str]]:
    if not SECTIONS_PATH.exists():
        return []
    return json.loads(SECTIONS_PATH.read_text())


def _score_section(section: dict[str, str], keywords: Iterable[str]) -> int:
    title = section["title"].lower()
    content = section["content"].lower()
    score = 0
    for keyword in keywords:
        score += title.count(keyword)
        score += content.count(keyword)
    return score


def _clean_lines(content: str) -> list[str]:
    lines: list[str] = []
    for raw_line in content.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        if re.match(r"^[\d .]{5,}$", line):
            continue
        if "................................" in line:
            continue
        lines.append(line)
    return lines


def _summarise(content: str, max_words: int = 90) -> str:
    cleaned_lines = _clean_lines(content)
    tokens = " ".join(cleaned_lines).split()
    if len(tokens) <= max_words:
        return " ".join(tokens)
    return " ".join(tokens[:max_words]) + " …"


def build_manual_context(*, keywords: Iterable[str], limit: int = 3) -> str:
    sections = _load_sections()
    if not sections:
        return ""

    scored = []
    for section in sections:
        score = _score_section(section, keywords)
        if score <= 0:
            continue
        top_level = section["number"].split(".")[0]
        scored.append((section, score, top_level))

    scored = [item for item in scored if item[1] > 0 and item[2] in {"2", "3", "4", "5"}]
    if not scored:
        return ""

    scored.sort(
        key=lambda item: (
            -item[1],
            [int(part) for part in item[0]["number"].split(".")],
        )
    )

    lines = []
    for section, score, _ in scored[:limit]:
        summary = _summarise(section["content"], max_words=45)
        number = section["number"]
        title = section["title"].strip()
        lines.append(f"- Section {number} {title}: {summary}")

    return "\n".join(lines)


def build_lubrication_installation_context() -> str:
    keywords = [
        "lubrication",
        "installation",
        "preload",
        "clearance",
        "mounting",
        "static load",
        "dynamic load",
        "torque",
        "temperature",
        "maintenance",
    ]
    return build_manual_context(keywords=[kw.lower() for kw in keywords], limit=4)


@lru_cache(maxsize=1)
def _load_case_studies() -> list[dict]:
    """Load case studies from JSON file."""
    if not CASE_STUDIES_PATH.exists():
        return []
    try:
        return json.loads(CASE_STUDIES_PATH.read_text())
    except json.JSONDecodeError:
        return []


def build_case_study_context(*, limit: int = 2) -> str:
    """Build context from real case studies for Real Application Stories pillar.

    Returns formatted case study summaries with real metrics to ground the LLM
    and prevent hallucinated numbers.
    """
    case_studies = _load_case_studies()
    if not case_studies:
        return ""

    # Randomly select case studies to provide variety
    selected = random.sample(case_studies, min(limit, len(case_studies)))

    lines = []
    for case in selected:
        results = case.get("results", {})
        results_str = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in results.items())

        summary = (
            f"- {case['title']} ({case['industry']}, {case['region']}): "
            f"{case['challenge']} → {case['solution']}. "
            f"Results: {results_str}. "
            f"Customer feedback: \"{case.get('quote', '')}\""
        )
        lines.append(summary)

    return "\n".join(lines)


__all__ = ["build_lubrication_installation_context", "build_case_study_context"]
