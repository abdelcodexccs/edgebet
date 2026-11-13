"""Sunday-evening recap: the week's results as a picture card, to the channel (and X)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import announce, cards, ledger, xpost

STATE = "state/weekly.json"


def window(now: datetime):
    """The Monday-to-Sunday week to recap, or None when it isn't recap time.
    Posted Sunday from 19:00 UTC; Monday until noon is a backup if Sunday's run was missed."""
    if now.weekday() == 6 and now.hour >= 19:
        end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif now.weekday() == 0 and now.hour < 12:
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        return None
    return end - timedelta(days=7), end


def label(start: datetime, end: datetime) -> str:
    last = end - timedelta(days=1)
    return (f"{start.day}–{last.day} {last:%b}".upper() if start.month == last.month
            else f"{start.day} {start:%b} – {last.day} {last:%b}".upper())


def run(book: pd.DataFrame, now: datetime, live: bool, stake: float = 100.0, currency: str = "$",
        footer: str = "") -> bool:
    w = window(now)
    if not w:
        return False
    start, end = w
    key = start.date().isoformat()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    if state.get("last") == key:
        return False
    k = pd.to_datetime(book["kickoff"], utc=True, errors="coerce")
    done = book[(k >= pd.Timestamp(start)) & (k < pd.Timestamp(end)) & book["status"].isin(["won", "lost", "void"])]
    rows = done.sort_values("kickoff").to_dict("records")
    if not rows:
        print("weekly recap: no settled picks this week")
        return False
    units = round(float(done["units"].astype(float).sum()), 2)
    week = {"won": int((done["status"] == "won").sum()), "lost": int((done["status"] == "lost").sum()),
            "void": int((done["status"] == "void").sum()), "units": units}
    total = ledger.record(book)
    lab = label(start, end)
    money = f"{'+' if units >= 0 else '-'}{currency}{abs(units) * stake:,.0f}"
    caption = "\n".join([
        f"📊 <b>WEEK RECAP</b> · {lab.title()}",
        f"✅ {week['won']} won · ❌ {week['lost']} lost" + (f" · ↩️ {week['void']} void" if week["void"] else ""),
        f"💰 <b>{units:+.2f} units</b> this week ({money} on {currency}{stake:,.0f} stakes)",
        "📈 All-time: " + announce.record_line(total).replace("Record: ", ""),
        "", "<i>Every pick and every result is posted, wins and losses.</i>",
        *([f"<i>{announce._esc(footer)}</i>"] if footer else [])])
    png = cards.week_card(rows, week, total, lab, stake, currency)
    print("\n----- WEEK RECAP -----\n" + caption + "\n-----")
    if not live:
        return False
    if announce.channel_configured():
        announce.post_photo_to_channel(png, caption)
    if xpost.configured() and os.environ.get("X_RECAPS", "true").lower() != "false":
        try:
            xpost.post(f"📊 Week recap {lab.title()}: {week['won']}W-{week['lost']}L, {units:+.2f} units\n"
                       f"All-time {total['won']}W-{total['lost']}L · ROI {total['roi'] * 100:+.1f}%\n\n"
                       "Free picks every day on Telegram (link in bio)\n#BettingTips #FootballPicks", png)
        except Exception as e:  # noqa: BLE001
            print("X recap failed:", e)
    os.makedirs("state", exist_ok=True)
    json.dump({"last": key, "posted_at": datetime.now(timezone.utc).isoformat()}, open(STATE, "w"))
    return True
