"""New Lean repositories of AI labs, found with GitHub's repository search (decision D39)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from news.config import ConfigError, load_config
from news.fetch import within_age
from news.keywords import compile_keywords
from news.sources import FetchContext, github_repos

from .conftest import NOW, fixture_path

CFG = load_config()
SRC = CFG.source("lab-lean-repos")


def _items(now=NOW):
    return github_repos.parse(SRC, fixture_path("lab-lean-repos").read_bytes(), now)


def test_one_item_per_repository():
    items = _items()
    titles = [i["title"] for i in items]
    assert len(items) == 56 == len(set(titles))
    mordell = next(i for i in items if i["title"].startswith("AxiomMath/Mordell: "))
    assert mordell["url"] == "https://github.com/AxiomMath/Mordell"
    assert mordell["authors"] == ["AxiomMath"]
    assert mordell["published"].startswith("2026-11-11T")
    assert "Faltings" in mordell["source_text"] and "(GitHub repository, created 11 Nov 2026)" in mordell["source_text"]
    bare = next(i for i in items if i["title"] == "AxiomMath/ClusterAlgebras")  # no description, no README here
    assert bare["source_text"] == "A new Lean repository on GitHub, created 03 Nov 2026."


def test_the_age_window_keeps_the_last_30_days():
    recent = within_age(SRC, _items(), NOW, 30)
    names = {i["title"].split(":")[0] for i in recent}
    assert {"AxiomMath/Mordell", "AxiomMath/Weil", "google-deepmind/code-verification"} <= names
    assert "anthropics/formal-math" not in names  # created 6 Aug 2026
    assert all(i["published"] >= "2026-10-24" for i in recent)


def test_publication_date_rule():
    start = date(2026, 11, 24)
    now = datetime(2026, 12, 10, 12, 0, tzinfo=timezone.utc)
    before_start = datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert github_repos.published_at(before_start, now, start) == before_start  # the first run's backfill
    fresh = now - timedelta(hours=30)
    assert github_repos.published_at(fresh, now, start) == fresh  # found within 2 days of creation
    private_until_now = datetime(2026, 11, 28, tzinfo=timezone.utc)
    assert github_repos.published_at(private_until_now, now, start) == now  # made public later
    assert github_repos.published_at(private_until_now, now, None) == private_until_now


def test_readme_summary_takes_the_first_prose_paragraph():
    readme = """# Cluster algebras

[![CI](https://x/badge.svg)](https://x)

```lean
theorem foo : True := trivial
```

This is a Lean formalization of chapters 1 to 7 of
[Fomin, Williams and Zelevinsky](https://arxiv.org/abs/1608.05735).

## Building
"""
    assert github_repos.readme_summary(readme) == (
        "This is a Lean formalization of chapters 1 to 7 of Fomin, Williams and Zelevinsky.")
    assert github_repos.readme_summary("# Title only\n") == ""


class _FakeGitHub:
    def __init__(self, repos, readmes):
        self.repos, self.readmes, self.calls = repos, readmes, []

    def get(self, url, params=None, headers=None, conditional=True, auth=None):
        self.calls.append((url, params))
        if url.endswith("/readme"):
            name = url.split("/repos/")[1].rsplit("/readme", 1)[0]
            return SimpleNamespace(status=200, content=self.readmes[name].encode())
        orgs = {t[4:] for t in params["q"].split() if t.startswith("org:")}
        found = [r for r in self.repos if r["owner"]["login"] in orgs]
        return SimpleNamespace(status=200, content=json.dumps({"items": found}).encode())


def test_fetch_queries_and_readme_lookups():
    repos = json.loads(fixture_path("lab-lean-repos").read_bytes())["items"]
    readmes = {"AxiomMath/ClusterAlgebras": "# CA\n\nA formalization of cluster algebras.\n"}
    http = _FakeGitHub(repos, readmes)
    result = github_repos.fetch(SRC, FetchContext(http=http, now=NOW, keywords=compile_keywords([])))
    searches = [p for u, p in http.calls if not u.endswith("/readme")]
    assert len(searches) == 2  # 12 organizations, 6 per query
    for p in searches:
        assert len(p["q"]) <= 256 and 'language:"Lean"' in p["q"] and "created:>=2026-07-26" in p["q"]
    # Only repositories that will show (created within 30 days) and lack a description get a README lookup.
    looked_up = [u.split("/repos/")[1].rsplit("/readme", 1)[0] for u, _ in http.calls if u.endswith("/readme")]
    assert looked_up == ["AxiomMath/ClusterAlgebras"]
    ca = next(i for i in result.items if i["title"] == "AxiomMath/ClusterAlgebras")
    assert ca["source_text"].startswith("A formalization of cluster algebras. (GitHub repository")
    assert len(result.items) == 56


def test_config_checks_github_repos(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("sources:\n  - {id: r, name: R, kind: github_repos, type: paper}\n")
    with pytest.raises(ConfigError, match="orgs"):
        load_config(p)
    p.write_text("sources:\n  - {id: r, name: R, kind: github_repos, type: paper, orgs: [a], watch_start: soon}\n")
    with pytest.raises(ConfigError, match="watch_start"):
        load_config(p)
