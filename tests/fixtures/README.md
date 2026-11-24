# Test fixtures

One saved response per source, named after the source id in `config/sources.yaml`.
Saved on 23 Nov 2026 with the bot's User-Agent, except:

- `arxiv-rss.xml` is constructed: the live feed was empty at the weekend, so it uses the
  rss.arxiv.org item format filled with five real papers from `arxiv-api.xml`.
  `arxiv-rss-weekend.xml` is the real (empty) weekend response.
- `lean-zulip.json` is synthetic, shaped like the Zulip `GET /api/v1/messages` reply,
  because no bot credentials exist yet.
- `hackernews.json` is the real reply for the query "Lean 4"; no story in it had 5 points yet.
- The fixtures for `mathstodon-itp`, `mathstodon-agda`, `mathstodon-rocq`, `mathstodon-isabelle`,
  `rocq-changelog` and `agda-releases` were saved on 24 Nov 2026. Items published after the tests'
  fixed time (23 Nov 2026, 03:17 UTC) were removed, and `agda-releases.json` keeps only the fields
  the parser reads, with release notes cut to 1,500 characters.

To refresh a fixture, download the source's URL again and run `pytest`.
