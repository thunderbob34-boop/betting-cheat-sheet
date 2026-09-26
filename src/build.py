"""Build the betting cheat sheet page.

Reads data/bet_log.csv (real money history, scoreboard computed fresh every
run), data/config.json, and every weekend/slot file under data/weekends/,
validates them against the house's non-negotiable ground rules, and renders
templates/page.html -> docs/index.html via simple string replacement of the
seven tokens defined in templates/CONTRACT.md. No templating library,
Python 3 stdlib only.

templates/page.html is owned by a separate styling pass -- this module never
edits it, never emits inline style= (other than what CONTRACT.md's own
markup already specifies), and never emits <script>. It only ever produces
plain semantic HTML fragments using the classes CONTRACT.md documents.

Usage:
    python3 src/build.py [--publish WEEKEND_ID/SLOT] [--now ISO] [--date YYYY-MM-DD]

Safety note: this script makes no network calls of any kind (no requests,
no urllib, no http.client) and touches no betting API. It cannot place a
bet. That's Ground Rule 7 ("Claude never places a bet") and it holds
trivially here because there is nothing in this file capable of placing one.
"""

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import odds
import slate

REPO_ROOT = Path(__file__).resolve().parent.parent


class RuleViolation(Exception):
    """Raised when a slot JSON's card violates one of the house's ground
    rules. This must always propagate all the way out of main() uncrossed
    and unswallowed -- the exception bubbling up to a non-zero exit code IS
    the safety mechanism ("do not render this page")."""


EPSILON = 1e-6  # float-rounding slack, nothing more

# In-code fallback if data/config.json is ever missing/unreadable/malformed
# -- the build must never crash for lack of a config file. Kept in sync
# with slate._CONFIG_DEFAULTS by hand (see that module's comment on why
# it's duplicated rather than imported).
_CONFIG_DEFAULTS = {
    "season_start": "2026-09-01",
    "starting_bankroll": 50.00,
    "weekend_loss_limit": 5.00,
    "reserve_factor_when_down": 0.75,
    "min_stake": 0.10,
    "stake_step": 0.05,
    "easy_share": 0.60,
    "slots": ["thu", "sat", "sun", "mon"],
    "lottery_stake": 0.50,
    "nfl_week1_thursday": "2026-09-10",
    "timezone": "America/New_York",
    "blacklist": ["MarShawn Lloyd"],
    "mobile_qbs": ["Jalen Hurts", "Caleb Williams", "Jayden Daniels"],
}


def load_config(path):
    """Load data/config.json. Never raises: a missing file, unreadable
    file, bad JSON, or a JSON value that isn't an object all fall back to
    _CONFIG_DEFAULTS (merged under/overridden by whatever valid data IS
    present, if any)."""
    config = dict(_CONFIG_DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return config
    if isinstance(data, dict):
        config.update(data)
    return config


# ---------------------------------------------------------------------------
# Ground-rule validation
# ---------------------------------------------------------------------------

LOTTERY_TICKET_MIN_LEGS = 10
LOTTERY_TICKET_MAX_LEGS = 20
FUN_PARLAY_MIN_LEGS = 2
FUN_PARLAY_MAX_LEGS = 3
FUN_PARLAY_LEG_FLOOR = 0.55
SOURCE_DISAGREEMENT_MAX_SPREAD = 0.10
SOURCE_AVERAGE_TOLERANCE = 0.02


def _load_json_file(path):
    """Load one JSON file, raising a RuleViolation (not a bare, path-less
    traceback) if it's missing or malformed. Every CLI in this repo reads
    many weekend/slot files per run -- a single corrupt file must fail
    loudly and name itself, not crash with a generic traceback that gives
    an unattended operator nothing to act on."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise RuleViolation(f"Malformed JSON in {path}: {e}")
    except OSError as e:
        raise RuleViolation(f"Could not read {path}: {e}")


def _check_pre_kickoff(bet, bet_label):
    """RULE 2 (structural half) -- pre-kickoff only. is_pre_kickoff must be
    true and no market/selection (bet or leg) may contain the substring
    'live'. Applies to every tier; skipped entirely for historical_import
    slots (see validate_slot_rules)."""
    if not bet.get("is_pre_kickoff", False):
        raise RuleViolation(
            f"RULE 2 VIOLATION: bet '{bet_label}' is not marked "
            "is_pre_kickoff: true"
        )
    for field in ("market", "selection"):
        val = bet.get(field)
        if isinstance(val, str) and "live" in val.lower():
            raise RuleViolation(
                f"RULE 2 VIOLATION: bet '{bet_label}' has {field}="
                f"{val!r}, which contains 'live'"
            )
    for leg in bet.get("legs", []) or []:
        leg_label = leg.get("selection", "<unnamed leg>")
        for field in ("market", "selection"):
            val = leg.get(field)
            if isinstance(val, str) and "live" in val.lower():
                raise RuleViolation(
                    f"RULE 2 VIOLATION: bet '{bet_label}' leg "
                    f"'{leg_label}' has {field}={val!r}, which "
                    "contains 'live'"
                )


def _check_kickoff_future(bet_or_leg, now, label, config):
    """RULE 2 (temporal half, --publish only) -- kickoff must be strictly
    after `now`.

    A kickoff string with no UTC offset is refused outright (never
    silently assumed to be UTC): it is never safe to guess whether a bare
    timestamp means UTC or local time, and guessing wrong shifts the
    effective kickoff by hours. A kickoff string that DOES carry an
    offset has that offset cross-checked against the configured
    timezone's real offset for that same wall-clock date/time -- this
    catches a DST-boundary authoring mistake (e.g. writing -05:00 for a
    game that's actually still -04:00/EDT) that would otherwise let a
    game that has already kicked off look "still upcoming"."""
    kickoff = bet_or_leg.get("kickoff")
    if not kickoff:
        raise RuleViolation(f"RULE 2 VIOLATION: {label} is missing a kickoff timestamp")
    try:
        dt = datetime.fromisoformat(kickoff)
    except ValueError:
        raise RuleViolation(f"RULE 2 VIOLATION: {label} kickoff {kickoff!r} is not valid ISO 8601")
    if dt.tzinfo is None:
        raise RuleViolation(
            f"RULE 2 VIOLATION: {label} kickoff {kickoff!r} has no UTC "
            "offset -- a bare timestamp is a data error (it is never safe "
            "to guess whether it means UTC or local time); fix the slot "
            "file to include an explicit offset (e.g. '-04:00')"
        )
    tz_name = config.get("timezone", "America/New_York")
    tz = ZoneInfo(tz_name)
    # Reinterpret the SAME wall-clock reading in the configured timezone
    # and compare offsets -- not the same instant converted (which would
    # trivially match itself and prove nothing).
    naive = dt.replace(tzinfo=None)
    expected_offset = naive.replace(tzinfo=tz).utcoffset()
    actual_offset = dt.utcoffset()
    if expected_offset is not None and abs((expected_offset - actual_offset).total_seconds()) > 1:
        raise RuleViolation(
            f"RULE 2 VIOLATION: {label} kickoff {kickoff} has UTC offset "
            f"{actual_offset}, but {tz_name}'s real offset for that "
            f"date/time is {expected_offset} -- the authored offset does "
            "not match, refusing to trust it (a DST-boundary authoring "
            "mistake could otherwise let a live game publish as still "
            "upcoming)"
        )
    if dt <= now:
        raise RuleViolation(
            f"RULE 2 VIOLATION: {label} kickoff {kickoff} is not strictly "
            f"after now ({now.isoformat()}) -- publish only before kickoff"
        )


_WS_RE = re.compile(r"\s+")


def _normalize_for_blacklist(text):
    """Case-fold and collapse whitespace runs (spaces, tabs, newlines, and
    Unicode spaces like NBSP -- all match \\s) to a single space, so a
    doubled/irregular space inside a blacklisted name can't defeat the
    substring match."""
    return _WS_RE.sub(" ", str(text)).strip().lower()


def _scan_blacklist(text, where, names):
    if not text:
        return
    low = _normalize_for_blacklist(text)
    for n in names:
        if n and n in low:
            raise RuleViolation(
                f"RULE 9 VIOLATION: {where} matches a blacklisted name "
                f"({n!r}): {text!r}"
            )


def _check_blacklist(slot, config, label):
    """RULE 9 -- any card selection, leg selection, player field, or
    leg_bank selection containing a config blacklist name (case-insensitive,
    whitespace-normalized) fails. Skipped for historical_import slots."""
    names = [_normalize_for_blacklist(n) for n in (config.get("blacklist") or []) if n]
    if not names:
        return
    card = slot.get("card") or {}
    easy = card.get("easy_bet")
    if easy:
        _scan_blacklist(easy.get("selection"), f"[{label}] card.easy_bet.selection", names)
        _scan_blacklist(easy.get("player"), f"[{label}] card.easy_bet.player", names)
    fun = card.get("fun_parlay")
    if fun:
        for i, leg in enumerate(fun.get("legs") or []):
            _scan_blacklist(leg.get("selection"), f"[{label}] card.fun_parlay.legs[{i}].selection", names)
            _scan_blacklist(leg.get("player"), f"[{label}] card.fun_parlay.legs[{i}].player", names)
    lot = card.get("lottery_ticket")
    if lot:
        for i, leg in enumerate(lot.get("legs") or []):
            _scan_blacklist(leg.get("selection"), f"[{label}] card.lottery_ticket.legs[{i}].selection", names)
            _scan_blacklist(leg.get("player"), f"[{label}] card.lottery_ticket.legs[{i}].player", names)
    for i, leg in enumerate(slot.get("leg_bank") or []):
        _scan_blacklist(leg.get("selection"), f"[{label}] leg_bank[{i}].selection", names)
        _scan_blacklist(leg.get("player"), f"[{label}] leg_bank[{i}].player", names)


_SCRIPT_RE = re.compile(r"^(.*?)\s+(leading|trailing)$", re.IGNORECASE)
# "vs"/"at" must be surrounded by real whitespace (a standalone word), or
# this would also match inside a team code that merely CONTAINS "at" (e.g.
# "ATL" itself starts with "AT"). "@" needs no surrounding whitespace.
_GAME_SEP_RE = re.compile(r"\s*@\s*|\s+vs\.?\s+|\s+at\s+", re.IGNORECASE)


def _split_game_teams(game):
    """Split a 'TEAM @ TEAM' (or 'TEAM vs TEAM' / 'TEAM at TEAM') style
    game string into its team names, tolerant of the separator used."""
    return [t.strip() for t in _GAME_SEP_RE.split(game.strip()) if t.strip()]


def _normalize_game(game):
    """Normalize a game string for equality comparison: case-fold, tolerate
    'vs'/'at' in place of '@', collapse whitespace, and ignore team order
    (so 'ATL @ GB' == 'GB @ ATL' == 'atl vs  gb'). Two independently
    authored bets in the same real game should never fail to match each
    other just because of formatting drift."""
    if not game:
        return None
    teams = [t.lower() for t in _split_game_teams(game)]
    return frozenset(teams) if len(teams) >= 2 else game.strip().lower()


def _script_ahead_side(game, script):
    """Which team a game_script says is ahead, normalized so 'ATL leading'
    and 'GB trailing' (in 'ATL @ GB') both resolve to 'ATL'. Returns None
    for a missing/neutral/unparseable script."""
    if not script or script.strip().lower() == "neutral" or not game:
        return None
    m = _SCRIPT_RE.match(script.strip())
    if not m:
        return None
    team, verb = m.group(1).strip(), m.group(2).lower()
    teams = _split_game_teams(game)
    if verb == "leading":
        return team
    others = [t for t in teams if t.lower() != team.lower()]
    return others[0] if others else None


def _check_game_script(easy_bet, fun_parlay, label):
    """RULE 10 -- easy_bet vs each fun_parlay leg: if same game and they
    need the same side to be ahead, fail. Neutral never conflicts. Game
    equality is whitespace/case/separator/team-order tolerant (see
    _normalize_game). Skipped for historical_import slots."""
    if not easy_bet or not fun_parlay:
        return
    easy_game = easy_bet.get("game")
    easy_game_norm = _normalize_game(easy_game)
    easy_side = _script_ahead_side(easy_game, easy_bet.get("game_script"))
    if not easy_game or easy_side is None:
        return
    for i, leg in enumerate(fun_parlay.get("legs") or []):
        if _normalize_game(leg.get("game")) != easy_game_norm:
            continue
        leg_side = _script_ahead_side(leg.get("game"), leg.get("game_script"))
        if leg_side is not None and leg_side.lower() == easy_side.lower():
            raise RuleViolation(
                f"RULE 10 VIOLATION: [{label}] card.easy_bet and "
                f"card.fun_parlay.legs[{i}] are both in {easy_game} and "
                f"both need {leg_side} to be ahead -- pick a genuinely "
                "independent leg instead"
            )


def _check_source_agreement(easy_bet, label):
    """RULE 11 -- card.easy_bet needs >= 2 prob_sources with a max-min
    spread <= 10 points, AND its own headline estimated_prob (the "Est."
    figure rendered on the page, and the number Edge is computed from)
    must actually be the sources' own average, within a small tolerance --
    otherwise the averaging math CLAUDE.md rule 8 requires ("the averaging
    math shown") is never actually verified, and a fabricated/transposed
    estimated_prob could reach the page unchecked. Skipped for
    historical_import slots."""
    sources = easy_bet.get("prob_sources") or []
    if len(sources) < 2:
        raise RuleViolation(
            f"RULE 11 VIOLATION: [{label}] card.easy_bet needs at least 2 "
            "prob_sources (independent probability estimates) -- with "
            "fewer than 2, keep it off the Easy Bet slot"
        )
    probs = [float(s.get("prob", 0.0)) for s in sources]
    spread = max(probs) - min(probs)
    if spread > SOURCE_DISAGREEMENT_MAX_SPREAD + EPSILON:
        raise RuleViolation(
            f"RULE 11 VIOLATION: [{label}] card.easy_bet prob_sources "
            f"disagree by {spread:.1%} (> 10 points) -- keep it off the "
            "Easy Bet slot"
        )
    avg = sum(probs) / len(probs)
    estimated = float(easy_bet.get("estimated_prob", 0.0))
    if abs(estimated - avg) > SOURCE_AVERAGE_TOLERANCE + EPSILON:
        raise RuleViolation(
            f"RULE 11 VIOLATION: [{label}] card.easy_bet estimated_prob "
            f"{estimated:.1%} does not match its own cited prob_sources "
            f"average {avg:.1%} (tolerance ±{SOURCE_AVERAGE_TOLERANCE:.0%}) "
            "-- the rendered 'Est.'/Edge must be provably derived from the "
            "sources actually cited"
        )


def _iter_market_entries(slot):
    """Yield (label, entry_dict) for every bet/leg/leg_bank object that
    could carry a market field."""
    card = slot.get("card") or {}
    easy = card.get("easy_bet")
    if easy:
        yield "card.easy_bet", easy
    fun = card.get("fun_parlay")
    if fun:
        for i, leg in enumerate(fun.get("legs") or []):
            yield f"card.fun_parlay.legs[{i}]", leg
    lot = card.get("lottery_ticket")
    if lot:
        for i, leg in enumerate(lot.get("legs") or []):
            yield f"card.lottery_ticket.legs[{i}]", leg
    for i, leg in enumerate(slot.get("leg_bank") or []):
        yield f"leg_bank[{i}]", leg


def _check_sack_mobile_qb(slot, config, label):
    """RULE 12 -- any bet/leg/leg_bank entry with market "sack" must have
    opp_qb; if opp_qb is in config.mobile_qbs, fail (sacks are much less
    reliable against a QB who can escape the pocket). Skipped for
    historical_import slots."""
    mobile_qbs = {q.lower() for q in (config.get("mobile_qbs") or [])}
    for entry_label, entry in _iter_market_entries(slot):
        # Substring match, not exact-equality -- real DK market labels for
        # this angle vary ("Sacks", "Sack Leader", "Anytime Sack", "1+
        # Sack"); no other real NFL market name contains "sack".
        if "sack" not in (entry.get("market") or "").strip().lower():
            continue
        opp_qb = entry.get("opp_qb")
        if not opp_qb:
            raise RuleViolation(
                f"RULE 12 VIOLATION: [{label}] {entry_label} has market "
                "'sack' but no opp_qb"
            )
        if opp_qb.lower() in mobile_qbs:
            raise RuleViolation(
                f"RULE 12 VIOLATION: [{label}] {entry_label} is a sack bet "
                f"against {opp_qb}, a mobile QB (config.mobile_qbs) -- "
                "sacks are much less reliable against a QB who can escape "
                "the pocket"
            )


def _check_historical_claim(slot, now, label):
    """A slot may only claim historical_import: true if every present tier
    already has a graded (non-null) result AND has a kickoff timestamp
    that is verifiably in the past relative to `now` -- "historical" must
    mean "this already happened," never "unchecked." Otherwise the claim
    itself is a RuleViolation, so a fresh/hallucinated slot can never sail
    past every other rule just by setting this one flag."""
    card = slot.get("card") or {}
    for tier_label, bet in (
        ("easy_bet", card.get("easy_bet")),
        ("fun_parlay", card.get("fun_parlay")),
        ("lottery_ticket", card.get("lottery_ticket")),
    ):
        if bet is None:
            continue
        if bet.get("result") is None:
            raise RuleViolation(
                f"[{label}] HISTORICAL IMPORT VIOLATION: card.{tier_label} "
                "is marked historical_import but has no graded result -- a "
                "historical slot must record what actually happened, not "
                "an open/ungraded bet"
            )
        entries = [bet] + list(bet.get("legs") or [])
        for entry in entries:
            kickoff = entry.get("kickoff")
            if not kickoff:
                if entry is bet:
                    raise RuleViolation(
                        f"[{label}] HISTORICAL IMPORT VIOLATION: "
                        f"card.{tier_label} has no kickoff timestamp -- a "
                        "historical claim must be verifiable as already "
                        "in the past"
                    )
                continue  # legs commonly don't carry their own kickoff
            try:
                dt = datetime.fromisoformat(kickoff)
            except ValueError:
                raise RuleViolation(
                    f"[{label}] HISTORICAL IMPORT VIOLATION: "
                    f"card.{tier_label} kickoff {kickoff!r} is not valid "
                    "ISO 8601"
                )
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= now:
                raise RuleViolation(
                    f"[{label}] HISTORICAL IMPORT VIOLATION: "
                    f"card.{tier_label} kickoff {kickoff} is not in the "
                    f"past (now={now.isoformat()}) -- historical_import "
                    "can only mark a slot that already happened"
                )


def validate_slot_rules(slot, config, now=None):
    """Validate one slot file's card against the house's tier-structure
    rules (always enforced) plus RULE 2/9/10/11/12 (skipped only for a
    slot whose historical_import claim is itself verified -- see
    _check_historical_claim; real settled history being transcribed, not
    a new card being built). RULE 1 (budget) and RULE 6 (one
    lottery/weekend) are NOT checked here -- see validate_publish and
    validate_lottery_count.

    Raises RuleViolation on any failure.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    historical = bool(slot.get("historical_import", False))
    card = slot.get("card") or {}
    easy_bet = card.get("easy_bet")
    fun_parlay = card.get("fun_parlay")
    lottery_ticket = card.get("lottery_ticket")
    label = f"{slot.get('weekend_id', '?')}/{slot.get('slot', '?')}"

    # --- slot-level date field (always enforced -- basic schema shape,
    # not a "research quality" judgment call; a malformed date must fail
    # loudly here rather than crashing rendering later). ---
    raw_date = slot.get("date")
    try:
        if not raw_date or not isinstance(raw_date, str):
            raise ValueError("missing or non-string date")
        date.fromisoformat(raw_date)
    except ValueError as e:
        raise RuleViolation(
            f"[{label}] TIER STRUCTURE VIOLATION: slot 'date' field "
            f"{raw_date!r} is not a valid ISO 8601 date ({e})"
        )

    # --- tier structure (always enforced, historical included -- basic
    # schema shape, not a "research quality" judgment call). ---
    if not easy_bet:
        raise RuleViolation(f"[{label}] TIER STRUCTURE VIOLATION: card.easy_bet is required")
    if easy_bet.get("legs"):
        raise RuleViolation(
            f"[{label}] TIER STRUCTURE VIOLATION: card.easy_bet must be a "
            "single straight selection, not multi-leg"
        )

    # --- stakes must never be negative (always enforced -- a negative
    # stake can offset a real one in a sum and hide it under the RULE 1
    # budget cap). ---
    for tier_label, bet in (("easy_bet", easy_bet), ("fun_parlay", fun_parlay)):
        if bet is None:
            continue
        stake = bet.get("stake")
        try:
            stake_val = float(stake)
        except (TypeError, ValueError):
            raise RuleViolation(
                f"[{label}] TIER STRUCTURE VIOLATION: card.{tier_label}.stake "
                f"{stake!r} is not a real number"
            )
        if stake_val < -EPSILON:
            raise RuleViolation(
                f"[{label}] TIER STRUCTURE VIOLATION: card.{tier_label}.stake "
                f"${stake_val:.2f} is negative -- a stake can never be less "
                "than $0"
            )

    if fun_parlay is not None:
        legs = fun_parlay.get("legs") or []
        if not (FUN_PARLAY_MIN_LEGS <= len(legs) <= FUN_PARLAY_MAX_LEGS):
            raise RuleViolation(
                f"[{label}] TIER STRUCTURE VIOLATION: card.fun_parlay must "
                f"have {FUN_PARLAY_MIN_LEGS}-{FUN_PARLAY_MAX_LEGS} legs, "
                f"got {len(legs)}"
            )
        for leg in legs:
            p = leg.get("estimated_prob", 0.0)
            if p < FUN_PARLAY_LEG_FLOOR:
                raise RuleViolation(
                    f"[{label}] TIER STRUCTURE VIOLATION: card.fun_parlay "
                    f"leg '{leg.get('selection', '<unnamed leg>')}' has "
                    f"estimated_prob {p}, below the {FUN_PARLAY_LEG_FLOOR:.0%} "
                    "floor every Fun Parlay leg must clear"
                )

    if lottery_ticket is not None:
        legs = lottery_ticket.get("legs") or []
        if not (LOTTERY_TICKET_MIN_LEGS <= len(legs) <= LOTTERY_TICKET_MAX_LEGS):
            raise RuleViolation(
                f"[{label}] TIER STRUCTURE VIOLATION: card.lottery_ticket "
                f"must have {LOTTERY_TICKET_MIN_LEGS}-{LOTTERY_TICKET_MAX_LEGS} "
                f"legs, got {len(legs)}"
            )
        stake = float(lottery_ticket.get("stake", 0.0))
        expected_stake = config.get("lottery_stake", 0.50)
        if abs(stake - expected_stake) > EPSILON:
            raise RuleViolation(
                f"[{label}] TIER STRUCTURE VIOLATION: card.lottery_ticket "
                f"stake must be exactly ${expected_stake:.2f} (flat, per "
                f"rule), got ${stake:.2f}"
            )

    if historical:
        _check_historical_claim(slot, now, label)
        return

    # --- RULE 2 (structural): pre-kickoff only, every tier. ---
    for tier_label, bet in (
        ("easy_bet", easy_bet),
        ("fun_parlay", fun_parlay),
        ("lottery_ticket", lottery_ticket),
    ):
        if bet is None:
            continue
        _check_pre_kickoff(bet, f"[{label}] {bet.get('id') or tier_label}")

    # --- RULE 9: blacklist. ---
    _check_blacklist(slot, config, label)

    # --- RULE 10: game script conflict (easy_bet vs each fun_parlay leg). ---
    _check_game_script(easy_bet, fun_parlay, label)

    # --- RULE 11: source disagreement (easy_bet prob_sources). ---
    if easy_bet is not None:
        _check_source_agreement(easy_bet, label)

    # --- RULE 12: sack vs mobile QB. ---
    _check_sack_mobile_qb(slot, config, label)


def validate_lottery_count(weekend_id, slot_dicts):
    """RULE 6 -- at most ONE lottery_ticket per weekend, counting across
    every slot file (historical included -- this is a real-money business
    rule about how many lottery bets got placed, not a research-quality
    check)."""
    count = sum(1 for s in slot_dicts if (s.get("card") or {}).get("lottery_ticket"))
    if count > 1:
        raise RuleViolation(
            f"RULE 6 VIOLATION: weekend {weekend_id} has {count} lottery "
            "tickets across its slot files -- at most 1 allowed per weekend"
        )


def validate_publish(slot, weekend_id, slot_name, config, now, weekends_dir):
    """The extra checks only a `--publish WEEKEND_ID/SLOT` run enforces:
    RULE 2 (every card bet AND leg's kickoff strictly after now) and
    RULE 1 (tiers 1-2 stake total <= this slot's budget, per
    slate.slot_budget). A historical_import slot is ALWAYS refused outright
    -- historical slots record what already happened and are never a live
    publish target, no matter how "clean" they otherwise look."""
    label = f"{weekend_id}/{slot_name}"
    if slot.get("historical_import"):
        raise RuleViolation(
            f"RULE 2 VIOLATION: [{label}] --publish refuses a "
            "historical_import slot -- historical slots record what "
            "already happened and are never a live publish target"
        )
    card = slot.get("card") or {}

    for tier_label, bet in (
        ("easy_bet", card.get("easy_bet")),
        ("fun_parlay", card.get("fun_parlay")),
        ("lottery_ticket", card.get("lottery_ticket")),
    ):
        if bet is None:
            continue
        _check_kickoff_future(bet, now, f"[{label}] {tier_label}", config)
        for i, leg in enumerate(bet.get("legs") or []):
            _check_kickoff_future(leg, now, f"[{label}] {tier_label}.legs[{i}]", config)

    # Sum of stakes CLAMPED at zero each -- a negative stake must never be
    # able to net against a real one and hide it under the budget cap.
    stake_total = 0.0
    if card.get("easy_bet"):
        stake_total += max(0.0, float(card["easy_bet"].get("stake", 0.0)))
    if card.get("fun_parlay"):
        stake_total += max(0.0, float(card["fun_parlay"].get("stake", 0.0)))

    budget = slate.slot_budget(weekend_id, slot_name, config, now, weekends_dir=weekends_dir)
    if stake_total > budget["total"] + EPSILON:
        raise RuleViolation(
            f"RULE 1 VIOLATION: [{label}] tiers 1-2 stake total "
            f"${stake_total:.2f} exceeds this slot's budget of "
            f"${budget['total']:.2f} ({budget['why']})"
        )


def _iter_weekend_files(weekends_dir, weekend_id):
    d = Path(weekends_dir) / weekend_id
    if not d.is_dir():
        return []
    return sorted(d.glob("*.json"))


def _load_weekend_slots(weekends_dir, weekend_id):
    """{slot_name: slot_dict} for every *.json file in
    weekends_dir/weekend_id/, keyed and stamped by the file's OWN
    filename -- never by its self-reported "slot" field. A file's location
    on disk is ground truth for its identity: trusting an internal field
    instead would let two distinct files with the same claimed "slot"
    silently collapse into one (dropping whichever loaded first, and
    hiding whatever it contained from every rule check), and would let a
    mislabeled internal field make a correctly-placed file invisible to
    the exclude_slot idempotency check. A body/filename disagreement is
    therefore a loud, hard failure, not a silent overwrite."""
    result = {}
    for path in _iter_weekend_files(weekends_dir, weekend_id):
        data = _load_json_file(path)
        file_slot = path.stem
        body_slot = data.get("slot")
        if body_slot is not None and body_slot != file_slot:
            raise RuleViolation(
                f"[{weekend_id}/{file_slot}] DATA ERROR: file {path.name} "
                f"declares internal slot {body_slot!r}, which does not "
                f"match its filename {file_slot!r} -- rename the file or "
                "fix its 'slot' field; refusing to silently prefer one "
                "over the other"
            )
        data["weekend_id"] = weekend_id
        data["slot"] = file_slot
        result[file_slot] = data
    return result


def validate_build(weekends_dir, config, now, publish_target=None):
    """Validate every slot file in the current + previous weekend (per-file
    rules plus RULE 6), then -- if publish_target=(weekend_id, slot) is
    given -- the extra --publish-only checks for that one slot.

    Must never crash just because data/weekends/ has no files yet.
    """
    window = slate.current_window(now, config)
    current_weekend_id = window["weekend_id"]
    previous_weekend_id = (date.fromisoformat(current_weekend_id) - timedelta(days=7)).isoformat()

    weekend_ids = {previous_weekend_id, current_weekend_id}
    if publish_target is not None:
        weekend_ids.add(publish_target[0])

    for weekend_id in sorted(weekend_ids):
        loaded = list(_load_weekend_slots(weekends_dir, weekend_id).values())
        for slot in loaded:
            validate_slot_rules(slot, config, now)
        validate_lottery_count(weekend_id, loaded)

    if publish_target is not None:
        wid, slot_name = publish_target
        target_path = Path(weekends_dir) / wid / f"{slot_name}.json"
        if not target_path.exists():
            raise RuleViolation(
                f"RULE 2 VIOLATION: --publish target {wid}/{slot_name} has "
                f"no slot file at {target_path}"
            )
        slot = _load_json_file(target_path)
        slot.setdefault("weekend_id", wid)
        slot.setdefault("slot", slot_name)
        validate_publish(slot, wid, slot_name, config, now, weekends_dir)


# ---------------------------------------------------------------------------
# Scoreboard (computed fresh from data/bet_log.csv every run) -- unchanged
# from the weekly-card era, still the season/all-time source of truth.
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?")

_RESULT_CATEGORY = {
    "Won": "W",
    "Lost": "L",
    "Cashed Out": "CO",
}


def _parse_date_placed(raw):
    raw = (raw or "").strip()
    m = _DATE_RE.match(raw)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def compute_scoreboard(csv_path, since=None, starting_bankroll=50.00):
    """Parse data/bet_log.csv and return the scoreboard dict. See prior
    docstring history in git for the full contract -- unchanged behavior:
    messy rows never crash the build, since=None means all-time, since=
    "YYYY-MM-DD" scopes to a season."""
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    wins = cashouts = losses = open_count = no_data_rows = 0
    cash_pl = 0.0
    settled = []

    for idx, row in enumerate(rows):
        date_str, time_str = _parse_date_placed(row.get("date_placed"))

        if since is not None and (date_str is None or date_str < since):
            continue

        bet_type = (row.get("bet_type") or "").strip()
        legs_field = (row.get("legs") or "").strip()
        odds_listed = (row.get("odds_listed") or "").strip()
        result = (row.get("result") or "").strip()

        if not bet_type and not legs_field and not odds_listed and not result:
            no_data_rows += 1
            continue

        sort_key = (date_str or "9999-99-99", time_str or "99:99", idx)

        net_cash_raw = (row.get("net_cash") or "").strip()
        try:
            cash_pl += float(net_cash_raw)
        except ValueError:
            pass

        category = _RESULT_CATEGORY.get(result)
        if result == "Won":
            wins += 1
        elif result == "Cashed Out":
            cashouts += 1
        elif result == "Lost":
            losses += 1
        elif result == "Open":
            open_count += 1
        else:
            no_data_rows += 1

        if category is not None:
            settled.append((sort_key, category))

    settled.sort(key=lambda t: t[0])

    current_streak = "—"
    if settled:
        last_category = settled[-1][1]
        streak_len = 0
        for _, category in reversed(settled):
            if category != last_category:
                break
            streak_len += 1
        current_streak = f"{last_category}{streak_len}"

    bankroll_remaining = starting_bankroll + cash_pl

    return {
        "wins": wins,
        "cashouts": cashouts,
        "losses": losses,
        "open": open_count,
        "no_data_rows": no_data_rows,
        "cash_pl": cash_pl,
        "bankroll_remaining": bankroll_remaining,
        "current_streak": current_streak,
    }


def find_earliest_date_label(csv_path):
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    earliest = None
    for row in rows:
        date_str, _ = _parse_date_placed(row.get("date_placed"))
        if date_str is not None and (earliest is None or date_str < earliest):
            earliest = date_str

    if earliest is None:
        return None
    return _format_month_year(earliest)


def compute_card_record(weekends_dir):
    """Tally every slot file across every weekend: tiers 1-2 W/L/P + net,
    and lottery W/L. This is "if every card bet was placed as written" --
    a running record of the CARD, separate from bet_log.csv's real
    placed-bet history (a card bet and what Gus actually placed in the DK
    app aren't always exactly the same size/price)."""
    tiers12 = {"W": 0, "L": 0, "P": 0}
    net12 = 0.0
    lottery = {"W": 0, "L": 0}

    weekends_dir = Path(weekends_dir)
    if weekends_dir.is_dir():
        for weekend_dir in sorted(p for p in weekends_dir.iterdir() if p.is_dir()):
            for path in sorted(weekend_dir.glob("*.json")):
                slot = _load_json_file(path)
                card = slot.get("card") or {}
                for tier in ("easy_bet", "fun_parlay"):
                    bet = card.get(tier)
                    if not bet:
                        continue
                    result = bet.get("result")
                    if result == "Won":
                        tiers12["W"] += 1
                    elif result == "Lost":
                        tiers12["L"] += 1
                    elif result == "Push":
                        tiers12["P"] += 1
                    net = bet.get("net")
                    if net is not None:
                        net12 += float(net)
                lot = card.get("lottery_ticket")
                if lot:
                    result = lot.get("result")
                    if result == "Won":
                        lottery["W"] += 1
                    elif result == "Lost":
                        lottery["L"] += 1

    return {"tiers12": tiers12, "net12": round(net12, 2), "lottery": lottery}


def _find_most_recent_graded_slot(weekends_dir):
    """Search backwards through weekends (newest weekend_id first, newest
    date within a weekend first) for the most recent slot file with at
    least one graded (non-null result) tier."""
    weekends_dir = Path(weekends_dir)
    if not weekends_dir.is_dir():
        return None
    weekend_dirs = sorted((p for p in weekends_dir.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    for wd in weekend_dirs:
        slots = []
        for path in sorted(wd.glob("*.json")):
            data = _load_json_file(path)
            data.setdefault("weekend_id", wd.name)
            data.setdefault("slot", path.stem)
            slots.append(data)
        slots.sort(key=lambda s: s.get("date", ""), reverse=True)
        for slot in slots:
            card = slot.get("card") or {}
            tiers = [card.get(t) for t in ("easy_bet", "fun_parlay", "lottery_ticket") if card.get(t)]
            if tiers and all(t.get("result") is not None for t in tiers):
                return slot
    return None


def _distinct_games(slot):
    games = []
    seen = set()

    def add(g):
        if g and g not in seen:
            seen.add(g)
            games.append(g)

    card = slot.get("card") or {}
    for tier in ("easy_bet", "fun_parlay", "lottery_ticket"):
        bet = card.get(tier)
        if not bet:
            continue
        add(bet.get("game"))
        for leg in bet.get("legs") or []:
            add(leg.get("game"))
    for leg in slot.get("leg_bank") or []:
        add(leg.get("game"))
    return games


def sort_leg_bank(leg_bank):
    """Leg Bank entries ranked by estimated probability, highest first."""
    return sorted(leg_bank, key=lambda leg: leg.get("estimated_prob", 0.0), reverse=True)


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------

def _format_month_year(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%b')} {dt.year}"


def _format_human_date(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def format_odds(o):
    """American odds with an explicit sign, e.g. -150 -> '-150', 150 -> '+150'."""
    return f"{int(o):+d}"


def format_money(v):
    """Signed dollar string, e.g. -5.93 -> '-$5.93', 44.07 -> '$44.07'."""
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def _money_signed(v):
    """Always-signed dollar string, e.g. 0.91 -> '+$0.91', -2.25 -> '-$2.25'."""
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.2f}"


def _fmt_time_ampm(dt):
    s = dt.strftime("%I:%M %p")
    return s.lstrip("0") or s


def _format_kickoff_et(kickoff_iso, tz):
    if not kickoff_iso:
        return ""
    try:
        dt = datetime.fromisoformat(kickoff_iso)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(tz)
    return f"{dt.strftime('%a')} {_fmt_time_ampm(dt)}"


_SLOT_DAY_LABEL = {"thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday", "mon": "Monday"}


def _slot_day_title(d):
    """'Sunday · Sep 27' -- full weekday + abbreviated month + day, no year."""
    return f"{d.strftime('%A')} · {d.strftime('%b')} {d.day}"


def _next_card_short(window):
    d = datetime.strptime(window["next_slot_date"], "%Y-%m-%d").date()
    return d.strftime("%a")


def _next_card_full(window):
    d = datetime.strptime(window["next_slot_date"], "%Y-%m-%d").date()
    return f"{d.strftime('%A')} · {d.strftime('%b')} {d.day}"


# ---------------------------------------------------------------------------
# Rendering -- fragments per templates/CONTRACT.md. build.py never edits
# templates/page.html; it only string-replaces the seven {{TOKEN}}s with
# the plain semantic HTML the contract documents.
# ---------------------------------------------------------------------------

def _result_badge_html(result, net):
    """<span class="result-badge won|lost|push|void|open">...</span> --
    "Open" while result is null, "Won +$0.91" / "Lost -$2.25" once graded
    (Won/Lost show the signed net; Push/Void just show the word, net is
    always 0 for those)."""
    if result is None:
        return '<span class="result-badge open">Open</span>'
    cls = result.lower()
    if result in ("Won", "Lost") and net is not None:
        label = f"{result} {_money_signed(float(net))}"
    else:
        label = result
    return f'<span class="result-badge {cls}">{escape(label)}</span>'


def _reason_detail_html(bet):
    """The collapsible tail every tier shares -- full reasoning + prob
    source + verify line, behind a native <details> so it's zero-JS and
    defaults closed."""
    reason = bet.get("reason", "")
    source = bet.get("estimated_prob_source", "")
    verify = bet.get("verify") or {}

    source_p = f'<p class="prob-source">{escape(source)}</p>' if source else ""
    verify_bits = [verify.get("source"), verify.get("fetched_at"), verify.get("note")]
    verify_line = " · ".join(escape(b) for b in verify_bits if b)
    verify_p = f'<p class="verify">{verify_line}</p>' if verify_line else ""

    return f'''<details class="reason-detail">
    <summary>Why &amp; sources</summary>
    <p class="reason">{escape(reason)}</p>
    {source_p}
    {verify_p}
  </details>'''


_TIER_META = {
    "easy_bet": (1, "Easy Bet", "tier-easy"),
    "fun_parlay": (2, "Fun Parlay", "tier-fun"),
    "lottery_ticket": (3, "Lottery Ticket", "tier-lottery"),
}


def render_card_bet(tier_key, bet, tz, historical=False):
    num, tier_label, cls = _TIER_META[tier_key]
    legs = bet.get("legs") or []
    is_parlay = tier_key in ("fun_parlay", "lottery_ticket")
    title = f"{len(legs)}-leg parlay" if is_parlay else escape(bet.get("selection", ""))
    dk_odds = bet.get("dk_odds")
    stake = float(bet.get("stake", 0.0))
    game = bet.get("game", "") or ""
    kickoff_disp = _format_kickoff_et(bet.get("kickoff"), tz)

    # historical_import numbers are reconstructed after the fact, not
    # recorded when the bet was placed -- that caveat must travel with the
    # figure wherever it's shown, not sit only behind the tap-to-expand
    # "Why & sources" detail.
    reconstructed_tag = '<span class="reconstructed-tag">Reconstructed</span>' if historical else ""

    if tier_key == "lottery_ticket":
        combined = odds.parlay_implied_prob([leg.get("estimated_prob", 0.0) for leg in legs])
        payout_implied = odds.american_to_implied_prob(dk_odds)
        prob_row = f'''<div class="prob-row lottery-odds">
    {reconstructed_tag}
    <span class="prob"><span class="k">Payout-implied</span> <b>{odds.format_prob(payout_implied)}</b></span>
    <span class="prob"><span class="k">Real chance</span> <b>{odds.format_prob(combined)}</b></span>
    <span class="prob one-in-x"><b>{odds.format_one_in_x(combined)}</b></span>
  </div>'''
    else:
        if is_parlay:
            est_prob = odds.parlay_implied_prob([leg.get("estimated_prob", 0.0) for leg in legs])
        else:
            est_prob = bet.get("estimated_prob", 0.0)
        implied = odds.american_to_implied_prob(dk_odds)
        edge_val = odds.edge(est_prob, implied)
        edge_cls = "positive" if edge_val >= 0 else "negative"
        prob_row = f'''<div class="prob-row">
    {reconstructed_tag}
    <span class="prob"><span class="k">Implied</span> <b>{odds.format_prob(implied)}</b></span>
    <span class="prob"><span class="k">Est.</span> <b>{odds.format_prob(est_prob)}</b></span>
    <span class="prob"><span class="k">Edge</span> <b class="edge {edge_cls}">{odds.format_prob_signed(edge_val)}</b></span>
  </div>'''

    leg_list_html = ""
    if is_parlay:
        leg_prob_cls = "leg-prob reconstructed" if historical else "leg-prob"
        items = "".join(
            f'<li><span class="leg-sel">{escape(leg.get("selection", ""))}</span> '
            f'<span class="{leg_prob_cls}">{odds.format_prob(leg.get("estimated_prob", 0.0))}</span></li>'
            for leg in legs
        )
        leg_list_html = f'<ul class="leg-list">{items}</ul>'

    badge = _result_badge_html(bet.get("result"), bet.get("net"))

    return f'''<article class="bet {cls}">
  <div class="bet-top">
    <span class="tier-badge">{num} · {escape(tier_label)}</span>
    {badge}
  </div>
  <div class="bet-head">
    <h3 class="bet-title">{title}</h3>
    <span class="odds">{format_odds(dk_odds)}{" est." if bet.get("odds_estimated") else ""}</span>
  </div>
  <p class="bet-meta">
    <span class="stake">${stake:.2f}</span>
    <span class="game">{escape(game)}</span>
    <span class="kickoff">{escape(kickoff_disp)}</span>
  </p>
  {prob_row}
  {leg_list_html}
  <p class="reason-summary">{escape(bet.get("reason_summary", ""))}</p>
  {_reason_detail_html(bet)}
</article>'''


def render_card_section(card, tz, historical=False):
    """The three tiers, in order, skipping any that are absent."""
    parts = []
    for tier in ("easy_bet", "fun_parlay", "lottery_ticket"):
        if card.get(tier):
            parts.append(render_card_bet(tier, card[tier], tz, historical=historical))
    return "".join(parts)


def render_more_collapsibles(slot):
    parts = []
    angles = slot.get("angles") or []
    if angles:
        items = "".join(f"<li>{escape(a)}</li>" for a in angles)
        parts.append(f'<details class="more angles"><summary>Angles</summary><ul>{items}</ul></details>')

    avoid = slot.get("avoid_list") or []
    if avoid:
        items = "".join(
            f'<div class="avoid-item"><p class="avoid-sel">{escape(a.get("selection", ""))}</p>'
            f'<p class="avoid-why">{escape(a.get("why_avoid", ""))}</p></div>'
            for a in avoid
        )
        parts.append(f'<details class="more avoid"><summary>Skip these</summary>{items}</details>')

    boosts = slot.get("boost_check") or []
    if boosts:
        items = "".join(
            f'<div class="boost-item {"fits" if b.get("fits_card") else "no-fit"}">'
            f'<p class="boost-verdict">{"Fits card" if b.get("fits_card") else "No fit"}</p>'
            f'<p class="boost-desc">{escape(b.get("boost_description", ""))}</p></div>'
            for b in boosts
        )
        parts.append(f'<details class="more boosts"><summary>Boosts</summary>{items}</details>')

    return "".join(parts)


def render_empty_state(title, sub):
    return f'''<div class="empty-state">
  <p class="empty-title">{escape(title)}</p>
  <p class="empty-sub">{escape(sub)}</p>
</div>'''


def _budget_block_html(budget):
    return f'''<div class="budget">
  <span class="budget-amount">${budget["total"]:.2f}</span>
  <span class="budget-label">to play this slot</span>
  <p class="budget-why">{escape(budget["why"])}</p>
</div>'''


def _slot_head_html(title, sub):
    return f'''<header class="slot-head">
  <h2 class="slot-title">{escape(title)}</h2>
  <p class="slot-sub">{escape(sub)}</p>
</header>'''


def _pick_today_slot(window, config, weekends_dir, now):
    """The soonest-upcoming-or-current slot file in the current weekend
    (date >= today), i.e. today's own card if it's been built. Returns
    (slot_name, slot_dict) or None."""
    tz = ZoneInfo(config["timezone"])
    today = now.astimezone(tz).date()
    slots = _load_weekend_slots(weekends_dir, window["weekend_id"])
    candidates = []
    for name, data in slots.items():
        d = data.get("date")
        if d and date.fromisoformat(d) >= today:
            candidates.append((name, data))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[1]["date"])
    return candidates[0]


def render_today(window, config, weekends_dir, now):
    tz = ZoneInfo(config["timezone"])
    picked = _pick_today_slot(window, config, weekends_dir, now)

    if picked is None:
        empty = render_empty_state(
            "No card yet today",
            f"Next card: {_next_card_full(window)}, around 9 AM",
        )
        recent = _find_most_recent_graded_slot(weekends_dir)
        recent_html = ""
        if recent and recent.get("date"):
            recent_html = render_slot_row(
                recent.get("slot", ""),
                date.fromisoformat(recent["date"]),
                "graded",
                recent,
                tz,
            )
        return empty + recent_html

    slot_name, slot = picked
    weekend_id = window["weekend_id"]
    d = date.fromisoformat(slot["date"])
    n_games = len(_distinct_games(slot))
    game_word = "game" if n_games == 1 else "games"
    sub = f"{window['nfl_week_label']} · {n_games} {game_word}"

    head = _slot_head_html(_slot_day_title(d), sub)
    budget = slate.slot_budget(weekend_id, slot_name, config, now, weekends_dir=weekends_dir)
    budget_html = _budget_block_html(budget)
    card = slot.get("card") or {}
    tiers_html = render_card_section(card, tz, historical=bool(slot.get("historical_import")))
    more_html = render_more_collapsibles(slot)
    return head + budget_html + tiers_html + more_html


def render_slot_row(slot_name, slot_date, status, slot_data, tz):
    """A <section class="slot-row STATUS"> block for one weekend slot --
    shared by the Weekend panel, the Today panel's no-card-yet fallback,
    and the Record panel's "Last weekend" block."""
    name_label = _SLOT_DAY_LABEL.get(slot_name, slot_name.title())
    date_label = f"{slot_date.strftime('%b')} {slot_date.day}"
    status_label = {
        "graded": "Graded",
        "published": "Published",
        "future": "Upcoming",
        "skipped": "Not built",
    }.get(status, status.title())

    head = f'''<div class="slot-row-head">
    <span class="slot-name">{escape(name_label)}</span> <span class="slot-date">{escape(date_label)}</span>
    <span class="slot-status">{escape(status_label)}</span>
  </div>'''

    bets_html = ""
    if slot_data:
        card = slot_data.get("card") or {}
        items = []
        for tier, short in (("easy_bet", "Easy"), ("fun_parlay", "Fun"), ("lottery_ticket", "Lottery")):
            bet = card.get(tier)
            if not bet:
                continue
            legs = bet.get("legs") or []
            sel = f"{len(legs)}-leg parlay" if legs else bet.get("selection", "")
            # odds_estimated: the DK price was never recorded (only reconstructed
            # after the fact) — never show an estimate as if it were the real line.
            odds_est = " est." if bet.get("odds_estimated") else ""
            line = f"${float(bet.get('stake', 0.0)):.2f} · {format_odds(bet.get('dk_odds'))}{odds_est}"
            badge = _result_badge_html(bet.get("result"), bet.get("net"))
            items.append(f'''<li class="slot-bet">
      <span class="slot-bet-tier">{short}</span>
      <span class="slot-bet-sel">{escape(sel)}</span>
      <span class="slot-bet-line">{line}</span>
      {badge}
    </li>''')
        if items:
            bets_html = f'<ul class="slot-bets">{"".join(items)}</ul>'

    return f'<section class="slot-row {status}">{head}{bets_html}</section>'


def _stoploss_html(status, config):
    limit = config["weekend_loss_limit"]
    settled_net = status["settled_net"]
    open_stakes = status["open_stakes"]
    loss_used = max(0.0, -settled_net)
    capacity_left = limit + settled_net - open_stakes
    pct = 0.0
    if limit > 0:
        pct = max(0.0, min(100.0, (loss_used / limit) * 100))
    return f'''<div class="stoploss">
  <div class="stoploss-bar"><div class="stoploss-used" style="width:{pct:.0f}%"></div></div>
  <p class="stoploss-text">${loss_used:.2f} of the ${limit:.2f} stop-loss used · ${capacity_left:.2f} left</p>
</div>'''


def _weekend_stat_grid_html(status, config):
    net = status["settled_net"]
    net_cls = "positive" if net >= 0 else "negative"
    capacity_left = config["weekend_loss_limit"] + net - status["open_stakes"]
    cap_cls = "positive" if capacity_left >= 0 else "negative"
    lottery_label = "Used" if status["lottery_used"] else "Available"
    return f'''<div class="stat-grid">
  <div class="stat"><span class="label">Tiers 1-2 Net</span><span class="value {net_cls}">{_money_signed(net)}</span></div>
  <div class="stat"><span class="label">Capacity Left</span><span class="value {cap_cls}">${capacity_left:.2f}</span></div>
  <div class="stat"><span class="label">Lottery Ticket</span><span class="value">{lottery_label}</span></div>
</div>'''


def _slot_row_status(data, today):
    if data is None:
        return None
    card = data.get("card") or {}
    tiers = [card.get(t) for t in ("easy_bet", "fun_parlay", "lottery_ticket") if card.get(t)]
    if tiers and all(t.get("result") is not None for t in tiers):
        return "graded"
    return "published"


def render_weekend(window, config, weekends_dir, now):
    tz = ZoneInfo(config["timezone"])
    today = now.astimezone(tz).date()
    weekend_id = window["weekend_id"]
    thursday = date.fromisoformat(weekend_id)
    slots_cfg = config.get("slots", ["thu", "sat", "sun", "mon"])
    slot_files = _load_weekend_slots(weekends_dir, weekend_id)

    status = slate.weekend_status(weekend_id, config, weekends_dir=weekends_dir)

    rows = []
    for s in slots_cfg:
        off = slate._SLOT_OFFSET.get(s, 0)
        d = thursday + timedelta(days=off)
        data = slot_files.get(s)
        row_status = _slot_row_status(data, today)
        if row_status is None:
            row_status = "future" if d >= today else "skipped"
        rows.append(render_slot_row(s, d, row_status, data, tz))

    return _stoploss_html(status, config) + _weekend_stat_grid_html(status, config) + "".join(rows)


def render_legs(window, config, weekends_dir, now):
    picked = _pick_today_slot(window, config, weekends_dir, now)
    if picked is None:
        return ""
    _, slot = picked
    leg_bank = slot.get("leg_bank") or []
    if not leg_bank:
        return ""

    tz = ZoneInfo(config["timezone"])
    ranked = sort_leg_bank(leg_bank)
    groups = {}
    order = []
    for leg in ranked:
        game = leg.get("game") or "—"
        if game not in groups:
            groups[game] = []
            order.append(game)
        groups[game].append(leg)

    parts = []
    for game in order:
        legs = groups[game]
        kickoff_disp = _format_kickoff_et(legs[0].get("kickoff"), tz)
        items = "".join(render_leg_entry(leg) for leg in legs)
        parts.append(
            f'<section class="leg-group"><h3 class="leg-group-title">{escape(game)} '
            f'<span class="kickoff">{escape(kickoff_disp)}</span></h3>{items}</section>'
        )
    return "".join(parts)


def render_leg_entry(leg):
    dk_odds = leg.get("dk_odds")
    implied = odds.american_to_implied_prob(dk_odds)
    est_prob = leg.get("estimated_prob", 0.0)
    edge_val = odds.edge(est_prob, implied)
    edge_cls = "positive" if edge_val >= 0 else "negative"
    market = leg.get("market", "") or ""
    prop_tag = '<span class="prop-tag">Player prop</span>' if leg.get("player") else ""

    return f'''<div class="leg">
  <div class="leg-head"><span class="leg-sel">{escape(leg.get("selection", ""))}</span><span class="odds">{format_odds(dk_odds)}</span></div>
  <p class="leg-meta">
    <span class="market">{escape(market)}</span> {prop_tag}
    <span class="prob"><span class="k">Impl</span> {odds.format_prob(implied)}</span>
    <span class="prob"><span class="k">Est</span> {odds.format_prob(est_prob)}</span>
    <span class="edge {edge_cls}">{odds.format_prob_signed(edge_val)}</span>
  </p>
  <p class="leg-reason">{escape(leg.get("reason", ""))}</p>
</div>'''


def render_scoreboard(season_sb):
    """The Record panel's primary stat-grid, scoped to the current season."""
    record_str = f'{season_sb["wins"]}W-{season_sb["losses"]}L-{season_sb["cashouts"]}CO-{season_sb["open"]}Open'
    pl_cls = "positive" if season_sb["cash_pl"] >= 0 else "negative"
    bank_cls = "positive" if season_sb["bankroll_remaining"] >= 0 else "negative"
    return f'''<div class="stat-grid">
  <div class="stat"><span class="label">Record</span><span class="value">{escape(record_str)}</span></div>
  <div class="stat"><span class="label">Cash P/L</span><span class="value {pl_cls}">{escape(format_money(season_sb["cash_pl"]))}</span></div>
  <div class="stat"><span class="label">Bankroll left</span><span class="value {bank_cls}">{escape(format_money(season_sb["bankroll_remaining"]))}</span></div>
  <div class="stat"><span class="label">Streak</span><span class="value">{escape(season_sb["current_streak"])}</span></div>
</div>'''


def _card_record_html(cr):
    t = cr["tiers12"]
    record_str = f'{t["W"]}W-{t["L"]}L-{t["P"]}P'
    net_cls = "positive" if cr["net12"] >= 0 else "negative"
    lot = cr["lottery"]
    return f'''<section class="card-record">
  <h3>Card record</h3>
  <p class="card-record-note">If every card bet was placed as written</p>
  <div class="stat-grid">
    <div class="stat"><span class="label">Tiers 1-2</span><span class="value">{escape(record_str)}</span></div>
    <div class="stat"><span class="label">Tiers 1-2 Net</span><span class="value {net_cls}">{escape(_money_signed(cr["net12"]))}</span></div>
    <div class="stat"><span class="label">Lottery</span><span class="value">{lot["W"]}W-{lot["L"]}L</span></div>
  </div>
</section>'''


def _last_weekend_html(prev_weekend_id, config, weekends_dir):
    tz = ZoneInfo(config["timezone"])
    thursday = date.fromisoformat(prev_weekend_id)
    slots_cfg = config.get("slots", ["thu", "sat", "sun", "mon"])
    slot_files = _load_weekend_slots(weekends_dir, prev_weekend_id)

    rows = []
    for s in slots_cfg:
        data = slot_files.get(s)
        if data is None:
            continue
        off = slate._SLOT_OFFSET.get(s, 0)
        d = thursday + timedelta(days=off)
        card = data.get("card") or {}
        tiers = [card.get(t) for t in ("easy_bet", "fun_parlay", "lottery_ticket") if card.get(t)]
        row_status = "graded" if tiers and all(t.get("result") is not None for t in tiers) else "published"
        rows.append(render_slot_row(s, d, row_status, data, tz))

    if not rows:
        return '<section class="last-weekend"><h3>Last weekend</h3><p class="record-note">No cards built last weekend.</p></section>'
    return f'<section class="last-weekend"><h3>Last weekend</h3>{"".join(rows)}</section>'


def render_record(config, csv_path, weekends_dir, window):
    season_sb = compute_scoreboard(csv_path, since=config["season_start"], starting_bankroll=config["starting_bankroll"])
    alltime_sb = compute_scoreboard(csv_path, since=None, starting_bankroll=config["starting_bankroll"])
    alltime_label = find_earliest_date_label(csv_path) or "—"

    stat_grid = render_scoreboard(season_sb)
    alltime_line = (
        '<p class="alltime-line">'
        f'All-time since {escape(alltime_label)}: '
        f'{alltime_sb["wins"]}W-{alltime_sb["losses"]}L-{alltime_sb["cashouts"]}CO-{alltime_sb["open"]}Open, '
        f'cash P/L {escape(format_money(alltime_sb["cash_pl"]))}'
        '</p>'
    )
    note_parts = []
    if season_sb.get("no_data_rows"):
        note_parts.append(f'<p class="record-note">{season_sb["no_data_rows"]} bet(s) missing details in the log.</p>')
    # A manually-known caveat (e.g. real wins not yet logged to
    # bet_log.csv) must still say so on the page -- it is additive to,
    # never a replacement for, the no_data_rows note above.
    for caveat in config.get("record_caveats") or []:
        note_parts.append(f'<p class="record-note">{escape(caveat)}</p>')
    note = "".join(note_parts)

    card_record_html = _card_record_html(compute_card_record(weekends_dir))

    prev_weekend_id = (date.fromisoformat(window["weekend_id"]) - timedelta(days=7)).isoformat()
    last_weekend_html = _last_weekend_html(prev_weekend_id, config, weekends_dir)

    return stat_grid + alltime_line + note + card_record_html + last_weekend_html


def render_page(template_path, weekends_dir, csv_path, config, now):
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    window = slate.current_window(now, config)
    tz = ZoneInfo(config["timezone"])
    local_now = now.astimezone(tz)

    today_html = render_today(window, config, weekends_dir, now)
    weekend_html = render_weekend(window, config, weekends_dir, now)
    legs_html = render_legs(window, config, weekends_dir, now)
    record_html = render_record(config, csv_path, weekends_dir, window)

    updated_at = f"Updated {local_now.strftime('%a')} {_fmt_time_ampm(local_now)} ET"
    next_update = f"Next card: {_next_card_short(window)} ~9 AM"

    sample_banner = ""
    picked = _pick_today_slot(window, config, weekends_dir, now)
    if picked is not None and picked[1].get("is_sample"):
        sample_banner = (
            '<div class="sample-banner">SAMPLE DATA — schema test only, '
            "not a real slot. Do not place any bets from this page.</div>"
        )

    replacements = {
        "{{TODAY}}": today_html,
        "{{WEEKEND}}": weekend_html,
        "{{LEGS}}": legs_html,
        "{{RECORD}}": record_html,
        "{{UPDATED_AT}}": updated_at,
        "{{NEXT_UPDATE}}": next_update,
        "{{SAMPLE_BANNER}}": sample_banner,
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    return template


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_now(args, config):
    if args.now:
        dt = datetime.fromisoformat(args.now)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    if args.date:
        tz = ZoneInfo(config.get("timezone", "America/New_York"))
        d = datetime.strptime(args.date, "%Y-%m-%d").date()
        return datetime(d.year, d.month, d.day, 12, 0, tzinfo=tz)
    return datetime.now(timezone.utc)


def main(argv):
    parser = argparse.ArgumentParser(prog="build.py")
    parser.add_argument("--publish", help="WEEKEND_ID/SLOT, e.g. 2026-09-24/sat -- extra RULE 1/RULE 2 checks for that slot")
    parser.add_argument("--now", help="full ISO 8601 timestamp override, for reproducible runs")
    parser.add_argument("--date", help="YYYY-MM-DD, evaluated at noon local time (ignored if --now is given)")
    parser.add_argument("--template", help="page template path (default: templates/page.html) -- override for tests only")
    args = parser.parse_args(argv[1:])

    repo_root = REPO_ROOT
    csv_path = repo_root / "data" / "bet_log.csv"
    weekends_dir = repo_root / "data" / "weekends"
    config_path = repo_root / "data" / "config.json"
    template_path = Path(args.template) if args.template else repo_root / "templates" / "page.html"
    output_path = repo_root / "docs" / "index.html"

    config = load_config(config_path)
    now = _resolve_now(args, config)

    publish_target = None
    if args.publish:
        wid, sep, slot_name = args.publish.partition("/")
        if not sep or not slot_name:
            print("--publish expects WEEKEND_ID/SLOT, e.g. 2026-09-24/sat", file=sys.stderr)
            sys.exit(2)
        publish_target = (wid, slot_name)

    # If this raises RuleViolation, it must propagate uncaught: that's the
    # actual safety mechanism for a page giving real-money advice. Do not
    # wrap this in a try/except that renders a degraded page anyway.
    validate_build(weekends_dir, config, now, publish_target=publish_target)

    html = render_page(template_path, weekends_dir, csv_path, config, now)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    window = slate.current_window(now, config)
    print(f"Now: {now.isoformat()}")
    print(f"Window: {window}")
    if publish_target:
        print(f"Publish target validated: {publish_target[0]}/{publish_target[1]}")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main(sys.argv)
