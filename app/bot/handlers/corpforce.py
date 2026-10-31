"""The window «cforce» (owner request 26.10, docs/PLAN_CORPFORCE.md part 2): the operations of the player's megacorp
in the districts of Night City, with the own fighters and the hired players at the side."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen, pack
from app.db import session as db
from app.engine import control as ctl_eng
from app.services import control, game
from app.services import corpforce as svc
from app.services.game import ActiveCharacter

WHY = {"off": "Операции закрыты.", "nocorp": "Нужна своя мегакорпорация.", "district": "Такого района нет.",
       "city": "Операции — только в Найт-Сити.", "jail": "Из камеры не командуют.",
       "busy": "Сначала разберись с тем, что уже ждёт.", "today": "На сегодня операций хватит: люди отдыхают.",
       "eddies": "Не хватает эдди на операцию.", "ap": "Не хватает ОД.", "nofoe": "Здесь не с кем воевать."}


async def force_screen(ctx: Ctx, ch: ActiveCharacter, note: str | None = None) -> Screen:
    content = ctx.content
    c = svc.cfg(content)
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        corp = await svc.my_corp(s, ch)
        own, hired = await svc.allies_count(s, content, ch)
        meters = await control.load(s, content)
    if corp is None:
        return Screen("Силовые операции — для владельцев своей мегакорпорации.", [[Button(texts.MENU, "menu")]],
                      name="cforce")
    left = max(0, c.per_day - svc.done_today(ch, tz))
    lines = [style.header("war", f"«{texts.esc(str(corp.get('name')))}»: силовые операции"),
             "Ты ведёшь людей в район: зачистка выбивает банды, удар по корпорации ломает её хватку. Рядом с тобой "
             "встают твои бойцы и нанятые игроки — охрана и сотрудники корпорации, каждый со своими навыками. "
             "Сколько людей приведёшь, столько и встретит другая сторона.",
             f"С тобой пойдут: свои бойцы {own}, игроки {hired}.",
             f"Операция: {c.ap} ОД, {views.money(c.price)} эдди, розыск +{c.heat}. Сегодня осталось: {left}.",
             "", "Выбери район (банды / корпорации):"]
    btns: list[Button] = []
    for key in svc.districts(content):
        m = meters.get(key) or {}
        d = content.districts[key]
        btns.append(Button(f"{d.name} {m.get(ctl_eng.GANG, '?')}/{m.get(ctl_eng.CORP, '?')}"[:24], "d", key))
    rows = pack(btns, content.balance.ui.button_chars, max_per_row=2)
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="cforce")


async def district_screen(ctx: Ctx, ch: ActiveCharacter, key: str) -> Screen:
    content = ctx.content
    c = svc.cfg(content)
    d = content.districts[key]
    async with db.session_scope() as s:
        m = (await control.load(s, content)).get(key) or {}
        year = await game.current_year(s, content)
    gangs = [content.gangs[g].name for g in d.gangs if g in content.available("gangs", year)]
    corps = [content.corps[k].name for k in ctl_eng.megacorps_of(content, key) if k in content.available("corps", year)]
    lines = [style.header("war", texts.esc(d.name)),
             f"Банды держат {m.get(ctl_eng.GANG, '?')}%: {texts.esc(', '.join(gangs) or 'никто')}.",
             f"Корпорации держат {m.get(ctl_eng.CORP, '?')}%: {texts.esc(', '.join(corps) or 'никто')}.",
             "",
             f"• Зачистка: бой с бандой района. Победа: банды −{c.cleanse_gang}%, корпорации +{c.cleanse_corp}%.",
             f"• Удар по корпорации: бой с её охраной. Победа: хватка корпораций −{c.strike_corp}%, удар по "
             f"стойкости корпорации."]
    rows = [[Button(f"Зачистка · {c.ap} ОД", "go", f"cleanse.{key}", style="danger")]]
    if corps:
        rows.append([Button(f"Удар по корпорации · {c.ap} ОД"[:28], "go", f"strike.{key}", style="danger")])
    rows.append([Button(texts.BACK, "open"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="cforce")


@on_screen("cforce")
async def cforce_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "empire")
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if action == "d" and arg in ctx.content.districts:
            return await district_screen(ctx, ch, arg)
        if action == "go" and arg and "." in arg:
            kind, key = arg.split(".", 1)
            year = await game.current_year(s, ctx.content)
            r = await svc.start(s, ctx.content, ch, random.Random(), year, tz, kind, key)
            if isinstance(r, str):
                return await force_screen(ctx, ch, WHY.get(r, r))
            from app.bot.handlers.combat import step_screen

            return step_screen(ctx.content, ch, r)
    return await force_screen(ctx, ch)
