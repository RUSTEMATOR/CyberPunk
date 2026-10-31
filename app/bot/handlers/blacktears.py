"""The window «btears» (owner request 26.10, docs/PLAN_BLACKTEARS.md): «🖤 Шёпот Raven» — the contract of «Чёрные
слёзы» waiting today, the reputation with its gifts and prices, the last contracts."""

from __future__ import annotations

import random
import time

from app.bot import texts
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, on_screen
from app.bot.ui.screen import Button, Screen
from app.db import session as db
from app.services import blacktears as svc
from app.services import game


async def bt_screen(ctx: Ctx, note: str | None = None) -> Screen:
    content = ctx.content
    c = svc.cfg(content)
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        offer = await svc.offer_of(s, ctx.user_id, tz)
    r = svc.rep(ch)
    t = svc.tier(content, r)
    nxt = next((x for x in c.tiers if x > r), None)
    lines = [style.header("event", "Шёпот Raven: «Чёрные слёзы»"),
             f"<i>{texts.esc(svc._data().get('intro', ''))}</i>", "",  # noqa: SLF001
             f"Репутация {style.bar(r, 100)} {r}/100 · <b>{svc.tier_name(content, r)}</b>"
             + (f" (следующий круг с {nxt})" if nxt else "")]
    rows: list[list[Button]] = []
    if offer is not None:
        k = svc.contract(str(offer["id"])) or {}
        lines += ["", "<b>Заказ на сегодня</b>", texts.esc(str(k.get("text", "")))]
        chk = k.get("check")
        if chk:
            lines.append(f"<i>Тут понадобится {chk['stat']}" + (f" и {chk['skill']}" if chk.get("skill") else "")
                         + ": чисто сделанный заказ чаще кончается хорошо.</i>")
        lines.append("Что изменит согласие или отказ, узнаешь потом. Может, к лучшему. Может, нет.")
        rows.append([Button(f"Сделать · {c.accept_ap} ОД", "yes", style="danger"), Button("Отказаться", "no")])
    else:
        lines += ["", "Заказа сейчас нет. Raven сам найдёт тебя днём, когда Люси что-то понадобится."]
    # owner request 26.10: what a circle gives and costs is known only once the character reaches it
    gifts = [(1, f"хорошие последствия чаще"),
             (2, f"раз в сутки Слёзы заметают следы (розыск −{c.sweep_heat})"),
             (3, f"деньги в последствиях ×{c.tier3_pay_mult:g}, раз в сутки доля Слёз"),
             (4, "хорошие последствия ещё чаще")]
    prices = [(2, "заказ иногда стоит человечности, а отказ почти всегда кончается плохо"),
              (3, "каждый заказ — розыск +1")]
    got = [g for n, g in gifts if t >= n]
    paid = [p for n, p in prices if t >= n]
    if got or paid:
        lines += ["", "<b>Твой круг</b>"] + [f"• {g};" for g in got]
        if paid:
            lines += ["<b>Чем платишь</b>"] + [f"• {p};" for p in paid]
    if nxt:
        lines.append("<i>Что даст следующий круг, узнаешь, когда дойдёшь.</i>")
    if t >= 2:
        rows.append([Button(f"Замести следы · розыск −{c.sweep_heat}", "sweep")])
    if t >= 3:
        rows.append([Button("Забрать долю Слёз", "share", style="success")])
    hist = list(ch.state.flags.get(svc.FLAG_HIST) or [])[-3:]
    if hist:
        lines += ["", "<b>Последние заказы</b>"]
        for h in reversed(hist):
            k = svc.contract(str(h.get("id"))) or {}
            mins = int((time.time() - float(h.get("at", 0))) // 60)
            lines.append(f"• {'сделано' if h.get('a') else 'отказ'}: {texts.esc(str(k.get('text', ''))[:70])}… "
                         f"({mins} мин назад)")
    if note:
        lines += ["", note]
    rows.append([Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="btears")


@on_screen("btears")
async def bt_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    note = None
    tz = ctx.ui.settings.timezone
    rng = random.Random()
    if action in ("yes", "no", "sweep", "share"):
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, ctx.content)
            if action in ("yes", "no"):
                note = "\n".join(await svc.answer(s, ctx.content, ch, rng, year, tz, action == "yes"))
            else:
                fn = svc.sweep if action == "sweep" else svc.share
                note = fn(ctx.content, ch, rng, year, tz)
                await game.save_active(s, ch, tz)
    return await bt_screen(ctx, note)


# -- Raven comes for the one who makes him trouble (owner answers 26.10) -----------------------------------------------


def hunt_screen(ctx: Ctx, ch, note: str | None = None) -> Screen:  # noqa: ANN001
    c = svc.cfg(ctx.content)
    lines = [style.header("war", "Raven Tearz пришёл за тобой"),
             "Дождь, пустая улица, фонари гаснут один за другим. Он стоит под последним: кепка, худи, катана на плече. "
             "Глаза горят красным — Люси тоже здесь. «Ты делаешь мне слишком много проблем, чумба. Давай закончим это "
             "быстро».",
             "",
             "Raven — босс, которого нельзя убить: побитый, он отступает. Проигрыш не убивает, но он заберёт "
             f"{c.boss_take_pct}% эдди.",
             f"• Уговорить: ХЛАД + убеждение против {c.boss_talk_dv}; провал — бой.",
             f"• Откупиться: {c.boss_price} эдди.",
             "• Драться."]
    if note:
        lines += ["", note]
    rows = [[Button("Драться", "fight", style="danger")], [Button("Уговорить", "talk")],
            [Button(f"Откупиться · {c.boss_price}", "pay")]]
    return Screen("\n".join(lines), rows, name="ravenhunt")


@on_screen("ravenhunt")
async def hunt_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    tz = ctx.ui.settings.timezone
    rng = random.Random()
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None or not ch.state.flags.get(svc.FLAG_HUNT):
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        if action == "pay":
            line = svc.boss_pay(ctx.content, ch, rng, year)
            if line is None:
                return hunt_screen(ctx, ch, "Не хватает эдди. «Пустой кошелёк, а туда же».")
            await game.save_active(s, ch, tz)
            return Screen(line, [[Button(texts.MENU, "menu")]], name="btears")
        if action == "talk":
            ok, line = svc.boss_talk(ctx.content, ch, rng)
            if ok:
                await game.save_active(s, ch, tz)
                return Screen(line, [[Button(texts.MENU, "menu")]], name="btears")
            step = await svc.boss_fight(s, ctx.content, ch, rng, year, tz)
            from app.bot.handlers.combat import step_screen

            scr = step_screen(ctx.content, ch, step)
            scr.text = line + "\n\n" + scr.text
            return scr
        if action == "fight":
            step = await svc.boss_fight(s, ctx.content, ch, rng, year, tz)
            from app.bot.handlers.combat import step_screen

            return step_screen(ctx.content, ch, step)
    return hunt_screen(ctx, ch)
