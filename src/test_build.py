import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import build
import slate
from build import (
    RuleViolation,
    compute_card_record,
    compute_scoreboard,
    find_earliest_date_label,
    load_config,
    render_card_bet,
    render_card_section,
    render_leg_entry,
    render_page,
    render_record,
    render_scoreboard,
    render_today,
    sort_leg_bank,
    validate_build,
    validate_lottery_count,
    validate_publish,
    validate_slot_rules,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = REPO_ROOT / "data" / "bet_log.csv"
CONFIG_PATH = REPO_ROOT / "data" / "config.json"
REAL_TEMPLATE_PATH = REPO_ROOT / "templates" / "page.html"
SEED_WEEKENDS_DIR = REPO_ROOT / "data" / "weekends"

CONFIG = dict(build._CONFIG_DEFAULTS)
FUTURE_KICKOFF = "2099-01-01T18:00:00-05:00"
# January in America/New_York is EST (-05:00) -- kept correctly offset so
# this constant tests exactly one thing ("kickoff in the past"), never
# colliding with the DST-offset cross-check added for the
# no-cross-check-on-kickoff-utc-offset-near-dst regression.
PAST_KICKOFF = "2020-01-01T18:00:00-05:00"


def _valid_easy_bet(**overrides):
    bet = {
        "id": "easy-bet",
        "selection": "Team A ML",
        "market": "moneyline",
        "dk_odds": -150,
        "estimated_prob": 0.62,
        "estimated_prob_source": "test",
        "prob_sources": [{"name": "Source A", "prob": 0.60}, {"name": "Source B", "prob": 0.64}],
        "is_pre_kickoff": True,
        "stake": 3.00,
        "reason_summary": "test easy bet",
        "reason": "test easy bet, full reasoning",
        "verify": {"source": "test", "fetched_at": "test", "note": "test"},
        "game": "AAA @ BBB",
        "kickoff": FUTURE_KICKOFF,
        "game_script": "neutral",
        "result": None,
        "net": None,
    }
    bet.update(overrides)
    return bet


def _valid_fun_parlay(**overrides):
    bet = {
        "id": "fun-parlay",
        "legs": [
            {"selection": "Leg 1", "estimated_prob": 0.65, "reason": "x", "game": "AAA @ BBB",
             "kickoff": FUTURE_KICKOFF, "game_script": "neutral"},
            {"selection": "Leg 2", "estimated_prob": 0.70, "reason": "x", "game": "CCC @ DDD",
             "kickoff": FUTURE_KICKOFF, "game_script": "neutral"},
        ],
        "dk_odds": 150,
        "estimated_prob_source": "test",
        "is_pre_kickoff": True,
        "stake": 2.00,
        "reason_summary": "test fun parlay",
        "reason": "test fun parlay, full reasoning",
        "verify": {"source": "test", "fetched_at": "test", "note": "test"},
        "game": "AAA @ BBB",
        "kickoff": FUTURE_KICKOFF,
        "game_script": "neutral",
        "result": None,
        "net": None,
    }
    bet.update(overrides)
    return bet


def _valid_lottery_ticket(n_legs=12, **overrides):
    bet = {
        "id": "lottery-ticket",
        "legs": [{"selection": f"Leg {i}", "estimated_prob": 0.5} for i in range(n_legs)],
        "dk_odds": 50000,
        "estimated_prob_source": "test",
        "is_pre_kickoff": True,
        "stake": 0.50,
        "reason_summary": "test lottery ticket",
        "reason": "test lottery ticket, full reasoning",
        "verify": {"source": "test", "fetched_at": "test", "note": "test"},
        "game": "AAA @ BBB",
        "kickoff": FUTURE_KICKOFF,
        "game_script": "neutral",
        "result": None,
        "net": None,
    }
    bet.update(overrides)
    return bet


def _valid_slot(weekend_id="2099-01-01", slot="thu", include_fun_parlay=True,
                include_lottery_ticket=False, historical=False):
    card = {"easy_bet": _valid_easy_bet()}
    if include_fun_parlay:
        card["fun_parlay"] = _valid_fun_parlay()
    if include_lottery_ticket:
        card["lottery_ticket"] = _valid_lottery_ticket()
    return {
        "weekend_id": weekend_id,
        "slot": slot,
        "date": "2099-01-01",
        "sport": "NFL",
        "historical_import": historical,
        "card": card,
        "leg_bank": [],
    }


def _write_slot(weekends_dir, slot_dict):
    d = Path(weekends_dir) / slot_dict["weekend_id"]
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{slot_dict['slot']}.json", "w", encoding="utf-8") as f:
        json.dump(slot_dict, f)


# ---------------------------------------------------------------------------
# Tier structure (always enforced, historical included)
# ---------------------------------------------------------------------------

class TestTierStructure(unittest.TestCase):
    def test_missing_easy_bet_raises(self):
        slot = _valid_slot()
        slot["card"] = {}
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_easy_bet_with_legs_raises(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["legs"] = [{"selection": "x", "estimated_prob": 0.6}]
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_easy_bet_alone_is_valid(self):
        slot = _valid_slot(include_fun_parlay=False)
        validate_slot_rules(slot, CONFIG)  # should not raise

    def test_fun_parlay_with_one_leg_raises(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay"]["legs"] = [{"selection": "x", "estimated_prob": 0.6}]
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_fun_parlay_with_four_legs_raises(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay"]["legs"] = [
            {"selection": f"Leg {i}", "estimated_prob": 0.6} for i in range(4)
        ]
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_fun_parlay_leg_below_55_percent_raises(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay"]["legs"][0]["estimated_prob"] = 0.54
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_fun_parlay_leg_at_exactly_55_percent_is_fine(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay"]["legs"][0]["estimated_prob"] = 0.55
        validate_slot_rules(slot, CONFIG)  # should not raise

    def test_lottery_ticket_below_10_legs_raises(self):
        slot = _valid_slot(include_lottery_ticket=True)
        slot["card"]["lottery_ticket"] = _valid_lottery_ticket(n_legs=9)
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_lottery_ticket_above_20_legs_raises(self):
        slot = _valid_slot(include_lottery_ticket=True)
        slot["card"]["lottery_ticket"] = _valid_lottery_ticket(n_legs=21)
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_lottery_ticket_wrong_stake_raises(self):
        slot = _valid_slot(include_lottery_ticket=True)
        slot["card"]["lottery_ticket"]["stake"] = 1.00
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_lottery_ticket_exempt_from_leg_probability_floor(self):
        slot = _valid_slot(include_lottery_ticket=True)
        slot["card"]["lottery_ticket"]["legs"] = [
            {"selection": f"Leg {i}", "estimated_prob": 0.10} for i in range(14)
        ]
        validate_slot_rules(slot, CONFIG)  # should not raise


# ---------------------------------------------------------------------------
# RULE 2 -- pre-kickoff only (structural half)
# ---------------------------------------------------------------------------

class TestRule2PreKickoff(unittest.TestCase):
    def test_non_pre_kickoff_easy_bet_raises(self):
        slot = _valid_slot()
        slot["card"]["easy_bet"]["is_pre_kickoff"] = False
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_live_substring_in_market_raises(self):
        slot = _valid_slot()
        slot["card"]["easy_bet"]["market"] = "live moneyline"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_non_pre_kickoff_lottery_ticket_raises(self):
        slot = _valid_slot(include_lottery_ticket=True)
        slot["card"]["lottery_ticket"]["is_pre_kickoff"] = False
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_historical_import_skips_pre_kickoff_check(self):
        # A historical slot must be GENUINELY historical (every present
        # tier graded, kickoff in the past) to earn its rule exemptions --
        # see TestHistoricalImportClaimValidated below.
        slot = _valid_slot(historical=True, include_fun_parlay=False)
        slot["card"]["easy_bet"]["is_pre_kickoff"] = False
        slot["card"]["easy_bet"]["kickoff"] = PAST_KICKOFF
        slot["card"]["easy_bet"]["result"] = "Lost"
        slot["card"]["easy_bet"]["net"] = -3.00
        validate_slot_rules(slot, CONFIG)  # should not raise


# ---------------------------------------------------------------------------
# RULE 9 -- blacklist
# ---------------------------------------------------------------------------

class TestRule9Blacklist(unittest.TestCase):
    def test_easy_bet_selection_blacklisted_raises(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["selection"] = "MarShawn Lloyd 25+ rush+rec yds"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_blacklist_is_case_insensitive(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["selection"] = "marshawn lloyd anytime TD"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_fun_parlay_leg_selection_blacklisted_raises(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay"]["legs"][0]["selection"] = "MarShawn Lloyd 4+ receptions"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_leg_bank_selection_blacklisted_raises(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["leg_bank"] = [{"selection": "MarShawn Lloyd anytime TD", "estimated_prob": 0.4}]
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_historical_import_exempt_from_blacklist(self):
        slot = _valid_slot(include_fun_parlay=False, historical=True)
        slot["card"]["easy_bet"]["selection"] = "MarShawn Lloyd 25+ rush+rec yds"
        slot["card"]["easy_bet"]["kickoff"] = PAST_KICKOFF
        slot["card"]["easy_bet"]["result"] = "Lost"
        slot["card"]["easy_bet"]["net"] = -3.00
        validate_slot_rules(slot, CONFIG)  # should not raise

    def test_clean_selection_passes(self):
        slot = _valid_slot(include_fun_parlay=False)
        validate_slot_rules(slot, CONFIG)  # should not raise


# ---------------------------------------------------------------------------
# RULE 10 -- game script conflict
# ---------------------------------------------------------------------------

class TestRule10GameScript(unittest.TestCase):
    def test_both_phrasings_of_same_script_conflict(self):
        slot = _valid_slot()
        slot["card"]["easy_bet"]["game"] = "ATL @ GB"
        slot["card"]["easy_bet"]["game_script"] = "ATL leading"
        slot["card"]["fun_parlay"]["legs"][0]["game"] = "ATL @ GB"
        slot["card"]["fun_parlay"]["legs"][0]["game_script"] = "GB trailing"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_reverse_phrasing_also_conflicts(self):
        slot = _valid_slot()
        slot["card"]["easy_bet"]["game"] = "ATL @ GB"
        slot["card"]["easy_bet"]["game_script"] = "GB leading"
        slot["card"]["fun_parlay"]["legs"][0]["game"] = "ATL @ GB"
        slot["card"]["fun_parlay"]["legs"][0]["game_script"] = "ATL trailing"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_different_games_do_not_conflict(self):
        slot = _valid_slot()
        slot["card"]["easy_bet"]["game"] = "ATL @ GB"
        slot["card"]["easy_bet"]["game_script"] = "ATL leading"
        slot["card"]["fun_parlay"]["legs"][0]["game"] = "XXX @ YYY"
        slot["card"]["fun_parlay"]["legs"][0]["game_script"] = "XXX leading"
        validate_slot_rules(slot, CONFIG)  # should not raise

    def test_neutral_never_conflicts(self):
        slot = _valid_slot()
        slot["card"]["easy_bet"]["game"] = "ATL @ GB"
        slot["card"]["easy_bet"]["game_script"] = "neutral"
        slot["card"]["fun_parlay"]["legs"][0]["game"] = "ATL @ GB"
        slot["card"]["fun_parlay"]["legs"][0]["game_script"] = "neutral"
        validate_slot_rules(slot, CONFIG)  # should not raise

    def test_opposite_scripts_same_game_do_not_conflict(self):
        slot = _valid_slot()
        slot["card"]["easy_bet"]["game"] = "ATL @ GB"
        slot["card"]["easy_bet"]["game_script"] = "ATL leading"
        slot["card"]["fun_parlay"]["legs"][0]["game"] = "ATL @ GB"
        slot["card"]["fun_parlay"]["legs"][0]["game_script"] = "GB leading"
        validate_slot_rules(slot, CONFIG)  # should not raise


# ---------------------------------------------------------------------------
# RULE 11 -- source disagreement
# ---------------------------------------------------------------------------

class TestRule11SourceAgreement(unittest.TestCase):
    def test_single_source_raises(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["prob_sources"] = [{"name": "Only One", "prob": 0.6}]
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_no_sources_raises(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["prob_sources"] = []
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_spread_over_10_points_raises(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["prob_sources"] = [
            {"name": "A", "prob": 0.50}, {"name": "B", "prob": 0.62},
        ]
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_spread_at_exactly_10_points_is_fine(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["prob_sources"] = [
            {"name": "A", "prob": 0.50}, {"name": "B", "prob": 0.60},
        ]
        slot["card"]["easy_bet"]["estimated_prob"] = 0.55  # matches the sources' average
        validate_slot_rules(slot, CONFIG)  # should not raise

    def test_historical_import_exempt(self):
        slot = _valid_slot(include_fun_parlay=False, historical=True)
        slot["card"]["easy_bet"]["prob_sources"] = []
        slot["card"]["easy_bet"]["kickoff"] = PAST_KICKOFF
        slot["card"]["easy_bet"]["result"] = "Lost"
        slot["card"]["easy_bet"]["net"] = -3.00
        validate_slot_rules(slot, CONFIG)  # should not raise


# ---------------------------------------------------------------------------
# RULE 12 -- sack vs mobile QB
# ---------------------------------------------------------------------------

class TestRule12SackMobileQb(unittest.TestCase):
    def test_sack_bet_against_mobile_qb_raises(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay"]["legs"][0]["market"] = "sack"
        slot["card"]["fun_parlay"]["legs"][0]["opp_qb"] = "Jalen Hurts"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_sack_bet_against_other_qb_passes(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay"]["legs"][0]["market"] = "sack"
        slot["card"]["fun_parlay"]["legs"][0]["opp_qb"] = "Some Pocket Passer"
        validate_slot_rules(slot, CONFIG)  # should not raise

    def test_sack_bet_missing_opp_qb_raises(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay"]["legs"][0]["market"] = "sack"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_mobile_qb_check_is_case_insensitive(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay"]["legs"][0]["market"] = "sack"
        slot["card"]["fun_parlay"]["legs"][0]["opp_qb"] = "jalen hurts"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)


# ---------------------------------------------------------------------------
# RULE 6 -- at most one lottery ticket per weekend
# ---------------------------------------------------------------------------

class TestRule6LotteryCount(unittest.TestCase):
    def test_two_lotteries_in_one_weekend_raises(self):
        thu = _valid_slot(weekend_id="2099-01-01", slot="thu", include_lottery_ticket=True)
        sat = _valid_slot(weekend_id="2099-01-01", slot="sat", include_lottery_ticket=True)
        with self.assertRaises(RuleViolation):
            validate_lottery_count("2099-01-01", [thu, sat])

    def test_one_lottery_is_fine(self):
        thu = _valid_slot(weekend_id="2099-01-01", slot="thu", include_lottery_ticket=True)
        sat = _valid_slot(weekend_id="2099-01-01", slot="sat", include_lottery_ticket=False)
        validate_lottery_count("2099-01-01", [thu, sat])  # should not raise

    def test_no_lottery_is_fine(self):
        validate_lottery_count("2099-01-01", [])  # should not raise


# ---------------------------------------------------------------------------
# --publish only: RULE 2 (kickoff future) + RULE 1 (budget)
# ---------------------------------------------------------------------------

class TestValidatePublish(unittest.TestCase):
    def test_past_kickoff_raises(self):
        with tempfile.TemporaryDirectory() as td:
            slot = _valid_slot(weekend_id="2026-09-24", slot="sat", include_fun_parlay=False)
            slot["card"]["easy_bet"]["kickoff"] = PAST_KICKOFF
            slot["card"]["easy_bet"]["stake"] = 0.10
            now = datetime.now(timezone.utc)
            with self.assertRaises(RuleViolation):
                validate_publish(slot, "2026-09-24", "sat", CONFIG, now, td)

    def test_leg_past_kickoff_raises(self):
        with tempfile.TemporaryDirectory() as td:
            slot = _valid_slot(weekend_id="2026-09-24", slot="sat")
            slot["card"]["easy_bet"]["stake"] = 0.10
            slot["card"]["fun_parlay"]["stake"] = 0.05
            slot["card"]["fun_parlay"]["legs"][0]["kickoff"] = PAST_KICKOFF
            now = datetime.now(timezone.utc)
            with self.assertRaises(RuleViolation):
                validate_publish(slot, "2026-09-24", "sat", CONFIG, now, td)

    def test_over_budget_raises(self):
        with tempfile.TemporaryDirectory() as td:
            # Fresh weekend, thu budget is $1.25 total -- stake way over that.
            slot = _valid_slot(weekend_id="2026-09-24", slot="thu", include_fun_parlay=False)
            slot["card"]["easy_bet"]["stake"] = 10.00
            now = datetime.now(timezone.utc)
            with self.assertRaises(RuleViolation):
                validate_publish(slot, "2026-09-24", "thu", CONFIG, now, td)

    def test_within_budget_passes(self):
        with tempfile.TemporaryDirectory() as td:
            slot = _valid_slot(weekend_id="2026-09-24", slot="thu", include_fun_parlay=False)
            slot["card"]["easy_bet"]["stake"] = 0.75  # within the $1.25 fresh-weekend budget
            now = datetime.now(timezone.utc)
            validate_publish(slot, "2026-09-24", "thu", CONFIG, now, td)  # should not raise

    def test_historical_import_is_always_refused_by_publish(self):
        # Required policy: --publish must REFUSE a historical_import slot
        # outright -- historical slots record what already happened and
        # are never a live publish target, no matter how "clean" they
        # otherwise look (even a genuinely graded, past-kickoff, small
        # stake historical slot must still be refused).
        with tempfile.TemporaryDirectory() as td:
            slot = _valid_slot(weekend_id="2026-09-24", slot="thu", historical=True,
                                include_fun_parlay=False)
            slot["card"]["easy_bet"]["kickoff"] = PAST_KICKOFF
            slot["card"]["easy_bet"]["result"] = "Lost"
            slot["card"]["easy_bet"]["net"] = -0.75
            slot["card"]["easy_bet"]["stake"] = 0.75
            now = datetime.now(timezone.utc)
            with self.assertRaises(RuleViolation):
                validate_publish(slot, "2026-09-24", "thu", CONFIG, now, td)


# ---------------------------------------------------------------------------
# validate_build orchestration
# ---------------------------------------------------------------------------

class TestValidateBuild(unittest.TestCase):
    def test_no_weekend_files_does_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            validate_build(Path(td), CONFIG, datetime.now(timezone.utc))  # should not raise

    def test_bad_slot_in_current_weekend_raises(self):
        with tempfile.TemporaryDirectory() as td:
            now = datetime(2026, 9, 26, 16, 0, tzinfo=timezone.utc)
            window = slate.current_window(now, CONFIG)
            slot = _valid_slot(weekend_id=window["weekend_id"], slot="sat")
            slot["card"]["easy_bet"]["selection"] = "MarShawn Lloyd 25+ rush+rec yds"
            _write_slot(td, slot)
            with self.assertRaises(RuleViolation):
                validate_build(Path(td), CONFIG, now)

    def test_real_seed_file_validates_cleanly(self):
        now = datetime(2026, 9, 26, 16, 0, tzinfo=timezone.utc)
        validate_build(SEED_WEEKENDS_DIR, CONFIG, now)  # should not raise


# ---------------------------------------------------------------------------
# Scoreboard -- pinned real-data numbers
# ---------------------------------------------------------------------------

class TestScoreboardRealData(unittest.TestCase):
    def test_matches_known_all_time_totals(self):
        sb = compute_scoreboard(CSV_PATH)
        self.assertEqual(sb["wins"], 1)
        self.assertEqual(sb["cashouts"], 1)
        self.assertEqual(sb["losses"], 21)
        self.assertEqual(sb["open"], 2)
        self.assertEqual(sb["no_data_rows"], 1)
        self.assertAlmostEqual(sb["cash_pl"], -59.93, delta=0.02)
        self.assertAlmostEqual(sb["bankroll_remaining"], -9.93, delta=0.02)
        self.assertTrue(sb["current_streak"].startswith("L"))
        streak_len = int(sb["current_streak"][1:])
        self.assertGreaterEqual(streak_len, 10)
        self.assertLessEqual(streak_len, 20)


class TestScoreboardSeasonScoping(unittest.TestCase):
    def test_season_since_sep_1_2026(self):
        sb = compute_scoreboard(CSV_PATH, since="2026-09-01", starting_bankroll=50.00)
        self.assertEqual(sb["wins"], 0)
        self.assertEqual(sb["losses"], 10)
        self.assertEqual(sb["cashouts"], 0)
        self.assertEqual(sb["open"], 2)
        self.assertEqual(sb["no_data_rows"], 1)
        self.assertAlmostEqual(sb["cash_pl"], -13.00, delta=0.01)
        self.assertAlmostEqual(sb["bankroll_remaining"], 37.00, delta=0.01)
        self.assertEqual(sb["current_streak"], "L10")


class TestFindEarliestDateLabel(unittest.TestCase):
    def test_matches_march_2026(self):
        self.assertEqual(find_earliest_date_label(CSV_PATH), "Mar 2026")


class TestLoadConfig(unittest.TestCase):
    def test_real_config_file(self):
        config = load_config(CONFIG_PATH)
        self.assertEqual(config["season_start"], "2026-09-01")
        self.assertAlmostEqual(config["starting_bankroll"], 50.00, delta=1e-9)
        self.assertAlmostEqual(config["weekend_loss_limit"], 5.00, delta=1e-9)
        self.assertAlmostEqual(config["reserve_factor_when_down"], 0.75, delta=1e-9)
        self.assertEqual(config["slots"], ["thu", "sat", "sun", "mon"])
        self.assertEqual(config["nfl_week1_thursday"], "2026-09-10")
        self.assertIn("MarShawn Lloyd", config["blacklist"])
        self.assertIn("Jalen Hurts", config["mobile_qbs"])

    def test_missing_file_falls_back_to_defaults(self):
        config = load_config(REPO_ROOT / "data" / "does_not_exist.json")
        self.assertEqual(config["season_start"], "2026-09-01")
        self.assertAlmostEqual(config["weekend_loss_limit"], 5.00, delta=1e-9)


class TestSortLegBank(unittest.TestCase):
    def test_sorts_descending_by_estimated_prob(self):
        legs = [
            {"selection": "A", "estimated_prob": 0.55},
            {"selection": "B", "estimated_prob": 0.72},
            {"selection": "C", "estimated_prob": 0.61},
        ]
        ranked = sort_leg_bank(legs)
        self.assertEqual([l["selection"] for l in ranked], ["B", "C", "A"])

    def test_does_not_mutate_input_list(self):
        legs = [{"selection": "A", "estimated_prob": 0.4}, {"selection": "B", "estimated_prob": 0.9}]
        original_order = [l["selection"] for l in legs]
        sort_leg_bank(legs)
        self.assertEqual([l["selection"] for l in legs], original_order)

    def test_empty_list(self):
        self.assertEqual(sort_leg_bank([]), [])


# ---------------------------------------------------------------------------
# Rendering fragments
# ---------------------------------------------------------------------------

class TestRenderScoreboard(unittest.TestCase):
    def test_no_weekly_budget_tile(self):
        sb = compute_scoreboard(CSV_PATH, since="2026-09-01", starting_bankroll=50.00)
        html = render_scoreboard(sb)
        self.assertNotIn("Weekly Budget", html)
        self.assertIn('class="stat-grid"', html)
        self.assertIn('class="label">Streak</span>', html)


class TestRenderCardBet(unittest.TestCase):
    def setUp(self):
        from zoneinfo import ZoneInfo
        self.tz = ZoneInfo("America/New_York")

    def test_easy_bet_tier_badge_and_result_open(self):
        bet = _valid_easy_bet()
        html = render_card_bet("easy_bet", bet, self.tz)
        self.assertIn("1 · Easy Bet", html)
        self.assertIn('class="result-badge open"', html)
        self.assertIn("tier-easy", html)
        self.assertIn('<summary>Why &amp; sources</summary>', html)

    def test_easy_bet_graded_shows_signed_money(self):
        bet = _valid_easy_bet(result="Won", net=1.50)
        html = render_card_bet("easy_bet", bet, self.tz)
        self.assertIn('class="result-badge won"', html)
        self.assertIn("+$1.50", html)

    def test_fun_parlay_title_is_leg_count(self):
        bet = _valid_fun_parlay()
        html = render_card_bet("fun_parlay", bet, self.tz)
        self.assertIn("2-leg parlay", html)
        self.assertIn("2 · Fun Parlay", html)
        self.assertIn("Leg 1", html)
        self.assertIn("Leg 2", html)

    def test_lottery_ticket_shows_one_in_x(self):
        bet = _valid_lottery_ticket(n_legs=12)
        html = render_card_bet("lottery_ticket", bet, self.tz)
        self.assertIn("3 · Lottery Ticket", html)
        self.assertIn("lottery-odds", html)
        self.assertIn("one-in-x", html)
        self.assertIn("~1 in 4,096", html)

    def test_card_section_orders_tiers(self):
        card = {"easy_bet": _valid_easy_bet(), "fun_parlay": _valid_fun_parlay(), "lottery_ticket": _valid_lottery_ticket()}
        html = render_card_section(card, self.tz)
        self.assertLess(html.index("Easy Bet"), html.index("Fun Parlay"))
        self.assertLess(html.index("Fun Parlay"), html.index("Lottery Ticket"))


# ---------------------------------------------------------------------------
# Full page render
# ---------------------------------------------------------------------------

_MINIMAL_TEMPLATE = """<!doctype html><html><head><title>t</title></head><body>
<header>{{UPDATED_AT}} {{NEXT_UPDATE}}</header>
{{SAMPLE_BANNER}}
<section class="panel" id="today">{{TODAY}}</section>
<section class="panel" id="weekend">{{WEEKEND}}</section>
<section class="panel" id="legs">{{LEGS}}</section>
<section class="panel" id="record">{{RECORD}}</section>
</body></html>
"""


class TestRenderPage(unittest.TestCase):
    def _template(self, tmpdir):
        path = Path(tmpdir) / "template.html"
        path.write_text(_MINIMAL_TEMPLATE, encoding="utf-8")
        return path

    def test_no_weekend_files_shows_empty_state(self):
        with tempfile.TemporaryDirectory() as td:
            template_path = self._template(td)
            weekends_dir = Path(td) / "weekends"
            now = datetime(2026, 9, 26, 16, 0, tzinfo=timezone.utc)
            html = render_page(template_path, weekends_dir, CSV_PATH, CONFIG, now)
            self.assertIn("No card yet today", html)
            self.assertIn("empty-state", html)
            self.assertNotIn("{{", html)  # every token replaced

    def test_all_seven_tokens_replaced_on_real_seed_data(self):
        with tempfile.TemporaryDirectory() as td:
            template_path = self._template(td)
            now = datetime(2026, 9, 26, 16, 0, tzinfo=timezone.utc)
            html = render_page(template_path, SEED_WEEKENDS_DIR, CSV_PATH, CONFIG, now)
            self.assertNotIn("{{", html)
            self.assertIn("MarShawn Lloyd", html)
            self.assertIn('class="slot-row graded"', html)

    def test_real_template_renders_without_crashing(self):
        now = datetime(2026, 9, 26, 16, 0, tzinfo=timezone.utc)
        html = render_page(REAL_TEMPLATE_PATH, SEED_WEEKENDS_DIR, CSV_PATH, CONFIG, now)
        self.assertNotIn("{{TODAY}}", html)
        self.assertNotIn("{{WEEKEND}}", html)
        self.assertNotIn("{{LEGS}}", html)
        self.assertNotIn("{{RECORD}}", html)
        self.assertNotIn("{{UPDATED_AT}}", html)
        self.assertNotIn("{{NEXT_UPDATE}}", html)
        self.assertNotIn("{{SAMPLE_BANNER}}", html)


class TestCardRecord(unittest.TestCase):
    def test_tallies_across_weekends(self):
        cr = compute_card_record(SEED_WEEKENDS_DIR)
        self.assertEqual(cr["tiers12"]["L"], 2)
        self.assertAlmostEqual(cr["net12"], -3.50, delta=1e-9)
        self.assertEqual(cr["lottery"]["L"], 1)

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            cr = compute_card_record(Path(td))
            self.assertEqual(cr["tiers12"], {"W": 0, "L": 0, "P": 0})
            self.assertEqual(cr["net12"], 0.0)


# ---------------------------------------------------------------------------
# Regression: a historical_import claim must be verified, not trusted, and
# --publish must always refuse a historical_import slot outright
# (guardrails / historical-import-bypasses-everything)
# ---------------------------------------------------------------------------

class TestHistoricalImportClaimValidated(unittest.TestCase):
    def test_historical_claim_with_ungraded_bet_raises(self):
        slot = _valid_slot(include_fun_parlay=False, historical=True)
        slot["card"]["easy_bet"]["kickoff"] = PAST_KICKOFF
        # result/net left at the fixture default (None) -- claims
        # historical_import but isn't actually graded yet.
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_historical_claim_with_future_kickoff_raises(self):
        slot = _valid_slot(include_fun_parlay=False, historical=True)
        slot["card"]["easy_bet"]["result"] = "Lost"
        slot["card"]["easy_bet"]["net"] = -3.00
        # kickoff left at the fixture default (FUTURE_KICKOFF) -- claims
        # historical_import for a game that hasn't happened yet.
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_historical_claim_with_no_kickoff_at_all_raises(self):
        slot = _valid_slot(include_fun_parlay=False, historical=True)
        slot["card"]["easy_bet"]["result"] = "Lost"
        slot["card"]["easy_bet"]["net"] = -3.00
        slot["card"]["easy_bet"]["kickoff"] = None
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_genuinely_historical_slot_passes(self):
        slot = _valid_slot(include_fun_parlay=False, historical=True)
        slot["card"]["easy_bet"]["kickoff"] = PAST_KICKOFF
        slot["card"]["easy_bet"]["result"] = "Lost"
        slot["card"]["easy_bet"]["net"] = -3.00
        validate_slot_rules(slot, CONFIG)  # should not raise

    def test_reproduces_the_known_exploit_end_to_end(self):
        # The exact scenario from the audit: a blacklisted player, a wildly
        # over-budget stake, a not-yet-played kickoff, and is_pre_kickoff
        # false -- all wrapped in historical_import: true. This must be
        # refused, not silently published.
        with tempfile.TemporaryDirectory() as td:
            slot = _valid_slot(weekend_id="2026-09-24", slot="sat",
                                include_fun_parlay=False, historical=True)
            slot["card"]["easy_bet"]["selection"] = "MarShawn Lloyd 25+ rush+rec yds"
            slot["card"]["easy_bet"]["stake"] = 999.00
            slot["card"]["easy_bet"]["is_pre_kickoff"] = False
            slot["card"]["easy_bet"]["kickoff"] = FUTURE_KICKOFF
            slot["card"]["easy_bet"]["result"] = None
            slot["card"]["easy_bet"]["net"] = None
            with self.assertRaises(RuleViolation):
                validate_slot_rules(slot, CONFIG)
            now = datetime.now(timezone.utc)
            with self.assertRaises(RuleViolation):
                validate_publish(slot, "2026-09-24", "sat", CONFIG, now, td)


# ---------------------------------------------------------------------------
# Regression: a negative stake must never be able to offset a real one and
# slip under the RULE 1 budget cap (guardrails /
# negative-stake-bypasses-rule1-budget)
# ---------------------------------------------------------------------------

class TestNegativeStakeRejected(unittest.TestCase):
    def test_negative_easy_bet_stake_raises_in_validate_slot_rules(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["stake"] = -100.00
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_negative_fun_parlay_stake_raises_in_validate_slot_rules(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay"]["stake"] = -1.00
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_negative_stake_cannot_offset_budget_at_publish(self):
        with tempfile.TemporaryDirectory() as td:
            # Fresh weekend, thu budget is $1.25 total. A -$100 easy_bet
            # stake would (before the fix) cancel out a real $50
            # fun_parlay stake, reading as -$50 total and sliding under
            # the cap -- the real fun_parlay stake is 40x the true budget.
            slot = _valid_slot(weekend_id="2026-09-24", slot="thu")
            slot["card"]["easy_bet"]["stake"] = -100.00
            slot["card"]["fun_parlay"]["stake"] = 50.00
            now = datetime.now(timezone.utc)
            with self.assertRaises(RuleViolation):
                validate_publish(slot, "2026-09-24", "thu", CONFIG, now, td)


# ---------------------------------------------------------------------------
# Regression: RULE 12's sack-vs-mobile-QB check must catch real market
# label variants, not just the bare literal "sack" (guardrails /
# rule12-sack-market-exact-string-match)
# ---------------------------------------------------------------------------

class TestRule12SackMarketVariants(unittest.TestCase):
    def test_plural_sacks_market_is_caught(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay"]["legs"][0]["market"] = "Sacks"
        slot["card"]["fun_parlay"]["legs"][0]["opp_qb"] = "Jalen Hurts"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_sack_leader_market_is_caught(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay"]["legs"][0]["market"] = "Sack Leader"
        slot["card"]["fun_parlay"]["legs"][0]["opp_qb"] = "Jalen Hurts"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_anytime_sack_market_is_caught(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay"]["legs"][0]["market"] = "Anytime Sack"
        slot["card"]["fun_parlay"]["legs"][0]["opp_qb"] = "Jalen Hurts"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)


# ---------------------------------------------------------------------------
# Regression: RULE 10's game-field comparison must tolerate whitespace,
# case, and team-order drift (guardrails /
# rule10-game-field-exact-string-match)
# ---------------------------------------------------------------------------

class TestRule10GameFieldNormalization(unittest.TestCase):
    def test_extra_whitespace_does_not_bypass(self):
        slot = _valid_slot()
        slot["card"]["easy_bet"]["game"] = "ATL @ GB"
        slot["card"]["easy_bet"]["game_script"] = "ATL leading"
        slot["card"]["fun_parlay"]["legs"][0]["game"] = "ATL @  GB"  # extra space
        slot["card"]["fun_parlay"]["legs"][0]["game_script"] = "ATL leading"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_case_difference_does_not_bypass(self):
        slot = _valid_slot()
        slot["card"]["easy_bet"]["game"] = "ATL @ GB"
        slot["card"]["easy_bet"]["game_script"] = "ATL leading"
        slot["card"]["fun_parlay"]["legs"][0]["game"] = "atl @ gb"
        slot["card"]["fun_parlay"]["legs"][0]["game_script"] = "ATL leading"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_reversed_team_order_does_not_bypass(self):
        slot = _valid_slot()
        slot["card"]["easy_bet"]["game"] = "ATL @ GB"
        slot["card"]["easy_bet"]["game_script"] = "ATL leading"
        slot["card"]["fun_parlay"]["legs"][0]["game"] = "GB @ ATL"
        slot["card"]["fun_parlay"]["legs"][0]["game_script"] = "ATL leading"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_vs_separator_does_not_bypass(self):
        slot = _valid_slot()
        slot["card"]["easy_bet"]["game"] = "ATL @ GB"
        slot["card"]["easy_bet"]["game_script"] = "ATL leading"
        slot["card"]["fun_parlay"]["legs"][0]["game"] = "ATL vs GB"
        slot["card"]["fun_parlay"]["legs"][0]["game_script"] = "ATL leading"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)


# ---------------------------------------------------------------------------
# Regression: RULE 9's blacklist substring check must tolerate whitespace
# variants inside the blacklisted name (guardrails /
# rule9-blacklist-whitespace-bypass)
# ---------------------------------------------------------------------------

class TestRule9BlacklistWhitespaceVariants(unittest.TestCase):
    def test_double_space_does_not_bypass(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["selection"] = "MarShawn  Lloyd 25+ rush+rec yds"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_tab_does_not_bypass(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["selection"] = "MarShawn\tLloyd 25+ rush+rec yds"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_non_breaking_space_does_not_bypass(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["selection"] = "MarShawn Lloyd 25+ rush+rec yds"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)


# ---------------------------------------------------------------------------
# Regression: an unrecognized slot's own internal "slot" field must never
# collapse two distinct on-disk files into one, and a mismatched internal
# field must fail loudly (guardrails / slot-file-dedup-by-body-field-drops-checks,
# stoploss / slot-file-metadata-trusted-over-filename [build.py side])
# ---------------------------------------------------------------------------

class TestLoadWeekendSlotsIdentity(unittest.TestCase):
    def test_two_files_with_same_internal_slot_are_not_collapsed(self):
        with tempfile.TemporaryDirectory() as td:
            sat = _valid_slot(weekend_id="2099-01-01", slot="sat", include_lottery_ticket=True)
            _write_slot(td, sat)
            d = Path(td) / "2099-01-01"
            # Its own filename is sat_v2.json (e.g. a retry/draft file) and
            # its internal "slot" field correctly names itself -- this is
            # NOT a mismatch, just two distinct, correctly self-labeled
            # files that must both be counted, not collapsed into one.
            sat_v2 = _valid_slot(weekend_id="2099-01-01", slot="sat_v2", include_lottery_ticket=True)
            with open(d / "sat_v2.json", "w", encoding="utf-8") as f:
                json.dump(sat_v2, f)
            loaded = build._load_weekend_slots(td, "2099-01-01")
            self.assertEqual(len(loaded), 2)
            lottery_count = sum(1 for s in loaded.values() if (s.get("card") or {}).get("lottery_ticket"))
            self.assertEqual(lottery_count, 2)

    def test_mismatched_internal_slot_field_raises(self):
        with tempfile.TemporaryDirectory() as td:
            slot = _valid_slot(weekend_id="2099-01-01", slot="thu")
            d = Path(td) / "2099-01-01"
            d.mkdir(parents=True)
            with open(d / "sun.json", "w", encoding="utf-8") as f:
                json.dump(slot, f)  # body says "thu", filename says "sun"
            with self.assertRaises(RuleViolation):
                build._load_weekend_slots(td, "2099-01-01")


# ---------------------------------------------------------------------------
# Regression: RULE 11's easy_bet estimated_prob must actually be derived
# from its own cited prob_sources, not an unrelated number (render /
# estimated-prob-not-checked-against-its-own-sources)
# ---------------------------------------------------------------------------

class TestRule11EstimatedProbMatchesSources(unittest.TestCase):
    def test_estimated_prob_far_from_source_average_raises(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["prob_sources"] = [
            {"name": "A", "prob": 0.56}, {"name": "B", "prob": 0.48},
        ]
        slot["card"]["easy_bet"]["estimated_prob"] = 0.95
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_estimated_prob_matching_source_average_passes(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["prob_sources"] = [
            {"name": "A", "prob": 0.56}, {"name": "B", "prob": 0.48},
        ]
        slot["card"]["easy_bet"]["estimated_prob"] = 0.52  # exact average
        validate_slot_rules(slot, CONFIG)  # should not raise

    def test_estimated_prob_within_tolerance_passes(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["easy_bet"]["prob_sources"] = [
            {"name": "A", "prob": 0.56}, {"name": "B", "prob": 0.48},
        ]
        slot["card"]["easy_bet"]["estimated_prob"] = 0.53  # avg 0.52, within tolerance
        validate_slot_rules(slot, CONFIG)  # should not raise


# ---------------------------------------------------------------------------
# Regression: a slot's own "date" field must be validated at build time
# (RuleViolation), not left to crash rendering later (grading /
# unvalidated-date-field-crashes-render)
# ---------------------------------------------------------------------------

class TestSlotDateFieldValidated(unittest.TestCase):
    def test_malformed_date_raises(self):
        slot = _valid_slot()
        slot["date"] = "not-a-date"
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_missing_date_raises(self):
        slot = _valid_slot()
        del slot["date"]
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_valid_date_passes(self):
        slot = _valid_slot()
        validate_slot_rules(slot, CONFIG)  # should not raise


# ---------------------------------------------------------------------------
# Regression: RULE 2's kickoff-future check must reject a naive (no UTC
# offset) timestamp instead of silently assuming UTC, and must catch an
# authored offset that doesn't match the real America/New_York offset for
# that date (grading / kickoff-missing-tz-offset-breaks-publish,
# stoploss / no-cross-check-on-kickoff-utc-offset-near-dst)
# ---------------------------------------------------------------------------

class TestKickoffOffsetValidation(unittest.TestCase):
    def test_naive_kickoff_with_no_offset_raises(self):
        with tempfile.TemporaryDirectory() as td:
            slot = _valid_slot(weekend_id="2026-09-24", slot="sat", include_fun_parlay=False)
            slot["card"]["easy_bet"]["kickoff"] = "2099-01-01T13:00:00"  # no offset at all
            slot["card"]["easy_bet"]["stake"] = 0.10
            now = datetime(2026, 9, 26, 13, 30, 0, tzinfo=timezone.utc)
            with self.assertRaises(RuleViolation):
                validate_publish(slot, "2026-09-24", "sat", CONFIG, now, td)

    def test_wrong_dst_offset_letting_a_live_game_through_is_caught(self):
        with tempfile.TemporaryDirectory() as td:
            # Real kickoff: 2026-09-26 1:00 PM ET (EDT, real offset
            # -04:00), i.e. 17:00 UTC. Authored with the WRONG offset
            # -05:00, which parses to 18:00 UTC -- an hour later than the
            # real kickoff, so an unvalidated offset would let a game that
            # already kicked off 30 minutes ago look "still upcoming".
            slot = _valid_slot(weekend_id="2026-09-24", slot="sat", include_fun_parlay=False)
            slot["card"]["easy_bet"]["kickoff"] = "2026-09-26T13:00:00-05:00"
            slot["card"]["easy_bet"]["stake"] = 0.10
            now = datetime(2026, 9, 26, 17, 30, 0, tzinfo=timezone.utc)
            with self.assertRaises(RuleViolation):
                validate_publish(slot, "2026-09-24", "sat", CONFIG, now, td)

    def test_correct_offset_passes(self):
        with tempfile.TemporaryDirectory() as td:
            slot = _valid_slot(weekend_id="2026-09-24", slot="sat", include_fun_parlay=False)
            slot["card"]["easy_bet"]["kickoff"] = FUTURE_KICKOFF  # -05:00, correct for January
            slot["card"]["easy_bet"]["stake"] = 0.10
            now = datetime.now(timezone.utc)
            validate_publish(slot, "2026-09-24", "sat", CONFIG, now, td)  # should not raise


# ---------------------------------------------------------------------------
# Regression: a malformed/unreadable slot JSON file must fail loudly and
# name the offending path (grading / unguarded-json-load-crashes-all-clis)
# ---------------------------------------------------------------------------

class TestMalformedJsonNamesPath(unittest.TestCase):
    def test_compute_card_record_names_bad_file(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "2020-01-02"
            d.mkdir(parents=True)
            bad_path = d / "bad.json"
            bad_path.write_text("", encoding="utf-8")
            with self.assertRaises(RuleViolation) as ctx:
                compute_card_record(Path(td))
            self.assertIn(str(bad_path), str(ctx.exception))

    def test_load_weekend_slots_names_bad_file(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "2020-01-02"
            d.mkdir(parents=True)
            bad_path = d / "bad.json"
            bad_path.write_text("not json", encoding="utf-8")
            with self.assertRaises(RuleViolation) as ctx:
                build._load_weekend_slots(td, "2020-01-02")
            self.assertIn(str(bad_path), str(ctx.exception))


# ---------------------------------------------------------------------------
# Regression: the Record panel must surface a manually-known caveat (e.g.
# real wins not yet logged), not just no_data_rows (render /
# record-note-hides-known-wrong-wins)
# ---------------------------------------------------------------------------

class TestRecordCaveats(unittest.TestCase):
    def test_config_caveats_rendered_as_record_notes(self):
        config = dict(CONFIG)
        config["record_caveats"] = ["9/14 and 9/17 wins not logged yet."]
        window = {"weekend_id": "2026-09-24"}
        html = render_record(config, CSV_PATH, SEED_WEEKENDS_DIR, window)
        self.assertIn('<p class="record-note">9/14 and 9/17 wins not logged yet.</p>', html)

    def test_no_caveats_key_does_not_crash(self):
        window = {"weekend_id": "2026-09-24"}
        html = render_record(CONFIG, CSV_PATH, SEED_WEEKENDS_DIR, window)  # should not raise
        self.assertIn('class="stat-grid"', html)


# ---------------------------------------------------------------------------
# Regression: an Edge value must always show a leading sign (matching
# templates/CONTRACT.md's "+3.5%" examples), not just for negative edges
# (render / edge-missing-contract-mandated-sign)
# ---------------------------------------------------------------------------

class TestEdgeSignage(unittest.TestCase):
    def setUp(self):
        from zoneinfo import ZoneInfo
        self.tz = ZoneInfo("America/New_York")

    def test_positive_edge_shows_plus_sign_in_card_bet(self):
        bet = _valid_easy_bet()  # dk_odds=-150, estimated_prob=0.62 -> implied 60.0%, edge +2.0%
        html = render_card_bet("easy_bet", bet, self.tz)
        self.assertIn('>+2.0%<', html)

    def test_positive_edge_shows_plus_sign_in_leg_entry(self):
        leg = {"selection": "Test Leg", "dk_odds": -150, "estimated_prob": 0.70,
               "market": "test", "reason": "r"}
        html = render_leg_entry(leg)  # implied 60.0%, edge +10.0%
        self.assertIn('>+10.0%<', html)

    def test_negative_edge_still_shows_minus_sign(self):
        bet = _valid_easy_bet(estimated_prob=0.50)  # implied 60.0%, edge -10.0%
        html = render_card_bet("easy_bet", bet, self.tz)
        self.assertIn('>-10.0%<', html)


# ---------------------------------------------------------------------------
# Regression: numbers rendered from a historical_import slot (reconstructed
# after the fact) must say so on the page, not look like live research
# (render / historical-import-numbers-shown-as-fact-outside-the-tap)
# ---------------------------------------------------------------------------

class TestHistoricalReconstructedTag(unittest.TestCase):
    def setUp(self):
        from zoneinfo import ZoneInfo
        self.tz = ZoneInfo("America/New_York")

    def test_historical_bet_shows_reconstructed_tag(self):
        bet = _valid_easy_bet(result="Lost", net=-3.00)
        html = render_card_bet("easy_bet", bet, self.tz, historical=True)
        self.assertIn("reconstructed-tag", html)

    def test_non_historical_bet_has_no_reconstructed_tag(self):
        bet = _valid_easy_bet()
        html = render_card_bet("easy_bet", bet, self.tz, historical=False)
        self.assertNotIn("reconstructed-tag", html)

    def test_historical_fun_parlay_leg_probs_flagged(self):
        bet = _valid_fun_parlay(result="Lost", net=-1.25)
        html = render_card_bet("fun_parlay", bet, self.tz, historical=True)
        self.assertIn("leg-prob reconstructed", html)

    def test_real_historical_seed_shows_reconstructed_tag_on_today_panel(self):
        now = datetime(2026, 9, 24, 16, 0, tzinfo=timezone.utc)
        window = slate.current_window(now, CONFIG)
        html = render_today(window, CONFIG, SEED_WEEKENDS_DIR, now)
        self.assertIn("reconstructed-tag", html)


class TestOddsEstimatedLabel(unittest.TestCase):
    """A DK price that was never recorded must never render as if it were
    the real line -- odds_estimated: true adds "est." everywhere odds show."""

    def setUp(self):
        from zoneinfo import ZoneInfo
        self.tz = ZoneInfo("America/New_York")

    def test_card_bet_odds_marked_est(self):
        bet = _valid_fun_parlay(result="Lost", net=-1.25)
        bet["odds_estimated"] = True
        html = render_card_bet("fun_parlay", bet, self.tz, historical=True)
        self.assertIn(" est.</span>", html)

    def test_card_bet_odds_unmarked_by_default(self):
        html = render_card_bet("easy_bet", _valid_easy_bet(), self.tz)
        self.assertNotIn(" est.</span>", html)

    def test_real_seed_weekend_rows_mark_unrecorded_prices(self):
        # The 9/24 fun parlay (+101) and lottery (+15300) prices were never
        # recorded by DK; the Lloyd easy bet (-216) was.
        seed = json.loads((SEED_WEEKENDS_DIR / "2026-09-24" / "thu.json").read_text())
        self.assertTrue(seed["card"]["fun_parlay"].get("odds_estimated"))
        self.assertTrue(seed["card"]["lottery_ticket"].get("odds_estimated"))
        self.assertFalse(seed["card"]["easy_bet"].get("odds_estimated", False))
        # Render the Thursday card itself -- which slot the Today tab shows
        # depends on whatever later slot files exist, so don't go through it.
        html = render_card_section(seed["card"], self.tz, historical=True)
        self.assertIn("+101 est.", html)
        self.assertIn("+15300 est.", html)
        self.assertNotIn("-216 est.", html)



# ---------------------------------------------------------------------------
# Tier 3 as a second Fun Parlay (when the slot has no Lottery Ticket)
# ---------------------------------------------------------------------------

def _second_fun_parlay(**overrides):
    bet = _valid_fun_parlay(id="fun-parlay-2", **overrides)
    bet["legs"] = [
        {"selection": "Leg 3", "estimated_prob": 0.60, "reason": "x", "game": "EEE @ FFF",
         "kickoff": FUTURE_KICKOFF, "game_script": "neutral"},
        {"selection": "Leg 4", "estimated_prob": 0.62, "reason": "x", "game": "GGG @ HHH",
         "kickoff": FUTURE_KICKOFF, "game_script": "neutral"},
    ]
    return bet


class TestSecondFunParlay(unittest.TestCase):
    def test_valid_second_fun_parlay_passes(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay_2"] = _second_fun_parlay()
        validate_slot_rules(slot, CONFIG)  # should not raise

    def test_needs_first_fun_parlay(self):
        slot = _valid_slot(include_fun_parlay=False)
        slot["card"]["fun_parlay_2"] = _second_fun_parlay()
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_cannot_share_slot_with_lottery_ticket(self):
        slot = _valid_slot(include_lottery_ticket=True)
        slot["card"]["fun_parlay_2"] = _second_fun_parlay()
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_same_legs_as_first_parlay_rejected(self):
        slot = _valid_slot()
        slot["card"]["fun_parlay_2"] = _valid_fun_parlay(id="fun-parlay-2")
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_leg_floor_applies(self):
        slot = _valid_slot()
        second = _second_fun_parlay()
        second["legs"][0]["estimated_prob"] = 0.50
        slot["card"]["fun_parlay_2"] = second
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_game_script_conflict_with_easy_bet(self):
        slot = _valid_slot()
        slot["card"]["easy_bet"]["game"] = "ATL @ GB"
        slot["card"]["easy_bet"]["game_script"] = "ATL leading"
        second = _second_fun_parlay()
        second["legs"][0]["game"] = "ATL @ GB"
        second["legs"][0]["game_script"] = "GB trailing"
        slot["card"]["fun_parlay_2"] = second
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, CONFIG)

    def test_blacklist_applies(self):
        slot = _valid_slot()
        second = _second_fun_parlay()
        second["legs"][0]["selection"] = "MarShawn Lloyd 25+ rush yds"
        slot["card"]["fun_parlay_2"] = second
        config = dict(CONFIG, blacklist=["MarShawn Lloyd"])
        with self.assertRaises(RuleViolation):
            validate_slot_rules(slot, config)

    def test_stake_counts_toward_slot_budget(self):
        with tempfile.TemporaryDirectory() as td:
            # Fresh weekend, thu budget is $1.25 total: 0.75 + 0.45 fits,
            # adding a 0.10 second parlay pushes it over.
            slot = _valid_slot(weekend_id="2026-09-24", slot="thu")
            slot["card"]["easy_bet"]["stake"] = 0.75
            slot["card"]["fun_parlay"]["stake"] = 0.45
            now = datetime.now(timezone.utc)
            validate_publish(slot, "2026-09-24", "thu", CONFIG, now, td)  # fits
            slot["card"]["fun_parlay_2"] = _second_fun_parlay(stake=0.10)
            with self.assertRaises(RuleViolation):
                validate_publish(slot, "2026-09-24", "thu", CONFIG, now, td)

    def test_counts_in_weekend_pacing(self):
        with tempfile.TemporaryDirectory() as td:
            slot = _valid_slot(weekend_id="2026-09-24", slot="thu")
            slot["card"]["easy_bet"].update(stake=0.50, result="Lost", net=-0.50)
            slot["card"]["fun_parlay"].update(stake=0.30, result="Lost", net=-0.30)
            slot["card"]["fun_parlay_2"] = _second_fun_parlay(stake=0.20)
            _write_slot(td, slot)
            status = slate.weekend_status("2026-09-24", CONFIG, weekends_dir=td)
            self.assertAlmostEqual(status["settled_net"], -0.80)
            self.assertAlmostEqual(status["open_stakes"], 0.20)
            self.assertEqual(status["open_bets"][0]["tier"], "fun_parlay_2")

    def test_renders_as_tier_three(self):
        card = {"easy_bet": _valid_easy_bet(), "fun_parlay": _valid_fun_parlay(),
                "fun_parlay_2": _second_fun_parlay()}
        html = render_card_section(card, ZoneInfo("America/New_York"))
        self.assertIn("3 · Fun Parlay #2", html)
        self.assertLess(html.index("2 · Fun Parlay"), html.index("3 · Fun Parlay #2"))

    def test_counts_in_card_record(self):
        with tempfile.TemporaryDirectory() as td:
            slot = _valid_slot(weekend_id="2026-09-24", slot="thu", include_fun_parlay=True)
            slot["card"]["easy_bet"].update(result="Lost", net=-3.0)
            slot["card"]["fun_parlay"].update(result="Lost", net=-2.0)
            slot["card"]["fun_parlay_2"] = _second_fun_parlay(stake=1.0, result="Won", net=1.5)
            _write_slot(td, slot)
            rec = compute_card_record(td)
            self.assertEqual(rec["tiers12"], {"W": 1, "L": 2, "P": 0})
            self.assertAlmostEqual(rec["net12"], -3.5)


# ---------------------------------------------------------------------------
# Legs tab: props, best value, market filter
# ---------------------------------------------------------------------------

def _prop(selection, price, prob, market="receptions", book=None, sources=2, **extra):
    leg = {"selection": selection, "market": market, "estimated_prob": prob, "player": "P",
           "game": "AAA @ BBB", "kickoff": FUTURE_KICKOFF, "reason": "x",
           "prob_sources": [{"name": f"S{i}", "prob": prob} for i in range(sources)]}
    if book:
        leg.update(odds=price, book=book)
    else:
        leg["dk_odds"] = price
    leg.update(extra)
    return leg


class TestLegsTabProps(unittest.TestCase):
    def test_best_value_needs_positive_edge_and_two_sources(self):
        good = _prop("Good 5+ rec", -120, 0.60)            # implied 54.5% -> +5.5
        better = _prop("Better 1+ sack", 110, 0.55, market="sacks", opp_qb="X")  # 47.6% -> +7.4
        negative = _prop("Neg 50+ yds", -200, 0.60)         # 66.7% -> negative
        single = _prop("Single TD", 150, 0.50, market="anytime_td", sources=1)
        flagged = _prop("Flagged", 150, 0.50, single_source=True)
        best = build.best_value_legs([good, better, negative, single, flagged])
        self.assertEqual([l["selection"] for l in best], ["Better 1+ sack", "Good 5+ rec"])

    def test_other_book_price_is_labelled_not_passed_off_as_dk(self):
        html = render_leg_entry(_prop("CMC anytime TD", -225, 0.62, market="anytime_td", book="Fanatics"))
        self.assertIn("-225", html)
        self.assertIn("Fanatics price", html)
        self.assertIn('data-cat="TD"', html)

    def test_single_source_is_flagged(self):
        html = render_leg_entry(_prop("X", -110, 0.6, sources=1))
        self.assertIn("1 source only", html)

    def test_legs_panel_has_best_value_and_filter_chips(self):
        with tempfile.TemporaryDirectory() as td:
            slot = _valid_slot(weekend_id="2026-09-24", slot="sun")
            slot["date"] = "2026-09-27"
            slot["leg_bank"] = [
                _prop("Good 5+ rec", -120, 0.60),
                _prop("Team ML", -150, 0.55, market="moneyline"),
                _prop("Kicker 2+ FG", -130, 0.60, market="special_teams"),
            ]
            _write_slot(td, slot)
            now = datetime(2026, 9, 26, 21, 0, tzinfo=timezone.utc)
            window = slate.current_window(now, CONFIG)
            html = build.render_legs(window, CONFIG, td, now)
            self.assertIn("Best value", html)
            for chip in ("All", "Receiving", "Game lines", "Kicker"):
                self.assertIn(f">{chip}</button>", html)

if __name__ == "__main__":
    unittest.main()
