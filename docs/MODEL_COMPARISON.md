# Haiku 5.5 and Sonnet 5.5 on the relevance and summary step

Phase 6 step 3 of the build plan. Run on 23 Nov 2026 with `python -m news.compare_models`. Raw judgements are in `docs/model_comparison/results.json`, and a side-by-side table of all 100 items is in `docs/model_comparison/report.md`.

## Set-up

- Items: the newest 100 items in the archive after the first real run (keyword filter only). This is exactly what the first production request would carry: 59 arXiv API papers, 34 Mastodon and Bluesky posts, and posts from Rocq Discourse, the Archive of Formal Proofs, Xena, the Lean blog, Lean releases, Paulson and Import AI.
- Both models got the same system prompt and schema (`news/claude.py`), effort `low`, one request each. A third run repeated Haiku at effort `medium`.

## Results

| | Haiku 5.5, low | Haiku 5.5, medium | Sonnet 5.5, low |
| --- | --- | --- | --- |
| Items judged relevant | 83 | 82 | 86 |
| Input tokens | 24,673 | 24,673 | 24,673 |
| Output tokens, including thinking | 12,478 | 15,301 | 8,196 |
| Time | 50 s | 60 s | 44 s |
| Cost of this 100-item run | $0.009 | $0.010 | $0.131 |
| Refusals, errors | 0, 0 | 0, 0 | 0, 0 |
| Summaries over 160 characters (cut by the job) | 0 | not checked | 6 |

- Relevance agreement between Haiku (low) and Sonnet: 95 of 100.
- Haiku at medium effort agreed with Haiku at low effort on 99 of 100 items, and made the same four "not relevant" calls that Sonnet disagreed with. More thinking does not change Haiku's judgement.
- Type agreement: 92 of 100. None of the 8 differences reach the page: social posts stay Social whatever the model says (decision D5), and the rest are news against blog for Import AI.
- Scores: mean absolute difference 0.35 on the 1 to 5 scale.
- Summaries: both models followed the style rules (no "This paper" openings). Haiku's are shorter (mean 103 characters, all within the limit); Sonnet's are slightly more specific with names but six ran over 160 characters and were cut.

## The five relevance disagreements, judged by hand

| Item | Haiku | Sonnet | Who is right |
| --- | --- | --- | --- |
| Kevin Buzzard's Xena blog: "A Modern Reorganization of Mathematics" (guest post on the Weil conjectures by a team that includes Mathlib contributors) | not relevant | relevant, 4 | Sonnet. The feed text is one line, but this is a Lean blog and the authors work on Mathlib. Haiku judged the thin text, not the source. |
| "Datatypes In, Zippers Out!" (arXiv cs.PL; the generated code is "proved correct against a reference traversal") | not relevant | relevant, 3 | Sonnet. The abstract claims correctness proofs. |
| "Self-verifiable LLM-driven science" (arXiv physics.chem-ph; proof assistants appear as a contrast) | not relevant | relevant, 2 | Haiku. This is the "mentioned only in passing" case the prompt describes. |
| "A Principled Approach to ... Choreographies" (arXiv cs.PL; operational semantics) | not relevant | relevant, 2 | Haiku, narrowly. Programming-language semantics without a verification result. |
| "Will AI mess my RTL?" (LLM-written SystemVerilog assertions for AI-written hardware) | relevant, 3 | not relevant | Either. Assertions are often checked formally, but the abstract does not say so. |

So each model made about two mistakes in 100. They differ in kind. Sonnet's two were extra items with a low score (2), which cost the reader little. Haiku's two were dropped items, and a dropped item is gone for good (it is removed and marked seen). One of them was a post from a source chosen because it is on topic.

The live Haiku runs on the local data (171 items, two runs, 48 dropped) confirmed this: the Xena guest post and the zipper paper were dropped. Both Import AI issues were dropped too, since they are general AI digests. With Claude on, Import AI will almost never appear, so it may as well be switched off.

## Cost per month

From the measured tokens, about 240 input tokens and 125 output tokens per item for Haiku (80 for Sonnet), plus about 850 tokens of fixed instructions per request:

| Model | Typical hour, 10 new items | Per month, if every hour had 10 items |
| --- | --- | --- |
| Haiku 5.5 | about $0.001 | about $0.70 |
| Sonnet 5.5 | about $0.015 | about $11 |

Hours with nothing new send no request, so real figures will be lower. The plan's estimates ($0.65 and about $13) hold.

## Recommendation

Keep Haiku 5.5, and stop it dropping items from sources that are on topic by construction. Add a per-source setting, for example `trusted: true`, for Xena, the Lean blog, Lean releases, the Archive of Formal Proofs, Lean FRO on Bluesky and Rocq Discourse. Items from these sources would always be kept, and Claude would only write their summary, type and score. Claude would still decide relevance for the broad sources (the arXiv API query, Hacker News, the Mastodon hashtags and the keyword-filtered feeds). This fixes the costlier of Haiku's two kinds of miss for nothing, and keeps the bill at about $0.70 a month.

If the occasional dropped arXiv paper (like the zipper paper) matters more than about $10 a month, move relevance to Sonnet 5.5 by setting the repository variable `CLAUDE_MODEL` to `claude-sonnet-5-5`; no code change is needed. Splitting the step (Sonnet for relevance, Haiku for summaries) would cost almost as much as Sonnet alone, because the expensive part is reading the items, so it is not worth the extra code.

Chosen model: **Claude Haiku 5.5 at effort `low`** (Jin, 23 Nov 2026). Import AI was switched off, and the `trusted` setting is built (decision D28 in `docs/IMPLEMENTATION_NOTES.md`).
