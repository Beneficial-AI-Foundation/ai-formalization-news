"""Atom feed of the newest items, written to data/feed.xml and served as /feed.xml."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

ATOM_NS = "http://www.w3.org/2005/Atom"
FEED_LIMIT = 50


def build_atom(items: list[dict[str, Any]], site: dict[str, str]) -> str:
    ET.register_namespace("", ATOM_NS)

    def el(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
        e = ET.SubElement(parent, f"{{{ATOM_NS}}}{tag}", attrs)
        if text is not None:
            e.text = text
        return e

    site_url = site.get("url", "").rstrip("/") + "/"
    title = site.get("title", "AI and formalization news")
    newest = items[:FEED_LIMIT]
    feed = ET.Element(f"{{{ATOM_NS}}}feed")
    el(feed, "id", site_url)
    el(feed, "title", f"{title} (Beneficial AI Foundation)")
    el(feed, "subtitle", "New papers, Lean and Mathlib updates, and writing on AI for formal verification.")
    # Newest item time, not the run time, so an unchanged run leaves the file unchanged.
    el(feed, "updated", max((i["fetched"] for i in newest), default="1970-01-01T00:00:00Z"))
    el(feed, "link", href=site_url, rel="alternate", type="text/html")
    el(feed, "link", href=site_url + "feed.xml", rel="self", type="application/atom+xml")
    author = el(feed, "author")
    el(author, "name", "Beneficial AI Foundation")
    for i in newest:
        entry = el(feed, "entry")
        el(entry, "id", i["url"])
        el(entry, "title", i["title"])
        el(entry, "link", href=i["url"], rel="alternate")
        el(entry, "published", i["published"])
        el(entry, "updated", i["published"])
        for name in i.get("authors", [])[:5] or [i["source"]]:
            a = el(entry, "author")
            el(a, "name", name)
        el(entry, "category", term=i["type"])
        summary = i.get("summary") or i.get("source_text") or ""
        if summary:
            el(entry, "summary", f"{summary} ({i['source']})", type="text")
    ET.indent(feed, space=" ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(feed, encoding="unicode") + "\n"
