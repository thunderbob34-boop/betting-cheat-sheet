"""Fair ("true") odds from the sharp market -- the yardstick for RULE 13.

A sharp book like Pinnacle takes big bets from professionals and moves its
line when they win, so its price (with its small cut stripped out) is the
best public estimate of a bet's real chance. A DraftKings price is only
worth betting when it beats that fair number by config.min_edge.

Read-only: fetches public odds pages and prints them. Never logs in, never
touches an account, cannot place a bet.

Usage:
    python3 src/sharp.py board [--team NAME]    # Pinnacle NFL: games + player props, de-vigged
    python3 src/sharp.py kalshi EVENT_TICKER    # Kalshi market midpoints, e.g. KXNFLGAME-26OCT01CLEPIT

Python 3 stdlib only.
"""

import argparse
import json
import sys
import urllib.request

import odds

PINNACLE = "https://guest.api.arcadia.pinnacle.com/0.1"
# The anonymous "guest" key pinnacle.com's own public odds pages send for
# logged-out visitors -- not an account credential.
PINNACLE_GUEST_KEY = "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R"
NFL_LEAGUE_ID = 889
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"


def _get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _pinnacle(path):
    return _get(f"{PINNACLE}{path}", {"X-API-Key": PINNACLE_GUEST_KEY})


def _pair(a_name, a_price, b_name, b_price, **extra):
    return {
        **extra,
        "sides": [
            {"side": a_name, "price": a_price, "fair_prob": round(odds.devig_two_way(a_price, b_price), 4)},
            {"side": b_name, "price": b_price, "fair_prob": round(odds.devig_two_way(b_price, a_price), 4)},
        ],
    }


def pinnacle_board(team=None):
    """Every NFL game on Pinnacle (moneyline, main spread, main total) plus
    every player/game prop it lists, each side with its de-vigged fair
    probability. `team` filters to games whose team names contain it."""
    matchups = _pinnacle(f"/leagues/{NFL_LEAGUE_ID}/matchups")
    markets = _pinnacle(f"/leagues/{NFL_LEAGUE_ID}/markets/straight")
    by_matchup = {}
    for m in markets:
        if m.get("period") == 0 and m.get("status", "open") == "open":  # prop markets carry no status
            by_matchup.setdefault(m["matchupId"], []).append(m)

    games = {}
    for g in matchups:
        if g.get("type") != "matchup" or g.get("parent"):
            continue
        teams = {p["alignment"]: p["name"] for p in g.get("participants", [])}
        name = f"{teams.get('away', '?')} @ {teams.get('home', '?')}"
        if team and team.lower() not in name.lower():
            continue
        lines = []
        for m in by_matchup.get(g["id"], []):
            if m.get("isAlternate"):
                continue
            prices = {p["designation"]: p for p in m.get("prices", [])}
            if m["type"] == "moneyline" and {"home", "away"} <= prices.keys():
                lines.append(_pair(teams["away"], prices["away"]["price"], teams["home"], prices["home"]["price"],
                                   market="moneyline"))
            elif m["type"] == "spread" and {"home", "away"} <= prices.keys():
                a, h = prices["away"], prices["home"]
                lines.append(_pair(f"{teams['away']} {a['points']:+g}", a["price"],
                                   f"{teams['home']} {h['points']:+g}", h["price"], market="spread"))
            elif m["type"] == "total" and {"over", "under"} <= prices.keys():
                pts = prices["over"]["points"]
                lines.append(_pair(f"Over {pts:g}", prices["over"]["price"], f"Under {pts:g}", prices["under"]["price"],
                                   market="total"))
        games[g["id"]] = {"game": name, "kickoff_utc": g.get("startTime"), "lines": lines, "props": []}

    for s in matchups:
        if s.get("type") != "special" or (s.get("parent") or {}).get("id") not in games:
            continue
        names = {p["id"]: p["name"] for p in s.get("participants", [])}
        for m in by_matchup.get(s["id"], []):
            prices = m.get("prices") or []
            if len(prices) != 2:
                continue  # two-sided props only: a 3+ way market can't be de-vigged this way
            a, b = prices
            pts = a.get("points")
            label = (s.get("special") or {}).get("description", "")
            games[s["parent"]["id"]]["props"].append(_pair(
                f"{names.get(a['participantId'], '?')}{f' {pts:g}' if pts is not None else ''}", a["price"],
                f"{names.get(b['participantId'], '?')}{f' {pts:g}' if pts is not None else ''}", b["price"],
                prop=label))
    return {"source": "Pinnacle (guest odds feed), de-vigged", "games": list(games.values())}


def kalshi_event(event_ticker):
    """Midpoint of each Kalshi market's bid/ask in an event -- an exchange
    price with no bookmaker cut, so the midpoint already is a fair chance."""
    data = _get(f"{KALSHI}/markets?event_ticker={event_ticker}&limit=200")
    out = []
    for m in data.get("markets", []):
        bid, ask = m.get("yes_bid_dollars"), m.get("yes_ask_dollars")
        if bid is None or ask is None:
            continue
        out.append({"ticker": m["ticker"], "yes": m.get("yes_sub_title"),
                    "bid": float(bid), "ask": float(ask), "fair_prob": round((float(bid) + float(ask)) / 2, 4)})
    return {"source": f"Kalshi {event_ticker}, bid/ask midpoint", "markets": out}


def main(argv):
    parser = argparse.ArgumentParser(prog="sharp.py")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("board")
    b.add_argument("--team", help="only games whose team names contain this, e.g. Eagles")
    k = sub.add_parser("kalshi")
    k.add_argument("event_ticker")
    args = parser.parse_args(argv[1:])
    result = pinnacle_board(args.team) if args.command == "board" else kalshi_event(args.event_ticker)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main(sys.argv)
