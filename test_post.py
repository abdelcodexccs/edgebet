"""Sends one welcome message to your channel to prove live posting works."""
from daily import load_env
from edgebet import announce

load_env()
msg = """👋 <b>Welcome to BET PICKS</b>

Football value picks from a statistical model, posted automatically.

<b>How it works</b>
• The model rates every team's attack and defence from real results
• It compares its own win chance with the bookmaker's odds
• A pick is posted only when our chance is clearly higher than the odds suggest (edge of +5% or more)
• Many days there is no pick. No edge, no bet.

<b>Every pick shows</b>
🎯 the bet and the odds
📊 bookmaker chance vs our model's chance
• the stats behind it

✅❌ <b>Every result is posted, wins and losses</b>, with a running record you can check.

<i>18+ · No bet is certain · Only stake what you can afford to lose</i>"""
mid = announce.post_to_channel(msg)
print("Posted to your channel. Message id:", mid)
announce.telegram("EdgeBet test: your channel post worked.")
