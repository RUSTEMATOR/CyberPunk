"""Calling help into a fight (owner request 24.10): the combat window -> «Подкрепление»."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui.callbacks import Ctx, on_screen
from app.bot.ui.screen import Button, Screen
from app.db import session as db
from app.services import combat as combat_svc
from app.services import game
from app.services import reinforce as svc

BLOCKED = {"over": "Бой уже закончен.", "used": "Помощь в этом бою уже вызвана.", "solo": "Здесь помощи не будет.",
           "none": "Такой помощи нет.", "eddies": "Не хватает эдди.", "gone": "Этот игрок сейчас не может приехать."}


async def reinf_screen(ctx: Ctx, note: str | None = None, pick: int | None = None) -> Screen:
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        cs = combat_svc.state_of(ch)
        if cs is None:
            return await menu_screen(ctx)
        people = await svc.helpers(s, ch)
    cfg = svc.cfg(c)
    lines = ["📞 <b>Подкрепление</b>", f"Эдди: {views.money(ch.state.eddies)} · ход {cs.tick}", ""]
    if note:
        lines += [note, ""]
    rows: list[list[Button]] = []
    rf = cs.ctx.get("reinf")
    if rf:
        lines.append(f"Уже в пути: {texts.esc(str(rf.get('name', '')))}, приедет на ходу {rf.get('at')}." if not rf.get("done")
                     else "Помощь в этом бою уже была.")
    elif pick is not None:
        name = dict(people).get(pick, "?")
        lines.append(f"Сколько предложить {texts.esc(name)}? Деньги отложатся сейчас и уйдут, когда помощь приедет. "
                     f"Ехать {cfg.player_wait} х. Не ответит до конца боя — вернутся.")
        rows += [[Button(views.money(p), "pp", f"{pick}:{p}") for p in cfg.player_prices[i:i + 2]]
                 for i in range(0, len(cfg.player_prices), 2)]
    else:
        lines.append("Один вызов на бой. Пока помощь едет, держись сам.")
        for key, n in cfg.npc.items():
            if n.requires_flag and not ch.state.flags.get(n.requires_flag):
                continue
            price = svc.npc_price(c, cs, key)
            lines += ["", f"<b>{texts.esc(n.name)}</b> · {views.money(price)} · через {n.wait} х. · бойцов {n.count}",
                      f"<i>{texts.esc(n.text)}</i>"]
            rows.append([Button(f"{n.name} · {views.money(price)}"[:30], "npc", key)])
        if people:
            lines += ["", "<b>Позвать игрока</b>: цену назначаешь ты, его персонаж встанет рядом, когда приедет."]
            rows += [[Button(f"Позвать: {name.split()[0]}"[:24], "pl", str(tg))] for tg, name in people[:6]]
    rows.append([Button("Назад в бой", "back")])
    return Screen("\n".join(lines), rows, name="reinf")


@on_screen("reinf")
async def reinf_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.bot.handlers.combat import combat_resume_screen
    from app.bot.handlers.players import _ping_target

    c = ctx.content
    if action == "pl" and arg and arg.lstrip("-").isdigit():
        return await reinf_screen(ctx, pick=int(arg))
    if action not in ("npc", "pp") or not arg:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
        return await combat_resume_screen(ctx, ch) if ch is not None else await menu_screen(ctx)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        cs = combat_svc.state_of(ch)
        if cs is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        rng = random.Random()
        to_tg = 0
        if action == "npc":
            r = svc.call_npc(c, ch, cs, arg, rng, year)
        else:
            tg, _, price = arg.partition(":")
            to_tg = int(tg)
            r = await svc.call_player(s, c, ch, cs, to_tg, int(price) if price.isdigit() else 0, rng, year)
        if not r.blocked:
            combat_svc._store(ch, cs)  # noqa: SLF001
            await game.save_active(s, ch, ctx.ui.settings.timezone)
    if r.blocked:
        return await reinf_screen(ctx, BLOCKED.get(r.blocked, r.blocked))
    if to_tg:
        await _ping_target(ctx, to_tg, "reinf")
    scr = await combat_resume_screen(ctx, ch)
    return Screen(texts.esc(r.text) + "\n\n" + scr.text, scr.keyboard, name=scr.name, state=scr.state)
