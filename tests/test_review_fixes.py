"""Regression tests for the defects found in the Codex and Kimi reviews of version 1."""

from __future__ import annotations

import copy
import json
from datetime import timedelta
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from news import claude
from news.config import load_config
from news.dedup import SeenStore
from news.net import HttpClient
from news.serve import UnsafeOutput, build_site
from news.sources import FetchContext, arxiv

from .conftest import NOW, FakeClaude, FixtureFetcher
from .test_pipeline import _news, _run


# ---------- the real SDK on a mock transport (Codex F-4) ----------

def _sdk_client(replies: list[dict]):
    """An Anthropic client whose HTTP replies come from `replies`, in order."""
    sent = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append({"path": request.url.path, "beta": request.headers.get("anthropic-beta", ""), "body": json.loads(request.content)})
        r = replies.pop(0)
        body = {
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": r["text"]}], "stop_reason": r["stop"], "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        if r["stop"] == "refusal":
            body["stop_details"] = {"type": "refusal", "category": "cyber", "explanation": "test"}
        return httpx2.Response(200, json=body)

    client = anthropic.Anthropic(api_key="test", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    return client, sent


ITEMS = [{"id": f"u:{k:016x}", "source": "S", "title": f"Item {k}", "source_text": "text"} for k in range(3)]


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-sonnet-5-5"])
def test_real_sdk_refusal_in_prose_is_handled(model):
    ok = json.dumps({"items": [{"id": ITEMS[1]["id"], "relevant": True, "type": "paper", "score": 3, "summary": "ok"}]})
    ok2 = json.dumps({"items": [{"id": ITEMS[2]["id"], "relevant": False, "type": "news", "score": 1, "summary": "no"}]})
    client, sent = _sdk_client([
        {"stop": "refusal", "text": "I can't help with that."},  # the batch
        {"stop": "refusal", "text": "I can't help with that."},  # item 0 alone
        {"stop": "end_turn", "text": ok},
        {"stop": "end_turn", "text": ok2},
    ])
    r = claude.judge(ITEMS, load_config().claude, client=client, model=model)
    assert len(sent) == 4
    assert r.refusals == [{"id": ITEMS[0]["id"], "category": "cyber"}]
    assert set(r.judgements) == {ITEMS[1]["id"], ITEMS[2]["id"]}
    assert sent[0]["body"]["output_config"]["format"]["type"] == "json_schema"
    if model == "claude-sonnet-5-5":
        assert sent[0]["body"]["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in sent[0]["beta"]


def test_real_sdk_truncated_json_is_handled():
    client, sent = _sdk_client([{"stop": "max_tokens", "text": '{"items": [{"id": "u:'}])
    r = claude.judge(ITEMS, load_config().claude, client=client, model="claude-haiku-5-5")
    assert r.judgements == {} and r.errors and "max_tokens" in r.errors[0]


def test_real_sdk_off_schema_text_is_handled():
    client, _ = _sdk_client([{"stop": "end_turn", "text": "not json"}])
    r = claude.judge(ITEMS, load_config().claude, client=client, model="claude-haiku-5-5")
    assert r.judgements == {} and r.errors


# ---------- judgements only for the ids in the request (Codex F-9) ----------

def test_judgement_for_an_id_outside_the_request_is_ignored():
    a, b = ITEMS[0], ITEMS[1]

    def batch(lines):
        return "refusal", None

    def only_a(lines):  # answers about B, which was not in this request
        return "end_turn", [{"id": b["id"], "relevant": False, "type": "news", "score": 1, "summary": "wrong"}]

    def only_b(lines):
        return "end_turn", [{"id": b["id"], "relevant": True, "type": "paper", "score": 4, "summary": "right"}]

    r = claude.judge([a, b], load_config().claude, client=FakeClaude([batch, only_a, only_b]))
    assert r.judgements[b["id"]].relevant is True and r.judgements[b["id"]].summary == "right"
    assert a["id"] not in r.judgements
    assert any("not in this request" in e for e in r.errors)


def test_time_budget_leaves_items_for_the_next_run():
    r = claude.judge(ITEMS, load_config().claude, client=FakeClaude(), budget_seconds=0)
    assert r.judgements == {} and r.requests == 0 and "time budget" in r.errors[0]


# ---------- one malformed link does not stop the run (Codex F-3) ----------

BAD_FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Broken host</title><link>https://[bad</link><pubDate>Sun, 22 Nov 2026 10:00:00 +0000</pubDate></item>
<item><title>Wrong scheme</title><link>httpx://example.org/bad</link><pubDate>Sun, 22 Nov 2026 10:00:00 +0000</pubDate></item>
<item><title>A good post on Lean</title><link>https://example.org/good</link><pubDate>Sun, 22 Nov 2026 10:00:00 +0000</pubDate></item>
</channel></rss>"""


def test_malformed_links_are_skipped_per_item(tmp_path):
    meta = _run(tmp_path, FixtureFetcher({"xena": BAD_FEED}))
    xena = next(s for s in meta["sources"] if s["id"] == "xena")
    assert xena["status"] == "ok" and xena["fetched"] == 1 and xena["kept"] == 1
    assert "2 malformed item(s) skipped" in xena["warning"]
    assert next(s for s in meta["sources"] if s["id"] == "afp")["kept"] > 0
    assert any(i["url"] == "https://example.org/good" for i in _news(tmp_path))


# ---------- arXiv paging does not use submission dates as a watermark (Codex F-7) ----------

def _atom(n: int, start: int) -> bytes:
    entries = "".join(
        f"<entry><id>http://arxiv.org/abs/2611.{start + k:05d}v1</id><title>Paper {start + k}</title>"
        f"<published>2026-11-21T00:00:00Z</published><summary>s</summary><author><name>A</name></author></entry>"
        for k in range(n)
    )
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>q</title>{entries}</feed>'.encode()


def test_arxiv_fetches_second_page_for_recent_full_page(monkeypatch):
    pages = [_atom(100, 0), _atom(50, 100)]
    calls = []

    class Http:
        def get(self, url, params=None, **kw):
            calls.append(params["start"])
            return SimpleNamespace(status=200, content=pages.pop(0))

    monkeypatch.setattr(arxiv.time, "sleep", lambda s: None)
    src = load_config().source("arxiv-api")
    # Last fetch was yesterday, after these papers were submitted: they must still be read.
    ctx = FetchContext(http=Http(), now=NOW, keywords=[], state={"last_fetch": "2026-11-22T06:00:00Z"})
    res = arxiv.fetch(src, ctx)
    assert calls == [0, 100] and len(res.items) == 150 and not res.warnings


# ---------- title duplicates wait for Claude's decision (Codex F-8) ----------

DUP_FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>A new approach to checking proofs</title><link>https://one.example.org/a</link><pubDate>Sun, 22 Nov 2026 10:00:00 +0000</pubDate></item>
<item><title>A new approach to checking proofs</title><link>https://two.example.org/b</link><pubDate>Sun, 22 Nov 2026 09:00:00 +0000</pubDate></item>
</channel></rss>"""


def test_title_duplicate_is_released_when_first_copy_is_hidden(tmp_path):
    cfg = load_config()
    for s in cfg.sources:
        s.enabled = s.id == "xena"
    cfg.source("xena").params["trusted"] = False  # an untrusted source, so Claude's "no" hides the item
    first = {}

    def reject_first(lines):
        first["id"] = lines[0]["id"]
        return "end_turn", [{"id": lines[0]["id"], "relevant": False, "type": "blog", "score": 1, "summary": "no"}]

    _run(tmp_path, FixtureFetcher({"xena": DUP_FEED}), cfg=cfg, use_claude=True, claude_client=FakeClaude([reject_first]))
    # The rejected copy is hidden; the held copy was released and waits for Claude.
    items = _news(tmp_path)
    assert [(i["url"], i["relevant"]) for i in items] == [("https://two.example.org/b", None)]
    assert first["id"] != items[0]["id"]
    hidden = json.loads((tmp_path / "data" / "hidden.json").read_text())["items"]
    assert [(i["id"], i["hidden"]) for i in hidden] == [(first["id"], True)]
    assert SeenStore.load(tmp_path / "data" / "seen.json").alts == {}
    fake = FakeClaude()
    _run(tmp_path, FixtureFetcher({"xena": DUP_FEED}), cfg=cfg, use_claude=True, claude_client=fake,
         now=NOW + timedelta(minutes=1))
    items = _news(tmp_path)
    assert [i["url"] for i in items] == ["https://two.example.org/b"] and items[0]["relevant"] is True


def test_title_duplicate_is_dropped_when_first_copy_is_kept(tmp_path):
    cfg = load_config()
    for s in cfg.sources:
        s.enabled = s.id == "xena"
    _run(tmp_path, FixtureFetcher({"xena": DUP_FEED}), cfg=cfg, use_claude=True, claude_client=FakeClaude())
    items = _news(tmp_path)
    assert len(items) == 1 and items[0]["relevant"] is True
    assert SeenStore.load(tmp_path / "data" / "seen.json").alts == {}


# ---------- refused items are not sent again (found while waiting for the reviews) ----------

def test_refused_item_is_not_retried(tmp_path):
    cfg = load_config()
    for s in cfg.sources:
        s.enabled = s.id == "xena"
    _run(tmp_path, FixtureFetcher(), cfg=cfg)  # fill the archive without Claude
    n = len(_news(tmp_path))
    fake = FakeClaude([lambda lines: ("refusal", None)] + [lambda lines: ("refusal", None)] * n)
    meta = _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=fake, now=NOW + timedelta(minutes=1))
    assert len(meta["claude"]["refusals"]) == n
    again = FakeClaude()
    _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=again, now=NOW + timedelta(hours=1))
    assert again.requests == []  # nothing left to send
    assert all(i["summary"] is None for i in _news(tmp_path))  # kept on the keyword filter alone


# ---------- Zulip announcements stay distinct through dedup (Codex F-10) ----------

def test_zulip_topics_survive_normalisation(tmp_path):
    cfg = load_config()
    for s in cfg.sources:
        s.enabled = s.id == "lean-zulip"
    meta = _run(tmp_path, FixtureFetcher(), cfg=cfg, now=NOW.replace(year=2026, month=11, day=25))
    assert meta["counts"]["new_kept"] == 2
    assert len({i["url"] for i in _news(tmp_path)}) == 2


# ---------- the site builder never deletes source directories (Codex F-1) ----------

def test_build_site_refuses_protected_outputs(tmp_path):
    from news.config import ROOT

    for out in (ROOT, ROOT / "site", ROOT / "data", ROOT / "news", ROOT.parent):
        with pytest.raises(UnsafeOutput):
            build_site(out)
    keep = tmp_path / "mine"
    keep.mkdir()
    (keep / "notes.txt").write_text("do not delete")
    with pytest.raises(UnsafeOutput):
        build_site(keep)
    assert (keep / "notes.txt").exists()
    out = build_site(tmp_path / "built")
    assert (out / "index.html").exists()
    build_site(out)  # rebuilding its own output is allowed
    link = tmp_path / "link"
    link.symlink_to(out)
    with pytest.raises(UnsafeOutput):
        build_site(link)


# ---------- trusted sources are never dropped (Jin's decision, 23 Nov 2026) ----------

def test_trusted_source_items_are_kept_when_claude_says_not_relevant(tmp_path):
    cfg = load_config()
    for s in cfg.sources:
        s.enabled = s.id in ("xena", "mathstodon-leanprover")
    assert cfg.source("xena").param("trusted") and not cfg.source("mathstodon-leanprover").param("trusted")

    def all_irrelevant(lines):
        return "end_turn", [{"id": x["id"], "relevant": False, "type": "blog", "score": 1, "summary": "Off topic."}
                            for x in lines]

    meta = _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=FakeClaude([all_irrelevant]))
    items = _news(tmp_path)
    assert items and {i["source_id"] for i in items} == {"xena"}  # the hashtag posts were hidden
    assert all(i["relevant"] is False and i["summary"] == "Off topic." and i["score"] == 1 for i in items)
    assert not any("hidden" in i for i in items)
    assert meta["claude"]["kept_trusted"] == len(items) and meta["claude"]["hidden"] == 20
    hidden = json.loads((tmp_path / "data" / "hidden.json").read_text())["items"]
    assert len(hidden) == 20 and {i["source_id"] for i in hidden} == {"mathstodon-leanprover"}
