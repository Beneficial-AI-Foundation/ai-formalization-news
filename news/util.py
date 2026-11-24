"""Small helpers for time, text and items."""

from __future__ import annotations

import calendar
import html
import re
import time
from datetime import datetime, timezone
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    d = dt.astimezone(timezone.utc)
    # strftime("%Y") does not pad years below 1000 ("1-01-01"); some feeds date pages 1 Jan 0001.
    return f"{d.year:04d}-{d:%m-%dT%H:%M:%S}Z"


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def from_struct_time(st: time.struct_time | None) -> datetime | None:
    """feedparser gives UTC struct_time values; turn one into an aware datetime."""
    if not st:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
    except (OverflowError, ValueError):
        return None


def strip_html(text: str | None) -> str:
    """Remove tags, decode entities and collapse whitespace."""
    if not text:
        return ""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", " ", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def clean_line(text: str | None) -> str:
    return _WS_RE.sub(" ", html.unescape(text or "")).strip()


def truncate(text: str, limit: int) -> str:
    """Cut text to at most `limit` characters, on a word boundary when possible."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


def make_item(
    *,
    source_id: str,
    source_name: str,
    type_: str,
    title: str,
    url: str,
    published: datetime | None,
    fetched: datetime,
    authors: list[str] | None = None,
    text: str = "",
    text_limit: int = 600,
) -> dict[str, Any]:
    """Build an item in the common schema. `id` is filled in later by dedup."""
    pub = published or fetched
    if pub > fetched:  # a feed dated in the future would pin itself to the top
        pub = fetched
    return {
        "id": "",
        "type": type_,
        "source": source_name,
        "source_id": source_id,
        "title": clean_line(title)[:500] or "(untitled)",
        "url": url,
        "authors": [clean_line(a) for a in (authors or []) if clean_line(a)][:20],
        "published": iso(pub),
        "fetched": iso(fetched),
        "source_text": truncate(text, text_limit) if text else "",
        "summary": None,
        "score": None,
        "relevant": None,
    }
