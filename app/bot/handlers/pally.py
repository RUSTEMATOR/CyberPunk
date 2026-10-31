"""The hired player's window «pally» (owner request 26.10, docs/PLAN_CORPFORCE.md part 1): the employer's fight right
now, joined in person like a multiplayer fight, and the own skills for its next tick; how the character fights there
when nobody chooses (as the player plays, or in turns).

The invitation «⚔️ Вступить в бой» (services/pally.invite) is a message outside the window: its button is caught by
`router` here and opens the window at the bottom of that chat. The employer's tick waits for the joined players
(`hold_for_allies`, called by the fight windows before a tick)."""

from __future__ import annotations

import asyncio
import time

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot import texts
from app.bot.views_combat import ally_text
from app.bot.handlers.menu import menu_screen
from app.bot.ui.callbacks import Ctx, goto, make_ctx, on_screen
from app.bot.ui.screen import Button, Screen, pack
from app.db import repo
from app.db import session as db
from app.engine import pally as eng
from app.services import game
from app.services import pally as svc

router = Router(name="pally")


def _top(content, style: dict) -> str:  # noqa: ANN001
    pairs: list[tuple[float, str]] = []
    for group in ("imp", "hack"):
        for k, v in (style.get(group) or {}).items():
            pairs.append((float(v), eng.label(content, f"{group}:{k}")))
    for k in ("ranged", "melee"):
        if float(style.get(k, 0) or 0):
            pairs.append((float(style[k]), eng.label(content, k)))
    pairs.sort(reverse=True)
    return ", ".join(n for _, n in pairs[:4]) or "пока нет своих боёв"


async def pally_screen(ctx: Ctx, note: str | None = None) -> Screen:
    content = ctx.content
    c = svc.cfg(content)
    cs = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        live = await svc.live_for(s, ctx.user_id, content)
        over = (await repo.ws_get(s, svc.OVER, {}) or {}).get(str(ctx.user_id))
        mine = {}
        if live is not None:
            mine = (await repo.ws_get(s, svc.ORDERS, {}) or {}).get(str(live["emp"])) or {}
            emp = await game.get_active_character(s, int(live["emp"]))
            if emp is not None:  # owner request 29.10: the window shows the employer's real fight
                from app.services import combat as combat_service

                cs = combat_service.state_of(emp)
    mode = str(ch.state.flags.get(eng.FLAG_MODE) or eng.PLAY)
    rows: list[list[Button]] = []
    now = time.time()
    if live is None:
        lines = ["⚔️ <b>Бой нанимателя</b>"]
        if over and now - float(over.get("at", 0)) < 30 * 60:
            lines.append(texts.esc(str(over.get("text"))))
        else:
            lines.append("Сейчас у нанимателя нет боя. Когда начнётся, придёт приглашение с кнопкой «Вступить в бой».")
    else:
        notes: list[str] = []
        aid = str(live.get("aid"))
        if live.get("last"):
            notes.append(f"Твой прошлый ход: {texts.esc(svc.move_label(content, str(live['last'])))}.")
        if live.get("down"):
            notes.append("Тебя вывели из этого боя.")
        elif not live.get("joined"):
            left = int(float(live.get("invite_until", 0) or 0) - now)
            notes.append("Ты в отряде на автомате. Вступи, чтобы выбирать ходы самому"
                         + (f" (наниматель ждёт ещё {left} с)." if left > 0 else "."))
            rows.append([Button("⚔️ Вступить в бой", "join", style="danger")])
        else:
            chosen = mine.get(aid)
            if chosen:
                notes.append(f"Ход {live.get('tick')}: выбрано «{texts.esc(svc.move_label(content, str(chosen)))}». "
                             "Ждём нанимателя.")
            else:
                left = int(float(live.get("turn_at", 0) or 0) + c.turn_s - now)
                notes.append(f"Ход {live.get('tick')}: твой ход"
                             + (f", осталось {left} с." if left > 0 else ". Время вышло: сработает сам, если не успеешь."))
            btns = [Button(eng.label(content, sk)[:24], "pick", str(i)) for i, sk in enumerate(live.get("skills") or [])]
            rows.extend(pack(btns, content.balance.ui.button_chars, max_per_row=2))
            hold = bool(live.get("hold")) or (cs is not None and (cs.ctx.get("cover") or {}).get(aid) == "hold")
            guard_row = [Button(svc.GUARD_LABELS[svc.GUARD], "guard", svc.GUARD)]
            rows.append(guard_row)
            rows.append([Button(svc.GUARD_LABELS[svc.UNHOLD], "guard", svc.UNHOLD) if hold
                         else Button(svc.GUARD_LABELS[svc.HOLD], "guard", svc.HOLD, style="danger")])
            rows.append([Button("Обновить", "open")])
        if cs is not None:
            text = ally_text(content, cs, aid, str(live.get("emp_name") or "Наниматель"), notes + ([note] if note else []))
        else:
            text = "\n".join([f"⚔️ <b>Бой нанимателя</b>: {texts.esc(str(live.get('emp_name')))} против "
                              f"{texts.esc(str(live.get('foe')))}.", texts.esc(str(live.get("snap") or "")), "", *notes]
                             + (["", note] if note else []))
        rows.append([Button(texts.MENU, "menu")])
        return Screen(text, rows, name="pally")
    lines += ["", "<b>Когда ты не выбираешь сам</b>",
              "• Как я играю: навыки по тому, что ты сам чаще используешь в своих боях.",
              "• По очереди: все навыки по кругу.",
              f"Сейчас: {'как я играю' if mode != eng.TURNS else 'по очереди'}. Твои частые навыки: "
              f"{texts.esc(_top(content, dict(ch.state.flags.get(eng.FLAG_STYLE) or {})))}."]
    rows.append([Button(("✅ " if mode != eng.TURNS else "") + "Как я играю", "mode", eng.PLAY),
                 Button(("✅ " if mode == eng.TURNS else "") + "По очереди", "mode", eng.TURNS)])
    if note:
        lines += ["", note]
    rows.append([Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="pally")


@on_screen("pally")
async def pally_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if action == "mode" and arg in eng.MODES:
            ch.state.flags[eng.FLAG_MODE] = arg
            await game.save_active(s, ch, ctx.ui.settings.timezone)
            note = "Запомнено."
        elif action == "join":
            live = await svc.live_for(s, ctx.user_id, ctx.content)
            note = (await svc.join_live(s, ctx.user_id, int(live["emp"]), ctx.chat_id) if live else "Этот бой уже кончился.") \
                or "Ты в бою. Выбирай ход."
        elif action == "guard" and arg in svc.GUARD_MOVES:  # owner request 29.10: cover the employer
            note = await svc.choose(s, ctx.content, ctx.user_id, arg)
        elif action == "pick" and arg and arg.isdigit():
            live = await svc.live_for(s, ctx.user_id, ctx.content)
            skills = list((live or {}).get("skills") or [])
            i = int(arg)
            note = await svc.choose(s, ctx.content, ctx.user_id, skills[i]) if 0 <= i < len(skills) else "Бой уже кончился."
    return await pally_screen(ctx, note)


async def open_screen(ctx: Ctx) -> Screen | None:
    return await goto(ctx, "pally", "open")


async def push_screen(ui, tg: int) -> None:  # noqa: ANN001
    """Redraw a joined hired player's window after the employer's tick (or the end of the fight)."""
    async with db.session_scope() as s:
        win = await repo.get_window(s, tg)
    if win is None or win.screen != "pally":
        return  # the player went elsewhere: the menu still has «⚔️ Бой нанимателя»
    ctx = Ctx(ui, tg, None, "", win.chat_id, None)
    await ui.windows.show(tg, await pally_screen(ctx), win.chat_id)


@router.callback_query(F.data.startswith(f"{svc.JOIN_PREFIX}:"))
async def on_join(query: CallbackQuery, ui) -> None:  # noqa: ANN001
    """«⚔️ Вступить в бой» under the invitation: only for the one it calls."""
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
    await ui.windows.open(msg.chat.id, user.id, await pally_screen(ctx, "Ты в бою. Выбирай ход."))


async def hold_for_allies(ctx: Ctx) -> None:
    """Before the employer's tick: wait (up to the invitation and turn timers) for the hired players who answer in
    person; the employer sees who is awaited. Without such players it returns at once."""
    content = ctx.content
    if not content.balance.pally.enabled:
        return
    shown = ""
    deadline = time.time() + content.balance.pally.invite_s + content.balance.pally.turn_s
    while time.time() < deadline:
        async with db.session_scope() as s:
            wait = await svc.waiting_for(s, content, ctx.user_id)
        if not wait:
            return
        line = ", ".join(sorted(texts.esc(n) for n, _ in wait))
        if line != shown:  # redrawn only when somebody answered, not every second
            shown = line
            longest = int(max(left for _, left in wait))
            await ctx.show(Screen(f"⏳ <b>Ждём отряд</b>\n{line}: до {longest} с.\nНе ответят — их навыки сработают "
                                  "сами.", [], name="cm"))
        await asyncio.sleep(1.5)
