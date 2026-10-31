"""Rich windows (owner request, Bot API 10.1-10.3 rich messages): the screen HTML turned into rich HTML.

Screens keep writing the ordinary Telegram HTML of `Screen.text` (lines split by "\\n"). `to_rich_html` turns it
into the HTML of a rich message: blank lines split paragraphs, a line break becomes `<br/>` (a bare newline is
collapsed to a space by Telegram), the first header line of a screen becomes a heading, a lone bold line inside
becomes a subheading, the separator line becomes a divider, quotes and code stay their own blocks.

A screen may put a button straight into its text with `inline_button`: `<tg-button type="callback_data" ...>`.
`split_inline_buttons` takes such buttons out for the plain fallback (when Telegram refuses the rich message,
the outbox sends the plain text and these buttons go under it as ordinary rows).
"""

from __future__ import annotations

import html
import re

from app.bot.ui import callbacks_codec as codec

STYLES = ("primary", "success", "danger")

_BTN_RE = re.compile(r'<tg-button\s+type="callback_data"\s+data="([^"]*)"(?:\s+style="(\w+)")?\s*>(.*?)</tg-button>', re.S)
_BLOCK_RE = re.compile(r"<(blockquote|pre)(\s[^>]*)?>.*?</\1>", re.S)
_HEAD_RE = re.compile(r"^(?P<icon>(?:<tg-emoji[^>]*>[^<]*</tg-emoji>|[^\w<«\"(]{1,4})\s)?<b>(?P<title>[^<]{1,80})</b>$")
_SEP_CHARS = set("┈─━—-=_ ")


def inline_button(screen: str, label: str, action: str, arg: str | None = None, style: str | None = None) -> str:
    """A button placed inside the text of a rich window. In the plain fallback it moves under the text."""
    data = html.escape(codec.encode(screen, action, arg), quote=True)
    st = f' style="{style}"' if style in STYLES else ""
    return f'<tg-button type="callback_data" data="{data}"{st}>{html.escape(label, quote=False)}</tg-button>'


def split_inline_buttons(text: str) -> tuple[str, list[tuple[str, str, str | None]]]:
    """(the text without inline buttons, [(label, callback data, style)])."""
    found: list[tuple[str, str, str | None]] = []

    def take(m: re.Match[str]) -> str:
        found.append((html.unescape(re.sub(r"<[^>]+>", "", m.group(3))), html.unescape(m.group(1)), m.group(2)))
        return ""

    out = _BTN_RE.sub(take, text)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return out, found


def _is_sep(line: str) -> bool:
    s = line.strip()
    return len(s) >= 4 and set(s) <= _SEP_CHARS


def _heading(line: str) -> str | None:
    m = _HEAD_RE.match(line.strip())
    if m is None:
        return None
    title = m.group("title").strip()
    if title.endswith((".", "!", "?", ":", ",")):
        return None  # a bold sentence, not a title
    icon = m.group("icon") or ""
    return f"{icon}{title}"


def _text_blocks(chunk: str, first: list[bool]) -> list[str]:
    """Paragraphs of a plain text chunk (no quotes or code inside)."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", chunk):
        lines = [ln for ln in para.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            continue
        buf: list[str] = []

        def flush() -> None:
            if buf:
                out.append("<p>" + "<br/>".join(buf) + "</p>")
                buf.clear()

        for ln in lines:
            if _is_sep(ln):
                flush()
                out.append("<hr/>")
                continue
            head = _heading(ln) if not buf else None
            if head is not None:
                flush()
                out.append(f"<h3>{head}</h3>" if first[0] else f"<h4>{head}</h4>")
                first[0] = False
                continue
            first[0] = False
            buf.append(ln)
        flush()
    return out


def _block(m: re.Match[str]) -> str:
    tag = m.group(1)
    whole = m.group(0)
    if tag == "pre":
        return whole
    open_end = whole.index(">") + 1
    inner = whole[open_end:-len("</blockquote>")].strip("\n")
    inner = "<br/>".join(inner.split("\n"))
    return whole[:open_end] + inner + "</blockquote>"


def to_rich_html(text: str) -> str:
    """The rich HTML of a screen text."""
    parts: list[str] = []
    pos = 0
    first = [True]
    for m in _BLOCK_RE.finditer(text):
        parts.extend(_text_blocks(text[pos:m.start()], first))
        parts.append(_block(m))
        first[0] = False
        pos = m.end()
    parts.extend(_text_blocks(text[pos:], first))
    return "".join(parts) or "<p>…</p>"
