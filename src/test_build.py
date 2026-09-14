import unittest
from pathlib import Path

from build import (
    RuleViolation,
    compute_scoreboard,
    find_earliest_date_label,
    load_config,
    render_scoreboard,
    sort_leg_bank,
    validate_rules,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = REPO_ROOT / "data" / "bet_log.csv"
CONFIG_PATH = REPO_ROOT / "data" / "config.json"


def _valid_week():
    """A minimal week dict that passes every rule, for tests to mutate."""
    return {
        "card": {
            "bets": [
                {
                    "id": "s1",
                    "kind": "straight",
                    "selection": "Team A ML",
                    "market": "moneyline",
                    "dk_odds": -150,
                    "estimated_prob": 0.62,
                    "is_pre_kickoff": True,
                    "stake": 3.00,
                    "reason": "test straight bet",
                },
                {
                    "id": "p1",
                    "kind": "parlay",
                    "legs": [
                        {"selection": "Leg 1", "estimated_prob": 0.65},
                        {"selection": "Leg 2", "estimated_prob": 0.70},
                    ],
                    "dk_odds": 150,
                    "is_pre_kickoff": True,
                    "stake": 2.00,
                    "reason": "test parlay",
                },
            ]
        }
    }


class TestRule1Budget(unittest.TestCase):
    def test_over_budget_card_raises(self):
        week = _valid_week()
        # 4.00 + 2.00 = 6.00, over the $5 weekly budget
        week["card"]["bets"][0]["stake"] = 4.00
        with self.assertRaises(RuleViolation):
            validate_rules(week)


class TestRule2PreKickoff(unittest.TestCase):
    def test_non_pre_kickoff_bet_raises(self):
        week = _valid_week()
        week["card"]["bets"][0]["is_pre_kickoff"] = False
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_live_substring_in_market_raises(self):
        week = _valid_week()
        week["card"]["bets"][0]["market"] = "live moneyline"
        with self.assertRaises(RuleViolation):
            validate_rules(week)


class TestRule6StraightIsDefault(unittest.TestCase):
    def test_two_straights_one_parlay_raises(self):
        week = _valid_week()
        extra_straight = dict(week["card"]["bets"][0])
        extra_straight["id"] = "s2"
        week["card"]["bets"].append(extra_straight)
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_one_straight_zero_parlay_is_valid(self):
        # "One straight bet plus AT MOST ONE small parlay" — a card with
        # exactly 1 straight and 0 parlays must be VALID, not rejected.
        week = _valid_week()
        week["card"]["bets"] = [week["card"]["bets"][0]]  # drop the parlay
        validate_rules(week)  # should not raise


class TestRule4LegCountFollowsProbability(unittest.TestCase):
    def test_four_leg_parlay_with_coin_flip_leg_raises(self):
        week = _valid_week()
        week["card"]["bets"][1]["legs"] = [
            {"selection": "Leg 1", "estimated_prob": 0.65},
            {"selection": "Leg 2", "estimated_prob": 0.70},
            {"selection": "Leg 3", "estimated_prob": 0.61},
            {"selection": "Leg 4", "estimated_prob": 0.50},
        ]
        with self.assertRaises(RuleViolation):
            validate_rules(week)

    def test_three_leg_parlay_with_coin_flip_leg_is_fine(self):
        # Rule 4 only kicks in above 3 legs — a 3-leg parlay with a 0.50
        # leg is exactly what the "cap at 3" language in the plan allows.
        week = _valid_week()
        week["card"]["bets"][1]["legs"] = [
            {"selection": "Leg 1", "estimated_prob": 0.65},
            {"selection": "Leg 2", "estimated_prob": 0.70},
            {"selection": "Leg 3", "estimated_prob": 0.50},
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


if __name__ == "__main__":
    unittest.main()
