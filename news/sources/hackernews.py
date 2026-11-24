"""Hacker News search through the Algolia API, one query per keyword.

With `match: any`, a single query instead lists every story with at least `min_points`
points, whatever its words; such a source sets `needs_claude`, so Claude decides.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from news.config import Source
from news.keywords import Keyword, matched
from news.net import FetchError
from news.sources import FetchContext, SourceResult
from news.util import make_item, strip_html

API_URL = "https://hn.algolia.com/api/v1/search_by_date"


def fetch(source: Source, ctx: FetchContext) -> SourceResult:
    lookback = int(source.param("lookback_hours", 48))
    since = int((ctx.now - timedelta(hours=lookback)).timestamp())
    if source.param("match") == "any":
        min_points = int(source.param("min_points", 5))
        params = {"tags": "story", "numericFilters": f"created_at_i>{since},points>={min_points}", "hitsPerPage": 200}
        resp = ctx.http.get(API_URL, params=params, conditional=False)
        return SourceResult(items=select(source, _hits(resp.content), ctx.keywords, ctx.now), http_status=resp.status)
    hits: dict[str, dict] = {}
    status = None
    errors = []
    skipped = 0
    for kw in ctx.keywords:
        if ctx.out_of_time():
            skipped += 1
            continue
        params = {
            "query": kw.phrase,
            "tags": "story",
            "numericFilters": f"created_at_i>{since}",
            "hitsPerPage": 50,
        }
        try:
            resp = ctx.http.get(API_URL, params=params, conditional=False)
        except FetchError as e:
            errors.append(f"{kw.phrase}: {e}")
            continue
        status = resp.status
        for h in _hits(resp.content):
            hits[str(h.get("objectID"))] = h
    if status is None:
        raise FetchError("every Hacker News query failed: " + "; ".join(errors[:3]))
    items = select(source, list(hits.values()), ctx.keywords, ctx.now)
    warnings = [f"{skipped} keyword searches skipped (time budget)"] if skipped else []
    if errors:
        warnings.append(f"{len(errors)} keyword searches failed, first: {errors[0]}"[:200])
    return SourceResult(items=items, http_status=status, warnings=warnings)


def _hits(content: bytes) -> list[dict]:
    try:
        data = json.loads(content)
    except ValueError as e:
        raise FetchError(f"Hacker News response is not JSON: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
        raise FetchError("Hacker News response has no hits list")
    return data["hits"]


def select(source: Source, hits: list[dict], keywords: list[Keyword], now: datetime) -> list[dict[str, Any]]:
    """Keep stories whose title or link matches a keyword (any story with `match: any`) and
    that have enough points.

    Stories below the threshold are left out entirely (not marked seen), so a
    story that gains points later can still qualify on a later run.
    """
    min_points = int(source.param("min_points", 5))
    any_story = source.param("match") == "any"
    since = now - timedelta(hours=int(source.param("lookback_hours", 48)))
    items = []
    for h in hits:
        title = h.get("title") or ""
        url = h.get("url") or ""
        if not title or not (any_story or matched(f"{title} {url}", keywords)):
            continue
        if int(h.get("points") or 0) < min_points:
            continue
        hn_url = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        created = h.get("created_at_i")
        published = datetime.fromtimestamp(created, tz=timezone.utc) if created else None
        if published is not None and published < since:
            continue  # the search asks for the same window; this also holds for a saved reply
        story = strip_html(h.get("story_text") or "")
        text = story or f"Discussed on Hacker News: {h.get('points', 0)} points, {h.get('num_comments') or 0} comments. {hn_url}"
        if url:
            # Claude sees the title and this text, not the link; a title such as "VP ≠ VNP over
            # Every Field" says little until it reads github.com/anthropics/formal-math/...
            text = f"Link: {url}. {text}"
        items.append(
            make_item(
                source_id=source.id,
                source_name=source.name,
                type_=source.type,
                title=title,
                url=url or hn_url,
                published=published,
                fetched=now,
                authors=[h["author"]] if h.get("author") else [],
                text=text,
            )
        )
    return items


def parse(source: Source, content: bytes, now: datetime, keywords: list[Keyword]) -> list[dict[str, Any]]:
    return select(source, _hits(content), keywords, now)
