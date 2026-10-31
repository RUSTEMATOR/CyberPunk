"""«🎯 Рейд-босс» (owner request 27.10, docs/PLAN_PBOSS.md): a player's character with a bounty on the head. Every
attack is a real turn-based fight to the death with the boss's own fighter on the boss's own health. «Город» →
«🎯 Рейд-босс»."""

from __future__ import annotations

import random

from app.bot import texts
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, on_screen
from app.bot.ui.screen import Button, Screen, pack
from app.content.loader import Content
from app.db import session as db
from app.services import game
from app.services import pboss as svc
from app.services.game import ActiveCharacter

WHY = {svc.BLOCK_NONE: "Сейчас охоты нет.", svc.BLOCK_SELF: "Это твоя голова. Можно только ждать охотников.",
       svc.BLOCK_JAIL: "Из камеры на охоту не ходят.", svc.BLOCK_AP: "Не хватает ОД.",
       svc.BLOCK_GONE: "Босса больше нет в городе.", svc.BLOCK_BUSY: "Сначала разберись с событием, которое ждёт ответа.",
       svc.BLOCK_AWAY: "Босс живёт в другом городе. Охотиться можно только на месте: переезжай туда же («Мир»)."}


def _wait(s: float) -> str:
    m = int(s // 60) + 1
    return f"{m // 60} ч {m % 60} мин" if m >= 60 else f"{m} мин"


def boss_screen(content: Content, ch: ActiveCharacter, rec: dict | None, note: str | None = None,
                hp: tuple[int, int] | None = None) -> Screen:
    c = svc.cfg(content)
    lines = [style.header("🎯", "Рейд-босс")]
    rows: list[list[Button]] = []
    if rec is None:
        lines += ["", "Сейчас за головами никто не охотится."]
    else:
        me = int(rec["tg"]) == int(ch.player_tg_id)
        lines += ["", f"<b>{texts.esc(rec['name'])}</b>. За голову: <b>{svc.money(rec['bounty'])} эдди</b> тому, кто добьёт."]
        if c.daily_add:  # owner request 27.10
            lines.append(f"Каждые сутки, пока босс жив, цена растёт на {svc.money(c.daily_add)}.")
        if hp is not None:
            lines.append(f"Здоровье босса: {style.bar(hp[0], hp[1])} {hp[0]}/{hp[1]}")
        lines += ["", f"Пошаговый бой насмерть один на один с бойцом босса: его оружие, броня, хром, программы и его "
                      f"здоровье, какое есть сейчас. Отказаться босс не может. Не добил — босс уходит раненым и может "
                      f"залечиться где угодно, пока следующий охотник его ищет. Упадёшь сам — всё как в любом бою: "
                      f"спасброски, Trauma Team или смерть. Босса вынести мало: у него свои Trauma Team, спасбросок, глюк и клон, награда только за окончательную смерть. Бой стоит {c.ap} ОД, раз в {c.hit_cooldown_h:g} ч."]
        hits = sorted((rec.get("hits") or {}).values(), key=lambda h: -int(h.get("dmg", 0)))[:5]
        if hits:
            lines += ["", "<b>Охотники:</b>"] + [f"• {texts.esc(str(h.get('name')))}: {h.get('dmg', 0)} урона, "
                                                  f"боёв {h.get('n', 0)}" for h in hits]
        if me:
            lines += ["", "Это ты. Город смотрит, сколько продержишься. Лечись между боями: клиника, импланты, всё что есть. "
                          "Когда придёт охотник, будет приглашение встать в бой самому."]
            rows.append([Button("🎯 Бой за мою голову", "fight")])
        else:
            wait = svc.cooldown_left(content, rec, ch.player_tg_id)
            if wait > 0:
                lines += ["", f"Следующий бой через {_wait(wait)}."]
            else:
                rows.append([Button(f"⚔️ В бой насмерть ({c.ap} ОД)"[:24], "hit", style="danger")])
    if note:
        lines += ["", note]
    rows.append([Button("Город", "city"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="pboss")


@on_screen("pboss")
async def pboss_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.bot.handlers.menu import menu_screen

    if action == "menu":
        return await menu_screen(ctx)
    if action == "fight":  # owner request 27.10: the boss's own window of the fight
        return await fight_screen(ctx)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if action == "city":
            from app.bot.handlers.city import city_screen

            return city_screen(ctx.content, ch)
        if action != "hit":
            rec = await svc.load(s)
            boss = await svc.boss_of(s, rec)
            return boss_screen(ctx.content, ch, rec, hp=svc.health(ctx.content, boss) if boss else None)
        year = await game.current_year(s, ctx.content)
        r = await svc.start(s, ctx.content, ch, random.Random(), year, ctx.ui.settings.timezone)
        rec = await svc.load(s)
        boss = await svc.boss_of(s, rec)
        hp = svc.health(ctx.content, boss) if boss else None
    if r.step is not None:
        from app.bot.handlers.combat import step_screen

        return step_screen(ctx.content, ch, r.step)  # type: ignore[arg-type]
    note = f"Рано: следующий бой через {_wait(r.wait_s)}." if r.blocked == svc.BLOCK_CD else WHY.get(r.blocked or "", r.blocked)
    return boss_screen(ctx.content, ch, rec, note, hp)


# -- the boss in person (owner request 27.10 «меня не пригласило в бой со мной») -----------------------------------------

from aiogram import F, Router  # noqa: E402
from aiogram.types import CallbackQuery  # noqa: E402

router = Router(name="pboss")


async def fight_screen(ctx: Ctx, note: str | None = None) -> Screen:
    import time as _time

    from app.db import repo

    content = ctx.content
    pc = content.balance.pally
    async with db.session_scope() as s:
        live = await svc.live_for(s, content, ctx.user_id)
        over = (await repo.ws_get(s, svc.OVER, {}) or {}).get(str(ctx.user_id))
        chosen = (await repo.ws_get(s, svc.ORDERS, {}) or {}).get(str((live or {}).get("hunter", ""))) if live else None
    lines = [style.header("🎯", "Бой за твою голову")]
    rows: list[list[Button]] = []
    now = _time.time()
    if live is None:
        if over and now - float(over.get("at", 0)) < 30 * 60:
            lines.append(texts.esc(str(over.get("text"))))
        else:
            lines.append("Сейчас за тобой никто не пришёл. Когда придут, будет приглашение «🎯 Защищаться самому».")
    else:
        lines += [f"Охотник: <b>{texts.esc(str(live.get('hunter_name')))}</b>.", texts.esc(str(live.get("snap") or ""))]
        if live.get("last"):
            lines.append(f"Твой прошлый ход: {texts.esc(svc.skill_label(content, str(live['last'])))}.")
        if not live.get("joined"):
            left = int(float(live.get("invite_until", 0) or 0) - now)
            lines.append("Твой боец дерётся сам. Встань в бой, чтобы выбирать ходы"
                         + (f" (охотник ждёт ещё {left} с)." if left > 0 else "."))
            rows.append([Button("🎯 Защищаться самому", "join", style="danger")])
        elif chosen:
            lines.append(f"Ход {live.get('tick')}: выбрано «{texts.esc(svc.skill_label(content, str(chosen)))}». "
                         f"Ждём охотника.")
            rows.append([Button("Обновить", "fight")])
        else:
            left = int(float(live.get("turn_at", 0) or 0) + pc.turn_s - now)
            lines.append(f"Ход {live.get('tick')}: выбери, что делает твой боец"
                         + (f", осталось {left} с." if left > 0 else ". Время вышло: боец решит сам, если не успеешь."))
            btns = [Button(svc.skill_label(content, sk)[:24], "pick", str(i),
                           style="danger" if sk == "flee" else None)
                    for i, sk in enumerate(live.get("skills") or [])]
            rows.extend(pack(btns, content.balance.ui.button_chars, max_per_row=2))
            rows.append([Button("Обновить", "fight")])
        lines.append(f"«Уйти от охотника»: шанс {int(svc.cfg(content).flee_chance * 100)}% вырваться с тем здоровьем, "
                     f"что осталось; не вышло — боец всё равно бьёт.")
    if note:
        lines += ["", note]
    rows.append([Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="pbfight")


@on_screen("pbfight")
async def pbfight_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.bot.handlers.menu import menu_screen

    if action == "menu":
        return await menu_screen(ctx)
    note = None
    async with db.session_scope() as s:
        live = await svc.live_for(s, ctx.content, ctx.user_id)
        if action == "join":
            note = (await svc.join_live(s, ctx.user_id, int(live["hunter"]), ctx.chat_id) if live
                    else "Этот бой уже кончился.") or "Ты в бою. Выбирай ход."
        elif action == "pick" and arg and arg.isdigit():
            skills = list((live or {}).get("skills") or [])
            i = int(arg)
            note = await svc.choose(s, ctx.content, ctx.user_id, skills[i]) if 0 <= i < len(skills) else "Бой уже кончился."
    return await fight_screen(ctx, note)


async def push_fight(ui, tg: int) -> None:  # noqa: ANN001
    """Redraw the boss's window after the hunter's tick (or the end of the fight)."""
    from app.db import repo

    async with db.session_scope() as s:
        win = await repo.get_window(s, tg)
    if win is None or win.screen != "pbfight":
        return
    ctx = Ctx(ui, tg, None, "", win.chat_id, None)
    await ui.windows.show(tg, await fight_screen(ctx), win.chat_id)


@router.callback_query(F.data.startswith(f"{svc.JOIN_PREFIX}:"))
async def on_join(query: CallbackQuery, ui) -> None:  # noqa: ANN001
    """«🎯 Защищаться самому» under the invitation: only for the boss it calls."""
    from app.bot.ui.callbacks import make_ctx

    parts = (query.data or "").split(":")
    user = query.from_user
    msg = query.message
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit() or msg is None:
        await query.answer()
        return
    if int(parts[2]) != user.id:
        await query.answer("Это приглашение не тебе.", show_alert=False)
        return
    async with db.session_scope() as s:
        why = await svc.join_live(s, user.id, int(parts[1]), msg.chat.id)
    await query.answer(why or "Ты в бою.")
    if why:
        return
    ui.outbox.delete(msg.chat.id, msg.message_id)
    ctx = await make_ctx(ui, user, msg.chat.id)
    await ui.windows.open(msg.chat.id, user.id, await fight_screen(ctx, "Ты в бою. Выбирай ход."))


async def hold_for_boss(ctx: Ctx) -> None:
    """Before the hunter's tick: wait for the boss who plays in person (the invitation and turn timers)."""
    import asyncio
    import time as _time

    pc = ctx.content.balance.pally
    deadline = _time.time() + pc.invite_s + pc.turn_s
    shown = False
    while _time.time() < deadline:
        async with db.session_scope() as s:
            w = await svc.waiting(s, ctx.content, ctx.user_id)
        if w is None:
            return
        if not shown:
            shown = True
            await ctx.show(Screen(f"⏳ <b>Ждём ход босса</b>\n{texts.esc(w[0])}: до {int(w[1])} с.\nНе ответит — "
                                  "его боец решит сам.", [], name="cm"))
        await asyncio.sleep(1.5)
