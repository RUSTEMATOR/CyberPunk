"""The visual style of the bot: the icon dictionary and the formatting helpers (owner's decision, style_ru.md rule 7).

Icons live in content/icons.yaml (schema `Icon`): a key, the regular emoji and an optional premium custom emoji id.
`ICONS[key]` is the form for message text (`<tg-emoji emoji-id="…">emoji</tg-emoji>` when an id is set, else the
emoji); `PLAIN[key]` is the regular emoji, the only form a button label can carry (`label` uses it).

Rules of use:
- one icon per line or button, at its start, followed by a space; never inside a sentence of narrative;
- narrative goes into `quote` (or `expandable` when it is long and secondary), numbers go into iconed lines;
- bars (`bar`, `meter`) only for a value that has a maximum: health, humanity, mood, charge, enemy health;
- the helpers take HTML that is already escaped: escape player text and content with `esc` before passing it in.

The button width budget (`ui.button_chars`, ui/screen.py) counts an emoji as two characters: see `text_width`.
"""

from __future__ import annotations

import html
import unicodedata
from pathlib import Path

import yaml

from app.content.schemas import Icon

esc = html.escape

ICONS_FILE = Path(__file__).resolve().parent.parent.parent.parent / "content" / "icons.yaml"
ARCANA_PREFIX = "arcana."

# -- icons ------------------------------------------------------------------------------------------------------------


def _load(path: Path) -> dict[str, Icon]:
    """The icons of content/icons.yaml. A broken file fails here at import; `loader --strict` names the problem."""
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []
    return {icon.key: icon for icon in (Icon.model_validate(rec) for rec in raw)}


def render(icon: Icon) -> str:
    """The text form of an icon: the custom emoji with its fallback, or the plain emoji."""
    if icon.custom_emoji_id:
        return f'<tg-emoji emoji-id="{icon.custom_emoji_id}">{icon.emoji}</tg-emoji>'
    return icon.emoji


_ALL = _load(ICONS_FILE)
PLAIN: dict[str, str] = {k: v.emoji for k, v in _ALL.items() if not k.startswith(ARCANA_PREFIX)}
ICONS: dict[str, str] = {k: render(v) for k, v in _ALL.items() if not k.startswith(ARCANA_PREFIX)}
# Misty's arcana by tarot key (content/tarot.yaml), text form.
ARCANA: dict[str, str] = {k[len(ARCANA_PREFIX):]: render(v) for k, v in _ALL.items() if k.startswith(ARCANA_PREFIX)}

# Every icon key the code uses: the content loader checks that icons.yaml has them all.
REQUIRED_ICONS: tuple[str, ...] = (
    "profile", "gigs", "work", "activities", "relations", "property", "ripper", "crime", "move", "daily", "week",
    "hp", "humanity", "mood", "eddies", "debt", "ap", "heat", "street_cred", "status", "fame", "charge", "ram",
    "glitches", "luck", "tokens", "xp", "stats", "skills", "roles", "vows", "traits", "family", "age", "district",
    "date", "home", "jail", "weapon", "armor", "chrome", "tolerance", "infection", "fight", "danger", "damage",
    "dodge", "cover", "hack", "enemy", "you", "stun", "flee", "death", "obituary", "grave", "maxtac", "ncpd",
    "fixer", "misty", "afterlife", "trauma", "psycho", "ward", "arrest", "escape", "news", "canon", "era", "war",
    "lineage", "legends", "who", "help", "engram", "legacy", "crypto", "car", "race", "cat", "catfight", "caliber",
    "dirt", "dump", "dice", "slots", "ok", "fail", "crit", "check", "event", "quest", "todo", "punch", "exit",
    "shop", "reward", "note", "time", "new", "lock", "money_in", "train", "study", "braindance", "therapy", "rest",
    "meditation", "martial_arts", "service", "surrender", "foe", "taken", "nonlethal", "stealth", "talk", "heart",
    "rank1", "rank2", "rank3", "lawyer",
)

# effect keys of the engine (texts.EFFECT_LABELS) -> icon keys
EFFECT_ICONS: dict[str, str] = {
    "eddies": "eddies", "hp": "hp", "mood": "mood", "humanity": "humanity", "street_cred": "street_cred",
    "status": "status", "fame": "fame", "heat": "heat", "luck_pool": "luck", "glitches": "glitches", "debt": "debt",
}

BAR_FULL = "▰"
BAR_EMPTY = "▱"
SEP = "┈┈┈┈┈┈┈┈┈┈┈┈"
DOT = "·"
MINUS = "−"


def icon(key: str) -> str:
    """The text form of an icon; an unknown key is a programming error, so it fails loudly."""
    return ICONS[key]


def line(key: str, text: str) -> str:
    """'💰 1 200 эдди': one iconed line. `text` is HTML, already escaped."""
    return f"{ICONS[key]} {text}"


def label(key: str, text: str) -> str:
    """A button label with its icon. Buttons are plain text: always the regular emoji, never a custom one."""
    return f"{PLAIN[key]} {text}"


def header(key_or_icon: str, title: str) -> str:
    """'💼 <b>Заказы: Уотсон</b>'. `title` is HTML, already escaped; the first argument is an icon key or an icon."""
    ic = ICONS.get(key_or_icon, key_or_icon)
    return f"{ic} <b>{title}</b>"


def quote(text: str) -> str:
    """A block quote (narrative, the narrator's text, a letter). `text` is HTML, already escaped."""
    text = text.strip()
    return f"<blockquote>{text}</blockquote>" if text else ""


def expandable(text: str) -> str:
    """A folded block quote: Telegram shows the first lines and a tap opens the rest."""
    text = text.strip()
    return f"<blockquote expandable>{text}</blockquote>" if text else ""


def spoiler(text: str) -> str:
    return f"<tg-spoiler>{text}</tg-spoiler>"


def bold(text: str) -> str:
    return f"<b>{text}</b>"


def italic(text: str) -> str:
    return f"<i>{text}</i>"


def strike(text: str) -> str:
    return f"<s>{text}</s>"


def signed(n: int | float) -> str:
    """'+5', '−5' (a real minus sign), '0'."""
    if isinstance(n, float) and not n.is_integer():
        return f"+{n:g}" if n > 0 else f"{MINUS}{abs(n):g}" if n < 0 else "0"
    n = int(n)
    return f"+{n}" if n > 0 else f"{MINUS}{abs(n)}" if n < 0 else "0"


def bar(value: int, maximum: int, width: int = 10) -> str:
    """'▰▰▰▰▰▰▱▱▱▱'. Any value above zero shows at least one cell; only a full value fills the bar."""
    width = max(1, width)
    if maximum <= 0:
        return BAR_EMPTY * width
    v = max(0, min(value, maximum))
    filled = round(v * width / maximum)
    if v > 0:
        filled = max(1, filled)
    if v < maximum:
        filled = min(width - 1, filled)
    return BAR_FULL * filled + BAR_EMPTY * (width - filled)


def meter(key: str, value: int, maximum: int, width: int = 10, title: str = "", extra: str = "") -> str:
    """'❤️ Здоровье ▰▰▰▰▰▰▱▱ 45/70': an iconed bar with the numbers; without a title the icon names the scale."""
    head = f"{ICONS[key]} {title} " if title else f"{ICONS[key]} "
    return f"{head}{bar(value, maximum, width)} {value}/{maximum}{extra}"


def delta(key: str, word: str, n: int) -> str:
    """'💰 эдди −200': one consequence line."""
    return f"{ICONS[key]} {word} {signed(n)}"


def rank(n: int) -> str:
    """The place in a top list: 🥇 🥈 🥉 for the first three, then '4.'."""
    return ICONS[f"rank{n}"] if 1 <= n <= 3 else f"{n}."


def sep() -> str:
    return SEP


def text_width(s: str) -> int:
    """How wide a button label renders on a phone, in narrow characters: an emoji (or any wide character) counts
    as two, a variation selector, a zero-width joiner and combining marks as nothing. For the icons of this module the
    width is never less than len(), so a label that fits by this width also fits by a plain length count."""
    width = 0
    chars = list(s)
    for i, ch in enumerate(chars):
        cp = ord(ch)
        if cp in (0xFE0F, 0xFE0E, 0x200D) or unicodedata.combining(ch):
            continue
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if unicodedata.east_asian_width(ch) in ("W", "F") or nxt == "️" or 0x1F000 <= cp <= 0x1FAFF:
            width += 2
        else:
            width += 1
    return width
