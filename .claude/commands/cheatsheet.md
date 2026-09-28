---
description: Grade finished bets, price today's slot against the sharp market, build the card (Value Bet or Pass + small Fun Parlay), publish the phone page (runs unattended on a schedule)
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
matters: the next slot's budget depends on it. A boosted bet pays at its `boosted_odds`
automatically. Passes are never graded.

**Price check (closing line).** For every straight card bet (`easy_bet`, `easy_bet_2`) from the
games just graded, read DraftKings' closing price from a real page (ESPN's game page odds block
shows DK's line; or a covers/odds-history page) and record it:
`python3 src/grade.py close <wid> <slot> <tier> <odds>`. If no page shows the close, skip it and
say so — never guess. This feeds the Record tab's "Price check".

If there's no slot today (Tue/Wed) or no games left in the slot (season over), skip to step 5
after grading.

## 3. Research today's slot — sharp price first

Slot → games: **thu** = Thursday NFL · **sat** = Saturday college football (ranked/notable games
plus any NFL Saturday games late in the season) · **sun** = every Sunday NFL game (1 PM, 4 PM,
SNF) · **mon** = Monday NFL. Only consider games kicking off **at least 60 minutes from now**.

The question is never "who will win?" It's "**where is DraftKings' price better than the sharp
market's fair price?**" (RULE 13, Gus 2026-09-28). In order:

1. **Fair odds.** `python3 src/sharp.py board` — Pinnacle's games (moneyline, spread, total) and
   player props with the cut stripped out (`fair_prob`). Then Kalshi for the same markets
   (`python3 src/sharp.py kalshi KXNFLGAME-<YYMONDD><AWAY><HOME>`, also `KXNFLSPREAD-`,
   `KXNFLTOTAL-`, and player props like `KXNFLREC-`); Polymarket/Novig if readable. These are
   the only probability sources that count for a card bet. For a prop, Pinnacle's line must be
   the same number as DK's (4.5 receptions vs 4.5) — a different line isn't comparable.
2. **DraftKings' prices** for the same markets, read on a real page (DK's own site, or a page
   quoting "via DraftKings" — ESPN odds, covers, scoresandodds). Never label another book's
   price as DK.
3. **Compare.** edge = fair_prob − DK implied. List every market with edge ≥ 2%. Props, alt
   lines and **DK boosts** are where these usually are — DK's main moneylines/spreads rarely beat
   Pinnacle. For each boost on the DK promos page, compute its value from the boosted price and
   the fair chance (a boost on a leg with no sharp price can't be checked — say so).
4. **Sanity.** Injury reports / inactives and weather (a line may already reflect news; a stale
   DK prop after a big injury is exactly the kind of edge to look for, but verify the news on a
   real page). Records: flag **0–2 teams**. Retail models (ESPN FPI, Dimers, SportsLine) can go in
   `reason` as color — never in `prob_sources`.

## 4. Build the slot card

Write `data/weekends/<wid>/<slot>.json` (copy the shape of the newest existing slot file; include
the `budget` snapshot from step 1 and an `angles` list).

1. **Value Bet** (`card.easy_bet`) — the straight bet with the biggest edge ≥ 2% vs the sharp
   fair price. `prob_sources` = ≥2 sharp sources (`{"name": "Pinnacle", "prob": <fair>, "odds":
   <this side>, "other_side_odds": <other side>, "url": ...}`, `{"name": "Kalshi", "prob": <mid>,
   "url": ...}`), `estimated_prob` = their average, sources within 10 points (rule 11). Stake =
   `budget.easy` (or split with #2). If a DK boost is what makes it +EV, put the regular price in
   `dk_odds`, the boosted price in `boosted_odds`, and name the boost (and its max wager) in
   `reason` and `plain_summary`.
   **If nothing clears 2%: Pass.** `{"pass": true, "stake": 0, "plain_summary": "...", "reason":
   "closest was X at DK -N vs fair Y% (edge Z%)"}`. A Pass is the right answer on most main
   lines — publish it without apology.
2. **Value Bet #2** (`card.easy_bet_2`, optional) — only if a second, different straight bet
   also clears 2%. Split `budget.easy` between them (bigger edge gets more). Never invent a
   second one to fill space.
3. **Fun Parlay** (`card.fun_parlay`, optional) — 2–3 legs, every leg ≥55%, stake ≤ `budget.fun`
   (≥ $0.10 or skip it). Prefer legs that are themselves at or near fair vs the sharp price
   (props welcome). It's for fun and the page says what it costs — keep it small. No second
   parlay, no Lottery Ticket (RULE 14). Leftover budget stays unbet.
4. **Game-script check** (rule 10) — tag every bet/leg `neutral` / `<TEAM> leading` /
   `<TEAM> trailing`; a straight bet and a Fun Parlay leg can't need the same side ahead.
5. **Leg bank — the full prop board.** Every market from step 3 that has both a DK price and a
   sharp fair price: `selection`, `market` (`anytime_td`, `pass_td`, `dst`, `special_teams`,
   `tackles`, `sacks`, `receiving_yards`, `receptions`, `rushing_yards`, `rush_rec_yards`,
   `passing_yards`, `moneyline`, `spread`, `total`), `player`, `game`, `kickoff`, `game_script`,
   `estimated_prob` (the sharp average), `prob_sources` (`[{name, prob, url}]`; with only one,
   `single_source: true`), and `dk_odds` (DK's own number read on a page) or `odds` + `book`.
   Include every game's total and spread, both sides. Sacks need `opp_qb`, never vs
   `config.mobile_qbs`. The Legs tab ranks positive-edge 2+-source legs as "Best value".
6. **Boost check** — each DK boost as `{"boost_description", "selection", "boosted_odds",
   "estimated_prob" (sharp fair), "fits_card"}`; the page shows "Worth it: +N¢ per $1" or "Skip".
   None found / promos page unreadable → one honest entry saying so.
7. **Avoid list** — 3–5 real traps (e.g. popular favorites DK prices above the sharp fair chance).

Every card bet needs `plain_summary`: one or two plain sentences, no percentages or model names —
what the bet is and why the price is good (e.g. "DraftKings is still paying like Warren's catches
are a coin flip; the sharp books have him catching 4+ most weeks"). Every bet and leg needs
`game` ("AWAY @ HOME"), `kickoff` (ISO with offset), `game_script`, `reason_summary`, `reason`,
and `verify` (source, fetched_at, "confirm the price in the DK app — if it's moved worse than
<price>, skip it"). Always give that walk-away price: the edge is gone past it. No blacklisted
players. If `budget.total` is 0 (stop-loss hit), still publish the card with $0 stakes and a
clear "stop-loss reached — watch only" summary.

## 5. Build, test, publish

```
python3 -m unittest discover -s src -p "test_*.py"
python3 src/build.py --publish <wid>/<slot>      # plain `python3 src/build.py` on grading-only days
```
A `RuleViolation` means the card breaks a rule: fix the **data** (swap the bet, trim the stake;
a RULE 13 edge failure means the Value Bet becomes a Pass) and rebuild. Never edit a rule to get through. If it can't be fixed, don't publish the slot file
— publish grading only and report why.

Then `git add data/weekends docs/index.html` (never `data/bet_log.csv` — it's only appended from
Gus's real slips, never by this run), `git commit -m "<slot> card <date>: <easy bet>"`, and
`git push`. Poll `gh api repos/thunderbob34-boop/betting-cheat-sheet/pages/builds/latest` until
`built`, then confirm the live page shows the new card.

## 6. Report

End with a short report: the card (each bet's selection, DK price, fair price, edge, stake — or
"Pass" and what came closest), this weekend's net and what's
left of the $5 stop-loss, anything graded, anything left pending or unverified. If a
PushNotification tool is available, send Gus one line: "<Day> card is up: <value bet> $X,
<fun parlay> $Y" or "<Day>: Pass — no DK price beats the sharp line; fun parlay $Y". Facts only.
