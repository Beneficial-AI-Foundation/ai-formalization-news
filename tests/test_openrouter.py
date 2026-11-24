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


# ---------- Falling back to OpenRouter when Anthropic fails (decision D41) ----------

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CREDIT = "Your credit balance is too low to access the Anthropic API."


def _no_credit():
    import anthropic

    return anthropic.BadRequestError(
        message=CREDIT, response=httpx2.Response(400, request=httpx2.Request("POST", ANTHROPIC_URL)),
        body={"type": "error", "error": {"type": "invalid_request_error", "message": CREDIT}})


def test_anthropic_failure_falls_back_to_openrouter(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    primary, backup = FakeClaude(error=_no_credit()), FakeClaude()
    r = claude.judge(ITEMS, load_config().claude, client=primary, fallback_client=backup)
    assert set(r.judgements) == {i["id"] for i in ITEMS}
    assert r.fallback and r.route == "openrouter" and r.model == "anthropic/claude-haiku-5.5"
    assert len(primary.requests) == 1 and backup.requests[0]["model"] == "anthropic/claude-haiku-5.5"
    assert any("Anthropic failed" in e and "credit balance is too low" in e for e in r.errors)
    assert r.usage()["fallback"] is True


def test_the_run_stays_on_openrouter_after_falling_back(monkeypatch):
    """Anthropic is tried once per run: later requests (here, one per item after a refusal)
    go to OpenRouter directly."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    primary = FakeClaude(error=_no_credit())
    backup = FakeClaude([lambda lines: ("refusal", None)])  # the whole batch refused, then one at a time
    r = claude.judge(ITEMS, load_config().claude, client=primary, fallback_client=backup)
    assert len(primary.requests) == 1 and len(backup.requests) == 1 + len(ITEMS)
    assert set(r.judgements) == {i["id"] for i in ITEMS}


@pytest.mark.parametrize("anthropic_key,openrouter_key", [("sk-test", ""), ("", "or-test")])
def test_no_fallback_without_a_second_route(monkeypatch, anthropic_key, openrouter_key):
    monkeypatch.setenv("ANTHROPIC_API_KEY", anthropic_key)
    monkeypatch.setenv("OPENROUTER_API_KEY", openrouter_key)
    backup = FakeClaude()
    r = claude.judge(ITEMS, load_config().claude, client=FakeClaude(error=_no_credit()), fallback_client=backup)
    assert not r.judgements and not r.fallback and backup.requests == []
    assert any(e.startswith("BadRequestError") for e in r.errors)


def test_fallback_through_the_real_sdk(monkeypatch):
    """The SDK raises its own error for an HTTP 400 from Anthropic, and the retry goes to
    OpenRouter's endpoint with the OpenRouter key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    sent = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        if request.url.host == "api.anthropic.com":
            return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": CREDIT}})
        body = json.loads(request.content)
        reply = {"items": [{"id": i["id"], "relevant": True, "type": "paper", "score": 3, "summary": "ok"}
                           for i in map(json.loads, body["messages"][0]["content"].splitlines()[1:-1])]}
        return httpx2.Response(200, json={
            "id": "gen-test", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(reply)}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

    mock = dict(max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    r = claude.judge(ITEMS, load_config().claude, client=claude.make_client("anthropic", **mock),
                     fallback_client=claude.make_client("openrouter", **mock))
    assert [str(q.url) for q in sent] == [ANTHROPIC_URL, "https://openrouter.ai/api/v1/messages"]
    assert sent[1].headers["authorization"] == "Bearer or-test"
    assert r.fallback and set(r.judgements) == {i["id"] for i in ITEMS}


def test_meta_records_the_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setattr(claude, "make_client", lambda route, **kw: FakeClaude())  # the OpenRouter client
    meta = _run(tmp_path, FixtureFetcher(), use_claude=True, claude_client=FakeClaude(error=_no_credit()))
    assert meta["claude"]["fallback"] is True and meta["claude"]["route"] == "openrouter"
    assert meta["claude"]["judged"] > 0
    # The Anthropic failure stays visible in the run's errors.
    assert any(e["source"] == "claude" and "Anthropic failed" in e["error"] for e in meta["errors"])
