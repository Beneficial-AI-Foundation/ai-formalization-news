"""One hourly run: fetch, dedup, filter, optionally ask Claude, and write data/.

    python -m news.run                    # update data/
    python -m news.run --data-dir DIR     # what the workflow runs, on the data branch checkout
    python -m news.run --no-claude        # keyword filter only
    python -m news.run --ignore-schedule  # fetch every enabled source now
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from news import claude
from news.config import ROOT, Config, load_config, load_synonyms
from news.dedup import SeenStore, title_key
from news.feed_xml import build_atom
from news.fetch import DEFAULT_MAX_AGE_DAYS, fetch_sources
from news.keywords import compile_keywords, item_matches
from news.net import HttpClient
from news.store import (
    Archive,
    Validator,
    dump_json,
    hidden_document,
    load_json,
    news_document,
    search_document,
    write_if_changed,
)
from news.util import iso, utcnow

DATA_DIR = ROOT / "data"


def run(
    cfg: Config,
    *,
    data_dir: Path = DATA_DIR,
    http: HttpClient | None = None,
    now: datetime | None = None,
    use_claude: bool | None = None,
    claude_client: Any | None = None,
    ignore_schedule: bool = False,
    synonyms: list[list[str]] | None = None,
    fetcher: Any | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    t0 = time.monotonic()
    now = now or utcnow()
    cache_path = data_dir / "http_cache.json"
    cache = load_json(cache_path, {"version": 1, "validators": {}, "sources": {}})
    http = http or HttpClient(cache.setdefault("validators", {}))
    synonyms = load_synonyms() if synonyms is None else synonyms
    use_claude = claude.enabled() if use_claude is None else use_claude

    # 1. Fetch.
    kwargs: dict[str, Any] = {"ignore_schedule": ignore_schedule, "log": log, "max_age_days": DEFAULT_MAX_AGE_DAYS}
    if fetcher is not None:
        kwargs["fetcher"] = fetcher
    reports = fetch_sources(cfg, http, cache, now, **kwargs)

    # 2. Dedup and keyword filter. Links were normalised and ids set in the fetch stage.
    seen = SeenStore.load(data_dir / "seen.json")
    archive = Archive(data_dir)
    keywords = compile_keywords(cfg.keywords)
    retry_days = cfg.claude.retry_days

    def is_trusted(item: dict[str, Any]) -> bool:
        """Sources marked `trusted: true` are on topic by design: Claude writes the
        summary, type and score for their items but never hides them."""
        try:
            return bool(cfg.source(item["source_id"]).param("trusted", False))
        except KeyError:  # the source was removed from the config
            return False

    def waiting() -> list[dict[str, Any]]:
        """Items still waiting for Claude: unjudged, young enough, never refused."""
        return [i for i in archive.pending(now, retry_days) if i["id"] not in seen.refused]

    # Titles of items still waiting for Claude: a later item with the same title is
    # held back as an alternative instead of being dropped, in case Claude rejects
    # the first copy.
    waiting_titles: dict[str, str] = {}
    if use_claude:
        for i in waiting():
            k = title_key(i["title"])
            if k:
                waiting_titles.setdefault(k, i["id"])
    batch_ids: set[str] = set()
    new_items: list[dict[str, Any]] = []
    origin: dict[str, str] = {}  # item id -> source id, for per-source counts
    rejected = 0
    for rep in reports:
        src = cfg.source(rep.id)
        for item in rep.items:
            if seen.has_id(item["id"]) or item["id"] in batch_ids or archive.get(item["id"]):
                continue
            tkey = title_key(item["title"])
            if seen.has_title(item["title"], now):
                seen.add(item["id"], now)  # same story as an item already kept
                continue
            batch_ids.add(item["id"])
            rep.new += 1
            if src.filter == "keywords" and not item_matches(item, keywords):
                seen.add(item["id"], now)
                rejected += 1
                continue
            if tkey and tkey in waiting_titles:
                seen.hold_alt(waiting_titles[tkey], item)  # same story as an item waiting for Claude
                seen.add(item["id"], now)
                continue
            if tkey:
                if use_claude:
                    waiting_titles[tkey] = item["id"]
                else:
                    seen.add_title(item["title"], now)  # kept now, so later copies are duplicates
            new_items.append(item)
            origin[item["id"]] = src.id

    for item in new_items:
        archive.put(item)
        seen.add(item["id"], now)

    # 3. Claude: judge new items and any earlier ones still waiting.
    claude_meta: dict[str, Any] = {"enabled": bool(use_claude), "model": None, "route": None, "input_tokens": 0, "output_tokens": 0,
                                   "requests": 0, "items_sent": 0, "judged": 0, "hidden": 0, "kept_trusted": 0, "released": 0,
                                   "refusals": [], "errors": []}
    if use_claude:
        queue = sorted(waiting(), key=lambda i: (i["fetched"], i["id"]))
        if queue:
            result = claude.judge(queue, cfg.claude, client=claude_client)
            claude_meta.update(result.usage())
            claude_meta.update(items_sent=min(len(queue), cfg.claude.max_items_per_run), refusals=result.refusals,
                               errors=result.errors, judged=len(result.judgements))
            log(f"[claude] {result.model} via {result.route}: {len(queue)} waiting, {len(result.judgements)} judged, "
                f"input {result.input_tokens} tokens, output {result.output_tokens} tokens, "
                f"{len(result.refusals)} refusals, {len(result.errors)} errors")
            for r in result.refusals:  # never sent again; the item stays with the keyword filter only
                seen.refused[r["id"]] = now.strftime("%Y-%m-%d")
            for item_id, j in result.judgements.items():
                item = archive.get(item_id)
                if item is None:
                    continue
                # Judged off topic, from a source that is not trusted: hide it instead of
                # deleting it. It stays in the archive (and hidden.json), so a wrong call
                # can be undone; the page, search and the feed leave it out.
                hide = not j.relevant and not is_trusted(item)
                item = dict(item)
                item.update(relevant=j.relevant, score=j.score, summary=j.summary)
                if item["type"] != "social":  # the Social button keeps every post
                    item["type"] = j.type
                if hide:
                    item["hidden"] = True
                archive.put(item)
                if hide:
                    claude_meta["hidden"] += 1
                    alts = seen.alts.pop(item_id, [])
                    if alts:  # give the next copy of the same story its own judgement on a later run
                        nxt = dict(alts[0], fetched=iso(now))
                        archive.put(nxt)
                        if alts[1:]:
                            seen.alts[nxt["id"]] = alts[1:]
                        claude_meta["released"] += 1
                    continue
                if not j.relevant:
                    claude_meta["kept_trusted"] += 1  # on topic by design: shown, marked not relevant
                seen.add_title(item["title"], now)
                seen.alts.pop(item_id, None)  # its copies were duplicates after all
        else:
            log("[claude] no items waiting; no request sent")
            claude_meta.update(model=claude.model_name(cfg.claude), route=claude.api_route())

    for rep in reports:
        rep.kept = sum(1 for i, s in origin.items() if s == rep.id and (archive.get(i) or {"hidden": True}).get("hidden") is not True)

    # 4. Build every output, check the schema, then write.
    validator = Validator()
    all_items = archive.all_items()
    news_doc = news_document(all_items)
    validator.check(news_doc, "news.json")
    hidden_doc = hidden_document(all_items)
    validator.check(hidden_doc, "hidden.json")
    archive_docs = archive.documents()
    for month in archive.dirty:
        validator.check(archive_docs[month], f"archive/{month}.json")
    search_doc = search_document(all_items, synonyms)
    seen.prune(now, waiting_ids={i['id'] for i in waiting()} if use_claude else set())

    errors = [{"source": r.id, "error": r.error} for r in reports if r.status == "error"]
    if claude_meta["errors"]:
        errors.append({"source": "claude", "error": "; ".join(claude_meta["errors"])[:300]})
    meta = {
        "version": 1,
        "last_run": iso(now),
        "duration_seconds": round(time.monotonic() - t0, 1),
        "counts": {
            "items_in_news": len(news_doc["items"]),
            "items_in_hidden": len(hidden_doc["items"]),
            "archive_total": len(all_items),
            "new_kept": sum(r.kept for r in reports),
            "new_rejected": rejected,
            "new_hidden": claude_meta["hidden"],
        },
        "sources": [r.to_json() for r in reports],
        "errors": errors,
        "claude": claude_meta,
    }

    changed = []
    for month in sorted(archive.dirty):
        if write_if_changed(data_dir / "archive" / f"{month}.json", dump_json(archive_docs[month])):
            changed.append(f"archive/{month}.json")
    for name, doc in (("news.json", news_doc), ("hidden.json", hidden_doc), ("search.json", search_doc),
                      ("seen.json", seen.to_json())):
        if write_if_changed(data_dir / name, dump_json(doc)):
            changed.append(name)
    if write_if_changed(data_dir / "feed.xml", build_atom(news_doc["items"], cfg.site)):
        changed.append("feed.xml")
    write_if_changed(data_dir / "meta.json", dump_json(meta))
    write_if_changed(cache_path, json.dumps(cache, indent=1, sort_keys=True) + "\n")

    log(f"[done] {meta['counts']['new_kept']} new items kept, {meta['counts']['new_rejected']} rejected by keywords, "
        f"{meta['counts']['new_hidden']} hidden by Claude, "
        f"{len(errors)} source errors; changed: {', '.join(changed) or 'nothing'}")
    meta["_changed"] = changed  # for tests; not written
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-claude", action="store_true", help="skip the Claude step even when a key exists")
    ap.add_argument("--ignore-schedule", action="store_true", help="fetch every enabled source, due or not")
    ap.add_argument("--config", default=None)
    ap.add_argument("--data-dir", default=str(DATA_DIR), help="where the data files live (default: data/)")
    args = ap.parse_args(argv)
    cfg = load_config(args.config) if args.config else load_config()
    run(
        cfg,
        data_dir=Path(args.data_dir),
        use_claude=False if args.no_claude else None,
        ignore_schedule=args.ignore_schedule,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
