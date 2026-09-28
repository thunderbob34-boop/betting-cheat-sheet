"""The weekend budget engine.

A "weekend" runs Thursday through Monday. Gus's own money rule: "only a $5
loss per weekend... if I win Thursday I have more to play with Saturday...
if I lose I want to be a little more reserved Saturday, Sunday, Monday, but
I do want to bet on all those days." This module turns that rule into a
number: how much is safe to stake on tiers 1-2 (Easy Bet + Fun Parlay) for
one slot, given how the rest of the weekend has gone so far.

The Lottery Ticket tier is explicitly OUTSIDE this pacing: it never counts
toward settled_net/open_stakes and its own stake never shrinks or grows the
budget for tiers 1-2. Gus set it outside the $5 line on purpose.

Python 3 stdlib only.
"""

import argparse
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEEKENDS_DIR = REPO_ROOT / "data" / "weekends"
DEFAULT_CONFIG_PATH = REPO_ROOT / "data" / "config.json"

# Kept in sync with build.py's _CONFIG_DEFAULTS by hand — duplicated rather
# than imported so that build.py (which imports this module for the RULE 1
# budget check at --publish time) never creates an import cycle.
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

EPSILON = 1e-6

# Slot name -> days after the weekend's Thursday. "fri" is tolerated (a slot
# file for it is read fine) even though it's not in the default config
# "slots" list.
_SLOT_OFFSET = {"thu": 0, "fri": 1, "sat": 2, "sun": 3, "mon": 4}
_OFFSET_SLOT = {v: k for k, v in _SLOT_OFFSET.items()}


class SlateError(Exception):
    """Raised when a weekend/slot JSON file can't be read or parsed. Always
    propagates uncaught -- same fail-loud contract as build.py's
    RuleViolation -- and always names the offending file path, so a single
    corrupt file doesn't crash every caller with a generic, path-less
    traceback."""


def load_config(path=DEFAULT_CONFIG_PATH):
    """Same contract as build.load_config: never raises, missing/bad file
    falls back to _CONFIG_DEFAULTS, merged under whatever valid data IS
    present."""
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
# Calendar: weekends, slots, NFL week labels
# ---------------------------------------------------------------------------

def _weekend_thursday_for_date(d):
    """Return the Thursday of the Thu..Mon window containing date d, or
    None if d is a Tuesday/Wednesday (between weekends)."""
    wd = d.weekday()  # Mon=0 .. Sun=6
    if wd == 3:  # Thu
        return d
    if wd == 4:  # Fri
        return d - timedelta(days=1)
    if wd == 5:  # Sat
        return d - timedelta(days=2)
    if wd == 6:  # Sun
        return d - timedelta(days=3)
    if wd == 0:  # Mon
        return d - timedelta(days=4)
    return None  # Tue=1, Wed=2


def nfl_week_label(thursday_date, config):
    """'NFL Week N' for the weekend whose Thursday is thursday_date, per
    config['nfl_week1_thursday']. 2026-09-24 with week1 2026-09-10 -> Week 3."""
    week1 = datetime.strptime(config["nfl_week1_thursday"], "%Y-%m-%d").date()
    n = 1 + (thursday_date - week1).days // 7
    return f"NFL Week {n}"


def current_window(now, config):
    """Figure out where `now` sits relative to the Thu..Mon weekend cycle.

    Returns {weekend_id, today_slot, next_slot, next_slot_date, nfl_week_label}.
    today_slot is None on Tue/Wed (between weekends) or any day whose slot
    isn't in config["slots"] (e.g. a plain Friday, by default).
    """
    tz = ZoneInfo(config.get("timezone", "America/New_York"))
    local_now = now.astimezone(tz)
    today = local_now.date()
    slots = config.get("slots", ["thu", "sat", "sun", "mon"])

    thursday = _weekend_thursday_for_date(today)
    if thursday is not None:
        offset = (today - thursday).days
        slot_name = _OFFSET_SLOT.get(offset)
        today_slot = slot_name if slot_name in slots else None
    else:
        # Tue/Wed: "belongs to the weekend that just ended" for display.
        wd = today.weekday()  # Tue=1, Wed=2
        last_monday = today - timedelta(days=wd)
        thursday = last_monday - timedelta(days=4)
        today_slot = None

    weekend_id = thursday.isoformat()

    # Next slot: the soonest slot (in this weekend or the next one) whose
    # date is strictly after today.
    candidates = []
    for s in slots:
        off = _SLOT_OFFSET.get(s)
        if off is None:
            continue
        candidates.append((thursday + timedelta(days=off), s))
    candidates.sort()

    next_slot = None
    next_slot_date = None
    for d, s in candidates:
        if d > today:
            next_slot = s
            next_slot_date = d.isoformat()
            break

    if next_slot is None:
        next_thursday = thursday + timedelta(days=7)
        first_slot = slots[0] if slots else "thu"
        off = _SLOT_OFFSET.get(first_slot, 0)
        next_slot = first_slot
        next_slot_date = (next_thursday + timedelta(days=off)).isoformat()

    return {
        "weekend_id": weekend_id,
        "today_slot": today_slot,
        "next_slot": next_slot,
        "next_slot_date": next_slot_date,
        "nfl_week_label": nfl_week_label(thursday, config),
    }


# ---------------------------------------------------------------------------
# Reading slot files
# ---------------------------------------------------------------------------

def _slot_files(weekend_id, weekends_dir):
    d = Path(weekends_dir) / weekend_id
    if not d.is_dir():
        return []
    return sorted(d.glob("*.json"))


def _load_slot(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise SlateError(f"Malformed JSON in {path}: {e}")
    except OSError as e:
        raise SlateError(f"Could not read {path}: {e}")


def _bet_summary(bet, tier):
    if tier in ("fun_parlay", "fun_parlay_2"):
        legs = bet.get("legs") or []
        sels = "; ".join(leg.get("selection", "") for leg in legs)
        return f"{len(legs)}-leg parlay: {sels}"
    return bet.get("selection", "")


def weekend_status(weekend_id, config, weekends_dir=None, exclude_slot=None):
    """Read every slot file for weekend_id and summarize tiers 1-2 pacing.

    exclude_slot, when given, skips that one slot's own file — this is what
    makes slot_budget's re-run of a slot idempotent (a slot's own draft
    stakes never shrink its own budget).

    The Lottery Ticket tier is excluded entirely from settled_net/open_stakes
    (Gus's rule); lottery_used just records whether ANY slot file in the
    weekend has one, for the at-most-one-per-weekend business rule (RULE 6
    in build.py) and for display.
    """
    weekends_dir = weekends_dir or DEFAULT_WEEKENDS_DIR
    settled_net = 0.0
    open_stakes = 0.0
    lottery_used = False
    open_bets = []

    for path in _slot_files(weekend_id, weekends_dir):
        data = _load_slot(path)
        # Slot identity is derived from the file's OWN location (its
        # filename), never from its own claimed "slot" field -- a
        # mislabeled internal field (a plausible copy/paste slip) must
        # never make a file invisible to (or silently miscounted by) the
        # exclude_slot idempotency check.
        slot = path.stem
        if slot == exclude_slot:
            continue
        card = data.get("card") or {}
        for tier in ("easy_bet", "easy_bet_2", "fun_parlay", "fun_parlay_2"):
            bet = card.get(tier)
            if not bet or bet.get("pass"):  # a Pass is $0 and never graded
                continue
            stake = float(bet.get("stake", 0.0))
            result = bet.get("result")
            if result is None:
                open_stakes += stake
                open_bets.append({
                    "slot": slot,
                    "tier": tier,
                    "summary": _bet_summary(bet, tier),
                    "kickoff": bet.get("kickoff"),
                    "game": bet.get("game"),
                })
            else:
                net = bet.get("net")
                if net is None:
                    raise ValueError(
                        f"{path}: card.{tier} has result={result!r} but net "
                        "is null -- a graded bet must carry both (see "
                        "grade.py's set_result/compute_net); fix the file "
                        "rather than silently dropping its stake from the "
                        "stop-loss accounting"
                    )
                settled_net += float(net)
        if card.get("lottery_ticket"):
            lottery_used = True

    return {
        "weekend_id": weekend_id,
        "settled_net": round(settled_net, 2),
        "open_stakes": round(open_stakes, 2),
        "lottery_used": lottery_used,
        "open_bets": open_bets,
    }


# ---------------------------------------------------------------------------
# Budget math
# ---------------------------------------------------------------------------

def _round_down_to_step(value, step):
    """Floor value to the nearest multiple of step, cents-safe (float math
    on dollar amounts otherwise drifts by fractions of a cent).

    This must floor DOWN only -- a slot's budget is always rounded down so
    worst-case weekend loss never exceeds the stop-loss. Nudge by a tiny,
    float-noise-scale epsilon before flooring; do NOT round value*100 to
    the nearest cent first (that rounds some values UP to the next cent,
    which can itself be an exact multiple of step_cents, silently
    returning one whole step above the true floor)."""
    if step <= 0:
        return round(value, 2)
    step_cents = round(step * 100)
    if step_cents <= 0:
        return round(value, 2)
    value_cents = value * 100
    steps = math.floor(value_cents / step_cents + 1e-9)
    return round(steps * step_cents / 100, 2)


def _money(v):
    return f"${v:,.2f}"


def _pluralize(n, word):
    return word if n == 1 else word + "s"


def slot_budget(weekend_id, slot, config, now, weekends_dir=None):
    """How much is safe to stake on tiers 1-2 for this one slot, right now.

    capacity = weekend_loss_limit + settled_net - open_stakes, computed
    across the weekend's OTHER slot files (this slot's own file, if it
    already exists, is excluded -- re-running/rebuilding the same slot is
    idempotent, it never shrinks its own budget).

    slots_remaining = this slot through the end of config["slots"],
    inclusive (e.g. "sat" with slots=[thu,sat,sun,mon] -> 3: sat,sun,mon).

    raw = capacity / slots_remaining, then x reserve_factor_when_down if
    the weekend is currently down (settled_net < 0) -- Gus's "more reserved
    after a loss" rule. Clamped at >= 0, floored to stake_step, and zeroed
    out entirely if it lands under min_stake.
    """
    weekends_dir = weekends_dir or DEFAULT_WEEKENDS_DIR
    status = weekend_status(weekend_id, config, weekends_dir=weekends_dir, exclude_slot=slot)
    settled_net = status["settled_net"]
    open_stakes = status["open_stakes"]
    capacity = config["weekend_loss_limit"] + settled_net - open_stakes

    slots = config.get("slots", ["thu", "sat", "sun", "mon"])
    if slot in slots:
        slots_remaining = len(slots) - slots.index(slot)
    elif slot == "fri":
        slots_remaining = 1  # explicitly tolerated exception, not a pacing slot
    else:
        # An unrecognized slot name must never silently be treated as the
        # weekend's very last remaining slot -- that hands it the ENTIRE
        # remaining stop-loss capacity instead of a fair share, and a
        # typo'd/retry slot file is exactly the realistic way this name
        # would arrive here.
        raise ValueError(
            f"slot_budget: slot {slot!r} is not one of config['slots'] "
            f"{slots!r} (and is not the tolerated 'fri' exception) -- "
            "refusing to guess how many slots remain in the weekend"
        )

    raw = capacity / slots_remaining if slots_remaining else capacity
    if settled_net < -EPSILON:
        raw *= config["reserve_factor_when_down"]

    raw = max(raw, 0.0)
    total = _round_down_to_step(raw, config["stake_step"])
    if total < config["min_stake"] - EPSILON:
        total = 0.0

    if total > 0:
        easy = _round_down_to_step(total * config["easy_share"], config["stake_step"])
        fun = round(total - easy, 2)
    else:
        easy = 0.0
        fun = 0.0

    plural = _pluralize(slots_remaining, "slot")
    if settled_net < -EPSILON:
        reserve_pct = round((1 - config["reserve_factor_when_down"]) * 100)
        why = (
            f"Down {_money(-settled_net)} this weekend → {_money(capacity)} left of the "
            f"${config['weekend_loss_limit']:.0f} stop-loss across {slots_remaining} {plural}, "
            f"holding {reserve_pct}% back."
        )
    elif settled_net > EPSILON:
        why = (
            f"Up {_money(settled_net)} this weekend → {_money(capacity)} available against "
            f"the ${config['weekend_loss_limit']:.0f} stop-loss across {slots_remaining} {plural}."
        )
    else:
        why = (
            f"Even this weekend → {_money(capacity)} available across "
            f"{slots_remaining} {plural}."
        )
    if open_stakes > EPSILON:
        why += f" {_money(open_stakes)} already committed and reserved."

    return {
        "weekend_id": weekend_id,
        "slot": slot,
        "capacity": round(capacity, 2),
        "settled_net": settled_net,
        "open_stakes": open_stakes,
        "slots_remaining": slots_remaining,
        "total": total,
        "easy": easy,
        "fun": fun,
        "lottery_available": not status["lottery_used"],
        "why": why,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _budget_target_for_window(window):
    """(weekend_id, slot) to hand to slot_budget for "the slot we should be
    computing a budget for right now", given a current_window() result.

    On a day with a today_slot (Thu/Sat/Sun/Mon), that's simply this
    weekend's own slot. On a Tue/Wed (or any day with no today_slot),
    window["weekend_id"] intentionally still names the weekend that just
    ended (current_window's own display/review contract -- see its
    docstring), but next_slot belongs to the UPCOMING weekend, not that
    elapsed one. Budget math must follow the slot's own weekend, so this
    derives it from next_slot_date rather than reusing window["weekend_id"]
    (which would silently mix an elapsed weekend's id with a next
    weekend's slot name)."""
    if window["today_slot"]:
        return window["weekend_id"], window["today_slot"]
    slot = window["next_slot"]
    next_date = date.fromisoformat(window["next_slot_date"])
    offset = _SLOT_OFFSET.get(slot, 0)
    weekend_id = (next_date - timedelta(days=offset)).isoformat()
    return weekend_id, slot


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


def _status_command(args):
    config = load_config(DEFAULT_CONFIG_PATH)
    now = _resolve_now(args, config)
    window = current_window(now, config)

    budget_weekend_id, slot_for_budget = _budget_target_for_window(window)
    budget = slot_budget(budget_weekend_id, slot_for_budget, config, now)
    status = weekend_status(window["weekend_id"], config)

    prev_weekend_id = (date.fromisoformat(window["weekend_id"]) - timedelta(days=7)).isoformat()
    prev_status = weekend_status(prev_weekend_id, config)

    output = {
        "now": now.isoformat(),
        "window": window,
        "weekend_status": status,
        "budget": budget,
        "previous_weekend_ungraded": {
            "weekend_id": prev_weekend_id,
            "open_bets": prev_status["open_bets"],
        },
    }
    print(json.dumps(output, indent=2))


def main(argv):
    parser = argparse.ArgumentParser(prog="slate.py")
    sub = parser.add_subparsers(dest="command", required=True)

    status_p = sub.add_parser("status")
    status_p.add_argument("--date", help="YYYY-MM-DD, evaluated at noon local time")
    status_p.add_argument("--now", help="full ISO 8601 timestamp override")
    status_p.set_defaults(func=_status_command)

    args = parser.parse_args(argv[1:])
    args.func(args)


if __name__ == "__main__":
    main(sys.argv)
