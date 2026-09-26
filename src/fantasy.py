"""Fantasy lineup helper -- Gus's ESPN leagues (full PPR).

Same discipline as the betting card: every projection comes from a source
actually read (ESPN's own projection in the league's scoring + Rotowire's
full-PPR projection via Sleeper's public API), the two are averaged, and
disagreement, injuries, byes and already-played games are flagged instead
of hidden. Nothing here logs in anywhere -- a league must be viewable to
the public for ESPN to hand over its rosters.

Usage:
    python3 src/fantasy.py pull [--week N]    # fetch every league in data/fantasy.json,
                                              # write data/fantasy/<season>-wk<N>.json
    python3 src/fantasy.py show [--week N]    # print the lineups from that file

Python 3 stdlib only.
"""

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "data" / "fantasy.json"
OUT_DIR = REPO_ROOT / "data" / "fantasy"

ESPN_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
SLEEPER_PROJ = "https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular" + "".join(
    f"&position%5B%5D={p}" for p in ("QB", "RB", "WR", "TE", "K", "DEF")
)
SLEEPER_PLAYERS = "https://api.sleeper.app/v1/players/nfl"

POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
# ESPN lineupSlotId -> slot name (20 = bench, 21 = IR).
ESPN_SLOTS = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K", 23: "FLEX", 20: "BENCH", 21: "IR"}
FLEX_OK = ("RB", "WR", "TE")
DEFAULT_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "D/ST"]
# ESPN and Sleeper spell a few team abbreviations differently.
ESPN_TO_SLEEPER_TEAM = {"WSH": "WAS"}

OUT_STATUSES = {"OUT", "INJURY_RESERVE", "IR", "SUSPENSION", "SUSPENDED", "PUP", "NFI"}
DOUBTFUL = {"DOUBTFUL"}
QUESTIONABLE = {"QUESTIONABLE"}

# Two projections this far apart (relative) get a "sources disagree" flag.
DISAGREE_RATIO = 0.35
# Start/sit calls closer than this many points are coin flips -- say so.
CLOSE_CALL_PTS = 1.0


class FantasyError(Exception):
    pass


def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "betting-cheat-sheet/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise FantasyError(
                f"{url} returned HTTP {e.code}: the league is private. Ask the league "
                "manager to turn on 'Make league viewable to public' (League Settings -> "
                "Basic Settings), or send roster screenshots instead. This tool never "
                "uses login cookies."
            )
        raise FantasyError(f"{url} returned HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise FantasyError(f"could not reach {url}: {e}")


# ---------------------------------------------------------------------------
# Pure logic (unit-tested)
# ---------------------------------------------------------------------------

def name_key(name, team, pos):
    """Match key for a player across ESPN and Sleeper when IDs don't line
    up: lowercase name without punctuation or Jr./Sr./II/III, plus team and
    position."""
    words = "".join(c for c in (name or "").lower() if c.isalnum() or c == " ").split()
    words = [w for w in words if w not in ("jr", "sr", "ii", "iii", "iv", "v")]
    team = ESPN_TO_SLEEPER_TEAM.get(team, team)
    return f"{' '.join(words)}|{team}|{pos}"


def normalize_status(status):
    return (status or "ACTIVE").strip().upper().replace(" ", "_")


def combine_projection(espn_pts, sleeper_pts):
    """Average whichever of the two projections exist. Returns
    (points, sources_used, disagree_flag)."""
    vals = [(n, v) for n, v in (("ESPN", espn_pts), ("Rotowire", sleeper_pts)) if v is not None]
    if not vals:
        return 0.0, [], False
    pts = sum(v for _, v in vals) / len(vals)
    disagree = False
    if len(vals) == 2:
        hi, lo = max(espn_pts, sleeper_pts), min(espn_pts, sleeper_pts)
        disagree = hi > 0 and (hi - lo) / hi > DISAGREE_RATIO and (hi - lo) >= 3.0
    return round(pts, 2), [n for n, _ in vals], disagree


def effective_points(player):
    """What a player is worth in a lineup decision: 0 if he can't play or
    his game already happened (those are reported separately)."""
    status = normalize_status(player.get("status"))
    if player.get("bye") or status in OUT_STATUSES or status in DOUBTFUL:
        return 0.0
    return float(player.get("proj", 0.0))


def optimize_lineup(players, slots=None):
    """Fill slots with the highest-projected eligible players. Fixed slots
    first, FLEX last from whatever RB/WR/TE is left -- that order is
    optimal for a single FLEX. Players whose game has already kicked off
    are locked: they stay wherever the league has them and can't be moved,
    so they're only placed into an open slot of their own position.

    Returns {"starters": [{slot, player|None}], "bench": [...], "notes": [...]}.
    """
    slots = list(slots or DEFAULT_SLOTS)
    pool = sorted(players, key=lambda p: effective_points(p), reverse=True)
    used = set()
    starters = [{"slot": s, "player": None} for s in slots]

    # Locked players (game already started) can't move: keep locked starters
    # in the slot ESPN has them in, and keep locked bench players benched.
    for p in pool:
        if not p.get("locked"):
            continue
        used.add(p["key"])
        cur = p.get("current_slot")
        spot = next((e for e in starters if e["slot"] == cur and e["player"] is None), None)
        if spot is not None:
            spot["player"] = p

    def pick(eligible):
        for p in pool:
            if p["key"] in used:
                continue
            if p["pos"] in eligible:
                used.add(p["key"])
                return p
        return None

    for entry in starters:
        if entry["slot"] != "FLEX" and entry["player"] is None:
            entry["player"] = pick((entry["slot"],))
    for entry in starters:
        if entry["slot"] == "FLEX" and entry["player"] is None:
            entry["player"] = pick(FLEX_OK)

    starting = {e["player"]["key"] for e in starters if e["player"]}
    bench = [p for p in pool if p["key"] not in starting]
    notes = []
    for entry in starters:
        p = entry["player"]
        if p is None:
            notes.append(f"No eligible player for {entry['slot']} -- pick one up off waivers.")
            continue
        if effective_points(p) == 0.0 and not p.get("locked"):
            notes.append(f"{p['name']} ({entry['slot']}) projects 0 ({_why_zero(p)}) -- find a replacement.")
        # Closest bench player who could take this slot.
        eligible = FLEX_OK if entry["slot"] == "FLEX" else (entry["slot"],)
        alt = next((b for b in bench if b["pos"] in eligible and effective_points(b) > 0 and not b.get("locked")), None)
        if alt and not p.get("locked") and effective_points(p) - effective_points(alt) < CLOSE_CALL_PTS:
            notes.append(
                f"Close call at {entry['slot']}: {p['name']} {effective_points(p):.1f} vs "
                f"{alt['name']} {effective_points(alt):.1f} -- check inactives before kickoff."
            )
        if normalize_status(p.get("status")) in QUESTIONABLE:
            notes.append(f"{p['name']} is questionable -- confirm he's active ~90 min before kickoff.")
        if p.get("disagree"):
            notes.append(f"{p['name']}: ESPN and Rotowire disagree a lot ({p['espn']} vs {p['rotowire']}).")
        if p.get("locked"):
            notes.append(f"{p['name']}'s game already kicked off -- he's locked in at {entry['slot']}.")
    return {"starters": starters, "bench": bench, "notes": notes}


def lineup_changes(starters, players):
    """Moves Gus has to make in the ESPN app: who comes in, who goes out,
    comparing the recommended starters with who ESPN has starting now.
    Swapping two starters between RB and FLEX changes nothing, so it's
    not reported."""
    recommended = {e["player"]["key"] for e in starters if e.get("player")}
    current = {p["key"] for p in players if p.get("current_slot") not in (None, "BENCH", "IR")}
    ins = [p for p in players if p["key"] in recommended - current]
    outs = [p for p in players if p["key"] in current - recommended]
    ins.sort(key=effective_points, reverse=True)
    outs.sort(key=effective_points, reverse=True)
    return [
        f"Start {i['name']} ({effective_points(i):.1f}) instead of {o['name']} ({effective_points(o):.1f})"
        for i, o in zip(ins, outs)
    ] + [f"Start {i['name']} ({effective_points(i):.1f})" for i in ins[len(outs):]] \
      + [f"Bench {o['name']} ({effective_points(o):.1f})" for o in outs[len(ins):]]


def _why_zero(p):
    if p.get("bye"):
        return "bye week"
    status = normalize_status(p.get("status"))
    if status != "ACTIVE":
        return status.lower().replace("_", " ")
    return "no projection"


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def load_pro_teams(season):
    data = fetch_json(ESPN_BASE.format(season=season) + "?view=proTeamSchedules_wl")
    teams = {}
    for t in data["settings"]["proTeams"]:
        teams[t["id"]] = {
            "abbrev": t["abbrev"],
            "bye": t.get("byeWeek"),
            "games": t.get("proGamesByScoringPeriod") or {},
        }
    return teams


def kickoff_for(pro_team, week):
    games = pro_team["games"].get(str(week)) or []
    if not games:
        return None
    return datetime.fromtimestamp(games[0]["date"] / 1000, tz=timezone.utc)


def sleeper_projections(season, week):
    """{espn_id(str) | 'DEF:<ABBR>' | name_key: full-PPR points} from
    Rotowire via Sleeper. Many Sleeper players carry no espn_id, so every
    projection is also indexed by name_key as a fallback."""
    rows = fetch_json(SLEEPER_PROJ.format(season=season, week=week))
    players = fetch_json(SLEEPER_PLAYERS)
    out = {}
    for r in rows:
        pts = (r.get("stats") or {}).get("pts_ppr")
        if pts is None:
            continue
        pid = r.get("player_id")
        info = players.get(pid) or {}
        if info.get("position") == "DEF" or (r.get("player") or {}).get("position") == "DEF":
            out[f"DEF:{pid}"] = float(pts)
        else:
            if info.get("espn_id"):
                out[str(info["espn_id"])] = float(pts)
            pl = r.get("player") or {}
            full = f"{pl.get('first_name', '')} {pl.get('last_name', '')}"
            out[name_key(full, r.get("team") or pl.get("team"), pl.get("position"))] = float(pts)
    return out


def _espn_week_projection(player, week):
    for s in player.get("stats") or []:
        if s.get("scoringPeriodId") == week and s.get("statSourceId") == 1 and s.get("statSplitTypeId") == 1:
            return round(float(s.get("appliedTotal", 0.0)), 2)
    return None


def espn_player_pool(season, week, limit=1500, league_id=None):
    """ESPN's public player list, no login. With league_id it's that league's
    free agents, projected in that league's own scoring (leagues differ);
    without, ESPN's default full-PPR list (for rosters typed in by hand)."""
    scope = f"leagues/{league_id}" if league_id else "leaguedefaults/3"
    url = (ESPN_BASE.format(season=season) + f"/segments/0/{scope}"
           f"?scoringPeriodId={week}&view=kona_player_info")
    flt = {"players": {"filterSlotIds": {"value": [0, 2, 4, 6, 16, 17]}, "limit": limit,
                       "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    if league_id:
        flt["players"]["filterStatus"] = {"value": ["FREEAGENT", "WAIVERS"]}
    req = urllib.request.Request(url, headers={"User-Agent": "betting-cheat-sheet/1.0",
                                               "X-Fantasy-Filter": json.dumps(flt)})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        raise FantasyError(f"could not reach ESPN's player list: {e}")
    return [p["player"] for p in data.get("players") or [] if p.get("player")]


def _plain(name):
    words = "".join(c for c in (name or "").lower() if c.isalnum() or c == " ").split()
    return " ".join(w for w in words if w not in ("jr", "sr", "ii", "iii", "iv", "v"))


def manual_roster_entries(roster, pool):
    """Turn a hand-typed roster (["Lamar Jackson", {"name": "Bijan Robinson",
    "slot": "RB"}, "Broncos D/ST", ...]) into ESPN-style roster entries.
    `slot` is only needed for players whose game already kicked off (so they
    stay where they were); everyone else can be written as a plain name."""
    by_name = {}
    for pl in pool:
        by_name.setdefault(_plain(pl.get("fullName")), pl)
    slot_ids = {v: k for k, v in ESPN_SLOTS.items()}
    entries, missing = [], []
    for item in roster:
        name = item if isinstance(item, str) else item.get("name", "")
        slot = None if isinstance(item, str) else item.get("slot")
        key = _plain(name)
        # "Broncos D/ST", "Broncos DST", "Broncos Defense" and "Broncos DEF" all mean the same unit.
        for alias in (" defense", " def", " d st"):
            if key.endswith(alias):
                key = key[: -len(alias)] + " dst"
        pl = by_name.get(key)
        if pl is None:
            missing.append(name)
            continue
        entries.append({"playerPoolEntry": {"player": pl},
                        "lineupSlotId": slot_ids.get((slot or "BENCH").upper(), 20)})
    return entries, missing


# Rough spread of a fantasy matchup's margin (points, one standard deviation).
# An assumption, not a sourced number: weekly team scores swing ~20-25 pts
# each, so the gap between two teams swings ~30-40. ESPN's own win probability
# is shown beside ours as the second opinion.
MATCHUP_MARGIN_SD = 35.0
STARTING = ("QB", "RB", "WR", "TE", "FLEX", "K", "D/ST")


def win_probability(my_pts, opp_pts, sd=MATCHUP_MARGIN_SD):
    """Chance my_pts beats opp_pts if the margin is normal around the
    projected gap."""
    return round(0.5 * (1 + math.erf((my_pts - opp_pts) / (sd * math.sqrt(2)))), 3)


def _espn_week_actual(player, week):
    for s in player.get("stats") or []:
        if s.get("scoringPeriodId") == week and s.get("statSourceId") == 0 and s.get("statSplitTypeId") == 1:
            return round(float(s.get("appliedTotal", 0.0)), 2)
    return None


def _player_from_entry(e, week, pro_teams, sleeper, now):
    pl = (e.get("playerPoolEntry") or {}).get("player") or {}
    pos = POSITIONS.get(pl.get("defaultPositionId"))
    if not pos:
        return None
    pro = pro_teams.get(pl.get("proTeamId")) or {}
    abbrev = pro.get("abbrev", "FA")
    espn = _espn_week_projection(pl, week)
    if pos == "D/ST":
        rw = sleeper.get(f"DEF:{ESPN_TO_SLEEPER_TEAM.get(abbrev, abbrev)}")
    else:
        rw = sleeper.get(str(pl.get("id")))
        if rw is None:
            rw = sleeper.get(name_key(pl.get("fullName"), abbrev, pos))
    pts, used, disagree = combine_projection(espn, rw)
    ko = kickoff_for(pro, week) if pro else None
    locked = bool(ko and ko <= now)
    actual = _espn_week_actual(pl, week) if locked else None
    if actual is not None:
        # His game has started or finished: what he's actually scored beats any projection.
        pts, used, disagree = actual, ["ESPN actual"], False
    return {
        "key": str(pl.get("id")),
        "name": pl.get("fullName", "?"),
        "pos": pos,
        "team": abbrev,
        "status": normalize_status(pl.get("injuryStatus")),
        "bye": pro.get("bye") == week,
        "kickoff": ko.isoformat() if ko else None,
        "locked": locked,
        "actual": actual,
        "current_slot": ESPN_SLOTS.get(e.get("lineupSlotId"), "BENCH"),
        "espn": espn,
        "rotowire": rw,
        "proj": pts,
        "sources": used,
        "disagree": disagree,
    }


def _team_name(t):
    return (t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}").strip()


def _lineup_value(p):
    """Points a player counts for in a lineup total: what he actually scored
    if his game has started, otherwise his usable projection."""
    return float(p["proj"]) if p.get("locked") else effective_points(p)


def _current_lineup_points(players):
    return round(sum(_lineup_value(p) for p in players if p.get("current_slot") in STARTING), 1)


def _starter_points(result):
    return round(sum(_lineup_value(e["player"]) for e in result["starters"] if e.get("player")), 1)


def apply_moves(players, moves, pool, week, pro_teams, sleeper, now):
    """Roster after the suggested add/drops (research file `moves`)."""
    by_name = {_plain(pl.get("fullName")): pl for pl in pool}
    out = list(players)
    applied = []
    for m in moves or []:
        add = by_name.get(_plain(m.get("add")))
        drop = _plain(m.get("drop"))
        if add is None or not any(_plain(p["name"]) == drop for p in out):
            continue
        out = [p for p in out if _plain(p["name"]) != drop]
        new = _player_from_entry({"playerPoolEntry": {"player": add}, "lineupSlotId": 20}, week, pro_teams, sleeper, now)
        if new:
            out.append(new)
            applied.append(f"+{new['name']} / -{m.get('drop')}")
    return out, applied


def pull_league(league, season, week, pro_teams, sleeper, now, pool=None, moves=None):
    if league.get("roster"):
        entries, missing = manual_roster_entries(league["roster"], pool or espn_player_pool(season, week))
        team = {"_name": league.get("team_name") or "My team", "roster": {"entries": entries}}
        return _lineup_from_team(league, team, {}, week, pro_teams, sleeper, now,
                                 extra_notes=[f"Couldn't find {m!r} in ESPN's player list -- check the spelling." for m in missing])
    url = (ESPN_BASE.format(season=season) + f"/segments/0/leagues/{league['league_id']}"
           f"?view=mTeam&view=mRoster&view=mSettings&view=mMatchupScore&scoringPeriodId={week}")
    data = fetch_json(url)
    want = (league.get("team_name") or "").strip().lower()
    team = None
    for t in data.get("teams") or []:
        if (want and _team_name(t).lower() == want) or (league.get("team_id") and t.get("id") == league["team_id"]):
            team = t
            team["_name"] = _team_name(t)
            break
    if team is None:
        names = [_team_name(t) for t in data.get("teams") or []]
        raise FantasyError(f"league {league['league_id']}: no team named {league.get('team_name')!r}; teams are {names}")
    result = _lineup_from_team(league, team, data, week, pro_teams, sleeper, now)

    # --- This week's matchup: best lineup vs the opponent's current lineup. ---
    game = next((m for m in data.get("schedule") or []
                 if m.get("matchupPeriodId") == week
                 and team["id"] in ((m.get("home") or {}).get("teamId"), (m.get("away") or {}).get("teamId"))), None)
    if game:
        me_side, opp_side = ("home", "away") if game["home"]["teamId"] == team["id"] else ("away", "home")
        opp_team = next((t for t in data["teams"] if t["id"] == game[opp_side]["teamId"]), None)
        opp_players = [p for p in (_player_from_entry(e, week, pro_teams, sleeper, now)
                                   for e in ((opp_team or {}).get("roster") or {}).get("entries") or []) if p]
        opp_pts = _current_lineup_points(opp_players)
        best_pts = _starter_points(result)
        current_pts = _current_lineup_points(result["_players"])
        matchup = {
            "opponent": _team_name(opp_team or {}),
            "opp_proj": opp_pts,
            "my_best": best_pts,
            "my_current": current_pts,
            "win_prob_best": win_probability(best_pts, opp_pts),
            "win_prob_current": win_probability(current_pts, opp_pts),
            "espn_my_proj": round(float(game[me_side].get("totalProjectedPoints") or 0.0), 1),
            "espn_opp_proj": round(float(game[opp_side].get("totalProjectedPoints") or 0.0), 1),
            "espn_win_prob": game[me_side].get("winProbability"),
        }
        if moves:
            pool = espn_player_pool(season, week, limit=600, league_id=league["league_id"])
            moved, applied = apply_moves(result["_players"], moves, pool, week, pro_teams, sleeper, now)
            if applied:
                alt = optimize_lineup(moved, league.get("slots") or DEFAULT_SLOTS)
                matchup["with_pickups"] = {
                    "moves": applied,
                    "my_best": _starter_points(alt),
                    "win_prob": win_probability(_starter_points(alt), opp_pts),
                }
        result["matchup"] = matchup
    result.pop("_players", None)
    return result


def _lineup_from_team(league, team, data, week, pro_teams, sleeper, now, extra_notes=()):
    players = [p for p in (_player_from_entry(e, week, pro_teams, sleeper, now)
                           for e in (team.get("roster") or {}).get("entries") or []) if p]
    slots = league.get("slots") or DEFAULT_SLOTS
    result = optimize_lineup(players, slots)
    result["notes"] = list(extra_notes) + result["notes"]
    has_current = any(p.get("current_slot") not in (None, "BENCH") for p in players)
    result["changes"] = lineup_changes(result["starters"], players) if has_current else None
    return {
        "league_name": league.get("name") or data.get("settings", {}).get("name") or str(league["league_id"]),
        "league_id": league["league_id"],
        "team_name": team["_name"],
        "slots": slots,
        "_players": players,
        **result,
    }


def cmd_pull(week):
    config = json.loads(CONFIG_PATH.read_text())
    season = config["season"]
    now = datetime.now(timezone.utc)
    pro_teams = load_pro_teams(season)
    sleeper = sleeper_projections(season, week)
    leagues = []
    research_path = OUT_DIR / f"research-{season}-wk{week:02d}.json"
    research = json.loads(research_path.read_text()).get("leagues", {}) if research_path.exists() else {}
    for league in config["leagues"]:
        try:
            moves = (research.get(str(league.get("league_id"))) or {}).get("moves")
            lg = pull_league(league, season, week, pro_teams, sleeper, now, moves=moves)
            lg.pop("_players", None)
            leagues.append(lg)
        except FantasyError as e:
            leagues.append({"league_name": league.get("name") or str(league["league_id"]),
                            "league_id": league["league_id"], "error": str(e)})
    out = {"season": season, "week": week, "generated_at": now.isoformat(),
           "scoring": config.get("scoring", "Full PPR"),
           "sources": "ESPN projection (league scoring) + Rotowire full-PPR projection (via Sleeper), averaged",
           "leagues": leagues}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{season}-wk{week:02d}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return path, out


def print_lineups(out):
    for lg in out["leagues"]:
        print(f"\n== {lg['league_name']} ==")
        if lg.get("error"):
            print("  ERROR:", lg["error"])
            continue
        print(f"  Team: {lg['team_name']}")
        for s in lg["starters"]:
            p = s["player"]
            if p:
                flag = "" if p["status"] == "ACTIVE" else f" [{p['status'].lower()}]"
                print(f"  {s['slot']:5s} {p['name']:24s} {p['pos']:4s} {p['team']:4s} {p['proj']:5.1f}{flag}")
            else:
                print(f"  {s['slot']:5s} (empty)")
        print("  Bench:", ", ".join(f"{b['name']} {b['proj']:.1f}" for b in lg["bench"]))
        for n in lg["notes"]:
            print("  !", n)


def current_week(config, now=None):
    now = now or datetime.now(timezone.utc)
    start = datetime.fromisoformat(config["week1_tuesday"]).replace(tzinfo=timezone.utc)
    return max(1, (now - start).days // 7 + 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pull", "show"):
        p = sub.add_parser(name)
        p.add_argument("--week", type=int)
    args = ap.parse_args(argv)
    config = json.loads(CONFIG_PATH.read_text())
    week = args.week or current_week(config)
    try:
        if args.cmd == "pull":
            path, out = cmd_pull(week)
            print(f"wrote {path}")
        else:
            out = json.loads((OUT_DIR / f"{config['season']}-wk{week:02d}.json").read_text())
        print_lineups(out)
    except FantasyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
