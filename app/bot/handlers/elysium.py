"""Elysium, the station of the rich (owner request: the «Elysium» crossover). city -> elysium."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import elysium as ee
from app.services import elysium as svc
from app.services import game
from app.services.game import ActiveCharacter

TITLE = "Элизиум"
INTRO = ("Колесо станции висит над Землёй, его видно днём. Там трава, бассейны и медкапсулы, которые за минуту "
         "лечат всё: рак, раны, лучевую болезнь. Внизу — пыль, заводы и очереди. Граждан станции немного, остальным "
         "остаются контрабандисты с шаттлами, которые сбивают через раз.")
BLOCKED = {
    "have": "Уже есть.",
    "eddies": "Не хватает эдди.",
    "ap": "ОД кончились. Нажми «Прожить месяц».",
    "jail": "Из камеры на станцию не улететь.",
    "not": "Медкапсулы только на станции. Нужно гражданство или шаттл.",
    "cool": "Медкапсула уже была: гражданину — раз в месяц, гостю — один раз за полёт.",
    "own": "Каркас уже в тебе.",
}


def elysium_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    c = ee.cfg(content)
    st = ch.state
    if ee.citizen(st):
        where = "🛰 Ты гражданин Элизиума: бонусы станции каждый месяц, медкапсула раз в месяц бесплатно."
    elif ee.on_board(st):
        where = "🛰 Ты на станции нелегально до конца месяца. Медкапсула — один раз."
    else:
        where = "🌍 Ты на Земле."
    lines = [style.header("district", TITLE), f"Эдди: {views.money(st.eddies)} · ОД: {st.ap} · розыск {st.heat}/5", where]
    if ee.radiation(st):
        lines.append(f"☢️ Лучевая болезнь: осталось {ee.radiation(st)} нед.")
    lines.append("")
    if note:
        lines += [note, ""]
    lines.append(f"<i>{INTRO}</i>")
    rows: list[list[Button]] = []
    if ee.on_board(st):
        rows.append([Button("Медкапсула", "medbay", style="success")])
    else:
        rows.append([Button(f"Шаттл контрабандиста · {views.money(c.shuttle_price)}", "shuttle")])
    if not ee.citizen(st):
        rows.append([Button(f"Гражданство · {views.money(c.citizen_price)}", "buy")])
        rows.append([Button(f"Украсть данные гражданина ({c.heist_ap} ОД)", "heist", style="danger")])
    rows.append([Button(f"Смена на заводе дронов ({c.plant_ap} ОД)", "plant")])
    rows.append([Button(f"Экзоскелет · {views.money(c.exo_price)}", "exo")])
    rows.append([Button(texts.BACK, "city"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="elysium")


def _note(content: Content, r: svc.ElysiumResult) -> str:
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


@on_screen("elysium")
async def elysium_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    c = ctx.content
    if action == "city":
        return await goto(ctx, "city")
    fn = {"buy": svc.buy_citizenship, "shuttle": svc.shuttle, "medbay": svc.medbay, "plant": svc.plant,
          "exo": svc.exo, "heist": svc.heist}.get(action)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        from app.bot.handlers.world import gate_screen, resident

        if not resident(c, ch, "elysium"):  # owner request 30.10: only for those who live there
            return gate_screen(c, ch, "elysium")
        if fn is None:
            return elysium_screen(c, ch)
        year = await game.current_year(s, c)
        r = await fn(s, c, ch, random.Random(), year, ctx.ui.settings.timezone)
    if r.died:
        from app.bot.handlers.week import death_screen

        return death_screen(ch.state.name, r.death_cause, [_note(c, r)])
    return elysium_screen(c, ch, _note(c, r))
