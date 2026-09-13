"""Odds math helpers for the betting cheat sheet.

Python 3 stdlib only — no third-party packages.
"""

from functools import reduce


def american_to_implied_prob(odds: int) -> float:
    """Convert American odds to an implied probability (0-1 fraction).

    Negative odds (favorites): abs(odds) / (abs(odds) + 100)
    Positive odds (underdogs): 100 / (odds + 100)

    Raises ValueError if odds == 0, which is not a valid American odds value.
    """
    if odds == 0:
        raise ValueError("0 is not a valid American odds value")

    if odds < 0:
        return abs(odds) / (abs(odds) + 100)

    return 100 / (odds + 100)


def parlay_implied_prob(leg_probs: list[float]) -> float:
    """Combine independent leg implied probabilities into a parlay probability.

    This is the naive product of the individual leg probabilities — i.e.
    "before DK's correlation adjustment" per the house plan. No correlation
    or de-vig adjustment is applied here.
    """
    return reduce(lambda acc, p: acc * p, leg_probs, 1.0)


def edge(estimated_prob: float, implied_prob: float) -> float:
    """Return the bettor's edge: estimated probability minus the implied probability."""
    return estimated_prob - implied_prob


def format_prob(p: float) -> str:
    """Format a probability fraction (e.g. 0.524) as a percentage string (e.g. "52.4%")."""
    return f"{p * 100:.1f}%"
