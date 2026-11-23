"""arXiv RSS (rss.arxiv.org): one feed for several categories joined with '+'."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from news.config import Source
from news.sources import FetchContext, SourceResult
from news.dedup import arxiv_id_from_url
from news.sources.feed import entry_published, parse_feed
from news.util import make_item, strip_html

KEEP_ANNOUNCE_TYPES = {"new", "cross"}  # "replace" and "replace-cross" are revisions of older papers

_ABSTRACT_RE = re.compile(r"^.*?Abstract:\s*", re.S)


def fetch(source: Source, ctx: FetchContext) -> SourceResult:
    resp = ctx.http.get(source.url, conditional=ctx.conditional)
    if resp.not_modified:
        return SourceResult(items=[], http_status=304, not_modified=True)
    return SourceResult(items=parse(source, resp.content, ctx.now), http_status=resp.status)


def parse(source: Source, content: bytes, now: datetime) -> list[dict[str, Any]]:
    d = parse_feed(content)
    items = []
    for e in d.entries:
        announce = (e.get("arxiv_announce_type") or "new").strip().lower()
        if announce not in KEEP_ANNOUNCE_TYPES:
            continue
        aid = arxiv_id_from_url(e.get("link", ""))
        if not aid:
            continue
        creators = e.get("author") or e.get("dc_creator") or ""
        # "A, B and C" and "A, B, and C" both give three names.
        authors = [a.strip() for a in re.split(r",\s*(?:and\s+)?|\s+and\s+", creators) if a.strip()]
        category = next((t.get("term") for t in e.get("tags") or [] if t.get("term")), None)
        abstract = _ABSTRACT_RE.sub("", strip_html(e.get("summary", "")))
        items.append(
            make_item(
                source_id=source.id,
                source_name=f"arXiv {category}" if category else source.name,
                type_=source.type,
                title=e.get("title", ""),
                url=f"https://arxiv.org/abs/{aid}",
                published=entry_published(e),
                fetched=now,
                authors=authors,
                text=abstract,
            )
        )
    return items
