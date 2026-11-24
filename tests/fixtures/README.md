# Test fixtures

One saved response per source, named after the source id in `config/sources.yaml`.
Saved on 23 Nov 2026 with the bot's User-Agent, except:

- `arxiv-rss.xml` is constructed: the live feed was empty at the weekend, so it uses the
  rss.arxiv.org item format filled with five real papers from `arxiv-api.xml`.
  `arxiv-rss-weekend.xml` is the real (empty) weekend response.
- `lean-zulip.json` and `lean-zulip-discussions.json` are synthetic: each maps a channel to a
  reply shaped like the real Zulip `GET /api/v1/messages` reply, so the repository stores no
  one's messages. They hold topics on both sides of each rule (an old topic, a busy topic with
  too few people, a quiet topic). The live channels were checked with the bot on 24 Nov 2026.
- `hackernews.json` is the real reply for the query "Lean 4"; no story in it had 5 points yet.
- The fixtures for `mathstodon-itp`, `mathstodon-agda`, `mathstodon-rocq`, `mathstodon-isabelle`,
  `rocq-changelog` and `agda-releases` were saved on 24 Nov 2026. Items published after the tests'
  fixed time (23 Nov 2026, 03:17 UTC) were removed, and `agda-releases.json` keeps only the fields
  the parser reads, with release notes cut to 1,500 characters.
- `forsyte.xml` and `sigil-logic.xml` were saved on 24 Nov 2026. `forsyte.xml` keeps the newest 30
  dated items of the feed's 334, plus two of its 159 static pages, which are dated 1 Jan 0001 and have
  empty titles; the 30-day window always drops those.

- `anthropic-formal-math.xml`, `openai-math.xml`, `deepmind-blog.xml` and `openai-news.xml` were
  saved on 24 Nov 2026. Items published after the tests' fixed time were removed, among them the
  VP != VNP commits and DeepMind's post on quantum algorithms. `deepmind-blog.xml` keeps the newest
  30 posts. `openai-news.xml` keeps the newest 30 of its 1,425 posts, plus "Sharing AI progress in
  mathematics" (6 Oct 2026), the one post the keyword filter keeps.

To refresh a fixture, download the source's URL again and run `pytest`.
