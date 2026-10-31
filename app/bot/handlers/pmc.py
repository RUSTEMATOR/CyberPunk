"""The own PMC in a conflict of two megacorps (owner request 25.10, docs/PLAN_ELITE.md part 4):
«Работа» → «Контракты элиты» → «ЧВК и конфликт корпораций»."""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import army as army_eng
from app.services import game
from app.services import pmc as svc
from app.services.game import ActiveCharacter

BLOCK = {"have": "ЧВК уже зарегистрирована.", "army": "Нужна своя армия от 15 бойцов («Криминал» → «Мои бойцы»).",
         "eddies": "Не хватает денег на лицензию.", "nopmc": "Сначала зарегистрируй ЧВК.", "gone": "Такой стороны нет.",
         "signed": "Контракт в этом конфликте уже подписан.", "nosign": "Сначала подпиши контракт с одной из сторон.",
         "jail": "Из камеры не выйти.", "busy": "Сначала разберись с тем, что уже ждёт.",
         "today": "На сегодня сражений хватит: не больше двух в день.", "ap": "Не хватает ОД."}


async def section(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int):  # noqa: ANN201
    """The part of «Контракты элиты» about the PMC: a line and the button to its screen."""
    w = await svc.conflict(session, content, year)
    a, b = content.corps[w["a"]].name, content.corps[w["b"]].name
    lines = ["", "<b>Своя ЧВК и конфликт корпораций</b>",
             f"Сейчас воюют {texts.esc(a)} и {texts.esc(b)}. Фронт: {texts.esc(svc.front_word(content, w))}."]
    return lines, [[Button("ЧВК и конфликт", "pmc", style="danger")]]


def pmc_screen(content: Content, ch: ActiveCharacter, w: dict, ladder: int, note: str | None = None) -> Screen:
    c = svc.cfg(content)
    a, b = content.corps[w["a"]].name, content.corps[w["b"]].name
    n = len(army_eng.soldiers(ch.state))
    lines = [style.header("🏢", "ЧВК и конфликт корпораций"),
             f"Конфликт №{w['n']}: <b>{texts.esc(a)}</b> против <b>{texts.esc(b)}</b>. "
             f"Фронт: {texts.esc(svc.front_word(content, w))}. Свои силы корпораций сдвигают его каждый день.",
             f"ЧВК подписывает контракт с одной стороной: содержание {views.money(c.retainer_per_fighter)} за бойца "
             f"каждый прожитый месяц, {views.money(c.battle_pay * max(1, ladder))} за выигранное сражение, премия "
             f"{views.money(c.win_bonus)} за каждую победу, когда сторона выиграет конфликт.",
             f"Сражение ({c.battle_ap} ОД, до {c.battles_per_day} в день) — элитный бой в {c.waves} волны, твои бойцы рядом."]
    rows: list[list[Button]] = []
    r = svc.mine(ch)
    pm = (w.get("pmcs") or {})
    if pm:
        lines += ["", "ЧВК в конфликте: " + "; ".join(
            f"{texts.esc(str(x.get('name')))} ({texts.esc(a if x.get('side') == 'a' else b)}, побед {x.get('won', 0)})"
            for x in pm.values())]
    if r is None:
        lines += ["", f"Своей ЧВК нет. Регистрация: армия от {c.need_army} бойцов (у тебя {n}) и лицензия "
                      f"{views.money(c.license)} эдди."]
        if n >= c.need_army:
            rows.append([Button(f"Лицензия · {views.money(c.license)}", "reg", style="success")])
    else:
        side = svc.signed_side(ch, w)
        lines += ["", f"Твоя ЧВК: <b>{texts.esc(str(r.get('name')))}</b>, бойцов {n}."]
        if side:
            lines.append(f"Контракт: на стороне {texts.esc(a if side == 'a' else b)}.")
            rows.append([Button(f"В сражение · {c.battle_ap} ОД", "battle", style="danger")])
        else:
            rows.append([Button(f"За {a}"[:24], "sign", "a"), Button(f"За {b}"[:24], "sign", "b")])
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="pmc")


@on_screen("pmc")
async def pmc_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "elitejob", "open")
    c = ctx.content
    tz = ctx.ui.settings.timezone
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        rng = random.Random()
        if action == "reg":
            why = await svc.register(s, c, ch, rng, year, tz)
            note = BLOCK.get(why, why) if why else "ЧВК зарегистрирована. Теперь выбери сторону."
        elif action == "sign" and arg:
            why = await svc.sign(s, c, ch, year, tz, arg)
            note = BLOCK.get(why, why) if why else "Контракт подписан. Содержание пойдёт каждый прожитый месяц."
        elif action == "battle":
            r = await svc.battle(s, c, ch, rng, year, tz)
            if isinstance(r, str):
                note = BLOCK.get(r, r)
            else:
                from app.bot.handlers.combat import step_screen

                return step_screen(c, ch, r)  # type: ignore[arg-type]
        w = await svc.conflict(s, c, year)
        from app.services import corpraid

        lv = await corpraid.ladder(s, ch)
    return pmc_screen(c, ch, w, lv, note)
