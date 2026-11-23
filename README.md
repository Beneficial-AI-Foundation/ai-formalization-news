# AI and formalization news

A static news page kept by the Beneficial AI Foundation. Every hour a GitHub Actions job collects new papers, Lean and Mathlib updates, blog posts and social posts about AI and formal verification, removes duplicates, optionally asks Claude to check relevance and write a one-sentence summary, and redeploys the page on GitHub Pages. There is no server and no database.

- Page: <https://beneficial-ai-foundation.github.io/ai-formalization-news/> (once Pages is switched on)
- Atom feed: `feed.xml` on the same site

## How it works

1. `.github/workflows/update.yml` runs at minute 17 of every hour (UTC), or by hand from the Actions tab.
2. `python -m news.run` fetches every enabled source in `config/sources.yaml` that is due, sending `If-None-Match` and `If-Modified-Since`, so unchanged feeds answer 304.
3. Links are normalised and each item gets an id. Items already seen, or with the same title as an item kept in the last 7 days, are dropped.
4. Sources marked `filter: keywords` keep only items that match a keyword.
5. If an `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY` secret is set (see Claude summaries), one Claude request tags each new item as relevant or not, picks its type, scores it from 1 to 5 and writes a summary of at most 160 characters. Without the key, the page shows each source's own text.
6. The job writes `news.json` (newest 300 items), `archive/YYYY-MM.json` (every kept item, never trimmed), `search.json` (compact rows for search), `meta.json` (run time, counts, errors) and `feed.xml`, checks them against `schema/news.schema.json`, commits them to the `data` branch only if items changed, and deploys the page.

A failing source never stops the run; its error shows in `meta.json` and on the page.

## Where the data lives

The code is on `main`; the generated data is on a separate `data` branch, which the workflow starts on its first run and commits to every hour that brings something new. The page is built from `main`'s `site/` and the `data` branch's files. So `main` keeps its pull-request rule and holds only code, and the hourly commits stay out of its history.

- To take an item off the page, delete it from its `archive/YYYY-MM.json` file on the `data` branch. The next run rebuilds the other files from the archive, and `seen.json` keeps the item from coming back.
- To preview the live data locally: `git fetch origin data && git worktree add ../news-data origin/data`, then `.venv/bin/python -m news.serve --data-dir ../news-data`.
- Locally, `python -m news.run` writes to `data/`, which is git-ignored on `main`.

## Repository layout

```
.github/workflows/update.yml   hourly schedule: run, commit to the data branch if changed, deploy
.github/scripts/data_branch.sh check out and commit to the data branch
config/sources.yaml            sources, keywords and Claude settings
config/search_synonyms.yaml    synonym groups for the search box
news/                          the Python job (one module per kind of source in news/sources/)
schema/news.schema.json        item schema
data/                          generated data from local runs (git-ignored; the workflow keeps its data on the data branch)
site/                          the page: HTML, CSS, JavaScript, fonts, MiniSearch
tests/                         pytest on saved responses; tests/js for search
docs/                          build plan, sketch, implementation notes, model comparison
```

## Run it locally

```
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m news.fetch --dry-run    # fetch every enabled source and print what came back
.venv/bin/python -m news.run --no-claude    # update data/ without Claude
.venv/bin/python -m news.serve              # build _site/ and open http://127.0.0.1:8000/
```

Tests run offline from saved responses:

```
.venv/bin/pytest -q
node --test 'tests/js/*.test.mjs'
```

## Add or remove a source

Edit `config/sources.yaml`. Copy an existing entry, give it a new `id`, and set:

- `kind`: `feed` for any RSS or Atom link; the other kinds are for arXiv, GitHub releases, Hacker News, Hugging Face Daily Papers and Lean Zulip.
- `type`: the tag on the page (`paper`, `lean`, `news`, `blog`, `social`).
- `filter`: `none` keeps every item; `keywords` keeps only items that match the `keywords` list. Use `keywords` for broad feeds.
- `every`: `1h`, `6h` or `daily`.
- `enabled`: `true` or `false`.
- `trusted`: optional. Set `true` for a source that is on topic by design (a Lean or proof-assistant blog, for example). With Claude on, its items still get a summary but are never dropped as not relevant.

Merge the change into `main` through a pull request; the next hourly run picks it up, or run the workflow by hand from the Actions tab. To test it first, run `python -m news.fetch --dry-run --source <id>` locally.

The keyword list, the Claude model and the synonym groups are in the same two config files.

## Claude summaries

- Add `ANTHROPIC_API_KEY` as a secret of the `github-pages` environment (Settings, Environments, github-pages, Environment secrets, Add environment secret). The workflow switches the Claude step on only when the secret exists.
- Why an environment secret: only jobs running on `main` may use the `github-pages` environment, so only they can read its secrets. A repository secret would also reach a workflow pushed on any branch, and every BAIF member can push branches here. A repository secret with the same name still works, but is less protected.
- The model is `claude-haiku-5-5` by default (`claude.model` in `config/sources.yaml`). To change it without a commit, set the repository variable `CLAUDE_MODEL`.
- Each run logs input and output token counts; `meta.json` keeps them under `claude`.
- If Claude refuses to judge an item (rare), the item is kept with its source text and no summary, and is not sent again. With a Sonnet or Opus model the request first falls back to another model server-side; a refusal means the whole chain declined.
- Set a monthly spending cap on the key in the Claude Console.

To rotate the key: create a new key in the Claude Console, replace the `ANTHROPIC_API_KEY` secret with it, run the workflow once by hand to check, then revoke the old key.

### Through OpenRouter instead (stopgap)

While the Anthropic account has no credit, the same model (Claude Haiku 5.5, same prompt and schema) can be reached through OpenRouter:

- Add `OPENROUTER_API_KEY` as a secret of the `github-pages` environment, the same way. Use a key made for this site with a monthly credit limit (for example $5), not a shared key: the site needs well under $1 a month.
- The workflow uses Anthropic when `ANTHROPIC_API_KEY` is set and OpenRouter only when it is not. `meta.json` records the route under `claude.route`.
- `CLAUDE_MODEL` and `claude.model` keep Anthropic's names (`claude-haiku-5-5`); the job translates them to OpenRouter's (`anthropic/claude-haiku-5.5`). The server-side fallback for Sonnet and Opus is Anthropic-only.

To switch back: add `ANTHROPIC_API_KEY` (the next run uses it), then delete `OPENROUTER_API_KEY`, and ask the OpenRouter admin to revoke the site key. Stored summaries stay as they are; nothing is judged again.

## If the page stops updating

GitHub disables scheduled workflows in a public repository after 60 days with no repository activity. The page shows a warning when its data is more than 3 hours old. To turn the schedule back on: open the Actions tab, select "Update news", and press "Enable workflow". Then run it once by hand.

## One-time setup (organization admin)

1. Make the repository public. Actions minutes are free for public repositories; a private one would spend about 720 to 1,440 of the organization's included minutes a month, and Pages on a private repository needs a paid plan.
2. Settings, Pages: set the source to "GitHub Actions".
3. Settings, Actions, General: set workflow permissions to "Read and write".
   `main` can require pull requests, since the workflow never pushes to it. Leave the `data` branch unprotected: the workflow pushes to it as `github-actions[bot]`.
4. Optional: add `ANTHROPIC_API_KEY` (or `OPENROUTER_API_KEY` as a stopgap), and `ZULIP_EMAIL` and `ZULIP_API_KEY` if Lean Zulip is switched on, as secrets of the `github-pages` environment (see Claude summaries).

## Licence

Code: MIT (see `LICENSE`). Headlines and summaries belong to their sources; each item links to the original. Fonts: Manrope and Poppins under the SIL Open Font License (`site/fonts/OFL.txt`). Search: MiniSearch under the MIT licence (`site/vendor/MINISEARCH_LICENSE.txt`).
