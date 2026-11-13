"""Check every key before going live. Costs nothing (one cheap read on X only
if you publish there).

    python check_setup.py
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from daily import load_env


def ok(msg):
    print("  OK  ", msg)


def bad(msg):
    print("  FAIL", msg)


def main():
    load_env()
    print("The Odds API")
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        bad("ODDS_API_KEY missing")
    else:
        try:  # the /sports list does not use quota
            with urllib.request.urlopen(f"https://api.the-odds-api.com/v4/sports?apiKey={key}", timeout=20) as r:
                n = len(json.loads(r.read()))
                ok(f"key works ({n} sports), credits left: {r.headers.get('x-requests-remaining')}")
        except Exception as e:  # noqa: BLE001
            bad(f"odds api: {e}")

    print("X (Twitter, optional, paid)")
    if "x" not in os.environ.get("PUBLISH_TO", "telegram"):
        print("  --   not used (PUBLISH_TO has no x)")
    need = [] if "x" not in os.environ.get("PUBLISH_TO", "telegram") else ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"]
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        bad(f"missing {missing}")
    elif need:
        try:
            import tweepy
            c = tweepy.Client(consumer_key=os.environ["X_API_KEY"], consumer_secret=os.environ["X_API_SECRET"],
                              access_token=os.environ["X_ACCESS_TOKEN"],
                              access_token_secret=os.environ["X_ACCESS_SECRET"])
            me = c.get_me(user_auth=True)
            ok(f"logged in as @{me.data.username}")
        except Exception as e:  # noqa: BLE001
            bad(f"X auth: {e}  (app must have Read and Write; regenerate tokens after changing it; credits needed)")

    print("Telegram channel (free publishing)")
    token, channel = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHANNEL_ID")
    if not token:
        bad("TELEGRAM_BOT_TOKEN missing (create a bot with @BotFather)")
    else:
        def tg(method, **params):
            q = urllib.parse.urlencode(params)
            try:
                with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}?{q}", timeout=20) as r:
                    return json.loads(r.read())["result"]
            except urllib.error.HTTPError as e:
                desc = json.loads(e.read() or b"{}").get("description", str(e))
                raise RuntimeError(f"{method}: {desc}") from None
        try:
            bot = tg("getMe")
            ok(f"bot works: @{bot['username']}")
            if channel:
                chat = tg("getChat", chat_id=channel)
                member = tg("getChatMember", chat_id=channel, user_id=bot["id"])
                if member["status"] == "administrator" and member.get("can_post_messages", True):
                    ok(f"bot is admin of '{chat.get('title')}' and can post")
                else:
                    bad(f"bot is '{member['status']}' in the channel: make it an admin with 'Post messages'")
            else:
                bad("TELEGRAM_CHANNEL_ID missing (e.g. @yourchannel)")
        except Exception as e:  # noqa: BLE001
            bad(f"telegram: {e}  (check the token, and that the bot was added to the channel as admin)")
    if token and os.environ.get("TELEGRAM_CHAT_ID"):
        from edgebet.announce import telegram
        telegram("EdgeBet setup test: private alerts work.")
        ok("private test message sent to you")

    print(f"Publishing to: {os.environ.get('PUBLISH_TO', 'telegram')} · "
          f"POST_MODE={os.environ.get('POST_MODE', 'dry')} (set to live to really post)")


if __name__ == "__main__":
    main()
