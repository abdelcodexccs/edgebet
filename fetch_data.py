"""Download results + odds (football-data.co.uk) and Champions League results
(openfootball on GitHub). Free.

    python fetch_data.py                      # everything the bot needs
    python fetch_data.py --leagues E0 SP1 --from 2023 --to 2026

League codes: E0 Premier League, SP1 La Liga, I1 Serie A, D1 Bundesliga,
F1 Ligue 1, and for Champions League strength links: P1 Portugal,
N1 Netherlands, B1 Belgium, T1 Turkey, SC0 Scotland.
"""
import argparse
import os
import urllib.request
from datetime import date

URL = "https://www.football-data.co.uk/mmz4281/{code}/{league}.csv"
UCL_URL = "https://raw.githubusercontent.com/openfootball/champions-league/master/{season}/cl.txt"
TOP5 = ["E0", "SP1", "I1", "D1", "F1"]
EXTRA = ["P1", "N1", "B1", "T1", "SC0"]


def current_start_year():
    t = date.today()
    return t.year if t.month >= 7 else t.year - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="+", default=TOP5 + EXTRA)
    ap.add_argument("--from", dest="start", type=int, default=current_start_year() - 3)
    ap.add_argument("--to", dest="end", type=int, default=current_start_year())
    ap.add_argument("--no-ucl", action="store_true")
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    for league in args.leagues:
        os.makedirs(os.path.join(args.out, league), exist_ok=True)
        for y in range(args.start, args.end + 1):
            code = f"{y % 100:02d}{(y + 1) % 100:02d}"
            dest = os.path.join(args.out, league, f"{league}-{y}.csv")
            try:
                urllib.request.urlretrieve(URL.format(code=code, league=league), dest)
                print("saved", dest)
            except Exception as e:  # noqa: BLE001
                print("failed", league, y, e)

    if not args.no_ucl:
        os.makedirs(os.path.join(args.out, "UCL"), exist_ok=True)
        for y in range(args.start, args.end + 1):
            season = f"{y}-{(y + 1) % 100:02d}"
            dest = os.path.join(args.out, "UCL", f"cl-{season}.txt")
            try:
                urllib.request.urlretrieve(UCL_URL.format(season=season), dest)
                print("saved", dest)
            except Exception as e:  # noqa: BLE001
                print("no Champions League file yet for", season, f"({e})")


if __name__ == "__main__":
    main()
