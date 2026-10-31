"""A talk with a talking smart weapon (owner request 24.10: «умное оружие, которое разговаривает»).

«Экипировка» -> «Поговорить: …» -> swchat. Same flow as the NPC talk (handlers/npcchat.py): the window waits for text,
the player's line is deleted from the chat, Claude answers in the weapon's voice (`Weapon.voice.persona`), the talk
changes no numbers. The weapon remembers the last talks (flag `sw_memory`).
"""

from __future__ import annotations

import asyncio

from app.bot import texts_narrator as T
from app.bot.ui.callbacks import Ctx, goto, on_screen, on_text
from app.bot.ui.screen import Button, Screen
from app.db import session as db
from app.engine import property as prop
from app.engine.state import KIND_WEAPON
from app.narrator import narrator
from app.services import game, narration

esc = T.esc
FLAG_MEMORY = "sw_memory"
_locks: dict[int, asyncio.Lock] = {}


def chat_screen(ctx: Ctx, key: str, history: list[list[str]], *, note: str | None = None,
                waiting: str | None = None) -> Screen:
    c = ctx.content
    nb = c.balance.narrator
    w = c.weapons[key]
    lines = [f"🗣 <b>{esc(w.name)}</b>", f"<i>{esc(w.description)}</i>", ""]
    for said, answer in history[-nb.npc_chat_exchanges_shown:]:
        lines += [T.CHAT_YOU.format(text=esc(said)), T.CHAT_NPC.format(name=esc(w.name), text=esc(answer)), ""]
    if waiting is not None:
        lines += [T.CHAT_YOU.format(text=esc(waiting)), T.CHAT_WAIT.format(name=esc(w.name)), ""]
    state = {"key": key, "history": history}
    left = nb.npc_chat_max_player_messages - len(history)
    if left <= 0:
        lines.append(f"{esc(w.name)} замолкает. Поговорить можно будет потом снова.")
        return Screen("\n".join(lines).strip(), [[Button("К экипировке", "end")]], name="swchat", state=state)
    if note:
        lines.append(note)
    lines.append(f"Пиши в чат, что говоришь оружию. Сообщение исчезнет из беседы. Осталось реплик: {left}.")
    return Screen("\n".join(lines).strip(), [[Button("Закончить", "end")]], awaiting_text="sw_chat", name="swchat",
                  state=state)


def _owns(st, key: str) -> bool:  # noqa: ANN001
    return any(it.key == key for it in prop.owned_items(st, KIND_WEAPON))


async def open_chat(ctx: Ctx, key: str) -> Screen | None:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
    c = ctx.content
    w = c.weapons.get(key)
    if ch is None or w is None or w.voice is None or not _owns(ch.state, key):
        return await goto(ctx, "prequip", "open")
    if not narrator.is_live():
        mem = list((ch.state.flags.get(FLAG_MEMORY) or {}).get(key) or [])
        idle = w.voice.idle[len(mem) % len(w.voice.idle)] if w.voice.idle else "…"
        return chat_screen(ctx, key, [], note=f"Связь с Оракулом слабая. {esc(w.name)} бормочет: «{esc(idle)}»")
    return chat_screen(ctx, key, [])


@on_screen("swchat")
async def swchat_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    return await goto(ctx, "prequip", "open")


@on_text("sw_chat")
async def sw_chat_text(ctx: Ctx, text: str) -> Screen | None:
    lock = _locks.setdefault(ctx.user_id, asyncio.Lock())
    if lock.locked():
        return None
    async with lock:
        c = ctx.content
        nb = c.balance.narrator
        key = str(ctx.state.get("key", ""))
        history = [list(x) for x in (ctx.state.get("history") or [])]
        w = c.weapons.get(key)
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None or w is None or w.voice is None or not _owns(ch.state, key):
                return None
            note = await narration.legacy_note(s, c, ch) if narrator.is_live() else ""
        line = " ".join(text.split())[: nb.npc_chat_player_max_chars]
        if not line or len(history) >= nb.npc_chat_max_player_messages:
            return chat_screen(ctx, key, history)
        if not narrator.is_live():
            return chat_screen(ctx, key, history, note=T.CHAT_OFF)
        await ctx.show(chat_screen(ctx, key, history, waiting=line))
        who = ("умное оружие с искусственным интеллектом в руке игрока (не человек, говорит из ствола или клинка); "
               + w.voice.persona)
        card = narration.npc_card(c, ch.state, name=w.name, who=who, relation=70, age=None, enemy=False, lineage_note=note)
        memory = list((ch.state.flags.get(FLAG_MEMORY) or {}).get(key) or [])
        if memory:
            card["npc"]["memory"] = memory  # type: ignore[index]
        reply = await narrator.npc_reply(card, [(a, b) for a, b in history], line, year=ch.state.current_year)
        if not reply:
            return chat_screen(ctx, key, history, note=f"{esc(w.name)} молчит. Индикатор на корпусе мигает.")
        history.append([line, reply])
        async with db.session_scope() as s:
            ch2 = await game.get_active_character(s, ctx.user_id)
            if ch2 is not None:
                mem = dict(ch2.state.flags.get(FLAG_MEMORY) or {})
                mem[key] = (list(mem.get(key) or []) + [f"Хозяин: {line[:160]} / {w.name}: {reply[:200]}"])[-8:]
                ch2.state.flags[FLAG_MEMORY] = mem
                await game.save_active(s, ch2, ctx.ui.settings.timezone)
        return chat_screen(ctx, key, history)
