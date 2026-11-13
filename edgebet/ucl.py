"""Champions League: results from openfootball, club names mapped onto the
football-data.co.uk names, and one combined model across many leagues.

Champions League games are what link the leagues together (Arsenal v Atletico
tells the model how strong the Premier League is compared with La Liga), so
every team ends up rated on one common scale.
"""
from __future__ import annotations

import csv
import difflib
import glob
import os
import re
from datetime import datetime

import pandas as pd

from .data import load_league
from .live import _norm

COUNTRY_LEAGUE = {"ENG": "E0", "ESP": "SP1", "ITA": "I1", "GER": "D1", "FRA": "F1", "MCO": "F1",
                  "POR": "P1", "NED": "N1", "BEL": "B1", "TUR": "T1", "SCO": "SC0"}
LINK_LEAGUES = ["E0", "SP1", "I1", "D1", "F1", "P1", "N1", "B1", "T1", "SC0"]

# openfootball / The Odds API name -> football-data.co.uk name
UCL_ALIASES = {
    "Club Atlético de Madrid": "Ath Madrid", "Atletico Madrid": "Ath Madrid", "Atlético Madrid": "Ath Madrid",
    "Athletic Club": "Ath Bilbao", "Athletic Bilbao": "Ath Bilbao",
    "FC Bayern München": "Bayern Munich", "Bayern Munich": "Bayern Munich",
    "Borussia Dortmund": "Dortmund", "Bayer 04 Leverkusen": "Leverkusen", "Bayer Leverkusen": "Leverkusen",
    "Eintracht Frankfurt": "Ein Frankfurt", "VfB Stuttgart": "Stuttgart", "1. FC Union Berlin": "Union Berlin",
    "FC Internazionale Milano": "Inter", "Inter Milan": "Inter", "Internazionale": "Inter",
    "AC Milan": "Milan", "Paris Saint-Germain FC": "Paris SG", "Paris Saint Germain": "Paris SG",
    "Paris Saint-Germain": "Paris SG", "Olympique de Marseille": "Marseille", "Marseille": "Marseille",
    "Olympique Lyonnais": "Lyon", "Stade Brestois 29": "Brest", "Racing Club de Lens": "Lens",
    "Sporting Clube de Portugal": "Sp Lisbon", "Sporting Lisbon": "Sp Lisbon", "Sporting CP": "Sp Lisbon",
    "Sporting Clube de Braga": "Sp Braga", "SC Braga": "Sp Braga", "Sport Lisboa e Benfica": "Benfica",
    "FC Porto": "Porto", "PSV": "PSV Eindhoven", "PSV Eindhoven": "PSV Eindhoven",
    "Feyenoord Rotterdam": "Feyenoord", "Royale Union Saint-Gilloise": "St. Gilloise",
    "Union Saint-Gilloise": "St. Gilloise", "Union St. Gilloise": "St. Gilloise",
    "Real Sociedad de Fútbol": "Sociedad", "Real Sociedad": "Sociedad",
    "Manchester City FC": "Man City", "Manchester City": "Man City",
    "Manchester United FC": "Man United", "Manchester United": "Man United",
    "Newcastle United FC": "Newcastle", "Newcastle United": "Newcastle",
    "Tottenham Hotspur FC": "Tottenham", "Tottenham Hotspur": "Tottenham",
    "Club Brugge KV": "Club Brugge", "Royal Antwerp FC": "Antwerp", "KRC Genk": "Genk",
    "AFC Ajax": "Ajax", "Ajax Amsterdam": "Ajax", "AZ Alkmaar": "AZ Alkmaar",
    "Galatasaray SK": "Galatasaray", "Fenerbahçe SK": "Fenerbahce", "Beşiktaş JK": "Besiktas",
    "Celtic FC": "Celtic", "Rangers FC": "Rangers", "Villarreal CF": "Villarreal",
    "Real Betis Balompié": "Betis", "Real Betis": "Betis", "RC Celta de Vigo": "Celta",
    "Nottingham Forest": "Nott'm Forest", "Nottingham Forest FC": "Nott'm Forest",
}
_DATE = re.compile(r"^\s+(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\w{3}) (\d{1,2})(?: (\d{4}))?\s*$")
_MATCH = re.compile(r"^\s+(?:\d{1,2}:\d{2}\s+)?(?P<h>.+?)\s+\((?P<hc>[A-Z]{3})\)\s+v\s+"
                    r"(?P<a>.+?)\s+\((?P<ac>[A-Z]{3})\)\s+(?P<rest>\d.*)$")
_MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def parse_openfootball(path: str) -> list[dict]:
    """Read one openfootball cl.txt season. Uses the 90-minute score (1X2 settles on 90')."""
    out, year, last_month, day = [], None, None, None
    for line in open(path, encoding="utf-8"):
        m = _DATE.match(line)
        if m:
            mon, d = _MON[m.group(2)], int(m.group(3))
            if m.group(4):
                year = int(m.group(4))
            elif last_month and mon < last_month:
                year += 1
            last_month, day = mon, datetime(year, mon, d)
            continue
        m = _MATCH.match(line.rstrip("\n"))
        if not m or day is None:
            continue
        rest = m.group("rest")
        if "a.e.t." in rest:  # take the 90-minute score from the brackets
            s = re.search(r"\((\d+)-(\d+)", rest)
        else:
            s = re.match(r"(\d+)-(\d+)", rest)
        if not s:
            continue
        out.append({"Date": day, "home_raw": m.group("h").strip(), "home_cc": m.group("hc"),
                    "away_raw": m.group("a").strip(), "away_cc": m.group("ac"),
                    "FTHG": int(s.group(1)), "FTAG": int(s.group(2))})
    return out


_STRIP = re.compile(r"\b(fc|cf|afc|ssc|ss|ac|bc|osc|sk|kv|club|clube|de|da|do|sport|sv|rc|rcd|fk|cd|ud|sl|sc|jk)\b")


class Mapper:
    """Maps any club name (openfootball or bookmaker) onto football-data names."""

    def __init__(self, league_teams: dict[str, set[str]]):
        self.by_league = league_teams
        self.all = {t: lg for lg, ts in league_teams.items() for t in ts}

    @staticmethod
    def _key(name: str) -> str:
        return re.sub(r"\s+", " ", _STRIP.sub(" ", _norm(name))).strip()

    def map(self, name: str, country: str | None = None) -> str | None:
        if name in self.all:
            return name
        if UCL_ALIASES.get(name) in self.all:
            return UCL_ALIASES[name]
        pool = self.by_league.get(COUNTRY_LEAGUE.get(country, ""), set()) if country else set(self.all)
        if not pool:
            return None
        keys = {self._key(t): t for t in pool}
        k = self._key(name)
        if k in keys:
            return keys[k]
        hits = [t for kk, t in keys.items() if kk and (kk in k.split() or kk in k or k in kk)]
        if len(hits) == 1:
            return hits[0]
        close = difflib.get_close_matches(k, list(keys), n=1, cutoff=0.8 if country is None else 0.7)
        return keys[close[0]] if close else None


def _with_ftr(df: pd.DataFrame) -> pd.DataFrame:
    df["FTR"] = ["H" if h > a else "A" if a > h else "D" for h, a in zip(df["FTHG"], df["FTAG"])]
    return df


def load_combined(data_dir: str = "data", since: pd.Timestamp | None = None,
                  until: pd.Timestamp | None = None, verbose: bool = False):
    """All linked domestic leagues + every Champions League result, one frame.
    Returns (frame, mapper, unmatched names)."""
    frames, teams = [], {}
    for lg in LINK_LEAGUES:
        folder = os.path.join(data_dir, lg)
        if not glob.glob(os.path.join(folder, "*.csv")):
            continue
        d = load_league(folder)[["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]].copy()
        if since is not None:
            d = d[d["Date"] >= since - pd.Timedelta(days=400)]  # team lists from recent seasons only
        d["comp"] = lg
        frames.append(d)
        teams[lg] = set(d["HomeTeam"]) | set(d["AwayTeam"])
    mapper = Mapper(teams)

    rows, unmatched = [], set()
    for f in sorted(glob.glob(os.path.join(data_dir, "UCL", "cl-*.txt"))):
        for r in parse_openfootball(f):
            h, a = mapper.map(r["home_raw"], r["home_cc"]), mapper.map(r["away_raw"], r["away_cc"])
            if r["home_cc"] in COUNTRY_LEAGUE and not h:
                unmatched.add(f'{r["home_raw"]} ({r["home_cc"]})')
            if r["away_cc"] in COUNTRY_LEAGUE and not a:
                unmatched.add(f'{r["away_raw"]} ({r["away_cc"]})')
            rows.append({"Date": r["Date"], "HomeTeam": h or f'{r["home_raw"]} ({r["home_cc"]})',
                         "AwayTeam": a or f'{r["away_raw"]} ({r["away_cc"]})',
                         "FTHG": r["FTHG"], "FTAG": r["FTAG"], "comp": "UCL"})
    # results the bot collected itself from The Odds API (current season)
    live_path = os.path.join(data_dir, "UCL", "live_results.csv")
    if os.path.exists(live_path):
        for r in csv.DictReader(open(live_path, encoding="utf-8")):
            h, a = mapper.map(r["home"]), mapper.map(r["away"])
            rows.append({"Date": pd.Timestamp(r["date"]), "HomeTeam": h or r["home"], "AwayTeam": a or r["away"],
                         "FTHG": int(r["home_goals"]), "FTAG": int(r["away_goals"]), "comp": "UCL"})
    if rows:
        u = _with_ftr(pd.DataFrame(rows))
        u = u.drop_duplicates(subset=["Date", "HomeTeam", "AwayTeam"])
        frames.append(u)
    df = pd.concat(frames, ignore_index=True).sort_values("Date").reset_index(drop=True)
    if since is not None:
        df = df[df["Date"] >= since]
    if until is not None:
        df = df[df["Date"] < until]
    if verbose and unmatched:
        print("  Champions League names not matched (rated separately):", sorted(unmatched))
    return df, mapper, unmatched


def save_live_results(events: list, path: str) -> int:
    """Append finished Champions League games from The Odds API scores feed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    seen = set()
    if os.path.exists(path):
        seen = {r["event_id"] for r in csv.DictReader(open(path, encoding="utf-8"))}
    new = []
    for ev in events:
        if not ev.get("completed") or not ev.get("scores") or ev["id"] in seen:
            continue
        s = {x["name"]: x["score"] for x in ev["scores"]}
        try:
            hg, ag = int(s[ev["home_team"]]), int(s[ev["away_team"]])
        except (KeyError, ValueError):
            continue
        new.append({"event_id": ev["id"], "date": ev["commence_time"][:10], "home": ev["home_team"],
                    "away": ev["away_team"], "home_goals": hg, "away_goals": ag})
    if new:
        exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(new[0]))
            if not exists:
                w.writeheader()
            w.writerows(new)
    return len(new)
