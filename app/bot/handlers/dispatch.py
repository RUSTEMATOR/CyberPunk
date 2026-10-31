"""Send the fighters out without the character (owner request 25.10): «Криминал» → «Мои бойцы» → «Отправить без тебя»."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import army as army_eng
from app.engine import companion as comp
from app.services import dispatch as svc
from app.services import game
from app.services.game import ActiveCharacter

BLOCK = {"gone": "Такого дела нет.", "nobody": "Отправлять некого: ни своих бойцов, ни нанятых.", "jail": "Из камеры приказы не отдают.",
         "today": "На сегодня хватит: не больше трёх вылазок в день.", "nopmc": "Нужен контракт ЧВК в конфликте корпораций.",
         "ap": "Не хватает ОД."}


def list_screen(content: Content, ch: ActiveCharacter, tz: str, note: str | None = None) -> Screen:
    c = svc.cfg(content)
    n = len(army_eng.soldiers(ch.state))
    sq = comp.active_members(ch.state)
    lines = [style.header("crime", "Отправить без тебя"),
             f"Твои люди идут на дело сами: свои бойцы ({n}) и нанятые горожане ({len(sq)}). Исход решает сила группы "
             f"против сложности дела; павшие погибают навсегда, выжившие растут в силе. {c.ap} ОД, до {c.per_day} вылазок "
             f"в день (сегодня {svc.done_today(ch, tz)}).", ""]
    rows: list[list[Button]] = []
    for m in c.missions:
        lo, hi = m.reward
        lines.append(f"• <b>{texts.esc(m.name)}</b> — сложность {m.difficulty}, {views.money(lo)}–{views.money(hi)} эдди. "
                     f"{texts.esc(m.text)}")
        rows.append([Button(m.name[:24], "m", m.key)])
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="dispatch")


def size_screen(content: Content, ch: ActiveCharacter, key: str) -> Screen:
    m = svc.mission(content, key)
    lines = [style.header("crime", m.name), texts.esc(m.text), "", "Кого отправить:"]
    rows: list[list[Button]] = []
    seen: set[tuple[int, int]] = set()
    for size, label in svc.SIZES.items():
        soldiers, squad = svc.group(content, ch, size)
        if (len(soldiers), len(squad)) in seen or (not soldiers and not squad):
            continue
        seen.add((len(soldiers), len(squad)))
        pw = svc.power(content, soldiers, squad)
        p = round(svc.chance(pw, m.difficulty) * 100)
        if not soldiers:
            label = "Наёмники"
        lines.append(f"• {label}: {svc.who_text(soldiers, squad)}, сила {pw}, шанс {p}%")
        rows.append([Button(f"{label} · {p}%"[:24], "go", f"{key}:{size}", style="danger")])
    rows.append([Button(texts.BACK, "open"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="dispatch")


@on_screen("dispatch")
async def dispatch_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "army", "open")
    c = ctx.content
    tz = ctx.ui.settings.timezone
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "m" and arg and svc.mission(c, arg):
            return size_screen(c, ch, arg)
        if action == "go" and arg and ":" in arg:
            key, _, size = arg.partition(":")
            r = await svc.send(s, c, ch, random.Random(), year, tz, key, size)
            note = BLOCK.get(r.blocked, r.blocked) if r.blocked else "\n".join(texts.esc(x) for x in r.lines)
    return list_screen(c, ch, tz, note)
