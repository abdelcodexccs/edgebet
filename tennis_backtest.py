"""Tennis (ATP + WTA) backtest with a surface-aware Elo model, compared with the
football VIP rule. No draws in tennis, so favourites settle cleanly.

Model: classic tennis Elo (K = 250 / (matches + 5) ** 0.4), one overall rating and
one per surface, blended 50/50. Every prediction uses only matches played before.
Odds: tennis-data.co.uk (Pinnacle = sharp, Max = best price).

    python tennis_backtest.py
"""
import io
import os
import urllib.request
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

TOURS = {"ATP": "http://www.tennis-data.co.uk/{y}/{y}.xlsx", "WTA": "http://www.tennis-data.co.uk/{y}w/{y}.xlsx"}


def load(first, last, folder="data_tennis"):
    os.makedirs(folder, exist_ok=True)
    frames = []
    for tour, url in TOURS.items():
        for y in range(first, last + 1):
            path = os.path.join(folder, f"{tour}-{y}.xlsx")
            if not os.path.exists(path):
                try:
                    req = urllib.request.Request(url.format(y=y), headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"})
                    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
                        f.write(r.read())
                except Exception as e:  # noqa: BLE001
                    print(f"::warning::could not download {tour} {y}: {e}")
                    continue
            try:
                d = pd.read_excel(path)
            except Exception as e:  # noqa: BLE001
                print(f"::warning::could not read {tour} {y}: {e}")
                continue
            d["Tour"] = tour
            frames.append(d)
    if not frames:
        raise SystemExit("No tennis files could be downloaded or read")
    df = pd.concat(frames, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Winner", "Loser"])
    return df.sort_values("Date", kind="stable").reset_index(drop=True)


def num(df, c):
    return pd.to_numeric(df[c], errors="coerce") if c in df else pd.Series(np.nan, index=df.index)


def run_elo(df):
    """Walk through matches in time order; store the pre-match win chance of the winner."""
    elo = defaultdict(lambda: 1500.0)
    selo = defaultdict(lambda: 1500.0)
    n = defaultdict(int)
    ns = defaultdict(int)
    p_w, n_w, n_l = [], [], []
    for r in df.itertuples(index=False):
        w, l, s = (r.Tour, r.Winner), (r.Tour, r.Loser), str(r.Surface)
        def prob(a, b):
            return 1 / (1 + 10 ** ((b - a) / 400))
        p = 0.5 * prob(elo[w], elo[l]) + 0.5 * prob(selo[(w, s)], selo[(l, s)])
        p_w.append(p)
        n_w.append(n[w])
        n_l.append(n[l])
        if str(getattr(r, "Comment", "Completed")).startswith("Completed"):
            for rating, key_w, key_l, cnt in ((elo, w, l, n), (selo, (w, s), (l, s), ns)):
                e = prob(rating[key_w], rating[key_l])
                kw, kl = 250 / (cnt[key_w] + 5) ** 0.4, 250 / (cnt[key_l] + 5) ** 0.4
                rating[key_w] += kw * (1 - e)
                rating[key_l] -= kl * (1 - e)
                cnt[key_w] += 1
                cnt[key_l] += 1
    df = df.copy()
    df["p_winner"], df["n_w"], df["n_l"] = p_w, n_w, n_l
    return df


def sides(df):
    """Two rows per match (winner side, loser side) with model, fair market and a real bookmaker price.
    Sharp line: Pinnacle, else Betfair Exchange, else market average. Bet price: Bet365, else average."""
    sw = num(df, "PSW").fillna(num(df, "BFEW")).fillna(num(df, "AvgW"))
    sl = num(df, "PSL").fillna(num(df, "BFEL")).fillna(num(df, "AvgL"))
    bw = num(df, "B365W").fillna(num(df, "AvgW"))
    bl = num(df, "B365L").fillna(num(df, "AvgL"))
    ok = (sw > 1) & (sl > 1) & (bw > 1) & (bl > 1) & (bw < 30) & (bl < 30) \
        & df["Comment"].astype(str).str.startswith("Completed") & (df["n_w"] >= 20) & (df["n_l"] >= 20)
    d = df[ok].copy()
    iw, il = 1 / sw[ok], 1 / sl[ok]
    fair_w = iw / (iw + il)
    base = {"date": d["Date"], "tour": d["Tour"], "event": d["Tournament"].astype(str)}
    a = pd.DataFrame({**base, "player": d["Winner"], "opp": d["Loser"], "model_p": d["p_winner"],
                      "market_p": fair_w, "odds": bw[ok], "won": True})
    b = pd.DataFrame({**base, "player": d["Loser"], "opp": d["Winner"], "model_p": 1 - d["p_winner"],
                      "market_p": 1 - fair_w, "odds": bl[ok], "won": False})
    s = pd.concat([a, b], ignore_index=True)
    s["profit"] = np.where(s["won"], s["odds"] - 1, -1.0)
    s["conf"] = (s["model_p"] + s["market_p"]) / 2
    s["week"] = s["date"].dt.to_period("W")
    return s


def vip(s, per_week=1, min_conf=0.60, min_odds=1.0):
    x = s[(s["model_p"] >= s["market_p"]) & (s["conf"] >= min_conf) & (s["odds"] >= min_odds)]
    return x.sort_values("conf", ascending=False).groupby("week").head(per_week).sort_values("date")


def stats(x):
    if not len(x):
        return None
    r = x["profit"]
    wl = "".join("1" if w else "0" for w in x.sort_values("date")["won"])
    return {"bets": len(x), "win": x["won"].mean(), "odds": x["odds"].mean(), "units": r.sum(), "roi": r.mean(),
            "t": r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 1 else 0.0,
            "run": max(len(z) for z in wl.split("1"))}


def row(name, st):
    if not st:
        return f"| {name} | 0 | | | | | | |"
    return (f"| {name} | {st['bets']} | {st['win']:.0%} | {st['odds']:.2f} | {st['units']:+.1f} | "
            f"**{st['roi']:+.1%}** | {st['t']:+.2f} | {st['run']} |")


HEAD = "| | Bets | Won | Avg odds | Units | ROI | t-stat | Worst losing run |\n|---|---|---|---|---|---|---|---|"


def main():
    now = pd.Timestamp(datetime.utcnow().date())
    y1, y10 = now - pd.Timedelta(days=365), now - pd.Timedelta(days=3653)
    df = load(y10.year - 3, now.year)
    print(f"{len(df)} matches loaded", flush=True)
    s = sides(run_elo(df))
    s = s[s["date"] < now]

    # model quality vs market (log loss on the winner side, lower is better)
    w = s[s["won"]]
    lines = ["# Tennis backtest (ATP + WTA)", "",
             f"Surface Elo model, walk-forward · Bet365 prices (one real bookmaker) · Pinnacle/Betfair as the sharp line · "
             f"players need 20+ previous matches", "",
             f"Model log loss {-np.log(w['model_p']).mean():.4f} vs Pinnacle {-np.log(w['market_p']).mean():.4f} "
             f"(last 10 years, lower is better)", ""]
    try:
        fb = pd.read_csv("results/backtest_10y_C_vip1.csv", parse_dates=["date"])
        fb = fb.assign(profit=fb["profit"], won=fb["won"].astype(bool))
    except Exception:  # noqa: BLE001
        fb = None
    for label, start in (("Last 12 months", y1), ("Last 10 years", y10)):
        x = s[s["date"] >= start]
        lines += [f"## {label}", "", HEAD,
                  row("Tennis VIP 1/week (same rule as football: model ≥ market, ≥60% likely)", stats(vip(x, 1))),
                  row("Tennis VIP 2/week", stats(vip(x, 2))),
                  row("Tennis VIP 1/week, ≥75% likely", stats(vip(x, 1, 0.75))),
                  row("Tennis VIP 1/week, odds 1.25+ (like football's ~1.32)", stats(vip(x, 1, 0.60, 1.25))),
                  row("Tennis: every favourite 60-75% (reference)",
                      stats(x[(x["market_p"] >= 0.60) & (x["market_p"] < 0.75)])),
                  row("Tennis: every favourite 75%+ (reference)", stats(x[x["market_p"] >= 0.75]))]
        if fb is not None:
            f = fb[fb["date"] >= start]
            lines.append(row("Football VIP 1/week (from the 10-year backtest)", stats(f)))
            # one combined weekly pick: football pick + tennis pick = 2 a week
            t1 = vip(x, 1)[["date", "won", "odds", "profit"]]
            both = pd.concat([f[["date", "won", "odds", "profit"]], t1], ignore_index=True)
            lines.append(row("Football 1/week + Tennis 1/week (2 a week)", stats(both)))
        lines.append("")
    by_tour = s[s["date"] >= y10]
    lines += ["## Tennis VIP 1/week by tour (10 years)", "", HEAD]
    for tour in TOURS:
        lines.append(row(tour, stats(vip(by_tour[by_tour["tour"] == tour], 1))))
    v = vip(s[s["date"] >= y10], 1).copy()
    v["season"] = v["date"].dt.year
    lines += ["", "## Tennis VIP 1/week by year", "", HEAD]
    for yr, g in v.groupby("season"):
        lines.append(row(str(yr), stats(g)))
    os.makedirs("results", exist_ok=True)
    vip(s[s["date"] >= y10], 1).to_csv("results/tennis_vip1.csv", index=False)
    open("results/tennis_backtest.md", "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
