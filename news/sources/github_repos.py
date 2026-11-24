"""New repositories in a list of GitHub organizations, found with GitHub's repository search,
for example the Lean repositories of AI labs. One item per repository.

Labs often build a repository in private and make it public on the day of the announcement,
so its creation date can be weeks before anyone could see it. GitHub does not say when a
repository became public. The rule here:
- A repository created before `watch_start` (the day this source was switched on) is dated by
  its creation; only the first run sees these, and the 30-day window keeps the recent ones.
- After that, a repository first found within 2 days of its creation is dated by its creation;
  one found later (it was private, or had no Lean code yet) is dated by the run that finds it,
  which is within the hour of it appearing.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from news.config import Source
from news.net import FetchError, github_headers
from news.sources import FetchContext, SourceResult
from news.util import make_item, parse_iso

SEARCH_URL = "https://api.github.com/search/repositories"
ORGS_PER_QUERY = 6  # GitHub limits a search query to 256 characters
NEW_FOR = timedelta(days=2)
README_LIMIT = 5  # README requests per run, for repositories without a description


def fetch(source: Source, ctx: FetchContext) -> SourceResult:
    orgs = [str(o) for o in source.param("orgs", [])]
    since = (ctx.now - timedelta(days=int(source.param("search_days", 120)))).date().isoformat()
    language = source.param("language")
    repos: list[dict[str, Any]] = []
    status = None
    for k in range(0, len(orgs), ORGS_PER_QUERY):
        terms = ([f'language:"{language}"'] if language else []) + [f"created:>={since}"]
        terms += [f"org:{o}" for o in orgs[k:k + ORGS_PER_QUERY]]
        params = {"q": " ".join(terms), "sort": "created", "order": "desc", "per_page": 100}
        resp = ctx.http.get(SEARCH_URL, params=params, headers=github_headers(), conditional=False)
        status = resp.status
        repos += _repos(resp.content)
    readmes = {}
    start = source.param("watch_start")
    watch_start = date.fromisoformat(str(start)) if start else None
    max_age = timedelta(days=int(source.param("max_age_days", 30)))

    def shown(r: dict[str, Any]) -> bool:  # within the age window, so worth a README lookup
        created = parse_iso(r.get("created_at"))
        return created is not None and ctx.now - published_at(created, ctx.now, watch_start) <= max_age

    for r in [r for r in repos if not (r.get("description") or "").strip() and shown(r)][:README_LIMIT]:
        try:
            resp = ctx.http.get(f"https://api.github.com/repos/{r['full_name']}/readme",
                                headers={**github_headers(), "Accept": "application/vnd.github.raw+json"},
                                conditional=False)
            readmes[r["full_name"]] = readme_summary(resp.content.decode("utf-8", "replace"))
        except FetchError:
            pass  # no README: the item has its name only
    return SourceResult(items=select(source, repos, ctx.now, readmes), http_status=status)


def _repos(content: bytes) -> list[dict[str, Any]]:
    try:
        data = json.loads(content)
    except ValueError as e:
        raise FetchError(f"GitHub search response is not JSON: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise FetchError("GitHub search response has no items list")
    return data["items"]


def readme_summary(text: str, limit: int = 500) -> str:
    """The first paragraph of a README that is prose, not a heading, badge, image or code."""
    in_code = False
    for block in re.split(r"\n\s*\n", text):
        lines = []
        for line in block.strip().splitlines():
            s = line.strip()
            if s.startswith("```"):
                in_code = not in_code
                continue
            if in_code or not s or s.startswith(("#", "![", "[![", "<", "|", "---")):
                continue
            lines.append(s)
        if lines:
            para = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", " ".join(lines))  # [text](link) -> text
            return para[:limit]
    return ""


def published_at(created: datetime, now: datetime, watch_start: date | None) -> datetime:
    if watch_start is None or created.date() < watch_start or now - created <= NEW_FOR:
        return created
    return now


def select(source: Source, repos: list[dict[str, Any]], now: datetime,
           readmes: dict[str, str] | None = None) -> list[dict[str, Any]]:
    readmes = readmes or {}
    start = source.param("watch_start")
    watch_start = date.fromisoformat(str(start)) if start else None
    items, seen = [], set()
    for r in repos:
        name = r.get("full_name") or ""
        created = parse_iso(r.get("created_at"))
        if not name or name in seen or not r.get("html_url") or created is None or r.get("fork"):
            continue
        seen.add(name)
        about = (r.get("description") or "").strip() or readmes.get(name, "")
        title = f"{name}: {(r.get('description') or '').strip()}" if (r.get("description") or "").strip() else name
        text = f"{about} (GitHub repository, created {created:%d %b %Y})." if about else \
            f"A new {r.get('language') or ''} repository on GitHub, created {created:%d %b %Y}.".replace("  ", " ")
        items.append(
            make_item(
                source_id=source.id,
                source_name=source.name,
                type_=source.type,
                title=title,
                url=r["html_url"],
                published=published_at(created, now, watch_start),
                fetched=now,
                authors=[r["owner"]["login"]] if (r.get("owner") or {}).get("login") else [],
                text=text,
            )
        )
    return items


def parse(source: Source, content: bytes, now: datetime) -> list[dict[str, Any]]:
    """Parse a saved search reply (no README lookups)."""
    return select(source, _repos(content), now)
