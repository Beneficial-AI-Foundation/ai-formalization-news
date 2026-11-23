"""arXiv API search (Atom). One query, paged at most once, 3 seconds between calls."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from news.config import Source
from news.dedup import arxiv_id_from_url
from news.sources import FetchContext, SourceResult
from news.sources.feed import entry_authors, entry_published, parse_feed
from news.util import make_item, parse_iso, strip_html

API_URL = "https://export.arxiv.org/api/query"
PAGE_SIZE = 100
PAUSE_SECONDS = 3.0


def fetch(source: Source, ctx: FetchContext) -> SourceResult:
    query = " ".join(str(source.param("query", "")).split())
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": PAGE_SIZE,
        "start": 0,
    }
    timeout = source.param("timeout")
    resp = ctx.http.get(API_URL, params=params, conditional=False, timeout=timeout)
    items = parse(source, resp.content, ctx.now)
    warnings = []
    # `published` is the submission time, not when a paper became visible, so it
    # cannot serve as a watermark. Fetch the one permitted extra page whenever the
    # first page is full and still inside the age window; dedup drops repeats.
    window = ctx.now - timedelta(days=int(source.param("max_age_days", 30)))
    if len(items) == PAGE_SIZE and min(parse_iso(i["published"]) for i in items) > window and not ctx.out_of_time():
        time.sleep(PAUSE_SECONDS)
        resp2 = ctx.http.get(API_URL, params={**params, "start": PAGE_SIZE}, conditional=False, timeout=timeout)
        page2 = parse(source, resp2.content, ctx.now)
        items += page2
        if len(page2) == PAGE_SIZE and min(parse_iso(i["published"]) for i in page2) > window:
            warnings.append("both pages full and recent; older papers may have been missed")
    return SourceResult(items=items, http_status=resp.status, warnings=warnings)


def parse(source: Source, content: bytes, now: datetime) -> list[dict[str, Any]]:
    d = parse_feed(content)
    items = []
    for e in d.entries:
        aid = arxiv_id_from_url(e.get("id", "")) or arxiv_id_from_url(e.get("link", ""))
        if not aid:
            continue
        primary = (e.get("arxiv_primary_category") or {}).get("term")
        items.append(
            make_item(
                source_id=source.id,
                source_name=f"arXiv {primary}" if primary else source.name,
                type_=source.type,
                title=e.get("title", ""),
                url=f"https://arxiv.org/abs/{aid}",
                published=entry_published(e),
                fetched=now,
                authors=entry_authors(e),
                text=strip_html(e.get("summary", "")),
            )
        )
    return items
