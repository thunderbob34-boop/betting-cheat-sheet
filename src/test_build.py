import unittest
from pathlib import Path

from build import RuleViolation, compute_scoreboard, validate_rules

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = REPO_ROOT / "data" / "bet_log.csv"


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


if __name__ == "__main__":
    unittest.main()
