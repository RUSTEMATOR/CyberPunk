"""Stickers of the legends (owner request 26.10, docs/PLAN_LIVING.md stage 4).

When a character leaves as a legend (services/legend.retire), a card goes into the queue (world_state
`sticker_queue`); the scheduler job draws it with Pillow (no image generation, code and a font only), adds it to the
group's sticker set (`<set_prefix>_by_<bot>`, created on the first legend, owned by the first admin) and sends it to
the group. Without Pillow installed the queue waits and nothing breaks.
"""

from __future__ import annotations

import io
import logging
import textwrap
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo

log = logging.getLogger(__name__)

QUEUE, SET = "sticker_queue", "sticker_set"
FONTS = Path(__file__).resolve().parents[2] / "content" / "fonts"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.stickers


async def queue(session: AsyncSession, card: dict) -> None:
    q = list(await repo.ws_get(session, QUEUE, []) or [])
    q.append(card)
    await repo.ws_set(session, QUEUE, q[-20:])


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def render(content: Content, card: dict) -> bytes:
    """A 512×512 PNG: a dark card, a neon frame in the colour of the ending, the crown, the name, the titles."""
    from PIL import Image, ImageDraw, ImageFont

    c = cfg(content)
    accent = _hex(c.colors.get(str(card.get("ending") or ""), c.colors.get("", "#f5c542")))
    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, size - 8, size - 8), radius=48, fill=(14, 14, 22, 245), outline=accent, width=10)
    d.rounded_rectangle((26, 26, size - 26, size - 26), radius=36, outline=(*accent, 90), width=2)
    bold = ImageFont.truetype(str(FONTS / "DejaVuSans-Bold.ttf"), 30)
    small = ImageFont.truetype(str(FONTS / "DejaVuSans.ttf"), 24)
    cx = size // 2
    crown = [(cx - 70, 150), (cx - 70, 92), (cx - 35, 122), (cx, 72), (cx + 35, 122), (cx + 70, 92), (cx + 70, 150)]
    d.polygon(crown, fill=accent)
    d.text((cx, 178), "ЛЕГЕНДА НАЙТ-СИТИ", font=small, fill=(200, 200, 210), anchor="mm")
    name = str(card.get("name") or "")
    for fs in (56, 48, 40, 34, 28):
        font = ImageFont.truetype(str(FONTS / "DejaVuSans-Bold.ttf"), fs)
        lines, cur = [], ""
        for word in name.split():  # whole words only, never cut inside a name
            test = f"{cur} {word}".strip()
            if cur and d.textlength(test, font=font) > 440:
                lines.append(cur)
                cur = word
            else:
                cur = test
        lines.append(cur)
        if len(lines) <= 2 and all(d.textlength(x, font=font) <= 440 for x in lines):
            break
    y = 240
    for ln in lines:
        d.text((cx, y), ln, font=font, fill=(255, 255, 255), anchor="mm")
        y += int(fs * 1.1)
    titles = ", ".join(card.get("titles") or [])
    for ln in textwrap.wrap(titles, width=28, break_long_words=False)[:2]:
        d.text((cx, y + 10), ln, font=bold, fill=accent, anchor="mm")
        y += 38
    foot = " · ".join(x for x in (str(card.get("ending_name") or ""), str(card.get("years") or "")) if x)
    if foot:
        d.text((cx, size - 70), foot, font=small, fill=(180, 180, 190), anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def process(session: AsyncSession, content: Content, bot, admin_id: int, chat_id: int) -> int:  # noqa: ANN001
    """Draw and publish the queued stickers. Returns how many went out."""
    q = list(await repo.ws_get(session, QUEUE, []) or [])
    if not q or not cfg(content).enabled or not admin_id:
        return 0
    try:
        import PIL  # noqa: F401
    except ImportError:
        log.warning("stickers: Pillow is not installed, %d legend stickers wait", len(q))
        return 0
    from aiogram.types import BufferedInputFile, InputSticker

    me = await bot.me()
    name = f"{cfg(content).set_prefix}_by_{me.username}"
    made = bool(await repo.ws_get(session, SET, False))
    sent = 0
    while q:
        card = q[0]
        png = render(content, card)
        st = InputSticker(sticker=BufferedInputFile(png, "legend.png"), format="static", emoji_list=["👑"])
        try:
            if not made:
                await bot.create_new_sticker_set(user_id=admin_id, name=name, title=cfg(content).set_title,
                                                 stickers=[st])
                made = True
                await repo.ws_set(session, SET, True)
            else:
                await bot.add_sticker_to_set(user_id=admin_id, name=name, sticker=st)
            got = await bot.get_sticker_set(name=name)
            if chat_id and got.stickers:
                await bot.send_sticker(chat_id, got.stickers[-1].file_id)
        except Exception:  # noqa: BLE001 - a Telegram refusal: three tries, then the card is dropped
            log.exception("stickers: could not publish %s", card.get("name"))
            card["tries"] = int(card.get("tries", 0)) + 1
            if card["tries"] < 3:
                q[0] = card
                break
        else:
            sent += 1
        q.pop(0)
    await repo.ws_set(session, QUEUE, q)
    return sent
