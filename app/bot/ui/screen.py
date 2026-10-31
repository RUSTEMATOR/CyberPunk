"""Screen: what a game handler returns (architecture rule 8, DESIGN.md section 3).

A handler never sends messages. It returns a `Screen`, and `WindowManager.show` puts it into the
player's one window. The text is Telegram HTML (the bot's default parse mode): escape every
player-typed string with `esc`.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.ui import callbacks_codec as codec
from app.bot.ui.style import text_width

log = logging.getLogger(__name__)

esc = html.escape

# Label characters a button shows on a narrow phone (iPhone mini/SE) with 1, 2, 3, 4+ buttons in a row.
# The live values come from balance.ui.button_chars; this is the fallback for direct calls. A label is measured with
# `width` (style.text_width): an emoji icon takes about two characters.
DEFAULT_BUTTON_CHARS: tuple[int, ...] = (24, 11, 7, 5)

ELLIPSIS = "…"
_TAG_RE = re.compile(r"</?(b|i|u|s|code|pre|a|tg-spoiler|blockquote|tg-emoji|tg-button)(\s[^>]*)?>")


@dataclass(frozen=True)
class Button:
    """An inline button. Its callback is `<screen name>:<action>[:<arg>]`, built when the screen renders."""

    text: str
    action: str
    arg: str | None = None
    style: str | None = None  # "primary" | "success" | "danger" (Bot API 9.4); None = the colour by its label
    url: str | None = None  # owner request 29.10: a link button in the group window (the Mini App presses the action)


# Button colours by label (owner request): the main step forward is green, what cannot be undone is red.
_SUCCESS = re.compile(r"(^|\s)(Прожить месяц|Беру|Согласиться|Принять|Да, |Подтвердить|Начать жизнь)")
_DANGER = re.compile(r"(^|\s)(Конец пути|Штурм|Напасть|Отомстить|Сменить путь|Уволиться|Удалить|Сдаться)")


def auto_style(label: str) -> str | None:
    if _SUCCESS.search(label):
        return "success"
    if _DANGER.search(label):
        return "danger"
    return None


Keyboard = list[list[Button]]


@dataclass
class Screen:
    """One state of the window.

    name:          id of the screen and prefix of its callbacks; the handler registered under this name
                   receives the button presses.
    text:          Telegram HTML; cut from the front to balance.ui.max_screen_chars when too long.
    keyboard:      rows of buttons.
    awaiting_text: key of the text handler that gets the player's next message, None = no input.
    state:         JSON-safe working data of the screen, saved in `windows.screen_state` and handed
                   back to the handlers through `ctx.window.state`.
    """

    text: str
    keyboard: Keyboard = field(default_factory=list)
    awaiting_text: str | None = None
    name: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    # owner request 26.10 (docs/PLAN_MODELS.md): a PNG shown as a photo right over the window while the screen is up;
    # screens whose name starts with `picture_keep` leave the last picture in place, any other screen takes it away
    picture: bytes | None = field(default=None, repr=False)
    picture_keep: str = ""
    picture_private: bool = False  # owner report 27.10: only in a private chat; in the group it would clutter the chat


def _balance_tags(text: str) -> str:
    """Drop closing tags that lost their opening tag when the front of the text was cut."""
    stack: list[str] = []
    out: list[str] = []
    pos = 0
    for m in _TAG_RE.finditer(text):
        out.append(text[pos:m.start()])
        pos = m.end()
        tag = m.group(0)
        name = m.group(1)
        if tag.startswith("</"):
            if name in stack:
                while stack and stack[-1] != name:
                    stack.pop()
                stack.pop()
                out.append(tag)
            # else: a stray closer, dropped
        else:
            stack.append(name)
            out.append(tag)
    out.append(text[pos:])
    return "".join(out)


def truncate_front(text: str, max_chars: int) -> str:
    """Keep the END of the text (the latest lines matter most). A leading ellipsis marks the cut."""
    if len(text) <= max_chars:
        return text
    tail = text[-(max_chars - 1):]
    nl = tail.find("\n")
    if 0 <= nl < min(200, len(tail) // 4):  # start on a clean line when one is close
        tail = tail[nl + 1:]
    return ELLIPSIS + _balance_tags(tail)


def row_budget(n: int, budgets: Sequence[int]) -> int:
    """Label characters each button gets in a row of `n` buttons."""
    return budgets[min(n, len(budgets)) - 1]


def width(label: str) -> int:
    """Rendered width of a button label in narrow characters (an emoji counts as two)."""
    return text_width(label)


_warned: set[tuple[str, str]] = set()


def fit_rows(keyboard: Keyboard, budgets: Sequence[int], screen_name: str = "") -> Keyboard:
    """The narrow-phone layout rule. Telegram cuts a label that does not fit with an ellipsis, and buttons
    never wrap: a row with a label over its per-button budget becomes one button per row. A single button
    still over the full-row budget is logged (once per label) so its source can be shortened."""
    out: Keyboard = []
    for row in keyboard:
        if not row:
            continue
        if len(row) > 1 and any(width(b.text) > row_budget(len(row), budgets) for b in row):
            out.extend([b] for b in row)
        else:
            out.append(row)
    for row in out:
        if len(row) == 1 and width(row[0].text) > budgets[0] and (screen_name, row[0].text) not in _warned:
            _warned.add((screen_name, row[0].text))
            log.warning("ui: button over %d chars on screen %r: %r", budgets[0], screen_name, row[0].text)
    return out


def pack(buttons: Sequence[Button], budgets: Sequence[int], max_per_row: int = 4) -> Keyboard:
    """Rows for a set of short buttons (list numbers, tabs): as many per row as every label fits, up to
    `max_per_row`. List screens put the details in the text as a numbered list and pick by number here."""
    per_row = 1
    for k in range(min(max_per_row, len(buttons)), 1, -1):
        if all(width(b.text) <= row_budget(k, budgets) for b in buttons):
            per_row = k
            break
    return [list(buttons[i:i + per_row]) for i in range(0, len(buttons), per_row)]


def pick_label(n: int, word: str | None, budgets: Sequence[int]) -> str:
    """«1 · Эскорт» when it fits two to a row, else just «1»."""
    label = f"{n} · {word}" if word else str(n)
    return label if width(label) <= row_budget(2, budgets) else str(n)


def list_rows(
    titles: Sequence[str], action: str | Sequence[str], args: Sequence[str | None], budgets: Sequence[int],
    numbers: Sequence[int] | None = None,
) -> tuple[bool, Keyboard]:
    """Pick buttons for a list screen. When every title fits a full row the buttons carry the titles (two to
    a row when they fit); otherwise they carry the list numbers (`numbers`, default 1..n) and the caller
    numbers the entries in the text. `action` is one for all or one per entry. Returns (numbered, rows)."""
    acts = [action] * len(titles) if isinstance(action, str) else list(action)
    if all(width(x) <= budgets[0] for x in titles):
        return False, pack([Button(x, c, a) for x, c, a in zip(titles, acts, args)], budgets, max_per_row=2)
    nums = list(numbers) if numbers is not None else list(range(1, len(titles) + 1))
    return True, pack([Button(str(n), c, a) for n, c, a in zip(nums, acts, args)], budgets)


def choices(
    labels: Sequence[str], action: str, budgets: Sequence[int], args: Sequence[str] | None = None,
) -> tuple[list[str], Keyboard]:
    """Buttons for a list of choices (one full-width button each), or, when a label does not fit, a numbered
    list for the screen text (escaped) and number buttons. Returns (text lines, rows)."""
    keys = list(args) if args is not None else [str(i) for i in range(len(labels))]
    if all(width(x) <= budgets[0] for x in labels):
        return [], [[Button(x, action, k)] for x, k in zip(labels, keys)]
    lines = [f"{n}. {esc(x)}" for n, x in enumerate(labels, 1)]
    return lines, pack([Button(str(n), action, k) for n, k in enumerate(keys, 1)], budgets)


def markup_of(screen: Screen, button_chars: Sequence[int] = DEFAULT_BUTTON_CHARS) -> InlineKeyboardMarkup:
    """The inline keyboard of a screen, laid out for narrow phones (`fit_rows`).
    Raises ValueError for callback data over 64 bytes."""
    if not screen.name and screen.keyboard:
        raise ValueError("a screen with buttons needs a name")
    rows = [
        [
            InlineKeyboardButton(text=b.text, url=b.url) if b.url else
            InlineKeyboardButton(text=b.text, callback_data=codec.encode(screen.name, b.action, b.arg),
                                 style=b.style or auto_style(b.text))
            for b in row
        ]
        for row in fit_rows(screen.keyboard, button_chars, screen.name)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render(
    screen: Screen, max_chars: int, button_chars: Sequence[int] = DEFAULT_BUTTON_CHARS,
) -> tuple[str, InlineKeyboardMarkup]:
    return truncate_front(screen.text, max_chars), markup_of(screen, button_chars)
