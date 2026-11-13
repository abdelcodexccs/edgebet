"""Plain-language reasons behind a pick, computed from real results.

Every line is a checked fact from the history file. A fact is only shown when
it supports the pick, and the model line is always shown, so the post explains
the bet without inventing anything.
"""
from __future__ import annotations

import pandas as pd


def _team_games(hist: pd.DataFrame, team: str) -> pd.DataFrame:
    g = hist[(hist["HomeTeam"] == team) | (hist["AwayTeam"] == team)].sort_values("Date")
    home = g["HomeTeam"] == team
    gf = g["FTHG"].where(home, g["FTAG"])
    ga = g["FTAG"].where(home, g["FTHG"])
    res = (gf > ga).map({True: "W", False: ""}) + (gf == ga).map({True: "D", False: ""})
    res = res.replace("", "L")
    return g.assign(is_home=home, gf=gf, ga=ga, res=res)


def _pts(results) -> int:
    return sum(3 if r == "W" else 1 if r == "D" else 0 for r in results)


def build(hist: pd.DataFrame, model, home: str, away: str, side: str,
          names: dict | None = None, season_start: pd.Timestamp | None = None) -> list[str]:
    """side = 'home' or 'away'. names maps model names to display names."""
    names = names or {}
    me, opp = (home, away) if side == "home" else (away, home)
    n_me, n_opp = names.get(me, me), names.get(opp, opp)
    out: list[tuple[float, str]] = []  # (strength, text)

    a, b = _team_games(hist, me), _team_games(hist, opp)

    # 1. recent form
    f_me, f_opp = list(a["res"].tail(5)), list(b["res"].tail(5))
    p_me, p_opp = _pts(f_me), _pts(f_opp)
    if len(f_me) == 5 and (p_me >= 9 or p_me - p_opp >= 4):
        out.append((p_me - p_opp + 2,
                    f"Form: {n_me} {' '.join(f_me)} ({p_me} pt{'s' * (p_me != 1)} in last 5), "
                    f"{n_opp} {' '.join(f_opp)} ({p_opp} pt{'s' * (p_opp != 1)})"))

    # 2. goals in the last 6
    s_me, c_opp = int(a["gf"].tail(6).sum()), int(b["ga"].tail(6).sum())
    c_me = int(a["ga"].tail(6).sum())
    if len(a) >= 6 and (s_me >= 10 or c_opp >= 10):
        out.append(((s_me + c_opp) / 4,
                    f"Goals: {n_me} scored {s_me} in their last 6, {n_opp} conceded {c_opp} in theirs"))
    elif len(a) >= 6 and c_me <= 4:
        out.append((4, f"Defence: {n_me} conceded only {c_me} in their last 6"))

    # 3. home / away record this season
    if season_start is not None:
        venue = a[(a["Date"] >= season_start) & (a["is_home"] == (side == "home"))]
        if len(venue) >= 2:
            w, d = int((venue["res"] == "W").sum()), int((venue["res"] == "D").sum())
            l_ = len(venue) - w - d
            if w / len(venue) >= 0.5:
                where = "at home" if side == "home" else "away"
                out.append((w / len(venue) * 6,
                            f"Venue: {n_me} are {w}W-{d}D-{l_}L {where} this season"))

    # 4. head to head
    h2h = a[(a["HomeTeam"] == opp) | (a["AwayTeam"] == opp)].tail(6)
    if len(h2h) >= 3:
        w, l_ = int((h2h["res"] == "W").sum()), int((h2h["res"] == "L").sum())
        d = len(h2h) - w - l_
        if w > l_:
            out.append((2 + w - l_, f"Head to head: {n_me} won {w}, drew {d}, lost {l_} of the last {len(h2h)} meetings"))

    # 5. model strength ranking among this season's teams
    rt = model.ratings()
    if season_start is not None:
        current = set(hist[hist["Date"] >= season_start][["HomeTeam", "AwayTeam"]].stack())
        if len(current) >= 10:
            rt = rt[rt["team"].isin(current)].reset_index(drop=True)
    rank = {t: i + 1 for i, t in enumerate(rt["team"])}
    if me in rank and opp in rank and rank[opp] - rank[me] >= 4:
        out.append((3, f"Ratings: the model ranks {n_me} #{rank[me]} and {n_opp} #{rank[opp]} of {len(rt)}"))

    out.sort(key=lambda x: x[0], reverse=True)
    lines = [t for _, t in out[:3]]
    xg_h, xg_a = model.expected_goals(home, away)
    lines.append(f"Model projects {names.get(home, home)} {xg_h:.1f} – {xg_a:.1f} {names.get(away, away)} in expected goals")
    return lines
