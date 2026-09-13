# CLAUDE.md — Weekly Betting Cheat Sheet

This file governs how Claude Code works in this repo. The full master plan lives at
`../docs/betting-cheat-sheet-plan.md` (outside this repo, in the job folder). Read it
before doing any work beyond Phase 0.

**Current status: Phase 1 (math + page) complete.** `odds.py`, `build.py`,
`templates/page.html`, and a hand-written sample week JSON
(`data/weeks/sample-phase1.json`, `is_sample: true`) exist and are tested.
`docs/index.html` currently renders that SAMPLE data (fictional team names, a
loud "do not place these bets" banner) — it is not a real card yet. Do not
build `grade.py` or the `/cheatsheet` slash command until Gus explicitly moves
the project to Phase 2+.

Scoreboard math is verified against the real `data/bet_log.csv`: 1 W / 1 CO /
18 L / 2 Open, cash P/L ≈ –$55.93, bankroll remaining ≈ –$5.93 (not floored at
zero — the tool is honest about being underwater), current streak L13.

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

1. **Bankroll:** $50 for the season plus winnings. **Weekly budget: $5.** The page never recommends more than $5 total per week across NFL + CFB.
2. **Pre-kickoff only.** Sheet is built Friday night (CFB) and Saturday night (NFL). No live-bet recommendations.
3. **Bet first, boost second.** Pick the ticket on merit, then check whether an available boost happens to fit. Never build a ticket to qualify for a boost.
4. **Leg count follows probability.** A parlay is fine when every leg is ≥60% to hit (the NBA winner). Coin-flip legs cap the ticket at 3.
5. **Every odds number is shown with its implied probability.** Gus should never have to guess what –455 or +2100 means.
6. **Straight bets are the default.** The weekly card is one straight bet plus at most one small parlay.
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
  data/
    bet_log.csv              # every bet Gus places (seed from dk_bet_history.csv)
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
