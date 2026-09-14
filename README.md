# Weekly Betting Cheat Sheet

A mobile-first page, regenerated weekly, that tells Gus what to bet on DraftKings
before kickoff and shows whether the approach is working. Advice only &mdash; Gus
places every bet himself.

Live page: https://thunderbob34-boop.github.io/betting-cheat-sheet/

Full plan: see `docs/betting-cheat-sheet-plan.md` in the job folder (one level up
from this repo) for the complete design; `CLAUDE.md` here has the operating rules.

**Status: Phase 2 (real weekly research) live.** Currently rendering a
one-off single-game edition (`2026-nfl-wk01-mnf`, Broncos @ Chiefs MNF,
Sept 14 2026). The card is three labeled tiers — Easy Bet (required),
Fun Parlay (optional, every leg ≥55%), Lottery Ticket (optional, flat
$0.50, outside the $5/week line) — each with a one-line summary and the
full reasoning behind a tap-to-expand. `/cheatsheet nfl|cfb`
(`.claude/commands/cheatsheet.md`) researches, builds, and diffs a new
edition — it always stops for approval before pushing. Phase 3 (`/grade`)
is next.
