"""Hugging Face Daily Papers (JSON). Papers are arXiv papers, so links go to arXiv."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from news.config import Source
from news.net import FetchError
from news.sources import FetchContext, SourceResult
from news.util import make_item, parse_iso, strip_html

_ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}$")


def fetch(source: Source, ctx: FetchContext) -> SourceResult:
    resp = ctx.http.get(source.url, conditional=ctx.conditional)
    if resp.not_modified:
        return SourceResult(items=[], http_status=304, not_modified=True)
    return SourceResult(items=parse(source, resp.content, ctx.now), http_status=resp.status)


def parse(source: Source, content: bytes, now: datetime) -> list[dict[str, Any]]:
    try:
        data = json.loads(content)
    except ValueError as e:
        raise FetchError(f"daily papers response is not JSON: {e}") from e
    if not isinstance(data, list):
        raise FetchError("daily papers response is not a list")
    items = []
    for row in data:
        paper = row.get("paper") or {}
        pid = str(paper.get("id") or "")
        if not pid:
            continue
        url = f"https://arxiv.org/abs/{pid}" if _ARXIV_ID.match(pid) else f"https://huggingface.co/papers/{pid}"
        authors = [a.get("name", "") for a in paper.get("authors") or [] if not a.get("hidden")]
        upvotes = paper.get("upvotes")
        text = strip_html(paper.get("summary") or row.get("summary") or "")
        if upvotes:
            text = f"{upvotes} upvotes on Hugging Face. {text}"
        items.append(
            make_item(
                source_id=source.id,
                source_name=source.name,
                type_=source.type,
                title=paper.get("title") or row.get("title") or "",
                url=url,
                published=parse_iso(row.get("publishedAt") or paper.get("submittedOnDailyAt") or paper.get("publishedAt")),
                fetched=now,
                authors=authors,
                text=text,
            )
        )
    return items
