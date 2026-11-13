"""Odds maths: implied probability, removing the bookmaker margin, EV and Kelly."""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq


def implied(odds) -> np.ndarray:
    return 1.0 / np.asarray(odds, dtype=float)


def overround(odds) -> float:
    """Bookmaker margin. 1.05 means a 5% margin built into the prices."""
    return float(implied(odds).sum())


def devig(odds, method: str = "power") -> np.ndarray:
    """Turn bookmaker odds into fair probabilities that sum to 1.

    'multiplicative' spreads the margin evenly. 'power' (default) puts more
    of it on longshots, which matches how books actually shade prices.
    """
    q = implied(odds)
    if method == "multiplicative":
        return q / q.sum()
    if method == "power":
        k = brentq(lambda k: (q ** k).sum() - 1.0, 0.5, 3.0)
        return q ** k
    raise ValueError(method)


def expected_value(p: float, odds: float) -> float:
    """Profit per 1 unit staked. 0.05 = +5% EV."""
    return p * odds - 1.0


def kelly(p: float, odds: float, fraction: float = 0.25, cap: float = 0.03) -> float:
    """Fraction of bankroll to stake. Quarter-Kelly, capped, never negative."""
    b = odds - 1.0
    full = (p * b - (1 - p)) / b
    return float(np.clip(full * fraction, 0.0, cap))
