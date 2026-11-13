"""Free live scores from ESPN's public scoreboard (no key, no credits).

Used for: goal alerts during matches, settling picks the moment a match ends,
and checking which leagues have fixtures before spending Odds API credits.
"""
from __future__ import annotations

import difflib
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone

from .live import _norm

URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
SLUGS = {"E0": "eng.1", "SP1": "esp.1", "I1": "ita.1", "D1": "ger.1", "F1": "fra.1", "UCL": "uefa.champions",
         "P1": "por.1", "N1": "ned.1"}
FINAL_EXTRA = ("AET", "PEN", "EXTRA")  # finished after extra time: 1X2 still settles on 90 minutes


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _minute(txt: str) -> int:
    """'34'' -> 34, '90'+3'' -> 90, '105'' -> 105."""
    m = re.match(r"\s*(\d+)", str(txt or ""))
    return int(m.group(1)) if m else 0


def parse(ev: dict) -> dict | None:
    try:
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        home, away = teams["home"], teams["away"]
    except (KeyError, IndexError, TypeError):
        return None
    st = ev.get("status") or comp.get("status") or {}
    t = st.get("type") or {}
    hid = str(home.get("team", {}).get("id"))
    goals = []
    for d in comp.get("details") or []:
        if not d.get("scoringPlay"):
            continue
        names = [a.get("displayName") or a.get("shortName") for a in d.get("athletesInvolved") or []]
        names = [n for n in names if n]
        side = "home" if str((d.get("team") or {}).get("id")) == hid else "away"
        goals.append({"minute": (d.get("clock") or {}).get("displayValue", ""), "side": side,
                      "scorer": names[0] if names else "", "own_goal": bool(d.get("ownGoal")),
                      "penalty": bool(d.get("penaltyKick")),
                      "kind": (d.get("type") or {}).get("text", "Goal")})
    name = str(t.get("name", ""))
    return {"id": str(ev.get("id")), "kickoff": ev.get("date", ""),
            "home": home.get("team", {}).get("displayName", ""), "away": away.get("team", {}).get("displayName", ""),
            "home_short": home.get("team", {}).get("shortDisplayName") or home.get("team", {}).get("displayName", ""),
            "away_short": away.get("team", {}).get("shortDisplayName") or away.get("team", {}).get("displayName", ""),
            "hs": _int(home.get("score")), "as": _int(away.get("score")),
            "state": t.get("state", ""), "completed": bool(t.get("completed")), "status": name,
            "detail": t.get("shortDetail") or t.get("detail") or "", "clock": st.get("displayClock", ""),
            "extra_time": any(x in name.upper() for x in FINAL_EXTRA), "goals": goals}


def scoreboard(league: str, dates: str | None = None, timeout: int = 20) -> list[dict]:
    """dates: 'YYYYMMDD' or 'YYYYMMDD-YYYYMMDD'. Raises on network errors."""
    url = URL.format(slug=SLUGS[league]) + (f"?dates={dates}&limit=200" if dates else "")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (edgebet)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return [e for e in (parse(ev) for ev in data.get("events", [])) if e]


def between(league: str, start: datetime, end: datetime) -> list[dict]:
    """Every game from start to end, one request per day (ESPN rejects date ranges)."""
    out, seen, d = [], set(), start.date()
    while d <= end.date():
        for e in scoreboard(league, f"{d:%Y%m%d}"):
            if e["id"] not in seen:
                seen.add(e["id"])
                out.append(e)
        d += timedelta(days=1)
    return out


def ninety_minute_score(e: dict) -> tuple[int, int]:
    """Score after 90 minutes (+ stoppage). Equals the final score unless extra time was played."""
    if not e["extra_time"]:
        return e["hs"], e["as"]
    h = sum(1 for g in e["goals"] if g["side"] == "home" and _minute(g["minute"]) <= 90)
    a = sum(1 for g in e["goals"] if g["side"] == "away" and _minute(g["minute"]) <= 90)
    return h, a


def _sim(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return max(overlap, difflib.SequenceMatcher(None, a, b).ratio())


def find(events: list[dict], home: str, away: str, kickoff: str, aliases: dict | None = None) -> dict | None:
    """The ESPN event for a pick (names from The Odds API), kickoff within 3 hours."""
    aliases = aliases or {}
    try:
        k = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00"))
    except ValueError:
        k = None
    best, best_s = None, 0.0
    for e in events:
        if k is not None and e["kickoff"]:
            ek = datetime.fromisoformat(e["kickoff"].replace("Z", "+00:00"))
            if abs(ek - k) > timedelta(hours=3):
                continue
        sh = max(_sim(home, e["home"]), _sim(aliases.get(home, home), e["home"]))
        sa = max(_sim(away, e["away"]), _sim(aliases.get(away, away), e["away"]))
        s = min(sh, sa) + 0.1 * max(sh, sa)
        if (min(sh, sa) >= 0.6 or max(sh, sa) >= 0.9) and s > best_s:
            best, best_s = e, s
    return best


def fixtures_between(league: str, start: datetime, end: datetime) -> int | None:
    """How many games ESPN lists in the window, or None if ESPN could not be reached."""
    try:
        evs = between(league, start, end)
    except Exception as e:  # noqa: BLE001
        print(f"  espn {league}: {e}")
        return None
    n = 0
    for e in evs:
        try:
            t = datetime.fromisoformat(e["kickoff"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if start <= t <= end and e["state"] == "pre":
            n += 1
    return n


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
