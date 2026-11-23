"""Fetch every enabled source that is due. A failing source never stops the others.

    python -m news.fetch --dry-run              # every enabled source, print status and items
    python -m news.fetch --dry-run --source xena
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from news.config import Config, Source, load_config
from news.dedup import make_id, normalize_url
from news.keywords import compile_keywords
from news.net import FetchError, HttpClient
from news.sources import FetchContext, SourceResult, fetcher_for
from news.store import Validator
from news.util import iso, parse_iso, utcnow

US_EASTERN = ZoneInfo("America/New_York")
# A little under the nominal interval, because scheduled runs start a few minutes late.
INTERVALS = {"1h": timedelta(minutes=50), "6h": timedelta(hours=5, minutes=50)}
# arXiv posts new papers around midnight US Eastern; an hour of margin avoids racing it.
DAILY_HOUR_EASTERN = 1
DEFAULT_MAX_AGE_DAYS = 30
# No new source starts after this many seconds, so the run keeps time to save and deploy.
FETCH_BUDGET_SECONDS = 150


@dataclass
class SourceReport:
    id: str
    name: str
    type: str
    status: str = "skipped"  # ok, not_modified, skipped, error
    http_status: int | None = None
    fetched: int = 0
    new: int = 0
    kept: int = 0
    error: str | None = None
    warning: str | None = None
    last_fetch: str | None = None
    items: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "enabled": True,
            "status": self.status,
            "http_status": self.http_status,
            "fetched": self.fetched,
            "new": self.new,
            "kept": self.kept,
            "error": self.error,
            "warning": self.warning,
            "last_fetch": self.last_fetch,
        }


def is_due(source: Source, last_fetch: str | None, now: datetime) -> bool:
    last = parse_iso(last_fetch)
    if last is None:
        return True
    if source.every == "daily":
        # Due once per day, on the first run at or after 01:00 US Eastern.
        now_et = now.astimezone(US_EASTERN)
        boundary = now_et.replace(hour=DAILY_HOUR_EASTERN, minute=0, second=0, microsecond=0)
        if now_et < boundary:
            boundary -= timedelta(days=1)
        return last.astimezone(US_EASTERN) < boundary
    return now - last >= INTERVALS[source.every]


def within_age(source: Source, items: list[dict[str, Any]], now: datetime, max_age_days: int) -> list[dict[str, Any]]:
    """Drop items published longer ago than `max_age_days` (some feeds return their
    whole history: the Archive of Formal Proofs feed has every entry ever).

    There is deliberately no count cap here: a cap applied before dedup would hide
    the same overflow items on every later run, so they would never be seen.
    """
    cut = iso(now - timedelta(days=int(source.param("max_age_days", max_age_days))))
    return sorted((i for i in items if i["published"] >= cut), key=lambda i: i["published"], reverse=True)


def prepare_items(items: list[dict[str, Any]], validator: Validator) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalise each link, set the id, and check the item against the schema.
    A malformed item is dropped on its own; the rest of the source still counts."""
    good, problems = [], []
    for item in items:
        try:
            item["url"] = normalize_url(item["url"])
            item["id"] = make_id(item["url"])
        except (ValueError, KeyError, TypeError) as e:
            problems.append(f"bad link {str(item.get('url'))[:80]!r}: {e}")
            continue
        err = validator.item_error(item)
        if err:
            problems.append(f"{item.get('url', '')[:80]}: {err}")
            continue
        good.append(item)
    return good, problems


def fetch_sources(
    cfg: Config,
    http: HttpClient,
    state: dict[str, Any],
    now: datetime,
    *,
    ignore_schedule: bool = False,
    conditional: bool = True,
    only: set[str] | None = None,
    fetcher: Callable[[str], Callable[[Source, FetchContext], SourceResult]] = fetcher_for,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    budget_seconds: float | None = FETCH_BUDGET_SECONDS,
    log: Callable[[str], None] = print,
) -> list[SourceReport]:
    """Fetch due sources. `state["sources"][id]["last_fetch"]` is updated on success."""
    keywords = compile_keywords(cfg.keywords)
    validator = Validator()
    source_state = state.setdefault("sources", {})
    deadline = time.monotonic() + budget_seconds if budget_seconds else None
    reports = []
    for src in cfg.enabled_sources:
        if only and src.id not in only:
            continue
        st = source_state.setdefault(src.id, {})
        rep = SourceReport(id=src.id, name=src.name, type=src.type, last_fetch=st.get("last_fetch"))
        reports.append(rep)
        if not ignore_schedule and not is_due(src, st.get("last_fetch"), now):
            log(f"[skip] {src.id}: not due (every {src.every}, last {st.get('last_fetch')})")
            continue
        if deadline is not None and time.monotonic() >= deadline:
            rep.warning = "time budget used up; fetched on the next run"
            log(f"[skip] {src.id}: {rep.warning}")
            continue
        ctx = FetchContext(http=http, now=now, keywords=keywords, state=st, conditional=conditional, deadline=deadline)
        try:
            result = fetcher(src.kind)(src, ctx)
            items, problems = prepare_items(within_age(src, result.items, now, max_age_days), validator)
            rep.items = items
            rep.fetched = len(items)
            rep.http_status = result.http_status
            rep.status = "not_modified" if result.not_modified else "ok"
            notes = list(result.warnings)
            if problems:
                notes.append(f"{len(problems)} malformed item(s) skipped, first: {problems[0]}")
            rep.warning = "; ".join(notes)[:300] or None
            http.commit()
            st["last_fetch"] = iso(now)
            rep.last_fetch = st["last_fetch"]
            log(f"[{rep.status}] {src.id}: HTTP {rep.http_status}, {len(result.items)} items parsed, "
                f"{rep.fetched} kept for dedup" + (f" ({rep.warning})" if rep.warning else ""))
        except FetchError as e:
            http.discard()
            rep.status, rep.error, rep.http_status = "error", str(e)[:300], e.http_status
            log(f"[error] {src.id}: {rep.error}")
        except Exception as e:  # a parser bug in one source must not stop the others
            http.discard()
            rep.status, rep.error = "error", f"{type(e).__name__}: {e}"[:300]
            log(f"[error] {src.id}: {rep.error}")
            traceback.print_exc(file=sys.stderr)
    return reports


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="fetch and print only; never write (the only mode)")
    ap.add_argument("--source", action="append", help="limit to these source ids")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    cfg = load_config(args.config) if args.config else load_config()
    now = utcnow()
    reports = fetch_sources(cfg, HttpClient(), {}, now, ignore_schedule=True, conditional=False,
                            only=set(args.source or []) or None, budget_seconds=None)
    print()
    empty = 0
    for rep in reports:
        first = rep.items[0]["title"][:90] if rep.items else "-"
        print(f"{rep.id:32} {rep.status:12} HTTP {str(rep.http_status):4} {rep.fetched:3} items  first: {first}")
        if rep.status == "error" or not rep.items:
            empty += 1
    print(f"\n{len(reports)} enabled sources, {len(reports) - empty} returned items, {empty} returned none or failed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
