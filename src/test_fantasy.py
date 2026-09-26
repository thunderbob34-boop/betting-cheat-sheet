import unittest

import fantasy


def _p(key, pos, proj, **extra):
    p = {"key": key, "name": key, "pos": pos, "team": "AAA", "status": "ACTIVE", "bye": False,
         "locked": False, "proj": proj, "espn": proj, "rotowire": proj, "disagree": False}
    p.update(extra)
    return p


def _roster():
    return [
        _p("QB1", "QB", 20), _p("QB2", "QB", 15),
        _p("RB1", "RB", 18), _p("RB2", "RB", 14), _p("RB3", "RB", 13),
        _p("WR1", "WR", 17), _p("WR2", "WR", 12), _p("WR3", "WR", 9),
        _p("TE1", "TE", 10), _p("TE2", "TE", 6),
        _p("K1", "K", 8), _p("DST1", "D/ST", 7),
    ]


def _starter(result, slot, n=0):
    return [e["player"]["key"] for e in result["starters"] if e["slot"] == slot][n]


class TestCombineProjection(unittest.TestCase):
    def test_average_of_two(self):
        pts, used, disagree = fantasy.combine_projection(10.0, 14.0)
        self.assertEqual(pts, 12.0)
        self.assertEqual(used, ["ESPN", "Rotowire"])
        self.assertFalse(disagree)

    def test_one_source(self):
        self.assertEqual(fantasy.combine_projection(None, 9.0)[:2], (9.0, ["Rotowire"]))

    def test_disagreement_flag(self):
        self.assertTrue(fantasy.combine_projection(4.0, 12.0)[2])


class TestNameKey(unittest.TestCase):
    def test_suffix_punctuation_and_team_spelling(self):
        self.assertEqual(fantasy.name_key("Travis Etienne Jr.", "NO", "RB"),
                         fantasy.name_key("Travis Etienne", "NO", "RB"))
        self.assertEqual(fantasy.name_key("Terry McLaurin", "WSH", "WR"),
                         fantasy.name_key("Terry McLaurin", "WAS", "WR"))
        self.assertEqual(fantasy.name_key("Ja'Marr Chase", "CIN", "WR"), "jamarr chase|CIN|WR")


class TestOptimizeLineup(unittest.TestCase):
    def test_best_players_fill_slots_flex_gets_best_leftover(self):
        r = fantasy.optimize_lineup(_roster())
        self.assertEqual(_starter(r, "QB"), "QB1")
        self.assertEqual({_starter(r, "RB", 0), _starter(r, "RB", 1)}, {"RB1", "RB2"})
        self.assertEqual(_starter(r, "FLEX"), "RB3")  # 13 beats WR3 9 and TE2 6
        self.assertEqual([b["key"] for b in r["bench"]], ["QB2", "WR3", "TE2"])

    def test_out_and_bye_players_sit(self):
        roster = _roster()
        roster[2]["status"] = "OUT"      # RB1
        roster[5]["bye"] = True          # WR1
        r = fantasy.optimize_lineup(roster)
        starters = {e["player"]["key"] for e in r["starters"]}
        self.assertNotIn("RB1", starters)
        self.assertNotIn("WR1", starters)
        self.assertIn("WR3", starters)

    def test_doubtful_counts_as_zero(self):
        roster = _roster()
        roster[8]["status"] = "DOUBTFUL"  # TE1
        r = fantasy.optimize_lineup(roster)
        self.assertEqual(_starter(r, "TE"), "TE2")

    def test_close_call_and_questionable_notes(self):
        roster = _roster()
        roster[9]["proj"] = 9.5           # bench TE2 now within 0.5 of TE1 (10)
        roster[0]["status"] = "QUESTIONABLE"
        notes = " ".join(fantasy.optimize_lineup(roster)["notes"])
        self.assertIn("Close call", notes)
        self.assertIn("QB1 is questionable", notes)

    def test_locked_starter_stays_put_and_locked_bench_stays_benched(self):
        roster = _roster()
        roster[3].update(locked=True, current_slot="RB", proj=2.0)   # RB2 played Thursday, started
        roster[7].update(locked=True, current_slot="BENCH", proj=30)  # WR3 played, was benched
        r = fantasy.optimize_lineup(roster)
        rbs = {_starter(r, "RB", 0), _starter(r, "RB", 1)}
        self.assertIn("RB2", rbs)
        starters = {e["player"]["key"] for e in r["starters"]}
        self.assertNotIn("WR3", starters)

    def test_missing_position_noted(self):
        roster = [p for p in _roster() if p["pos"] != "K"]
        r = fantasy.optimize_lineup(roster)
        self.assertIsNone([e for e in r["starters"] if e["slot"] == "K"][0]["player"])
        self.assertTrue(any("No eligible player for K" in n for n in r["notes"]))



class TestManualRoster(unittest.TestCase):
    POOL = [
        {"id": 1, "fullName": "Travis Etienne Jr.", "defaultPositionId": 2},
        {"id": -16007, "fullName": "Broncos D/ST", "defaultPositionId": 16},
        {"id": 3, "fullName": "Bijan Robinson", "defaultPositionId": 2},
    ]

    def test_names_suffixes_and_defense_aliases(self):
        entries, missing = fantasy.manual_roster_entries(
            ["Travis Etienne", "Broncos Defense", {"name": "Bijan Robinson", "slot": "RB"}, "Nobody Real"],
            self.POOL)
        ids = [e["playerPoolEntry"]["player"]["id"] for e in entries]
        self.assertEqual(ids, [1, -16007, 3])
        self.assertEqual(entries[2]["lineupSlotId"], 2)    # RB
        self.assertEqual(entries[0]["lineupSlotId"], 20)   # bench by default
        self.assertEqual(missing, ["Nobody Real"])


class TestLineupChanges(unittest.TestCase):
    def test_reports_real_swaps_not_rb_flex_relabels(self):
        roster = _roster()
        for p in roster:
            p["current_slot"] = "BENCH"
        cur = {"QB1": "QB", "RB1": "RB", "RB3": "RB", "RB2": "FLEX", "WR1": "WR", "WR3": "WR",
               "TE1": "TE", "K1": "K", "DST1": "D/ST"}
        for p in roster:
            p["current_slot"] = cur.get(p["key"], "BENCH")
        r = fantasy.optimize_lineup(roster)
        changes = fantasy.lineup_changes(r["starters"], roster)
        self.assertEqual(changes, ["Start WR2 (12.0) instead of WR3 (9.0)"])

    def test_no_changes_when_already_optimal(self):
        roster = _roster()
        r = fantasy.optimize_lineup(roster)
        slots = {e["player"]["key"]: e["slot"] for e in r["starters"]}
        for p in roster:
            p["current_slot"] = slots.get(p["key"], "BENCH")
        self.assertEqual(fantasy.lineup_changes(r["starters"], roster), [])

if __name__ == "__main__":
    unittest.main()
