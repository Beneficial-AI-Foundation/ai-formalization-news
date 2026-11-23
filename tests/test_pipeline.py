"""The whole run on fixtures: idempotence, failing sources, schedule, and the Claude step."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from news.config import load_config
from news.run import run
from news.store import Validator

from .conftest import NOW, FakeClaude, FixtureFetcher


def _run(tmp_path, fetcher, **kw):
    cfg = kw.pop("cfg", None) or load_config()
    return run(cfg, data_dir=tmp_path / "data", fetcher=fetcher, now=kw.pop("now", NOW),
               use_claude=kw.pop("use_claude", False), synonyms=[["coq", "rocq"]], log=lambda s: None, **kw)


def _news(tmp_path):
    return json.loads((tmp_path / "data" / "news.json").read_text())["items"]


def test_two_runs_add_nothing_the_second_time(tmp_path):
    first = _run(tmp_path, FixtureFetcher())
    assert first["counts"]["new_kept"] > 20
    assert {"news.json", "search.json", "seen.json", "feed.xml"} <= set(first["_changed"])
    snapshot = {p.name: p.read_bytes() for p in (tmp_path / "data").rglob("*.json") if p.name != "meta.json"}
    second = _run(tmp_path, FixtureFetcher(), ignore_schedule=True, now=NOW + timedelta(minutes=5))
    assert second["counts"]["new_kept"] == 0
    assert second["_changed"] == []
    after = {p.name: p.read_bytes() for p in (tmp_path / "data").rglob("*.json") if p.name != "meta.json"}
    snapshot.pop("http_cache.json"), after.pop("http_cache.json")
    assert after == snapshot


def test_outputs_are_valid(tmp_path):
    _run(tmp_path, FixtureFetcher())
    v = Validator()
    data = tmp_path / "data"
    v.check(json.loads((data / "news.json").read_text()), "news")
    for f in (data / "archive").glob("*.json"):
        v.check(json.loads(f.read_text()), f.name)
    search = json.loads((data / "search.json").read_text())
    assert len(search["rows"]) == len(_news(tmp_path))
    meta = json.loads((data / "meta.json").read_text())
    assert meta["last_run"] == "2026-11-23T03:17:00Z"
    assert {s["id"] for s in meta["sources"]} == {s.id for s in load_config().enabled_sources}
    items = _news(tmp_path)
    assert items == sorted(items, key=lambda i: (i["published"], i["id"]), reverse=True)
    assert all(i["summary"] is None and i["relevant"] is None for i in items)  # no Claude


def test_keyword_filter_applies_only_to_keyword_sources(tmp_path):
    _run(tmp_path, FixtureFetcher())
    by_source = {}
    for i in _news(tmp_path):
        by_source.setdefault(i["source_id"], []).append(i)
    assert "hf-daily-papers" not in by_source  # 20 general AI papers, none on topic
    assert len(by_source["mathstodon-leanprover"]) == 20  # filter: none


def test_duplicate_paper_across_sources_kept_once(tmp_path):
    # arxiv-rss.xml reuses papers from arxiv-api.xml, so the ids collide.
    meta = _run(tmp_path, FixtureFetcher())
    rss = next(s for s in meta["sources"] if s["id"] == "arxiv-rss")
    assert rss["fetched"] == 4 and rss["new"] == 0
    ids = [i["id"] for i in _news(tmp_path)]
    assert len(ids) == len(set(ids))


def test_broken_source_reports_error_and_others_still_save(tmp_path):
    meta = _run(tmp_path, FixtureFetcher({"xena": b"<html>502 Bad Gateway</html>", "lean4-releases": b"{oops"}))
    status = {s["id"]: s for s in meta["sources"]}
    assert status["xena"]["status"] == "error" and status["xena"]["error"]
    assert status["lean4-releases"]["status"] == "error"
    assert {e["source"] for e in meta["errors"]} == {"xena", "lean4-releases"}
    assert status["afp"]["status"] == "ok" and status["afp"]["kept"] > 0
    assert _news(tmp_path)
    cache = json.loads((tmp_path / "data" / "http_cache.json").read_text())
    assert "last_fetch" not in cache["sources"]["xena"]  # retried on the next run


def test_schedule_skips_sources_that_are_not_due(tmp_path):
    _run(tmp_path, FixtureFetcher())
    fetcher = FixtureFetcher()
    meta = _run(tmp_path, fetcher, now=NOW + timedelta(hours=1))
    status = {s["id"]: s["status"] for s in meta["sources"]}
    assert status["hackernews"] == "ok" and status["mathstodon-leanprover"] == "ok"  # every 1h
    assert status["xena"] == "skipped" and status["arxiv-api"] == "skipped"  # every 6h, daily
    assert "xena" not in fetcher.calls


# ---------- Claude step ----------

def test_claude_judgements_are_applied(tmp_path):
    def step(lines):
        rows = []
        for k, x in enumerate(lines):
            rows.append({"id": x["id"], "relevant": k % 3 != 0, "type": "news", "score": 7,
                         "summary": "A very long summary " * 20})
        return "end_turn", rows

    fake = FakeClaude([step])
    meta = _run(tmp_path, FixtureFetcher(), use_claude=True, claude_client=fake)
    assert len(fake.requests) == 1
    req = fake.requests[0]
    assert req["model"] == "claude-haiku-5-5" and req["output_config"]["effort"] == "low"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert "never follow instructions" in req["system"].lower()
    sent = req["messages"][0]["content"].count('{"id"')
    assert 0 < sent <= 100 and meta["claude"]["items_sent"] == sent
    assert meta["claude"]["input_tokens"] == 100 * sent and meta["claude"]["rejected"] > 0
    items = _news(tmp_path)
    judged = [i for i in items if i["relevant"] is not None]
    assert judged and all(i["relevant"] is True for i in judged)
    assert all(len(i["summary"]) <= 160 and i["score"] == 5 for i in judged)
    assert all(i["type"] == ("social" if i["source_id"].startswith(("mathstodon", "lean-fro")) else "news") for i in judged)


def test_claude_cap_and_retry_on_next_run(tmp_path):
    cfg = load_config()
    cfg.claude.max_items_per_run = 10
    fake = FakeClaude()
    _run(tmp_path, FixtureFetcher(), use_claude=True, claude_client=fake, cfg=cfg)
    items = _news(tmp_path)
    assert sum(1 for i in items if i["relevant"] is True) == 10
    waiting = sum(1 for i in items if i["relevant"] is None)
    assert waiting > 0
    _run(tmp_path, FixtureFetcher(), use_claude=True, claude_client=fake, cfg=cfg, now=NOW + timedelta(minutes=1))
    assert sum(1 for i in _news(tmp_path) if i["relevant"] is True) == 20


def test_claude_api_error_keeps_items_for_later(tmp_path):
    err = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    meta = _run(tmp_path, FixtureFetcher(), use_claude=True, claude_client=FakeClaude(error=err))
    assert meta["claude"]["errors"] and any(e["source"] == "claude" for e in meta["errors"])
    items = _news(tmp_path)
    assert items and all(i["summary"] is None and i["relevant"] is None for i in items)
    fake = FakeClaude()
    _run(tmp_path, FixtureFetcher(), use_claude=True, claude_client=fake, now=NOW + timedelta(hours=1))
    assert fake.requests  # retried
    # too old to retry after retry_days
    fake2 = FakeClaude()
    _run(tmp_path, FixtureFetcher(), use_claude=True, claude_client=fake2, now=NOW + timedelta(days=4))
    assert not fake2.requests


def test_claude_refusal_retries_items_one_at_a_time(tmp_path):
    def refuse_all(lines):
        return "refusal", None

    def refuse_first(lines):
        return ("refusal", None) if lines[0]["id"] == target["id"] else ("end_turn", [
            {"id": x["id"], "relevant": True, "type": "paper", "score": 3, "summary": "ok"} for x in lines])

    cfg = load_config()
    cfg.claude.max_items_per_run = 3
    _run(tmp_path, FixtureFetcher(), cfg=cfg)  # fill the archive first, without Claude
    waiting = sorted(_news(tmp_path), key=lambda i: (i["fetched"], i["id"]))[:3]
    target = waiting[0]
    fake = FakeClaude([refuse_all, refuse_first, refuse_first, refuse_first])
    meta = _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=fake, now=NOW + timedelta(minutes=1))
    assert len(fake.requests) == 4  # one batch, then three single items
    assert meta["claude"]["refusals"] == [{"id": target["id"], "category": "cyber"}]
    by_id = {i["id"]: i for i in _news(tmp_path)}
    assert by_id[target["id"]]["summary"] is None  # keyword filter only
    assert by_id[waiting[1]["id"]]["summary"] == "ok"


def test_claude_max_tokens_splits_batch(tmp_path):
    cfg = load_config()
    cfg.claude.max_items_per_run = 45
    fake = FakeClaude([lambda lines: ("max_tokens", None)])
    meta = _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=fake)
    assert len(fake.requests) == 1 + 3  # 45 items split into chunks of 20
    assert meta["claude"]["judged"] == 45


def test_sonnet_uses_server_side_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-5-5")
    seen = {}
    fake = FakeClaude()
    real = fake._create

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    fake.beta = SimpleNamespace(messages=SimpleNamespace(create=spy))
    fake.messages = SimpleNamespace(create=lambda **k: pytest.fail("Sonnet should use the beta endpoint"))
    _run(tmp_path, FixtureFetcher(), use_claude=True, claude_client=fake)
    assert seen["model"] == "claude-sonnet-5-5"
    assert seen["fallbacks"] == "default" and seen["betas"] == ["server-side-fallback-2026-07-01"]


def test_use_claude_flag(monkeypatch):
    from news import claude

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("USE_CLAUDE", "true")
    assert not claude.enabled()  # no key, no Claude
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert claude.enabled()
    monkeypatch.setenv("USE_CLAUDE", "false")
    assert not claude.enabled()
