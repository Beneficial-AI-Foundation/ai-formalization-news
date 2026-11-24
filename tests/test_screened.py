"""Sources marked `needs_claude` (decision D40): Claude judges each item before it is stored;
an item judged off topic is dropped, not hidden; without Claude, or when an item is not
judged, it waits for a later run."""

from __future__ import annotations

import json
from datetime import timedelta

from news.config import load_config
from news.store import Archive

from .conftest import NOW, FakeClaude, FixtureFetcher
from .test_pipeline import _run

ON_TOPIC = ("Riemann", "Lean")


def _only_top():
    cfg = load_config()
    for s in cfg.sources:
        s.enabled = s.id == "hackernews-top"
    return cfg


def _judge(lines):
    return "end_turn", [{"id": x["id"], "relevant": any(w in x["title"] for w in ON_TOPIC), "type": "news",
                         "score": 4 if any(w in x["title"] for w in ON_TOPIC) else 1, "summary": "A summary."}
                        for x in lines]


def _load(tmp_path, name):
    return json.loads((tmp_path / "data" / name).read_text())


def test_on_topic_stories_are_kept_and_the_rest_dropped(tmp_path):
    cfg = _only_top()
    meta = _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=FakeClaude([_judge]))
    archive = Archive(tmp_path / "data").all_items()
    assert sorted(i["title"] for i in archive) == [
        "Lean verification of the Riemann Hypothesis", "Masullo on the Riemann Hypothesis",
        "Progress map for the Lean formalization of Alessandro Masullo's manuscript"]
    assert all(i["relevant"] is True and i["summary"] == "A summary." and not i.get("hidden") for i in archive)
    assert meta["counts"]["new_kept"] == 3 and meta["counts"]["new_dropped"] == 24 == meta["claude"]["dropped"]
    assert meta["counts"]["new_hidden"] == 0 and _load(tmp_path, "hidden.json")["items"] == []
    assert len(_load(tmp_path, "news.json")["items"]) == 3

    # Every story now has a verdict, so the next run sends nothing.
    fake = FakeClaude()
    _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=fake, ignore_schedule=True,
         now=NOW + timedelta(hours=1))
    assert fake.requests == []


def test_without_claude_the_stories_wait(tmp_path):
    cfg = _only_top()
    meta = _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=False)
    assert meta["counts"]["new_kept"] == 0 and Archive(tmp_path / "data").all_items() == []
    # Nothing was marked seen, so a run with Claude judges every story.
    fake = FakeClaude([_judge])
    meta = _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=fake, ignore_schedule=True,
                now=NOW + timedelta(hours=1))
    assert fake.requests and meta["counts"]["new_kept"] == 3


def test_stories_not_judged_are_sent_again(tmp_path):
    cfg = _only_top()

    def first_ten(lines):  # the reply covers only part of the request
        return _judge(lines[:10])

    _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=FakeClaude([first_ten]))
    fake = FakeClaude([_judge])
    _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=fake, ignore_schedule=True,
         now=NOW + timedelta(minutes=5))
    sent = fake.requests[0]["messages"][0]["content"].count('{"id"')
    assert sent == 17  # the 27 stories less the 10 judged
    assert len(Archive(tmp_path / "data").all_items()) == 3
