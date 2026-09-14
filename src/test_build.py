import unittest
from pathlib import Path

from build import (
    RuleViolation,
    compute_scoreboard,
    find_earliest_date_label,
    load_config,
    render_card_section,
    render_easy_bet,
    render_fun_parlay,
    render_lottery_ticket,
    render_scoreboard,
    sort_leg_bank,
    validate_rules,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = REPO_ROOT / "data" / "bet_log.csv"
CONFIG_PATH = REPO_ROOT / "data" / "config.json"


def _valid_easy_bet():
    return {
        "id": "easy-bet",
        "selection": "Team A ML",
        "market": "moneyline",
        "dk_odds": -150,
        "estimated_prob": 0.62,
        "is_pre_kickoff": True,
        "stake": 3.00,
        "reason_summary": "test easy bet",
        "reason": "test easy bet, full reasoning",
    }


def _valid_fun_parlay():
    return {
        "id": "fun-parlay",
        "legs": [
            {"selection": "Leg 1", "estimated_prob": 0.65},
            {"selection": "Leg 2", "estimated_prob": 0.70},
        ],
        "dk_odds": 150,
        "is_pre_kickoff": True,
        "stake": 2.00,
        "reason_summary": "test fun parlay",
        "reason": "test fun parlay, full reasoning",
    }


def _valid_lottery_ticket(n_legs=12):
    return {
        "id": "lottery-ticket",
        "legs": [
            {"selection": f"Leg {i}", "estimated_prob": 0.5} for i in range(n_legs)
        ],
        "dk_odds": 50000,
        "is_pre_kickoff": True,
        "stake": 0.50,
        "reason_summary": "test lottery ticket",
        "reason": "test lottery ticket, full reasoning",
    }


def _valid_week(include_fun_parlay=True, include_lottery_ticket=False):
    """A minimal week dict that passes every rule, for tests to mutate."""
    card = {"easy_bet": _valid_easy_bet()}
    if include_fun_parlay:
        card["fun_parlay"] = _valid_fun_parlay()
    if include_lottery_ticket:
        card["lottery_ticket"] = _valid_lottery_ticket()
    return {"card": card}


class TestRule1Budget(unittest.TestCase):
    def test_over_budget_card_raises(self):
        week = _valid_week()
        # 4.00 + 2.00 = 6.00, over the $5 weekly budget
        week["card"]["easy_bet"]["stake"] = 4.00
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_lottery_ticket_stake_excluded_from_5_dollar_line(self):
        # Easy Bet $3 + Fun Parlay $2 = exactly $5; the Lottery Ticket's own
        # $0.50 must NOT push this over the cap — it's outside the line.
        week = _valid_week(include_lottery_ticket=True)
        validate_rules(week)  # should not raise


class TestRule2PreKickoff(unittest.TestCase):
    def test_non_pre_kickoff_easy_bet_raises(self):
        week = _valid_week()
        week["card"]["easy_bet"]["is_pre_kickoff"] = False
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_live_substring_in_market_raises(self):
        week = _valid_week()
        week["card"]["easy_bet"]["market"] = "live moneyline"
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_non_pre_kickoff_lottery_ticket_raises(self):
        # The Lottery Ticket is exempt from the leg-count rule and the $5
        # line, but NOT from pre-kickoff-only — that rule has no exceptions.
        week = _valid_week(include_lottery_ticket=True)
        week["card"]["lottery_ticket"]["is_pre_kickoff"] = False
        with self.assertRaises(RuleViolation):
            validate_rules(week)


class TestRule6TierStructure(unittest.TestCase):
    def test_missing_easy_bet_raises(self):
        week = {"card": {}}
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_easy_bet_with_legs_raises(self):
        # Easy Bet must be a single straight selection, not multi-leg.
        week = _valid_week(include_fun_parlay=False)
        week["card"]["easy_bet"]["legs"] = [{"selection": "x", "estimated_prob": 0.6}]
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_easy_bet_alone_is_valid(self):
        # Fun Parlay and Lottery Ticket are each optional.
        week = _valid_week(include_fun_parlay=False)
        validate_rules(week)  # should not raise

    def test_fun_parlay_with_one_leg_raises(self):
        week = _valid_week()
        week["card"]["fun_parlay"]["legs"] = [{"selection": "x", "estimated_prob": 0.6}]
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_fun_parlay_with_four_legs_raises(self):
        week = _valid_week()
        week["card"]["fun_parlay"]["legs"] = [
            {"selection": f"Leg {i}", "estimated_prob": 0.6} for i in range(4)
        ]
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_fun_parlay_leg_below_55_percent_raises(self):
        week = _valid_week()
        week["card"]["fun_parlay"]["legs"][0]["estimated_prob"] = 0.54
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_fun_parlay_leg_at_exactly_55_percent_is_fine(self):
        week = _valid_week()
        week["card"]["fun_parlay"]["legs"][0]["estimated_prob"] = 0.55
        validate_rules(week)  # should not raise

    def test_lottery_ticket_below_10_legs_raises(self):
        week = _valid_week(include_lottery_ticket=True)
        week["card"]["lottery_ticket"] = _valid_lottery_ticket(n_legs=9)
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_lottery_ticket_above_20_legs_raises(self):
        week = _valid_week(include_lottery_ticket=True)
        week["card"]["lottery_ticket"] = _valid_lottery_ticket(n_legs=21)
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_lottery_ticket_wrong_stake_raises(self):
        week = _valid_week(include_lottery_ticket=True)
        week["card"]["lottery_ticket"]["stake"] = 1.00
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_lottery_ticket_is_exempt_from_any_leg_probability_floor(self):
        # This is the whole point of the tier — real long-shot legs, no
        # 55%/60% floor like Fun Parlay or the old Rule 4 would require.
        week = _valid_week(include_lottery_ticket=True)
        week["card"]["lottery_ticket"]["legs"] = [
            {"selection": f"Leg {i}", "estimated_prob": 0.10} for i in range(14)
        ]
        validate_rules(week)  # should not raise


class TestScoreboardRealData(unittest.TestCase):
    def test_matches_known_real_totals(self):
        sb = compute_scoreboard(CSV_PATH)
        self.assertEqual(sb["wins"], 1)
        self.assertEqual(sb["cashouts"], 1)
        self.assertEqual(sb["losses"], 18)
        self.assertEqual(sb["open"], 2)
        self.assertEqual(sb["no_data_rows"], 1)
        self.assertAlmostEqual(sb["cash_pl"], -55.93, delta=0.02)
        self.assertAlmostEqual(sb["bankroll_remaining"], -5.93, delta=0.02)
        # loss streak should be in the low-to-mid teens per the plan's sanity check
        self.assertTrue(sb["current_streak"].startswith("L"))
        streak_len = int(sb["current_streak"][1:])
        self.assertGreaterEqual(streak_len, 10)
        self.assertLessEqual(streak_len, 16)


class TestScoreboardSeasonScoping(unittest.TestCase):
    def test_season_since_sep_1_2026(self):
        sb = compute_scoreboard(CSV_PATH, since="2026-09-01", starting_bankroll=50.00)
        self.assertEqual(sb["wins"], 0)
        self.assertEqual(sb["losses"], 7)
        self.assertEqual(sb["cashouts"], 0)
        self.assertEqual(sb["open"], 2)
        self.assertEqual(sb["no_data_rows"], 1)
        self.assertAlmostEqual(sb["cash_pl"], -9.00, delta=0.01)
        self.assertAlmostEqual(sb["bankroll_remaining"], 41.00, delta=0.01)
        self.assertEqual(sb["current_streak"], "L7")

    def test_alltime_unchanged_by_default_starting_bankroll(self):
        # since=None must reproduce the exact pre-patch all-time numbers —
        # this scoreboard must NOT change just because season scoping exists.
        sb = compute_scoreboard(CSV_PATH, since=None, starting_bankroll=50.00)
        self.assertEqual(sb["wins"], 1)
        self.assertEqual(sb["losses"], 18)
        self.assertEqual(sb["cashouts"], 1)
        self.assertEqual(sb["open"], 2)
        self.assertAlmostEqual(sb["cash_pl"], -55.93, delta=0.02)

    def test_since_none_ignores_starting_bankroll_change_correctly(self):
        # starting_bankroll only shifts bankroll_remaining, never cash_pl.
        sb1 = compute_scoreboard(CSV_PATH, since=None, starting_bankroll=50.00)
        sb2 = compute_scoreboard(CSV_PATH, since=None, starting_bankroll=100.00)
        self.assertAlmostEqual(sb1["cash_pl"], sb2["cash_pl"], delta=1e-9)
        self.assertAlmostEqual(sb2["bankroll_remaining"] - sb1["bankroll_remaining"], 50.00, delta=1e-9)


class TestFindEarliestDateLabel(unittest.TestCase):
    def test_matches_march_2026(self):
        # The real log's earliest row is 2026-03-21 (NCAAB) — this must read
        # as "Mar 2026" today, but is derived from the data, not hardcoded.
        self.assertEqual(find_earliest_date_label(CSV_PATH), "Mar 2026")


class TestLoadConfig(unittest.TestCase):
    def test_real_config_file(self):
        config = load_config(CONFIG_PATH)
        self.assertEqual(config["season_start"], "2026-09-01")
        self.assertAlmostEqual(config["starting_bankroll"], 50.00, delta=1e-9)

    def test_missing_file_falls_back_to_defaults(self):
        config = load_config(REPO_ROOT / "data" / "does_not_exist.json")
        self.assertEqual(config["season_start"], "2026-09-01")
        self.assertAlmostEqual(config["starting_bankroll"], 50.00, delta=1e-9)


class TestSortLegBank(unittest.TestCase):
    def test_sorts_descending_by_estimated_prob(self):
        legs = [
            {"selection": "A", "estimated_prob": 0.55},
            {"selection": "B", "estimated_prob": 0.72},
            {"selection": "C", "estimated_prob": 0.61},
        ]
        ranked = sort_leg_bank(legs)
        self.assertEqual([l["selection"] for l in ranked], ["B", "C", "A"])
        self.assertEqual(
            [l["estimated_prob"] for l in ranked],
            sorted((l["estimated_prob"] for l in legs), reverse=True),
        )

    def test_does_not_mutate_input_list(self):
        legs = [
            {"selection": "A", "estimated_prob": 0.4},
            {"selection": "B", "estimated_prob": 0.9},
        ]
        original_order = [l["selection"] for l in legs]
        sort_leg_bank(legs)
        self.assertEqual([l["selection"] for l in legs], original_order)

    def test_missing_estimated_prob_sorts_last(self):
        legs = [
            {"selection": "no-prob"},
            {"selection": "has-prob", "estimated_prob": 0.5},
        ]
        ranked = sort_leg_bank(legs)
        self.assertEqual([l["selection"] for l in ranked], ["has-prob", "no-prob"])

    def test_empty_list(self):
        self.assertEqual(sort_leg_bank([]), [])


_MINIMAL_SEASON_SB = {
    "wins": 0, "losses": 0, "cashouts": 0, "open": 0, "no_data_rows": 0,
    "cash_pl": 0.0, "bankroll_remaining": 50.0, "current_streak": "—",
}
_MINIMAL_ALLTIME_SB = dict(_MINIMAL_SEASON_SB)


class TestRenderScoreboardBudgetTile(unittest.TestCase):
    def test_omitted_by_default(self):
        html = render_scoreboard(_MINIMAL_SEASON_SB, _MINIMAL_ALLTIME_SB, "2026-09-01", "Mar 2026")
        self.assertNotIn("Weekly Budget Used", html)

    def test_shown_when_card_stake_total_given(self):
        html = render_scoreboard(
            _MINIMAL_SEASON_SB, _MINIMAL_ALLTIME_SB, "2026-09-01", "Mar 2026",
            card_stake_total=2.00,
        )
        self.assertIn("Weekly Budget Used", html)
        self.assertIn("$2.00", html)
        self.assertIn("$5.00", html)
        self.assertIn("$3.00", html)  # remaining


class TestRenderTiers(unittest.TestCase):
    def test_easy_bet_shows_summary_not_full_reason_by_default(self):
        html = render_easy_bet(_valid_easy_bet())
        self.assertIn("1. Easy Bet", html)
        self.assertIn("test easy bet</p>", html)  # reason_summary, visible
        self.assertIn("<details", html)
        self.assertIn("test easy bet, full reasoning", html)  # still present, just collapsed
        self.assertIn("<summary>", html)

    def test_fun_parlay_tier_badge_and_legs(self):
        html = render_fun_parlay(_valid_fun_parlay())
        self.assertIn("2. Fun Parlay", html)
        self.assertIn("Leg 1", html)
        self.assertIn("Leg 2", html)

    def test_lottery_ticket_shows_combined_probability_and_one_in_x(self):
        # 12 legs at 0.5 each -> combined = 0.5**12 ≈ 0.000244 -> ~1 in 4096
        ticket = _valid_lottery_ticket(n_legs=12)
        html = render_lottery_ticket(ticket)
        self.assertIn("3. Lottery Ticket", html)
        self.assertIn("outside the $5/week line", html)
        self.assertIn("Real combined prob.", html)
        self.assertIn("Honest odds", html)
        self.assertIn("~1 in 4,096", html)

    def test_card_section_orders_tiers_and_skips_absent_ones(self):
        card = {"easy_bet": _valid_easy_bet()}
        html = render_card_section(card)
        self.assertIn("1. Easy Bet", html)
        self.assertNotIn("2. Fun Parlay", html)
        self.assertNotIn("3. Lottery Ticket", html)

        card["fun_parlay"] = _valid_fun_parlay()
        card["lottery_ticket"] = _valid_lottery_ticket()
        html = render_card_section(card)
        easy_pos = html.index("1. Easy Bet")
        fun_pos = html.index("2. Fun Parlay")
        lottery_pos = html.index("3. Lottery Ticket")
        self.assertLess(easy_pos, fun_pos)
        self.assertLess(fun_pos, lottery_pos)


if __name__ == "__main__":
    unittest.main()
