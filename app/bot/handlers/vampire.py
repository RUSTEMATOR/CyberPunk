"""The vampire's window (owner request 24.10): work -> «Кормёжка»."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import derived
from app.engine import vampire as eng
from app.services import game
from app.services import vampire as svc
from app.services.game import ActiveCharacter

BLOCKED = {"not": "Контура в венах нет.", "jail": "В камере некого.", "none": "Такой охоты нет.",
           "eddies": "Не хватает эдди.", "full": "Контур полон: больше не влезет.", "ap": "ОД кончились."}


def vamp_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    c = eng.cfg(content)
    b = eng.blood(st)
    lines = [style.header("hp", "Кормёжка"),
             f"❤️ {st.hp}/{derived.max_hp(st, content)} · кровь в контуре {b}/{c.blood_max} · ОД {st.ap}",
             "",
             f"Каждая единица крови — +1 к максимуму здоровья, каждые {c.regen_per} — +1 к восстановлению. Тело сжигает "
             f"{c.decay} в неделю. Пусто — голод: настроение и проверки падают, раны не затягиваются.",
             "Днём не выходи: свет режет глаза, солнце жжёт кожу."]
    if b <= 0:
        lines.append("⚠️ <b>Голод.</b> Контур пуст.")
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    for key, f in c.feeds.items():
        extra = f" · {views.money(-f.eddies)}" if f.eddies < 0 else ""
        lines += ["", f"<b>{texts.esc(f.name)}</b> · кровь +{f.blood}{extra}" +
                  (f" · человечность {style.signed(f.humanity)}" if f.humanity else "") +
                  (" · проверка" if f.stat else "")]
        rows.append([Button(f"{f.name}{extra}"[:24], "feed", key)])
    rows.append([Button(texts.BACK, "work"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="vamp")


@on_screen("vamp")
async def vamp_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    if action == "menu":
        return await menu_screen(ctx)
    if action == "work":
        return await goto(ctx, "work", "open")
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if action != "feed" or not arg:
            return vamp_screen(c, ch)
        year = await game.current_year(s, c)
        r = await svc.feed(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, arg)
    if r.blocked or r.plan is None:
        return vamp_screen(c, ch, BLOCKED.get(r.blocked or "none", ""))
    note = texts.esc(r.plan.text)
    if r.plan.check is not None:
        note = views.check_line(r.plan.check) + "\n" + note
    if r.applied is not None:
        extra = "\n".join(views.effects_lines(r.applied, c))
        if extra:
            note += "\n" + extra
    return vamp_screen(c, ch, note)
