---
description: Grade finished bets, research today's slot, build the three-tier card, publish the phone page (runs unattended on a schedule)
---

# /cheatsheet [auto|next|thu|sat|sun|mon]

- `auto` (default, what the 9 AM schedule uses): the slot is today's (`today_slot`); Tue/Wed have
  no slot, so those runs only grade and republish.
- `next`: build the slot **after** today's (`next_slot`) — e.g. Saturday evening → Sunday's card.
  Used for a manual "give me tomorrow's card early" run.
- `thu|sat|sun|mon`: that slot of the current weekend.

This command runs **unattended** from a scheduled task on Gus's Mac — nobody is watching.
Publishing is automatic; `build.py`'s rules are the safety net. Read `CLAUDE.md` in full first
(ground rules 1–12, research playbook).

Repo: `/Users/gusjohnson/HQ/ME/Personal Goose/Betting Cheat Sheet/Engine`. Run every command from
there. Times are US Eastern.

## 0. Sync

`git pull --ff-only`. If it fails (diverged), stop, don't force anything, and report why.

## 1. Where are we

`python3 src/slate.py status` → weekend id, today's slot (or none on Tue/Wed), this slot's budget
(`easy`, `fun`, `total`, and the plain-English `why`), whether the weekend's lottery ticket is
still available, and open bets. Use those stake numbers exactly — never exceed them.

For `next` or an explicit slot: take that slot's date (`next_slot_date`, or the matching day of
the current weekend) and run `python3 src/slate.py status --date <that date>` to get **that**
slot's budget. Never pass `--date`/`--now` to `build.py --publish` — the kickoff check must use
the real clock.

## 2. Grade first

`python3 src/grade.py pending` lists bets whose games should be over. For each: find the real
final score / player stat line (box score pages — fetch the page itself, don't trust a search
summary), then `python3 src/grade.py set <wid> <slot> <tier> Won|Lost|Push|Void`. A parlay is
Won only if every leg hit (Void legs per DK rules → if unsure, leave it pending and say so). If a
result can't be confirmed from a real source, leave it pending — never guess. Grading first
matters: the next slot's budget depends on it.

If there's no slot today (Tue/Wed) or no games left in the slot (season over), skip to step 5
after grading.

## 3. Research today's slot

Slot → games: **thu** = Thursday NFL · **sat** = Saturday college football (ranked/notable games
plus any NFL Saturday games late in the season) · **sun** = every Sunday NFL game (1 PM, 4 PM,
SNF) · **mon** = Monday NFL. Only consider games kicking off **at least 60 minutes from now**.

For the candidate games, from real sources (WebSearch to find, WebFetch to read the page):
- DraftKings lines (spread, total, moneylines) and **player props**. If DK's own number isn't
  findable, use another named book and say which.
- Injury reports / inactives, weather for outdoor games.
- **≥2 independent win/probability sources** per bet you might put on the card (prediction
  markets like Kalshi/Polymarket, models like SportsLine, numberFire, Dimers, Stats Insider,
  ESPN FPI). Record each in `prob_sources` as `{"name", "prob"}`.
- Records: flag every **0–2 team** (desperation angle). Each team's **sack leader** and the
  opposing QB (skip vs mobile QBs).
- For props, a projection source (e.g. FantasyPros / numberFire projections vs the line) counts
  as a probability source only if it states a probability or you can show the math plainly.

## 4. Build the slot card

Write `data/weekends/<wid>/<slot>.json` (copy the shape of the newest existing slot file; include
the `budget` snapshot from step 1 and an `angles` list). Order matters:

1. **Easy Bet** — one straight bet, highest probability with a real (averaged, ≥2-source) edge,
   stake = `budget.easy` (or less if nothing clears the bar — say so). Sources must agree within
   10 points (rule 11). Near-zero edge is fine if it's still the best, safest bet; say so plainly.
2. **Fun Parlay** — 2–3 legs, every leg ≥55%, stake = `budget.fun` (or omit it and give the Easy
   Bet the full `budget.total`).
3. **Game-script check** (rule 10) — tag every bet/leg `neutral` / `<TEAM> leading` /
   `<TEAM> trailing`; the Easy Bet and a Fun Parlay leg can't need the same side ahead.
4. **Tier 3 — Lottery Ticket or Fun Parlay #2.** Lottery Ticket only if `lottery_available` and
   this is the **Sunday** slot (or Monday if Sunday passed without one): 10–20 real legs, stake
   exactly $0.50, real combined probability. **Every other slot gets `fun_parlay_2`** — a
   different variety of Fun Parlay (different legs/games, e.g. a 3-leg mid-favorite ticket for a
   bigger price), same leg rules and game-script check. `fun_parlay` + `fun_parlay_2` stakes
   together = `budget.fun` (split it, e.g. $0.15 / $0.10; each ≥ $0.10 unless the fun budget is
   too small for two, in which case skip #2 and say so). Grade it with tier `fun_parlay_2`.
5. **Leg bank — the full prop board** (Gus, 2026-09-26: "add in all the individual odds…
   whatever else you can think of to give me the best odds"). Aim for ~40–60 real entries across
   the slot's games, covering every market that has a readable price: anytime TD, QB pass TDs,
   D/ST (defense TD, team sacks), special teams / kicker (FGs, kicking points), tackles+assists,
   sacks (`opp_qb` required; never vs `config.mobile_qbs`), receiving yards, receptions, rushing
   yards, rush+rec yards, plus game lines. Use these `market` keys: `anytime_td`, `pass_td`, `dst`,
   `special_teams`, `tackles`, `sacks`, `receiving_yards`, `receptions`, `rushing_yards`,
   `rush_rec_yards`, `passing_yards`, `moneyline`, `spread`, `total`. Each entry: `selection`,
   `player`, `game`, `kickoff`, `game_script`, `estimated_prob` (the average of `prob_sources`),
   `prob_sources` (`[{name, prob, url}]`, ≥2 stated probabilities; with only one, set
   `single_source: true` and it stays off the card), and the price — `dk_odds` only when it's
   DraftKings' own number read on a page, otherwise `odds` + `book` (never label another book's
   price as DK). The Legs tab puts the positive-edge, 2+-source legs at the top ("Best value")
   and lets Gus filter by market. When a prop in the Best value list clears the 55% floor and
   beats a parlay leg on edge, use it in a Fun Parlay (props count as legs like any other).
   Then boost check (real promos or an honest "none found"), avoid list (3–5 real traps).

Every bet and leg needs `game` ("AWAY @ HOME"), `kickoff` (ISO with offset), `game_script`,
`reason_summary` (one line — Gus scans, he doesn't read), `reason` (full, behind tap-to-expand),
and `verify` (source, fetched_at, "confirm the price in the DK app"). No blacklisted players
(`config.blacklist`). If `budget.total` is 0 (stop-loss hit), still publish the card with $0
stakes and a clear "stop-loss reached — watch only" summary.

## 5. Build, test, publish

```
python3 -m unittest discover -s src -p "test_*.py"
python3 src/build.py --publish <wid>/<slot>      # plain `python3 src/build.py` on grading-only days
```
A `RuleViolation` means the card breaks a rule: fix the **data** (swap the bet, trim the stake)
and rebuild. Never edit a rule to get through. If it can't be fixed, don't publish the slot file
— publish grading only and report why.

Then `git add data/weekends docs/index.html` (never `data/bet_log.csv` — it's only appended from
Gus's real slips, never by this run), `git commit -m "<slot> card <date>: <easy bet>"`, and
`git push`. Poll `gh api repos/thunderbob34-boop/betting-cheat-sheet/pages/builds/latest` until
`built`, then confirm the live page shows the new card.

## 6. Report

End with a short report: the three bets (selection, odds, stake), this weekend's net and what's
left of the $5 stop-loss, anything graded, anything left pending or unverified. If a
PushNotification tool is available, send Gus one line: "<Day> card is up: <easy bet> $X,
<fun parlay> $Y". Facts only.
