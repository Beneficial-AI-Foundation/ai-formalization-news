"""Events get their own type and filter button; job posts and funding calls are left out;
the Lean 4 exercise series is skipped (decision D42)."""

from __future__ import annotations

import copy
import json
import re

from news import claude
from news.config import load_config
from news.fetch import within_age
from news.sources import feed
from news.store import Archive, Validator

from .conftest import NOW, FakeClaude, FixtureFetcher, fixture_path
from .test_pipeline import _run

EVENT = re.compile(r"call for|workshop|seminar|school|course announcement|appel", re.I)
OPPORTUNITY = re.compile(r"phd|postdoc|position|scholarship|research roles|funding", re.I)


def _kind(title):
    return "opportunity" if OPPORTUNITY.search(title) else "event" if EVENT.search(title) else "news"


def _judge(lines):
    return "end_turn", [{"id": x["id"], "relevant": True, "type": _kind(x["title"]), "score": 3,
                         "summary": "A summary."} for x in lines]


def _only(source_id):
    cfg = load_config()
    for s in cfg.sources:
        s.enabled = s.id == source_id
    return cfg


def _load(tmp_path, name):
    return json.loads((tmp_path / "data" / name).read_text())["items"]


def test_lean_exercise_posts_are_skipped():
    src = load_config().source("mathstodon-leanprover")
    content = fixture_path("mathstodon-leanprover").read_bytes()
    titles = [i["title"] for i in feed.parse(src, content, NOW)]
    everything = copy.deepcopy(src)
    del everything.params["exclude_titles"]
    all_titles = [i["title"] for i in feed.parse(everything, content, NOW)]
    skipped = [t for t in all_titles if t not in titles]
    assert len(skipped) == 2
    assert skipped[0].startswith("Lean 4 challenge 31:") and skipped[1].startswith("Reto 31 de Lean 4:")


def test_events_get_their_type_and_opportunities_are_hidden_even_from_a_trusted_source(tmp_path):
    cfg = _only("rocq-discourse")
    assert cfg.source("rocq-discourse").param("trusted")
    _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=FakeClaude([_judge]))
    news, hidden = _load(tmp_path, "news.json"), _load(tmp_path, "hidden.json")

    events = {i["title"] for i in news if i["type"] == "event"}
    assert "NWPT 2026: Call for Participation" in events and "2027 Dutch Winter School on Logic and Verification" in events
    hidden_titles = {i["title"] for i in hidden}
    src = cfg.source("rocq-discourse")
    recent = within_age(src, feed.parse(src, fixture_path("rocq-discourse").read_bytes(), NOW), NOW, 30)
    assert hidden_titles == {i["title"] for i in recent if _kind(i["title"]) == "opportunity"}
    assert "PhD scholarships at the University of Strathclyde 2027" in hidden_titles
    assert not any(OPPORTUNITY.search(i["title"]) for i in news)
    # The page has no "opportunity" type: a hidden job post keeps the source's type.
    assert {i["type"] for i in hidden} == {"news"}
    assert all(i["type"] != "opportunity" for i in Archive(tmp_path / "data").all_items())


def test_a_screened_opportunity_is_dropped(tmp_path):
    cfg = _only("hackernews-top")

    def judge(lines):
        return "end_turn", [{"id": x["id"], "relevant": True, "type": "opportunity" if k == 0 else "news",
                             "score": 3, "summary": "A summary."} for k, x in enumerate(lines)]

    meta = _run(tmp_path, FixtureFetcher(), cfg=cfg, use_claude=True, claude_client=FakeClaude([judge]))
    assert meta["claude"]["dropped"] == 1 and meta["counts"]["new_kept"] == 26


def test_prompt_and_schemas_know_the_new_types():
    p = claude.SYSTEM_PROMPT
    assert '"event": a call for papers' in p and '"opportunity": a job or position' in p
    assert "Jobs, workshops" not in p  # jobs are no longer "relevant" by rule
    enum = claude.JUDGEMENTS_SCHEMA["properties"]["items"]["items"]["properties"]["type"]["enum"]
    assert {"event", "opportunity"} <= set(enum)
    assert claude.Judgement(id="x", relevant=True, type="event", score=3, summary="s").type == "event"
    item = {"id": "u:0000000000000001", "type": "event", "source": "S", "source_id": "s", "title": "T",
            "url": "https://example.org/", "authors": [], "published": "2026-11-23T00:00:00Z",
            "fetched": "2026-11-23T00:00:00Z", "source_text": "", "summary": None, "relevant": None, "score": None}
    Validator().check({"version": 1, "items": [item]}, "news.json")  # "event" is a valid page type
