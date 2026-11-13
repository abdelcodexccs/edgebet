"""Live watcher for every pending pick: goal alerts + the result the moment a match ends.

Runs on GitHub Actions (.github/workflows/live.yml). It is started by the daily run
whenever there are pending picks, sleeps until kick-off, then checks ESPN's free
live scoreboard every ~45 seconds.

  * a goal is scored          -> "GOAL!" post (score, minute, scorer, how our pick stands)
  * a goal is cancelled (VAR) -> short correction post
  * full time                 -> pick settled in state/ledger.csv, pushed, then the result
                                 + updated record is posted (so it is never posted twice)

A GitHub job can run ~6 hours; when the next kick-off is later than that the watcher
starts a fresh copy of itself and exits.

    python live_watch.py                    # watch (POST_MODE=live really posts)
    python live_watch.py --replay 20261108  # test: rebuild what it would have posted that day
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from daily import cfg, load_env
from edgebet import announce, espn, ledger

STATE = "state/live.json"
FOOTER = "18+ · No bet is certain · Only stake what you can afford to lose"
SKIP = ("POSTPONED", "CANCELED", "CANCELLED", "ABANDONED", "SUSPENDED", "DELAYED")


def now() -> datetime:
    return datetime.now(timezone.utc)


def kickoff(row) -> datetime:
    return datetime.fromisoformat(str(row["kickoff"]).replace("Z", "+00:00"))


def sh(*cmd) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True)


class Watcher:
    def __init__(self, dry: bool):
        self.dry = dry
        self.ledger_path = cfg("LEDGER", "state/ledger.csv")
        self.goals_to = cfg("GOAL_ALERTS_TO", "channel")      # channel | me | both | off
        self.results_to = cfg("RESULTS_TO", "channel")
        self.poll = cfg("POLL_SECONDS", 45)
        self.deadline = now() + timedelta(minutes=cfg("RUN_MINUTES", 330))
        self.max_wait = timedelta(hours=cfg("LIVE_MAX_WAIT_HOURS", 30))
        self.state = json.load(open(STATE)) if os.path.exists(STATE) else {}
        self.deferred: dict[str, datetime] = {}
        self.slugs: dict[str, str] = {}
        self.slug_retry: dict[str, datetime] = {}
        self.last_sync = now()

    # ---------- git / ledger ----------
    def sync(self, force=False):
        if self.dry or (not force and now() - self.last_sync < timedelta(minutes=10)):
            return
        self.save_state()
        sh("git", "pull", "--rebase", "--autostash", "-q")
        self.last_sync = now()

    def save_state(self):
        os.makedirs("state", exist_ok=True)
        # forget matches older than 3 days
        cut = (now() - timedelta(days=3)).isoformat()
        self.state = {k: v for k, v in self.state.items() if v.get("kickoff", "9") >= cut}
        json.dump(self.state, open(STATE, "w"), indent=1)

    def push(self, message: str) -> bool:
        if self.dry:
            return True
        self.save_state()
        sh("git", "add", STATE, self.ledger_path)
        if sh("git", "diff", "--cached", "--quiet").returncode != 0:
            sh("git", "commit", "-q", "-m", message)
        for _ in range(5):
            if sh("git", "push", "-q").returncode == 0:
                return True
            sh("git", "pull", "--rebase", "--autostash", "-q")
            time.sleep(3)
        return False

    def settle(self, row: dict, h: int, a: int) -> dict | None:
        """Settle on the freshest ledger, push, and only then return the row to post."""
        for _ in range(4):
            if not self.dry:
                sh("git", "fetch", "-q", "origin")
                sh("git", "reset", "-q", "--hard", "origin/main")
            book = ledger.load(self.ledger_path)
            r = ledger.settle(book, row["event_id"], h, a)
            if r is None:
                return None  # already settled (e.g. by the daily run)
            ledger.save(book, self.ledger_path)
            if self.push(f"result {row['home']} {h}-{a} {row['away']}"):
                return {**r, "_record": ledger.record(book)}
            time.sleep(5)
        print("could not save the result, leaving it for the daily run")
        return None

    def post(self, html: str, to: str):
        print("\n----- POST (" + to + ") -----\n" + html + "\n-----", flush=True)
        if self.dry or to == "off":
            return
        try:
            announce.send(html, to)
        except Exception as e:  # noqa: BLE001
            print("telegram failed:", e)

    def chain(self):
        """Start a fresh copy of this workflow (a GitHub job is limited to ~6 hours)."""
        self.push("live watcher handoff")
        if not self.dry:
            r = sh("gh", "api", "-X", "POST", f"repos/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/workflows/live.yml/dispatches",
                   "-f", f"ref={os.environ.get('GITHUB_REF_NAME', 'main')}")
            print("handoff:", r.returncode, r.stderr.strip())

    # ---------- one pass ----------
    def pending(self):
        book = ledger.load(self.ledger_path)
        rows = book[book["status"] == "pending"].to_dict("records")
        out = []
        for r in rows:
            try:
                k = kickoff(r)
            except ValueError:
                continue
            if (r.get("league") in espn.SLUGS or r.get("league") == "INT") and k + timedelta(hours=4) > now():
                out.append((k, r))
        return sorted(out, key=lambda x: x[0])

    def check(self, active):
        boards = {}
        for k, row in active:
            lg = row["league"]
            if lg == "INT":  # national teams: find which ESPN competition has the game, then remember it
                eid = str(row["event_id"])
                slug = (self.state.get(eid) or {}).get("slug") or self.slugs.get(eid)
                if slug is None:
                    if now() < self.slug_retry.get(eid, now()):
                        continue
                    slug, _ = espn.find_any(espn.slugs_for("INT", row["sport_key"]), row["home"], row["away"],
                                            row["kickoff"])
                    if slug is None:
                        self.slug_retry[eid] = now() + timedelta(minutes=5)
                        print(f"no ESPN match yet for {row['home']} v {row['away']}")
                        continue
                    self.slugs[eid] = slug
                lg = slug
            if lg not in boards:
                try:
                    boards[lg] = espn.between(lg, k - timedelta(hours=12), k + timedelta(hours=12))
                except Exception as e:  # noqa: BLE001
                    print(f"espn {lg} failed: {e}")
                    boards[lg] = None
            if boards[lg] is None:
                continue
            e = espn.find(boards[lg], row["home"], row["away"], row["kickoff"])
            if e is None:
                print(f"no ESPN match yet for {row['home']} v {row['away']}")
                continue
            self.update(row, e)

    def update(self, row: dict, e: dict):
        eid = str(row["event_id"])
        prev = self.state.get(eid)
        if e["hs"] is None or e["as"] is None or any(x in e["status"].upper() for x in SKIP):
            return
        if prev is None:  # first look: remember the score, nothing to announce yet
            self.state[eid] = {"hs": e["hs"], "as": e["as"], "n": len(e["goals"]), "kickoff": row["kickoff"],
                               "espn": e["id"], "slug": self.slugs.get(eid)}
            print(f"watching {row['home']} v {row['away']}: {e['hs']}-{e['as']} ({e['detail']})")
            if not e["completed"]:
                return
            prev = self.state[eid]
        dh, da = e["hs"] - prev["hs"], e["as"] - prev["as"]
        if dh > 0 or da > 0:
            new_total = e["hs"] + e["as"]
            if len(e["goals"]) < new_total and not e["completed"]:
                first = self.deferred.setdefault(eid, now())
                if now() - first < timedelta(seconds=100):
                    return  # scorer details usually follow within a minute
            self.deferred.pop(eid, None)
            details = e["goals"][prev["hs"] + prev["as"]:new_total]
            sides = ["home"] * max(dh, 0) + ["away"] * max(da, 0)
            if len(details) == len(sides):  # keep ESPN's order when we have it
                sides = [d["side"] if d["side"] in sides else s for d, s in zip(details, sides)]
            for i, side in enumerate(sides):
                g = details[i] if i < len(details) else None
                self.post(announce.goal_message(e, g, side, row), self.goals_to)
        elif dh < 0 or da < 0:
            self.post(announce.goal_cancelled_message(e, row), self.goals_to)
        prev.update({"hs": e["hs"], "as": e["as"], "n": len(e["goals"])})
        if e["completed"]:
            h, a = espn.ninety_minute_score(e)
            r = self.settle(row, h, a)
            if r:
                self.post(announce.fulltime_message(r, r.pop("_record"), cfg("CHANNEL_FOOTER", FOOTER)),
                          self.results_to)

    # ---------- main loop ----------
    def run(self):
        print(f"live watcher until {self.deadline:%H:%M} UTC · goals -> {self.goals_to} · results -> "
              f"{self.results_to} · {'DRY RUN' if self.dry else 'LIVE'}", flush=True)
        while True:
            self.sync()
            pend = self.pending()
            if not pend:
                print("no pending picks left: done")
                break
            active = [(k, r) for k, r in pend if k - timedelta(minutes=10) <= now()]
            if active:
                if now() > self.deadline:
                    self.chain()
                    break
                self.check(active)
                time.sleep(self.poll)
                continue
            nxt = pend[0][0]
            if nxt - now() > self.max_wait:
                print(f"next kick-off {nxt:%a %H:%M} UTC is too far away, the daily run will restart me")
                break
            if nxt - timedelta(minutes=10) > self.deadline:
                self.chain()
                break
            wait = min((nxt - timedelta(minutes=10) - now()).total_seconds(), 600)
            print(f"next kick-off {pend[0][1]['home']} v {pend[0][1]['away']} at {nxt:%a %H:%M} UTC, "
                  f"sleeping {wait / 60:.0f} min", flush=True)
            time.sleep(max(wait, 5))
        self.push("live watcher state")


def replay(day: str):
    """Rebuild every post the watcher would have made on a past day (nothing is posted)."""
    book = ledger.load(cfg("LEDGER", "state/ledger.csv"))
    rows = book[book["kickoff"].astype(str).str.replace("-", "").str.startswith(day)].to_dict("records")
    print(f"{len(rows)} picks with kick-off on {day}")
    for row in rows:
        try:
            evs = espn.scoreboard(row["league"], day)
        except Exception as e:  # noqa: BLE001
            print(f"{row['league']}: ESPN failed: {e}")
            continue
        e = espn.find(evs, row["home"], row["away"], row["kickoff"])
        print(f"\n=== {row['home']} v {row['away']} ({row['league']}) -> ESPN: "
              f"{e and (e['home'] + ' v ' + e['away'] + ' ' + str(e['hs']) + '-' + str(e['as']) + ' ' + e['status'])}")
        if not e:
            print("   ESPN games that day:", [f"{x['home']} v {x['away']}" for x in evs])
            continue
        hs = as_ = 0
        for g in e["goals"]:
            hs, as_ = hs + (g["side"] == "home"), as_ + (g["side"] == "away")
            print(announce.goal_message({**e, "hs": hs, "as": as_}, g, g["side"], row) + "\n")
        h, a = espn.ninety_minute_score(e)
        tmp = book.copy()
        tmp.loc[tmp["event_id"] == row["event_id"], "status"] = "pending"
        r = ledger.settle(tmp, row["event_id"], h, a)
        print(announce.fulltime_message(r, ledger.record(tmp), FOOTER))
        print(f"   ledger says: {row['status']} {row['score']}  |  ESPN 90': {h}-{a} -> "
              f"{'MATCH' if r and r['status'] == row['status'] and r['score'] == row['score'] else 'DIFFERENT'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", help="YYYYMMDD: print what would have been posted that day")
    args = ap.parse_args()
    load_env()
    if args.replay == "probe":  # which ESPN national-team competitions answer, and their games today/tomorrow
        d = now()
        for slug in espn.INT_SLUGS:
            try:
                evs = espn.between(slug, d, d + timedelta(days=1))
                print(f"{slug}: OK, {len(evs)} games  " + "; ".join(f"{e['home']} v {e['away']}" for e in evs[:4]))
            except Exception as e:  # noqa: BLE001
                print(f"{slug}: {e}")
        return
    if args.replay:
        replay(args.replay)
        return
    Watcher(dry=cfg("POST_MODE", "dry") != "live").run()


if __name__ == "__main__":
    main()
