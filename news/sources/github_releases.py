"""GitHub releases through the REST API (Lean 4, Agda, Rocq)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from news.config import Source
from news.net import FetchError, github_headers
from news.sources import FetchContext, SourceResult
from news.util import make_item, parse_iso, strip_html


def api_url(repo: str) -> str:
    return f"https://api.github.com/repos/{repo}/releases?per_page=10"


def fetch(source: Source, ctx: FetchContext) -> SourceResult:
    resp = ctx.http.get(api_url(source.param("repo")), headers=github_headers(), conditional=ctx.conditional)
    if resp.not_modified:
        return SourceResult(items=[], http_status=304, not_modified=True)
    return SourceResult(items=parse(source, resp.content, ctx.now), http_status=resp.status)


def parse(source: Source, content: bytes, now: datetime) -> list[dict[str, Any]]:
    try:
        releases = json.loads(content)
    except ValueError as e:
        raise FetchError(f"releases response is not JSON: {e}") from e
    if not isinstance(releases, list):
        raise FetchError(f"unexpected releases response: {str(releases)[:200]}")
    label = source.param("label") or source.param("repo")
    items = []
    for r in releases:
        if r.get("draft") or not r.get("html_url"):
            continue
        tag = r.get("tag_name") or ""
        name = (r.get("name") or "").strip() or tag
        title = f"{label} {name}" if label.lower() not in name.lower() else name
        if r.get("prerelease"):
            title += " (pre-release)"
        author = (r.get("author") or {}).get("login")
        items.append(
            make_item(
                source_id=source.id,
                source_name=source.name,
                type_=source.type,
                title=title,
                url=r["html_url"],
                published=parse_iso(r.get("published_at") or r.get("created_at")),
                fetched=now,
                authors=[author] if author else [],
                text=strip_html(r.get("body") or "").replace("#", "").strip(),
            )
        )
    return items
