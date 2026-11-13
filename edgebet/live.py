"""Live data: upcoming odds and final scores from The Odds API (the-odds-api.com),
plus matching their team names to football-data.co.uk names used by the model.
"""
from __future__ import annotations

import csv
import difflib
import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.the-odds-api.com/v4/sports/{sport}/{endpoint}"
CREDITS = {"left": None}  # updated from every Odds API reply

# league folder (football-data code) -> The Odds API sport key
LEAGUES = {
    "E0": ("soccer_epl", "Premier League", "#EPL"),
    "SP1": ("soccer_spain_la_liga", "La Liga", "#LaLiga"),
    "I1": ("soccer_italy_serie_a", "Serie A", "#SerieA"),
    "D1": ("soccer_germany_bundesliga", "Bundesliga", "#Bundesliga"),
    "F1": ("soccer_france_ligue_one", "Ligue 1", "#Ligue1"),
    "UCL": ("soccer_uefa_champs_league", "Champions League", "#UCL"),
}

# The Odds API name -> football-data.co.uk name, where fuzzy matching is not enough.
# Add your own in team_aliases.csv (columns: api_name,model_name).
ALIASES = {
    "Manchester United": "Man United", "Manchester City": "Man City",
    "Nottingham Forest": "Nott'm Forest", "Wolverhampton Wanderers": "Wolves",
    "Tottenham Hotspur": "Tottenham", "Brighton and Hove Albion": "Brighton",
    "Newcastle United": "Newcastle", "West Ham United": "West Ham",
    "Leicester City": "Leicester", "Leeds United": "Leeds", "Ipswich Town": "Ipswich",
    "Atlético Madrid": "Ath Madrid", "Atletico Madrid": "Ath Madrid",
    "Athletic Bilbao": "Ath Bilbao", "Real Betis": "Betis", "Celta Vigo": "Celta",
    "Rayo Vallecano": "Vallecano", "Real Sociedad": "Sociedad", "Espanyol": "Espanol",
    "Inter Milan": "Inter", "AC Milan": "Milan", "AS Roma": "Roma", "Hellas Verona": "Verona",
    "Borussia Monchengladbach": "M'gladbach", "Borussia Dortmund": "Dortmund",
    "Bayer Leverkusen": "Leverkusen", "Eintracht Frankfurt": "Ein Frankfurt",
    "1. FC Köln": "FC Koln", "FC Koln": "FC Koln", "FC St. Pauli": "St Pauli",
    "Paris Saint Germain": "Paris SG", "Paris Saint-Germain": "Paris SG",
    "Olympique Lyonnais": "Lyon", "Olympique Marseille": "Marseille",
    "Saint Etienne": "St Etienne", "Stade Brestois 29": "Brest", "Stade Rennais": "Rennes",
}
_NOISE = {"fc", "afc", "cf", "sc", "ac", "as", "ssc", "club", "the", "and", "de", "vfb", "vfl", "tsg", "fsv", "1"}


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = [w for w in re.split(r"[^a-z0-9]+", s) if w and w not in _NOISE]
    return " ".join(words)


class TeamMatcher:
    """Maps bookmaker team names onto the names the model was trained on."""

    def __init__(self, model_teams, alias_file: str = "team_aliases.csv"):
        self.teams = list(model_teams)
        self.norm = {_norm(t): t for t in self.teams}
        self.aliases = dict(ALIASES)
        if os.path.exists(alias_file):
            with open(alias_file, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    self.aliases[row["api_name"].strip()] = row["model_name"].strip()

    def match(self, name: str) -> str | None:
        if name in self.teams:
            return name
        if self.aliases.get(name) in self.teams:
            return self.aliases[name]
        n = _norm(name)
        if n in self.norm:
            return self.norm[n]
        # one name containing the other, e.g. "brentford" in "brentford"
        hits = [t for k, t in self.norm.items() if k and (k in n or n in k)]
        if len(hits) == 1:
            return hits[0]
        close = difflib.get_close_matches(n, list(self.norm), n=1, cutoff=0.75)
        return self.norm[close[0]] if close else None


def _get(sport: str, endpoint: str, params: dict) -> list:
    key = (os.environ.get("ODDS_API_KEY") or "").strip().strip('"').strip("'")
    key = key.split("=", 1)[1].strip() if key.upper().startswith("ODDS_API_KEY=") else key
    if not key:
        raise RuntimeError("ODDS_API_KEY is not set (get a free key at the-odds-api.com).")
    url = API.format(sport=sport, endpoint=endpoint) + "?" + urllib.parse.urlencode({**params, "apiKey": key})
    try:
        r = urllib.request.urlopen(url, timeout=30)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        raise RuntimeError(f"The Odds API refused {sport}/{endpoint}: HTTP {e.code} {body}") from None
    with r:
        left = r.headers.get("x-requests-remaining")
        if left is not None:
            print(f"  odds-api credits left: {left}")
            try:
                CREDITS["left"] = int(float(left))
            except ValueError:
                pass
        return json.loads(r.read().decode())


def fetch_odds(sport: str, regions: str | None = None) -> list:
    """Each region costs 1 credit per market per call. 'eu' already includes Pinnacle.
    ODDS_MARKETS=h2h,totals also fetches over/under, unless credits are running low."""
    regions = regions or os.environ.get("ODDS_REGIONS", "eu")
    mk = os.environ.get("ODDS_MARKETS", "h2h")
    low = int(os.environ.get("MIN_CREDITS_FOR_EXTRA", "60"))
    if mk != "h2h" and CREDITS["left"] is not None and CREDITS["left"] < low:
        print(f"  credits below {low}: winner market only")
        mk = "h2h"
    return _get(sport, "odds", {"regions": regions, "markets": mk, "oddsFormat": "decimal"})


def fetch_sports() -> list:
    """Every competition The Odds API covers right now. Free: costs no credits."""
    key = (os.environ.get("ODDS_API_KEY") or "").strip().strip('"').strip("'")
    key = key.split("=", 1)[1].strip() if key.upper().startswith("ODDS_API_KEY=") else key
    url = "https://api.the-odds-api.com/v4/sports?" + urllib.parse.urlencode({"apiKey": key})
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_scores(sport: str, days_from: int = 3) -> list:
    return _get(sport, "scores", {"daysFrom": days_from})


def parse_event(ev: dict, bet_book: str = "best") -> dict | None:
    """Pull home/draw/away prices out of one Odds API event.

    bet_book: 'best' (best price across books), 'avg' (average), or a bookmaker
    key such as 'unibet_eu'. Pinnacle is always read separately as the sharp line.
    """
    home, away = ev["home_team"], ev["away_team"]
    books = {}
    for b in ev.get("bookmakers", []):
        for m in b.get("markets", []):
            if m["key"] != "h2h":
                continue
            px = {o["name"]: o["price"] for o in m["outcomes"]}
            if home in px and away in px and "Draw" in px:
                books[b["key"]] = (b["title"], [px[home], px["Draw"], px[away]])
    if not books:
        return None
    sharp = books.get("pinnacle", (None, None))[1]
    # betting exchanges show prices before commission, so they look better than they are
    exch = ("betfair_ex", "matchbook", "smarkets")
    others = {k: v for k, v in books.items()
              if k != "pinnacle" and not k.startswith(exch)} or books
    if bet_book == "best":
        odds, names = [], []
        for i in range(3):
            k = max(others, key=lambda k: others[k][1][i])
            odds.append(others[k][1][i])
            names.append(others[k][0])
    elif bet_book == "avg":
        odds = [round(sum(v[1][i] for v in others.values()) / len(others), 2) for i in range(3)]
        names = ["market avg"] * 3
    else:
        if bet_book not in books:
            return None
        odds, names = books[bet_book][1], [books[bet_book][0]] * 3
    # over / under 2.5 goals, when the totals market was fetched
    tot = {}
    for b in ev.get("bookmakers", []):
        for m in b.get("markets", []):
            if m["key"] != "totals":
                continue
            px = {o["name"].lower(): o["price"] for o in m["outcomes"] if abs(float(o.get("point", 0)) - 2.5) < 1e-6}
            if "over" in px and "under" in px:
                tot[b["key"]] = (b["title"], px["over"], px["under"])
    totals = None
    if tot:
        soft = {k: v for k, v in tot.items() if k != "pinnacle" and not k.startswith(exch)} or tot
        ko = max(soft, key=lambda k: soft[k][1])
        ku = max(soft, key=lambda k: soft[k][2])
        totals = {"best": (soft[ko][1], soft[ku][2]), "books": (soft[ko][0], soft[ku][0]),
                  "sharp": (tot["pinnacle"][1], tot["pinnacle"][2]) if "pinnacle" in tot else None}
    return {"event_id": ev["id"], "kickoff": ev["commence_time"], "home": home, "away": away,
            "odds": odds, "book_names": names, "sharp": sharp, "n_books": len(books), "totals": totals}


def upcoming(events: list, hours: int = 30, now: datetime | None = None) -> list:
    now = now or datetime.now(timezone.utc)
    end = now + timedelta(hours=hours)
    out = []
    for ev in events:
        t = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if now < t <= end:
            out.append(ev)
    return out


def final_score(ev: dict) -> tuple[int, int] | None:
    if not ev.get("completed") or not ev.get("scores"):
        return None
    s = {x["name"]: int(x["score"]) for x in ev["scores"]}
    return s.get(ev["home_team"]), s.get(ev["away_team"])
