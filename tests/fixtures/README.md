# Test fixtures

One saved response per source, named after the source id in `config/sources.yaml`.
Saved on 23 Nov 2026 with the bot's User-Agent, except:

- `arxiv-rss.xml` is constructed: the live feed was empty at the weekend, so it uses the
  rss.arxiv.org item format filled with five real papers from `arxiv-api.xml`.
  `arxiv-rss-weekend.xml` is the real (empty) weekend response.
- `lean-zulip.json` is synthetic, shaped like the Zulip `GET /api/v1/messages` reply,
  because no bot credentials exist yet.
- `hackernews.json` is the real reply for the query "Lean 4"; no story in it had 5 points yet.

To refresh a fixture, download the source's URL again and run `pytest`.
