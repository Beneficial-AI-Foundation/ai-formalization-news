"""One module per kind of source. Each has `fetch(source, ctx)` and `parse(...)`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from news.config import Source
from news.keywords import Keyword
from news.net import HttpClient


@dataclass
class FetchContext:
    http: HttpClient
    now: datetime
    keywords: list[Keyword]
    state: dict[str, Any] = field(default_factory=dict)  # this source's saved state
    conditional: bool = True  # send If-None-Match and If-Modified-Since
    deadline: float | None = None  # time.monotonic() value after which no new request starts

    def out_of_time(self) -> bool:
        import time

        return self.deadline is not None and time.monotonic() >= self.deadline


@dataclass
class SourceResult:
    items: list[dict[str, Any]]
    http_status: int | None = None
    not_modified: bool = False
    warnings: list[str] = field(default_factory=list)


def fetcher_for(kind: str) -> Callable[[Source, FetchContext], SourceResult]:
    from news.sources import arxiv, arxiv_rss, feed, github_releases, github_repos, hackernews, hf_papers, zulip

    return {
        "feed": feed.fetch,
        "arxiv": arxiv.fetch,
        "arxiv_rss": arxiv_rss.fetch,
        "github_releases": github_releases.fetch,
        "github_repos": github_repos.fetch,
        "hackernews": hackernews.fetch,
        "hf_papers": hf_papers.fetch,
        "zulip": zulip.fetch,
    }[kind]
