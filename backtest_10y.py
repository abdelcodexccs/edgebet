"""10-year walk-forward backtest of all three systems, top five leagues.

  A. Value bot     - live rules: edge >= +5%, odds 1.50-2.50, no draws, max 3 a day
  B. Daily mode    - A, plus the best game as "Pick of the day" on days with no value
  C. VIP 1/week    - the week's most likely winner where model and market agree
                     (at least 60% likely; no pick that week otherwise)
  D. VIP 2/week    - same, top two

Every week the model is rebuilt using only matches already played. Prices are the
best available across bookmakers (Max, or Betbrain max in older files), checked
against Pinnacle's closing line (Betfair Exchange when Pinnacle is missing).

    python backtest_10y.py --years 10
Writes results/backtest_10y.md and results/backtest_10y_<system>.csv
"""
import argparse
import os
import urllib.request
from datetime import datetime

import numpy as np
import pandas as pd

from edgebet import data as D
from edgebet.backtest import predict_walk_forward
from edgebet.odds import devig, kelly
from edgebet.strategy import Rules, pick

LEAGUES = {"E0": "Premier League", "SP1": "La Liga", "I1": "Serie A", "D1": "Bundesliga", "F1": "Ligue 1"}
URL = "https://www.football-data.co.uk/mmz4281/{code}/{league}.csv"
D.BOOKS.update({"bfe": ("BFEH", "BFED", "BFEA"), "bfe_close": ("BFECH", "BFECD", "BFECA"),
                "bbavg": ("BbAvH", "BbAvD", "BbAvA"), "avg": ("AvgH", "AvgD", "AvgA")})


def fetch(folder, first, last):
    for lg in LEAGUES:
        os.makedirs(os.path.join(folder, lg), exist_ok=True)
        for y in range(first, last + 1):
            dest = os.path.join(folder, lg, f"{lg}-{y}.csv")
            if os.path.exists(dest) and y < last:
                continue
            code = f"{y % 100:02d}{(y + 1) % 100:02d}"
            try:
                urllib.request.urlretrieve(URL.format(code=code, league=lg), dest)
            except Exception as e:  # noqa: BLE001
                print("  could not download", lg, y, e)


def combine(df, name, *sources):
    cols = []
    for side in "HDA":
        s = pd.Series(np.nan, index=df.index)
        for src in sources:
            if src + side in df:
                s = s.fillna(pd.to_numeric(df[src + side], errors="coerce"))
        df[name + side] = s
        cols.append(name + side)
    D.BOOKS[name] = tuple(cols)


def prepare(folder, lg):
    df = D.load_league(os.path.join(folder, lg))
    combine(df, "best", "Max", "BbMx")
    combine(df, "sharp", "PS", "BFE")
    combine(df, "sharp_close", "PSC", "BFEC")
    return df


def candidates(preds):
    """Every match: model + fair market probabilities and the best price, per side."""
    rows = []
    for r in preds.itertuples(index=False):
        sharp = np.array([getattr(r, c) for c in D.BOOKS["sharp"]], float)
        close = np.array([getattr(r, c) for c in D.BOOKS["sharp_close"]], float)
        best = np.array([getattr(r, c) for c in D.BOOKS["best"]], float)
        if not (np.isfinite(sharp).all() and np.isfinite(best).all()):
            continue
        fair = devig(sharp)
        fair_close = devig(close) if np.isfinite(close).all() else fair
        model = np.array([r.model_home, r.model_draw, r.model_away])
        rows.append({"date": r.date, "match": f"{r.home_team} v {r.away_team}", "result": r.result,
                     "score": f"{r.fthg}-{r.ftag}", "sharp": sharp, "fair": fair, "fair_close": fair_close,
                     "model": model, "best": best})
    return rows


def as_bet(c, k, kind, ev, p):
    won = c["result"] == k
    o = c["best"][k]
    return {"date": c["date"], "league": c["league"], "match": c["match"], "score": c["score"],
            "pick": ["home", "draw", "away"][k], "odds": o, "p": p, "ev": ev, "kind": kind, "won": won,
            "profit": o - 1 if won else -1.0, "clv": o * c["fair_close"][k] - 1}


def system_value(cands, rules, daily=False, cap=3):
    by_day = {}
    for c in cands:
        p = rules.blend * c["model"] + (1 - rules.blend) * c["fair"]
        p = p / p.sum()
        b = pick(p, c["best"], rules)
        lean = b or pick(p, c["best"], Rules(**{**rules.__dict__, "min_ev": -1.0}))
        if lean:
            by_day.setdefault(c["date"], []).append(
                as_bet(c, lean["k"], "value" if b else "lean", lean["ev"], lean["p"]))
    out = []
    for day, bets in sorted(by_day.items()):
        value = sorted([b for b in bets if b["kind"] == "value"], key=lambda b: -b["ev"])[:cap]
        if value:
            out += value
        elif daily:
            out += sorted(bets, key=lambda b: -b["ev"])[:1]
    return pd.DataFrame(out)


def system_vip(cands, per_week, min_conf=0.60):
    rows = []
    for c in cands:
        for k in (0, 2):
            conf = (c["model"][k] + c["fair"][k]) / 2
            if c["model"][k] >= c["fair"][k] and conf >= min_conf:
                b = as_bet(c, k, "vip", c["model"][k] * c["best"][k] - 1, (c["model"][k] + c["fair"][k]) / 2)
                rows.append(b)
    d = pd.DataFrame(rows)
    d["week"] = pd.to_datetime(d["date"]).dt.to_period("W")
    return (d.sort_values("p", ascending=False).groupby("week").head(per_week)
             .sort_values("date").drop(columns="week").reset_index(drop=True))


def stats(b, bankroll=400.0, stake=0.01):
    if not len(b):
        return {"bets": 0}
    b = b.sort_values("date")
    r = b["profit"]
    curve = np.cumsum(np.concatenate([[0], r.to_numpy()]))
    dd_units = float((curve - np.maximum.accumulate(curve)).min())
    runs = max((len(s) for s in "".join("1" if w else "0" for w in b["won"]).split("1")), default=0)
    bank = bankroll
    for x in b.itertuples():  # 1% of current bankroll per bet
        s = bank * stake
        bank += s * (x.odds - 1) if x.won else -s
    return {"bets": len(b), "win": b["won"].mean(), "odds": b["odds"].mean(), "units": r.sum(),
            "roi": r.mean(), "t": r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 1 else 0.0,
            "clv": b["clv"].mean(), "dd": dd_units, "run": runs, "bank": bank}


def fmt_row(name, s):
    if not s.get("bets"):
        return f"| {name} | 0 | | | | | | | | |"
    return (f"| {name} | {s['bets']} | {s['win']:.0%} | {s['odds']:.2f} | {s['units']:+.1f} | "
            f"**{s['roi']:+.1%}** | {s['t']:+.2f} | {s['clv']:+.1%} | {s['dd']:.1f} | {s['run']} |")


HEAD = "| | Bets | Won | Avg odds | Units | ROI | t-stat | vs closing | Worst dip (u) | Worst losing run |\n|---|---|---|---|---|---|---|---|---|---|"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--data", default="data_hist")
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()
    end = pd.Timestamp(datetime.utcnow().date())
    start = end - pd.Timedelta(days=int(365.25 * args.years))
    last = end.year if end.month >= 7 else end.year - 1
    first = start.year - 4
    if not args.no_fetch:
        print(f"Downloading {first}-{last} for {len(LEAGUES)} leagues ...", flush=True)
        fetch(args.data, first, last)

    rules = Rules()
    cands = []
    for lg, name in LEAGUES.items():
        print(f"Walk-forward {name} from {start.date()} ...", flush=True)
        df = prepare(args.data, lg)
        preds = predict_walk_forward(df, str(start.date()))
        preds = preds[preds["date"] < end]
        cs = candidates(preds)
        for c in cs:
            c["league"] = lg
        print(f"  {len(preds)} matches priced, {len(cs)} with odds", flush=True)
        cands += cs

    systems = {"A_value": system_value(cands, rules),
               "B_daily": system_value(cands, rules, daily=True),
               "C_vip1": system_vip(cands, 1),
               "D_vip2": system_vip(cands, 2)}
    labels = {"A_value": "A. Value bot (live rules)", "B_daily": "B. Daily mode (value + pick of the day)",
              "C_vip1": "C. VIP 1 pick / week", "D_vip2": "D. VIP 2 picks / week"}
    os.makedirs("results", exist_ok=True)
    lines = [f"# 10-year backtest, top 5 leagues", "",
             f"{start.date()} to {end.date()} · walk-forward (model rebuilt weekly from past matches only) · "
             f"best price across bookmakers · flat 1 unit per bet", "", "## Overall", "", HEAD]
    for key, b in systems.items():
        b.to_csv(f"results/backtest_10y_{key}.csv", index=False)
        lines.append(fmt_row(labels[key], stats(b)))
    lines += ["", "$400 bankroll staking 1% of the current bankroll per bet ends at: " +
              ", ".join(f"{labels[k].split('.')[0]} ${stats(b)['bank']:.0f}" for k, b in systems.items() if len(b)), ""]
    for key, b in systems.items():
        if not len(b):
            continue
        lines += [f"## {labels[key]} by season", "", HEAD]
        b = b.copy()
        dt = pd.to_datetime(b["date"])
        b["season"] = np.where(dt.dt.month >= 7, dt.dt.year, dt.dt.year - 1)
        for s, g in b.groupby("season"):
            lines.append(fmt_row(f"{s}-{(s + 1) % 100:02d}", stats(g)))
        lines += ["", f"By league:", "", HEAD]
        for lg, name in LEAGUES.items():
            lines.append(fmt_row(name, stats(b[b["league"] == lg])))
        lines.append("")
    lines += ["t-stat: about 2 or more means the result is unlikely to be luck. "
              "'vs closing': average price advantage over the sharp closing line (positive = real edge sign)."]
    open("results/backtest_10y.md", "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
