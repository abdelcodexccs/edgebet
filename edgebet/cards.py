"""ODDS BROTHERS picture cards for Telegram: one for each pick, one for each result.

The money figures use an example stake (CARD_STAKE, default $100) and say so on the
card: they show what the pick pays, not a bet that was placed.
"""
from __future__ import annotations

import io
import os

from PIL import Image, ImageDraw, ImageFont

from . import markets

HERE = os.path.join(os.path.dirname(__file__), "..", "assets")
FONT = os.path.join(HERE, "fonts", "Montserrat.ttf")
W, H = 1080, 1350
BG, PANEL, PANEL2 = (11, 16, 32), (22, 30, 56), (30, 40, 72)
WHITE, GREY, GREEN, RED, GOLD = (255, 255, 255), (150, 160, 190), (46, 230, 107), (255, 82, 82), (255, 196, 60)


def _f(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(FONT, size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:  # noqa: BLE001
        pass
    return f


def _fit(d: ImageDraw.ImageDraw, text: str, max_w: int, size: int, weight: int = 800) -> ImageFont.FreeTypeFont:
    while size > 28 and d.textlength(text, font=_f(size, weight)) > max_w:
        size -= 4
    return _f(size, weight)


def _center(d, y, text, font, fill, x0=0, x1=W):
    w = d.textlength(text, font=font)
    d.text(((x0 + x1 - w) / 2, y), text, font=font, fill=fill)


def _pill(d, x, y, text, font, bg, fg, padx=28, pady=12):
    w = d.textlength(text, font=font)
    h = font.size + 2 * pady
    d.rounded_rectangle((x, y, x + w + 2 * padx, y + h), h // 2, fill=bg)
    d.text((x + padx, y + pady - font.size * 0.08), text, font=font, fill=fg)
    return x + w + 2 * padx


def _header(im, d, right_text):
    try:
        logo = os.path.join(HERE, "logo.png")
        if os.path.exists(logo):  # the channel logo (round badge)
            b = Image.open(logo).convert("RGBA").resize((136, 136), Image.LANCZOS)
            mask = Image.new("L", (136, 136), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 135, 135), fill=255)
            im.paste(b, (52, 52), mask)
        else:
            b = Image.open(os.path.join(HERE, "brothers.jpg")).convert("RGB")
            s = min(b.size)
            b = b.crop(((b.size[0] - s) // 2, 0, (b.size[0] + s) // 2, s)).resize((120, 120))
            mask = Image.new("L", (120, 120), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 119, 119), fill=255)
            d.ellipse((56, 56, 184, 184), fill=GREEN)
            im.paste(b, (60, 60), mask)
    except Exception:  # noqa: BLE001
        pass
    d.text((210, 72), "ODDS BROS TIPS", font=_f(52, 900), fill=WHITE)
    try:
        from .announce import channel_link
        link = channel_link() or "Telegram"
    except Exception:  # noqa: BLE001
        link = "Telegram"
    d.text((212, 138), link, font=_f(30, 600), fill=GREY)
    f = _f(30, 700)
    d.text((W - 60 - d.textlength(right_text, font=f), 92), right_text, font=f, fill=GREY)


def _footer(d, note):
    d.line((60, H - 150, W - 60, H - 150), fill=PANEL2, width=2)
    _center(d, H - 128, note, _f(28, 600), GREY)
    _center(d, H - 82, "18+  ·  No bet is certain  ·  Bet responsibly", _f(30, 700), GREY)


def _png(im) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def pick_card(p: dict, league: str, kickoff_local: str, stake: float = 100.0, currency: str = "$") -> bytes:
    """p: a pick (home, away, team, odds, implied, final_p, ev, tier, outcome)."""
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    _header(im, d, kickoff_local)

    value = p.get("tier", "value") == "value"
    d.rounded_rectangle((60, 230, W - 60, 1020), 40, fill=PANEL)
    x = _pill(d, 100, 270, "VALUE PICK" if value else "PICK OF THE DAY", _f(34, 900),
              GREEN if value else GOLD, BG)
    d.text((x + 24, 284), league.upper(), font=_fit(d, league.upper(), W - 160 - x, 30, 700), fill=GREY)

    match = f"{p['home']}  vs  {p['away']}"
    _center(d, 360, match, _fit(d, match, W - 200, 60, 800), WHITE)

    side = markets.label(p.get("outcome", "home"), p["home"], p["away"])
    kind = markets.NAMES.get(p.get("outcome"), "Our pick").upper()
    d.rounded_rectangle((100, 460, W - 100, 640), 30, fill=PANEL2)
    d.text((140, 482), kind, font=_f(30, 700), fill=GREY)
    d.text((140, 524), side, font=_fit(d, side, 560, 64, 900), fill=WHITE)
    d.text((W - 140 - d.textlength("ODDS", font=_f(30, 700)), 482), "ODDS", font=_f(30, 700), fill=GREY)
    odds = f"{float(p['odds']):.2f}"
    fo = _f(84, 900)
    d.text((W - 140 - d.textlength(odds, font=fo), 516), odds, font=fo, fill=GREEN)

    cols = [("Bookmaker", f"{float(p['implied']) * 100:.1f}%"), ("Our model", f"{float(p['final_p']) * 100:.1f}%"),
            ("Edge", f"{float(p['ev']) * 100:+.1f}%")]
    cw = (W - 200) / 3
    for i, (k, v) in enumerate(cols):
        x0 = 100 + i * cw
        _center(d, 680, k, _f(30, 600), GREY, x0, x0 + cw)
        _center(d, 724, v, _f(52, 800), WHITE if i < 2 else (GREEN if float(p["ev"]) > 0 else GREY), x0, x0 + cw)

    win = stake * float(p["odds"])
    d.rounded_rectangle((100, 830, W - 100, 990), 30, fill=(16, 52, 40))
    d.text((140, 852), f"Example stake  {currency}{stake:,.0f}", font=_f(34, 700), fill=WHITE)
    d.text((140, 905), "Potential win", font=_f(40, 800), fill=WHITE)
    amount = f"{currency}{win:,.2f}"
    fa = _f(80, 900)
    d.text((W - 140 - d.textlength(amount, font=fa), 880), amount, font=fa, fill=GREEN)

    _center(d, 1060, "Every pick and every result is posted, wins and losses.", _f(30, 600), GREY)
    _footer(d, "Example stake only, not a bet slip.")
    return _png(im)


def result_card(r: dict, rec: dict, stake: float = 100.0, currency: str = "$") -> bytes:
    """r: a settled ledger row (home, away, score, team, odds, status, units)."""
    won = r["status"] == "won"
    void = r["status"] == "void"
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    _header(im, d, "FULL TIME")
    accent = GREEN if won else GOLD if void else RED

    d.rounded_rectangle((60, 230, W - 60, 1060), 40, fill=PANEL, outline=accent, width=6)
    _center(d, 280, "WON" if won else "VOID" if void else "LOST", _f(170, 900), accent)
    score = f"{r['home']}  {r['score']}  {r['away']}"
    _center(d, 500, score, _fit(d, score, W - 200, 58, 800), WHITE)
    pick = f"Pick: {markets.label(str(r.get('pick', 'home')), r['home'], r['away'])} @ {float(r['odds']):.2f}"
    _center(d, 590, pick, _fit(d, pick, W - 200, 42, 700), GREY)

    d.rounded_rectangle((100, 680, W - 100, 860), 30, fill=(16, 52, 40) if won else (60, 50, 20) if void else (60, 22, 30))
    back = stake * float(r["odds"]) if won else stake if void else 0.0
    profit = back - stake
    d.text((140, 705), f"Example stake  {currency}{stake:,.0f}", font=_f(34, 700), fill=WHITE)
    d.text((140, 760), "Profit" if won else "Stake back" if void else "Lost", font=_f(44, 800), fill=WHITE)
    amount = f"{'+' if profit >= 0 else '-'}{currency}{abs(profit):,.2f}"
    fa = _f(84, 900)
    d.text((W - 140 - d.textlength(amount, font=fa), 735), amount, font=fa, fill=accent)

    if rec.get("won", 0) + rec.get("lost", 0):
        line = f"Record  {rec['won']}W - {rec['lost']}L   ·   {rec['units']:+.2f} units   ·   ROI {rec['roi'] * 100:+.1f}%"
        _center(d, 920, line, _fit(d, line, W - 200, 38, 800), WHITE)
    _footer(d, "Example stake only, not a bet slip.")
    return _png(im)


def week_card(rows: list[dict], week: dict, total: dict, label: str, stake: float = 100.0,
              currency: str = "$") -> bytes:
    """Weekly recap: every settled pick of the week, the week's record and the all-time record."""
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    _header(im, d, label)
    up = week["units"] >= 0
    accent = GREEN if up else RED
    d.rounded_rectangle((60, 230, W - 60, 520), 40, fill=PANEL, outline=accent, width=6)
    _center(d, 258, "THIS WEEK", _f(36, 800), GREY)
    rec = f"{week['won']}W - {week['lost']}L" + (f" - {week['void']}V" if week.get("void") else "")
    _center(d, 300, rec, _f(120, 900), WHITE)
    money = f"{'+' if up else '-'}{currency}{abs(week['units']) * stake:,.0f}"
    _center(d, 440, f"{week['units']:+.2f} units  ·  {money} on {currency}{stake:,.0f} stakes", _f(38, 800), accent)

    y = 560
    shown = rows[:7]
    for r in shown:
        mark = {"won": ("WON", GREEN), "void": ("VOID", GOLD)}.get(r["status"], ("LOST", RED))
        d.rounded_rectangle((60, y, W - 60, y + 64), 18, fill=PANEL)
        x = _pill(d, 80, y + 11, mark[0], _f(26, 900), mark[1], BG, padx=16, pady=8)
        line = f"{r['home']} {r['score']} {r['away']}"
        pick = markets.label(str(r.get("pick", "home")), r["home"], r["away"])
        odds = f"@ {float(r['odds']):.2f}"
        fo = _f(30, 800)
        ow = d.textlength(odds, font=fo)
        d.text((W - 80 - ow, y + 16), odds, font=fo, fill=WHITE)
        avail = W - 80 - ow - 20 - (x + 18)
        d.text((x + 18, y + 4), line, font=_fit(d, line, avail, 30, 800), fill=WHITE)
        d.text((x + 18, y + 38), pick, font=_fit(d, pick, avail, 22, 600), fill=GREY)
        y += 72
    if len(rows) > len(shown):
        _center(d, y + 4, f"+ {len(rows) - len(shown)} more in the channel", _f(28, 600), GREY)
    if total.get("won", 0) + total.get("lost", 0):
        line = f"All-time  {total['won']}W - {total['lost']}L   ·   {total['units']:+.2f} units   ·   ROI {total['roi'] * 100:+.1f}%"
        _center(d, H - 210, line, _fit(d, line, W - 160, 34, 800), WHITE)
    _footer(d, f"Money shown for a {currency}{stake:,.0f} example stake per pick.")
    return _png(im)
