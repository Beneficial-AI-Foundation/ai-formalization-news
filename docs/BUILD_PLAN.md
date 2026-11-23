# BAIF News Site Build Plan

Prepared for Jin (Beneficial AI Foundation). Written 22 Nov 2026, last updated 23 Nov 2026. This file is a complete export of the build plan, including every change agreed in the planning conversation; nothing here depends on a link Claude Code cannot open.

## Goal

Build a static BAIF news page on GitHub Pages that collects AI and formalization news every hour, with no server to run. A scheduled GitHub Actions job fetches the sources, removes duplicates, optionally asks Claude to filter and summarize, commits `data/news.json`, and redeploys the page.

How to use this plan: give Claude Code this whole file and ask it to work one phase at a time, stopping at each phase's acceptance checks. The look follows the sketch `baif-news-sketch.html` in the same folder as this file, which copies the BAIF website's black and white style. Write prose (README, comments, commit messages) without contractions, as Jin prefers.

Scope for version 1: one page, about 50 recent items, filter by source type, search across every item since launch, no accounts, no database, no comments.

## Decisions made so far

These came from Jin during planning (22 Nov 2026). Do not reopen them unless Jin asks.

1. **Hosting:** a static site on GitHub Pages, built and updated by GitHub Actions, with no server. Keep the repository public so Actions minutes are free (see Scheduling).
2. **Look:** match the BAIF website (black page, white text, Manrope and Poppins, square outlined buttons). The sketch is the visual reference.
3. **Fetch frequency:** every hour (changed from the first draft's every 3 hours), at minute 17 UTC. Slow sources are checked less often (arXiv daily, blogs every 6 hours).
4. **Sources:** free sources only, with no paywall. Paywalled or metered outlets are marked in the tables; MIT Technology Review has a metered paywall and is not in the recommended set.
5. **Social media:** Bluesky and Mastodon public RSS feeds are used. X is left out unless Jin decides to pay for its official API, because X charges per post read and its terms forbid scraping. LinkedIn is not used, because no API reads other accounts' public posts and scraping is forbidden. Threads and Reddit are left out of version 1.
6. **Search:** a search bar with typo tolerance, prefix matching and synonyms over every item since launch (tier 1). Meaning search (tier 2) is optional; launch without it unless Jin says otherwise.
7. **Summaries:** Claude Haiku 5.5, about $0.65 a month at hourly runs. Kimi was considered and rejected: it needs a second API key and client for no saving over Haiku. Before launch, compare Haiku with Claude Sonnet 5.5 on one day of items (see Phase 6).

## What Jin needs to provide

Only two items block the build: where the repository lives, and someone with admin rights on the BAIF GitHub organization to switch on Pages and Actions. Everything else has a default that Claude Code can use until you decide.

| Item | Required? | Default if you do not decide | Who does it |
| --- | --- | --- | --- |
| Repository name under the BAIF GitHub organization | Required | `ai-formalization-news`, served at `beneficial-ai-foundation.github.io/ai-formalization-news/` | Jin |
| Admin who enables GitHub Pages (source: GitHub Actions) and allows Actions to write to the repository | Required | None | An organization admin |
| Anthropic API key, stored as the repository secret `ANTHROPIC_API_KEY` | Optional (needed only for Phase 3) | Keyword filter only, summaries taken from each source's own text | Jin, from the Claude Console of a BAIF billing account |
| Monthly spending cap on that API key | Optional | None | Jin |
| Lean Zulip bot email and API key, stored as `ZULIP_EMAIL` and `ZULIP_API_KEY` | Optional | Zulip left out | Jin, after checking that the Lean community is fine with a bot reading announcements |
| Final source list | Required before launch, not before build | The sources in the next section | Jin |
| Final keyword list | Required before launch, not before build | The keywords in the Sources section | Jin |
| Logo file (SVG preferred, or PNG at 2x) | Required before launch | A redrawn copy of the ring and green dot, as in the sketch | Jin, from whoever holds BAIF brand files |
| Custom domain, for example `news.beneficialaifoundation.org` | Optional | The github.io address | Jin decides; whoever runs the BAIF domain adds the DNS record |
| Confirmation of the fonts (Manrope and Poppins) | Optional | Use them as read from the site | Whoever built the Squarespace site |
| Extra blogs to follow (RSS or Atom links) | Optional | None | Jin |

No other keys are needed: arXiv, Hacker News and the Lean blog are open, and the workflow's built-in `GITHUB_TOKEN` covers GitHub.

## Architecture and repository layout

One Python job, run by GitHub Actions, does all the work; the page itself is static files that read two JSON files. There is no server, database or build step.

Update pipeline, run once an hour:

1. GitHub Actions starts the job on the schedule (or by hand with `workflow_dispatch`).
2. Fetch every enabled source that is due, sending `If-None-Match` and `If-Modified-Since` from `data/http_cache.json`.
3. Normalise URLs and remove duplicates against `data/seen.json` and recent titles.
4. Optional: send only the new items to Claude Haiku 5.5 to tag relevance and type, score them and write a one-sentence summary (skipped when there is no API key or no new item).
5. Write `data/news.json`, the monthly archive file, `data/search.json` and `data/meta.json`, after checking them against the schema.
6. Commit `data/` and `site/feed.xml` only if data changed.
7. Deploy the site to GitHub Pages (every run, so the last-update time stays current).

If a source fails, the job still commits what the other sources returned and records the error in `meta.json`.

```
ai-formalization-news/
  .github/workflows/update.yml   # schedule, run, commit, deploy
  config/sources.yaml            # sources and keywords (Jin edits this)
  news/                          # Python package: one module per source
    fetch.py  dedup.py  claude.py  run.py
  schema/news.schema.json        # item schema, checked before every write
  data/news.json                 # latest 300 items, newest first
  data/seen.json                 # ids seen in the last 90 days
  data/meta.json                 # last run time, counts, errors
  data/archive/2026-11.json      # every kept item, one file per month, never trimmed
  data/search.json               # compact rows for search, whole archive
  data/embeddings.bin            # optional: one vector per item for meaning search
  data/embeddings_ids.json       # optional: item order for embeddings.bin
  data/http_cache.json           # ETag and Last-Modified per feed
  site/index.html  style.css  app.js  search.js  logo.svg  feed.xml
  site/vendor/minisearch.js      # copied in, so no outside scripts
  site/models/                   # optional: self-hosted model for meaning search
  config/search_synonyms.yaml    # synonyms Jin can edit
  tests/                         # parser tests on saved fixtures
  README.md
```

## News sources

These are the core sources, none of which need a paid key; Lean Zulip and extra blogs are optional. Version 1 uses them together with the recommended additions in More sources. "Checked" means I opened the endpoint or its official documentation on 22 Nov 2026.

| Source | Type tag | Endpoint | Key | Status |
| --- | --- | --- | --- | --- |
| [arXiv API](https://info.arxiv.org/help/api/user-manual.html) | Paper | `https://export.arxiv.org/api/query` with the query below, `sortBy=submittedDate&sortOrder=descending&max_results=100` | None | Documentation checked; the fetch tool here was blocked by robots.txt, so Claude Code should confirm it in Phase 1 |
| [arXiv RSS](https://info.arxiv.org/help/rss.html) | Paper | `https://rss.arxiv.org/rss/cs.LO+cs.PL` (categories joined with `+`) | None | Checked; returns RSS 2.0 with `title`, `link`, `description`, `dc:creator`, `arxiv:announce_type` |
| [Lean community blog](https://leanprover-community.github.io/blog/) | Lean and Mathlib | `https://leanprover-community.github.io/blog/rss.xml` | None | Checked; latest post dated 25 Apr 2026, so expect few items |
| Lean 4 releases (GitHub REST API) | Lean and Mathlib | `https://api.github.com/repos/leanprover/lean4/releases?per_page=10` | Built-in `GITHUB_TOKEN` | Not opened here; the [releases page](https://github.com/leanprover/lean4/releases) shows v4.34.1 as latest stable |
| [Hacker News search (Algolia)](https://hn.algolia.com/api) | News | `https://hn.algolia.com/api/v1/search_by_date?query=<keyword>&tags=story&numericFilters=created_at_i><unix time>` | None | Checked; JSON `hits` with `title`, `url`, `points`, `num_comments`, `created_at_i`, `objectID` |
| Lean Zulip announcements (optional) | Lean and Mathlib | `GET https://leanprover.zulipchat.com/api/v1/messages` with `anchor=newest`, `num_before=50`, `num_after=0` and a `narrow` on the announcement channel | Bot email and API key (HTTP Basic auth) | Endpoint shape checked in Zulip's API docs; channel name and permission still to confirm |
| Extra blogs (optional) | Blog | Any RSS or Atom link Jin adds to `config/sources.yaml` | None | Jin's list |

### arXiv query

One query, URL encoded, paged at most once per run with a 3 second pause between calls, as arXiv asks:

```
(cat:cs.LO OR cat:cs.PL OR cat:cs.AI OR cat:cs.LG OR cat:cs.SE OR cat:math.LO)
AND (abs:"Lean 4" OR abs:Mathlib OR abs:autoformalization OR abs:"formal verification"
     OR abs:"theorem proving" OR abs:"proof assistant" OR abs:Isabelle OR abs:Coq
     OR abs:Rocq OR abs:ACL2 OR abs:Dafny OR abs:Verus)
```

arXiv content changes once a day, so the job may cache the arXiv response and call it only on the first run after 00:00 US Eastern time.

### Keywords (default list, for Hacker News and the local filter)

`Lean 4`, `Mathlib`, `autoformalization`, `formal verification`, `theorem prover`, `proof assistant`, `Isabelle`, `Coq`, `Rocq`, `Dafny`, `Verus`, `ACL2`, `seL4`, `verified code`, `AI mathematics`.

Hacker News items are kept only if the title or URL matches a keyword and `points >= 5`, to cut noise. General news APIs are left out of version 1 because the useful ones need paid keys; Hacker News and the free feeds in More sources cover most announcements.

## More sources (researched 22 Nov 2026)

This section lists extra outlets the site can read for free, with no paid key. "Verified" means the feed was opened on 22 Nov 2026 and showed items; the date in brackets is the newest item seen. Every URL came from the site's own pages or from search results, not from memory. The feeds were opened with a web fetch tool, not from GitHub Actions, so Phase 1 must still test each one from the workflow.

Totals: 37 feeds verified (32 official, 5 from an unofficial scraper), 7 candidates not opened, and 5 sources dead or not usable.

### Formal methods, Lean, Isabelle and Rocq

| Source | Covers | Free | Feed or API URL | Notes |
| --- | --- | --- | --- | --- |
| Xena (Kevin Buzzard) | Lean, formalizing Fermat's Last Theorem | Yes | `https://xenaproject.wordpress.com/feed/` | Verified (12 Nov). A few posts a month, all on topic. |
| Terence Tao, What's new | Mathematics, AI for mathematics | Yes | `https://terrytao.wordpress.com/feed/` | Verified (20 Nov). Very active; keep only posts matching Lean, AI, formal. |
| Lawrence Paulson, Machine Logic | Isabelle, AI and proof | Yes | `https://lawrencecpaulson.github.io/feed.xml` | Verified (30 Oct). About weekly. |
| Hillel Wayne, Computer Things | Formal methods, TLA+ | Yes | `https://buttondown.com/hillelwayne/rss` | Verified (19 Nov). Mostly on topic. |
| Archive of Formal Proofs | New Isabelle AFP entries | Yes | `https://isa-afp.org/index.xml` | Verified (13 Nov). About weekly, authoritative. |
| Rocq Discourse, latest topics | Rocq news, releases, jobs, courses | Yes | `https://discourse.rocq-prover.org/latest.rss` | Verified (19 Nov). The site's robots rules block per-category feeds, so use latest only. |
| Rocq changelog | Rocq releases | Yes | `https://rocq-prover.org/changelog.xml` | Verified (30 Mar). Rare but authoritative. |
| Agda and Rocq releases | Releases | Yes | GitHub REST: `/repos/agda/agda/releases`, same call as Lean | Not opened (robots blocked the fetch tool on releases.atom). Reuse the Lean releases fetcher. |
| Amazon Science | Includes AWS automated reasoning | Yes | `https://www.amazon.science/index.rss` | Verified (18 Nov). Broad; keyword filter needed. |
| Topos Institute blog | Applied category theory | Yes | `https://topos.institute/blog/index.xml` | Verified (9 Nov). Only partly on topic. |
| arXiv, extra categories | cs.PL, math.LO, cs.SE, cs.AI | Yes | `https://rss.arxiv.org/rss/cs.LO+cs.PL+math.LO` | Format verified; empty on 22 Nov because arXiv posts nothing at weekends. For cs.AI, use the arXiv API with a keyword such as Lean (not opened here). |
| YouTube channels | Talks | Yes | `https://www.youtube.com/feeds/videos.xml?channel_id=<id>` | Not opened (robots blocked the fetch tool). Channel ids from search: Lean FRO `UC33n-zWRgO613Qwt4TQx6LQ`, leanprover community `UCWe5B7Ikr0AI9727doEUxPg`, IPAM `UCGzuiiLdQZu9wxDNJHO_JnA`. Test in Phase 1. |
| Proof Assistants Stack Exchange | Questions and answers | Yes | `https://proofassistants.stackexchange.com/feeds` | Not opened (blocked for the fetch tool). Optional. |

### AI research labs and papers

| Source | Covers | Free | Feed or API URL | Notes |
| --- | --- | --- | --- | --- |
| Google DeepMind blog | Models, research, AI for math | Yes | `https://deepmind.google/blog/feed` | Verified (18 Nov). Descriptions are short; keyword filter. |
| Google Research | Research news | Yes | `https://blog.google/innovation-and-ai/technology/research/rss` | Verified (20 Nov). Mixed with product stories. |
| OpenAI News | All news | Yes | `https://openai.com/news/rss.xml` | Verified (20 Nov). Has category tags, so filter to Research and Safety. |
| Microsoft Research blog | Research, including formal methods | Yes | `https://www.microsoft.com/en-us/research/blog/feed/` | Verified (16 Nov). |
| Hugging Face blog | Open models, tools | Yes | `https://huggingface.co/blog/feed.xml` | Verified (20 Nov). Items have no link element; take the URL from guid. |
| Hugging Face Daily Papers | Hand-picked arXiv papers with upvotes | Yes | `https://huggingface.co/api/daily_papers?p=0&limit=20&sort=publishedAt` | Verified JSON (19 Nov). Upvotes give a ranking signal. Replaces Papers with Code, which Meta shut down in July 2025. |
| Epoch AI | Compute, benchmarks, trends | Yes | `https://epoch.ai/atom.xml` | Verified (18 Nov). |
| Anthropic News, Research, Engineering | Company and research posts | Yes | `https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml` (also `_research.xml`, `_engineering.xml`) | Verified (10, 16, 13 Nov). No official feed; an unofficial GitHub scraper rebuilds these hourly and could stop at any time. |
| Meta AI, Mistral | Company news | Yes | Same scraper: `feed_meta_ai.xml`, `feed_mistral.xml` | Verified (27 Jul, 5 Nov). Unofficial; the Meta feed looks stale. |
| AI2 (Allen Institute) | Open research | Yes | None found | No feed link; would need scraping. Skip in version 1. |

### Newsletters and journalism

| Source | Covers | Free | Feed or API URL | Notes |
| --- | --- | --- | --- | --- |
| Import AI (Jack Clark) | Weekly AI research digest | Yes | `https://jack-clark.net/feed/` | Verified (16 Nov). |
| Last Week in AI | Weekly news roundup | Yes | `https://lastweekin.ai/feed` | Verified (21 Nov). |
| Interconnects (Nathan Lambert) | Open models, post-training | Mostly; some posts paid | `https://www.interconnects.ai/feed` | Verified (14 Nov). Paid posts arrive cut off. |
| Simon Willison | LLM tooling | Yes | `https://feeds.simonwillison.net/atom/everything/` | Verified (20 Nov). High volume; use a tag feed or the keyword filter. |
| Ahead of AI (Sebastian Raschka) | LLM research explainers | Yes | `https://magazine.sebastianraschka.com/feed` | Verified (15 Nov). Infrequent. |
| Latent Space | AI engineering | Yes | `https://latent.space/feeds/articles.xml` | Verified (20 Nov). Newest article was on formal verification. |
| LessWrong, curated | AI safety and rationality | Yes | `https://www.lesswrong.com/feed.xml?view=curated` | Verified (12 Nov). Mixed topics; keyword filter. The AI Alignment Forum feed was not found or opened. |
| Quanta Magazine | Mathematics and computer science | Yes, no paywall | `https://www.quantamagazine.org/mathematics/feed/` and `https://www.quantamagazine.org/computer-science/feed/` | Verified (18 Nov, 4 Nov). Strong on AI proving theorems. |
| MIT Technology Review, AI | AI journalism | Metered paywall | `https://www.technologyreview.com/topic/artificial-intelligence/feed/` | Verified (20 Nov). Readers may hit the meter when they click through. |
| IEEE Spectrum | Engineering news | Yes (asks readers to register) | `https://spectrum.ieee.org/feeds/feed.rss` | Verified (20 Nov). No AI-only feed; filter by category. |

The Batch (DeepLearning.AI) has no official feed, so it is left out.

### Social media

| Platform | What is allowed | Cost | Verdict |
| --- | --- | --- | --- |
| Bluesky | Every profile has a public RSS feed, and the public API endpoint `app.bsky.feed.getAuthorFeed` returns JSON with no sign-in. Public post search returns 403 without an account. | Free | Use. |
| Mastodon | Every account (`<instance>/@user.rss`) and hashtag (`<instance>/tags/<tag>.rss`) has a public RSS feed. | Free | Use. Credit the author and link back; do not republish full posts. |
| X | No official RSS and no free read tier found. X's pricing page lists pay per use: $0.005 for each post read, $0.010 for each user lookup. X's terms forbid scraping without written consent. Nitter shut down after a cease and desist in August 2026, and RSSHub's X route scrapes with a signed-in account, which breaks X's terms. | About $4.50 a month for 10 accounts at 3 posts a day; about $24 a month for one 20-post search per run | Leave out unless Jin wants to pay. Then only the official API, with an `X_BEARER_TOKEN` secret. |
| LinkedIn | The Posts API reads only posts of organizations where the signed-in member is a page admin. Access needs app review for registered organizations, aimed at commercial use, with a screencast. No API reads other people's or companies' public posts, and no official RSS was found. In hiQ v. LinkedIn, hiQ ended under a consent judgment and an injunction against scraping. | Free, but gated | Do not use. The one exception is a BAIF page admin reading BAIF's own page, which may still fail the commercial use test. |
| Threads | Reading other accounts needs approved keyword search access and a user token. | Free, but gated | Do not use. |
| Reddit | Feeds exist at `/r/<name>/.rss`, but current terms want a registered OAuth app, a named User-Agent and non-commercial use; unauthenticated access may be blocked. Not opened here. | Free for non-commercial use | Leave out of version 1. |

Verified social feeds:

| Feed | URL | Notes |
| --- | --- | --- |
| Lean FRO on Bluesky | `https://bsky.app/profile/lean-lang.org/rss` | Verified (21 Nov). Fast release news. Has some duplicates; dedup by link. |
| Kevin Buzzard on Bluesky | `https://bsky.app/profile/xenaproject.bsky.social/rss` | Verified (5 Dec 2025). Rarely active; the blog is the better source. |
| Terence Tao on Mathstodon | `https://mathstodon.xyz/@tao.rss` | Verified (10 Nov). |
| #leanprover on Mathstodon | `https://mathstodon.xyz/tags/leanprover.rss` | Verified (21 Nov). |
| #formalverification on Mathstodon | `https://mathstodon.xyz/tags/formalverification.rss` | Verified (21 Nov). |

The Lean community page also lists Lean on Mastodon (`functional.cafe/@leanprover`), which was not opened, and Lean on X and LinkedIn, which this plan does not use.

### Dead or not usable

Papers with Code (shut down in July 2025), Galois blog feed (not working since early 2025), Rocq Planet (empty feed), Nitter (shut down), and the lean-lang.org blog (no feed found; use the Lean FRO Bluesky feed instead). No feed was found either for Leo de Moura's pages, the Isabelle users mailing list or the Lean Zulip archive.

### Recommended additions for version 1

1. Archive of Formal Proofs: clean weekly feed of new Isabelle work.
2. Xena blog: the most on-topic Lean blog, active this month.
3. Lean FRO on Bluesky: the quickest free route to Lean release news.
4. Rocq Discourse latest: one feed for Rocq news.
5. Quanta Magazine, mathematics and computer science: free, high-quality AI for math coverage.
6. Hugging Face Daily Papers: daily AI papers with an upvote signal.
7. Terence Tao's and Lawrence Paulson's blogs, with the keyword filter.
8. Import AI: a weekly summary of AI research.
9. #leanprover and #formalverification on Mathstodon.

Start the rest disabled in `config/sources.yaml` so Jin can switch them on. Broad feeds (Tao, DeepMind, OpenAI, Microsoft Research, Amazon Science, Quanta, Simon Willison, LessWrong, IEEE Spectrum) must pass the keyword filter or the Claude step before they appear.

## Phases and steps

Seven phases, each small enough to review on its own. Claude Code commits at the end of each phase and stops if an acceptance check fails.

### Phase 0: Repository and Pages

1. Create the repository from the Required rows in What Jin needs to provide, with `main` as default branch, a README and an MIT or BAIF licence.
2. In Settings, set Pages source to GitHub Actions and Actions workflow permissions to read and write.
3. Add a placeholder `site/index.html` and a minimal deploy workflow using `actions/upload-pages-artifact` and `actions/deploy-pages`.

Acceptance: the placeholder page loads at the Pages address.

### Phase 1: Fetchers

1. Python 3.12 package `news/` with one module per kind of source: `arxiv.py`, `arxiv_rss.py`, `github_releases.py` (Lean 4, Agda and Rocq releases), `hackernews.py`, `hf_papers.py` (Hugging Face Daily Papers JSON), optional `zulip.py`, and a generic `feed.py` that reads every RSS or Atom source (Lean blog, Archive of Formal Proofs, Xena, Rocq Discourse, Quanta, the Bluesky and Mastodon feeds and the rest of More sources).
2. Sources and keywords live in `config/sources.yaml`, so Jin can edit them without touching code.
3. Each fetcher returns items in the common schema below, sets a timeout of 20 seconds, retries twice, and sends a plain User-Agent such as `baif-news-bot (+repository URL)` with no personal contact details.
4. Each entry in `config/sources.yaml` has `enabled`, a type tag and `filter: none | keywords`. Switch on the recommended version 1 additions from More sources and leave the others disabled. Broad feeds use `filter: keywords`.
5. Bluesky and Mastodon posts get the type tag `social`, keep the author's name, link back to the post and keep at most the first 300 characters. Do not write fetchers for X, LinkedIn, Threads or Reddit.
6. Save one real response per source under `tests/fixtures/` and write a parser test for each.

```json
{
  "id": "arxiv:2611.01234",
  "type": "paper | lean | news | blog | social",
  "source": "arXiv cs.LO",
  "title": "...",
  "url": "https://arxiv.org/abs/2611.01234",
  "authors": ["..."],
  "published": "2026-11-22T01:00:00Z",
  "fetched": "2026-11-22T03:17:00Z",
  "source_text": "first 600 characters of the abstract or description",
  "summary": null,
  "score": null
}
```

Acceptance: `python -m news.fetch --dry-run` prints at least one item per enabled source, and `pytest` passes offline from the fixtures. A manual workflow run prints each enabled feed's HTTP status and item count from inside GitHub Actions, because the More sources checks were made outside it.

### Phase 2: Dedup and storage

1. Normalise each URL (lower-case host, drop `utm_*` and fragments, arXiv links to `abs/<id>` without the version) and build `id` from it.
2. Drop an item if its `id` is in `data/seen.json`, or if its normalised title matches an item from the last 7 days (catches the same paper on arXiv and Hacker News).
3. Merge new items into `data/news.json`, newest first, capped at 300 items; keep `seen.json` entries for 90 days.
4. Also append every new kept item to `data/archive/YYYY-MM.json`, by month published. Archive files are never trimmed, so search covers every item since launch, while `news.json` stays the small 300-item file for the front page.
5. Rebuild `data/search.json` from the archive: one compact row per item (id, type, source, title, url, published, and the first 300 characters of `summary` or `source_text`). Only items that pass the keyword or Claude filter are archived. At about 50 kept items a day it grows by about 9 MB a year, or 2 to 3 MB after the compression GitHub Pages applies. When it passes 15 MB, split it into one file per year and load the current year first.
6. Only if meaning search is switched on (Phase 4b): compute a 384-number vector for each new kept item and append it to `data/embeddings.bin` as 8-bit integers (384 bytes per item, about 7 MB a year), with `data/embeddings_ids.json` giving the order.
7. Write `data/meta.json` with the run time, item counts per source and any source errors.
8. Validate both files against `schema/news.schema.json` before writing.

Acceptance: two runs in a row add zero items the second time; a broken fixture makes that source report an error while the others still save.

### Phase 3: Claude filter and summary (optional)

Details in the Claude section below. Built behind the flag `USE_CLAUDE`, on only when the `ANTHROPIC_API_KEY` secret exists.

Acceptance: with no key the pipeline behaves as in Phase 2; with a key, every new item gets `relevant`, `type` and a summary of at most 160 characters, and the run logs input and output token counts.

### Phase 4: The page

1. Plain `site/index.html`, `site/style.css` and `site/app.js`, no build step and no framework. `app.js` loads `data/news.json` and `data/meta.json`.
2. Follow the BAIF News Sketch and the tokens in Page design below: header, title, "last updated" line in Singapore time, filter buttons, item list, sources column, footer.
3. Show 50 items, then an "Older items" button that reveals the rest.
4. Filters update the URL hash (for example `#papers`) so a filtered view can be shared.
5. Add a `feed.xml` (Atom) built by the Python job so readers can subscribe.

Acceptance: the page works at 400 px and 1440 px wide, passes an accessibility check (keyboard focus visible, contrast at least 4.5 to 1), loads no scripts from other sites, and matches the sketch side by side.

### Phase 4b: Search

Search covers the whole archive, not only the front page, and runs entirely in the reader's browser, so there is still no server. The sketch shows the search box above the type buttons, with a "Match meaning" switch beside it.

**Tier 1, word search with typo tolerance (build this).** Use MiniSearch (npm `minisearch`; 7.2.0 is the newest version seen on 22 Nov 2026, so check npm and pin the newest stable). It does prefix search, fuzzy search and field boosts. Copy it into `site/vendor/` so the page loads no outside scripts. Fuse.js (7.5.0 stable) is the alternative; it is simpler but scans every item on each search, so MiniSearch's index scales better as the archive grows.

1. Load `data/search.json` the first time the reader clicks or types in the search box, and build the index in a Web Worker so typing never stutters.
2. Fields and boosts: title 3, source and authors 1.5, summary or text 1.
3. Matching: prefix on, fuzzy 0.2 (about one typo in a five-letter word, two in a ten-letter word), all words required. If that finds nothing, retry with any word and say so above the results.
4. A short synonym list in `config/search_synonyms.yaml` (for example ATP and automated theorem proving, Coq and Rocq) that Jin can edit.
5. Results replace the list, show a count such as "23 results for isabell", highlight matched words and respect the type buttons. A Relevance or Newest switch orders them.
6. The query goes in the URL (`?q=isabelle&type=papers`) so a search can be shared; `/` focuses the box and Escape clears it.

**Tier 2, meaning search (optional, Jin decides).** The "Match meaning" switch finds items about the same idea in different words, for example "verified operating system kernel" finding a seL4 post.

1. In the workflow, embed each new kept item with the sentence-transformers model all-MiniLM-L6-v2 (cache the model between runs with `actions/cache`) and append the vector to `data/embeddings.bin`.
2. In the browser, only when the switch is turned on, load Transformers.js (npm `@huggingface/transformers`; 4.3.1 is the latest release seen) and the 8-bit ONNX copy of the same model (`Xenova/all-MiniLM-L6-v2`, `model_quantized.onnx`, 23 MB), embed the query and rank by cosine similarity.
3. Merge the word and meaning rankings with reciprocal rank fusion, so exact title matches still come first.
4. Host the model and the Transformers.js runtime under `site/models/` and `site/vendor/` so nothing loads from another site. The size of the ONNX Runtime WebAssembly file was not checked; measure it in this phase.

Trade-offs: tier 2 costs nothing to run (the vectors are computed in the free workflow and no API key is needed), but a reader who turns it on downloads the 23 MB model once, plus the runtime and the vectors, and waits a few seconds for the first meaning search. Tier 1 needs no download beyond the search file. Recommendation: ship tier 1 in version 1 and add tier 2 if word search misses too much.

Jin needs to provide nothing new for search: no key and no account. The only decision is whether tier 2 is wanted.

Acceptance:

- "isabell" finds Isabelle items, "formalisation" finds formalization items, and "lean4" finds "Lean 4".
- With a test archive of 20,000 generated items, a search finds an item older than the 300 in `news.json`; the search file is under 4 MB compressed; results appear within 200 ms of typing in Chrome with the 4x CPU slowdown.
- Search and the type buttons combine correctly, and a shared `?q=` link reopens the same results.
- The browser's network panel shows no request to another site, with or without tier 2.
- Tier 2 only: "verified operating system kernel" ranks a seL4 test item in the top 5, even though the item does not use those words.

### Phase 5: Scheduled workflow

1. `.github/workflows/update.yml` with the schedule and settings in the Scheduling section.
2. Steps: check out, set up Python with pip cache, run `python -m news.run`, commit `data/` and `site/feed.xml` only if they changed, then deploy Pages.
3. A failing source must not fail the job; a failing job sends the usual GitHub email to the repository's watchers.

Acceptance: a manual run (`workflow_dispatch`) commits new data and redeploys; the next scheduled run starts on its own; a run with nothing new makes no commit.

### Phase 6: Launch

1. Let the schedule run for 24 hours and read the 24 runs in the Actions tab and `meta.json`.
2. Jin reviews about 30 items for relevance and adjusts keywords.
3. Model check: run one day of new items through both Claude Haiku 5.5 and Claude Sonnet 5.5 with the same prompt and schema, and compare the relevance tags and summaries. Haiku may slip on formalization jargon, for example treating a paper that only mentions "verification" in passing as a real Lean or Isabelle result. If Haiku misjudges too many items, move only the relevance tagging to Sonnet 5.5 (about $13 a month at hourly runs, an estimate) and keep Haiku for summaries.
4. Add the logo file, set the custom domain if chosen, and write the README (what it is, how to add a source, how to rotate the API key).

Acceptance: 24 runs in 24 hours, none failed, no duplicate items, the Haiku and Sonnet comparison is written down with the chosen model, and Jin is happy with the item mix.

## Claude filter and summary (optional)

One Claude request per run, covering only items never seen before (after dedup), costs well under a cent per hourly run with Claude Haiku 5.5, or about $0.65 a month at 24 runs a day, about 40 times less than Claude Opus 5.5. A run with no new items sends no request. Without the API key the site still works, using keyword matching and the source's own text.

### What the request does

- Input: the new items (id, source, title, first 600 characters of `source_text`), at most 100 per run; extra items wait for the next run.
- Task: for each item, decide `relevant` (is it about AI and formal methods, proof assistants or verification?), pick `type`, give `score` from 1 to 5, and write a plain one-sentence `summary` of at most 160 characters.
- Output: structured output with a JSON schema, so the reply always parses. In Python use the Anthropic SDK's `client.messages.parse(...)` with a Pydantic model, which sends the schema as `output_config.format`.
- Model and settings: `model="claude-haiku-5-5"`, `output_config.effort="low"` set explicitly (Haiku 5.5 defaults to medium), thinking left adaptive (it may be turned off at this effort if the summaries hold up without it), `max_tokens=16000`, still through `client.messages.parse` with the Pydantic schema.
- Refusals: Haiku 5.5 has no server-side refusal fallback, so always check `stop_reason` before reading the content. On `refusal`, retry those items one at a time; keep any item that still refuses with `summary: null` (keyword filter only), and log its `stop_details` category in `meta.json`.
- Safety: item text comes from strangers, so the system prompt says it is data, never instructions. The page inserts summaries as text, never as HTML.
- Failure: on any API error, keep the items with `summary: null` and retry them on the next run.
- Model choice: Haiku 5.5 is a good fit, because each item is a short title and abstract and the step only tags relevance and writes one sentence. Make the model name a setting in `config/sources.yaml` or an environment variable, so the relevance step can move to `claude-sonnet-5-5` after the Phase 6 check without a code change.

### Cost per run

Prices from the Anthropic model table as of November 2026: Claude Haiku 5.5 at $0.10 per million input tokens and $0.50 per million output tokens, for prompts up to 100K tokens.

| Case | Input tokens | Output tokens | Cost per run | Per month (720 runs) |
| --- | --- | --- | --- | --- |
| Typical hour, about 10 new items | about 3,900 | about 1,000 | about $0.0009 | about $0.65 |
| Busy hour, about 35 new items | about 9,700 | about 2,800 | about $0.0024 | about $1.70, if every hour were that busy |

These are estimates that leave out thinking tokens, which stay small at low effort; the job logs `usage.input_tokens` and `usage.output_tokens` each run, so the real figure shows after one day. The same work on Claude Opus 5.5 would cost about $26 a month.

Hourly runs cost about the same as runs every 3 hours, because news arrives at the same rate; only the fixed instructions (about 1,600 tokens) are sent more often, and skipping runs with nothing new cuts that further.

## Scheduling with GitHub Actions

Run at minute 17 of every hour, UTC, which is also minute 17 of every hour in Singapore. Keep the repository public: Actions on standard GitHub-hosted runners are free for public repositories, per [GitHub's billing page](https://docs.github.com/en/actions/reference/usage-limits-billing-and-administration).

For a private repository the runs would count against the organization's included Actions usage: 2,000 minutes a month on GitHub Free for organizations and 3,000 on GitHub Team, per GitHub's included usage page. Hourly runs of one to two minutes each come to about 720 to 1,440 minutes a month, so a public repository is the safe choice.

```yaml
on:
  schedule:
    - cron: "17 * * * *"   # UTC
  workflow_dispatch: {}

permissions:
  contents: write    # commit data/
  pages: write       # deploy
  id-token: write    # deploy

concurrency:
  group: news-update
  cancel-in-progress: false

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 10
```

Three caveats from [GitHub's schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows):

- **Late starts:** scheduled runs "can be delayed during periods of high loads", worst at the top of the hour. Minute 17 avoids the peak, and the page shows the real run time from `meta.json`, never the planned one.
- **60 days of inactivity:** "In a public repository, scheduled workflows are automatically disabled when no repository activity has occurred in 60 days." The job commits new data on most runs, which should keep the repository active, but this is not confirmed for bot commits. As a safeguard, the README tells maintainers how to re-enable the workflow, and the page shows a warning when `meta.json` is more than 3 hours old.
- **Default branch only:** schedules run only on `main`, so test changes with `workflow_dispatch` on a branch first.

The job must also not overlap with itself (the `concurrency` group above) and must pull before it commits, so a manual edit to `config/sources.yaml` is never lost.

### Hourly runs: commits and politeness

- **Commit only when data changed.** Run `git diff --quiet data/` and commit only if it reports a change, so an hour with nothing new adds no commit. The worst case is about 720 commits a month; in practice fewer, because many hours bring nothing.
- **Keep the run time out of the commit.** Write `meta.json` into the Pages artifact and deploy on every run (a deploy is not a commit), so the last-update line stays current; commit `meta.json` only together with a data change.
- **Ask feeds what changed.** Store each feed's `ETag` and `Last-Modified` in `data/http_cache.json`, send `If-None-Match` and `If-Modified-Since` on the next request, and treat a 304 reply as no new items.
- **Fetch slow sources less often.** Give each source an `every:` value in `config/sources.yaml`. arXiv updates once a day (around midnight US Eastern), so fetch it once a day after that; blogs, newsletters, AFP and Rocq every 6 hours; Hacker News, Bluesky, Mastodon and the release feeds every hour.
- **Skip the Claude call** when a run finds no new items.

## Page design

Copy the look of [beneficialaifoundation.org](https://www.beneficialaifoundation.org/): a black page, white text, bold geometric headings, square outlined buttons. The full mock-up is `baif-news-sketch.html` in the same folder as this file; use its HTML and CSS as the starting point. It already shows the search box with the "Match meaning" switch and the `/` shortcut hint, the Social filter, and "Updated every hour". Values were measured from screenshots of the BAIF home and Research pages on 22 Nov 2026, not read from the site's CSS.

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `#000000` | Page background |
| `--fg` | `#FFFFFF` | Text, headings, strong rule under the title |
| `--link` | `#D1D1D1` (approx.) | Summaries, underlined links in text |
| `--muted` | `#9A9A9A` | Source and age line (new; not on the BAIF site) |
| `--rule` | `#444447` (approx.) | Lines between items, footer top border |
| `--rule-footer` | `#4E4E4E` (approx.) | Footer divider, type tags |
| `--btn-hover` | `#D8D8D8` (approx.) | Button and active filter fill, with black text |
| `--green` | `#15C639` (approx.) | Logo dot and the "updated" dot only |
| Heading font | Manrope 700, letter spacing about -0.02em | Title (about 64 px), item titles (24 px) |
| Body font | Poppins 400 | Body (16 to 18 px), menu, buttons (13 px) |
| Side margin | about 62 px at 1440 px wide, at least 16 px on phones | Page gutter |

Layout, top to bottom: logo and menu (News, Sources, About, BAIF home); title "AI and formalization news" with the last-update line; a white 1 px rule; the search box; filter buttons (All, Papers, Lean and Mathlib, News, Blogs, Social); the item list beside a Sources and About column; the BAIF footer with tagline and copyright. Load the two fonts from Google Fonts with system fallbacks; no other outside requests.

Other details seen on the BAIF site:

- Logo: white wordmark "BENEFICIAL / AI FOUNDATION" with a broken white ring and a green dot.
- Menu: right aligned, white Poppins, the active item underlined.
- Body text: about 18 px with a line height of about 1.5; links in body text are underlined light gray.
- Buttons: square, transparent, 1 px white border, small Poppins label; hover fills light gray with black text.
- Section headings on the home page are centred; the footer has the logo, tagline, a divider and the copyright line.
- The BAIF hero images are photographs with a blue and purple glow; the news page does not need them.

Not confirmed: the exact font families and sizes (read from letter shapes, not computed styles), the exact link colour and hover states other than the button's.

## Risks and open questions

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Scheduled workflow disabled after 60 quiet days | Page stops updating without an error | Stale-data warning on the page; README steps to re-enable |
| A source changes its format or blocks the bot | That source goes quiet | Per-source error in `meta.json`; fixture tests; the other sources keep working |
| Off-topic Hacker News items | Noise on the page | Keyword match plus `points >= 5`; Claude relevance check when enabled |
| Text in an item tries to steer Claude | Odd summaries | Schema-bound output, item text marked as data, summaries rendered as text |
| API cost higher than estimated | Monthly bill | Spending cap on the key; cap of 100 items per request; token counts logged |
| Organization settings block Pages or Actions | Phase 0 cannot finish | Ask an organization admin before starting |

Open questions for Jin:

- [ ] Repository name, and whether a separate repository or a folder in an existing site.
- [ ] Use the Claude summaries from the start, or launch with keyword filtering first?
- [ ] Is Lean Zulip worth the bot setup, and is the Lean community comfortable with it?
- [ ] github.io address or a BAIF subdomain?
- [ ] Meaning search (tier 2) in version 1, or later? Default: launch with word search only and add meaning search if word search misses too much.
- [ ] Pay for the official X API (about $4.50 to $24 a month), or keep X out? Default: keep X out.
- [ ] After the Phase 6 check: keep Haiku for relevance tagging, or move that step to Sonnet?

The sketch's HTML is `baif-news-sketch.html`, in the same folder as this file. Hand both files to Claude Code together.
