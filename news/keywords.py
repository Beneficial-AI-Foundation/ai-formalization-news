"""Keyword matching for the local filter and the Hacker News search."""

from __future__ import annotations

import re
from dataclasses import dataclass


def normalize(text: str) -> str:
    """Lower-case, collapse whitespace, and fold British "-isation" into "-ization"."""
    return re.sub(r"isation", "ization", " ".join(text.lower().split()))


@dataclass(frozen=True)
class Keyword:
    phrase: str
    pattern: re.Pattern[str]


def compile_keywords(phrases: list[str]) -> list[Keyword]:
    """Build one regex per phrase.

    Words may be separated by one space, one hyphen or nothing ("Lean 4",
    "Lean-4", "Lean4"); a plural ending is allowed; the match must start and end on a
    word boundary, so "Coq" does not match "Coquand".
    """
    out = []
    for phrase in phrases:
        words = [w for w in re.split(r"[\s\-]+", normalize(phrase)) if w]
        if not words:
            continue
        body = r"[\s\-]?".join(re.escape(w) for w in words)
        pattern = re.compile(r"(?<![a-z0-9])" + body + r"(?:s|es)?(?![a-z0-9])")
        out.append(Keyword(phrase, pattern))
    return out


def matched(text: str, keywords: list[Keyword]) -> list[str]:
    norm = normalize(text)
    return [k.phrase for k in keywords if k.pattern.search(norm)]


def item_matches(item: dict, keywords: list[Keyword]) -> bool:
    text = " ".join([item.get("title", ""), item.get("source_text", ""), item.get("url", "")])
    return bool(matched(text, keywords))
