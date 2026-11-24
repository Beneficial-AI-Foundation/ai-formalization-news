"""Shared test helpers. Every test runs offline: opening a socket fails."""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from news.config import load_config
from news.net import FetchError
from news.sources import (SourceResult, arxiv, arxiv_rss, feed, github_releases, github_repos, hackernews, hf_papers,
                          zulip)

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 23, 3, 17, tzinfo=timezone.utc)

PARSERS = {
    "feed": feed.parse,
    "arxiv": arxiv.parse,
    "arxiv_rss": arxiv_rss.parse,
    "github_releases": github_releases.parse,
    "github_repos": github_repos.parse,
    "hf_papers": hf_papers.parse,
    "zulip": zulip.parse,
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def guard(*args, **kwargs):
        raise RuntimeError("tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


@pytest.fixture(autouse=True)
def no_api_keys(monkeypatch):
    """Keys and switches from the developer's shell must not change which route or
    model a test sees; tests that need them set them."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "OPENROUTER_API_KEY",
                 "CLAUDE_MODEL", "USE_CLAUDE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def cfg():
    return load_config()


def fixture_path(source_id: str) -> Path:
    for ext in ("xml", "json"):
        p = FIXTURES / f"{source_id}.{ext}"
        if p.exists():
            return p
    raise FileNotFoundError(source_id)


def parse_fixture(source, content: bytes, now: datetime, keywords) -> list[dict[str, Any]]:
    if source.kind == "hackernews":
        return hackernews.parse(source, content, now, keywords)
    return PARSERS[source.kind](source, content, now)


class FixtureFetcher:
    """Stands in for the network: each source returns its saved fixture.

    `overrides` maps a source id to bytes (for example a broken response).
    `calls` counts fetches per source.
    """

    def __init__(self, overrides: dict[str, bytes] | None = None):
        self.overrides = overrides or {}
        self.calls: dict[str, int] = {}

    def __call__(self, kind: str):
        def fetch(source, ctx):
            self.calls[source.id] = self.calls.get(source.id, 0) + 1
            content = self.overrides.get(source.id)
            if content is None:
                content = fixture_path(source.id).read_bytes()
            try:
                items = parse_fixture(source, content, ctx.now, ctx.keywords)
            except FetchError:
                raise
            return SourceResult(items=items, http_status=200)

        return fetch


class FakeClaude:
    """Mimics client.messages.create / client.beta.messages.create with a JSON schema.

    `script` is a list of callables, one per request, each taking the list of
    item dicts sent and returning (stop_reason, list of judgement dicts or None).
    None rows give the text a real reply would have: prose for a refusal, JSON
    cut off for max_tokens. When the script runs out, every item is judged relevant.
    """

    def __init__(self, script=None, error: Exception | None = None):
        self.script = list(script or [])
        self.error = error
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        import json

        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        body = kwargs["messages"][0]["content"]
        lines = [json.loads(line) for line in body.splitlines() if line.startswith("{")]
        step = self.script.pop(0) if self.script else None
        if step is None:
            stop, rows = "end_turn", [
                {"id": x["id"], "relevant": True, "type": "paper", "score": 3, "summary": f"Summary of {x['title']}"}
                for x in lines
            ]
        else:
            stop, rows = step(lines)
        if rows is not None:
            text = json.dumps({"items": rows})
        elif stop == "refusal":
            text = "I can't help with classifying this content."
        else:
            text = '{"items": [{"id": "'
        details = SimpleNamespace(category="cyber", explanation="test") if stop == "refusal" else None
        return SimpleNamespace(
            stop_reason=stop,
            stop_details=details,
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=100 * len(lines), output_tokens=30 * len(lines)),
        )
