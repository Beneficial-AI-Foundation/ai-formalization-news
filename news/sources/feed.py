"""Generic RSS and Atom reader, used for blogs, newsletters, Bluesky and Mastodon."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import feedparser

from news.config import Source
from news.net import FetchError
from news.sources import FetchContext, SourceResult
from news.util import from_struct_time, make_item, strip_html, truncate

SOCIAL_TEXT_LIMIT = 300
SOCIAL_TITLE_LIMIT = 140


def fetch(source: Source, ctx: FetchContext) -> SourceResult:
    resp = ctx.http.get(source.url, conditional=ctx.conditional)
    if resp.not_modified:
        return SourceResult(items=[], http_status=304, not_modified=True)
    return SourceResult(items=parse(source, resp.content, ctx.now), http_status=resp.status)


def parse_feed(content: bytes) -> Any:
    """Parse bytes with feedparser; raise FetchError if nothing usable came back."""
    d = feedparser.parse(content)
    if d.bozo and not d.entries:
        raise FetchError(f"not a valid feed: {d.get('bozo_exception')!r}")
    if not d.entries and not d.get("feed", {}).get("title"):
        raise FetchError("feed has no entries and no title")
    return d


def entry_link(entry: Any) -> str | None:
    link = entry.get("link")
    if link and link.startswith("http"):
        return link
    for alt in entry.get("links") or []:
        href = alt.get("href", "")
        if href.startswith("http") and alt.get("rel", "alternate") == "alternate":
            return href
    guid = entry.get("id", "")
    if guid.startswith("http"):  # Hugging Face blog items carry the URL only in guid
        return guid
    return None


def entry_published(entry: Any) -> datetime | None:
    return from_struct_time(entry.get("published_parsed") or entry.get("updated_parsed"))


def entry_authors(entry: Any) -> list[str]:
    names = [a.get("name", "") for a in entry.get("authors") or [] if a.get("name")]
    if not names and entry.get("author"):
        names = [entry["author"]]
    return names


def entry_text(entry: Any) -> str:
    if entry.get("summary"):
        return strip_html(entry["summary"])
    for c in entry.get("content") or []:
        if c.get("value"):
            return strip_html(c["value"])
    return ""


def mastodon_author(link: str) -> str | None:
    """https://mathstodon.xyz/@user/123 gives @user@mathstodon.xyz. Some servers put a
    prefix before the account (/g/group/@user/posts/...), so search the whole path."""
    p = urlparse(link)
    m = re.search(r"/@([^/@]+)/", p.path)
    if m:
        return f"@{m.group(1)}@{p.netloc}"
    return None


def parse(source: Source, content: bytes, now: datetime) -> list[dict[str, Any]]:
    d = parse_feed(content)
    wanted_categories = {c.lower() for c in source.param("categories", []) or []}
    exclude = source.param("exclude_titles")
    excluded_title = re.compile(str(exclude), re.IGNORECASE) if exclude else None
    items = []
    for entry in d.entries:
        link = entry_link(entry)
        if not link:
            continue
        if excluded_title and excluded_title.search(entry.get("title") or ""):
            continue  # for example a merge commit, which repeats the commit before it
        if wanted_categories:
            terms = {t.get("term", "").lower() for t in entry.get("tags") or []}
            if not terms & wanted_categories:
                continue
        text = entry_text(entry)
        authors = entry_authors(entry)
        if source.type == "social":
            # Posts have no title: show the opening words, keep at most 300 characters,
            # credit the author and link back to the post.
            author = source.param("author") or (authors[0] if authors else None) or mastodon_author(link)
            title = truncate(text.split("\n")[0], SOCIAL_TITLE_LIMIT) if text else (entry.get("title") or "Post")
            items.append(
                make_item(
                    source_id=source.id,
                    source_name=source.name,
                    type_="social",
                    title=title,
                    url=link,
                    published=entry_published(entry),
                    fetched=now,
                    authors=[author] if author else [],
                    text=text,
                    text_limit=SOCIAL_TEXT_LIMIT,
                )
            )
            continue
        items.append(
            make_item(
                source_id=source.id,
                source_name=source.name,
                type_=source.type,
                title=entry.get("title") or truncate(text, SOCIAL_TITLE_LIMIT),
                url=link,
                published=entry_published(entry),
                fetched=now,
                authors=authors,
                text=text,
            )
        )
    return items
