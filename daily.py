"""The whole daily cycle in one command.

  1. refresh this season's results (football-data.co.uk)
  2. settle finished picks (ESPN free scores; The Odds API as fallback)
  3. fit the model and price the next ~30 hours of fixtures
  4. choose the best value bet(s) under the backtested rules
  5. post results + today's pick + reasons + running record to your Telegram
     channel (free) and/or X (dry run unless POST_MODE=live)
  6. send yourself a private Telegram alert with the suggested stake

Usage:
    python daily.py                 # uses settings in .env
    python daily.py --mock tests/mock --now 2018-05-12T09:00:00Z   # offline test
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from edgebet import announce, espn, intl, ledger, live, markets, reasons, ucl
from edgebet.data import load_league
from edgebet.model import DixonColes
from edgebet.strategy import OUTCOMES, Rules, pick, probabilities


# Extra bet types switched on, with the rules that held up in the market backtest:
# pick code -> (min odds, max odds, min edge for a VALUE pick). Codes not listed are off.
MARKET_RULES: dict = {}
# "Pick of the day" on days without a value bet: "likely" = the most likely bet of the day
# (team to win, double chance or draw no bet); "edge" = the old rule (closest to a value bet).
POD_MODE = os.environ.get("POD_MODE", "likely")
POD_MARKETS = ("home", "away", "1X", "X2", "dnb_home", "dnb_away")

def load_env(path=".env"):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.split(" #", 1)[0]  # allow trailing comments
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def cfg(key, default):
    v = os.environ.get(key, default)
    return type(default)(v) if not isinstance(default, bool) else str(v).lower() in ("1", "true", "yes")


def local_time(iso: str) -> str:
    """Kickoff in your time zone, e.g. 'Sat 22 Apr, 15:00'."""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    try:
        t = t.astimezone(ZoneInfo(cfg("TIMEZONE", "Africa/Casablanca")))
    except Exception:  # noqa: BLE001
        pass
    return t.strftime("%a %d %b, %H:%M")


def refresh_season(league: str, data_dir: str, now: datetime):
    y = now.year if now.month >= 7 else now.year - 1
    if league == "UCL":
        season = f"{y}-{(y + 1) % 100:02d}"
        dest = os.path.join(data_dir, "UCL", f"cl-{season}.txt")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            urllib.request.urlretrieve(
                f"https://raw.githubusercontent.com/openfootball/champions-league/master/{season}/cl.txt", dest)
            print(f"  refreshed {dest}")
        except Exception:  # noqa: BLE001
            pass  # not published yet; the bot collects this season's results itself
        for lg in ucl.LINK_LEAGUES:
            if lg not in refreshed:
                refresh_season(lg, data_dir, now)
        return
    if league in refreshed:
        return
    refreshed.add(league)
    code = f"{y % 100:02d}{(y + 1) % 100:02d}"
    dest = os.path.join(data_dir, league, f"{league}-{y}.csv")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        urllib.request.urlretrieve(f"https://www.football-data.co.uk/mmz4281/{code}/{league}.csv", dest)
        print(f"  refreshed {dest}")
    except Exception as e:  # noqa: BLE001
        print(f"  could not refresh {league} {y}: {e} (using existing files)")


refreshed: set = set()


class Feed:
    """Real API calls, or JSON files from --mock for offline testing."""

    def __init__(self, mock_dir=None):
        self.mock = mock_dir
        self._scores = {}

    def _mock(self, kind, sport):
        p = os.path.join(self.mock, f"{kind}_{sport}.json")
        return json.load(open(p)) if os.path.exists(p) else []

    def odds(self, sport):
        return self._mock("odds", sport) if self.mock else live.fetch_odds(sport)

    def scores(self, sport):
        if sport not in self._scores:
            self._scores[sport] = self._mock("scores", sport) if self.mock else live.fetch_scores(sport)
        return self._scores[sport]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", help="folder with odds_<sport>.json / scores_<sport>.json")
    ap.add_argument("--now", help="pretend current UTC time (testing)")
    ap.add_argument("--env", default=".env")
    args = ap.parse_args()
    load_env(args.env)

    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(timezone.utc)
    leagues = [x.strip() for x in cfg("LEAGUES", "E0").split(",") if x.strip()]
    data_dir = cfg("HISTORY_DIR", "data")
    ledger_path = cfg("LEDGER", "state/ledger.csv")
    live_mode = cfg("POST_MODE", "dry") == "live"
    bankroll = cfg("BANKROLL", 1000.0)
    rules = Rules(min_ev=cfg("MIN_EV", Rules.min_ev), max_odds=cfg("MAX_ODDS", Rules.max_odds),
                  blend=cfg("BLEND", Rules.blend))
    feed = Feed(args.mock)
    if not args.mock and announce.channel_configured():
        ch = announce.channel_info()  # saves the channel's permanent id (renaming won't break posting)
        print(f"  channel: {ch.get('title', '')} @{ch.get('username', '')}")
    book = ledger.load(ledger_path)
    alerts = []

    # 1-2. refresh results and settle pending picks
    settled = []
    use_espn = not args.mock and cfg("USE_ESPN", True)
    for lg in leagues:
        if cfg("REFRESH_DATA", True) and not args.mock:
            refresh_season(lg, data_dir, now)
        sport = live.LEAGUES[lg][0]
        pend = book[(book["status"] == "pending") & (book["sport_key"] == sport)]
        if not len(pend):
            continue
        if use_espn:  # free: the live watcher normally settles these already
            try:
                evs = espn.between(lg, now - timedelta(days=4), now)
                for row in pend.to_dict("records"):
                    e = espn.find(evs, row["home"], row["away"], row["kickoff"])
                    if e and e["completed"] and e["hs"] is not None:
                        r = ledger.settle(book, row["event_id"], *espn.ninety_minute_score(e))
                        if r:
                            settled.append(r)
            except Exception as e:  # noqa: BLE001
                print(f"  espn results {lg} failed: {e}")
        pend = book[(book["status"] == "pending") & (book["sport_key"] == sport)]
        finished = pd.to_datetime(pend["kickoff"], utc=True) < pd.Timestamp(now) - pd.Timedelta(hours=2, minutes=30)
        if finished.any():  # fallback: The Odds API (2 credits)
            for ev in feed.scores(sport):
                fs = live.final_score(ev)
                if fs and None not in fs:
                    r = ledger.settle(book, ev["id"], *fs)
                    if r:
                        settled.append(r)
    for row in book[(book["status"] == "pending") & (book["league"] == "INT")].to_dict("records"):
        k = pd.Timestamp(row["kickoff"])
        if k > pd.Timestamp(now) - pd.Timedelta(hours=2):
            continue
        e = None
        if use_espn:
            _, e = espn.find_any(espn.slugs_for("INT", row["sport_key"]), row["home"], row["away"], row["kickoff"])
        if e and e["completed"] and e["hs"] is not None:
            r = ledger.settle(book, row["event_id"], *espn.ninety_minute_score(e))
        else:  # fallback: The Odds API (2 credits)
            r = None
            for ev in feed.scores(row["sport_key"]):
                fs = live.final_score(ev)
                if ev["id"] == row["event_id"] and fs and None not in fs:
                    r = ledger.settle(book, ev["id"], *fs)
        if r:
            settled.append(r)
    if "UCL" in leagues and now.weekday() in (2, 3, 4) and use_espn:  # Wed-Fri: collect Tue-Thu results
        try:
            evs = [e for e in espn.between("UCL", now - timedelta(days=3), now)
                   if e["completed"] and e["hs"] is not None]
            as_api = [{"id": "espn-" + e["id"], "completed": True, "commence_time": e["kickoff"],
                       "home_team": e["home"], "away_team": e["away"],
                       "scores": [{"name": e["home"], "score": espn.ninety_minute_score(e)[0]},
                                  {"name": e["away"], "score": espn.ninety_minute_score(e)[1]}]} for e in evs]
            n = ucl.save_live_results(as_api, os.path.join(data_dir, "UCL", "live_results.csv"))
            if n:
                print(f"  saved {n} new Champions League results")
        except Exception as e:  # noqa: BLE001
            print(f"  espn UCL results failed: {e}")
    if settled:
        ledger.save(book, ledger_path)

    # 3-4. price upcoming fixtures (skipped on the evening results-only run)
    candidates, leans, scan = [], [], []
    today_str = now.date().isoformat()
    posted_today = int(book["posted_at"].astype(str).str.startswith(today_str).sum()) if len(book) else 0
    quota_full = posted_today >= cfg("MAX_PICKS_PER_DAY", 1)
    if quota_full and not cfg("RESULTS_ONLY", False):
        print(f"Today's {posted_today} pick(s) already posted: no pricing, no credits spent")
    look = cfg("LOOKAHEAD_HOURS", 30)

    def price(lg, sport, name, tag, events, hist, model, matcher, min_n, season_start, neutral=False):
        for ev in events:
            if (book["event_id"] == ev["id"]).any():
                continue  # already posted
            e = live.parse_event(ev, cfg("BET_BOOK", "best"))
            if e is None:
                scan.append(f"{ev['home_team']} v {ev['away_team']}: no 1X2 odds")
                continue
            h, a = matcher.match(e["home"]), matcher.match(e["away"])
            if not h or not a:
                if lg in ("UCL", "INT"):  # clubs/countries the model doesn't cover
                    scan.append(f"{e['home']} v {e['away']}: team not rated")
                else:
                    alerts.append(f"Unmatched team: {e['home']} / {e['away']} -> add to team_aliases.csv")
                continue
            if not (model.knows(h, min_n) and model.knows(a, min_n)):
                scan.append(f"{e['home']} v {e['away']}: not enough history for a team")
                continue
            if e["sharp"] is None and cfg("REQUIRE_SHARP", True):
                scan.append(f"{e['home']} v {e['away']}: no Pinnacle price ({e['n_books']} books)")
                continue
            mp = model.predict(h, a, neutral)
            p = probabilities(mp, e["sharp"], rules.blend if e["sharp"] else 1.0)
            b = pick(p, e["odds"], rules)
            evs = [p[k] * e["odds"][k] - 1 for k in range(3)]
            scan.append(f"[{lg}] {e['home']} v {e['away']}: ours H/D/A " + "/".join(f"{x:.0%}" for x in p)
                        + " | best odds " + "/".join(f"{x:.2f}" for x in e["odds"])
                        + " | EV " + "/".join(f"{x:+.1%}" for x in evs) + (" <- PICK" if b else ""))
            # more bet types: double chance, draw no bet, over/under
            allx = markets.extra_bets(e["odds"], e["sharp"], p, e.get("totals"), mp["over25"],
                                      rules.blend if e["sharp"] else 1.0)
            extra = [x for x in allx if x["outcome"] in MARKET_RULES and
                     MARKET_RULES[x["outcome"]][0] <= x["odds"] <= MARKET_RULES[x["outcome"]][1]]
            for x in extra:  # a market switched on as VALUE bets (none are by default: none beat the market)
                if x["ev"] >= MARKET_RULES[x["outcome"]][2] and (b is None or x["ev"] > b["ev"]):
                    b = x
            if allx:
                scan.append("      more bets: " + ", ".join(
                    f"{markets.label(x['outcome'], 'H', 'A')} {x['odds']:.2f} ({x['p']:.0%}) EV {x['ev']:+.1%}"
                    for x in allx))

            if POD_MODE == "likely":
                # pick of the day = the most likely bet (win, double chance or draw no bet) where the model
                # agrees with the market. 10-year test: ~80% hit rate, about break-even.
                pool = [x for x in allx if x["outcome"] in POD_MARKETS]
                for k, code in ((0, "home"), (2, "away")):
                    if code in POD_MARKETS:
                        o = float(e["odds"][k])
                        pool.append({"outcome": code, "k": k, "odds": o, "p": float(p[k]), "implied": 1 / o,
                                     "ev": float(p[k] * o - 1), "conf": float(p[k])})
                pool = [x for x in pool if x["odds"] >= cfg("POD_MIN_ODDS", 1.2) and x["ev"] >= -0.02]
                lean = max(pool, key=lambda x: x["p"]) if pool else None
                if lean:
                    lean = {**lean, "rank": lean["p"]}
            else:  # best playable side even below the value bar
                lean = pick(p, e["odds"], Rules(**{**rules.__dict__, "min_ev": -1.0}))
                if lean:
                    lean = {**lean, "rank": lean["ev"]}

            def make_row(bet, tier):
                code = bet["outcome"]
                side = {"1X": "home", "dnb_home": "home", "X2": "away", "dnb_away": "away"}.get(code, code)
                names_ = {h: e["home"], a: e["away"]}
                if side in ("home", "away"):
                    why = reasons.build(hist, model, h, a, side, names_, season_start)
                elif side.startswith(("over", "under")):
                    why = reasons.goals(hist, model, h, a, side, names_, season_start)
                else:
                    why = []
                model_p = {"1X": mp["home"] + mp["draw"], "X2": mp["away"] + mp["draw"],
                           "dnb_home": mp["home"] / (1 - mp["draw"]), "dnb_away": mp["away"] / (1 - mp["draw"]),
                           "over2.5": mp["over25"], "under2.5": 1 - mp["over25"]}.get(code, mp.get(code))
                if code in ("1X", "X2", "dnb_home", "dnb_away"):
                    book_name = f"{e['book_names'][bet['k']]} + {e['book_names'][1]}"
                elif code in ("over2.5", "under2.5"):
                    book_name = e["totals"]["books"][0 if code == "over2.5" else 1]
                else:
                    book_name = e["book_names"][bet["k"]]
                stake = bet.get("stake_frac", 0) if tier == "value" else cfg("LEAN_STAKE", 0.005)
                return {**e, **bet, "stake_frac": stake, "league": lg, "league_name": name, "sport_key": sport,
                        "hashtag": tag, "team": markets.short(code, e["home"], e["away"]), "model_p": model_p,
                        "final_p": bet["p"], "book": book_name, "why": why, "tier": tier}

            if b:
                candidates.append(make_row(b, "value"))
            elif lean:
                leans.append(make_row(lean, "safe" if POD_MODE == "likely" else "lean"))

    today = pd.Timestamp(now.replace(tzinfo=None))
    season_start = pd.Timestamp(now.year if now.month >= 7 else now.year - 1, 7, 1)
    pricing = not cfg("RESULTS_ONLY", False) and not quota_full
    club_games = 0
    for lg in (leagues if pricing else []):
        sport, name, tag = live.LEAGUES[lg]
        if use_espn and lg in espn.SLUGS:  # free fixture check before spending an Odds API credit
            n_fix = espn.fixtures_between(lg, now, now + timedelta(hours=look))
            if n_fix == 0:
                print(f"  {name}: no games in the next {look}h (ESPN), odds not fetched")
                continue
        events = live.upcoming(feed.odds(sport), look, now)
        if not events:
            continue
        club_games += len(events)
        if lg == "UCL":
            # one model across all linked leagues + every Champions League result
            hist, mapper, _ = ucl.load_combined(data_dir, since=today - pd.Timedelta(days=3 * 365), until=today)
            matcher = mapper
            matcher.match = mapper.map
            min_n = cfg("UCL_MIN_MATCHES", 25)
        else:
            hist = load_league(os.path.join(data_dir, lg))
            hist = hist[(hist["Date"] < today) & (hist["Date"] >= today - pd.Timedelta(days=3 * 365))]
            matcher = None
            min_n = rules.min_matches
        model = DixonColes().fit(hist, as_of=pd.Timestamp(now.date()))
        if matcher is None:
            matcher = live.TeamMatcher(model.teams)
        price(lg, sport, name, tag, events, hist, model, matcher, min_n, season_start)

    # 3b. national teams (Nations League, qualifiers, friendlies...) when the clubs are on a break
    if pricing and cfg("INTERNATIONALS", True) and not args.mock and (club_games == 0 or cfg("INTL_ALWAYS", False)):
        try:
            comps = [c for c in live.fetch_sports() if intl.is_international(c)]
        except Exception as e:  # noqa: BLE001
            comps = []
            print(f"  could not list international competitions: {e}")
        print("  international competitions on now: " + (", ".join(c["title"] for c in comps) or "none"))
        model = hist = matcher = None
        for c in comps:
            events = live.upcoming(feed.odds(c["key"]), look, now)
            if not events:
                continue
            if model is None:
                hist = intl.load(today - pd.Timedelta(days=cfg("INTL_YEARS", 6) * 365), today)
                model = DixonColes(xi=cfg("INTL_XI", 0.0012)).fit(hist, as_of=pd.Timestamp(now.date()))
                matcher = intl.matcher(model.teams)
                print(f"  national-team model: {len(hist)} games, {len(model.teams)} teams, "
                      f"home edge {model.home_adv:.2f}")
            price("INT", c["key"], c["title"], intl.hashtag(c["key"]), events, hist, model, matcher,
                  cfg("INTL_MIN_MATCHES", 15), None, intl.neutral(c["key"]))

    print(f"\nGames checked in the next {cfg('LOOKAHEAD_HOURS', 30)}h: "
          f"{sum(1 for x in scan if not x.startswith('      '))}")
    for line in scan:
        print("  " + line)
    print(f"Rule: bet only if EV >= {rules.min_ev:+.0%}, odds {rules.min_odds}-{rules.max_odds}, no draws\n")
    candidates.sort(key=lambda c: c["ev"], reverse=True)
    # a second run on the same day (backup schedule) only fills what's left of today's quota
    left = max(0, cfg("MAX_PICKS_PER_DAY", 1) - posted_today)
    picks = candidates[:left]
    # Daily mode: no value bet today -> still post the best game, clearly labelled
    if not picks and leans and cfg("DAILY_PICK", False) and posted_today == 0:
        leans.sort(key=lambda c: c["rank"], reverse=True)
        picks = leans[:1]
        print(f"No value bet, daily mode: posting best game as Pick of the day "
              f"({picks[0]['team']} @ {picks[0]['odds']:.2f}, edge {picks[0]['ev']:+.1%})")

    # 5-6. compose, publish, alert
    targets = [t.strip() for t in cfg("PUBLISH_TO", "telegram").split(",") if t.strip()]
    footer = cfg("CHANNEL_FOOTER", "18+ · No bet is certain · Only stake what you can afford to lose")
    rec = ledger.record(book)
    posts = []  # (ledger row or None, pick or None, settled list)
    if picks:
        for i, pk in enumerate(picks):
            book = ledger.add(book, {
                "posted_at": now.isoformat(), "event_id": pk["event_id"], "league": pk["league"],
                "sport_key": pk["sport_key"], "kickoff": pk["kickoff"], "home": pk["home"],
                "away": pk["away"], "pick": pk["outcome"], "team": pk["team"], "odds": pk["odds"],
                "book": pk["book"], "implied": round(pk["implied"], 4), "model_p": round(pk["model_p"], 4),
                "final_p": round(pk["final_p"], 4), "ev": round(pk["ev"], 4),
                "stake_pct": round(pk["stake_frac"], 4), "tier": pk.get("tier", "value")})
            posts.append((len(book) - 1, pk, settled if i == 0 else []))
    elif settled and cfg("POST_RESULTS_ONLY", True):
        posts.append((None, None, settled))
        rec = ledger.record(book)

    for row_i, pk, done in posts:
        published = []
        png = None
        if pk and cfg("CARD_IMAGES", True):
            try:
                from edgebet import cards
                png = cards.pick_card(pk, pk["league_name"], local_time(pk["kickoff"]),
                                      cfg("CARD_STAKE", 100.0), cfg("CARD_CURRENCY", "$"))
            except Exception as e:  # noqa: BLE001
                print("card failed, posting text only:", e)
        if "telegram" in targets:
            msg = announce.channel_message(
                done, pk, rec, pk["why"] if pk else None,
                pk["league_name"] if pk else "", local_time(pk["kickoff"]) if pk else "", footer)
            print("\n----- TELEGRAM CHANNEL -----\n" + msg + "\n-----")
            if live_mode and announce.channel_configured():
                try:
                    mid = announce.post_photo_to_channel(png, msg) if png else announce.post_to_channel(msg)
                    published.append("Telegram")
                    if row_i is not None:
                        book.at[row_i, "message_id"] = mid
                except Exception as e:  # noqa: BLE001
                    alerts.append(f"Telegram channel post failed: {e}")
        if "x" in targets:
            text = announce.compose(done, pk, rec, pk["hashtag"] if pk else "")
            print("\n----- TWEET -----\n" + text + f"\n----- ({len(text)} chars) -----")
            if live_mode and announce.x_configured():
                try:
                    tid = announce.post_to_x(text, png)
                    published.append("X")
                    if row_i is not None:
                        book.at[row_i, "tweet_id"] = tid
                except Exception as e:  # noqa: BLE001
                    alerts.append(f"X post failed: {e}")
        if pk:
            stake = bankroll * pk["stake_frac"]
            announce.telegram(f"{'POSTED to ' + ', '.join(published) if published else 'DRAFT (not posted)'}: "
                              f"{pk['team']} @ {pk['odds']:.2f} ({pk['home']} v {pk['away']})\n"
                              f"Your stake: {stake:.0f} ({pk['stake_frac']:.1%} of {bankroll:.0f}) "
                              f"at {pk['book']}. Kickoff {local_time(pk['kickoff'])}")
    if not posts:
        print("No value bet today and nothing to settle. Nothing posted.")
    ledger.save(book, ledger_path)
    # Sunday evening: the week's results as one picture post (channel + X)
    if not args.mock:
        try:
            from edgebet import weekly
            weekly.run(book, now, live_mode, cfg("CARD_STAKE", 100.0), cfg("CARD_CURRENCY", "$"),
                       cfg("CHANNEL_FOOTER", footer))
        except Exception as e:  # noqa: BLE001
            alerts.append(f"weekly recap failed: {e}")
    for a in alerts:
        print("ALERT:", a)
    if alerts:
        announce.telegram("EdgeBet alerts:\n" + "\n".join(alerts))
    print("record:", ledger.record(book))


if __name__ == "__main__":
    main()
