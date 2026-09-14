"""Build the weekly betting cheat sheet page.

Reads data/bet_log.csv (scoreboard, computed fresh every run) plus one week
JSON file from data/weeks/ (the researched card/leg-bank/boosts/etc), validates
the week JSON against the house's non-negotiable ground rules (plan Section 3),
and renders templates/page.html -> docs/index.html via simple string
replacement. No templating library, Python 3 stdlib only.

Usage:
    python3 src/build.py [path/to/week.json]

With no argument, the alphabetically-last *.json file in data/weeks/ is used.

Safety note: this script makes no network calls of any kind (no requests,
no urllib, no http.client) and touches no betting API. It cannot place a bet.
That's Ground Rule 7 ("Claude never places a bet") and it holds trivially here
because there is nothing in this file capable of placing one.
"""

import csv
import json
import re
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import odds


class RuleViolation(Exception):
    """Raised when a week JSON's card violates one of the house's ground rules.

    This must always propagate all the way out of main() uncrossed and
    unswallowed. A RuleViolation means "do not render this page" — the
    exception bubbling up to a non-zero exit code IS the safety mechanism.
    """


EPSILON = 1e-6  # float-rounding slack for the $5 budget check, nothing more
WEEKLY_BUDGET_CAP = 5.00  # Rule 1 — the whole week's cap across NFL + CFB combined

# In-code fallback if data/config.json is ever missing/unreadable/malformed —
# the build must never crash for lack of a config file.
_CONFIG_DEFAULTS = {"season_start": "2026-09-01", "starting_bankroll": 50.00}


# ---------------------------------------------------------------------------
# Config (data/config.json — optional; falls back to _CONFIG_DEFAULTS)
# ---------------------------------------------------------------------------

def load_config(path):
    """Load data/config.json and return a dict with at least season_start
    and starting_bankroll. Never raises: a missing file, unreadable file,
    bad JSON, or a JSON value that isn't an object all fall back to
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
# Ground-rule validation (plan Section 3 — non-negotiable)
# ---------------------------------------------------------------------------

LOTTERY_TICKET_STAKE = 0.50  # exact, per Gus — "flat", not a range like tiers 1-2
LOTTERY_TICKET_MIN_LEGS = 10
LOTTERY_TICKET_MAX_LEGS = 20
FUN_PARLAY_MIN_LEGS = 2
FUN_PARLAY_MAX_LEGS = 3
FUN_PARLAY_LEG_FLOOR = 0.55


def _check_pre_kickoff(bet, bet_label):
    """RULE 2 (pre-kickoff only) — applies to every tier, no exceptions,
    including the Lottery Ticket. Gus relaxed the leg-count rule and the
    $5-cap membership for the Lottery Ticket, not this one."""
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


def validate_rules(week):
    """Validate week['card'] (the three named tiers — easy_bet, fun_parlay,
    lottery_ticket) against the house's hard guardrails.

    Raises RuleViolation (which must be allowed to propagate) on any failure.
    Rules 3 and 7 are handled by comment below, not by a check here, because
    they are not properties a static JSON file can violate.
    """
    card = week.get("card", {})
    easy_bet = card.get("easy_bet")
    fun_parlay = card.get("fun_parlay")
    lottery_ticket = card.get("lottery_ticket")

    # --- RULE 6: tier structure. Easy Bet is required; Fun Parlay and
    # Lottery Ticket are each optional (0 or 1). ---
    if not easy_bet:
        raise RuleViolation(
            "RULE 6 VIOLATION: card.easy_bet is required (the weekly card "
            "always starts with one straight bet)"
        )
    if easy_bet.get("legs"):
        raise RuleViolation(
            "RULE 6 VIOLATION: card.easy_bet must be a single straight "
            "selection, not a multi-leg bet"
        )

    if fun_parlay is not None:
        legs = fun_parlay.get("legs", []) or []
        if not (FUN_PARLAY_MIN_LEGS <= len(legs) <= FUN_PARLAY_MAX_LEGS):
            raise RuleViolation(
                f"RULE 6 VIOLATION: card.fun_parlay must have "
                f"{FUN_PARLAY_MIN_LEGS}-{FUN_PARLAY_MAX_LEGS} legs, got "
                f"{len(legs)}"
            )
        for leg in legs:
            p = leg.get("estimated_prob", 0.0)
            if p < FUN_PARLAY_LEG_FLOOR:
                leg_label = leg.get("selection", "<unnamed leg>")
                raise RuleViolation(
                    f"RULE 6 VIOLATION: card.fun_parlay leg '{leg_label}' "
                    f"has estimated_prob {p}, below the "
                    f"{FUN_PARLAY_LEG_FLOOR:.0%} floor every Fun Parlay leg "
                    "must clear"
                )

    if lottery_ticket is not None:
        legs = lottery_ticket.get("legs", []) or []
        if not (LOTTERY_TICKET_MIN_LEGS <= len(legs) <= LOTTERY_TICKET_MAX_LEGS):
            raise RuleViolation(
                f"RULE 6 VIOLATION: card.lottery_ticket must have "
                f"{LOTTERY_TICKET_MIN_LEGS}-{LOTTERY_TICKET_MAX_LEGS} legs, "
                f"got {len(legs)}"
            )
        stake = float(lottery_ticket.get("stake", 0.0))
        if abs(stake - LOTTERY_TICKET_STAKE) > EPSILON:
            raise RuleViolation(
                f"RULE 6 VIOLATION: card.lottery_ticket stake must be "
                f"exactly ${LOTTERY_TICKET_STAKE:.2f} (flat, per rule), got "
                f"${stake:.2f}"
            )
        # No per-leg probability floor here on purpose — a Lottery Ticket is
        # explicitly exempt from Fun Parlay's 55% floor and the old >3-leg
        # 60% floor. The whole point is real long-shot legs; the honesty
        # requirement is that the combined probability is SHOWN (see
        # render_lottery_ticket's "1 in X"), not that it's high.

    # --- RULE 1: budget. Easy Bet + Fun Parlay stakes <= $5.00. The
    # Lottery Ticket is explicitly OUTSIDE this line per Gus — its flat
    # $0.50 never counts against the cap and never crowds out tiers 1-2. ---
    total_stake = float(easy_bet.get("stake", 0.0))
    if fun_parlay is not None:
        total_stake += float(fun_parlay.get("stake", 0.0))
    if total_stake > WEEKLY_BUDGET_CAP + EPSILON:
        raise RuleViolation(
            f"RULE 1 VIOLATION: Easy Bet + Fun Parlay total ${total_stake:.2f}, "
            f"exceeds the ${WEEKLY_BUDGET_CAP:.0f}/week budget (the Lottery "
            "Ticket's stake is outside this line by design and is not "
            "included in this total)"
        )

    # --- RULE 2: pre-kickoff only, every tier, no exceptions. ---
    for label, bet in (
        ("easy_bet", easy_bet),
        ("fun_parlay", fun_parlay),
        ("lottery_ticket", lottery_ticket),
    ):
        if bet is None:
            continue
        bet_label = bet.get("id") or label
        _check_pre_kickoff(bet, bet_label)

    # --- RULE 3: "bet first, boost second". ---
    # This is a research-process rule (pick the ticket on merit, THEN check
    # whether a boost happens to fit it), not a property a static rendered
    # JSON file can expose — nothing here can tell whether Gus/Claude looked
    # at boosts before or after building the card. The only thing this
    # script can and does do mechanically is keep boost_check structurally
    # separate from card: nothing below ever derives card contents from
    # boost_check, and boosts are rendered as their own clearly-labeled
    # {{BOOST_CHECK}} section, never merged into the card itself. The same
    # applies to tier order: "Lottery Ticket only after tiers 1 and 2 are
    # set" is a build-order discipline (see .claude/commands/cheatsheet.md),
    # not something a finished JSON file can prove either way.

    # --- RULE 7: "Claude never places a bet." ---
    # Holds trivially: this module makes no network calls and touches no
    # betting API. Confirmed by `grep -rE "requests|urllib|http.client" src/`
    # turning up nothing beyond this comment.


# ---------------------------------------------------------------------------
# Scoreboard (computed fresh from data/bet_log.csv every run)
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?")

_RESULT_CATEGORY = {
    "Won": "W",
    "Lost": "L",
    "Cashed Out": "CO",
}


def _parse_date_placed(raw):
    """Extract a leading YYYY-MM-DD (and HH:MM if present) from a messy
    date_placed value like '2026-09-10 (approx)'. Returns (date_str, time_str)
    where either may be None if unparseable/absent."""
    raw = (raw or "").strip()
    m = _DATE_RE.match(raw)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def compute_scoreboard(csv_path, since=None, starting_bankroll=50.00):
    """Parse data/bet_log.csv and return the scoreboard dict.

    Handles real messiness without crashing: a row with no
    bet_type/legs/odds/result at all is counted as a no_data_rows and
    skipped from every other count; a net_cash value that doesn't parse as
    a float is simply not added to cash_pl rather than raising.

    since: an inclusive "YYYY-MM-DD" lower bound on a row's parsed leading
    date_placed. None (the default) means no filter — every row is
    considered, exactly as before this parameter existed. When since is
    given, a row is only counted (in wins/cashouts/losses/open/
    no_data_rows/cash_pl/streak) if its parsed date is >= since; a row
    whose date_placed doesn't parse at all is EXCLUDED under a since filter
    (we can't confirm it falls in-season) but is still counted normally
    when since is None.
    """
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    wins = cashouts = losses = open_count = no_data_rows = 0
    cash_pl = 0.0
    settled = []  # list of (sort_key, category) for the streak calculation

    for idx, row in enumerate(rows):
        date_str, time_str = _parse_date_placed(row.get("date_placed"))

        if since is not None and (date_str is None or date_str < since):
            # Out of scope for a season-scoped call (or unconfirmable) —
            # excluded from every count, including no_data_rows.
            continue

        bet_type = (row.get("bet_type") or "").strip()
        legs_field = (row.get("legs") or "").strip()
        odds_listed = (row.get("odds_listed") or "").strip()
        result = (row.get("result") or "").strip()

        if not bet_type and not legs_field and not odds_listed and not result:
            # A row with no bet_type/legs/odds/result at all — just a date
            # and a note. Skip it entirely from every count rather than
            # erroring.
            no_data_rows += 1
            continue

        # Rows with a known date/time sort by that. Rows on the same date
        # with no reliable time fall back to original file order via idx,
        # and sort after same-date rows whose time IS known.
        sort_key = (date_str or "9999-99-99", time_str or "99:99", idx)

        net_cash_raw = (row.get("net_cash") or "").strip()
        try:
            cash_pl += float(net_cash_raw)
        except ValueError:
            pass  # blank (Open bets) or otherwise unparseable — just skip

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
            # An otherwise-populated row with an unrecognized result string.
            # Don't crash on messy/unexpected data — just don't count it
            # anywhere rather than guessing.
            no_data_rows += 1

        if category is not None:
            settled.append((sort_key, category))

    settled.sort(key=lambda t: t[0])

    current_streak = "—"  # em dash, no settled bets yet
    if settled:
        last_category = settled[-1][1]
        streak_len = 0
        for _, category in reversed(settled):
            if category != last_category:
                break
            streak_len += 1
        current_streak = f"{last_category}{streak_len}"

    bankroll_remaining = starting_bankroll + cash_pl  # never floored at zero, on purpose

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
    """Scan every row of bet_log.csv (regardless of any season scoping) for
    the earliest parseable date_placed, and return it formatted as
    abbreviated-month + 4-digit-year (e.g. 'Mar 2026'). Returns None if no
    row has a parseable date. Used for the all-time scoreboard line's label
    so it never goes stale if older history is appended later — it's
    derived from the data, not a hardcoded string."""
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


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------

def _format_month_year(date_str):
    """'2026-03-21' -> 'Mar 2026'."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%b')} {dt.year}"


def _format_human_date(date_str):
    """'2026-09-01' -> 'Sep 1, 2026'. Avoids the non-portable %-d/%#d strftime
    directives by building the string manually."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def format_odds(o):
    """American odds with an explicit sign, e.g. -150 -> '-150', 150 -> '+150'."""
    return f"{int(o):+d}"


def format_money(v):
    """Signed dollar string, e.g. -5.93 -> '-$5.93', 44.07 -> '$44.07'."""
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


# ---------------------------------------------------------------------------
# Rendering — one function per template section, each returning an HTML
# fragment for its {{TOKEN}} per the contract documented at the top of
# templates/page.html.
# ---------------------------------------------------------------------------

def render_scoreboard(season_sb, alltime_sb, season_start, alltime_since_label, card_stake_total=None):
    """Render the {{SCOREBOARD}} fragment: primary tiles scoped to the
    current season (season_sb), preceded by a heading naming the season
    start, followed by one compact supplementary all-time line (alltime_sb)
    labeled with alltime_since_label — a single secondary line, not a
    second tile grid.

    card_stake_total, when given, is the stake total of the card currently
    being rendered (not a bet_log.csv figure) — it renders a "Weekly Budget
    Used" tile so a one-off edition built mid-week (e.g. a single Monday
    Night Football card) can show how much of the $5/week cap it accounts
    for, distinct from any other edition already built/approved this same
    week. Omit or pass None to skip the tile (e.g. a full weekly edition
    where the card total is obviously the whole week's spend already)."""

    def stat(label, value, cls=""):
        cls_attr = f" {cls}" if cls else ""
        return (
            f'<div class="stat"><span class="label">{escape(label)}</span>'
            f'<span class="value{cls_attr}">{escape(str(value))}</span></div>'
        )

    pl_cls = "positive" if season_sb["cash_pl"] >= 0 else "negative"
    bankroll_cls = "positive" if season_sb["bankroll_remaining"] >= 0 else "negative"

    heading = f'<h3>This Season (since {escape(_format_human_date(season_start))})</h3>'

    tiles = [
        stat(
            "Record",
            f'{season_sb["wins"]}W-{season_sb["losses"]}L-{season_sb["cashouts"]}CO-{season_sb["open"]}Open',
        ),
        stat("Current Streak", season_sb["current_streak"]),
        stat("Cash P/L", format_money(season_sb["cash_pl"]), pl_cls),
        stat("Bankroll Remaining", format_money(season_sb["bankroll_remaining"]), bankroll_cls),
    ]
    if card_stake_total is not None:
        remaining = WEEKLY_BUDGET_CAP - card_stake_total
        tiles.append(stat(
            "Weekly Budget Used",
            f'{format_money(card_stake_total)} of {format_money(WEEKLY_BUDGET_CAP)} '
            f'({format_money(remaining)} left)',
        ))
    if season_sb.get("no_data_rows"):
        tiles.append(stat("Bets Missing Details", season_sb["no_data_rows"]))

    # .stat-grid is a 2-column CSS grid (already defined in templates/page.html)
    # so the tiles fit on one phone screen instead of stacking full-width.
    grid = f'<div class="stat-grid">{"".join(tiles)}</div>'

    alltime_label = alltime_since_label or "—"
    alltime_line = (
        '<div class="small muted">'
        f'All-time since {escape(alltime_label)}: '
        f'{alltime_sb["wins"]}W-{alltime_sb["losses"]}L-{alltime_sb["cashouts"]}CO-{alltime_sb["open"]}Open, '
        f'cash P/L {escape(format_money(alltime_sb["cash_pl"]))}'
        '</div>'
    )

    return heading + grid + alltime_line


def _leg_list_html(legs):
    items = "".join(
        f'<li>{escape(leg.get("selection", ""))} '
        f'<span class="prob small muted">(est. '
        f'{odds.format_prob(leg.get("estimated_prob", 0.0))})</span></li>'
        for leg in legs
    )
    return f'<ul class="leg-list">{items}</ul>'


def _reason_detail_html(bet):
    """The collapsible tail every tier shares: full reasoning + prob source
    + verify line, behind a native <details> so it's zero-JS and defaults
    to closed — "scan it, not read it" is the always-visible summary line;
    this is for whoever wants to check the work."""
    reason = bet.get("reason", "")
    source = bet.get("estimated_prob_source", "")
    verify = bet.get("verify") or {}

    source_bit = (
        f'<div class="small muted">Prob. source: {escape(source)}</div>'
        if source else ""
    )
    verify_bits = []
    if verify.get("source"):
        verify_bits.append(f"Verify: {escape(verify['source'])}")
    if verify.get("fetched_at"):
        verify_bits.append(escape(verify["fetched_at"]))
    if verify.get("note"):
        verify_bits.append(escape(verify["note"]))
    verify_line = " &middot; ".join(verify_bits)

    return f'''<details class="reason-detail">
    <summary>Why &amp; sources</summary>
    <p class="reason">{escape(reason)}</p>
    {source_bit}
    <div class="verify">{verify_line}</div>
  </details>'''


def render_easy_bet(bet):
    stake = float(bet.get("stake", 0.0))
    dk_odds = bet.get("dk_odds")
    market = bet.get("market", "")
    title = escape(bet.get("selection", ""))
    est_prob = bet.get("estimated_prob", 0.0)

    implied = odds.american_to_implied_prob(dk_odds)
    edge_val = odds.edge(est_prob, implied)
    edge_class = "edge-positive" if edge_val >= 0 else "edge-negative"
    market_bit = f" &middot; {escape(market)}" if market else ""

    return f'''<div class="bet tier-easy">
  <div class="tier-badge">1. Easy Bet</div>
  <div class="bet-header">
    <h3>{title}</h3>
    <span class="odds mono">{format_odds(dk_odds)}</span>
  </div>
  <div class="stake-line">Stake: <span class="stake mono">${stake:.2f}</span>{market_bit}</div>
  <div class="prob-row">
    <span><span class="k">Implied:</span> <span class="prob mono">{odds.format_prob(implied)}</span></span>
    <span><span class="k">Estimated:</span> <span class="prob mono">{odds.format_prob(est_prob)}</span></span>
    <span><span class="k">Edge:</span> <span class="prob mono {edge_class}">{odds.format_prob(edge_val)}</span></span>
  </div>
  <p class="reason-summary">{escape(bet.get("reason_summary", ""))}</p>
  {_reason_detail_html(bet)}
</div>'''


def render_fun_parlay(bet):
    stake = float(bet.get("stake", 0.0))
    dk_odds = bet.get("dk_odds")
    legs = bet.get("legs", []) or []
    est_prob = odds.parlay_implied_prob([leg.get("estimated_prob", 0.0) for leg in legs])
    implied = odds.american_to_implied_prob(dk_odds)
    edge_val = odds.edge(est_prob, implied)
    edge_class = "edge-positive" if edge_val >= 0 else "edge-negative"

    return f'''<div class="bet tier-fun">
  <div class="tier-badge">2. Fun Parlay</div>
  <div class="bet-header">
    <h3>Parlay &mdash; {len(legs)} legs</h3>
    <span class="odds mono">{format_odds(dk_odds)}</span>
  </div>
  <div class="stake-line">Stake: <span class="stake mono">${stake:.2f}</span></div>
  <div class="prob-row">
    <span><span class="k">Implied:</span> <span class="prob mono">{odds.format_prob(implied)}</span></span>
    <span><span class="k">Estimated:</span> <span class="prob mono">{odds.format_prob(est_prob)}</span></span>
    <span><span class="k">Edge:</span> <span class="prob mono {edge_class}">{odds.format_prob(edge_val)}</span></span>
  </div>
  {_leg_list_html(legs)}
  <p class="reason-summary">{escape(bet.get("reason_summary", ""))}</p>
  {_reason_detail_html(bet)}
</div>'''


def render_lottery_ticket(bet):
    """Every leg's real probability is shown honestly (via the shared
    combined-probability + "1 in X" pair) rather than an Implied/Estimated/
    Edge framing, which doesn't mean much for a for-fun long shot. Stake is
    always exactly LOTTERY_TICKET_STAKE and is called out as outside the
    $5/week line, per Gus."""
    stake = float(bet.get("stake", 0.0))
    dk_odds = bet.get("dk_odds")
    legs = bet.get("legs", []) or []
    combined_prob = odds.parlay_implied_prob([leg.get("estimated_prob", 0.0) for leg in legs])
    payout_implied = odds.american_to_implied_prob(dk_odds)

    return f'''<div class="bet tier-lottery">
  <div class="tier-badge">3. Lottery Ticket</div>
  <div class="bet-header">
    <h3>Parlay &mdash; {len(legs)} legs</h3>
    <span class="odds mono">{format_odds(dk_odds)}</span>
  </div>
  <div class="stake-line">Stake: <span class="stake mono">${stake:.2f}</span> <span class="small muted">(outside the $5/week line)</span></div>
  <div class="prob-row">
    <span><span class="k">Payout-implied:</span> <span class="prob mono">{odds.format_prob(payout_implied)}</span></span>
    <span><span class="k">Real combined prob.:</span> <span class="prob mono">{odds.format_prob(combined_prob)}</span></span>
    <span><span class="k">Honest odds:</span> <span class="prob mono">{odds.format_one_in_x(combined_prob)}</span></span>
  </div>
  {_leg_list_html(legs)}
  <p class="reason-summary">{escape(bet.get("reason_summary", ""))}</p>
  {_reason_detail_html(bet)}
</div>'''


def sort_leg_bank(leg_bank):
    """Leg Bank entries ranked by estimated probability, highest first."""
    return sorted(leg_bank, key=lambda leg: leg.get("estimated_prob", 0.0), reverse=True)


def render_leg_bank_entry(leg):
    dk_odds = leg.get("dk_odds")
    implied = odds.american_to_implied_prob(dk_odds)
    est_prob = leg.get("estimated_prob", 0.0)
    edge_val = odds.edge(est_prob, implied)
    edge_class = "edge-positive" if edge_val >= 0 else "edge-negative"

    return f'''<div class="leg">
  <div class="leg-header">
    <span class="selection">{escape(leg.get("selection", ""))}</span>
    <span class="odds mono">{format_odds(dk_odds)}</span>
  </div>
  <div class="meta">
    <span>{escape(leg.get("market", ""))}</span>
    <span><span class="k">Implied:</span> <span class="prob mono">{odds.format_prob(implied)}</span></span>
    <span><span class="k">Est.:</span> <span class="prob mono">{odds.format_prob(est_prob)}</span></span>
    <span><span class="k">Edge:</span> <span class="prob mono {edge_class}">{odds.format_prob(edge_val)}</span></span>
  </div>
  <p class="small">{escape(leg.get("reason", ""))}</p>
</div>'''


def render_boost(item):
    fits = bool(item.get("fits_card", False))
    cls = "fits" if fits else "no-fit"
    verdict = "Fits card" if fits else "No fit"
    return f'''<div class="boost-item {cls}">
  <div class="verdict">{verdict}</div>
  <p>{escape(item.get("boost_description", ""))}</p>
  <p class="small muted">{escape(item.get("explanation", ""))}</p>
</div>'''


def render_avoid(item):
    return f'''<div class="avoid-item">
  <h3>{escape(item.get("selection", ""))}</h3>
  <p class="why-tempting"><span class="k">Tempting:</span> {escape(item.get("why_tempting", ""))}</p>
  <p class="why-avoid"><span class="k">Avoid:</span> {escape(item.get("why_avoid", ""))}</p>
</div>'''


_GRADED_RESULT_CLASS = {
    "Won": "result-won",
    "Lost": "result-lost",
    "Cashed Out": "result-cashed-out",
    "Open": "result-open",
}


def render_graded(item):
    result = item.get("result", "")
    cls = _GRADED_RESULT_CLASS.get(result, "result-open")
    return f'''<div class="graded-item {cls}">
  <h3>{escape(item.get("selection", ""))}</h3>
  <div class="result">{escape(result)}</div>
  <p class="lesson">{escape(item.get("lesson", ""))}</p>
</div>'''


def render_card_section(card):
    """Render the {{CARD}} fragment: the three tiers, in order, skipping any
    that are absent (only Easy Bet is required)."""
    parts = []
    if card.get("easy_bet"):
        parts.append(render_easy_bet(card["easy_bet"]))
    if card.get("fun_parlay"):
        parts.append(render_fun_parlay(card["fun_parlay"]))
    if card.get("lottery_ticket"):
        parts.append(render_lottery_ticket(card["lottery_ticket"]))
    return "".join(parts)


def render_page(week, season_sb, alltime_sb, season_start, alltime_since_label, template_path):
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    sample_banner = ""
    if week.get("is_sample"):
        sample_banner = (
            '<div class="sample-banner">SAMPLE DATA &mdash; schema test only, '
            "not a real week. Do not place any bets from this page.</div>"
        )

    # "Weekly Budget Used" always reflects Easy Bet + Fun Parlay only — the
    # Lottery Ticket's flat $0.50 is deliberately outside the $5/week line
    # (it's shown, separately labeled as such, on its own tier card instead)
    # so it never crowds out tiers 1-2, per Gus. None if there's no card at
    # all (defensive — every real/sample week file has one).
    card = week.get("card") or {}
    card_stake_total = None
    if card.get("easy_bet"):
        card_stake_total = float(card["easy_bet"].get("stake", 0.0))
        if card.get("fun_parlay"):
            card_stake_total += float(card["fun_parlay"].get("stake", 0.0))

    replacements = {
        "{{SAMPLE_BANNER}}": sample_banner,
        "{{SCOREBOARD}}": render_scoreboard(
            season_sb, alltime_sb, season_start, alltime_since_label, card_stake_total
        ),
        "{{CARD}}": render_card_section(card),
        "{{LEG_BANK}}": "".join(render_leg_bank_entry(l) for l in sort_leg_bank(week.get("leg_bank", []))),
        "{{BOOST_CHECK}}": "".join(render_boost(b) for b in week.get("boost_check", [])),
        "{{AVOID_LIST}}": "".join(render_avoid(a) for a in week.get("avoid_list", [])),
        "{{LAST_WEEK_GRADED}}": "".join(render_graded(g) for g in week.get("last_week_graded", [])),
        "{{BUILT_AT}}": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

    for token, value in replacements.items():
        template = template.replace(token, value)
    return template


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_default_week_file(weeks_dir: Path) -> Path:
    json_files = sorted(weeks_dir.glob("*.json"))
    if not json_files:
        raise FileNotFoundError(f"No week JSON files found in {weeks_dir}")
    return json_files[-1]


def main(argv):
    repo_root = Path(__file__).resolve().parent.parent
    csv_path = repo_root / "data" / "bet_log.csv"
    weeks_dir = repo_root / "data" / "weeks"
    config_path = repo_root / "data" / "config.json"
    template_path = repo_root / "templates" / "page.html"
    output_path = repo_root / "docs" / "index.html"

    if len(argv) > 1:
        week_path = Path(argv[1])
        if not week_path.is_absolute():
            cwd_candidate = Path.cwd() / week_path
            week_path = cwd_candidate if cwd_candidate.exists() else (repo_root / week_path)
    else:
        week_path = find_default_week_file(weeks_dir)

    with open(week_path, "r", encoding="utf-8") as f:
        week = json.load(f)

    config = load_config(config_path)

    # Scoreboard is computed twice: season_sb (the number Gus actually acts
    # on — "bankroll remaining" scoped to the current football season) and
    # alltime_sb (full history back to March 2026, kept as context, not
    # discarded — see CLAUDE.md / plan). Both use the same starting_bankroll
    # so "bankroll remaining" means the same $ baseline in either view.
    season_sb = compute_scoreboard(
        csv_path, since=config["season_start"], starting_bankroll=config["starting_bankroll"]
    )
    alltime_sb = compute_scoreboard(
        csv_path, since=None, starting_bankroll=config["starting_bankroll"]
    )
    alltime_since_label = find_earliest_date_label(csv_path)

    # If this raises RuleViolation, it must propagate uncaught: that's the
    # actual safety mechanism for a page giving real-money advice. Do not
    # wrap this in a try/except that renders a degraded page anyway.
    validate_rules(week)

    html = render_page(
        week, season_sb, alltime_sb, config["season_start"], alltime_since_label, template_path
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Week file: {week_path}")
    print(f"Season scoreboard (since {config['season_start']}): {season_sb}")
    print(f"All-time scoreboard (since {alltime_since_label}): {alltime_sb}")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main(sys.argv)
