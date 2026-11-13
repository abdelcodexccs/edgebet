"""Which bet types hold up? Walk-forward test of the same model on more markets:

  1X2         team to win (the live rules, as the baseline)
  DC          double chance 1X / X2, priced by splitting the stake over the two 1X2
              outcomes at the best prices (a real, placeable bet)
  DNB         draw no bet, built the same way (draw = stake back)
  OU 2.5      over / under 2.5 goals at the best price

Probabilities are 50% model + 50% fair Pinnacle (Betfair when Pinnacle is missing).
"vs closing" = price advantage over the sharp closing line (positive = genuine edge).

    python backtest_markets.py --years 10           # downloads seasons into data_hist
    python backtest_markets.py --years 2 --data data --no-fetch
Prints the tables (nothing is saved or posted).
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backtest_10y import LEAGUES, combine, fetch, prepare
from edgebet import data as D
from edgebet.backtest import predict_walk_forward
from edgebet.odds import devig


OU_COLS = ["Max>2.5", "Max<2.5", "BbMx>2.5", "BbMx<2.5", "P>2.5", "P<2.5", "BFE>2.5", "BFE<2.5",
           "PC>2.5", "PC<2.5", "BFEC>2.5", "BFEC<2.5"]
_load_csv = D.load_csv


def _load_csv_with_totals(path):
    """The normal loader keeps only 1X2 prices; keep the over/under 2.5 prices too."""
    df = _load_csv(path)
    raw = pd.read_csv(path, encoding="latin1").dropna(subset=["HomeTeam", "AwayTeam"])
    for c in OU_COLS:
        if c in raw:
            df[c] = pd.to_numeric(raw[c], errors="coerce").to_numpy()
    return df


D.load_csv = _load_csv_with_totals


def first(df, cols):
    s = pd.Series(np.nan, index=df.index)
    for c in cols:
        if c in df:
            s = s.fillna(pd.to_numeric(df[c], errors="coerce"))
    return s


def totals(df):
    return pd.DataFrame({
        "Date": df["Date"], "HomeTeam": df["HomeTeam"], "AwayTeam": df["AwayTeam"],
        "best_o": first(df, ["Max>2.5", "BbMx>2.5"]), "best_u": first(df, ["Max<2.5", "BbMx<2.5"]),
        "sharp_o": first(df, ["P>2.5", "BFE>2.5"]), "sharp_u": first(df, ["P<2.5", "BFE<2.5"]),
        "close_o": first(df, ["PC>2.5", "BFEC>2.5"]), "close_u": first(df, ["PC<2.5", "BFEC<2.5"]),
    })


def bets_for(preds: pd.DataFrame, lg: str) -> list[dict]:
    out = []
    for r in preds.itertuples(index=False):
        sharp = np.array([getattr(r, c) for c in D.BOOKS["sharp"]], float)
        close = np.array([getattr(r, c) for c in D.BOOKS["sharp_close"]], float)
        best = np.array([getattr(r, c) for c in D.BOOKS["best"]], float)
        if not (np.isfinite(sharp).all() and np.isfinite(best).all() and (sharp > 1.01).all() and (best > 1.01).all()):
            continue
        fair = devig(sharp)
        fc = devig(close) if np.isfinite(close).all() and (close > 1.01).all() else fair
        m = np.array([r.model_home, r.model_draw, r.model_away])
        p = 0.5 * m + 0.5 * fair
        p = p / p.sum()
        res = int(r.result)  # 0 home, 1 draw, 2 away
        goals = r.fthg + r.ftag
        base = {"date": r.date, "league": lg, "match": f"{r.home_team} v {r.away_team}"}

        def add(market, side, odds, prob, ev, clv, profit, conf):
            out.append({**base, "market": market, "side": side, "odds": odds, "p": prob, "ev": ev,
                        "clv": clv, "profit": profit, "won": profit > 0, "conf": conf})

        oH, oD, oA = best
        for k, side in ((0, "home"), (2, "away")):
            o = best[k]
            add("1X2", side, o, p[k], p[k] * o - 1, o * fc[k] - 1, o - 1 if res == k else -1.0,
                (m[k] + fair[k]) / 2)
        for k, side in ((0, "1X"), (2, "X2")):
            o = 1 / (1 / best[k] + 1 / oD)
            q, qc = p[k] + p[1], fc[k] + fc[1]
            add("DC", side, o, q, q * o - 1, o * qc - 1, o - 1 if res in (k, 1) else -1.0,
                (m[k] + m[1] + fair[k] + fair[1]) / 2)
        for k, side in ((0, "home"), (2, "away")):
            o = best[k] * (1 - 1 / oD)
            ev = p[k] * o + p[1] - 1
            profit = o - 1 if res == k else 0.0 if res == 1 else -1.0
            add("DNB", side, o, p[k] / (1 - p[1]), ev, fc[k] * o + fc[1] - 1, profit,
                (m[k] / (1 - m[1]) + fair[k] / (1 - fair[1])) / 2)
        def ok(*xs):
            return all(np.isfinite(x) and x > 1.01 for x in xs)

        if ok(r.best_o, r.best_u, r.sharp_o, r.sharp_u):
            fo = devig(np.array([r.sharp_o, r.sharp_u]))
            fco = devig(np.array([r.close_o, r.close_u])) if ok(r.close_o, r.close_u) else fo
            po = 0.5 * r.model_over25 + 0.5 * fo[0]
            for side, o, q, qc, hit, mq, fq in (("over", r.best_o, po, fco[0], goals > 2.5, r.model_over25, fo[0]),
                                                ("under", r.best_u, 1 - po, fco[1], goals < 2.5,
                                                 1 - r.model_over25, fo[1])):
                add("OU2.5", side, o, q, q * o - 1, o * qc - 1, o - 1 if hit else -1.0, (mq + fq) / 2)
    return out


def summary(b: pd.DataFrame) -> str:
    if not len(b):
        return "| 0 | | | | | | |"
    r = b["profit"]
    t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 1 else 0
    yrs = b.assign(y=pd.to_datetime(b["date"]).dt.year).groupby("y")["profit"].sum()
    return (f"| {len(b)} | {b['won'].mean():.0%} | {b['odds'].mean():.2f} | **{r.mean():+.1%}** | {t:+.2f} | "
            f"{b['clv'].mean():+.1%} | {(yrs > 0).sum()}/{len(yrs)} |")


HEAD = "| Strategy | Bets | Won | Avg odds | ROI | t-stat | vs closing | Years in profit |\n|---|---|---|---|---|---|---|---|"


def strategies(all_bets: pd.DataFrame):
    def best_per_match(x):
        return x.sort_values("ev", ascending=False).groupby(["match", "date"]).head(1)

    rows = []
    for market, lo, hi in (("1X2", 1.5, 2.5), ("DC", 1.25, 2.5), ("DC", 1.4, 2.5), ("DNB", 1.4, 2.5),
                           ("DNB", 1.6, 2.5), ("OU2.5", 1.6, 2.4)):
        x = all_bets[(all_bets["market"] == market) & all_bets["odds"].between(lo, hi)]
        for ev in (0.0, 0.03, 0.05, 0.08):
            rows.append((f"{market} odds {lo}-{hi}, edge ≥ {ev:.0%}", best_per_match(x[x["ev"] >= ev])))
    # high-confidence weekly picks (VIP style) per market
    for market, lo in (("1X2", 1.2), ("DC", 1.2), ("DNB", 1.2), ("OU2.5", 1.4)):
        x = all_bets[(all_bets["market"] == market) & (all_bets["odds"] >= lo) & (all_bets["ev"] >= -0.02)]
        x = x.assign(week=pd.to_datetime(x["date"]).dt.to_period("W"))
        v = x.sort_values("conf", ascending=False).groupby("week").head(1)
        rows.append((f"{market} VIP: most likely bet of the week, odds ≥ {lo}", v))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--data", default="data_hist")
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()
    end = pd.Timestamp(datetime.now(timezone.utc).date())
    start = end - pd.Timedelta(days=int(365.25 * args.years))
    if not args.no_fetch:
        fetch(args.data, start.year - 4, end.year if end.month >= 7 else end.year - 1)
    allb = []
    for lg, name in LEAGUES.items():
        print(f"Walk-forward {name} ...", flush=True)
        df = prepare(args.data, lg)
        preds = predict_walk_forward(df, str(start.date()))
        preds = preds[preds["date"] < end]
        tot = totals(df).rename(columns={"Date": "date", "HomeTeam": "home_team", "AwayTeam": "away_team"})
        preds = preds.merge(tot, on=["date", "home_team", "away_team"], how="left")
        allb += bets_for(preds, lg)
    b = pd.DataFrame(allb)
    recent = b[pd.to_datetime(b["date"]) >= end - pd.Timedelta(days=730)]
    for label, data in ((f"{start.date()} to {end.date()}", b), ("Last 2 years", recent)):
        print(f"\n## {label}\n\n{HEAD}")
        for name, x in strategies(data):
            print(f"| {name} {summary(x)}")
    print("\nBy league, DC odds 1.25-2.5 edge ≥ 3% and OU2.5 edge ≥ 3% (full period):")
    for lg in LEAGUES:
        for market, lo, hi in (("DC", 1.25, 2.5), ("OU2.5", 1.6, 2.4)):
            x = b[(b["league"] == lg) & (b["market"] == market) & b["odds"].between(lo, hi) & (b["ev"] >= 0.03)]
            x = x.sort_values("ev", ascending=False).groupby(["match", "date"]).head(1)
            print(f"| {lg} {market} {summary(x)}")


if __name__ == "__main__":
    main()
