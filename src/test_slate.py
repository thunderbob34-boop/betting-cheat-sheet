import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import slate

CONFIG = dict(slate._CONFIG_DEFAULTS)


def _write_slot(weekends_dir, weekend_id, slot, card, extra=None):
    d = Path(weekends_dir) / weekend_id
    d.mkdir(parents=True, exist_ok=True)
    data = {"weekend_id": weekend_id, "slot": slot, "date": "2026-01-01", "card": card}
    if extra:
        data.update(extra)
    with open(d / f"{slot}.json", "w", encoding="utf-8") as f:
        json.dump(data, f)


def _settled_bet(stake, result, net):
    return {"stake": stake, "result": result, "net": net, "dk_odds": -110}


def _open_bet(stake):
    return {"stake": stake, "result": None, "net": None, "dk_odds": -110}


class TestCurrentWindow(unittest.TestCase):
    def test_thursday(self):
        now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)  # a Thursday
        w = slate.current_window(now, CONFIG)
        self.assertEqual(w["weekend_id"], "2026-09-24")
        self.assertEqual(w["today_slot"], "thu")
        self.assertEqual(w["next_slot"], "sat")
        self.assertEqual(w["next_slot_date"], "2026-09-26")
        self.assertEqual(w["nfl_week_label"], "NFL Week 3")

    def test_friday_is_not_a_configured_slot(self):
        now = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)  # Friday
        w = slate.current_window(now, CONFIG)
        self.assertEqual(w["weekend_id"], "2026-09-24")
        self.assertIsNone(w["today_slot"])
        self.assertEqual(w["next_slot"], "sat")

    def test_saturday(self):
        now = datetime(2026, 9, 26, 16, 0, tzinfo=timezone.utc)
        w = slate.current_window(now, CONFIG)
        self.assertEqual(w["today_slot"], "sat")
        self.assertEqual(w["next_slot"], "sun")
        self.assertEqual(w["next_slot_date"], "2026-09-27")

    def test_monday_next_slot_is_next_weekend_thursday(self):
        now = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)  # Monday
        w = slate.current_window(now, CONFIG)
        self.assertEqual(w["weekend_id"], "2026-09-24")
        self.assertEqual(w["today_slot"], "mon")
        self.assertEqual(w["next_slot"], "thu")
        self.assertEqual(w["next_slot_date"], "2026-10-01")

    def test_tuesday_belongs_to_weekend_that_just_ended(self):
        now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)  # Tuesday
        w = slate.current_window(now, CONFIG)
        self.assertEqual(w["weekend_id"], "2026-09-24")
        self.assertIsNone(w["today_slot"])
        self.assertEqual(w["next_slot"], "thu")
        self.assertEqual(w["next_slot_date"], "2026-10-01")

    def test_wednesday_belongs_to_weekend_that_just_ended(self):
        now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)  # Wednesday
        w = slate.current_window(now, CONFIG)
        self.assertEqual(w["weekend_id"], "2026-09-24")
        self.assertIsNone(w["today_slot"])
        self.assertEqual(w["next_slot"], "thu")
        self.assertEqual(w["next_slot_date"], "2026-10-01")

    def test_nfl_week_label_increments(self):
        now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)  # next Thursday
        w = slate.current_window(now, CONFIG)
        self.assertEqual(w["nfl_week_label"], "NFL Week 4")


class TestWeekendStatus(unittest.TestCase):
    def test_no_files_yet(self):
        with tempfile.TemporaryDirectory() as td:
            status = slate.weekend_status("2026-09-24", CONFIG, weekends_dir=td)
            self.assertEqual(status["settled_net"], 0.0)
            self.assertEqual(status["open_stakes"], 0.0)
            self.assertFalse(status["lottery_used"])
            self.assertEqual(status["open_bets"], [])

    def test_settled_and_open_stakes(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "thu", {
                "easy_bet": _settled_bet(2.25, "Lost", -2.25),
                "fun_parlay": _settled_bet(1.25, "Lost", -1.25),
            })
            _write_slot(td, "2026-09-24", "sat", {
                "easy_bet": _open_bet(1.00),
            })
            status = slate.weekend_status("2026-09-24", CONFIG, weekends_dir=td)
            self.assertAlmostEqual(status["settled_net"], -3.50, delta=1e-9)
            self.assertAlmostEqual(status["open_stakes"], 1.00, delta=1e-9)
            self.assertEqual(len(status["open_bets"]), 1)

    def test_lottery_excluded_from_pacing(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "thu", {
                "lottery_ticket": _settled_bet(0.50, "Lost", -0.50),
            })
            status = slate.weekend_status("2026-09-24", CONFIG, weekends_dir=td)
            self.assertEqual(status["settled_net"], 0.0)
            self.assertEqual(status["open_stakes"], 0.0)
            self.assertTrue(status["lottery_used"])

    def test_exclude_slot_makes_its_own_file_invisible(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "sat", {
                "easy_bet": _settled_bet(1.00, "Lost", -1.00),
            })
            status = slate.weekend_status("2026-09-24", CONFIG, weekends_dir=td, exclude_slot="sat")
            self.assertEqual(status["settled_net"], 0.0)


class TestSlotBudgetWorkedExamples(unittest.TestCase):
    """Pinned worked examples from the spec."""

    def test_fresh_weekend_thursday(self):
        with tempfile.TemporaryDirectory() as td:
            b = slate.slot_budget("2026-09-24", "thu", CONFIG, datetime.now(timezone.utc), weekends_dir=td)
            self.assertAlmostEqual(b["total"], 1.25, delta=1e-9)
            self.assertAlmostEqual(b["easy"], 0.75, delta=1e-9)
            self.assertAlmostEqual(b["fun"], 0.50, delta=1e-9)

    def test_down_after_thursday_saturday_budget(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "thu", {
                "easy_bet": _settled_bet(0.75, "Lost", -0.75),
                "fun_parlay": _settled_bet(0.50, "Lost", -0.50),
            })
            b = slate.slot_budget("2026-09-24", "sat", CONFIG, datetime.now(timezone.utc), weekends_dir=td)
            self.assertAlmostEqual(b["capacity"], 3.75, delta=1e-9)
            self.assertEqual(b["slots_remaining"], 3)
            self.assertAlmostEqual(b["total"], 0.90, delta=1e-9)

    def test_up_after_thursday_saturday_budget(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "thu", {
                "easy_bet": _settled_bet(1.25, "Won", 0.91),
            })
            b = slate.slot_budget("2026-09-24", "sat", CONFIG, datetime.now(timezone.utc), weekends_dir=td)
            self.assertAlmostEqual(b["capacity"], 5.91, delta=1e-9)
            self.assertEqual(b["slots_remaining"], 3)
            self.assertAlmostEqual(b["total"], 1.95, delta=1e-9)

    def test_last_slot_gets_capacity_over_one(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "thu", {
                "easy_bet": _settled_bet(0.75, "Lost", -0.75),
                "fun_parlay": _settled_bet(0.50, "Lost", -0.50),
            })
            b = slate.slot_budget("2026-09-24", "mon", CONFIG, datetime.now(timezone.utc), weekends_dir=td)
            self.assertEqual(b["slots_remaining"], 1)
            # capacity 3.75 * reserve 0.75 = 2.8125 -> floor to 2.80
            self.assertAlmostEqual(b["total"], 2.80, delta=1e-9)

    def test_idempotent_rerun_of_same_slot(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "thu", {
                "easy_bet": _settled_bet(0.75, "Lost", -0.75),
                "fun_parlay": _settled_bet(0.50, "Lost", -0.50),
            })
            b1 = slate.slot_budget("2026-09-24", "sat", CONFIG, datetime.now(timezone.utc), weekends_dir=td)
            # Now a draft sat.json exists with its own (unsettled) stake --
            # re-running sat's own budget must not change because of it.
            _write_slot(td, "2026-09-24", "sat", {
                "easy_bet": _open_bet(0.50),
            })
            b2 = slate.slot_budget("2026-09-24", "sat", CONFIG, datetime.now(timezone.utc), weekends_dir=td)
            self.assertAlmostEqual(b1["total"], b2["total"], delta=1e-9)
            self.assertAlmostEqual(b1["capacity"], b2["capacity"], delta=1e-9)

    def test_round_down_below_min_stake_floors_to_zero(self):
        config = dict(CONFIG)
        config["min_stake"] = 4.00  # deliberately huge so 1.25 total floors to 0
        with tempfile.TemporaryDirectory() as td:
            b = slate.slot_budget("2026-09-24", "thu", config, datetime.now(timezone.utc), weekends_dir=td)
            self.assertEqual(b["total"], 0.0)
            self.assertEqual(b["easy"], 0.0)
            self.assertEqual(b["fun"], 0.0)

    def test_lottery_available_reflects_weekend_usage(self):
        with tempfile.TemporaryDirectory() as td:
            b = slate.slot_budget("2026-09-24", "sat", CONFIG, datetime.now(timezone.utc), weekends_dir=td)
            self.assertTrue(b["lottery_available"])
            _write_slot(td, "2026-09-24", "thu", {
                "lottery_ticket": _settled_bet(0.50, "Lost", -0.50),
            })
            b2 = slate.slot_budget("2026-09-24", "sat", CONFIG, datetime.now(timezone.utc), weekends_dir=td)
            self.assertFalse(b2["lottery_available"])

    def test_why_string_mentions_key_figures_when_down(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "thu", {
                "easy_bet": _settled_bet(2.25, "Lost", -2.25),
                "fun_parlay": _settled_bet(1.25, "Lost", -1.25),
            })
            b = slate.slot_budget("2026-09-24", "sat", CONFIG, datetime.now(timezone.utc), weekends_dir=td)
            self.assertIn("Down $3.50", b["why"])
            self.assertIn("$1.50", b["why"])
            self.assertIn("3 slots", b["why"])
            self.assertIn("25%", b["why"])


class TestLoadConfig(unittest.TestCase):
    def test_missing_file_falls_back_to_defaults(self):
        config = slate.load_config(Path("/does/not/exist.json"))
        self.assertEqual(config["weekend_loss_limit"], 5.00)
        self.assertEqual(config["slots"], ["thu", "sat", "sun", "mon"])


# ---------------------------------------------------------------------------
# Regression: _round_down_to_step must floor, never round up (stoploss /
# round-down-to-step-rounds-up)
# ---------------------------------------------------------------------------

class TestRoundDownToStep(unittest.TestCase):
    def test_known_regression_value_floors_down_not_up(self):
        # 1.2466666666666668 * 100 = 124.666..., which the old
        # round(value*100) path rounds to the nearest CENT (125) before
        # floor-dividing -- 125 happens to be an exact multiple of 5, so it
        # wrongly returns 1.25 (one whole step above the true floor, 1.20).
        self.assertAlmostEqual(slate._round_down_to_step(1.2466666666666668, 0.05), 1.20, delta=1e-9)

    def test_settled_net_scenario_floors_down_not_up(self):
        # Real-shaped scenario from the audit: thu settles -$2.00 (Lost)
        # and +$0.18 (Won at +18), settled_net = -1.82 (not contrived).
        # capacity = 5.00 - 1.82 = 3.18, slots_remaining(sat) = 3,
        # raw = 3.18/3 = 1.06, reserved (down) *0.75 = 0.795 -- the correct
        # floor to a nickel is 0.75; the old buggy rounding produced 0.80.
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "thu", {
                "easy_bet": _settled_bet(2.00, "Lost", -2.00),
                "fun_parlay": _settled_bet(1.00, "Won", 0.18),
            })
            b = slate.slot_budget("2026-09-24", "sat", CONFIG, datetime.now(timezone.utc), weekends_dir=td)
            self.assertAlmostEqual(b["settled_net"], -1.82, delta=1e-9)
            self.assertAlmostEqual(b["total"], 0.75, delta=1e-9)


# ---------------------------------------------------------------------------
# Regression: slot identity must come from the file's location (its own
# filename), never from its own claimed "slot" field (stoploss /
# slot-file-metadata-trusted-over-filename)
# ---------------------------------------------------------------------------

class TestWeekendStatusSlotIdentity(unittest.TestCase):
    def test_slot_identity_derived_from_filename_not_body(self):
        with tempfile.TemporaryDirectory() as td:
            # File is correctly named/placed at thu.json, but its own body
            # claims (wrongly) to be "sun" -- a plausible copy/paste slip.
            _write_slot(td, "2026-09-24", "thu", {
                "easy_bet": _settled_bet(2.25, "Lost", -2.25),
                "fun_parlay": _settled_bet(1.25, "Lost", -1.25),
            }, extra={"slot": "sun"})
            # Computing SUN's own budget (exclude_slot="sun") must NOT make
            # this file invisible just because its mislabeled internal
            # field happens to equal "sun" -- its real -$3.50 loss must
            # still be counted, since the file actually lives at thu.json.
            status = slate.weekend_status("2026-09-24", CONFIG, weekends_dir=td, exclude_slot="sun")
            self.assertAlmostEqual(status["settled_net"], -3.50, delta=1e-9)


# ---------------------------------------------------------------------------
# Regression: a graded bet (result set) must never have a null net --
# it must be counted, or the file is malformed and building must fail
# loudly (stoploss / graded-bet-with-null-net-vanishes-from-accounting)
# ---------------------------------------------------------------------------

class TestWeekendStatusNullNetGuard(unittest.TestCase):
    def test_result_set_with_null_net_raises(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "sat", {
                "easy_bet": {"stake": 1.00, "result": "Lost", "net": None, "dk_odds": -110},
            })
            with self.assertRaises(ValueError):
                slate.weekend_status("2026-09-24", CONFIG, weekends_dir=td)

    def test_graded_with_net_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            _write_slot(td, "2026-09-24", "sat", {
                "easy_bet": _settled_bet(1.00, "Lost", -1.00),
            })
            status = slate.weekend_status("2026-09-24", CONFIG, weekends_dir=td)  # should not raise
            self.assertAlmostEqual(status["settled_net"], -1.00, delta=1e-9)


# ---------------------------------------------------------------------------
# Regression: an unrecognized slot name must never silently be treated as
# the weekend's last remaining slot (guardrails /
# unrecognized-slot-name-grabs-full-weekend-budget)
# ---------------------------------------------------------------------------

class TestSlotBudgetUnrecognizedSlot(unittest.TestCase):
    def test_unrecognized_slot_name_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                slate.slot_budget("2026-09-24", "saturday_extra", CONFIG, datetime.now(timezone.utc), weekends_dir=td)

    def test_fri_is_still_tolerated(self):
        with tempfile.TemporaryDirectory() as td:
            b = slate.slot_budget("2026-09-24", "fri", CONFIG, datetime.now(timezone.utc), weekends_dir=td)
            self.assertEqual(b["slots_remaining"], 1)


# ---------------------------------------------------------------------------
# Regression: the budget target on a Tue/Wed (or any day with no
# today_slot) must be the UPCOMING weekend that next_slot actually belongs
# to, not window["weekend_id"] (which intentionally still names the
# weekend that just ended, for display/review purposes) (grading /
# tue-wed-status-budget-uses-stale-weekend-id)
# ---------------------------------------------------------------------------

class TestBudgetTargetForWindow(unittest.TestCase):
    def test_on_tuesday_budget_targets_upcoming_weekend_not_elapsed_one(self):
        now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)  # Tuesday
        window = slate.current_window(now, CONFIG)
        self.assertEqual(window["weekend_id"], "2026-09-24")  # unchanged display semantics
        weekend_id, slot = slate._budget_target_for_window(window)
        self.assertEqual(slot, "thu")
        self.assertEqual(weekend_id, "2026-10-01")  # NOT the elapsed "2026-09-24"

    def test_on_wednesday_budget_targets_upcoming_weekend(self):
        now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)  # Wednesday
        window = slate.current_window(now, CONFIG)
        weekend_id, slot = slate._budget_target_for_window(window)
        self.assertEqual(slot, "thu")
        self.assertEqual(weekend_id, "2026-10-01")

    def test_on_saturday_budget_targets_current_weekend_today_slot(self):
        now = datetime(2026, 9, 26, 16, 0, tzinfo=timezone.utc)  # Saturday
        window = slate.current_window(now, CONFIG)
        weekend_id, slot = slate._budget_target_for_window(window)
        self.assertEqual(slot, "sat")
        self.assertEqual(weekend_id, "2026-09-24")

    def test_on_monday_budget_targets_current_weekend_today_slot(self):
        now = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)  # Monday
        window = slate.current_window(now, CONFIG)
        weekend_id, slot = slate._budget_target_for_window(window)
        self.assertEqual(slot, "mon")
        self.assertEqual(weekend_id, "2026-09-24")


# ---------------------------------------------------------------------------
# Regression: a malformed/unreadable slot JSON file must fail loudly and
# name the offending path, not crash every CLI with a path-less traceback
# (grading / unguarded-json-load-crashes-all-clis)
# ---------------------------------------------------------------------------

class TestLoadSlotMalformedJson(unittest.TestCase):
    def test_malformed_slot_file_names_path(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "2026-09-24"
            d.mkdir(parents=True)
            bad_path = d / "sat.json"
            bad_path.write_text("{", encoding="utf-8")
            with self.assertRaises(slate.SlateError) as ctx:
                slate.weekend_status("2026-09-24", CONFIG, weekends_dir=td)
            self.assertIn(str(bad_path), str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
