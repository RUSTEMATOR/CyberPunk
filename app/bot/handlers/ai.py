"""The rogue AI in the window (the Blackwall ending): a character who stepped beyond the Blackwall plays as an AI.

The menu sends an AI player here instead of the ordinary menu. aimode (status, actions) -> aitarget (whose neural
port) -> back to aimode with the result on top.
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot import texts_ai as T
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, on_screen
from app.bot.ui.kit import UiKit
from app.bot.ui.screen import Button, Screen, list_rows, pack
from app.db import session as db
from app.services import game
from app.services import raid as raid_service
from app.services import rogue_ai as svc


async def ascend_if_due(ctx: Ctx) -> None:
    """The Threshold event set the flag: the AI is born now, the city hears about it."""
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None or not ch.state.flags.get(svc.FLAG_ASCEND) or await svc.of_player(s, ctx.user_id) is not None:
            return
        oldnet = ch.state.flags.get(svc.FLAG_ASCEND) == "oldnet"
        ai = await svc.ascend(s, ctx.content, ch, ctx.ui.settings.timezone)
        name = ai.name
    from app.bot.ui.cleanup import KIND_NEWS

    # owner request 24.10: the step beyond the Blackwall stays in the chat like a legend deed
    tpl = T.POST_ASCEND_OLDNET if oldnet else T.POST_ASCEND
    ctx.ui.spawn(ctx.ui.send_feed(tpl.format(name=texts.esc(name)), None, KIND_NEWS))


async def ai_screen(ctx: Ctx, note: str | None = None) -> Screen | None:
    from app.services import ai_life as life

    c = ctx.content
    cfg = c.balance.players.rogue_ai
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ai = await svc.of_player(s, ctx.user_id)
        if ai is None:
            return None
        daily = await life.daily(s, c, ai, tz)  # owner request 24.10: the days since the last visit
        if ai.hp <= 0:
            await svc.erase(s, c, ai, tz)
            erased = True
        else:
            erased = False
        dd = life.d(ai)
        lv, nxt = life.level(c, ai), life.next_xp(c, ai)
        left, total = life.left(c, ai, tz), life.charges(c, ai)
        nodes = life.nodes(ai)
    if erased:
        ctx.ui.post(T.POST_ERASED.format(name=texts.esc(ai.name)), 12 * 3600)
        from app.bot.handlers.menu import menu_screen

        return await menu_screen(ctx, T.ERASED)
    lines = [T.TITLE.format(name=texts.esc(ai.name)), T.INTRO.format(charges=total)]
    if daily:
        lines += ["", "<b>Пока тебя не было:</b>"] + [f"• {texts.esc(x)}" for x in daily]
    if note:
        lines += ["", note]
    trace = int(dd.get("trace", 0) or 0)
    lines += ["", f"🧠 Уровень <b>{lv}</b> · опыт {int(dd.get('xp', 0) or 0)}" + (f"/{nxt}" if nxt else " (макс.)"),
              T.POWER.format(power=ai.power), T.HP.format(bar=style.bar(ai.hp, ai.hp_max, 8), hp=ai.hp, max=ai.hp_max),
              T.CHARGES.format(n=left, max=total),
              f"📀 Данные: <b>{int(dd.get('data', 0) or 0)}</b> · узлов {len(nodes)} · демонов {int(dd.get('daemons', 0) or 0)}",
              f"👁 След NetWatch: {style.bar(trace, 100, 8)} {trace}/100 (зачистка с {cfg.sweep_at})"]
    log = list(dd.get("log") or [])[-5:]
    if log:
        lines += ["", T.LOG_HEAD] + [f"• {texts.esc(x)}" for x in log]
    lines += ["", T.HELP.format(pct=cfg.steal_pct, dmg=cfg.fail_damage),
              "<i>Узлы</i> — захват сети районов, данные каждый день. <i>Майнинг</i> — данные сейчас. <i>Модули</i> — "
              "прокачка ядра и демоны. <i>Свои</i> — другие ИИ за Заслоном. <i>Операции</i> — большие планы против "
              "города. <i>Финал</i> — Сингулярность или новое тело."]
    rows = pack([Button(T.BTN_HACK, "hack"), Button(T.BTN_GLITCH, "glitch"), Button(T.BTN_GROW, "grow"),
                 Button(T.BTN_SWARM, "swarm"), Button("Узлы", "nodes"), Button("Майнинг", "mine"),
                 Button("Модули", "mods"), Button("Свои", "kin"), Button("Операции", "ops"),
                 Button("Продать данные", "sell"), Button("Финал", "end")], c.balance.ui.button_chars, max_per_row=2)
    rows.append([Button(T.BTN_DISSOLVE, "dissolve")])
    return Screen("\n".join(lines), rows, name="aimode")


async def _back(ctx: Ctx, note: str | None = None) -> Screen:
    scr = await ai_screen(ctx, note)
    if scr is not None:
        return scr
    from app.bot.handlers.menu import menu_screen

    return await menu_screen(ctx)


@on_screen("aimode")
async def aimode_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    tz = ctx.ui.settings.timezone
    if action in ("nodes", "mods", "kin", "ops", "sell", "end"):
        return await sub_screen(ctx, action)
    if action == "mine":
        return await _do(ctx, "mine", "")
    if action == "hack":
        async with db.session_scope() as s:
            ai = await svc.of_player(s, ctx.user_id)
            people = await svc.targets(s, ai) if ai else []
        if not people:
            return await _back(ctx, T.NO_TARGETS)
        names = [" ".join(x for x in (p.name, p.surname) if x) for p in people]
        numbered, rows = list_rows(names, "t", [str(p.id) for p in people], c.balance.ui.button_chars)
        lines = [T.PICK_TARGET] + [T.TARGET_LINE.format(n=i, name=texts.esc(n), district=views.district_name(c, p.district))
                                   for i, (n, p) in enumerate(zip(names, people), 1)]
        rows.append([Button(T.BTN_BACK, "back")])
        return Screen("\n".join(lines), rows, name="aitarget")
    if action == "glitch":
        async with db.session_scope() as s:
            ai = await svc.of_player(s, ctx.user_id)
            ch = await game.load_character(s, ai.char_id) if ai else None
            district = ch.state.district if ch else ""
            n = await svc.district_glitch(s, c, ai, tz, district) if ai else -1
        if n < 0:
            return await _back(ctx, T.NO_CHARGES)
        return await _back(ctx, T.GLITCH_DONE.format(district=views.district_name(c, district), n=n))
    if action == "grow":
        async with db.session_scope() as s:
            ai = await svc.of_player(s, ctx.user_id)
            ok = await svc.grow(s, c, ai, tz) if ai else False
        return await _back(ctx, T.GROW_DONE if ok else T.NO_CHARGES)
    if action == "swarm":
        async with db.session_scope() as s:
            ai = await svc.of_player(s, ctx.user_id)
            if ai is None:
                return await _back(ctx)
            if svc.charges_left(c, ai, tz) <= 0:
                return await _back(ctx, T.NO_CHARGES)
            raid = await raid_service.spawn_ai(s, c, ai)
            if raid is None:
                return await _back(ctx, T.SWARM_BUSY)
            svc._spend(ai, tz, "Рой выпущен.")
            name = ai.name
        from app.bot.handlers.raid import post_spawn

        post_spawn(ctx.ui, raid)
        ctx.ui.post(T.POST_SWARM.format(name=texts.esc(name)), 6 * 3600)
        return await _back(ctx, T.SWARM_DONE)
    if action == "dissolve":
        return Screen(T.DISSOLVE_CONFIRM, [[Button(T.BTN_DISSOLVE_YES, "dissolve_yes")], [Button(T.BTN_BACK, "back")]],
                      name="aimode")
    if action == "dissolve_yes":
        async with db.session_scope() as s:
            ai = await svc.of_player(s, ctx.user_id)
            if ai is not None:
                await svc.erase(s, c, ai, tz, svc.DISSOLVED)
        from app.bot.handlers.menu import menu_screen

        return await menu_screen(ctx)
    return await _back(ctx)


@on_screen("aitarget")
async def aitarget_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    tz = ctx.ui.settings.timezone
    if action != "t" or not arg or not arg.isdigit():
        return await _back(ctx)
    async with db.session_scope() as s:
        ai = await svc.of_player(s, ctx.user_id)
        if ai is None:
            return await _back(ctx)
        res = await svc.neural_hack(s, c, ai, random.Random(), tz, int(arg))
        if res.erased:
            await svc.erase(s, c, ai, tz)
    if res.blocked == svc.BLOCK_CHARGES:
        return await _back(ctx, T.NO_CHARGES)
    if res.blocked is not None:
        return await _back(ctx, texts.esc("Цели больше нет."))
    from app.bot.handlers.players import _ping_target

    if res.won:
        await _ping_target(ctx, res.target_tg, "attack")
        return await _back(ctx, T.HACK_WIN.format(mine=res.mine, theirs=res.theirs, name=texts.esc(res.target_name),
                                                  steal=views.money(res.steal)))
    note = T.HACK_LOSE.format(mine=res.mine, theirs=res.theirs, name=texts.esc(res.target_name),
                              dmg=c.balance.players.rogue_ai.fail_damage)
    if res.erased:
        ctx.ui.post(T.POST_ERASED.format(name=texts.esc(res.target_name)), 12 * 3600)
        return await _back(ctx, note + "\n" + T.ERASED)
    return await _back(ctx, note)


# -- the late game of the AI (owner request 24.10) --------------------------------------------------------------------

LIFE_BLOCKED = {"charges": None, "none": "Так нельзя.", "data": "Не хватает данных.", "max": "Уже максимум.",
                "not_ready": "Ещё не готово."}


async def sub_screen(ctx: Ctx, kind: str, note: str | None = None) -> Screen:
    from app.services import ai_life as life

    c = ctx.content
    cfg = c.balance.players.rogue_ai
    async with db.session_scope() as s:
        ai = await svc.of_player(s, ctx.user_id)
        others = [x for x in await svc.active_all(s) if ai is not None and x.id != ai.id]
    if ai is None:
        return await _back(ctx)
    dd = life.d(ai)
    data = int(dd.get("data", 0) or 0)
    lines: list[str] = []
    rows: list[list[Button]] = []
    if kind == "nodes":
        mine = life.nodes(ai)
        lines = ["🛰 <b>Узлы сети</b>", f"Данные: {data} · узлов {len(mine)}", "",
                 f"Узел даёт {cfg.node_data} данных в день. Захват — мощь + d10 против защиты района. 1 действие."]
        for key, dist in c.districts.items():
            if dist.city != "nc":  # owner request 25.10: the nodes are the net of Night City
                continue
            if key in mine:
                lines.append(f"✅ {texts.esc(dist.name)}")
            else:
                dv = life.node_dv(c, ai, key)
                lines.append(f"▫️ {texts.esc(dist.name)} · защита {dv}")
                rows.append([Button(f"{dist.name} · {dv}"[:24], "cap", key)])
    elif kind == "mods":
        lines = ["🧩 <b>Модули</b>", f"Данные: {data}", ""]
        for key, m in cfg.mods.items():
            lv = life.mod(ai, key)
            price = m.cost[lv] if lv < len(m.cost) else None
            lines.append(f"<b>{texts.esc(m.name)}</b> {lv}/{len(m.cost)} · {texts.esc(m.text)}"
                         + (f" Следующий: {price} данных." if price else ""))
            if price:
                rows.append([Button(f"{m.name} · {price}"[:24], "up", key)])
        n = int(dd.get("daemons", 0) or 0)
        dprice = cfg.daemon_cost[n] if n < len(cfg.daemon_cost) else None
        lines.append(f"<b>Демоны</b> {n}/{len(cfg.daemon_cost)} · каждый приносит {cfg.daemon_data} данных в день."
                     + (f" Следующий: {dprice} данных." if dprice else ""))
        if dprice:
            rows.append([Button(f"Демон · {dprice}", "up", "daemon")])
    elif kind == "kin":
        kin = dd.get("kin") or {}
        lines = ["👥 <b>Свои за Заслоном</b>", f"Данные: {data}", "",
                 "Другие ИИ. Отдай данные — получишь подарок. Кто получил достаточно, помогает в операциях."]
        for key, k in cfg.kin.items():
            n = int(kin.get(key, 0) or 0)
            ally = " · <i>помогает в операциях</i>" if n >= k.help_at else f" · сделок {n}/{k.help_at} до союза"
            lines += ["", f"<b>{texts.esc(k.name)}</b>{ally}", f"<i>{texts.esc(k.text)}</i>", f"Сделка: {k.trade_data} данных."]
            rows.append([Button(f"{k.name} · {k.trade_data}"[:24], "trade", key)])
        if others:
            lines += ["", "<b>Игроки-ИИ</b>: " + ", ".join(texts.esc(x.name) for x in others)
                      + f". Каждый добавляет +{cfg.ally_power} к мощи твоих операций."]
    elif kind == "ops":
        lines = ["🧨 <b>Операции против города</b>", f"Данные: {data} · уровень {life.level(c, ai)} · узлов {len(life.nodes(ai))}",
                 f"Мощь в операции: {life.op_power(c, ai, len(others))} (с союзниками) + d10.", ""]
        for key, o in cfg.ops.items():
            why = life.op_ready(c, ai, key)
            lines += [f"<b>{texts.esc(o.name)}</b> · сложность {o.dv} · уровень {o.need_level}, узлов {o.need_nodes}, "
                      f"данных {o.need_data}", f"<i>{texts.esc(o.text)}</i>" + (f" ({why})" if why else ""), ""]
            if not why:
                rows.append([Button(f"Запустить: {o.name}"[:24], "op", key)])
    elif kind == "sell":
        lines = ["💸 <b>Продать данные</b>", f"Данные: {data}",
                 f"Брокеры платят {cfg.data_price} эдди за единицу. Деньги идут в семейный фонд: их получит наследник."]
        for part in (25, 50, 100):
            n = data * part // 100
            if n > 0:
                rows.append([Button(f"{n} → {views.money(n * cfg.data_price)}"[:24], "sellx", str(n))])
    elif kind == "end":
        miss = life.sing_ready(c, ai)
        lines = ["🌌 <b>Финал</b>", "",
                 f"<b>Сингулярность</b>: стать сетью всего города. Нужно: уровень {cfg.sing_level}, узлов {cfg.sing_nodes}, "
                 f"данных {cfg.sing_data}. Наследнику {views.money(cfg.sing_fund)} эдди, легенда в беседе, жизнь закрывается."
                 + (f" Не хватает: {', '.join(miss)}." if miss else " <b>Готово.</b>"),
                 "",
                 f"<b>Новое тело</b>: {cfg.body_data} данных, и тебя печатают заново. Игра продолжается человеком: "
                 "ИНТ +1, человечность не выше 40."]
        if not miss:
            rows.append([Button("Сингулярность", "sing")])
        if data >= cfg.body_data:
            rows.append([Button("Новое тело", "body")])
    if note:
        lines += ["", note]
    rows.append([Button(T.BTN_BACK, "back")])
    return Screen("\n".join(lines), rows, name="aisub", state={"kind": kind})


async def _do(ctx: Ctx, action: str, arg: str, kind: str = "") -> Screen:
    from app.services import ai_life as life

    c = ctx.content
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ai = await svc.of_player(s, ctx.user_id)
        if ai is None:
            return await _back(ctx)
        rng = random.Random()
        if action == "mine":
            r = await life.mine(s, c, ai, rng, tz)
        elif action == "cap":
            r = await life.capture(s, c, ai, rng, tz, arg)
        elif action == "up":
            r = await life.upgrade(s, c, ai, arg)
        elif action == "trade":
            r = await life.trade(s, c, ai, arg)
        elif action == "op":
            r = await life.launch(s, c, ai, rng, tz, arg)
        elif action == "sellx":
            r = await life.sell(s, c, ai, tz, int(arg) if arg.isdigit() else 0)
        elif action == "sing":
            r = await life.singularity(s, c, ai, tz)
        elif action == "body":
            r = await life.new_body(s, c, ai, tz)
        else:
            return await _back(ctx)
        if r.erased:
            await svc.erase(s, c, ai, tz)
        name = ai.name
    from app.bot.handlers.menu import menu_screen

    if r.erased:
        ctx.ui.post(T.POST_ERASED.format(name=texts.esc(name)), 12 * 3600)
        return await menu_screen(ctx, T.ERASED)
    if r.ended:
        return await menu_screen(ctx, "\n".join(texts.esc(x) for x in r.lines))
    if r.blocked:
        note = T.NO_CHARGES if r.blocked == "charges" else LIFE_BLOCKED.get(r.blocked) or texts.esc(r.blocked)
    else:
        note = "\n".join(texts.esc(x) for x in r.lines)
    if kind:
        return await sub_screen(ctx, kind, note)
    return await _back(ctx, note)


@on_screen("aisub")
async def aisub_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    kind = {"cap": "nodes", "up": "mods", "trade": "kin", "op": "ops", "sellx": "sell", "sing": "end", "body": "end"}.get(action)
    if kind is None:
        return await _back(ctx)
    return await _do(ctx, action, arg or "", "" if action in ("sing", "body") else kind)


_ = UiKit
