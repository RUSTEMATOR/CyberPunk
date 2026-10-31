"""The window of the enemies (owner request 25.10): «Криминал» → «Враги»."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import pressure as pe
from app.services import enemies as svc
from app.services import game
from app.services.game import ActiveCharacter

BLOCK = {"jail": "Из камеры с врагами не разобраться.", "busy": "Сначала разберись с тем, что уже ждёт.",
         "none": "С этим сейчас всё чисто.", "eddies": "Не хватает эдди.", "ap": "Не хватает ОД."}


def foes_screen(content: Content, ch: ActiveCharacter, year: int, bounty: int, note: str | None = None) -> Screen:
    c = svc.cfg(content)
    st = ch.state
    pr = svc.prices(content, ch, bounty)
    fixer = game.district_fixer(content, st.district, year)
    fname = texts.esc(fixer.name) if fixer else "Фиксер района"
    lines = [style.header("war", "Враги"),
             f"Эдди: {views.money(st.eddies)} · ОД: {st.ap}", "",
             f"Убрать врага можно деньгами (через фиксера: {fname}) или самому.", ""]
    rows: list[list[Button]] = []
    nem = pe.nemesis(st)
    if nem:
        lvl = int(nem.get("lvl", 0) or 0)
        lines.append(f"🩸 <b>Заклятый враг: {texts.esc(nem['name'])}</b> (ур. {lvl + 1}).")
        lines.append(f"  Заказ через фиксера: {views.money(pr['contract'])}, удача {int(c.contract_chance * 100)}%; "
                     "провал — враг злее, деньги не вернуть.")
        lines.append(f"  Выследить самому: {c.hunt_ap} ОД, проверка ИНТ + убеждение (сложность {c.hunt_dv + 2 * lvl}), "
                     "потом бой сразу. Провал проверки — бой из засады.")
        rows.append([Button(f"Заказать · {views.money(pr['contract'])}"[:24], "m", "contract"),
                     Button("Выследить", "h", "hunt", style="danger")])
    if pe.enmity(st):
        lines.append(f"🗡 <b>Враги: {pe.enmity(st)}</b> — обиженные, которые мстят. Фиксер уладит до "
                     f"{min(c.settle_levels, pe.enmity(st))} за {views.money(pr['settle'])}.")
        rows.append([Button(f"Уладить · {views.money(pr['settle'])}"[:24], "m", "settle")])
    if pe.hook(st):
        lines.append(f"🪝 <b>Крючок: {pe.hook(st)}</b> — кто-то держит тебя услугой. Выкупить: {views.money(pr['hook'])}; "
                     f"надавить самому: {c.lean_ap} ОД, ХЛАД + убеждение {c.lean_dv}, провал — врагов +1.")
        rows.append([Button(f"Выкупить · {views.money(pr['hook'])}"[:24], "m", "hook"), Button("Надавить", "h", "lean")])
    if bounty:
        lines.append(f"💀 <b>Награда за твою голову: {views.money(bounty)}.</b> Снять через фиксера: {views.money(pr['bounty'])}.")
        rows.append([Button(f"Снять награду · {views.money(pr['bounty'])}"[:24], "m", "bounty")])
    foes = svc.hostile(content, ch, year)
    if foes:
        lines.append("🏴 <b>Враждебные фракции:</b> " + ", ".join(f"{texts.esc(n)} ({r})" for _, n, r in foes[:5]) + ".")
        lines.append(f"  Перемирие через фиксера: отношение +{c.truce_gain}. Вендетта самому: {c.vendetta_ap} ОД, бой "
                     f"опасности {c.vendetta_danger} с самыми злыми; победа — врагов −{c.vendetta_enmity}, перемирие страха.")
        for k, n, r in foes[:3]:
            rows.append([Button(f"Мир: {n} · {views.money(svc.truce_price(content, r))}"[:24], "t", k)])
        rows.append([Button(f"Вендетта: {foes[0][1]}"[:24], "h", "vendetta", style="danger")])
    if len(lines) == 5:
        lines.append("Врагов нет. Пока.")
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="foes")


@on_screen("foes")
async def foes_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.services import bounties

    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "crime")
    c = ctx.content
    tz = ctx.ui.settings.timezone
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        rng = random.Random()
        if action in ("m", "t") and arg:
            why, line = await svc.by_money(s, c, ch, rng, year, tz, "truce" if action == "t" else arg,
                                           arg if action == "t" else None)
            note = BLOCK.get(why, why) if why else texts.esc(line)
        elif action == "h" and arg:
            r = await svc.by_hand(s, c, ch, rng, year, tz, arg)
            if isinstance(r, str):
                note = BLOCK.get(r, r)
            elif isinstance(r, tuple):
                _, chk, line = r
                note = "\n".join([views.check_line(chk), texts.esc(line)])
            else:
                from app.bot.handlers.combat import step_screen

                return step_screen(c, ch, r)  # type: ignore[arg-type]
        bounty = await bounties.on(s, ch.id)
    return foes_screen(c, ch, year, bounty, note)
