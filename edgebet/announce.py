"""Publish picks: Telegram channel (free), X (paid, optional), private Telegram alert."""
from __future__ import annotations

import html

from . import markets
import json
import os
import urllib.parse
import urllib.request

TWEET_LIMIT = 270  # X counts emoji as 2, keep a small safety margin


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def result_line(r: dict) -> str:
    mark = {"won": "✅", "void": "↩️"}.get(r["status"], "❌")
    sign = f'{float(r["units"]):+.2f}u'
    return f'{mark} {r["home"]} {r["score"]} {r["away"]} ({r["team"]} {sign})'


def pick_lines(p: dict) -> list[str]:
    kind = "💎 Value pick" if p.get("tier", "value") == "value" else "📌 Pick of the day"
    when = f' · {p["kickoff_local"]}' if p.get("kickoff_local") else ""
    return [
        f'⚽ {p["home"]} v {p["away"]}{when}',
        f'{kind}: {markets.label(p["outcome"], p["home"], p["away"])} @ {p["odds"]:.2f}',
        f'Bookmaker {_pct(p["implied"])} · Our model {_pct(p["final_p"])}',
    ]


def record_line(rec: dict) -> str:
    if rec["won"] + rec["lost"] == 0:
        return "Record: first pick, every result will be posted."
    return f'Record: {rec["won"]}W-{rec["lost"]}L · {rec["units"]:+.2f}u · ROI {rec["roi"] * 100:+.1f}%'


def compose(settled: list[dict], pick: dict | None, rec: dict, hashtag: str = "") -> str | None:
    """One tweet: yesterday's results, today's pick, running record. No links
    (on X's pay-per-use API a post with a URL costs ~13x more)."""
    if not settled and pick is None:
        return None
    blocks = []
    if settled:
        blocks.append("\n".join(result_line(r) for r in settled))
    if pick is not None:
        blocks.append("\n".join(pick_lines(pick)))
    tail = (record_line(rec) + "\nFree picks every day on Telegram (link in bio)"
            + f"\n#BettingTips #FootballPicks{(' ' + hashtag) if hashtag else ''}")
    blocks.append(tail)
    text = "\n\n".join(blocks)
    if len(text) > TWEET_LIMIT and settled:  # too long: drop the oldest result and retry
        return compose(settled[1:], pick, rec, hashtag)
    return text[:TWEET_LIMIT]


def post_to_x(text: str, png: bytes | None = None) -> str:
    """Post to X with an optional picture (see xpost.py). Returns the post id."""
    from . import xpost
    return xpost.post(text, png)


def _post_to_x_tweepy(text: str) -> str:
    """Post as your own account (X API v2, OAuth 1.0a user keys). Returns the tweet id."""
    import tweepy  # pip install tweepy

    client = tweepy.Client(
        consumer_key=os.environ["X_API_KEY"], consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"], access_token_secret=os.environ["X_ACCESS_SECRET"],
    )
    resp = client.create_tweet(text=text)
    return str(resp.data["id"])


def telegram(text: str) -> None:
    """Private alert to yourself. Silently skipped if not configured."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20)
    except Exception as e:  # noqa: BLE001
        print("telegram failed:", e)


# ---------------- Telegram channel (free) ----------------

def _esc(s) -> str:
    return html.escape(str(s), quote=False)


def channel_result(r: dict) -> str:
    head = {"won": "✅ <b>WON</b>", "void": "↩️ <b>VOID</b> (stake back)"}.get(r["status"], "❌ <b>LOST</b>")
    pick = markets.label(str(r["pick"]), r["home"], r["away"])
    return (f'{head}  {_esc(r["home"])} {_esc(r["score"])} {_esc(r["away"])}\n'
            f'Pick was {_esc(pick)} @ {float(r["odds"]):.2f} → {float(r["units"]):+.2f} units')


def channel_pick(p: dict, why: list[str], league: str, kickoff_local: str) -> str:
    side = _esc(markets.label(p["outcome"], p["home"], p["away"]))
    if p["outcome"] in markets.NAMES:
        side = f'{markets.NAMES[p["outcome"]]}: {side}'
    lines = [
        f"⚽ <b>{_esc(league)}</b> · {_esc(kickoff_local)}",
        f'🏟 {_esc(p["home"])} vs {_esc(p["away"])}',
        "",
        ("💎 <b>VALUE PICK</b>" if p.get("tier", "value") == "value" else "📌 <b>PICK OF THE DAY</b>"),
        f"🎯 <b>{side} @ {p['odds']:.2f}</b>",
        f'📊 Bookmaker {_pct(p["implied"])} · Our model {_pct(p["final_p"])} · Edge {p["ev"] * 100:+.1f}%',
        *({"value": [],
           "safe": [f"<i>Our most likely bet today ({_pct(p['final_p'])}): the model and the bookmakers agree. "
                    "No big edge, so a small stake.</i>"]}.get(p.get("tier", "value"),
          ["<i>Today's strongest game, below our +5% value bar: lower confidence, smaller stake.</i>"])),
        "",
        "<b>Why we like it</b>",
        *[f"• {_esc(w)}" for w in why],
    ]
    return "\n".join(lines)


def channel_message(settled: list[dict], pick: dict | None, rec: dict, why=None,
                    league: str = "", kickoff_local: str = "", footer: str = "") -> str | None:
    if not settled and pick is None:
        return None
    blocks = []
    if settled:
        blocks.append("<b>Results</b>\n" + "\n\n".join(channel_result(r) for r in settled))
    if pick is not None:
        blocks.append(channel_pick(pick, why or [], league, kickoff_local))
    blocks.append("📈 " + _esc(record_line(rec)))
    if footer:
        blocks.append(f"<i>{_esc(footer)}</i>")
    return "\n\n".join(blocks)


def post_to_channel(text_html: str) -> str:
    """Post to your Telegram channel (the bot must be an admin of it). Free.
    Returns the message id."""
    token, chat = _creds()
    data = urllib.parse.urlencode({"chat_id": chat, "text": text_html, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("ok"):
        raise RuntimeError(resp)
    return str(resp["result"]["message_id"])


def _status_vs_pick(pick: str, hs: int, as_: int) -> str:
    lead = "home" if hs > as_ else "away" if as_ > hs else "draw"
    if lead == pick:
        return "✅ winning right now"
    if lead == "draw":
        return "⏸ level right now"
    return "⚠️ behind right now"


def goal_message(e: dict, goal: dict | None, side: str, pick: dict | None) -> str:
    """Live goal alert. e: ESPN event, side: 'home'/'away' (team that scored)."""
    home, away = (pick["home"], pick["away"]) if pick else (e["home"], e["away"])
    scorer_team = home if side == "home" else away
    minute = f' · {_esc(goal["minute"])}' if goal and goal.get("minute") else ""
    lines = [f"⚽ <b>GOAL!</b> {_esc(scorer_team)}{minute}",
             f"<b>{_esc(home)} {e['hs']}-{e['as']} {_esc(away)}</b>"]
    if goal and goal.get("scorer"):
        tag = " (own goal)" if goal.get("own_goal") else " (penalty)" if goal.get("penalty") else ""
        lines.append(f"Scorer: {_esc(goal['scorer'])}{tag}")
    if pick:
        lines += ["", f'🎯 Our pick: {_esc(markets.label(str(pick["pick"]), pick["home"], pick["away"]))} @ {float(pick["odds"]):.2f} · '
                      f'{markets.live_status(str(pick["pick"]), e["hs"], e["as"])}']
    return "\n".join(lines)


def goal_cancelled_message(e: dict, pick: dict | None) -> str:
    home, away = (pick["home"], pick["away"]) if pick else (e["home"], e["away"])
    return f"🚫 <b>Goal cancelled</b> (VAR)\n<b>{_esc(home)} {e['hs']}-{e['as']} {_esc(away)}</b>"


def fulltime_message(r: dict, rec: dict, footer: str = "") -> str:
    head = {"won": "✅ <b>WON</b>", "void": "↩️ <b>VOID</b> (stake back)"}.get(r["status"], "❌ <b>LOST</b>")
    pick = markets.label(str(r["pick"]), r["home"], r["away"])
    lines = [f'🏁 <b>FULL TIME</b> · {_esc(r["home"])} {_esc(r["score"])} {_esc(r["away"])}',
             head + f' · Pick was {_esc(pick)} @ {float(r["odds"]):.2f} → {float(r["units"]):+.2f} units',
             "", "📈 " + _esc(record_line(rec))]
    if footer:
        lines += ["", f"<i>{_esc(footer)}</i>"]
    return "\n".join(lines)


def send(text_html: str, to: str = "channel", png: bytes | None = None) -> None:
    """to: 'channel', 'me' (your private chat with the bot) or 'both'. png: optional picture."""
    if to in ("channel", "both") and channel_configured():
        post_photo_to_channel(png, text_html) if png else post_to_channel(text_html)
    if to in ("me", "both"):
        token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
        if token and chat:
            data = urllib.parse.urlencode({"chat_id": chat, "text": text_html, "parse_mode": "HTML"}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20)


CHANNEL_STATE = os.path.join("state", "channel.json")
_channel_cache: dict = {}


def channel_info() -> dict:
    """The channel's permanent number id and its current @username.

    The number never changes, so renaming the channel doesn't break the bot: the id is
    saved in state/channel.json the first time, and the username is refreshed every run."""
    if _channel_cache:
        return _channel_cache
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().strip('"').strip("'")
    token = token.split("=", 1)[1].strip() if token.upper().startswith("TELEGRAM_BOT_TOKEN=") else token
    chat = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip().strip('"').strip("'")
    chat = chat.split("=", 1)[1].strip() if chat.upper().startswith("TELEGRAM_CHANNEL_ID=") else chat
    saved = {}
    if os.path.exists(CHANNEL_STATE):
        try:
            saved = json.load(open(CHANNEL_STATE))
        except ValueError:
            saved = {}
    info = {"id": saved.get("id") or chat, "username": saved.get("username", "")}
    for who in [x for x in (saved.get("id"), chat) if x]:
        try:
            q = urllib.parse.urlencode({"chat_id": who})
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getChat?{q}", timeout=20) as r:
                res = json.loads(r.read().decode())["result"]
            info = {"id": res["id"], "username": res.get("username", ""), "title": res.get("title", "")}
            if info != {k: saved.get(k) for k in info}:
                os.makedirs("state", exist_ok=True)
                json.dump(info, open(CHANNEL_STATE, "w"), ensure_ascii=False)
            break
        except Exception:  # noqa: BLE001  (offline, or the old @username no longer exists)
            continue
    _channel_cache.update(info)
    return _channel_cache


def channel_link() -> str:
    u = os.environ.get("CHANNEL_LINK") or (channel_info().get("username") if channel_configured() else "")
    if not u and os.path.exists(CHANNEL_STATE):
        u = json.load(open(CHANNEL_STATE)).get("username", "")
    return u if "/" in str(u) else f"t.me/{u}" if u else ""


def _creds():
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip().strip('"').strip("'")
    token = token.split("=", 1)[1].strip() if token.upper().startswith("TELEGRAM_BOT_TOKEN=") else token
    return token, str(channel_info()["id"])


def _visible_len(text_html: str) -> int:
    import re
    return len(html.unescape(re.sub(r"<[^>]+>", "", text_html)))


def post_photo_to_channel(png: bytes, text_html: str, chat: str | None = None) -> str:
    """Picture + message. Telegram captions hold 1024 characters: a longer message goes
    right under the picture as its own post. Returns the id of the post holding the text."""
    import uuid
    token, channel = _creds()
    chat = chat or channel
    fits = _visible_len(text_html) <= 1024
    fields = {"chat_id": chat}
    if fits:
        fields.update({"caption": text_html, "parse_mode": "HTML"})
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"card.png\"\r\n"
             f"Content-Type: image/png\r\n\r\n").encode() + png + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("ok"):
        raise RuntimeError(resp)
    if fits:
        return str(resp["result"]["message_id"])
    return post_to_channel(text_html)


def channel_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHANNEL_ID"))


def x_configured() -> bool:
    from . import xpost
    return xpost.configured()


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, default=str)
