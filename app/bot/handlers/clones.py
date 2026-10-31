"""The window «clones» (owner request 26.10, docs/PLAN_EQUILIBRIUM.md part 3): the clone corp's premium clinic for the
rich, and for those who know the truth about its Ark — the guard shifts, the leaks and the storm."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.db import session as db
from app.engine import injury
from app.services import clones as svc
from app.services import game


async def clones_screen(ctx: Ctx, note: str | None = None) -> Screen:
    content = ctx.content
    c = svc.cfg(content)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        rec = await svc.corp(s)
    st = ch.state
    knows = bool(st.flags.get("clone_known") or st.flags.get("clone_met"))
    rows: list[list[Button]] = []
    if not svc.alive(rec):
        lines = [style.header("event", "Клиника клонов закрыта"),
                 f"«{svc.name_of(rec)}» пала, Ковчег открыт. Новая корпорация с похожими услугами придёт: спрос "
                 "никуда не делся."]
    else:
        lines = [style.header("event", f"«{svc.name_of(rec)}»: индивидуальные биологические решения"),
                 "Клиника премиум-класса. Новая рука из твоей собственной плоти. Новое тело, если старое подведёт."]
        if knows:
            lines.append("<i>Ты знаешь, откуда берётся плоть: из «Ковчега», где клоны богатых ждут лотерею на Остров. "
                         "Острова нет.</i>")
            lines.append(f"Сколько знает город: {style.bar(int(rec.get('expose', 0)), c.expose_fall)} "
                         f"{rec.get('expose', 0)}/{c.expose_fall}. На вершине корпорация падёт.")
        lines += ["", f"• Вырастить утраченную часть тела: {views.money(svc.part_price(content, ch))}.",
                  f"• Клон-страховка: {views.money(svc.insurance_price(content, ch))}. Если умрёшь, а мозг цел, "
                  "Травма Тим перенесёт его в клона (без хрома, кроме чипов в голове). Корпорация падёт — "
                  "страховка сгорит."]
        if st.flags.get(svc.FLAG_READY) is not None:
            lines.append("🧬 Твой клон растёт в Ковчеге.")
        for part in injury.lost(st):
            rows.append([Button(f"Вырастить: {injury.part_name(part)}"[:28], "grow", part)])
        if st.flags.get(svc.FLAG_READY) is None:
            rows.append([Button(f"Клон-страховка · {views.money(svc.insurance_price(content, ch))}"[:28], "insure")])
        if knows:
            lines += ["", "<b>Правда о Ковчеге</b>",
                      f"• Смена в охране ({c.guard_ap} ОД, бой): беглецов возвращают, город знает меньше, оплата.",
                      "• Слить файлы (1 ОД, ИНТ + нетраннинг): город знает больше.",
                      f"• Штурм Ковчега ({c.storm_ap} ОД, тяжёлый бой): город узнаёт сразу много, клоны выходят."]
            rows.append([Button(f"Смена в охране · {c.guard_ap} ОД", "guard")])
            rows.append([Button("Слить файлы · 1 ОД", "leak"), Button(f"Штурм · {c.storm_ap} ОД", "storm", style="danger")])
    if note:
        lines += ["", note]
    rows.append([Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="clones")


@on_screen("clones")
async def clones_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    note = None
    tz = ctx.ui.settings.timezone
    rng = random.Random()
    if action in ("grow", "insure", "guard", "storm", "leak"):
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, ctx.content)
            if action == "grow" and arg in injury.PARTS:
                note = await svc.grow(s, ctx.content, ch, rng, year, arg)
            elif action == "insure":
                note = await svc.insure(s, ctx.content, ch, rng, year)
            elif action == "leak":
                note = await svc.leak(s, ctx.content, ch, rng, year, tz, ctx.ui)
            elif action in ("guard", "storm"):
                r = await (svc.guard if action == "guard" else svc.storm)(s, ctx.content, ch, rng, year, tz)
                if not isinstance(r, str):
                    from app.bot.handlers.combat import step_screen

                    return step_screen(ctx.content, ch, r)
                note = r
            await game.save_active(s, ch, tz)
    return await clones_screen(ctx, note)


async def open_clones(ctx: Ctx) -> Screen | None:
    return await goto(ctx, "clones", "open")
