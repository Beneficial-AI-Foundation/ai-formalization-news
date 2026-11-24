"""Keywords, URL normalisation, the seen store, scheduling, storage, Atom feed, HTTP client."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import feedparser
import pytest

from news.config import ConfigError, Source, load_config
from news.dedup import SeenStore, make_id, normalize_title, normalize_url, title_key
from news.feed_xml import build_atom
from news.fetch import fetch_sources, is_due, within_age
from news.keywords import compile_keywords, matched
from news.net import FetchError, HttpClient
from news.store import SchemaError, Validator, news_document, search_document
from news.util import make_item, truncate

from .conftest import NOW

KW = compile_keywords(load_config().keywords)


# ---------- keywords ----------

@pytest.mark.parametrize(
    "text",
    ["New Lean 4 release", "written in Lean4", "a lean-4 port", "Formal Verification of a kernel",
     "autoformalisation with LLMs", "Interactive theorem provers compared", "Coq's new name is Rocq",
     "#formalverification", "seL4 proofs", "Proof assistants in teaching",
     "Claude-Written Paper Claims VP ≠ VNP, With a Lean Proof", "github.com/anthropics/formal-math/tree/main/vp-vnp",
     "a Lean formalisation of the paper", "machine-checked proofs", "formal proofs of correctness",
     "AI for formal mathematics", "three million lines of proofs in Lean", "Introducing AlphaProof Code",
     "the theorem is formalized in Lean"],
)
def test_keywords_match(text):
    assert matched(text, KW), text


@pytest.mark.parametrize("text", ["Coquand and type theory", "lean manufacturing", "Isabella's garden", "a formal dinner",
                                  "lean--4", "foo - Lean - 4 - bar"])
def test_keywords_do_not_match(text):
    assert not matched(text, KW), text


def test_every_configured_keyword_matches_itself():
    # A keyword Jin adds to config/sources.yaml must compile and find its own text.
    for k in load_config().keywords:
        assert matched(k, compile_keywords([k])), k
    assert matched("verified in F* and C++", compile_keywords(["F*", "C++"])) == ["F*", "C++"]


def test_arxiv_rss_author_list_with_oxford_comma():
    from news.sources import arxiv_rss

    rss = b"""<?xml version="1.0"?><rss xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom" version="2.0"><channel><title>t</title>
<item><title>P</title><link>https://arxiv.org/abs/2611.00001</link><description>Abstract: x</description>
<pubDate>Mon, 23 Nov 2026 00:00:00 -0500</pubDate><arxiv:announce_type>new</arxiv:announce_type><dc:creator>Ann One, Bo Two, and Cy Three</dc:creator></item>
</channel></rss>"""
    items = arxiv_rss.parse(load_config().source("arxiv-rss"), rss, NOW)
    assert items[0]["authors"] == ["Ann One", "Bo Two", "Cy Three"]


# ---------- dedup ----------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://ArXiv.org/abs/2611.01234v3", "https://arxiv.org/abs/2611.01234"),
        ("http://arxiv.org/pdf/2611.01234v1.pdf", "https://arxiv.org/abs/2611.01234"),
        ("https://export.arxiv.org/abs/2611.01234", "https://arxiv.org/abs/2611.01234"),
        ("https://huggingface.co/papers/2611.01234", "https://arxiv.org/abs/2611.01234"),
        ("https://arxiv.org/abs/math/0601001v2", "https://arxiv.org/abs/math/0601001"),
        ("https://Example.ORG/post/?utm_source=x&id=3&utm_medium=y#frag", "https://example.org/post?id=3"),
        ("https://example.org:443/a/", "https://example.org/a"),
        ("https://example.org", "https://example.org/"),
    ],
)
def test_normalize_url(url, expected):
    assert normalize_url(url) == expected


def test_zulip_links_keep_their_message_fragment():
    a = "https://leanprover.zulipchat.com/#narrow/channel/113486-announce/topic/A/near/1"
    b = "https://leanprover.zulipchat.com/#narrow/channel/113486-announce/topic/B/near/2"
    assert normalize_url(a) == a and make_id(a) != make_id(b)


def test_ids():
    assert make_id("https://arxiv.org/pdf/2611.01234v2") == "arxiv:2611.01234"
    a = make_id("https://example.org/x?utm_source=feed")
    assert a == make_id("https://EXAMPLE.org/x#top") and a.startswith("u:") and len(a) == 18


def test_title_key():
    assert normalize_title("  Lean 4: A New Hope!! ") == "lean 4 a new hope"
    assert title_key("Proving Rust Correct with Lean 4") == title_key("proving rust correct, with LEAN 4.")
    assert title_key("v4.36.0") is None  # too short to compare safely


def test_seen_store_windows():
    s = SeenStore()
    s.add("u:1", NOW - timedelta(days=91))
    s.add("u:2", NOW - timedelta(days=10))
    s.add_title("A paper about formal verification of kernels", NOW - timedelta(days=8))
    s.add_title("Another paper about Lean and Mathlib", NOW - timedelta(days=2))
    assert not s.has_title("A paper about formal verification of kernels", NOW)  # older than 7 days
    assert s.has_title("another paper about lean and mathlib", NOW)
    s.prune(NOW)
    assert not s.has_id("u:1") and s.has_id("u:2")
    assert len(s.titles) == 1


# ---------- schedule and limits ----------

def _src(every):
    return Source(id="x", name="X", kind="feed", type="blog", url="https://e.org", enabled=True, every=every)


def test_is_due():
    assert is_due(_src("1h"), None, NOW)
    assert not is_due(_src("1h"), "2026-11-23T02:50:00Z", NOW)
    assert is_due(_src("1h"), "2026-11-23T02:17:00Z", NOW)
    assert not is_due(_src("6h"), "2026-11-22T23:17:00Z", NOW)
    assert is_due(_src("6h"), "2026-11-22T21:17:00Z", NOW)
    # daily: first run at or after 01:00 US Eastern. NOW is 22:17 on 22 Nov in New York.
    assert not is_due(_src("daily"), "2026-11-22T06:00:00Z", NOW)  # 01:00 on 22 Nov Eastern
    assert is_due(_src("daily"), "2026-11-22T05:30:00Z", NOW)  # 00:30 on 22 Nov: before the day's papers
    assert is_due(_src("daily"), "2026-11-22T04:00:00Z", NOW)  # 23:00 on 21 Nov Eastern
    early = datetime(2026, 11, 23, 5, 30, tzinfo=timezone.utc)  # 00:30 on 23 Nov Eastern
    assert not is_due(_src("daily"), "2026-11-22T06:00:00Z", early)


def test_within_age_keeps_every_recent_item():
    # No count cap: a cap before dedup would hide the same overflow items forever.
    src = _src("6h")
    items = [make_item(source_id="x", source_name="X", type_="blog", title=f"t{k}", url=f"https://e.org/{k}",
                       published=NOW - timedelta(hours=k * 12), fetched=NOW) for k in range(80)]
    kept = within_age(src, items, NOW, 30)
    assert len(kept) == 61 and kept[0]["title"] == "t0"  # 0 to 30 days in 12-hour steps


def test_fetch_budget_skips_remaining_sources(cfg):
    from .conftest import FixtureFetcher

    reports = fetch_sources(cfg, HttpClient({}), {}, NOW, fetcher=FixtureFetcher(), budget_seconds=1e-9, log=lambda s: None)
    assert reports and all(r.status == "skipped" and "time budget" in (r.warning or "") for r in reports)


# ---------- items, storage, schema ----------

def test_make_item_clamps_future_dates_and_truncates():
    i = make_item(source_id="x", source_name="X", type_="blog", title=" A\n title ", url="https://e.org",
                  published=NOW + timedelta(days=3), fetched=NOW, text="word " * 400)
    assert i["published"] == i["fetched"]
    assert i["title"] == "A title"
    assert len(i["source_text"]) <= 600 and i["source_text"].endswith("…")


def test_truncate():
    assert truncate("short", 160) == "short"
    t = truncate("alpha beta gamma delta " * 20, 50)
    assert len(t) <= 50 and t.endswith("…") and not t.endswith(" …")


def _item(k, **kw):
    i = make_item(source_id="x", source_name="X", type_="blog", title=f"Item {k}", url=f"https://e.org/{k}",
                  published=NOW - timedelta(hours=k), fetched=NOW - timedelta(hours=k))
    i["id"] = make_id(i["url"])
    i.update(kw)
    return i


def test_schema_rejects_bad_items():
    v = Validator()
    v.check({"version": 1, "items": [_item(1)]}, "ok")
    for bad in ({"type": "video"}, {"summary": "x" * 161}, {"score": 9}, {"url": "ftp://x"}, {"id": "nope"}):
        with pytest.raises(SchemaError):
            v.check({"version": 1, "items": [_item(1, **bad)]}, "bad")


def test_news_document_caps_and_is_stable():
    items = [_item(k) for k in range(320)]
    doc = news_document(items)
    assert len(doc["items"]) == 300
    assert doc["items"][0]["title"] == "Item 0"
    assert doc["updated"] == items[0]["fetched"]
    assert news_document(list(reversed(items))) == doc


def test_search_document_rows():
    doc = search_document([_item(1, summary="A summary", authors=["A", "B"])], [["coq", "rocq"]])
    assert doc["fields"] == ["id", "type", "source", "title", "url", "published", "text", "authors", "source_id"]
    row = dict(zip(doc["fields"], doc["rows"][0]))
    assert row["text"] == "A summary" and row["authors"] == "A, B" and row["source_id"] == "x"
    assert doc["synonyms"] == [["coq", "rocq"]]


def test_atom_feed_parses():
    items = [_item(k, summary="Sum <b>&</b>") for k in range(60)]
    xml = build_atom(items, {"url": "https://example.org/news/", "title": "AI and formalization news"})
    d = feedparser.parse(xml.encode())
    assert not d.bozo
    assert len(d.entries) == 50
    assert d.entries[0].link == "https://e.org/0"
    assert "Sum <b>&</b>" in d.entries[0].summary or "Sum &lt;b&gt;" in d.entries[0].summary


# ---------- config ----------

def test_config_rejects_bad_entries(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("sources:\n  - {id: a, name: A, kind: feed, type: video, url: 'https://e.org'}\n")
    with pytest.raises(ConfigError):
        load_config(p)
    p.write_text("sources:\n  - {id: a, name: A, kind: feed, type: blog}\n")
    with pytest.raises(ConfigError):
        load_config(p)
    p.write_text("sources:\n  - {id: a, name: A, kind: feed, type: blog, url: 'https://e.org', exclude_titles: '^(Merge'}\n")
    with pytest.raises(ConfigError, match="exclude_titles"):
        load_config(p)


# ---------- HTTP client ----------

class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def get(self, url, params=None, headers=None, timeout=None, auth=None):
        self.sent.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
        status, hdrs = self.responses.pop(0)
        return SimpleNamespace(url=url, status_code=status, content=b"<rss/>", headers=hdrs)


def test_http_conditional_requests_and_commit():
    sess = _FakeSession([(200, {"ETag": '"abc"', "Last-Modified": "Mon, 23 Nov 2026 01:00:00 GMT"}), (304, {})])
    client = HttpClient({}, session=sess)
    r = client.get("https://e.org/feed")
    assert r.status == 200 and sess.sent[0]["timeout"] == 20
    assert client.validators == {}  # nothing stored until the caller commits
    client.commit()
    r = client.get("https://e.org/feed")
    assert r.not_modified
    assert sess.sent[1]["headers"]["If-None-Match"] == '"abc"'
    assert sess.sent[1]["headers"]["If-Modified-Since"].startswith("Mon, 23 Nov 2026")


def test_http_discard_and_errors():
    sess = _FakeSession([(200, {"ETag": '"new"'}), (503, {})])
    client = HttpClient({}, session=sess)
    client.get("https://e.org/feed")
    client.discard()
    assert client.validators == {}
    with pytest.raises(FetchError) as e:
        client.get("https://e.org/feed")
    assert e.value.http_status == 503


def test_iso_pads_the_year():
    from datetime import datetime, timezone

    from news.util import iso

    assert iso(datetime(1, 1, 1, tzinfo=timezone.utc)) == "0001-01-01T00:00:00Z"
    assert iso(datetime(2026, 11, 24, 3, 5, 9, 123456, tzinfo=timezone.utc)) == "2026-11-24T03:05:09Z"
