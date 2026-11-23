# Implementation notes for version 1

Companion to `BUILD_PLAN.md`. It records the scope of this build, the data contract between the Python job and the page, the gates, the reuse audit, and every decision that fills a gap in the plan. Prose here follows the plan's rule: no contractions.

## Scope of this build

In scope, built and checked locally on branch `feat/news-site-v1`:

- Phase 1 (fetchers), Phase 2 (dedup and storage), Phase 3 (Claude filter and summary), Phase 4 (page), Phase 4b tier 1 (word search), Phase 5 (workflow file).
- Phase 6 step 3: the Haiku 5.5 and Sonnet 5.5 comparison on the newest 100 real items (the first production request), written to `docs/MODEL_COMPARISON.md`. The choice of model is Jin's. A comparison on one day of production items can be repeated after launch with `python -m news.compare_models --since-hours 24`.
- A local deployment for review: `python -m news.serve` builds `_site/` and serves it on localhost.

Out of scope (needs GitHub admin rights, a push, or 24 hours of scheduled runs):

- Phase 0 settings (Pages source, Actions write permission), the first deploy, and the Phase 5 and Phase 6 checks that need runs inside GitHub Actions.
- Phase 4b tier 2 (meaning search). The plan's default is to launch without it.
- Lean Zulip live access (no bot credentials). The fetcher exists, is disabled, and is tested on a synthetic fixture shaped like the Zulip API docs.
- X, LinkedIn, Threads and Reddit, as the plan says.

The plan asks for a commit at the end of each phase. The implement workflow leaves all changes uncommitted for review instead; commits happen after Jin approves.

## Repository layout as built

```
.github/workflows/update.yml   hourly schedule, run, commit to the data branch when data changed, deploy Pages
.github/scripts/data_branch.sh check out the data branch as a worktree; commit and push to it (decision D30)
config/sources.yaml            sources, keywords, Claude settings (Jin edits this)
config/search_synonyms.yaml    synonym groups for search (Jin edits this)
news/                          Python package
  config.py   net.py    util.py   keywords.py
  sources/    arxiv.py  arxiv_rss.py  feed.py  github_releases.py  hackernews.py  hf_papers.py  zulip.py
  fetch.py    dedup.py  store.py  claude.py  feed_xml.py  run.py  serve.py  compare_models.py
schema/news.schema.json        item schema, checked before every write
data/                          news.json, seen.json, meta.json, search.json, feed.xml, archive/YYYY-MM.json
                               git-ignored on main; the workflow keeps these on the data branch (D30)
                               http_cache.json is never committed (see decision D3)
site/                          index.html style.css app.js search.js search-core.js search-worker.js logo.svg logo-footer.svg
site/vendor/minisearch.js      MiniSearch 7.2.0 UMD build, copied in
site/fonts/                    Manrope and Poppins woff2, self-hosted (decision D2)
tests/                         pytest on saved fixtures; node tests for search
```

## Data contract (Python job writes, page reads)

The Pages artifact is `site/`, plus `news.json`, `hidden.json`, `meta.json` and `search.json` copied to `_site/data/`, plus `feed.xml` copied to `_site/feed.xml`. In the workflow these come from the `data` branch (D30). The page fetches relative URLs `data/news.json`, `data/meta.json`, `data/search.json`, and `data/hidden.json` only when the reader asks to see filtered-out items.

### Item (news.json, archive files)

```json
{
  "id": "arxiv:2611.01234",
  "type": "paper",
  "source": "arXiv",
  "source_id": "arxiv-api",
  "title": "...",
  "url": "https://arxiv.org/abs/2611.01234",
  "authors": ["..."],
  "published": "2026-11-22T01:00:00Z",
  "fetched": "2026-11-22T03:17:00Z",
  "source_text": "first 600 characters (300 for social posts)",
  "summary": null,
  "score": null,
  "relevant": null
}
```

- `type` is one of `paper`, `lean`, `news`, `blog`, `social`.
- `summary` (at most 160 characters), `score` (1 to 5) and `relevant` (true or false) are null until Claude has judged the item.
- Additions to the plan's schema: `source_id` (the key in `config/sources.yaml`, so an item can be traced to its source entry) and `relevant` (Phase 3 says every item gets it).
- `hidden: true` (optional) marks an item Claude judged off topic from a source that is not trusted (D33).

### news.json

`{"version": 1, "updated": "<ISO time>", "items": [Item, ...]}`, newest `published` first, at most 300 items, hidden items left out.

### hidden.json

The same shape as news.json, holding the newest 300 hidden items (D33).

### archive/YYYY-MM.json

`{"version": 1, "month": "2026-11", "items": [Item, ...]}`, by month of `published`, never trimmed.

### search.json

```json
{
  "version": 1,
  "fields": ["id", "type", "source", "title", "url", "published", "text", "authors", "source_id"],
  "rows": [["arxiv:2611.01234", "paper", "arXiv", "...", "https://...", "2026-11-22T01:00:00Z", "first 300 characters of summary or source_text", "Author One, Author Two", "arxiv-api"]],
  "synonyms": [["atp", "automated theorem proving"], ["coq", "rocq"]]
}
```

Rows are arrays to keep the file small. Hidden items are left out. `authors` is added to the plan's list because the plan boosts authors in search; `source_id` is added for the source filter (D29).

### meta.json

```json
{
  "version": 1,
  "last_run": "2026-11-23T03:17:00Z",
  "duration_seconds": 12.3,
  "counts": {"items_in_news": 120, "items_in_hidden": 40, "new_kept": 4, "new_rejected": 30, "new_hidden": 2, "archive_total": 800},
  "sources": [
    {"id": "xena", "name": "Xena (Kevin Buzzard)", "type": "blog", "enabled": true,
     "status": "ok | not_modified | skipped | error", "http_status": 200,
     "fetched": 10, "new": 1, "kept": 1, "error": null, "last_fetch": "<ISO time>"}
  ],
  "errors": [{"source": "xena", "error": "..."}],
  "claude": {"enabled": false, "model": null, "route": null, "input_tokens": 0, "output_tokens": 0, "items_sent": 0, "refusals": []}
}
```

## Pipeline behaviour

1. Load `config/sources.yaml`. A source runs when it is enabled and due (`every: 1h | 6h | daily`; `daily` means once per US Eastern calendar day). Due times and the HTTP validators live in `data/http_cache.json`.
2. Each fetcher uses a 20 second timeout, two retries, `If-None-Match` and `If-Modified-Since`, and the User-Agent `baif-news-bot/1.0 (+https://github.com/Beneficial-AI-Foundation/ai-formalization-news)`. A 304 means no new items.
3. Dedup: normalise the URL (lower-case host, drop `utm_*` and fragments, arXiv to `abs/<id>` without version), build `id` (`arxiv:<id>` for arXiv, otherwise `u:` plus the first 16 hex digits of the SHA-1 of the normalised URL). Drop an item whose id is in `seen.json` or whose normalised title matches a kept item from the last 7 days.
4. Keyword filter for sources with `filter: keywords`: case-insensitive whole-phrase match on title and text, spaces or hyphens between words optional ("Lean 4" matches "Lean4"), plural endings allowed, "isation" treated as "ization".
5. Claude step when `USE_CLAUDE` is on and a key exists (decision D6 for ordering). Items judged off topic are hidden, unless their source is trusted (D28, D33). Items that could not be judged (API error, more than 100 waiting) are kept with null fields and retried on later runs while they are less than 3 days old.
6. Every item that reaches a final decision (kept, or rejected by keyword or Claude) is added to `seen.json`. Hacker News stories under 5 points are not marked seen, so they can qualify later (the fetcher looks back 48 hours each run).
7. Write news.json, hidden.json, archive files, search.json, meta.json and `feed.xml` (Atom, newest 50) into the data directory after schema validation. A failing source records an error and the others still save.

## Stop conditions

Done when every gate below passes or its failure is recorded, the local site runs, and the model comparison is written down with a recommendation.

## Gates

| Gate | Command or check |
| --- | --- |
| Offline tests | `.venv/bin/pytest -q` with network blocked (fixtures only) |
| Search tests | `node --test 'tests/js/*.test.mjs'` (isabell, formalisation, lean4, synonyms, type filter, any-word fallback); Node 23 does not accept a directory |
| Dry run | `python -m news.fetch --dry-run` prints at least one item per enabled source, or the failure is recorded |
| Idempotence | two `python -m news.run` in a row; the second adds zero items |
| Broken fixture | test: one source returns garbage, it reports an error, the others save |
| Claude | with a key: every new item gets `relevant`, `type`, summary of at most 160 characters, unless Claude refuses it (then it is recorded and kept with the source text only); tokens logged |
| Page | 400 px and 1440 px screenshots; focus visible; text contrast at least 4.5:1; no request to another origin |
| Search scale | 20,000 generated items: an item older than the newest 300 is found; search file under 4 MB gzipped; results under 200 ms with 4x CPU slowdown in headless Chrome |
| Workflow | YAML parses; steps reviewed by hand (no Actions run possible before a push) |

## Reuse audit

The repository was empty apart from a README, so there is no in-project analog for anything. Library choices:

| Need | Choice | Classification |
| --- | --- | --- |
| RSS and Atom parsing, including arXiv API Atom | `feedparser` | REUSE-AS-IS |
| HTTP with timeout and retries | `requests` with `urllib3.Retry` | REUSE-AS-IS |
| YAML config | `PyYAML` | REUSE-AS-IS |
| Schema check | `jsonschema` | REUSE-AS-IS |
| Claude structured output | `anthropic` SDK `messages.parse` with a Pydantic model | REUSE-AS-IS |
| Browser search | MiniSearch 7.2.0, vendored | REUSE-AS-IS |
| Atom feed writer | small writer on `xml.etree.ElementTree` | JUSTIFY-FORK: one function; a dependency such as `feedgen` adds lxml for 40 lines of output |
| Local server | `http.server` from the standard library | REUSE-AS-IS |

## Decisions that fill gaps in the plan

- **D1. Phase 0 placeholder.** Not built separately; `update.yml` is a superset of the minimal deploy workflow.
- **D2. Fonts.** The plan says to load Manrope and Poppins from Google Fonts, but Phase 4b requires that the network panel shows no request to another site. Both hold only if the fonts are self-hosted, so the woff2 files (SIL Open Font License) are copied into `site/fonts/`.
- **D3. HTTP cache persistence.** `http_cache.json` (validators and last fetch times) changes on most runs. Committing it would make a commit every hour even when nothing is new, which breaks "a run with nothing new makes no commit". It is git-ignored and carried between runs with `actions/cache`, keyed to the branch and to the hash of `seen.json` on the data branch, and saved only after the data commit is pushed. So validators are never reused with data they do not belong to: if a push fails, the next run fetches the same items again. If the cache is lost, the next run refetches everything and dedup drops the repeats.
- **D4. What counts as a data change.** The workflow commits when any data file other than `meta.json` changed (including `feed.xml` and new archive files). `meta.json` is committed only together with such a change, and is always deployed.
- **D5. Type from Claude.** Claude's `type` replaces the source's type, except that social posts stay `social`, so the Social button always holds every post.
- **D6. Keyword filter before Claude.** Sources marked `filter: keywords` pass the keyword filter first, then Claude judges what remains. This keeps high-volume feeds (Simon Willison, Tao) cheap. Claude cannot rescue an item the keywords missed.
- **D7. Source settings not fixed by the plan.** Hugging Face Daily Papers uses `filter: keywords` (it is a broad AI feed of about 20 papers a day). Import AI was first enabled with `filter: none` (a recommended addition) and switched off on 23 Nov 2026 by Jin's decision, because the Claude step drops every issue as general AI news. arXiv RSS (cs.LO and cs.PL) uses `filter: keywords`; the arXiv API query already filters by abstract. Agda and Rocq GitHub releases start disabled, as "the rest" in the plan.
- **D8. Default keywords.** The plan's list plus "theorem proving", which the plan's own arXiv query uses and which "theorem prover" does not match.
- **D9. Match meaning switch.** Tier 2 is not built, so the switch is not shown. The markup for it is kept in a comment in `index.html`.
- **D10. Sonnet refusal fallback.** When the model setting is a Sonnet 5.5 or Opus 5.5 model, the request adds the server-side refusal fallback (`fallbacks: "default"`). Haiku 5.5 has none, so the plan's one-at-a-time retry applies.
- **D11. Licence.** MIT, as in most BAIF tool repositories; confirmed by Jin on 23 Nov 2026.
- **D12. Intake limits.** Some feeds return their whole history (the Archive of Formal Proofs feed has 1,060 entries). Each fetch keeps only items published in the last 30 days (`max_age_days`, a per-source setting). There is no count cap: a cap applied before dedup would hide the same overflow items on every later run.
- **D13. arXiv API timeout.** 60 seconds for the arXiv API source (one call timed out at 30 seconds while building); every other source keeps the plan's 20 seconds.
- **D14. arXiv RSS announcements.** Only `new` and `cross` items are kept; `replace` items are revisions of older papers.
- **D15. Fixtures.** The live arXiv RSS feed was empty at the weekend, so `tests/fixtures/arxiv-rss.xml` is built in the feed's item format from five real papers; the real empty response is kept as `arxiv-rss-weekend.xml`. `lean-zulip.json` is synthetic. See `tests/fixtures/README.md`.
- **D16. Page details beyond the sketch.** Typo tolerance only for words of 5 or more letters (so "lean" does not find "mean"); numbers match exactly; a short stop-word list; at most 500 search results returned, 50 shown at a time; a skip link, a "Clear search" button and a visible "Subscribe (RSS)" link to the Atom feed (relabelled from "Subscribe (Atom)" at Jin's request, because "RSS" is the better-known name); side-column headings are `h2` for heading order; a same-origin Content-Security-Policy. Social posts without a Claude summary show only their title (the opening words of the post), because the text under it would repeat it.
- **D17. Review notes.** `notes/` (where the review tooling saves critiques) is git-ignored.
- **D18. Claude call.** The plan names `client.messages.parse` with a Pydantic model. In the SDK that method validates the reply text inside the call, so a refusal written in prose or a reply cut off at `max_tokens` raises before `stop_reason` can be checked. The job therefore calls `messages.create` with the same JSON schema in `output_config.format`, checks `stop_reason`, and then validates the text with the Pydantic model. A judgement counts only if its id was in the request that returned it.
- **D19. Time budgets.** HTTP retries back off 1 s and 2 s and ignore `Retry-After`. No new source starts after 150 s, and Hacker News stops its keyword searches at the same point. Claude calls time out after 120 s with one retry, and no new call starts after 120 s. Anything skipped waits for the next run. The workflow timeout is 15 minutes, up from the plan's 10, so these limits always leave time to save and deploy.
- **D20. Workflow checkout and push.** The job checks out the head of `main`, not the triggering commit, so a run that waited in the concurrency queue deploys the newest code, and fetches the `data` branch fresh, so it starts from the data the previous run pushed. A rejected push is retried up to three times, each time after replaying the data commit on top of the branch (an edit by hand during the run is kept); a conflict or a failed push fails the job loudly.
- **D21. Title duplicates and Claude.** A title is recorded for dedup only when its item is finally kept. With Claude on, a new item whose title matches an item still waiting for Claude is held in `seen.json` (`alts`) instead of being dropped. If Claude rejects the first copy, the held copy is released for judging; if Claude keeps it, the held copy is discarded as a duplicate.
- **D22. Refusals.** An item Claude refuses to judge is recorded in `seen.json` (`refused`) and never sent again. It stays on the page with its source text (the keyword filter only).
- **D23. Daily sources.** `every: daily` means the first run at or after 01:00 US Eastern, an hour after arXiv's midnight update, to avoid racing it.
- **D24. Malformed items.** Links are normalised and each item is checked against the schema inside its source's guarded stage. A malformed item is skipped and counted in that source's `warning` in `meta.json`; it never stops the run.
- **D25. Zulip links.** URL normalisation keeps `#narrow/...` fragments on `*.zulipchat.com`, because Zulip addresses messages in the fragment.
- **D26. Site builder.** `python -m news.serve` refuses to replace the repository, `site/`, `data/`, anything that contains them or sits inside them, symbolic links, and any non-empty directory without its build marker.
- **D27. Decisions recorded on 23 Nov 2026.** Model: Claude Haiku 5.5 at effort `low`. The repository was made public. The locally generated data is not committed; the first workflow run builds it (on the `data` branch, D30).
- **D28. Trusted sources.** A per-source `trusted: true` (Jin's decision, 23 Nov 2026, after the model comparison) marks sources that are on topic by design: Xena, the Lean community blog, Lean 4 releases, the Archive of Formal Proofs, Lean FRO on Bluesky, Rocq Discourse, and Lean Zulip when switched on. Claude still judges their items in the same request (summary, type, score), but an item judged not relevant is kept with `relevant: false` instead of being dropped. Hashtag feeds, broad blogs and the arXiv query stay untrusted.
- **D29. Filter by source** (asked by Jin on 23 Nov 2026, beyond the plan). Each source in the Sources column is a button showing its type and the number of its items in the latest news. Choosing one shows only that source's items, highlights it and greys out the others; choosing it again, or "Show all sources" in the bar above the list, clears it. The source name on each item does the same, which matters on phones, where the column sits below the list. The filter combines with the type buttons and with search (search rows carry `source_id`), and lives in the URL (`?source=afp`, with `type` and `q` when set), so a filtered view can be shared. One source at a time.
- **D30. Data on its own branch** (Jin's decision, 23 Nov 2026). The plan has the job commit `data/` to `main`, but `main` requires a reviewed pull request, so the bot's hourly push would be rejected. The job now takes its code from `main` and keeps the generated files on a `data` branch with its own history, checked out as a git worktree in `data-branch/`; `data/` is git-ignored on `main`. The feed moved from `site/feed.xml` to `feed.xml` in the data directory, so all generated output is in one place; the site builder copies it to `_site/feed.xml`, so its address is unchanged. The first run starts the branch (with a short README and a `.gitignore` for `http_cache.json`). Fetches and pushes run from the main checkout, where `actions/checkout` keeps the credentials. The job runs only on `main`, since the data branch and the site are shared. `tests/test_data_branch.py` runs the script against a local bare repository: first run, a run with nothing new, a meta-only change, an edit by hand during a run, and a conflicting edit. Side benefit: the up to 720 data commits a month stay out of `main`'s history.
- **D31. OpenRouter route** (Jin's decision, 23 Nov 2026, while the Anthropic account has no credit). The Claude step can reach the same model through OpenRouter's Anthropic-format endpoint (`https://openrouter.ai/api`) with the same SDK, prompt, schema and effort, so no second code path is needed. The route is chosen by which secret exists: `ANTHROPIC_API_KEY` first, then `OPENROUTER_API_KEY`; an empty value counts as unset, since GitHub passes a missing secret as an empty string. The OpenRouter key goes to the SDK as `auth_token`, which stops the SDK from reading `ANTHROPIC_API_KEY`. Model names stay Anthropic's in config and are translated (`claude-haiku-5-5` to `anthropic/claude-haiku-5.5`). The Sonnet and Opus server-side fallback beta is not sent to OpenRouter. `meta.json` records `claude.route`. Live checks on 23 Nov 2026: three items judged through the route with an empty `ANTHROPIC_API_KEY`, and a probe whose schema used a made-up key name came back in that shape, so OpenRouter applies `output_config.format`.
- **D32. Secrets in the `github-pages` environment** (23 Nov 2026). The organization gives every member write access to its repositories, and a repository secret reaches any workflow run from any branch of the repository, so anyone who can push a branch could read it. The `github-pages` environment only admits `main` (its deployment branch policy), and the job already runs in it, so the API keys and the Zulip credentials go there as environment secrets: reading them needs code on `main`, which needs a reviewed pull request. The workflow reads them under the same names, so no code change was needed. Repository secrets with the same names still work.
- **D33. Hide instead of delete, and keep borderline items** (Jin's decision, 23 Nov 2026, after the first Claude run). The first run through OpenRouter judged 100 arXiv papers and deleted 37. Some of them looked on topic by title (autoformalization, mechanised definitions, dependent type theory). Re-judging the same 37 showed that Haiku is inconsistent on borderline items. The same prompt kept 12 of them an hour later. The new prompt kept "Datatypes In, Zippers Out!" in one batch and dropped it in another. Two changes:
  - **Hide instead of delete.** An item judged off topic from a source that is not trusted stays in the archive with `relevant: false` and `hidden: true`. It is left out of news.json, search.json and the feed, and listed in `hidden.json` (newest 300). A switch below the list ("Show N items filtered out as off topic") loads hidden.json and mixes those items into the list, dimmed and tagged "Filtered out". Search still covers only the items on the page. A wrong call can be undone by clearing the item's judgement in its archive file, and the next run judges it again. Hidden items are not judged again on their own and do not count as kept in the per-source counts. A held title duplicate (D21) is still released for its own judgement.
  - **Keep borderline items.** The prompt now counts mechanised proofs in any field, the type theory, logic and semantics under proof assistants, and autoformalization into any formal language. It also says: "When you are unsure, or the text is too short to tell, and the item could plausibly be about one of these topics, mark it relevant with score 2."

  Measured on Haiku 5.5 through OpenRouter:
  - On the 37 deleted papers, the new prompt keeps 21, against 12 for the old prompt run again. The clearly off-topic papers (turbofans, ridge regression, Latin rectangles) stay out.
  - On the 100-item comparison set it keeps 84, against 83 for the old prompt and 86 for Sonnet. Agreement with Sonnet is 94, against 95.
  - The cost is unchanged.

## Follow-ups

- Split `search.json` by year once it passes 15 MB (build plan Phase 2, step 5; not built in version 1): [issue #1](https://github.com/Beneficial-AI-Foundation/ai-formalization-news/issues/1).
