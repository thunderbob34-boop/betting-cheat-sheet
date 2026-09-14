# CLAUDE.md — Weekly Betting Cheat Sheet

This file governs how Claude Code works in this repo. The full master plan lives at
`../docs/betting-cheat-sheet-plan.md` (outside this repo, in the job folder). Read it
before doing any work beyond Phase 0.

**Current status: Phase 2 (real weekly research) live.** `docs/index.html`
currently renders `data/weeks/2026-nfl-wk01-mnf.json` (a one-off single-game
edition, Under 43.5 on Broncos @ Chiefs MNF, Sept 14 2026) — built same-day,
same evening as kickoff, and pushed same-day. `data/weeks/2026-nfl-wk02.json`
(NFL Week 2, Sunday Sept 20 2026 — a real Easy Bet + Fun Parlay) is the
prior edition and is **not currently the one rendering** — `build.py` only
ever renders whichever week file it's pointed at; both files still exist and
either can be rebuilt any time. `is_sample: false` on both.
`.claude/commands/cheatsheet.md` is the real `/cheatsheet nfl|cfb` command —
read it before running or scheduling a new edition. `grade.py` (Phase 3) is
still not built.

**Card schema (2026-09-14): three named tiers, not a flat bet list.**
`week["card"]` is now `{"easy_bet": {...}, "fun_parlay": {...} | absent,
"lottery_ticket": {...} | absent}` — see rule 6 above for what each tier
requires. Every card/leg_bank/leg-bank-leg bet object needs both
`reason_summary` (one line, always visible) and `reason` (the full
reasoning, rendered inside a `<details>` tap-to-expand alongside the prob
source and verify block — see `_reason_detail_html`). `render_scoreboard`'s
"Weekly Budget Used" tile is no longer opt-in (the old `show_weekly_budget_tile`
field is gone) — it's always computed as `easy_bet.stake + fun_parlay.stake`
(never the Lottery Ticket's). All three existing week files
(`sample-phase1.json`, `2026-nfl-wk01-mnf.json`, `2026-nfl-wk02.json`) were
migrated to this schema the same day it was introduced — `sample-phase1.json`
demonstrates all three tiers, including a 12-leg Lottery Ticket example.

**Open cross-edition budget question, still unresolved as of 2026-09-14:**
tonight's MNF edition spends $2.00 of the $5/week cap on its own Easy Bet
tier. The already-built `2026-nfl-wk02.json` separately totals its own
Easy Bet + Fun Parlay at a full $5.00. If both are meant to share one
$5/week pool (Gus's own framing when he approved the MNF card was "this
week's $5"), `2026-nfl-wk02.json` needs trimming to fit within the ~$3
remaining before it's next approved/pushed — it has NOT been trimmed yet.
Today's tier restructuring doesn't resolve this on its own (each edition's
budget tile only reflects that one file's own tiers 1-2, not a running
total across multiple editions in the same real week). The scheduled Sat
9/19 8pm ET refresh routine (trig_01LMYBqnhdXhcZxogaZFP9jN) was created
before this constraint existed and does not know about it either — check
with Gus before pushing whatever that routine produces, and make sure it
reads `.claude/commands/cheatsheet.md` for the current tier schema (it will,
per its own prompt, but it was written to describe the old flat schema, so
say so explicitly if reviewing its output).

**Honesty rule learned the hard way on 2026-nfl-wk02:** never estimate a card
bet's probability from a single source — average at least two independent
real win-probability models and cite both. A single-sourced Kalshi-only
estimate overstated the edge (+5.2%) until Gus caught it; averaging a second
real source (Stats Insider) brought it to a more honest +4.2%. Also: prefer
fetching a page directly over trusting a search engine's auto-summary of it —
a summary once reported 71% for a page whose actual text said 74%.

**Rendering rule learned the same day:** real source citations include long
URLs inline in reason/verify text. `templates/page.html`'s `body` CSS has
`overflow-wrap: anywhere` for exactly this reason — don't remove it, and don't
assume "no fixed-width CSS" is enough evidence a page won't scroll sideways;
actually render it and measure `scrollWidth` vs `clientWidth` at 390px.

The scoreboard is split into two scopes, both computed fresh from
`data/bet_log.csv` on every run (`data/config.json` holds `season_start` and
`starting_bankroll`):

- **This Season** (bets on/after `season_start`, currently 2026-09-01) is the
  primary set of stat tiles — this is what "Bankroll Remaining" now means.
  Verified: 0 W / 0 CO / 7 L / 2 Open, cash P/L –$9.00, bankroll remaining
  $41.00, current streak L7.
- **All-time** (unchanged, includes the March–August NCAAB/MLB/World Cup
  history) renders as one muted footnote line below the tiles, labeled
  dynamically from the earliest row's date (currently "since Mar 2026"), not
  hardcoded. Verified: 1 W / 1 CO / 18 L / 2 Open, cash P/L ≈ –$55.93.

Neither number is ever floored at zero — the tool is honest about being
underwater if a scope's bankroll goes negative.

`src/build.py` hard-fails (raises `RuleViolation`, non-zero exit, page not
written) on any week JSON that would violate Rule 1 (>$5 card), Rule 2 (a live
or non-pre-kickoff bet), or Rule 6 (more than 1 straight + 1 parlay). Rule 4
(coin-flip legs cap a parlay at 3) is enforced for parlays with >3 legs. Rules
3 and 5 are structural (boosts rendered in a separate section, never driving
`card`; every odds number always rendered next to its implied probability).
Rule 7 holds trivially — grep confirms no network/HTTP code exists anywhere in
`src/`. Run `python3 -m unittest discover -s src -p "test_*.py" -v` before
touching any of these files again.

## Ground rules (plan §3 — non-negotiable, the page enforces them)

1. **Bankroll:** $50 for the season plus winnings. **Weekly budget: $5.** The page never recommends more than $5 total per week across NFL + CFB. (The Lottery Ticket tier's flat $0.50 — see rule 6 — is explicitly outside this line, by design, not an exception being quietly taken.)
2. **Pre-kickoff only.** Sheet is built Friday night (CFB) and Saturday night (NFL). No live-bet recommendations. No exceptions for any tier, including the Lottery Ticket.
3. **Bet first, boost second.** Pick the ticket on merit, then check whether an available boost happens to fit. Never build a ticket to qualify for a boost.
4. **Leg count follows probability.** For the Fun Parlay tier (see rule 6), every leg must be ≥55% to hit. The Lottery Ticket tier is explicitly exempt from any leg-probability floor — that's the point of it — but must still show its honest combined probability and "1 in X" plainly (see rule 6).
5. **Every odds number is shown with its implied probability.** Gus should never have to guess what –455 or +2100 means.
6. **The weekly card is three labeled tiers, in this order:**
   - **Easy Bet** (required, exactly one) — a single straight bet, highest probability with real edge, ~$2-3.
   - **Fun Parlay** (optional, at most one) — 2-3 legs, every leg ≥55% to hit, ~$1-2.
   - **Lottery Ticket** (optional, at most one, added 2026-09-14) — one 10-20 leg parlay, stake flat at exactly $0.50, targeting a big payout. Labeled plainly as a lottery ticket. Shows its honest combined probability (the real product of the legs, not the DK payout-implied number) and a "1 in X" figure next to it. Explicitly exempt from the leg-count/probability rule in 4 and from the $5/week line in rule 1 — it must never crowd out tiers 1-2. Built **only after** tiers 1 and 2 are set (a build-order discipline, like rule 3 — not something a finished file can prove, so this is enforced by following `.claude/commands/cheatsheet.md`'s order, not by `build.py`).
7. **Claude never places a bet.** The page is advice; Gus places every bet himself in the app.

## Audit rule

Anything the page or any tool in this repo could produce that recommends more than
$5/week total, a live bet, or a boost-first ticket **is a bug**. Full stop — treat it
as a correctness failure, not a style nit, and fix it before anything else.

## bet_log.csv is real money history

`data/bet_log.csv` contains Gus's actual DraftKings bet history. **Never edit the
values in existing rows** — not to "fix" a blank-details row, a fuzzy "(approx)" date,
an open/unsettled bet, or anything else. Only append new rows. If a row looks wrong,
leave it as-is; it's the historical record, not a bug.

## Weekly workflow (plan §8)

| When | Who | What |
|---|---|---|
| Fri evening | Claude Code | `/cheatsheet cfb` → research, write week JSON, build page, push |
| Sat evening | Claude Code | `/cheatsheet nfl` → same for NFL |
| Sat/Sun morning | Gus | Open page on phone, place bets in DK app, screenshot each bet slip |
| Sun night / Mon | Gus | Drop screenshots into Claude; Claude appends to `bet_log.csv` |
| Mon morning | Claude Code | `/grade` → pull finals, mark results, rebuild scoreboard, push |

Bet logging stays manual (screenshots) in v1.

## Repo layout (plan §7)

```
betting-cheat-sheet/
  CLAUDE.md                  # rules from §3, workflow from §8, house style
  .claude/
    commands/
      cheatsheet.md           # the real /cheatsheet nfl|cfb command (Phase 2)
  data/
    bet_log.csv              # every bet Gus places (seed from dk_bet_history.csv)
    config.json              # season_start + starting_bankroll — scopes the
                              # scoreboard to the current season; missing file
                              # falls back to in-code defaults, never crashes
    weeks/
      2026-nfl-wk02.json     # researched lines, legs, card, boosts for one edition
      2026-cfb-wk03.json
  src/
    odds.py                  # American ↔ implied probability, parlay math, edge
    build.py                 # week JSON + bet_log.csv → docs/index.html
    grade.py                 # scores → mark bets won/lost, update log + scoreboard
  templates/
    page.html                # single mobile-first template, no framework
  docs/
    index.html               # GitHub Pages output (the phone page)
    archive/                 # previous weeks' pages
```

## House style

- Mobile-first. Gus reads this on his phone, standing up, Saturday night.
- No frameworks, no build step beyond `python src/build.py` (once that exists). One HTML file.
- Function first. Field Instrument styling (the `my-style` skill / design system) comes
  in Phase 4, after the page works — do not spend Phase 0–3 effort on visual polish.
- Every number Gus could act on needs to be self-explanatory (see ground rule 5).
