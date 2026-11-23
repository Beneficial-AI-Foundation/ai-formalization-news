"""Read and write the files under data/: archive, news.json, search.json, meta.json."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema

from news.config import ROOT
from news.util import parse_iso

SCHEMA_PATH = ROOT / "schema" / "news.schema.json"
NEWS_LIMIT = 300
SEARCH_TEXT_LIMIT = 300
SEARCH_FIELDS = ["id", "type", "source", "title", "url", "published", "text", "authors", "source_id"]


class SchemaError(ValueError):
    """An item file failed the schema check; nothing was written."""


def _sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return (item["published"], item["id"])


def sort_newest_first(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=_sort_key, reverse=True)


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def write_if_changed(path: Path, text: str) -> bool:
    """Write only when the content differs, so unchanged files keep a clean git diff."""
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return True


class Validator:
    def __init__(self, schema_path: Path = SCHEMA_PATH):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self._v = jsonschema.Draft202012Validator(schema)
        item_schema = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/item"}
        self._item = jsonschema.Draft202012Validator(item_schema)

    def item_error(self, item: dict[str, Any]) -> str | None:
        """First schema error of one item, or None if it is valid."""
        err = next(iter(self._item.iter_errors(item)), None)
        if err is None:
            return None
        where = "/".join(str(p) for p in err.path) or "item"
        return f"{where}: {err.message}"[:200]

    def check(self, doc: dict[str, Any], label: str) -> None:
        errors = sorted(self._v.iter_errors(doc), key=lambda e: list(e.path))
        if errors:
            first = errors[0]
            where = "/".join(str(p) for p in first.path)
            raise SchemaError(f"{label}: {len(errors)} schema error(s); first at {where}: {first.message}")


class Archive:
    """Every kept item, one file per month of `published`, never trimmed."""

    def __init__(self, data_dir: Path):
        self.dir = data_dir / "archive"
        self.months: dict[str, dict[str, dict[str, Any]]] = {}
        self.dirty: set[str] = set()
        if self.dir.exists():
            for f in sorted(self.dir.glob("*.json")):
                doc = json.loads(f.read_text(encoding="utf-8"))
                self.months[f.stem] = {i["id"]: i for i in doc.get("items", [])}

    @staticmethod
    def month_of(item: dict[str, Any]) -> str:
        return item["published"][:7]

    def get(self, item_id: str) -> dict[str, Any] | None:
        for items in self.months.values():
            if item_id in items:
                return items[item_id]
        return None

    def put(self, item: dict[str, Any]) -> None:
        """Add a new item or replace an existing one with the same id."""
        self.remove(item["id"])
        month = self.month_of(item)
        self.months.setdefault(month, {})[item["id"]] = item
        self.dirty.add(month)

    def remove(self, item_id: str) -> bool:
        for month, items in self.months.items():
            if item_id in items:
                del items[item_id]
                self.dirty.add(month)
                return True
        return False

    def all_items(self) -> list[dict[str, Any]]:
        return sort_newest_first([i for items in self.months.values() for i in items.values()])

    def pending(self, now: datetime, retry_days: int) -> list[dict[str, Any]]:
        """Items Claude has not judged yet that are still young enough to retry."""
        cut = now - timedelta(days=retry_days)
        return [i for i in self.all_items() if i.get("relevant") is None and (parse_iso(i["fetched"]) or now) >= cut]

    def documents(self) -> dict[str, dict[str, Any]]:
        return {
            month: {"version": 1, "month": month, "items": sort_newest_first(list(items.values()))}
            for month, items in sorted(self.months.items())
        }


def news_document(archive_items: list[dict[str, Any]]) -> dict[str, Any]:
    items = sort_newest_first(archive_items)[:NEWS_LIMIT]
    # "updated" is the newest fetch time among the items, not the run time, so a
    # run that finds nothing new leaves news.json byte-for-byte unchanged.
    updated = max((i["fetched"] for i in items), default=None)
    return {"version": 1, "updated": updated, "items": items}


def search_document(archive_items: list[dict[str, Any]], synonyms: list[list[str]]) -> dict[str, Any]:
    rows = []
    for i in sort_newest_first(archive_items):
        text = (i.get("summary") or i.get("source_text") or "")[:SEARCH_TEXT_LIMIT]
        authors = ", ".join(i.get("authors", [])[:5])
        rows.append([i["id"], i["type"], i["source"], i["title"], i["url"], i["published"], text, authors, i["source_id"]])
    return {"version": 1, "fields": SEARCH_FIELDS, "rows": rows, "synonyms": synonyms}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))
