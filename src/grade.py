"""Grade settled bets in a slot file.

Never touches data/bet_log.csv -- that file is Gus's real-money history and
is edited by hand (screenshots -> Claude appends rows) elsewhere. This tool
only ever writes to data/weekends/<weekend_id>/<slot>.json.

Usage:
    python3 src/grade.py set <weekend_id> <slot> <easy_bet|fun_parlay|lottery_ticket> <Won|Lost|Push|Void>
    python3 src/grade.py pending [--now ISO]

Python 3 stdlib only.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import odds

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEEKENDS_DIR = REPO_ROOT / "data" / "weekends"

VALID_TIERS = ("easy_bet", "fun_parlay", "lottery_ticket")
VALID_RESULTS = ("Won", "Lost", "Push", "Void")

PENDING_GRACE = timedelta(hours=4)


class GradeError(Exception):
    """Raised for any grade.py usage error (bad tier/result, missing file,
    missing bet) -- never for anything in bet_log.csv, which this module
    never reads or writes."""


def _slot_path(weekend_id, slot, weekends_dir=None):
    weekends_dir = weekends_dir or DEFAULT_WEEKENDS_DIR
    return Path(weekends_dir) / weekend_id / f"{slot}.json"


def _load_json_file(path):
    """Load one JSON file, raising a GradeError (not a bare, path-less
    traceback) that names the offending path if it's missing or
    malformed."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise GradeError(f"Malformed JSON in {path}: {e}")
    except OSError as e:
        raise GradeError(f"Could not read {path}: {e}")


def compute_net(result, stake, dk_odds):
    """Won -> stake * (decimal - 1); Lost -> -stake; Push/Void -> 0.0.
    Rounded to the cent.

    Computed with exact Decimal arithmetic, not binary float, and rounded
    with standard half-up rounding -- a payout that lands exactly on a
    half-cent boundary (common with nickel stakes and American odds) must
    round the way a person expects, not wherever binary-float noise
    happens to push it (e.g. 0.10 * (2.05 - 1) == 0.105 exactly, but as a
    Python float is 0.10499999999999998, which round() floors to $0.10
    instead of the correct $0.11)."""
    if result == "Won":
        if not dk_odds:
            raise GradeError(f"invalid dk_odds {dk_odds!r} for a Won bet -- cannot compute a payout")
        stake_d = Decimal(str(stake))
        odds_d = Decimal(str(dk_odds))
        if odds_d < 0:
            decimal_odds = Decimal(1) + Decimal(100) / (-odds_d)
        else:
            decimal_odds = Decimal(1) + odds_d / Decimal(100)
        net = stake_d * (decimal_odds - Decimal(1))
        return float(net.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if result == "Lost":
        return round(-stake, 2)
    if result in ("Push", "Void"):
        return 0.0
    raise GradeError(f"Unknown result {result!r}, expected one of {VALID_RESULTS}")


def set_result(weekend_id, slot, tier, result, weekends_dir=None):
    """Set card[tier].result and .net in the slot file, preserving every
    other field and the file's json.dump(indent=2) + trailing-newline
    formatting. Returns the updated bet dict.

    Refuses (GradeError) to silently overwrite a bet that already has a
    result -- a mistaken re-invocation (wrong slot/tier, or a retried run)
    must never silently flip a previously-settled result with no warning
    and no record of what it used to be. If this is an intentional
    correction, edit the file by hand instead."""
    if tier not in VALID_TIERS:
        raise GradeError(f"Unknown tier {tier!r}, expected one of {VALID_TIERS}")
    if result not in VALID_RESULTS:
        raise GradeError(f"Unknown result {result!r}, expected one of {VALID_RESULTS}")

    path = _slot_path(weekend_id, slot, weekends_dir)
    if not path.exists():
        raise GradeError(f"No slot file at {path}")

    data = _load_json_file(path)

    card = data.get("card") or {}
    bet = card.get(tier)
    if not bet:
        raise GradeError(f"{path} has no card.{tier} to grade")

    if bet.get("result") is not None:
        raise GradeError(
            f"{path}: card.{tier} already has a result ({bet['result']!r}, "
            f"net {bet.get('net')!r}) -- refusing to silently overwrite "
            "it. If this is an intentional correction, edit the file by "
            "hand."
        )

    stake = float(bet.get("stake", 0.0))
    net = compute_net(result, stake, bet.get("dk_odds"))
    bet["result"] = result
    bet["net"] = net

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return bet


def pending(weekends_dir=None, now=None):
    """Every card bet (any tier, any weekend) with result still null whose
    kickoff is more than PENDING_GRACE (4h) before now."""
    weekends_dir = Path(weekends_dir or DEFAULT_WEEKENDS_DIR)
    now = now or datetime.now(timezone.utc)
    results = []
    if not weekends_dir.is_dir():
        return results

    for weekend_dir in sorted(p for p in weekends_dir.iterdir() if p.is_dir()):
        for path in sorted(weekend_dir.glob("*.json")):
            data = _load_json_file(path)
            card = data.get("card") or {}
            for tier in VALID_TIERS:
                bet = card.get(tier)
                if not bet or bet.get("result") is not None:
                    continue
                legs = bet.get("legs") or []
                selection = bet.get("selection") or (
                    f"{len(legs)}-leg parlay" if legs else "<unlabeled>"
                )
                kickoff = bet.get("kickoff")
                if not kickoff:
                    # An open bet with no kickoff at all can never be
                    # surfaced by the grace-period check below (nothing to
                    # compare "now" against), so it would otherwise sit
                    # "open" forever, silently and permanently suppressing
                    # every later slot's stop-loss budget. Surface it
                    # explicitly instead of dropping it.
                    results.append({
                        "weekend_id": data.get("weekend_id") or weekend_dir.name,
                        "slot": data.get("slot") or path.stem,
                        "tier": tier,
                        "selection": selection,
                        "kickoff": None,
                        "needs_kickoff": True,
                        "game": bet.get("game"),
                    })
                    continue
                try:
                    dt = datetime.fromisoformat(kickoff)
                except ValueError:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if now - dt > PENDING_GRACE:
                    results.append({
                        "weekend_id": data.get("weekend_id") or weekend_dir.name,
                        "slot": data.get("slot") or path.stem,
                        "tier": tier,
                        "selection": selection,
                        "kickoff": kickoff,
                        "needs_kickoff": False,
                        "game": bet.get("game"),
                    })
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _set_command(args):
    bet = set_result(args.weekend_id, args.slot, args.tier, args.result)
    print(json.dumps({
        "weekend_id": args.weekend_id,
        "slot": args.slot,
        "tier": args.tier,
        "result": bet["result"],
        "net": bet["net"],
    }, indent=2))


def _pending_command(args):
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    print(json.dumps(pending(now=now), indent=2))


def main(argv):
    parser = argparse.ArgumentParser(prog="grade.py")
    sub = parser.add_subparsers(dest="command", required=True)

    set_p = sub.add_parser("set")
    set_p.add_argument("weekend_id")
    set_p.add_argument("slot")
    set_p.add_argument("tier", choices=VALID_TIERS)
    set_p.add_argument("result", choices=VALID_RESULTS)
    set_p.set_defaults(func=_set_command)

    pending_p = sub.add_parser("pending")
    pending_p.add_argument("--now", help="full ISO 8601 timestamp override")
    pending_p.set_defaults(func=_pending_command)

    args = parser.parse_args(argv[1:])
    args.func(args)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except GradeError as e:
        print(f"grade.py error: {e}", file=sys.stderr)
        sys.exit(1)
