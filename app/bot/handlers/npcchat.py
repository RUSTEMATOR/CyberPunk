"""A talk with an NPC in the player's window (PLAN 8.6, DESIGN.md section 14).

Relations -> NPC -> "Поговорить". The window waits for text (`awaiting_text`), the player's line is deleted from the
chat by the window layer, and the window shows the last `npc_chat_exchanges_shown` exchanges. At most
`npc_chat_max_player_messages` lines per talk. The reply is Claude's (narrative model) in the NPC's voice; the talk
changes the numbers only through an action tag the engine checks (services/npcact.py, owner request 26.10). When Claude does not answer (error, pause, a text that fails the check) the NPC stays
silent and the line is not counted. One answer at a time per player: a second line sent while the first is being
answered is ignored.
"""

from __future__ import annotations

import asyncio
import logging

from app.bot import texts_narrator as T
from app.bot.handlers.relations import _role_word, card_screen, relations_screen
from app.bot.ui.callbacks import Ctx, on_screen, on_text
from app.bot.ui.screen import Button, Screen
from app.db import session as db
from app.engine import relations as rel
from app.narrator import narrator
from app.services import game, narration
from app.services import relations as rel_service

log = logging.getLogger(__name__)
esc = T.esc

_locks: dict[int, asyncio.Lock] = {}


def chat_screen(
    shown: int, name: str, role: str, idx: int, history: list[list[str]], limit: int, *, note: str | None = None,
    waiting: str | None = None, did: str = "",
) -> Screen:
    """The talk window. `history` holds all exchanges, only the last `shown` are written out."""
    lines = [T.CHAT_HEAD.format(name=esc(name), role=role), ""]
    for said, answer in history[-shown:]:
        lines += [T.CHAT_YOU.format(text=esc(said)), T.CHAT_NPC.format(name=esc(name), text=esc(answer)), ""]
    if waiting is not None:
        lines += [T.CHAT_YOU.format(text=esc(waiting)), T.CHAT_WAIT.format(name=esc(name)), ""]
    left = limit - len(history)
    state = {"idx": idx, "name": name, "history": history, "did": did}
    if left <= 0:
        if note:
            lines.append(note)
        lines.append(T.CHAT_DONE.format(name=esc(name)))
        return Screen("\n".join(lines).strip(), [[Button(T.CHAT_TO_CARD, "end")]], name="npcchat", state=state)
    if note:
        lines.append(note)
    lines.append(T.CHAT_PROMPT.format(left=left))
    return Screen("\n".join(lines).strip(), [[Button(T.CHAT_END, "end")]], awaiting_text="npc_chat", name="npcchat", state=state)


async def open_chat(ctx: Ctx, idx: int) -> Screen:
    """Entry from the NPC card."""
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
    if ch is None:
        from app.bot.handlers.menu import menu_screen

        return await menu_screen(ctx)
    npc = rel_service.find(ch, idx)
    if npc is None or not npc.alive:
        return relations_screen(ctx.content, ch)
    if not narrator.is_live():
        return card_screen(ctx.content, ch, idx, T.CHAT_OFF)
    nb = ctx.content.balance.narrator
    return chat_screen(nb.npc_chat_exchanges_shown, npc.name, _role_word(npc), idx, [], nb.npc_chat_max_player_messages)


@on_screen("npcchat")
async def npcchat_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
    if ch is None:
        from app.bot.handlers.menu import menu_screen

        return await menu_screen(ctx)
    idx = int(ctx.state.get("idx", -1))
    if rel_service.find(ch, idx, ctx.state.get("name")) is None:
        return relations_screen(ctx.content, ch)
    return card_screen(ctx.content, ch, idx)


@on_text("npc_chat")  # owner report 24.10: the line is deleted; the window shows it
async def npc_chat_text(ctx: Ctx, text: str) -> Screen | None:
    lock = _locks.setdefault(ctx.user_id, asyncio.Lock())
    if lock.locked():
        return None  # the previous line is still being answered
    async with lock:
        nb = ctx.content.balance.narrator
        history = [list(pair) for pair in (ctx.state.get("history") or [])]
        idx = int(ctx.state.get("idx", -1))
        import time as _t

        did = str(ctx.state.get("did") or f"{ctx.user_id}-{int(_t.time())}")  # owner request 26.10: the talk's id
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return None
            npc = rel_service.find(ch, idx, ctx.state.get("name"))
            if npc is None or not npc.alive:
                return relations_screen(ctx.content, ch)
            note = await narration.legacy_note(s, ctx.content, ch) if narrator.is_live() else ""
        role = _role_word(npc)
        limit = nb.npc_chat_max_player_messages
        shown = nb.npc_chat_exchanges_shown
        line = " ".join(text.split())[: nb.npc_chat_player_max_chars]
        if not line or len(history) >= limit:
            return chat_screen(shown, npc.name, role, idx, history, limit, did=did)
        if not narrator.is_live():
            return card_screen(ctx.content, ch, idx, T.CHAT_OFF)
        await ctx.show(chat_screen(shown, npc.name, role, idx, history, limit, waiting=line, did=did))
        card = narration.npc_card(
            ctx.content, ch.state, name=npc.name, who=role, relation=npc.relation,
            age=rel.age_of(ch.state, npc, ctx.content), enemy=bool(npc.data.get("enemy")), lineage_note=note,
        )
        if npc.data.get("story") in ("raven", "lit"):  # as much as the player knows of them (owner report 26.10)
            from app.services import tearz

            card["npc"]["bio"] = tearz.bio_for(ch.state, str(npc.data["story"]))  # type: ignore[index]
        elif npc.data.get("bio"):  # owner request 26.10: story people and the AI in the head speak as themselves
            card["npc"]["bio"] = str(npc.data["bio"])  # type: ignore[index]
        memory = list(npc.data.get("memory") or [])  # owner request: the NPC remembers earlier talks
        if memory:
            card["npc"]["memory"] = memory  # type: ignore[index]
        done = bool(ctx.state.get("acted"))
        async with db.session_scope() as s:  # owner request 26.10: the city's talk, the player's fame, real actions
            from app.services import deeds, dyn, npcact

            card["city"] = await deeds.city_talk(s, ctx.content, ch.state.district)
            card["known_for"] = await deeds.known_for(s, ch.id)
            if not done:
                has_dyn = bool(dyn.alive(await dyn.all_factions(s)))
                card["npc"]["actions"] = npcact.menu(ch, npc, has_dyn=has_dyn)  # type: ignore[index]
        reply, act = await narrator.npc_reply_act(card, [(a, b) for a, b in history], line, year=ch.state.current_year)
        if not reply:
            return chat_screen(shown, npc.name, role, idx, history, limit, note=T.CHAT_SILENT.format(name=esc(npc.name)),
                               did=did)
        history.append([line, reply])
        note = None
        async with db.session_scope() as s:  # keep the last talks with this NPC across sessions
            ch2 = await game.get_active_character(s, ctx.user_id)
            npc2 = rel_service.find(ch2, idx, ctx.state.get("name")) if ch2 is not None else None
            if ch2 is not None and npc2 is not None:
                mem = list(npc2.data.get("memory") or []) + [f"Игрок: {line[:160]} / {npc2.name}: {reply[:200]}"]
                npc2.data["memory"] = mem[-8:]
                if act and not done:
                    import random as _r

                    from app.services import npcact

                    note = await npcact.apply(s, ctx.content, ch2, npc2, act, _r.Random(),
                                              await game.current_year(s, ctx.content)) or None
                await game.save_active(s, ch2, ctx.ui.settings.timezone)
                from app.services import dialogs  # owner request 26.10: the talk is judged later and has consequences

                await dialogs.record(s, ctx.content, ch2, npc2, history, did)
        if note and act == "lead":  # owner report 27.10: the lead becomes a scene at once, first in the events
            from app.services import uniqueevent

            ctx.ui.spawn(uniqueevent.add_from_lead(ctx.content, ctx.user_id, ctx.ui.settings.timezone,
                                                   {"who": npc.name, "said": reply, "asked": line}))
        scr = chat_screen(shown, npc.name, role, idx, history, limit, note=f"<b>↳ {esc(note)}</b>" if note else None,
                          did=did)
        if note and scr.state is not None:
            scr.state["acted"] = True
        elif done and scr.state is not None:
            scr.state["acted"] = True
        return scr
