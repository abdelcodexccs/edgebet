"""Walk-forward backtest: the model only ever sees matches played before the bet.

Step 1 `predict_walk_forward` refits the model every week and stores its
probabilities next to the odds. Step 2 `simulate` replays any set of betting
rules over those predictions, so many rule sets can be compared cheaply.
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from .data import BOOKS
from .model import DixonColes
from .odds import devig
from .strategy import OUTCOMES, Rules, pick, probabilities

RESULT_INDEX = {"H": 0, "D": 1, "A": 2}


def predict_walk_forward(df: pd.DataFrame, start: str, train_years: float = 3.0,
                         xi: float = 0.0018, min_matches: int = 10,
                         progress: bool = False) -> pd.DataFrame:
    test = df[df["Date"] >= pd.Timestamp(start)].copy()
    test["week"] = test["Date"].dt.to_period("W-MON").dt.start_time
    odds_cols = [c for trio in BOOKS.values() for c in trio if c in df.columns]
    model = DixonColes(xi=xi)
    rows = []
    weeks = test.groupby("week")
    for i, (week, games) in enumerate(weeks):
        train = df[(df["Date"] < week) &
                   (df["Date"] >= week - pd.Timedelta(days=int(365 * train_years)))]
        model.fit(train, as_of=week)
        for g in games.itertuples(index=False):
            h, a = g.HomeTeam, g.AwayTeam
            if not (model.knows(h, min_matches) and model.knows(a, min_matches)):
                continue
            mp = model.predict(h, a)
            row = {"date": g.Date, "home_team": h, "away_team": a,
                   "fthg": g.FTHG, "ftag": g.FTAG, "result": RESULT_INDEX[g.FTR],
                   **{f"model_{o}": mp[o] for o in OUTCOMES},
                   "xg_home": mp["xg_home"], "xg_away": mp["xg_away"]}
            row.update({c: getattr(g, c) for c in odds_cols})
            rows.append(row)
        if progress and i % 20 == 0:
            print(f"  week {i + 1}/{weeks.ngroups}  {week.date()}", flush=True)
    return pd.DataFrame(rows)


def _trio(row, book):
    return np.array([row[c] for c in BOOKS[book]], dtype=float)


def simulate(preds: pd.DataFrame, rules: Rules, book: str = "pinnacle",
             sharp: str = "pinnacle", close: str = "pinnacle_close",
             bankroll: float = 1000.0) -> pd.DataFrame:
    bank, bets = bankroll, []
    for _, r in preds.iterrows():
        bet_odds, sharp_odds, close_odds = _trio(r, book), _trio(r, sharp), _trio(r, close)
        if not (np.all(np.isfinite(sharp_odds)) and np.all(np.isfinite(close_odds))):
            continue
        mp = {o: r[f"model_{o}"] for o in OUTCOMES}
        p = probabilities(mp, sharp_odds, rules.blend)
        b = pick(p, bet_odds, rules)
        if b is None:
            continue
        won = b["k"] == r["result"]
        stake = bank * b["stake_frac"]
        profit = stake * (b["odds"] - 1) if won else -stake
        bank += profit
        fair_close = devig(close_odds)
        bets.append({
            "date": r["date"].date(), "match": f'{r["home_team"]} v {r["away_team"]}',
            "pick": b["outcome"], "odds": b["odds"],
            "model_p": round(mp[b["outcome"]], 4), "final_p": round(b["p"], 4),
            "implied": round(b["implied"], 4), "ev": round(b["ev"], 4), "won": won,
            "flat_profit": (b["odds"] - 1) if won else -1.0,
            "stake": round(stake, 2), "profit": round(profit, 2), "bankroll": round(bank, 2),
            # closing line value: was our price better than the fair closing price?
            "clv": round(b["odds"] * fair_close[b["k"]] - 1, 4),
            "score": f'{r["fthg"]}-{r["ftag"]}',
        })
    return pd.DataFrame(bets)


def model_quality(preds: pd.DataFrame) -> dict:
    """How good are the model's probabilities vs Pinnacle's closing line? Lower is better."""
    p = preds.dropna(subset=list(BOOKS["pinnacle_close"]))
    y = np.eye(3)[p["result"].to_numpy()]
    m = p[[f"model_{o}" for o in OUTCOMES]].to_numpy()
    c = np.vstack([devig(_trio(r, "pinnacle_close")) for _, r in p.iterrows()])
    out = {"matches": len(p)}
    for name, q in (("model", m), ("pinnacle_close", c)):
        out[f"logloss_{name}"] = round(float(-np.mean(np.log((q * y).sum(1)))), 4)
        out[f"brier_{name}"] = round(float(np.mean(((q - y) ** 2).sum(1))), 4)
    return out


def summary(bets: pd.DataFrame, bankroll: float = 1000.0) -> dict:
    if not len(bets):
        return {"bets": 0}
    r = bets["flat_profit"]
    curve = np.concatenate([[bankroll], bets["bankroll"].to_numpy()])
    dd = (curve / np.maximum.accumulate(curve) - 1).min()
    runs = [len(list(g)) for k, g in itertools.groupby(~bets["won"]) if k]
    return {
        "bets": len(bets),
        "win_rate": round(bets["won"].mean(), 4),
        "breakeven_win_rate": round((1 / bets["odds"]).mean(), 4),
        "avg_odds": round(bets["odds"].mean(), 3),
        "flat_profit_units": round(r.sum(), 2),
        "flat_roi": round(r.mean(), 4),
        "roi_t_stat": round(r.mean() / (r.std(ddof=1) / np.sqrt(len(r))), 2),
        "kelly_final_bankroll": round(curve[-1], 2),
        "max_drawdown": round(float(dd), 4),
        "avg_clv": round(bets["clv"].mean(), 4),
        "beat_close_pct": round((bets["clv"] > 0).mean(), 4),
        "longest_losing_run": max(runs, default=0),
    }
