# ODDS BROTHERS bot

Football picks posted automatically to the ODDS BROTHERS Telegram channel.

**How it works**

- **Model:** a Dixon-Coles goal model rates every team's attack and defence from real results (top 5 leagues + Champions League).
- **Prices:** the model is compared with bookmaker prices. A pick is posted only when our probability is clearly above the market's.
- **Daily run:** `daily.py`, on GitHub Actions. It settles finished picks, prices the next 30 hours of games and posts the pick with its reasons.
- **Live run:** `live_watch.py`. It follows every pending pick. It posts each goal as it happens, then the result and the updated record at full time.
- **Record:** every pick and every result is kept in `state/ledger.csv`, losses included.

18+ · No bet is certain · Only stake what you can afford to lose.
