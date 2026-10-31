"""Contracts of the elite (owner request 25.10, docs/PLAN_ELITE.md parts 3-4): «Работа» → «Контракты элиты»:
the bodyguard of a corporate head, the own PMC in a corporate war."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.services import escort, game
from app.services.game import ActiveCharacter

BLOCK = {"busy": "Сначала доведи до конца нынешний контракт.", "small": "Корпорации берут в охрану только тех, о ком "
         "говорит улица: нужна вторая ступень вызовов.", "gone": "Этого предложения уже нет.", "none": "Сейчас покушений нет.",
         "jail": "Из камеры не выйти.", "busy_ev": "Сначала разберись с тем, что уже ждёт.", "ap": "Не хватает ОД."}


def elite_screen(content: Content, ch: ActiveCharacter, year: int, lv: int, ok: bool, note: str | None = None,
                 extra: tuple[list[str], list[list[Button]]] | None = None) -> Screen:
    c = escort.cfg(content)
    lines = [style.header("🕴", "Контракты элиты"),
             "Работа для тех, кого город уже знает в лицо. Платят как корпоратам, но каждое дело — элитный бой."]
    rows: list[list[Button]] = []
    lines += ["", "<b>Телохранитель главы корпорации</b>",
              f"Контракт на {c.months} прожитых месяца: аванс при подписи, оплата каждый месяц, премия в конце. "
              "Каждый месяц покушение — бой «защитить человека». Охраняемый погиб или покушение прошло без тебя — "
              "контракт сорван, корпорация это запомнит."]
    r = escort.current(ch)
    if r is not None:
        corp = content.corps.get(str(r["corp"]))
        lines.append(f"Сейчас: {texts.esc(str(r['title']))} {texts.esc(corp.name if corp else '')}, "
                     f"<b>{texts.esc(str(r['vip']))}</b>. Оплата {views.money(int(r['monthly']))} в месяц, "
                     f"осталось месяцев: {r.get('left')}. Отбито покушений: {r.get('won', 0)}.")
        if r.get("due"):
            lines.append("<b>Покушение готовится прямо сейчас.</b> Без тебя оно пройдёт в конце месяца.")
            rows.append([Button(f"В бой: покушение · {c.fight_ap} ОД", "fight", style="danger")])
        else:
            lines.append("Этот месяц спокоен. Следующее покушение — после «Прожить месяц».")
    elif not ok:
        lines.append("<i>" + BLOCK["small"] + "</i>")
    else:
        for o in escort.offers(content, ch, year, lv):
            corp = content.corps[o["corp"]]
            total = o["monthly"] * c.advance_pct // 100 + o["monthly"] * c.months + o["monthly"] * c.bonus_pct // 100
            lines.append(f"• {texts.esc(corp.name)}: {texts.esc(o['title'])} {texts.esc(o['vip'])} — "
                         f"{views.money(o['monthly'])} в месяц, всего до {views.money(total)}")
            rows.append([Button(f"Охранять: {corp.name}"[:24], "sign", o["corp"])])
    if extra is not None:
        lines += extra[0]
        rows += extra[1]
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="elitejob")


@on_screen("elitejob")
async def elitejob_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "work", "open")
    if action == "pmc":  # docs/PLAN_ELITE.md part 4
        return await goto(ctx, "pmc", "open")
    c = ctx.content
    tz = ctx.ui.settings.timezone
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        rng = random.Random()
        if action == "sign" and arg:
            why = await escort.sign(s, c, ch, rng, year, tz, arg)
            note = BLOCK.get(why, why) if why else "Контракт подписан, аванс на счету. Первое покушение уже готовят."
        elif action == "fight":
            r = await escort.fight(s, c, ch, rng, year, tz)
            if isinstance(r, str):
                note = BLOCK.get("busy_ev" if r == "busy" else r, r)
            else:
                from app.bot.handlers.combat import step_screen

                return step_screen(c, ch, r)  # type: ignore[arg-type]
        lv = await escort.ladder(s, ch)
        ok = await escort.eligible(s, c, ch)
        from app.bot.handlers import pmc as pmc_h  # docs/PLAN_ELITE.md part 4

        extra = await pmc_h.section(s, c, ch, year)
    return elite_screen(c, ch, year, lv, ok, note, extra)
