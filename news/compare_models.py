"""Phase 6 model check: judge the same items with two models and compare.

    python -m news.compare_models                       # newest 100 archived items, Haiku 5.5 and Sonnet 5.5
    python -m news.compare_models --since-hours 24      # the items first fetched in the last 24 hours
    python -m news.compare_models --limit 50 --out docs/model_comparison

Both models get the same system prompt, schema and settings as the hourly job
(see news/claude.py). Writes results.json (every judgement) and report.md
(agreement figures, cost, and a side-by-side table). Exits with status 1 unless
both models judged every item, so a failed comparison never looks complete.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from news import claude
from news.config import ROOT, load_config
from news.store import Archive

# US dollars per million tokens, prompts up to 100K tokens (Anthropic model table, November 2026).
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
RUNS_PER_MONTH = 720


def cost(model: str, input_tokens: int, output_tokens: int) -> float:
    if model not in PRICES:
        raise SystemExit(f"no price known for {model}; add it to PRICES in news/compare_models.py")
    pin, pout = PRICES[model]
    return input_tokens / 1e6 * pin + output_tokens / 1e6 * pout


def compare(items: list[dict[str, Any]], models: list[str]) -> dict[str, Any]:
    cfg = load_config()
    runs = {}
    for m in models:
        print(f"Judging {len(items)} items with {m} ...", flush=True)
        r = claude.judge(items, cfg.claude, model=m)
        runs[m] = r
        print(f"  {len(r.judgements)} judged, {r.input_tokens} in / {r.output_tokens} out tokens, "
              f"{r.seconds:.1f} s, {len(r.refusals)} refusals, {len(r.errors)} errors", flush=True)
    rows = []
    for i in items:
        row = {"id": i["id"], "source": i["source"], "title": i["title"], "url": i["url"],
               "source_type": i["type"], "text": i.get("source_text", "")}
        for m in models:
            j = runs[m].judgements.get(i["id"])
            row[m] = j.model_dump() if j else None
        rows.append(row)
    published = sorted(i["published"] for i in items)
    fetched = sorted(i["fetched"] for i in items)
    return {
        "settings": {"effort": cfg.claude.effort, "max_tokens": cfg.claude.max_tokens, "items": len(items),
                     "system_prompt_chars": len(claude.SYSTEM_PROMPT),
                     "published_from": published[0], "published_to": published[-1],
                     "fetched_from": fetched[0], "fetched_to": fetched[-1]},
        "usage": {m: {**runs[m].usage(), "refusals": runs[m].refusals, "errors": runs[m].errors,
                      "cost_usd": round(cost(m, runs[m].input_tokens, runs[m].output_tokens), 5)} for m in models},
        "rows": rows,
    }


def report(result: dict[str, Any], models: list[str]) -> str:
    a, b = models[0], models[1]
    rows = result["rows"]
    both = [r for r in rows if r[a] and r[b]]
    rel_agree = sum(r[a]["relevant"] == r[b]["relevant"] for r in both)
    type_agree = sum(r[a]["type"] == r[b]["type"] for r in both)
    score_diff = sum(abs(r[a]["score"] - r[b]["score"]) for r in both) / max(1, len(both))
    a_only = [r for r in both if r[a]["relevant"] and not r[b]["relevant"]]
    b_only = [r for r in both if r[b]["relevant"] and not r[a]["relevant"]]

    def esc(s: str) -> str:
        return str(s).replace("|", "\\|").replace("\n", " ")

    out = [f"# Model comparison: {a} and {b}", ""]
    s = result["settings"]
    out.append(f"{s['items']} items, same system prompt and schema, effort `{s['effort']}`, one request per model.")
    out.append(f"Items published {s['published_from'][:10]} to {s['published_to'][:10]}; "
               f"first fetched {s['fetched_from'][:16]} to {s['fetched_to'][:16]} UTC.")
    incomplete = [m for m in models if any(r[m] is None for r in rows)]
    if incomplete:
        out += ["", f"**Incomplete:** {', '.join(incomplete)} did not judge every item; see the errors column."]
    out.append("")
    out.append("| Model | Judged | Relevant | Input tokens | Output tokens | Seconds | Cost of this run | Refusals | Errors |")
    out.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for m in models:
        u = result["usage"][m]
        judged = [r for r in rows if r[m]]
        out.append(f"| {m} | {len(judged)} | {sum(r[m]['relevant'] for r in judged)} | {u['input_tokens']} | "
                   f"{u['output_tokens']} | {u['seconds']} | ${u['cost_usd']:.4f} | {len(u['refusals'])} | {len(u['errors'])} |")
    out += ["", f"- Relevance agreement: {rel_agree} of {len(both)} ({100 * rel_agree / max(1, len(both)):.0f}%)",
            f"- Type agreement: {type_agree} of {len(both)} ({100 * type_agree / max(1, len(both)):.0f}%)",
            f"- Mean absolute score difference: {score_diff:.2f}",
            f"- Relevant only for {a}: {len(a_only)}; relevant only for {b}: {len(b_only)}", ""]

    out += ["## Relevance disagreements", "", f"| Item | Source | {a} | {b} |", "| --- | --- | --- | --- |"]
    for r in a_only + b_only:
        out.append(f"| [{esc(r['title'][:90])}]({r['url']}) | {esc(r['source'])} | "
                   f"{'yes' if r[a]['relevant'] else 'no'}, {r[a]['score']}: {esc(r[a]['summary'])} | "
                   f"{'yes' if r[b]['relevant'] else 'no'}, {r[b]['score']}: {esc(r[b]['summary'])} |")

    out += ["", "## Every item", "", f"| Item | {a} | {b} |", "| --- | --- | --- |"]
    for r in rows:
        def cell(j: dict | None) -> str:
            if not j:
                return "not judged"
            return f"{'relevant' if j['relevant'] else 'not relevant'}, {j['type']}, {j['score']}: {esc(j['summary'])}"
        out.append(f"| [{esc(r['title'][:90])}]({r['url']}) ({esc(r['source'])}) | {cell(r[a])} | {cell(r[b])} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs=2, default=["claude-haiku-5-5", "claude-sonnet-5-5"])
    ap.add_argument("--limit", type=int, default=100, help="at most this many items (one request per model)")
    ap.add_argument("--since-hours", type=float, default=None, help="only items first fetched in the last N hours")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "model_comparison"))
    args = ap.parse_args(argv)
    for m in args.models:
        cost(m, 0, 0)  # fail early on a model with no known price
    items = Archive(Path(args.data_dir)).all_items()
    if args.since_hours is not None:
        from datetime import timedelta

        from news.util import iso, utcnow

        cut = iso(utcnow() - timedelta(hours=args.since_hours))
        items = [i for i in items if i["fetched"] >= cut]
    items = items[: args.limit]
    if not items:
        raise SystemExit("no items to compare")
    # Judge the source text, as the hourly job does, even if an item already has a summary.
    items = [{**i, "summary": None, "relevant": None, "score": None} for i in items]
    result = compare(items, args.models)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "report.md").write_text(report(result, args.models), encoding="utf-8")
    print(f"Wrote {out / 'results.json'} and {out / 'report.md'}")
    complete = all(r[m] is not None for r in result["rows"] for m in args.models)
    if not complete:
        print("Comparison incomplete: a model did not judge every item.")
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
