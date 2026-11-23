"""Items Claude judges off topic are hidden, not deleted (decision D33): they stay
in the archive and in hidden.json, and the page, search and the feed leave them out."""

from __future__ import annotations

import json
from datetime import timedelta

from news import claude
from news.config import load_config
from news.serve import build_site
from news.store import Archive

from .conftest import NOW, FakeClaude, FixtureFetcher
from .test_pipeline import _news, _run


def _load(tmp_path, name):
    return json.loads((tmp_path / "data" / name).read_text())


def _hide_every_other(lines):
    return "end_turn", [{"id": x["id"], "relevant": k % 2 == 0, "type": "paper", "score": 3 if k % 2 == 0 else 1,
                         "summary": "On topic." if k % 2 == 0 else "Off topic."} for k, x in enumerate(lines)]


def _one_source(source_id):
    cfg = load_config()
    for s in cfg.sources:
        s.enabled = s.id == source_id
    assert not cfg.source(source_id).param("trusted", False)
    return cfg


def test_off_topic_items_are_hidden_not_deleted(tmp_path):
    cfg = _one_source("mathstodon-leanprover")
    meta = _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=FakeClaude([_hide_every_other]))

    archive = Archive(tmp_path / "data").all_items()
    hidden = [i for i in archive if i.get("hidden")]
    shown = [i for i in archive if not i.get("hidden")]
    assert hidden and shown and len(hidden) == meta["claude"]["hidden"] == meta["counts"]["new_hidden"]
    assert all(i["relevant"] is False and i["summary"] == "Off topic." for i in hidden)

    hidden_ids = {i["id"] for i in hidden}
    assert {i["id"] for i in _news(tmp_path)} == {i["id"] for i in shown}
    assert {i["id"] for i in _load(tmp_path, "hidden.json")["items"]} == hidden_ids
    assert not hidden_ids & {row[0] for row in _load(tmp_path, "search.json")["rows"]}
    feed = (tmp_path / "data" / "feed.xml").read_text()
    assert not any(i["url"] in feed for i in hidden) and all(i["url"] in feed for i in shown[:5])
    assert meta["counts"]["items_in_hidden"] == len(hidden)
    assert meta["sources"][0]["kept"] == len(shown)  # hidden items do not count as kept
    assert "hidden.json" in meta["_changed"]


def test_hidden_items_are_not_judged_again_but_restored_ones_are(tmp_path):
    cfg = _one_source("mathstodon-leanprover")
    _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=FakeClaude([_hide_every_other]))
    hidden = [i for i in Archive(tmp_path / "data").all_items() if i.get("hidden")]

    # A later run sends nothing: every item has a judgement.
    fake = FakeClaude()
    _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=fake, now=NOW + timedelta(minutes=5))
    assert fake.requests == []

    # Restoring an item by hand (clearing its judgement) sends it to Claude again.
    archive = Archive(tmp_path / "data")
    item = dict(hidden[0], relevant=None, score=None, summary=None)
    del item["hidden"]
    archive.put(item)
    month = Archive.month_of(item)
    (tmp_path / "data" / "archive" / f"{month}.json").write_text(json.dumps(archive.documents()[month]))
    fake = FakeClaude()
    _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=fake, now=NOW + timedelta(minutes=10))
    assert len(fake.requests) == 1 and fake.requests[0]["messages"][0]["content"].count('{"id"') == 1
    assert item["id"] in {i["id"] for i in _news(tmp_path)}


def test_site_build_includes_hidden_json(tmp_path):
    cfg = _one_source("mathstodon-leanprover")
    _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=FakeClaude([_hide_every_other]))
    out = build_site(tmp_path / "_site", data_dir=tmp_path / "data")
    assert json.loads((out / "data" / "hidden.json").read_text())["items"]


def test_prompt_keeps_borderline_items():
    p = claude.SYSTEM_PROMPT
    assert "When you are unsure" in p and "mark it relevant with score 2" in p
    assert "mechanised (machine-checked) proofs" in p
