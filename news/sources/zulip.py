"""Lean Zulip (optional): one item per new topic in a list of channels. Needs ZULIP_EMAIL
and ZULIP_API_KEY, the address and key of a Zulip bot.

Announcement channels keep every new topic. Discussion channels set `min_messages` and
`min_people`, so only busy topics count, like the points threshold on Hacker News: a topic
below them is left out (not marked seen) and can qualify on a later run.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote

from news.config import Source
from news.net import FetchError
from news.sources import FetchContext, SourceResult
from news.util import make_item, strip_html

DEFAULT_SITE = "https://leanprover.zulipchat.com"

# Given a channel and a topic, return the topic's first message, or None if it has none.
FirstMessage = Callable[[str, str], "dict[str, Any] | None"]


def channels_of(source: Source) -> list[str]:
    return [str(c) for c in (source.param("channels") or [source.param("channel", "General announcements")])]


def fetch(source: Source, ctx: FetchContext) -> SourceResult:
    email = os.environ.get("ZULIP_EMAIL")
    key = os.environ.get("ZULIP_API_KEY")
    if not email or not key:
        raise FetchError("ZULIP_EMAIL and ZULIP_API_KEY are not set")
    api = source.param("site", DEFAULT_SITE).rstrip("/") + "/api/v1/messages"

    def get(narrow: list[dict[str, str]], **params: Any) -> dict[str, Any]:
        params.update(apply_markdown="false", narrow=json.dumps(narrow))
        resp = ctx.http.get(api, params=params, conditional=False, auth=(email, key))
        return _reply(resp.content)

    def first_message(channel: str, topic: str) -> dict[str, Any] | None:
        narrow = [{"operator": "channel", "operand": channel}, {"operator": "topic", "operand": topic}]
        msgs = get(narrow, anchor="oldest", num_before=0, num_after=1).get("messages") or []
        return msgs[0] if msgs else None

    replies: dict[str, dict[str, Any]] = {}
    errors = []
    for channel in channels_of(source):
        try:
            replies[channel] = get([{"operator": "channel", "operand": channel}], anchor="newest",
                                   num_before=int(source.param("messages", 1000)), num_after=0)
        except FetchError as e:  # for example a renamed channel: report it, keep the others
            errors.append(f"{channel}: {e}")
    if not replies:
        raise FetchError("every Zulip channel failed: " + "; ".join(errors[:3]))
    items = select(source, replies, ctx.now, first_message)
    warnings = [f"{len(errors)} channel(s) failed, first: {errors[0]}"[:200]] if errors else []
    return SourceResult(items=items, http_status=200, warnings=warnings)


def _reply(content: bytes) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except ValueError as e:
        raise FetchError(f"Zulip response is not JSON: {e}") from e
    if not isinstance(data, dict) or data.get("result") != "success":
        raise FetchError(f"Zulip error: {data.get('msg') if isinstance(data, dict) else data!r}")
    return data


def _zulip_hash(text: str) -> str:
    """Zulip's narrow URLs percent-encode and then use '.' in place of '%'."""
    return quote(text, safe="").replace(".", "%2E").replace("%", ".")


def _text(message: dict[str, Any]) -> str:
    """Message text without HTML, with mentions (@**Name**, @_**Name|12**) reduced to the name."""
    return re.sub(r"@_?\*\*([^*|]+)(?:\|\d+)?\*\*", r"\1", strip_html(message.get("content")))


def select(source: Source, replies: dict[str, dict[str, Any]], now: datetime,
           first_message: FirstMessage | None = None) -> list[dict[str, Any]]:
    """One item per topic that started within `lookback_hours` and has at least
    `min_messages` messages from `min_people` people in the window.

    Each reply holds a channel's newest messages, so a long-quiet topic with a new reply
    looks new. `first_message` looks up the real start of each topic that qualifies; the
    saved test replies have none and are taken as they are.
    """
    site = source.param("site", DEFAULT_SITE).rstrip("/")
    min_messages = int(source.param("min_messages", 1))
    min_people = int(source.param("min_people", 1))
    lookback = source.param("lookback_hours")
    since = (now - timedelta(hours=float(lookback))).timestamp() if lookback else None
    busy_only = min_messages > 1 or min_people > 1
    items = []
    for channel, data in replies.items():
        topics: dict[str, list[dict[str, Any]]] = {}
        for m in sorted(data.get("messages") or [], key=lambda m: m.get("id", 0)):
            topics.setdefault(m.get("subject") or "", []).append(m)
        for topic, msgs in topics.items():
            first = msgs[0]
            if since is not None and (first.get("timestamp") or 0) < since:
                continue
            people = len({m.get("sender_id") or m.get("sender_full_name") for m in msgs})
            if len(msgs) < min_messages or people < min_people:
                continue
            if first_message is not None:
                real = first_message(channel, topic)
                if real is None or real.get("id") != first.get("id"):
                    if real is None or (since is not None and (real.get("timestamp") or 0) < since):
                        continue  # an older topic with new replies, not a new one
                    first = real
            text = _text(first)
            if busy_only:
                text = f"Discussed on the Lean Zulip in #{channel}: {len(msgs)} messages from {people} people. {text}"
            stream = f"{first.get('stream_id')}-{_zulip_hash(str(first.get('display_recipient') or channel))}"
            ts = first.get("timestamp")
            items.append(
                make_item(
                    source_id=source.id,
                    source_name=source.name,
                    type_=source.type,
                    title=topic or _text(first)[:120],
                    url=f"{site}/#narrow/channel/{stream}/topic/{_zulip_hash(topic)}/near/{first.get('id')}",
                    published=datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
                    fetched=now,
                    authors=[first["sender_full_name"]] if first.get("sender_full_name") else [],
                    text=text,
                )
            )
    return items


def parse(source: Source, content: bytes, now: datetime) -> list[dict[str, Any]]:
    """Parse saved replies: a JSON object mapping each channel to its `GET /messages` reply,
    or a single reply for a source with one channel."""
    try:
        data = json.loads(content)
    except ValueError as e:
        raise FetchError(f"Zulip response is not JSON: {e}") from e
    if isinstance(data, dict) and "result" in data:
        data = {channels_of(source)[0]: data}
    if not isinstance(data, dict):
        raise FetchError("Zulip fixture is not a JSON object")
    return select(source, {ch: _reply(json.dumps(r).encode()) for ch, r in data.items()}, now)
