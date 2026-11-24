"""Each source's parser on its saved fixture."""

from __future__ import annotations

import pytest

from news.config import load_config
from news.dedup import make_id, normalize_url
from news.keywords import compile_keywords
from news.net import FetchError
from news.store import Validator
from news.sources import feed

from .conftest import NOW, fixture_path, parse_fixture

CFG = load_config()
KEYWORDS = compile_keywords(CFG.keywords)
# Hacker News: the saved reply has no story with 5 points yet, so it is checked separately.
WITH_ITEMS = [s for s in CFG.sources if s.enabled and s.kind != "hackernews"]


def test_every_enabled_source_has_a_fixture():
    for s in CFG.enabled_sources:
        assert fixture_path(s.id).exists(), s.id


@pytest.mark.parametrize("source", WITH_ITEMS, ids=lambda s: s.id)
def test_parser_returns_valid_items(source):
    items = parse_fixture(source, fixture_path(source.id).read_bytes(), NOW, KEYWORDS)
    assert items, f"{source.id} parsed no items"
    validator = Validator()
    for item in items:
        item["url"] = normalize_url(item["url"])
        item["id"] = make_id(item["url"])
    validator.check({"version": 1, "items": items}, source.id)
    for item in items:
        assert item["type"] == source.type
        assert item["title"].strip()
        assert item["fetched"] == "2026-11-23T03:17:00Z"
        assert item["published"] <= item["fetched"]


def test_arxiv_api_links_are_abs_without_version():
    src = CFG.source("arxiv-api")
    items = parse_fixture(src, fixture_path("arxiv-api").read_bytes(), NOW, KEYWORDS)
    assert len(items) == 100
    for i in items:
        assert i["url"].startswith("https://arxiv.org/abs/")
        assert "v" not in i["url"].rsplit("/", 1)[1]
        assert i["source"].startswith("arXiv ")
        assert i["authors"]


def test_arxiv_rss_skips_replacements_and_strips_preamble():
    src = CFG.source("arxiv-rss")
    items = parse_fixture(src, fixture_path("arxiv-rss").read_bytes(), NOW, KEYWORDS)
    assert len(items) == 4  # five entries, one "replace"
    for i in items:
        assert not i["source_text"].startswith("arXiv:")
        assert "Announce Type" not in i["source_text"]
        assert len(i["authors"]) >= 1


def test_arxiv_rss_weekend_feed_is_empty_not_an_error():
    src = CFG.source("arxiv-rss")
    assert parse_fixture(src, fixture_path("arxiv-rss-weekend").read_bytes(), NOW, KEYWORDS) == []


def test_social_posts_are_short_and_credited():
    for sid in ("lean-fro-bluesky", "mathstodon-leanprover", "mathstodon-formalverification"):
        src = CFG.source(sid)
        items = parse_fixture(src, fixture_path(sid).read_bytes(), NOW, KEYWORDS)
        for i in items:
            assert i["type"] == "social"
            assert len(i["source_text"]) <= 300
            assert len(i["title"]) <= 140
            assert i["authors"], f"{sid} item without author"
    masto = parse_fixture(CFG.source("mathstodon-leanprover"), fixture_path("mathstodon-leanprover").read_bytes(), NOW, KEYWORDS)
    from urllib.parse import urlparse

    # Tag feeds carry posts from other servers too; each author is @user@<the post's server>.
    assert all(i["authors"][0].startswith("@") and i["authors"][0].endswith("@" + urlparse(i["url"]).netloc) for i in masto)
    bsky = parse_fixture(CFG.source("lean-fro-bluesky"), fixture_path("lean-fro-bluesky").read_bytes(), NOW, KEYWORDS)
    assert all(i["authors"] == ["Lean FRO"] for i in bsky)


def test_hf_papers_link_to_arxiv():
    src = CFG.source("hf-daily-papers")
    items = parse_fixture(src, fixture_path("hf-daily-papers").read_bytes(), NOW, KEYWORDS)
    assert len(items) == 20
    assert all(make_id(i["url"]).startswith("arxiv:") for i in items)


def test_github_releases_mark_prereleases():
    src = CFG.source("lean4-releases")
    items = parse_fixture(src, fixture_path("lean4-releases").read_bytes(), NOW, KEYWORDS)
    assert items[0]["title"].startswith("Lean 4 ")
    assert any("(pre-release)" in i["title"] for i in items)
    assert any("(pre-release)" not in i["title"] for i in items)


def test_github_releases_stable_only_skips_prereleases():
    source = CFG.source("agda-releases")
    assert source.param("stable_only") is True
    content = fixture_path("agda-releases").read_bytes()
    stable = parse_fixture(source, content, NOW, KEYWORDS)
    assert [i["title"] for i in stable] == ["Agda v2.9.0", "Agda v2.8.0.2", "Agda v2.8.0.1", "Agda v2.8.0"]
    source.params["stable_only"] = False
    try:
        every = parse_fixture(source, content, NOW, KEYWORDS)
    finally:
        source.params["stable_only"] = True
    assert len(every) == 8 and sum("(pre-release)" in i["title"] for i in every) == 4


def test_keywords_cover_other_proof_assistants():
    from news.keywords import item_matches

    for text in ("New release of Agda", "A HOL4 formalisation", "Porting to HOL-Light", "Metamath set.mm",
                 "The Mizar Mathematical Library"):
        assert item_matches({"title": text, "source_text": ""}, KEYWORDS), text
    query = CFG.source("arxiv-api").param("query")
    for term in ("abs:Agda", "abs:HOL4", 'abs:"HOL Light"', "abs:Metamath", "abs:Mizar"):
        assert term in query, term


def test_hackernews_threshold_and_keyword_match():
    import copy

    src = CFG.source("hackernews")
    content = fixture_path("hackernews").read_bytes()
    assert parse_fixture(src, content, NOW, KEYWORDS) == []  # no story has 5 points yet
    low = copy.deepcopy(src)
    low.params["min_points"] = 1
    items = parse_fixture(low, content, NOW, KEYWORDS)
    assert items
    titles = " ".join(i["title"] for i in items)
    assert "Lean 4" in titles
    assert "FlightTrack" not in titles  # matched the search but not a keyword


def test_hackernews_story_matched_by_its_link():
    """VP != VNP (23 Nov 2026): neither the title nor the link had a keyword until "formal math"
    was added, which matches the link github.com/anthropics/formal-math/..."""
    from news.sources import hackernews

    hit = {"objectID": "46000001", "title": "VP ≠ VNP over Every Field", "points": 299, "num_comments": 257,
           "url": "https://github.com/anthropics/formal-math/tree/main/vp-vnp", "author": "someone",
           "created_at_i": 1795471740}  # 23 Nov 2026, 22:09 UTC
    src = CFG.source("hackernews")
    items = hackernews.select(src, [hit], KEYWORDS, NOW)
    assert [i["title"] for i in items] == ["VP ≠ VNP over Every Field"]
    assert items[0]["url"] == hit["url"]
    before = compile_keywords([k for k in CFG.keywords if k not in ("formal math", "formal mathematics")])
    assert hackernews.select(src, [hit], before, NOW) == []


@pytest.mark.parametrize("sid", ["anthropic-formal-math", "openai-math"])
def test_commit_feeds_skip_merge_and_bump_commits(sid):
    import copy

    src = CFG.source(sid)
    content = fixture_path(sid).read_bytes()
    kept = [i["title"] for i in feed.parse(src, content, NOW)]
    everything = copy.deepcopy(src)
    del everything.params["exclude_titles"]
    all_titles = [i["title"] for i in feed.parse(everything, content, NOW)]
    assert any(t.startswith("Merge pull request") for t in all_titles)
    assert kept == [t for t in all_titles if not t.startswith(("Merge pull request", "Merge branch", "Bump "))]
    assert len(kept) < len(all_titles)


def test_formal_math_commits_link_to_the_commit():
    items = feed.parse(CFG.source("anthropic-formal-math"), fixture_path("anthropic-formal-math").read_bytes(), NOW)
    eth = next(i for i in items if i["title"].startswith("Add eth: paper and Lean 4 formalization"))
    assert eth["url"].startswith("https://github.com/anthropics/formal-math/commit/")
    assert eth["published"] == "2026-10-27T03:07:21Z"


@pytest.mark.parametrize("sid,total,kept", [
    ("openai-news", 31, "Sharing AI progress in mathematics"),
    ("deepmind-blog", 30, "Introducing AlphaProof Code: a breakthrough in verifying complex software at depth"),
])
def test_lab_blog_keyword_filter(sid, total, kept):
    """Lab blogs post about models and products most days; the keyword filter keeps the posts
    on formal mathematics and verification."""
    from news.keywords import item_matches

    items = feed.parse(CFG.source(sid), fixture_path(sid).read_bytes(), NOW)
    assert len(items) == total
    assert [i["title"] for i in items if item_matches(i, KEYWORDS)] == [kept]


def test_zulip_announcements_one_item_per_new_topic():
    src = CFG.source("lean-zulip")
    items = parse_fixture(src, fixture_path("lean-zulip").read_bytes(), NOW, KEYWORDS)
    # "Lean 3 is end of life" started 60 days ago, before the 30-day lookback.
    assert [i["title"] for i in items] == ["Lean v4.37.0 released", "Formalising the Kakeya conjecture in Lean"]
    assert items[0]["url"].endswith("/near/600001")
    assert "#narrow/channel/113486-General.20announcements/topic/" in items[0]["url"]
    assert items[0]["source_text"] == "Lean v4.37.0 is out. Thanks to Person 3 and Person 4 for the release notes."
    assert items[1]["authors"] == ["Person 5"]


def test_zulip_discussions_keep_busy_new_topics():
    src = CFG.source("lean-zulip-discussions")
    items = parse_fixture(src, fixture_path("lean-zulip-discussions").read_bytes(), NOW, KEYWORDS)
    # Left out: "Old busy topic" (started 72 hours ago), "grind fails with simp" (11 messages
    # but 3 people) and "Quiet question" (3 messages).
    assert [i["title"] for i in items] == ["VNP != VP in Lean", "Solved a 20-year-old problem"]
    assert items[0]["source_text"].startswith("Discussed on the Lean Zulip in #general: 12 messages from 6 people. A Lean")
    assert items[0]["published"] == "2026-11-22T07:17:00Z"


class _FakeZulip:
    """Answers GET /messages like the Zulip API: a channel's newest messages, or a topic's first."""

    def __init__(self, channels, firsts, failing=()):
        self.channels, self.firsts, self.failing, self.requests = channels, firsts, set(failing), []

    def get(self, url, params=None, conditional=True, auth=None):
        import json
        from types import SimpleNamespace

        narrow = {n["operator"]: n["operand"] for n in json.loads(params["narrow"])}
        self.requests.append(narrow)
        if narrow["channel"] in self.failing:
            raise FetchError("HTTP 400 from " + url, http_status=400)
        if "topic" in narrow:
            msgs = [self.firsts[narrow["topic"]]] if narrow["topic"] in self.firsts else []
        else:
            msgs = self.channels.get(narrow["channel"], [])
        return SimpleNamespace(status=200, content=json.dumps({"result": "success", "messages": msgs}).encode())


def test_zulip_fetch_checks_where_each_topic_started(monkeypatch):
    """A channel's newest messages can begin in the middle of an old topic; its first visible
    message is then a reply. The fetcher asks for each qualifying topic's real first message."""
    from news.sources import FetchContext, zulip

    monkeypatch.setenv("ZULIP_EMAIL", "bot@example.org")
    monkeypatch.setenv("ZULIP_API_KEY", "k")
    ts = int(NOW.timestamp())

    def m(i, topic, hours_ago, sender):
        return {"id": i, "subject": topic, "timestamp": ts - hours_ago * 3600, "sender_id": sender,
                "sender_full_name": f"P{sender}", "stream_id": 1, "display_recipient": "general", "content": f"m{i}"}

    revived = [m(50 + k, "Revived old topic", 5 - k * 0.1, k % 6) for k in range(12)]
    fresh = [m(80 + k, "Fresh topic", 3 - k * 0.1, k % 6) for k in range(12)]
    http = _FakeZulip({"general": revived + fresh, "AI authored projects": []},
                      firsts={"Revived old topic": m(7, "Revived old topic", 24 * 400, 1), "Fresh topic": fresh[0]},
                      failing=["Autoformalization"])
    src = CFG.source("lean-zulip-discussions")
    result = zulip.fetch(src, FetchContext(http=http, now=NOW, keywords=KEYWORDS))
    assert [i["title"] for i in result.items] == ["Fresh topic"]
    assert result.warnings and "Autoformalization" in result.warnings[0]
    checked = [r["topic"] for r in http.requests if "topic" in r]
    assert sorted(checked) == ["Fresh topic", "Revived old topic"]


def test_zulip_fetch_fails_only_if_every_channel_fails(monkeypatch):
    from news.sources import FetchContext, zulip

    monkeypatch.setenv("ZULIP_EMAIL", "bot@example.org")
    monkeypatch.setenv("ZULIP_API_KEY", "k")
    src = CFG.source("lean-zulip")
    http = _FakeZulip({}, {}, failing=zulip.channels_of(src))
    with pytest.raises(FetchError, match="every Zulip channel failed"):
        zulip.fetch(src, FetchContext(http=http, now=NOW, keywords=KEYWORDS))


def test_feed_pages_dated_year_1_never_count_as_new():
    """Forsyte's feed lists its static pages with the date 1 Jan 0001; the age window drops them."""
    from datetime import datetime, timezone

    from news.fetch import within_age

    src = CFG.source("forsyte")
    items = parse_fixture(src, fixture_path("forsyte").read_bytes(), NOW, KEYWORDS)
    assert sum(i["published"].startswith("0001") for i in items) == 2
    recent = within_age(src, items, datetime(2026, 9, 10, tzinfo=timezone.utc), 30)
    assert recent and not any(i["published"].startswith("0001") for i in recent)
    assert recent[0]["title"].startswith("Daniela Kaufmann receives")


def test_feed_category_filter():
    import copy

    src = copy.deepcopy(CFG.source("rocq-discourse"))
    src.params["categories"] = ["Announcements"]
    items = feed.parse(src, fixture_path("rocq-discourse").read_bytes(), NOW)
    all_items = feed.parse(CFG.source("rocq-discourse"), fixture_path("rocq-discourse").read_bytes(), NOW)
    assert 0 < len(items) < len(all_items)


@pytest.mark.parametrize("content", [b"", b"<html><body>Service unavailable</body></html>", b"not xml at all {"])
def test_broken_feed_raises_fetch_error(content):
    with pytest.raises(FetchError):
        feed.parse(CFG.source("xena"), content, NOW)


@pytest.mark.parametrize("sid", ["lean4-releases", "hf-daily-papers", "lean-zulip"])
def test_broken_json_raises_fetch_error(sid):
    from .conftest import PARSERS

    src = CFG.source(sid)
    with pytest.raises(FetchError):
        PARSERS[src.kind](src, b"<html>oops</html>", NOW)
