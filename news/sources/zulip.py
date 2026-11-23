"""Lean Zulip announcements (optional). Needs ZULIP_EMAIL and ZULIP_API_KEY."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from news.config import Source
from news.net import FetchError
from news.sources import FetchContext, SourceResult
from news.util import make_item, strip_html


def fetch(source: Source, ctx: FetchContext) -> SourceResult:
    email = os.environ.get("ZULIP_EMAIL")
    key = os.environ.get("ZULIP_API_KEY")
    if not email or not key:
        raise FetchError("ZULIP_EMAIL and ZULIP_API_KEY are not set")
    site = source.param("site", "https://leanprover.zulipchat.com").rstrip("/")
    params = {
        "anchor": "newest",
        "num_before": 50,
        "num_after": 0,
        "apply_markdown": "false",
        "narrow": json.dumps([{"operator": "channel", "operand": source.param("channel", "announce")}]),
    }
    resp = ctx.http.get(f"{site}/api/v1/messages", params=params, conditional=False, auth=(email, key))
    return SourceResult(items=parse(source, resp.content, ctx.now), http_status=resp.status)


def _zulip_hash(text: str) -> str:
    """Zulip's narrow URLs percent-encode and then use '.' in place of '%'."""
    return quote(text, safe="").replace(".", "%2E").replace("%", ".")


def parse(source: Source, content: bytes, now: datetime) -> list[dict[str, Any]]:
    try:
        data = json.loads(content)
    except ValueError as e:
        raise FetchError(f"Zulip response is not JSON: {e}") from e
    if data.get("result") != "success":
        raise FetchError(f"Zulip error: {data.get('msg')}")
    site = source.param("site", "https://leanprover.zulipchat.com").rstrip("/")
    # One item per topic: the first message of each topic in the window.
    first: dict[str, dict] = {}
    for m in sorted(data.get("messages") or [], key=lambda m: m.get("id", 0)):
        first.setdefault(m.get("subject") or "", m)
    items = []
    for topic, m in first.items():
        stream = f"{m.get('stream_id')}-{_zulip_hash(str(m.get('display_recipient') or 'announce'))}"
        url = f"{site}/#narrow/channel/{stream}/topic/{_zulip_hash(topic)}/near/{m.get('id')}"
        ts = m.get("timestamp")
        items.append(
            make_item(
                source_id=source.id,
                source_name=source.name,
                type_=source.type,
                title=topic or strip_html(m.get("content"))[:120],
                url=url,
                published=datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
                fetched=now,
                authors=[m["sender_full_name"]] if m.get("sender_full_name") else [],
                text=strip_html(m.get("content")),
            )
        )
    return items
