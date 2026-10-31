"""A `Screen` as JSON for the Mini App (docs/WEBAPP.md, "Screen payload").

The app gets the same screen the group window shows, without the Telegram limits: the full text (no front cut),
the keyboard as the handler built it (no narrow-phone split), and the buttons a screen put into its text.
Telegram HTML is turned into the small set of tags the app renders: b i u s code pre a blockquote, plus
`<span class="spoiler">` for tg-spoiler and `<img class="ce">` for a premium tg-emoji (app/webapp/emoji.py; the plain
emoji is its alt text and the fallback). A first line that is a bare bold title becomes the payload's `title`.
"""

from __future__ import annotations

import html
import re
from typing import Any

from app.bot.ui import callbacks_codec as codec
from app.bot.ui import rich as rich_ui
from app.bot.ui.screen import Screen, auto_style

_EMOJI_RE = re.compile(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", re.S)
_EMOJI_ID_RE = re.compile(r'<tg-emoji\s+emoji-id="(\d{1,32})"\s*>(.*?)</tg-emoji>', re.S)
_TITLE_RE = re.compile(r'^(?P<icon>(?:<tg-emoji[^>]*>[^<]*</tg-emoji>|[^\w<«"(]{1,4})\s)?<b>(?P<title>[^<]{1,80})</b>$')
_SPOILER_OPEN_RE = re.compile(r"<tg-spoiler>")
_SPOILER_CLOSE_RE = re.compile(r"</tg-spoiler>")
_BQ_EXPANDABLE_RE = re.compile(r"<blockquote\s+expandable\s*>")


def emoji_to_plain(text: str) -> str:
    return _EMOJI_RE.sub(r"\1", text or "")


def _ce_img(m: re.Match[str]) -> str:
    eid, alt = m.group(1), html.escape(html.unescape(m.group(2)), quote=True)
    # the size is inline too: a stale stylesheet in a phone's cache must not show 100 px pictures (owner report 24.10)
    return (f'<img class="ce" src="/emoji/{eid}" alt="{alt}" data-ce="{eid}" width="20" height="20" '
            f'style="width:1.25em;height:1.25em;vertical-align:-0.28em">')


def emoji_ids(text: str) -> list[str]:
    return [eid for eid, _ in _EMOJI_ID_RE.findall(text or "")]


def to_web_html(text: str) -> str:
    out = _EMOJI_ID_RE.sub(_ce_img, text)
    out = _EMOJI_RE.sub(r"\1", out)
    out = _SPOILER_OPEN_RE.sub('<span class="spoiler">', out)
    out = _SPOILER_CLOSE_RE.sub("</span>", out)
    out = _BQ_EXPANDABLE_RE.sub('<blockquote class="expandable">', out)
    return out.strip("\n")


def _button(screen: str, label: str, action: str, arg: str | None, style: str | None) -> dict[str, Any]:
    b: dict[str, Any] = {"label": label, "screen": screen, "action": action, "arg": arg,
                         "style": style or auto_style(label)}
    from app.webapp import emoji

    for plain, eid in emoji.by_emoji.items():  # Telegram buttons carry plain emoji; the app shows the premium one
        if label.startswith(plain):
            b["ce"], b["ce_alt"], b["text"] = eid, plain, label[len(plain):].lstrip()
            break
    return b


def _split_title(text: str) -> tuple[str | None, str | None, str]:
    """(title, its icon html, rest) when the first line is a bare bold title (the same rule as rich windows)."""
    head, _sep, rest = text.lstrip("\n").partition("\n")
    m = _TITLE_RE.match(head.strip())
    if m is None or m.group("title").strip().endswith((".", "!", "?", ":", ",")):
        return None, None, text
    icon = to_web_html(m.group("icon").strip()) if m.group("icon") else None
    return html.unescape(m.group("title").strip()), icon, rest


def screen_payload(screen: Screen) -> dict[str, Any]:
    """JSON-safe description of a screen: text html, button rows, the text input it waits for."""
    plain, inline = rich_ui.split_inline_buttons(screen.text or "")
    rows: list[list[dict[str, Any]]] = []
    for label, data, style in inline:  # buttons inside the text go first, as in the plain group window
        cd = codec.decode(data)
        if cd is not None:
            rows.append([_button(cd.screen, label, cd.action, cd.arg, style)])
    for row in screen.keyboard:
        if row:
            rows.append([_button(screen.name, b.text, b.action, b.arg, b.style) for b in row])
    from app.webapp import emoji

    emoji.want_later(emoji_ids(plain) + [b["ce"] for row in rows for b in row if b.get("ce")])
    out: dict[str, Any] = {"name": screen.name, "html": to_web_html(plain), "rows": rows,
                           "awaiting_text": screen.awaiting_text}
    from app.webapp import living

    st = living.screen_state(screen.name, screen.state)  # W5: the map and the birth card as the bot built them
    if st is not None:
        out["state"] = st
    where = living.place(screen.name, screen.state)  # W12: the colour theme of a ripperdoc
    if where:
        out["place"] = where
    fx = (screen.state or {}).get("fx") if isinstance(screen.state, dict) else None
    wq = (screen.state or {}).get("wq") if isinstance(screen.state, dict) else None
    if wq:  # owner request 30.10: the quick-reaction game of every weapon at hand (app/bot/qte_modes.py)
        out["wq"] = wq
    if fx:  # owner request 30.10: the player's chrome going off in a fight (app/bot/combat_fx.py), played once by the app
        out["fx"] = fx
    title, icon, rest = _split_title(plain)
    if title:  # the app draws it in the title font; `html` stays whole for the menu header match (hud.py)
        out["title"], out["title_icon"], out["body"] = title, icon, to_web_html(rest)
    return out


def closed_payload() -> dict[str, Any]:
    return {"name": "", "html": "", "rows": [], "awaiting_text": None, "closed": True}
