---
description: Research real lines/context for the upcoming NFL or CFB slate, build the week JSON, show the diff, wait for approval before pushing
---

# /cheatsheet nfl|cfb

Argument: `$1` is `nfl` or `cfb`. If missing, ask which.

Read `CLAUDE.md` in full first — Section 3 is non-negotiable, and `src/build.py`
hard-fails on a card that violates it. Read `../docs/betting-cheat-sheet-plan.md`
for the full design if you haven't already this session.

## 1. Figure out which edition this is

- `nfl` → the next upcoming Sunday's NFL slate (the plan's convention is Saturday
  night research for Sunday games). `cfb` → the next upcoming Saturday's slate.
- Week file name: `data/weeks/<season>-<sport>-wk<NN>.json`, e.g.
  `2026-nfl-wk03.json`. Check `data/weeks/` for the most recent prior edition of
  this sport to know the last week number and to diff against.
- Only research the single upcoming game day Gus actually asked about for this
  run (e.g. "Sunday's games") — not every game in the NFL week unless told
  otherwise.

## 2. Research — real data only, never fabricate

Use WebSearch for anything time-sensitive (lines, injuries, props, promos) —
prefer Firecrawl search if it's connected and working, but don't waste retries
on it if it's erroring; fall back to WebSearch. This environment's WebSearch is
grounded to the real current date, so current-week content is genuinely
findable.

**CRITICAL HONESTY RULES — these are not optional:**

- Every line, odds number, injury note, or probability must come from an
  actual search result you actually got back. An honest "not found" beats a
  plausible-sounding invention, always.
- **Never estimate a card bet's probability from a single source.** Find at
  least two independent real win-probability models or analytical predictions
  (e.g. a prediction-market-implied probability like Kalshi/Polymarket, plus a
  published model like Stats Insider, SportsLine, or similar) and average
  them. Cite both sources and show the averaging math in
  `estimated_prob_source`. This was a real mistake caught on the first real
  edition (2026-nfl-wk02) — a single-sourced estimate overstated the edge, and
  Gus caught it. Don't repeat it. If a genuinely averaged edge comes out small
  or negative, that's a correct, honest result — report it as such, don't
  chase a bigger number.
- When you get a number from a page, prefer fetching the actual page directly
  over trusting a search engine's auto-summary of it — summaries have been
  observed to garble a specific figure (a search summary showed 71% for a game
  where the actual page said 74%). If a number matters, confirm it against the
  real page text.
- If two sources disagree meaningfully (not just book-to-book vig), report
  both with their sources rather than silently picking one.

## 3. Build the week JSON

Follow the exact schema in the most recent existing week file (card, leg_bank,
boost_check, avoid_list, last_week_graded, verify blocks) — `data/weeks/sample-phase1.json`
demonstrates all three card tiers if you want a clean reference. `is_sample: false`.
Every guardrail in `src/build.py` (Section 3, all rules) must pass — if it
doesn't, fix the data, never the rule.

`week["card"]` is three named tiers, **built in this order** (a build-order
discipline like Rule 3 — nothing checks this mechanically, so actually do it
in order):

1. **`easy_bet`** (required) — a single straight bet, highest probability with
   real edge, ~$2-3. Fields: `id`, `selection`, `market`, `dk_odds`,
   `estimated_prob`, `estimated_prob_source`, `is_pre_kickoff: true`, `stake`,
   `reason_summary` (one line, always visible on the page), `reason` (the full
   reasoning — rendered behind a tap-to-expand, so it's fine for this to be as
   long as the research actually supports), `verify`.
2. **`fun_parlay`** (optional — at most one) — 2-3 legs, `legs: [{selection,
   estimated_prob, reason}]`, **every leg's `estimated_prob` >= 0.55** or
   `build.py` rejects it. Same top-level fields as `easy_bet` (`dk_odds` is the
   overall parlay price, `reason_summary`/`reason`/`verify` at the parlay
   level), ~$1-2.
3. **`lottery_ticket`** (optional — at most one, **only decide this after 1
   and 2 are set**) — one 10-20 leg parlay (`legs`, same leg shape as
   `fun_parlay`). `stake` must be **exactly 0.50** — not a range, not
   approximate. No leg-probability floor (that's the point — pick something
   genuinely long-shot if you're including this tier at all, don't water it
   down to look safer). Its stake is outside the $5/week line by design; don't
   let it influence tiers 1-2's sizing. `dk_odds` is the actual DK-listed/
   boosted payout odds for the ticket (used to show a "payout-implied"
   probability on the page, honestly contrasted against the real combined
   probability computed from the legs).

Only build a Lottery Ticket when you actually have 10-20 real, researched legs
to put in it — an empty/thin one is worse than skipping the tier entirely for
that edition.

Other sections, unchanged:
- `leg_bank`: real entries across multiple games/markets — `build.py` sorts by
  estimated_prob automatically, don't worry about order.
- `boost_check`: real current promos if found, honest "nothing found" if not.
- `avoid_list`: 3-5 real traps from the actual slate.
- `last_week_graded`: pull the real settled bets for the prior slate straight
  from `data/bet_log.csv` — never invent a result for an Open/unsettled bet.

Run `python3 -m unittest discover -s src -p "test_*.py" -v` and
`python3 src/build.py data/weeks/<new-file>.json` yourself. Both must succeed
before you show this to Gus.

## 4. Diff against the previous edition

Find the most recent prior week file for this sport in `data/weeks/`. Produce
a clear, human-readable diff for Gus: what changed in the card (same pick,
different odds/probability? a different pick entirely?), what changed in the
scoreboard (new results logged since last time), and call out anything that
moved meaningfully (a line move, an injury that flipped a recommendation).
If there's no prior edition for this sport yet, say so plainly instead of
diffing against nothing.

## 5. Show Gus — do NOT push yet

Present the new card, the diff from the previous edition, and any honesty
caveats (single-source gaps, source disagreements, etc.) in chat. Do **not**
run `git add`/`git commit`/`git push` at this point, even if this command was
triggered by a schedule/cron run and no one is watching live — wait for an
explicit approval message before pushing. This matches how every edition of
this page has been built so far; don't silently change that just because a run
happens to be unattended.

Once Gus approves (in this session or a follow-up message), commit with a
clear message (ending with the standing attribution lines for this session)
and push to `main`. Poll GitHub Pages until the build shows `built` and confirm
the live URL reflects the change before reporting done.
