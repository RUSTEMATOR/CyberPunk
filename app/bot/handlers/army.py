"""The window of the own fighters (owner request 25.10): «Криминал» → «Мои бойцы»."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen, on_text
from app.bot.ui.screen import Button, Screen, pack
from app.content.loader import Content
from app.db import session as db
from app.engine import army as eng
from app.services import army as svc
from app.services import game
from app.services.game import ActiveCharacter

BLOCK = {"jail": "Из камеры людей не соберёшь.", "busy": "Сначала разберись с тем, что уже ждёт.",
         "today": "На сегодня хватит: завтра снова.", "eddies": "Не хватает эдди.", "ap": "Не хватает ОД.",
         "small": "Война банд — с банды и выше: нужно хотя бы пять бойцов.", "gone": "Такой цели нет.",
         "not": "Этот человек не боец.", "att": "Не доверяет тебе настолько.", "have": "Уже в твоих людях."}
NAME_MAX = 32


def army_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    c = eng.cfg(content)
    st = ch.state
    roster = eng.soldiers(st)
    n = len(roster)
    t = eng.tier(content, n)
    nt = eng.next_tier(content, n)
    lines = [style.header("crime", eng.title(content, st) or "Мои бойцы")]
    if t is None:
        lines.append("Пока никого. Людей можно собрать на улице или взять к себе вооружённого горожанина, который тебе "
                     "доверяет (его карточка в «Городе» → «Завербовать»). Иногда побеждённый бандит сам просится к тебе.")
    else:
        lines.append(f"Бойцов: <b>{n}</b> · сила {eng.power(st)} · в бой с тобой идут {min(n, t.in_fight)}"
                     + (f" · до ступени «{nt.name}»: ещё {nt.size - n}" if nt else ""))
        lines.append(f"Каждый месяц: рэкет +{eng.racket(content, st)}, жалованье −{eng.wage(content, st)} эдди. "
                     "Нечем платить — часть людей уходит.")
    lines += ["", "Ступени: " + " → ".join(f"{x.name} ({x.size}+, в бою {x.in_fight})" for x in c.tiers) + ".",
              "Победа даёт бойцам опыт, они растут в силе. Павший погибает навсегда."]
    if roster:
        lines += ["", "<b>Лучшие</b>"]
        for s in eng.strongest(st, 8):
            lines.append(f"• {texts.esc(str(s.get('name')))} · {eng.ROLES.get(int(s.get('danger', 1)), 'боец')} "
                         f"(сила {s.get('danger')})")
        if n > 8:
            lines.append(f"…и ещё {n - 8}.")
    if note:
        lines += ["", note]
    rows: list[list[Button]] = [[Button(f"Вербовка · {views.money(c.street_price)}"[:24], "street")]]
    if eng.needs_name(content, st):
        rows.insert(0, [Button("Дать имя банде", "name", style="success")])
    if t is not None and [x.key for x in c.tiers].index(t.key) >= 1:
        rows.append([Button(f"Война банд · {c.war_ap} ОД", "war", style="danger")])
    from app.engine import companion as comp  # owner request 25.10: the fighters go without the character

    if roster or comp.active_members(st):
        rows.append([Button("Отправить без тебя", "dispatch", style="danger")])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="army")


def war_screen(content: Content, ch: ActiveCharacter, year: int) -> Screen:
    c = eng.cfg(content)
    t = eng.tier(content, len(eng.soldiers(ch.state)))
    tix = [x.key for x in c.tiers].index(t.key) if t else 0
    lines = [style.header("war", "Война банд"),
             f"Вся сила идёт на точки чужой банды: опасность 5, врагов на {c.war_extra_per_tier * tix} больше, "
             f"с тобой {min(len(eng.soldiers(ch.state)), (t.in_fight if t else 0) * c.war_ally_mult, c.ally_cap)} бойцов. "
             f"{c.war_ap} ОД, до {c.war_per_day} войн в день. Победа: касса, перебежчики, банды района теряют землю."]
    targets = svc.war_targets(content, ch, year)
    rows = pack([Button(content.gangs[k].name[:24], "go", k) for k in targets], content.balance.ui.button_chars, max_per_row=2)
    rows.append([Button(texts.BACK, "open"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="army")


@on_screen("army")
async def army_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "crime")
    if action == "dispatch":  # owner request 25.10
        return await goto(ctx, "dispatch", "open")
    c = ctx.content
    tz = ctx.ui.settings.timezone
    if action == "name":
        return Screen(f"{style.header('crime', 'Имя банды')}\nНапиши в чат, как будут звать твоих людей "
                      f"(до {NAME_MAX} знаков).", [[Button(texts.BACK, "open")]], awaiting_text="army_name", name="army")
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "war":
            return war_screen(c, ch, year)
        if action == "go" and arg:
            r = await svc.war(s, c, ch, random.Random(), year, tz, arg)
            if not isinstance(r, str):
                from app.bot.handlers.combat import step_screen

                return step_screen(c, ch, r)  # type: ignore[arg-type]
            note = BLOCK.get(r, r)
        if action == "street":
            blocked, out, chk = await svc.street(s, c, ch, random.Random(), year, tz)
            note = BLOCK.get(blocked, blocked) if blocked else "\n".join(
                ([views.check_line(chk)] if chk else []) + [texts.esc(x) for x in out])
    return army_screen(c, ch, note)


@on_text("army_name")
async def army_name_text(ctx: Ctx, text: str) -> Screen | None:
    name = " ".join((text or "").split())[:NAME_MAX].strip("«»\"' ")
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if not name:
            return army_screen(ctx.content, ch, "Нужно имя.")
        rec = eng.rec(ch.state)
        rec["name"] = name
        eng.save(ch.state, rec)
        await game.save_active(s, ch, ctx.ui.settings.timezone)
    return army_screen(ctx.content, ch, f"Теперь твои люди — «{texts.esc(name)}».")
