"""The OpenRouter route: a stopgap that reaches the same Claude model through
OpenRouter's Anthropic-format endpoint while the Anthropic account has no credit."""

from __future__ import annotations

import json

import httpx2
import pytest

from news import claude
from news.compare_models import cost
from news.config import load_config

from .conftest import FakeClaude, FixtureFetcher
from .test_pipeline import _run

ITEMS = [{"id": f"u:{k:016x}", "source": "S", "title": f"Item {k}", "source_text": "text"} for k in range(3)]


def test_route_choice(monkeypatch):
    assert claude.api_route() is None and not claude.enabled()
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    assert claude.api_route() == "openrouter" and claude.enabled()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # how GitHub Actions passes a missing secret
    assert claude.api_route() == "openrouter"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert claude.api_route() == "anthropic"  # Anthropic wins when both are set
    monkeypatch.setenv("USE_CLAUDE", "false")
    assert not claude.enabled()
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("OPENROUTER_API_KEY", "  ")
    assert claude.api_route() is None


def test_model_names(monkeypatch):
    assert claude.openrouter_model("claude-haiku-5-5") == "anthropic/claude-haiku-5.5"
    assert claude.openrouter_model("claude-sonnet-5-5") == "anthropic/claude-sonnet-5.5"
    assert claude.openrouter_model("anthropic/claude-opus-5.5") == "anthropic/claude-opus-5.5"
    settings = load_config().claude
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert claude.model_name(settings) == "claude-sonnet-5-5"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    assert claude.model_name(settings) == "anthropic/claude-sonnet-5.5"
    # The model comparison prices OpenRouter's names like Anthropic's.
    assert cost("anthropic/claude-haiku-5.5", 1_000_000, 1_000_000) == pytest.approx(0.60)


def test_openrouter_client_ignores_an_empty_anthropic_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", " or-test \n")
    client = claude.make_client("openrouter")
    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api"
    assert client.auth_token == "or-test" and client.api_key is None


@pytest.mark.parametrize("configured", [None, "claude-sonnet-5-5"])
def test_real_sdk_request_on_the_openrouter_route(monkeypatch, configured):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    if configured:
        monkeypatch.setenv("CLAUDE_MODEL", configured)
    sent = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        body = json.loads(request.content)
        reply = {"items": [{"id": i["id"], "relevant": True, "type": "paper", "score": 3, "summary": "ok"}
                           for i in map(json.loads, body["messages"][0]["content"].splitlines()[1:-1])]}
        return httpx2.Response(200, json={
            "id": "gen-test", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(reply)}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

    client = claude.make_client("openrouter", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    r = claude.judge(ITEMS, load_config().claude, client=client)

    assert len(sent) == 1
    req, body = sent[0], json.loads(sent[0].content)
    assert str(req.url) == "https://openrouter.ai/api/v1/messages"
    assert req.headers["authorization"] == "Bearer or-test" and "x-api-key" not in req.headers
    assert body["model"] == ("anthropic/claude-sonnet-5.5" if configured else "anthropic/claude-haiku-5.5")
    assert body["output_config"]["format"]["type"] == "json_schema" and body["output_config"]["effort"] == "low"
    # The server-side fallback is an Anthropic beta; it is never sent to OpenRouter.
    assert "fallbacks" not in body and "fallback" not in req.headers.get("anthropic-beta", "")
    assert r.route == "openrouter" and set(r.judgements) == {i["id"] for i in ITEMS} and not r.errors


def test_meta_records_the_route(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    meta = _run(tmp_path, FixtureFetcher(), use_claude=True, claude_client=FakeClaude())
    assert meta["claude"]["route"] == "openrouter"
    assert meta["claude"]["model"] == "anthropic/claude-haiku-5.5"
