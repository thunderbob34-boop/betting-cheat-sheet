import unittest

import sharp

MATCHUPS = [
    {"id": 1, "type": "matchup", "startTime": "2026-10-02T00:15:00Z",
     "participants": [{"alignment": "home", "name": "Pittsburgh Steelers"}, {"alignment": "away", "name": "Cleveland Browns"}]},
    {"id": 2, "type": "matchup", "startTime": "2026-10-04T17:00:00Z",
     "participants": [{"alignment": "home", "name": "Dallas Cowboys"}, {"alignment": "away", "name": "Houston Texans"}]},
    {"id": 10, "type": "special", "parent": {"id": 1}, "special": {"description": "Jaylen Warren Total Receptions"},
     "participants": [{"id": 101, "name": "Over"}, {"id": 102, "name": "Under"}]},
]
MARKETS = [
    {"matchupId": 1, "period": 0, "status": "open", "isAlternate": False, "type": "moneyline",
     "prices": [{"designation": "home", "price": -150}, {"designation": "away", "price": 130}]},
    {"matchupId": 1, "period": 0, "status": "open", "isAlternate": True, "type": "spread",
     "prices": [{"designation": "home", "points": -7.5, "price": 200}, {"designation": "away", "points": 7.5, "price": -240}]},
    {"matchupId": 1, "period": 0, "status": "open", "isAlternate": False, "type": "total",
     "prices": [{"designation": "over", "points": 38.5, "price": -110}, {"designation": "under", "points": 38.5, "price": -110}]},
    {"matchupId": 10, "period": 0, "type": "total",  # prop markets carry no status
     "prices": [{"participantId": 101, "points": 3.5, "price": -120}, {"participantId": 102, "points": 3.5, "price": 100}]},
]


class TestPinnacleBoard(unittest.TestCase):
    def setUp(self):
        self._orig = sharp._pinnacle
        sharp._pinnacle = lambda path: MATCHUPS if path.endswith("/matchups") else MARKETS

    def tearDown(self):
        sharp._pinnacle = self._orig

    def test_devigs_main_lines_and_props_skipping_alternates(self):
        board = sharp.pinnacle_board()
        game = next(g for g in board["games"] if g["game"] == "Cleveland Browns @ Pittsburgh Steelers")
        self.assertEqual([l["market"] for l in game["lines"]], ["moneyline", "total"])
        ml = game["lines"][0]["sides"]
        self.assertAlmostEqual(ml[0]["fair_prob"] + ml[1]["fair_prob"], 1.0, places=3)
        self.assertEqual(game["lines"][1]["sides"][0]["fair_prob"], 0.5)
        self.assertEqual(game["props"][0]["prop"], "Jaylen Warren Total Receptions")
        self.assertEqual(game["props"][0]["sides"][0]["side"], "Over 3.5")

    def test_team_filter(self):
        board = sharp.pinnacle_board(team="texans")
        self.assertEqual([g["game"] for g in board["games"]], ["Houston Texans @ Dallas Cowboys"])
