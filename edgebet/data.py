"""Load football-data.co.uk style CSVs (results + bookmaker odds)."""
from __future__ import annotations

import glob
import os

import pandas as pd

# Odds column prefixes in football-data.co.uk files.
#   PS   = Pinnacle (sharpest book, pre-match "opening" snapshot)
#   PSC  = Pinnacle closing odds (the benchmark for closing line value)
#   B365 = Bet365 (a typical soft book, similar to what Rainbet prices)
#   Max / BbMx = best price across books
BOOKS = {
    "pinnacle": ("PSH", "PSD", "PSA"),
    "pinnacle_close": ("PSCH", "PSCD", "PSCA"),
    "b365": ("B365H", "B365D", "B365A"),
    "max": ("MaxH", "MaxD", "MaxA"),
    "bbmax": ("BbMxH", "BbMxD", "BbMxA"),
}

KEEP = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="latin1")
    df = df.dropna(subset=["HomeTeam", "AwayTeam"])
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, format="mixed")
    cols = KEEP + [c for trio in BOOKS.values() for c in trio if c in df.columns]
    df = df[cols].copy()
    df["source"] = os.path.basename(path)
    return df


def load_league(folder: str) -> pd.DataFrame:
    """Load every CSV in a folder (one file per season) into one sorted frame."""
    files = sorted(glob.glob(os.path.join(folder, "*.csv")))
    if not files:
        raise FileNotFoundError(f"No CSV files in {folder}. Run fetch_data.py first.")
    df = pd.concat([load_csv(f) for f in files], ignore_index=True)
    df = df.dropna(subset=["FTHG", "FTAG"])  # unplayed fixtures have no score
    df["FTHG"] = df["FTHG"].astype(int)
    df["FTAG"] = df["FTAG"].astype(int)
    return df.sort_values("Date").reset_index(drop=True)


def odds_columns(df: pd.DataFrame, book: str) -> tuple[str, str, str]:
    cols = BOOKS[book]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(f"Odds for '{book}' not in data (missing {missing}).")
    return cols
