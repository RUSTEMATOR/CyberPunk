"""Libria, the city where feeling is a crime (owner request 23.10: the «Equilibrium» crossover). city -> libria."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import libria as le
from app.services import game
from app.services import libria as svc
from app.services.game import ActiveCharacter

TITLE = "Либрия"
INTRO = ("Город за стеной, построенный после Третьей мировой. Здесь решили, что войны рождаются из чувств, и запретили "
         "чувствовать. Каждый житель дважды в день колет Прозиум. Картины, музыку и книги жгут клирики Тетраграмматона "
         "в чёрных плащах, мастера ган-каты. С экранов говорит Отец. В подвалах прячется Подполье.")
BLOCKED = {
    "have": "Ты уже клирик.",
    "eddies": "Не хватает эдди.",
    "ap": "ОД кончились. Нажми «Прожить месяц».",
    "jail": "Из камеры в Либрию не попасть.",
    "nodose": "На экзамен не пускают без Прозиума в крови. Сначала курс.",
    "notcleric": "Это работа клирика Тетраграмматона.",
    "dosed": "Пока Прозиум в крови, ты не чувствуешь ничего. Даже желания бунтовать. Дождись конца курса.",
    "gone": "Этого уже нет.",
}


def libria_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    c = le.cfg(content)
    st = ch.state
    if le.on_dose(st):
        state_line = f"💊 Прозиум в крови: ещё {le.dose_left(st)} нед. Ни горя, ни радости."
    else:
        state_line = "💊 Без Прозиума. Ты чувствуешь. Здесь это преступление."
    lines = [style.header("district", TITLE), f"Эдди: {views.money(st.eddies)} · ОД: {st.ap} · розыск {st.heat}/5",
             state_line]
    if le.cleric(st):
        lines.append("🧥 Клирик Тетраграмматона: ган-ката +2 к бою, +1 к стрельбе."
                     + (f" Дома спрятано: {le.art(st)}." if le.art(st) else ""))
    lines.append("")
    if note:
        lines += [note, ""]
    lines.append(f"<i>{INTRO}</i>")
    lines += ["", "Прозиум: эмпатия −2, общение −2, бой +1, хром ранит душу вдвое меньше, настроение держится серым. "
                  "Когда курс кончается — тяжёлая неделя, но человечность возвращается."]
    rows: list[list[Button]] = []
    if le.rec(st).get("pending_art"):
        rows.append([Button("Сжечь", "burn", style="danger"), Button("Оставить себе", "keep")])
    rows.append([Button(f"Курс Прозиума · {views.money(c.dose_price)}", "dose")])
    if le.cleric(st):
        rows.append([Button(f"Рейд на нарушителей ({c.raid_ap} ОД)", "raid")])
        rows.append([Button("Пропустить дозу и восстать", "rebel", style="danger")])
    else:
        rows.append([Button("Экзамен клирика", "exam")])
    rows.append([Button(f"Подполье: контрабанда ({c.smuggle_ap} ОД)", "smuggle")])
    rows.append([Button(texts.BACK, "city"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="libria")


def _note(content: Content, r: svc.LibriaResult) -> str:
    if r.blocked is not None:
        return BLOCKED.get(r.blocked, r.blocked)
    parts: list[str] = []
    if r.check is not None:
        parts.append(views.check_line(r.check))  # type: ignore[arg-type]
    if r.text:
        parts.append(style.quote(texts.esc(r.text)))
    if r.applied is not None:
        sm = views.effects_summary(r.applied, content)
        if sm:
            parts.append(sm)
    parts.extend(texts.esc(x) for x in r.lines)
    return "\n".join(parts)


@on_screen("libria")
async def libria_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    c = ctx.content
    if action == "city":
        return await goto(ctx, "city")
    fn = {"dose": svc.dose, "exam": svc.exam, "raid": svc.raid, "smuggle": svc.smuggle, "rebel": svc.rebel}.get(action)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        from app.bot.handlers.world import gate_screen, resident

        if not resident(c, ch, "libria"):  # owner request 30.10: only for those who live there
            return gate_screen(c, ch, "libria")
        year = await game.current_year(s, c)
        rng, tz = random.Random(), ctx.ui.settings.timezone
        if action in ("burn", "keep"):
            r = await svc.art_choice(s, c, ch, rng, year, tz, keep=action == "keep")
        elif fn is not None:
            r = await fn(s, c, ch, rng, year, tz)
        else:
            return libria_screen(c, ch)
    return libria_screen(c, ch, _note(c, r))
