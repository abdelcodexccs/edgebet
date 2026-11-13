"""Bet types beyond 'team to win': double chance, draw no bet, over/under goals.

Pick codes stored in the ledger's `pick` column:
  home / draw / away        1X2
  1X / X2                   double chance (home or draw / away or draw)
  dnb_home / dnb_away       draw no bet (stake back on a draw)
  over2.5 / under2.5        total goals

Double chance and draw no bet are priced from the best 1X2 prices by splitting the stake
over two outcomes, which is a real bet anyone can place (and usually beats a bookmaker's
own double-chance price). Bookmakers' own markets work the same way.
"""
from __future__ import annotations

import numpy as np

from .odds import devig, kelly

NAMES = {"1X": "Double chance", "X2": "Double chance", "dnb_home": "Draw no bet", "dnb_away": "Draw no bet",
         "over2.5": "Total goals", "under2.5": "Total goals"}


def label(code: str, home: str, away: str) -> str:
    return {"home": f"{home} to win", "away": f"{away} to win", "draw": "Draw",
            "1X": f"{home} or Draw", "X2": f"{away} or Draw",
            "dnb_home": f"{home} (draw no bet)", "dnb_away": f"{away} (draw no bet)",
            "over2.5": "Over 2.5 goals", "under2.5": "Under 2.5 goals"}.get(code, code)


def short(code: str, home: str, away: str) -> str:
    """Team name for the ledger's `team` column."""
    return {"home": home, "away": away, "draw": "Draw", "1X": f"{home} or Draw", "X2": f"{away} or Draw",
            "dnb_home": home, "dnb_away": away, "over2.5": "Over 2.5", "under2.5": "Under 2.5"}.get(code, code)


def outcome(code: str, hs: int, as_: int) -> str:
    """'won', 'lost' or 'void' (stake back) for a final score."""
    r = "home" if hs > as_ else "away" if as_ > hs else "draw"
    if code in ("home", "draw", "away"):
        return "won" if code == r else "lost"
    if code == "1X":
        return "won" if r in ("home", "draw") else "lost"
    if code == "X2":
        return "won" if r in ("away", "draw") else "lost"
    if code in ("dnb_home", "dnb_away"):
        if r == "draw":
            return "void"
        return "won" if r == code[4:] else "lost"
    if code == "over2.5":
        return "won" if hs + as_ > 2.5 else "lost"
    if code == "under2.5":
        return "won" if hs + as_ < 2.5 else "lost"
    raise ValueError(code)


def live_status(code: str, hs: int, as_: int) -> str:
    """How the pick stands during the match."""
    if code == "over2.5":
        need = 3 - (hs + as_)
        return "✅ landed" if need <= 0 else f"⏳ {need} more goal{'s' if need > 1 else ''} needed"
    if code == "under2.5":
        return "❌ gone" if hs + as_ >= 3 else "✅ on track"
    o = outcome(code, hs, as_)
    if o == "won":
        return "✅ winning right now"
    if o == "void":
        return "⏸ level right now (stake back if it ends like this)"
    return "⏸ level right now" if hs == as_ else "⚠️ behind right now"


def extra_bets(best: list[float], sharp, p: np.ndarray, totals: dict | None, model_over: float,
               blend: float, kelly_fraction: float = 0.25, cap: float = 0.03) -> list[dict]:
    """Every extra-market bet for one match. best/sharp: home/draw/away prices,
    p: final 1X2 probabilities, totals: {'best': (over, under), 'sharp': (over, under) or None}."""
    oH, oD, oA = best
    out = []

    def add(code, odds, q, ev, conf):
        out.append({"outcome": code, "k": {"1X": 0, "dnb_home": 0, "X2": 2, "dnb_away": 2}.get(code, -1),
                    "odds": float(odds), "p": float(q), "implied": 1 / float(odds), "ev": float(ev),
                    "conf": float(conf), "stake_frac": kelly(min(q, 0.99), odds, kelly_fraction, cap)})

    for code, k in (("1X", 0), ("X2", 2)):
        o = 1 / (1 / best[k] + 1 / oD)
        q = p[k] + p[1]
        add(code, o, q, q * o - 1, q)
    for code, k in (("dnb_home", 0), ("dnb_away", 2)):
        o = best[k] * (1 - 1 / oD)
        if o > 1.01:
            q = p[k] / (1 - p[1])
            add(code, o, q, p[k] * o + p[1] - 1, q)
    if totals and totals.get("best") and all(x and x > 1.01 for x in totals["best"]):
        bo, bu = totals["best"]
        po = model_over
        if totals.get("sharp") and all(x and x > 1.01 for x in totals["sharp"]):
            po = blend * model_over + (1 - blend) * devig(np.array(totals["sharp"]))[0]
        add("over2.5", bo, po, po * bo - 1, po)
        add("under2.5", bu, 1 - po, (1 - po) * bu - 1, 1 - po)
    return out
