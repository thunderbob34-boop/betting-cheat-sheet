# CLAUDE.md — Betting Cheat Sheet

This file governs how Claude Code works in this repo. The original master plan lives at
`../betting-cheat-sheet-plan.md` (the job folder, one level up from this `Engine/` repo, after
the 2026-09-16 `~/HQ` reorg). This file overrides the plan wherever they disagree.

## What this is now (as of 2026-09-26)

An autonomous phone app: a card for **every game day, Thursday through Monday, all season**,
built, graded, and published by a scheduled Claude run on Gus's Mac — no approval step per
edition. Gus opens the page (installed to his home screen), sees **three bets for the current
time slot** (Easy Bet, Fun Parlay, and a third bet), and places them himself in DraftKings.

- Live page: https://thunderbob34-boop.github.io/betting-cheat-sheet/ (GitHub Pages from `docs/`).
- Four tabs: **Today** (this slot's three bets), **Weekend** (every slot Thu–Mon, results, the
  stop-loss meter), **Legs** (the full prop board — TDs, pass TDs, D/ST, kicker, tackles, sacks,
  receiving, receptions, rushing, rush+rec, game lines — with a Best value list and market filter), **Record** (real record from
  `bet_log.csv`, the card's own record, last weekend).
- Automation: a local scheduled task (Claude desktop app → Scheduled) runs `/cheatsheet auto`
  on the mornings of each slot day plus a Tuesday grading run. It only runs while the Claude
  app is open on the Mac; a missed run fires on next launch.
- Publishing is automatic. The safety net is `src/build.py`: any rule violation hard-fails the
  build and nothing is published. Never weaken a rule to get a build through — fix the data.

## Ground rules (non-negotiable — `build.py` enforces what a file can prove)

1. **Weekend stop-loss: $5.** Gus's words: "only a $5 loss per weekend… if I win Thursday I
   have more to play with Saturday… if I lose, a little more reserved, but I do want to bet on
   all those days." Each slot's Easy Bet + Fun Parlay stake comes from `src/slate.py`:
   capacity = $5 + this weekend's settled net − stakes still open, spread over the slots left,
   ×0.75 when the weekend is down, rounded down to $0.05, $0 below $0.10. Worst case for a
   weekend is −$5 on tiers 1–2. Season bankroll is $50 plus winnings (scoreboard shows it,
   never floored). Constants live in `data/config.json`.
2. **Pre-kickoff only.** No live bets, ever, even when Gus pushes. At publish every card bet and
   leg must carry a `kickoff` timestamp still in the future, or the build fails.
3. **Bet first, boost second.** Pick on merit; boosts are checked afterward in their own section.
4. **Leg count follows probability.** Fun Parlay legs are each ≥55%. No parlay gets more than 3
   coin-flip legs. The Lottery Ticket is the one exemption (see 6).
5. **Every odds number shows implied probability, true-probability estimate, and edge** — one tap
   away ("Why & the numbers"). The face of every card is plain English (Gus, 2026-09-26: "I don't want
   to see so many tiny numbers. Just tell me what is what and what to do where"): what to bet, what
   it pays back, chance in words, and a Good / Fair / Overpriced price label.
6. **Each slot's card is three labeled tiers, in order:** Easy Bet (required — one straight
   bet, highest probability with real edge, ~60% of the slot budget), Fun Parlay (optional —
   2–3 legs, each ≥55%, the rest of the slot budget), Lottery Ticket (optional, **at most one
   per weekend**, flat $0.50, 10–20 legs, exempt from rule 4's floor, **outside** the $5
   stop-loss and excluded from pacing both ways, shows real combined probability and
   "1 in X"). Build the Lottery Ticket only after tiers 1–2 are set.
   **Tier 3 on every other slot is Fun Parlay #2** (`card.fun_parlay_2`) — Gus (2026-09-26):
   "just feed me the three I need… I still want to see a couple of varieties of fun leg
   parlays." Same rules as the Fun Parlay (2–3 legs, each ≥55%, game-script check vs the Easy
   Bet), a genuinely different mix (not the same legs — e.g. two big favorites in one, three
   mid-favorites for a bigger price in the other), and it **shares the slot's fun budget**
   with Fun Parlay #1, so it counts toward the $5 stop-loss and never adds money. A slot has
   either a Lottery Ticket or a Fun Parlay #2, never both.
7. **Claude never places a bet.** The page is advice; Gus places every bet himself.
8. **Honesty.** Every fact comes from a source actually read. Every edge claim needs ≥2
   independent probability sources, cited, with the averaging math shown. Verify a number on
   the source page itself, not a search engine's summary (a summary once said 71% where the
   page said 74%). A near-zero or negative averaged edge is a correct answer — report it,
   size down, don't chase. Flag "right player, wrong price" bets and keep them off the card.
9. **Blacklist:** `data/config.json` → `blacklist`. Currently **MarShawn Lloyd** (Packers RB)
   — never on any tier, any leg, or the leg bank. His leg sank the entire 9/24 card.
10. **Game-script check.** Tag every card bet/leg `game_script`: `neutral`, `<TEAM> leading`, or
    `<TEAM> trailing`. The Easy Bet and a Fun Parlay leg may not need the same side ahead in
    the same game ("ATL leading" = "GB trailing" in ATL @ GB). Swap one for a neutral bet.
11. **Source disagreement is a warning sign.** If an Easy Bet's probability sources differ by
    more than 10 points, don't average them into an edge — it can't be the Easy Bet.
12. **No sack legs against mobile QBs** (`config.mobile_qbs`: Jalen Hurts, Caleb Williams,
    Jayden Daniels). Sack legs carry `opp_qb`.

Rules 3 and "Lottery Ticket after tiers 1–2" are build-order discipline no file can prove; the
rest are checked by `build.py` (RULE numbers in its error messages match this list).

## Research playbook (angles, not hard rules)

- **0–2 desperation (NFL):** a 0–2 team fights to avoid 0–3 (that's when coaches get fired) —
  treat it as a live side regardless of venue. Proof: 9/24, 0–2 Atlanta won 35–14 at Green Bay
  as a 5.5-pt underdog. Flag every 0–2 team in the slot's `angles`; don't write a card that
  needs a 0–2 team blown out unless research clearly supports it.
- **Sack leader 1+ sack (Gus's favorite):** each team's sack leader to record 1+ sack — skip it
  against mobile QBs (rule 12). Still has to clear the probability rules; under 55% it goes to
  the Lottery Ticket, not the Fun Parlay.
- **Game script decides RB props.** 9/24 lesson: Lloyd's yards collapsed when GB trailed;
  Bijan's catches collapsed when ATL led and ran. Volume receivers (London, 9 catches) are
  neutral — that leg hit. Split backfields and patched-up offensive lines are warning signs.
- **Screenshot mode:** building a card from DraftKings screenshots Gus pastes only happens for
  that week's already-chosen featured game — not for every screenshot he sends.

## Data

- `data/bet_log.csv` — **real money history. Never edit existing rows; only append.** Gus's
  actual DK bets, logged from his screenshots. Drives the Record tab's real record/bankroll.
  - Known gap: the 9/14 (KC@DEN) and 9/17 (DET@BUF) Easy Bet and Fun Parlay **wins** and their
    two lost 18-leg lottery tickets, plus a 9/17 off-system live 4-leg SGP ($0.35, lost), are
    not logged yet — waiting on Gus's DK slips (stake + payout). Until then the season record
    shows 0 wins, which is wrong. Never guess stakes or payouts.
  - DK balance was ~$0.07 after 9/24; Gus deposits himself.
- `data/weekends/<thursday-date>/<slot>.json` — one file per slot card (thu/sat/sun/mon). Each
  bet carries `result`/`net`, set only via `src/grade.py`. These files are the card ledger and
  drive pacing, the Weekend tab, and the card record ("if every card bet was placed as
  written"). `historical_import: true` marks files imported from `bet_log.csv` (exempt from
  research-quality rules and the blacklist, since they record what actually happened).
- `data/weeks/` — legacy one-card-per-week files (Sept 13–14). Archive; not rendered.
- `data/config.json` — season start, bankroll, stop-loss/pacing constants, blacklist, mobile QBs.

## Commands

```
python3 src/slate.py status                 # which slot, weekend net, this slot's budget, open bets
python3 src/grade.py pending                # bets whose games are done but ungraded
python3 src/grade.py set <wid> <slot> <tier> Won|Lost|Push|Void
python3 src/build.py                        # validate + render docs/index.html
python3 src/build.py --publish <wid>/<slot> # also enforce kickoff-in-future + slot budget
python3 -m unittest discover -s src -p "test_*.py" -v
```

## Fantasy lineups (Lineup tab)

Gus's two ESPN leagues, both full PPR, slots QB / RB / RB / WR / WR / TE / FLEX / K / D/ST.
`data/fantasy.json` holds the league IDs and his team name in each. `python3 src/fantasy.py pull`
reads each league's rosters from ESPN's public API (the league must be viewable to the public;
never use login cookies), averages ESPN's projection (league scoring) with Rotowire's full-PPR
projection (via Sleeper's public API), sits OUT / IR / doubtful / bye players, keeps anyone whose
game already kicked off locked where ESPN has him, and writes `data/fantasy/<season>-wk<NN>.json`.
Then `python3 src/build.py` renders the Lineup tab. Close calls (< 1 pt), questionable starters and
big source disagreements are flagged, not hidden. The page only advises; Gus sets his lineup in the ESPN app.

**Projections alone aren't the job** (Gus, 2026-09-26: "I can see the projections… do your research
online for the best possible start"). Every week, after `pull`, research each real start/sit decision
from pages actually read: FantasyPros PPR expert consensus rank (ECR), usage (snaps, target share,
carries, red-zone work), the opposing defense vs the position, injury/practice news, Vegas spread and
total (game script), and weather for outdoor games. Also check the league's free agents
(`kona_player_info` with `filterStatus` FREEAGENT) for K, D/ST and TE streams. Write the result to
`data/fantasy/research-<season>-wk<NN>.json` (`verdict`, per-player `players` notes, `waivers`,
`sources`) keyed by league ID — the Lineup tab shows it under each player. When research disagrees
with the projection-based lineup, say so in `verdict` and give the reason; don't silently override.
Suggested pickups also go in `moves` (`[{"add", "drop"}]`); `pull` re-runs the lineup with them so
the page shows the projected score and win % with and without the pickups.

**Timing** (Gus, 2026-09-26: "I want to wake up on Wednesday or Thursday morning and be able to set
my team and set my bets"). The scheduled run adds a Wednesday 9 AM run that builds the week's
lineups + research for both leagues (after ESPN waivers process); Thu/Sun/Mon runs refresh them.
The Today tab opens with a fantasy summary (changes to make, projected win %, ESPN's win %) above the
bet card. Win % = normal model on the projected margin with a ±35-pt swing (an assumption, stated on
the page), shown beside ESPN's own number.

`.claude/commands/cheatsheet.md` is the full run procedure (research → slot file → grade →
build → commit → push). The scheduled task just runs it.

## Lessons that must not be relearned

- **Single-source edges lie** (2026-nfl-wk02: Kalshi-only said +5.2%, averaged with a second
  model it was +4.2%; a week later, after lines moved, it was ~0).
- **Lines move a lot in a week** — build each slot the morning of, not days ahead.
- **Cloud routines can't fetch pages** (egress proxy blocks WebFetch; only search snippets) —
  that's why automation runs locally on the Mac, where page fetches and `git push` work.
- **Long URLs break a 390px layout** unless text can wrap mid-word — keep `overflow-wrap:
  anywhere` on `body`, and verify by measuring `scrollWidth` vs `clientWidth` at 390px.

## House style

- Mobile-first, installable (home-screen web app), five tabs. Today opens with a tick-off
  "things to do" checklist (lineup changes + bets). Look (Gus, 2026-09-26): "fun, bubbly… like
  something Apple made, with glass" — frosted glass cards over a soft night-game color glow, rounded
  type, a floating glass tab bar. Plain words on the face, numbers behind a tap.
- No frameworks, no build step beyond `python3 src/build.py`. Python stdlib only.
