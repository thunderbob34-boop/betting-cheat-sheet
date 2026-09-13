import unittest

from odds import (
    american_to_implied_prob,
    parlay_implied_prob,
    edge,
    format_prob,
)


class TestAmericanToImpliedProb(unittest.TestCase):
    def test_negative_110(self):
        self.assertAlmostEqual(american_to_implied_prob(-110), 0.524, delta=0.001)
        self.assertEqual(format_prob(american_to_implied_prob(-110)), "52.4%")

    def test_positive_200(self):
        self.assertAlmostEqual(american_to_implied_prob(200), 0.333, delta=0.001)
        self.assertEqual(format_prob(american_to_implied_prob(200)), "33.3%")

    def test_negative_455(self):
        self.assertAlmostEqual(american_to_implied_prob(-455), 0.820, delta=0.001)
        self.assertEqual(format_prob(american_to_implied_prob(-455)), "82.0%")

    def test_positive_150(self):
        # 100 / (150 + 100) = 0.4
        self.assertAlmostEqual(american_to_implied_prob(150), 0.400, delta=0.001)
        self.assertEqual(format_prob(american_to_implied_prob(150)), "40.0%")

    def test_zero_raises_value_error(self):
        with self.assertRaises(ValueError):
            american_to_implied_prob(0)


class TestParlayImpliedProb(unittest.TestCase):
    def test_two_legs(self):
        self.assertAlmostEqual(parlay_implied_prob([0.6, 0.7]), 0.42, delta=0.001)

    def test_single_leg(self):
        self.assertAlmostEqual(parlay_implied_prob([0.524]), 0.524, delta=0.001)

    def test_empty_list_is_identity(self):
        self.assertAlmostEqual(parlay_implied_prob([]), 1.0, delta=0.001)


class TestEdge(unittest.TestCase):
    def test_positive_edge(self):
        self.assertAlmostEqual(edge(0.65, 0.55), 0.10, delta=0.001)

    def test_negative_edge(self):
        self.assertAlmostEqual(edge(0.40, 0.524), -0.124, delta=0.001)


class TestFormatProb(unittest.TestCase):
    def test_format_rounds_to_one_decimal(self):
        self.assertEqual(format_prob(0.5238095), "52.4%")

    def test_format_exact(self):
        self.assertEqual(format_prob(0.42), "42.0%")


if __name__ == "__main__":
    unittest.main()
