"""One-year walk-forward backtest of the exact live rules across the top five
leagues, then (optionally) post the results and every bet to your channel.

The model is refit every week using only matches played BEFORE that week, so
it never sees the future. Bets are taken at the best price across books (like
the live bot), compared with the sharp closing line, and capped at
MAX_PICKS_PER_DAY per day (highest edge first), like the live bot.

    python backtest_year.py               # E0 SP1 I1 D1 F1, last 365 days
    python backtest_year.py --days 730 --leagues E0 SP1
"""
import argparse
import html
import itertools
import os
from datetime import datetime

import numpy as np
import pandas as pd

from daily import cfg, load_env
from edgebet import data as D
from edgebet.backtest import predict_walk_forward, simulate
from edgebet.live import LEAGUES
from edgebet.odds import devig, kelly
from edgebet.strategy import Rules

D.BOOKS.update({"bfe": ("BFEH", "BFED", "BFEA"), "bfe_close": ("BFECH", "BFECD", "BFECA")})
LEAGUE_NAMES = {k: v[1] for k, v in LEAGUES.items()}
FLAGS = {"E0": "🏴", "SP1": "🇪🇸", "I1": "🇮🇹", "D1": "🇩🇪", "F1": "🇫🇷"}


def e(x):
    return html.escape(str(x), quote=False)


def add_sharp(df):
    """Pinnacle where the file has it, otherwise Betfair Exchange."""
    for key, (ps, bf) in {"sharp": ("PS", "BFE"), "sharp_close": ("PSC", "BFEC")}.items():
        cols = []
        for side in "HDA":
            a = df[ps + side] if ps + side in df else pd.Series(np.nan, index=df.index)
            b = df[bf + side] if bf + side in df else pd.Series(np.nan, index=df.index)
            df[f"{key}{side}"] = a.fillna(b)
            cols.append(f"{key}{side}")
        D.BOOKS[key] = tuple(cols)
    return df


def run_league(lg, start, end, rules):
    df = add_sharp(D.load_league(os.path.join("data", lg)))
    preds = predict_walk_forward(df, str(start.date()))
    preds = preds[preds["date"] < end]
    bets = simulate(preds, rules, book="max", sharp="sharp", close="sharp_close")
    y = np.eye(3)[preds["result"].to_numpy()]
    m = preds[["model_home", "model_draw", "model_away"]].to_numpy()
    ok = preds[list(D.BOOKS["sharp_close"])].notna().all(axis=1).to_numpy()
    c = np.vstack([devig(r) for r in preds.loc[ok, list(D.BOOKS["sharp_close"])].to_numpy(float)])
    quality = (float(-np.mean(np.log((m[ok] * y[ok]).sum(1)))), float(-np.mean(np.log((c * y[ok]).sum(1)))))
    if len(bets):
        bets["league"] = lg
    return bets, len(preds), quality


def stats(b, bankroll=1000.0):
    r = b["flat_profit"]
    bank, curve = bankroll, [bankroll]
    for x in b.itertuples():
        st = bank * kelly(x.final_p, x.odds)
        bank += st * (x.odds - 1) if x.won else -st
        curve.append(bank)
    curve = np.array(curve)
    runs = [len(list(g)) for k, g in itertools.groupby(~b["won"]) if k]
    return {"n": len(b), "w": int(b["won"].sum()), "l": int((~b["won"]).sum()),
            "units": float(r.sum()), "roi": float(r.mean()), "odds": float(b["odds"].mean()),
            "be": float((1 / b["odds"]).mean()), "clv": float(b["clv"].mean()),
            "beat": float((b["clv"] > 0).mean()), "dd": float((curve / np.maximum.accumulate(curve) - 1).min()),
            "run": max(runs, default=0), "t": float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))) if len(r) > 1 else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="+", default=["E0", "SP1", "I1", "D1", "F1"])
    ap.add_argument("--days", type=int, default=365)
    args = ap.parse_args()
    load_env()
    end = pd.Timestamp(datetime.now().date())
    start = end - pd.Timedelta(days=args.days)
    rules = Rules(min_ev=cfg("MIN_EV", Rules.min_ev), max_odds=cfg("MAX_ODDS", Rules.max_odds),
                  blend=cfg("BLEND", Rules.blend))
    cap = cfg("MAX_PICKS_PER_DAY", 3)
    print(f"Backtesting {start.date()} to {end.date()}: EV>={rules.min_ev:.0%}, odds {rules.min_odds}-"
          f"{rules.max_odds}, no draws, max {cap} picks/day ...")

    all_bets, quality, n_games = [], {}, 0
    for lg in args.leagues:
        print(f"  {LEAGUE_NAMES.get(lg, lg)} ...", flush=True)
        b, n, q = run_league(lg, start, end, rules)
        n_games += n
        quality[lg] = q
        if len(b):
            all_bets.append(b)
    if not all_bets:
        print("No bets in the period.")
        return
    bets = pd.concat(all_bets, ignore_index=True)
    bets["date"] = pd.to_datetime(bets["date"])
    # same daily cap as the live bot: best edges first
    bets = (bets.sort_values(["date", "ev"], ascending=[True, False])
                .groupby("date", group_keys=False).head(cap).reset_index(drop=True))
    os.makedirs("results", exist_ok=True)
    bets.to_csv("results/backtest_last_year.csv", index=False)

    s = stats(bets)
    head = [
        "📊 <b>BACKTEST: last 12 months, top 5 leagues</b>",
        f"<i>{start:%d %b %Y} → {end:%d %b %Y} · simulated, not real bets</i>",
        "",
        "Same model and rules as the live picks. Each week the model was rebuilt from matches already "
        f"played, then priced the next games ({n_games} games checked).",
        "",
        f"🎯 Bets: <b>{s['n']}</b> ({s['w']}W - {s['l']}L, win rate {s['w'] / s['n']:.0%})",
        f"💰 Profit: <b>{s['units']:+.2f} units</b> (1 unit per bet)",
        f"📈 ROI: <b>{s['roi']:+.1%}</b>",
        f"🎲 Average odds {s['odds']:.2f} · needed {s['be']:.0%} wins to break even",
        f"📉 Worst drawdown (Kelly stakes): {s['dd']:.0%} · longest losing run: {s['run']}",
        f"🔍 Beat the closing price on {s['beat']:.0%} of bets (avg {s['clv']:+.1%})",
        "",
        "<b>By league</b> (bets · W-L · units · ROI)",
    ]
    for lg in args.leagues:
        b = bets[bets["league"] == lg]
        if len(b):
            t = stats(b)
            head.append(f"{FLAGS.get(lg, '')} {LEAGUE_NAMES.get(lg, lg)}: {t['n']} · {t['w']}-{t['l']} · "
                        f"{t['units']:+.2f} · {t['roi']:+.0%}")
        else:
            head.append(f"{FLAGS.get(lg, '')} {LEAGUE_NAMES.get(lg, lg)}: no bets")
    head += ["", "<b>By month</b> (bets · units)"]
    for p, g in bets.groupby(bets["date"].dt.to_period("M")):
        head.append(f"{p.strftime('%b %Y')}: {len(g)} · {g['flat_profit'].sum():+.2f}")
    head += ["", "<i>Champions League picks are live but not in this backtest (no free odds history).",
             "Past results don't guarantee future ones. 18+ · Bet responsibly.</i>"]
    msgs = ["\n".join(head)]

    lines = []
    for b in bets.itertuples():
        home, away = b.match.split(" v ")
        team = home if b.pick == "home" else away
        lines.append(f"{'✅' if b.won else '❌'} {b.date:%d %b} {FLAGS.get(b.league, '')} {e(b.match)} ({b.score})\n"
                     f"    {e(team)} @ {b.odds:.2f} · edge {b.ev:+.0%} · {b.flat_profit:+.2f}u")
    chunks, cur = [], ""
    for ln in lines:
        if len(cur) + len(ln) > 3600:
            chunks.append(cur)
            cur = ""
        cur += ln + "\n"
    chunks.append(cur)
    for k, c in enumerate(chunks, 1):
        msgs.append(f"<b>Every bet ({k}/{len(chunks)})</b>\n\n" + c.rstrip())

    print("\n" + "=" * 60)
    for m in msgs:
        for tag in ("<b>", "</b>", "<i>", "</i>"):
            m = m.replace(tag, "")
        print(m)
        print("-" * 60)
    print("Model vs sharp closing line (log loss, lower is better):")
    for lg, (a, c) in quality.items():
        print(f"  {LEAGUE_NAMES.get(lg, lg)}: model {a:.4f} · closing line {c:.4f}")
    print(f"t-stat of ROI: {s['t']:.2f} (needs about 2 to be more than luck)")
    print("Saved every bet to results/backtest_last_year.csv")

    if input(f"\nPost these {len(msgs)} messages to your Telegram channel? (y/n): ").strip().lower() == "y":
        from edgebet import announce
        for m in msgs:
            announce.post_to_channel(m)
        print("Posted.")
    else:
        print("Not posted.")


if __name__ == "__main__":
    main()
