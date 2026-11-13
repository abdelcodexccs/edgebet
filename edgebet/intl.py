"""National teams: Nations League, World Cup / Euro / AFCON qualifiers, friendlies and
tournaments, so the channel keeps posting during international breaks.

Results: the free international results dataset (every men's international since 1872,
https://github.com/martj42/international_results). Competitions running right now are
found through The Odds API sports list, which costs no credits.
"""
from __future__ import annotations

import os
import time
import urllib.request

import pandas as pd

from . import live

RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
CACHE = os.path.join(".cache", "intl_results.csv")

# Odds API sport keys that are national-team football
KEY_PARTS = ("fifa_world_cup", "nations_league", "uefa_european_championship", "euro_qualification",
             "copa_america", "africa_cup_of_nations", "international_friendl", "gold_cup", "asian_cup",
             "world_cup_qualif", "uefa_euro")
# final tournaments are played at neutral grounds (qualifiers, Nations League and friendlies are not)
NEUTRAL_KEYS = {"soccer_fifa_world_cup", "soccer_uefa_european_championship", "soccer_conmebol_copa_america",
                "soccer_africa_cup_of_nations", "soccer_concacaf_gold_cup", "soccer_afc_asian_cup"}
TAGS = {"nations_league": "#NationsLeague", "world_cup": "#WorldCup", "euro": "#EURO",
        "africa_cup": "#AFCON", "copa_america": "#CopaAmerica", "friendl": "#Friendly"}

# bookmaker name -> name in the results dataset
ALIASES = {
    "USA": "United States", "United States of America": "United States", "Korea Republic": "South Korea",
    "Republic of Korea": "South Korea", "Korea DPR": "North Korea", "IR Iran": "Iran", "Türkiye": "Turkey",
    "Turkiye": "Turkey", "Czechia": "Czech Republic", "Côte d'Ivoire": "Ivory Coast",
    "Cote d'Ivoire": "Ivory Coast", "Cape Verde Islands": "Cape Verde", "Cabo Verde": "Cape Verde",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina", "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Ireland": "Republic of Ireland", "Rep. of Ireland": "Republic of Ireland", "China PR": "China",
    "Congo DR": "DR Congo", "DR Congo": "DR Congo", "Democratic Republic of Congo": "DR Congo",
    "Congo": "Congo", "Kyrgyz Republic": "Kyrgyzstan", "FYR Macedonia": "North Macedonia",
    "Macedonia": "North Macedonia", "Swaziland": "Eswatini", "Chinese Taipei": "Taiwan",
    "St Kitts and Nevis": "Saint Kitts and Nevis", "St Lucia": "Saint Lucia",
    "St Vincent and the Grenadines": "Saint Vincent and the Grenadines", "UAE": "United Arab Emirates",
}


def is_international(sport: dict) -> bool:
    key = sport.get("key", "")
    return (key.startswith("soccer_") and any(p in key for p in KEY_PARTS)
            and not sport.get("has_outrights") and sport.get("active", True))


def neutral(sport_key: str) -> bool:
    return sport_key in NEUTRAL_KEYS


def hashtag(sport_key: str) -> str:
    for part, tag in TAGS.items():
        if part in sport_key:
            return tag
    return "#International"


def refresh(max_age_hours: float = 12) -> str:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    if not os.path.exists(CACHE) or time.time() - os.path.getmtime(CACHE) > max_age_hours * 3600:
        req = urllib.request.Request(RESULTS_URL, headers={"User-Agent": "Mozilla/5.0 (edgebet)"})
        with urllib.request.urlopen(req, timeout=60) as r, open(CACHE + ".tmp", "wb") as f:
            f.write(r.read())
        os.replace(CACHE + ".tmp", CACHE)
    return CACHE


def load(since: pd.Timestamp, until: pd.Timestamp) -> pd.DataFrame:
    """Played internationals in [since, until) in the model's column names."""
    d = pd.read_csv(refresh())
    d["Date"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna(subset=["Date", "home_score", "away_score"])
    d = d[(d["Date"] >= since) & (d["Date"] < until)]
    out = pd.DataFrame({
        "Date": d["Date"], "HomeTeam": d["home_team"], "AwayTeam": d["away_team"],
        "FTHG": d["home_score"].astype(int), "FTAG": d["away_score"].astype(int),
        "Neutral": d["neutral"].astype(str).str.upper().eq("TRUE").astype(float),
        "Weight": d["tournament"].eq("Friendly").map({True: 0.6, False: 1.0}),
        "Tournament": d["tournament"],
    })
    out["FTR"] = ["H" if h > a else "A" if a > h else "D" for h, a in zip(out["FTHG"], out["FTAG"])]
    return out.sort_values("Date").reset_index(drop=True)


def matcher(teams) -> live.TeamMatcher:
    m = live.TeamMatcher(teams)
    m.aliases.update(ALIASES)
    return m
