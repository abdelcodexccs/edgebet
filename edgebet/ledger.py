"""Every posted pick is logged, then settled with the real score. Nothing is deleted,
so the public record includes the losses."""
from __future__ import annotations

import os

import pandas as pd

COLUMNS = ["posted_at", "event_id", "league", "sport_key", "kickoff", "home", "away",
           "pick", "team", "odds", "book", "implied", "model_p", "final_p", "ev",
           "stake_pct", "tier", "status", "score", "units", "message_id", "tweet_id"]


def load(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        df = pd.read_csv(path, dtype={"event_id": str, "tweet_id": str, "message_id": str, "score": str})
        return df.reindex(columns=COLUMNS)
    return pd.DataFrame(columns=COLUMNS)


def save(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df[COLUMNS].to_csv(path, index=False)


def add(df: pd.DataFrame, row: dict) -> pd.DataFrame:
    row = {c: row.get(c) for c in COLUMNS}
    row["status"] = "pending"
    return pd.concat([df, pd.DataFrame([row])], ignore_index=True)


def settle(df: pd.DataFrame, event_id: str, home_goals: int, away_goals: int) -> dict | None:
    """Mark one pending pick won/lost. Returns the settled row."""
    idx = df.index[(df["event_id"] == event_id) & (df["status"] == "pending")]
    if not len(idx):
        return None
    i = idx[0]
    result = "home" if home_goals > away_goals else "away" if away_goals > home_goals else "draw"
    won = df.at[i, "pick"] == result
    df.at[i, "status"] = "won" if won else "lost"
    df.at[i, "score"] = f"{home_goals}-{away_goals}"
    df.at[i, "units"] = round(float(df.at[i, "odds"]) - 1, 2) if won else -1.0
    return df.loc[i].to_dict()


def record(df: pd.DataFrame) -> dict:
    done = df[df["status"].isin(["won", "lost"])]
    n = len(done)
    units = float(done["units"].astype(float).sum()) if n else 0.0
    return {"won": int((done["status"] == "won").sum()), "lost": int((done["status"] == "lost").sum()),
            "units": round(units, 2), "roi": units / n if n else 0.0, "pending": int((df["status"] == "pending").sum())}
