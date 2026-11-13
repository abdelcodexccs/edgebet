"""Turn model probabilities + bookmaker odds into bet decisions."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .odds import devig, expected_value, kelly

OUTCOMES = ("home", "draw", "away")


@dataclass
class Rules:
    # Defaults = the settings that did best on 2012-16 EPL and were then
    # checked on unseen 2016-18 data (see README for the honest numbers).
    min_ev: float = 0.05        # only bet when EV >= +5%
    min_odds: float = 1.50
    max_odds: float = 2.50       # longshots were where the model lost most
    allow_draw: bool = False     # draws are where 1X2 models are weakest
    blend: float = 0.5           # weight on model vs sharp market (1.0 = model only)
    kelly_fraction: float = 0.25
    max_stake: float = 0.03      # never more than 3% of bankroll on one bet
    min_matches: int = 10        # skip teams the model has barely seen


def probabilities(model_p: dict, sharp_odds=None, blend: float = 1.0) -> np.ndarray:
    """Final probability = blend * model + (1 - blend) * fair sharp market."""
    p = np.array([model_p[o] for o in OUTCOMES])
    if sharp_odds is not None and blend < 1.0 and np.all(np.isfinite(sharp_odds)):
        p = blend * p + (1 - blend) * devig(sharp_odds)
    return p / p.sum()


def pick(p: np.ndarray, odds, rules: Rules) -> dict | None:
    """Best value bet for one match, or None if nothing clears the rules."""
    best = None
    for k, name in enumerate(OUTCOMES):
        o = float(odds[k])
        if not np.isfinite(o) or not (rules.min_odds <= o <= rules.max_odds):
            continue
        if name == "draw" and not rules.allow_draw:
            continue
        ev = expected_value(p[k], o)
        if ev >= rules.min_ev and (best is None or ev > best["ev"]):
            best = {"outcome": name, "k": k, "odds": o, "p": float(p[k]),
                    "implied": 1 / o, "ev": ev,
                    "stake_frac": kelly(p[k], o, rules.kelly_fraction, rules.max_stake)}
    return best
