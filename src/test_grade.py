import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import grade


def _write_slot(weekends_dir, weekend_id, slot, data):
    d = Path(weekends_dir) / weekend_id
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{slot}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


class TestComputeNet(unittest.TestCase):
    def test_won_negative_110_one_dollar(self):
        self.assertAlmostEqual(grade.compute_net("Won", 1.00, -110), 0.91, delta=1e-9)

    def test_won_positive_150_two_dollars(self):
        self.assertAlmostEqual(grade.compute_net("Won", 2.00, 150), 3.00, delta=1e-9)

    def test_lost(self):
        self.assertAlmostEqual(grade.compute_net("Lost", 2.25, -216), -2.25, delta=1e-9)

    def test_push(self):
        self.assertEqual(grade.compute_net("Push", 5.00, -110), 0.0)

    def test_void(self):
        self.assertEqual(grade.compute_net("Void", 5.00, -110), 0.0)

    def test_unknown_result_raises(self):
        with self.assertRaises(grade.GradeError):
            grade.compute_net("Cancelled", 1.00, -110)


class TestSetResult(unittest.TestCase):
    def test_sets_result_and_net(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "sat", {
                "weekend_id": "2026-09-24",
                "slot": "sat",
                "card": {"easy_bet": {"stake": 1.00, "dk_odds": -110, "selection": "Team A ML"}},
            })
            bet = grade.set_result("2026-09-24", "sat", "easy_bet", "Won", weekends_dir=td)
            self.assertEqual(bet["result"], "Won")
            self.assertAlmostEqual(bet["net"], 0.91, delta=1e-9)

    def test_preserves_other_fields_and_formatting(self):
        with tempfile.TemporaryDirectory() as td:
            original = {
                "weekend_id": "2026-09-24",
                "slot": "sat",
                "date": "2026-09-26",
                "angles": ["some angle"],
                "card": {
                    "easy_bet": {
                        "stake": 2.25,
                        "dk_odds": -216,
                        "selection": "MarShawn Lloyd 25+ rush+rec yds",
                        "reason": "keep me",
                    },
                    "fun_parlay": {"stake": 1.00, "dk_odds": 150, "legs": [{"selection": "x", "estimated_prob": 0.6}]},
                },
                "leg_bank": [{"selection": "untouched leg"}],
            }
            _write_slot(td, "2026-09-24", "sat", original)
            grade.set_result("2026-09-24", "sat", "easy_bet", "Lost", weekends_dir=td)

            path = Path(td) / "2026-09-24" / "sat.json"
            raw = path.read_text(encoding="utf-8")
            self.assertTrue(raw.endswith("\n"))
            data = json.loads(raw)
            self.assertEqual(data["date"], "2026-09-26")
            self.assertEqual(data["angles"], ["some angle"])
            self.assertEqual(data["leg_bank"], [{"selection": "untouched leg"}])
            self.assertEqual(data["card"]["easy_bet"]["reason"], "keep me")
            self.assertEqual(data["card"]["easy_bet"]["result"], "Lost")
            self.assertAlmostEqual(data["card"]["easy_bet"]["net"], -2.25, delta=1e-9)
            # fun_parlay untouched -- no result field added to a tier we
            # didn't grade
            self.assertNotIn("result", data["card"]["fun_parlay"])

    def test_unknown_tier_raises(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "sat", {"card": {"easy_bet": {"stake": 1.0, "dk_odds": -110}}})
            with self.assertRaises(grade.GradeError):
                grade.set_result("2026-09-24", "sat", "not_a_tier", "Won", weekends_dir=td)

    def test_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(grade.GradeError):
                grade.set_result("2026-09-24", "sat", "easy_bet", "Won", weekends_dir=td)

    def test_never_touches_bet_log_csv(self):
        # A bet_log.csv sitting right next to the weekends dir must survive
        # set_result/pending untouched -- grade.py only ever opens paths
        # under weekends_dir.
        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "bet_log.csv"
            csv_contents = "date_placed,result\n2026-01-01,Won\n"
            csv_path.write_text(csv_contents, encoding="utf-8")

            _write_slot(td, "2026-09-24", "sat", {
                "card": {"easy_bet": {"stake": 1.0, "dk_odds": -110}},
            })
            grade.set_result("2026-09-24", "sat", "easy_bet", "Won", weekends_dir=td)
            grade.pending(weekends_dir=td, now=datetime.now(timezone.utc))

            self.assertEqual(csv_path.read_text(encoding="utf-8"), csv_contents)


class TestComputeNetDecimalRounding(unittest.TestCase):
    def test_half_cent_boundary_rounds_up_not_down(self):
        # 0.10 * (2.05 - 1) = 0.105 exactly -- must round to $0.11, not the
        # $0.10 a naive binary-float computation produces.
        self.assertAlmostEqual(grade.compute_net("Won", 0.10, 105), 0.11, delta=1e-9)

    def test_second_half_cent_boundary_rounds_up(self):
        self.assertAlmostEqual(grade.compute_net("Won", 0.05, 230), 0.12, delta=1e-9)


class TestComputeNetInvalidOdds(unittest.TestCase):
    def test_won_with_zero_dk_odds_raises_grade_error(self):
        with self.assertRaises(grade.GradeError):
            grade.compute_net("Won", 1.00, 0)

    def test_won_with_missing_dk_odds_raises_grade_error(self):
        with self.assertRaises(grade.GradeError):
            grade.compute_net("Won", 1.00, None)


class TestSetResultRegradeGuard(unittest.TestCase):
    def test_regrading_an_already_graded_bet_raises(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "sat", {
                "card": {"easy_bet": {"stake": 1.00, "dk_odds": -110, "result": "Won", "net": 0.91}},
            })
            with self.assertRaises(grade.GradeError):
                grade.set_result("2026-09-24", "sat", "easy_bet", "Lost", weekends_dir=td)

    def test_grading_an_ungraded_bet_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "sat", {
                "card": {"easy_bet": {"stake": 1.00, "dk_odds": -110}},
            })
            bet = grade.set_result("2026-09-24", "sat", "easy_bet", "Won", weekends_dir=td)
            self.assertEqual(bet["result"], "Won")


class TestSetResultUnicodePreservation(unittest.TestCase):
    def test_unicode_in_untouched_field_survives_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as td:
            original = {
                "weekend_id": "2026-09-24", "slot": "sat",
                "card": {
                    "easy_bet": {"stake": 1.00, "dk_odds": -110, "reason": "keep me — as is"},
                    "fun_parlay": {"stake": 1.00, "dk_odds": 150, "legs": [{"selection": "x"}]},
                },
            }
            d = Path(td) / "2026-09-24"
            d.mkdir(parents=True)
            with open(d / "sat.json", "w", encoding="utf-8") as f:
                json.dump(original, f, indent=2, ensure_ascii=False)
                f.write("\n")
            grade.set_result("2026-09-24", "sat", "fun_parlay", "Won", weekends_dir=td)
            raw = (d / "sat.json").read_text(encoding="utf-8")
            self.assertIn("—", raw)
            self.assertNotIn("\\u2014", raw)


class TestMalformedJsonNamesPath(unittest.TestCase):
    def test_set_result_on_malformed_json_names_path(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "2026-09-24"
            d.mkdir(parents=True)
            bad_path = d / "sat.json"
            bad_path.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(grade.GradeError) as ctx:
                grade.set_result("2026-09-24", "sat", "easy_bet", "Won", weekends_dir=td)
            self.assertIn(str(bad_path), str(ctx.exception))

    def test_pending_on_malformed_json_names_path(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "2026-09-24"
            d.mkdir(parents=True)
            bad_path = d / "sat.json"
            bad_path.write_text("", encoding="utf-8")
            with self.assertRaises(grade.GradeError) as ctx:
                grade.pending(weekends_dir=td, now=datetime.now(timezone.utc))
            self.assertIn(str(bad_path), str(ctx.exception))


class TestPendingMissingKickoff(unittest.TestCase):
    def test_open_bet_with_no_kickoff_is_surfaced_not_dropped(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "sat", {
                "weekend_id": "2026-09-24", "slot": "sat",
                "card": {"easy_bet": {
                    "stake": 1.0, "dk_odds": -110, "selection": "Team A ML",
                    "result": None, "net": None,
                }},
            })
            now = datetime(2099, 1, 1, tzinfo=timezone.utc)  # arbitrarily far in the future
            results = grade.pending(weekends_dir=td, now=now)
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["needs_kickoff"])
            self.assertEqual(results[0]["selection"], "Team A ML")


class TestPending(unittest.TestCase):
    def test_open_bet_past_grace_period_is_pending(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "thu", {
                "weekend_id": "2026-09-24",
                "slot": "thu",
                "card": {"easy_bet": {
                    "stake": 1.0, "dk_odds": -110, "selection": "Team A ML",
                    "result": None, "net": None,
                    "kickoff": "2026-09-24T20:15:00-04:00",
                }},
            })
            now = datetime(2026, 9, 25, 5, 0, tzinfo=timezone.utc)  # well past kickoff+4h
            results = grade.pending(weekends_dir=td, now=now)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["tier"], "easy_bet")

    def test_open_bet_within_grace_period_is_not_pending(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "thu", {
                "weekend_id": "2026-09-24",
                "slot": "thu",
                "card": {"easy_bet": {
                    "stake": 1.0, "dk_odds": -110, "selection": "Team A ML",
                    "result": None, "net": None,
                    "kickoff": "2026-09-24T20:15:00-04:00",
                }},
            })
            now = datetime(2026, 9, 24, 21, 0, tzinfo=timezone.utc)  # 45min after kickoff
            results = grade.pending(weekends_dir=td, now=now)
            self.assertEqual(len(results), 0)

    def test_already_graded_bet_is_not_pending(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "thu", {
                "weekend_id": "2026-09-24",
                "slot": "thu",
                "card": {"easy_bet": {
                    "stake": 1.0, "dk_odds": -110, "selection": "Team A ML",
                    "result": "Won", "net": 0.91,
                    "kickoff": "2026-09-24T20:15:00-04:00",
                }},
            })
            now = datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc)
            results = grade.pending(weekends_dir=td, now=now)
            self.assertEqual(len(results), 0)

    def test_no_weekends_dir(self):
        results = grade.pending(weekends_dir=Path("/does/not/exist"), now=datetime.now(timezone.utc))
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
