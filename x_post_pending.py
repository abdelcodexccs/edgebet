"""Post every pending pick that isn't on X yet (picture card + text), and save the post id.

    python x_post_pending.py          # POST_MODE=live really posts
"""
from __future__ import annotations

import os

from daily import cfg, load_env, local_time
from edgebet import announce, cards, intl, ledger, live, xpost

NAMES = {"soccer_uefa_nations_league": "UEFA Nations League"}


def league_name(row) -> tuple[str, str]:
    if row["league"] in live.LEAGUES:
        _, name, tag = live.LEAGUES[row["league"]]
        return name, tag
    key = str(row["sport_key"])
    name = NAMES.get(key) or key.replace("soccer_", "").replace("_", " ").title().replace("Uefa", "UEFA").replace("Fifa", "FIFA")
    return name, intl.hashtag(key)


def main():
    load_env()
    path = cfg("LEDGER", "state/ledger.csv")
    book = ledger.load(path)
    rec = ledger.record(book)
    todo = book[(book["status"] == "pending") & (book["tweet_id"].isna() | (book["tweet_id"].astype(str).isin(["", "nan"])))]
    print(f"{len(todo)} pending pick(s) not on X yet")
    for i, row in todo.iterrows():
        name, tag = league_name(row)
        pk = {"home": row["home"], "away": row["away"], "outcome": row["pick"], "team": row["team"],
              "odds": float(row["odds"]), "implied": float(row["implied"]), "final_p": float(row["final_p"]),
              "ev": float(row["ev"]), "tier": row["tier"] if row["tier"] != "lean" else "safe",
              "kickoff_local": local_time(row["kickoff"])}
        text = announce.compose([], pk, rec, tag)
        png = cards.pick_card(pk, name, local_time(row["kickoff"]), cfg("CARD_STAKE", 100.0), cfg("CARD_CURRENCY", "$"))
        print("\n----- X -----\n" + text + "\n-----")
        if cfg("POST_MODE", "dry") == "live" and xpost.configured():
            tid = xpost.post(text, png)
            book.at[i, "tweet_id"] = tid
            print(f"::notice::Posted to X: https://x.com/OddsBrosTips/status/{tid}")
    ledger.save(book, path)


if __name__ == "__main__":
    main()
