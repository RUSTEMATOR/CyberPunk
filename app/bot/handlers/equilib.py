"""The window «equil» (owner request 26.10, docs/PLAN_EQUILIBRIUM.md part 1): the three forces of the city, the state
of the balance, what it costs everybody, and the player's ways to set it right."""

from __future__ import annotations

import random

from app.bot import texts
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.db import session as db
from app.engine import equilib as eng
from app.services import equilib as svc
from app.services import game


async def equil_screen(ctx: Ctx, note: str | None = None) -> Screen:
    content = ctx.content
    c = svc.cfg(content)
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        st = await svc.state(s, content)
    sh = st["shares"]
    t = int(st["tier"])
    lines = [style.header("war", f"Равновесие города: {eng.TIER_NAMES[min(3, t)]}"),
             "Банды держат улицы, Порядок (NCPD и корпорации) держит законы и деньги, Агентство «Полночь» — "
             "независимые детективы и эджраннеры — следит, чтобы никто не подмял город под себя. Пока три силы "
             "равны, город живёт. Перекос бьёт по всем.", ""]
    for f in eng.FORCES:
        pct = round(sh[f] * 100)
        lines.append(f"{eng.NAMES[f]}: {style.bar(pct, 100)} {pct}%" + (" · сильнее всех" if f == st["dom"] and t else "")
                     + (" · слабее всех" if f == st["weak"] and t else ""))
    lines += ["", "<b>Что сейчас чувствует город</b>"] + svc.consequences(content, st)
    left = max(0, c.per_day - svc.done_today(ch, tz))
    lines += ["", "<b>Что можешь сделать ты</b>",
              f"• Дело Агентства: {c.case_ap} ОД, ИНТ + убеждение против {c.case_dv}. Агентство сильнее, немного денег.",
              f"• Посредничество: {c.mediate_ap} ОД, ХЛАД + убеждение против {c.mediate_dv}. Самая сильная сила "
              "уступает понемногу во всех районах.",
              "• Зачистка банд и удары по корпорациям — «Улицы: банды и корпы».",
              f"Сегодня осталось действий: {left}."]
    if note:
        lines += ["", note]
    rows = [[Button(f"Дело Агентства · {c.case_ap} ОД", "case", style="success")],
            [Button(f"Посредничество · {c.mediate_ap} ОД", "mediate")],
            [Button("Улицы: банды и корпы", "ctrl")],
            [Button(texts.BACK, "back"), Button(texts.MENU, "menu")]]
    return Screen("\n".join(lines), rows, name="equil")


@on_screen("equil")
async def equil_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "city", "open")
    if action == "ctrl":
        return await goto(ctx, "ctrl", "open")
    note = None
    if action in ("case", "mediate"):
        tz = ctx.ui.settings.timezone
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, ctx.content)
            fn = svc.case if action == "case" else svc.mediate
            note = await fn(s, ctx.content, ch, random.Random(), year, tz)
            await game.save_active(s, ch, tz)
    return await equil_screen(ctx, note)
