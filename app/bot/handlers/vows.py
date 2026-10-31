"""Vows in the window (PLAN 4.8): see the vows held, take a new one. Opens from the profile.

vows (held + free) -> vowtake (the price of breaking, confirm) -> vows with a note.
"""

from __future__ import annotations

from app.bot import texts, views
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen, list_rows
from app.db import session as db
from app.engine import vows as engine_vows
from app.services import game
from app.services import vows as vow_service


async def vows_screen(ctx: Ctx, note: str | None = None) -> Screen:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
    if ch is None:
        return Screen(texts.VOW_NO_CHAR, [[Button(texts.BACK, "back")]], name="vows")
    st, content = ch.state, ctx.content
    lines = [texts.VOWS_TITLE, texts.VOWS_INTRO, ""]
    if note:
        lines += [note, ""]
    lines.append(texts.VOWS_HELD)
    if not st.vows:
        lines.append(texts.VOWS_NONE_HELD)
    for key, weeks in st.vows.items():
        vow = content.vows.get(key)
        if vow is None:
            continue
        lines.append(texts.VOWS_HELD_LINE.format(name=texts.esc(vow.name), weeks=weeks))
        got = engine_vows.reached_bonuses(vow, weeks)
        if got:
            mods: dict[str, int] = {}
            for b in got:
                for k, v in b.mods.items():
                    mods[k] = mods.get(k, 0) + v
            lines.append(texts.VOWS_BONUS_NOW.format(mods=views.mods_text(mods)))
        nxt = engine_vows.next_bonus(vow, weeks)
        if nxt is not None:
            lines.append(texts.VOWS_BONUS_NEXT.format(left=nxt.weeks - weeks, mods=views.mods_text(dict(nxt.mods))))
    free = [v for k, v in content.vows.items() if k not in st.vows]
    lines += ["", texts.VOWS_FREE]
    if not free:
        lines.append(texts.VOWS_NONE_FREE)
    numbered, rows = list_rows([v.name for v in free], "t", [v.key for v in free], ctx.content.balance.ui.button_chars)
    for n, vow in enumerate(free, 1):
        lines.append((f"{n}. " if numbered else "") + texts.VOWS_FREE_LINE.format(
            name=texts.esc(vow.name), desc=texts.esc(vow.description)))
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="vows")


@on_screen("vows")
async def vows_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await goto(ctx, "profile")
    if action == "t" and arg:
        vow = ctx.content.vows.get(arg)
        if vow is None:
            return await vows_screen(ctx, texts.VOW_TAKE_GONE)
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await vows_screen(ctx)
        why = engine_vows.can_take(ch.state, ctx.content, arg)
        if why is not None:
            return await vows_screen(ctx, _refusal(why))
        text = texts.VOW_CONFIRM.format(
            name=texts.esc(vow.name), desc=texts.esc(vow.description), cost=views.effects_cost(dict(vow.break_effects)),
        )
        return Screen(text, [[Button(texts.VOW_CONFIRM_BTN, "ok", arg)], [Button(texts.VOWS_BTN_NO, "no")]],
                      name="vowtake", state={"vow": arg})
    return await vows_screen(ctx)


def _refusal(why: str) -> str:
    return {
        engine_vows.TAKE_HELD: texts.VOW_TAKE_HELD,
        engine_vows.TAKE_CHROME: texts.VOW_TAKE_CHROME,
    }.get(why, texts.VOW_TAKE_GONE)


@on_screen("vowtake")
async def vowtake_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "ok" and arg and arg == ctx.state.get("vow"):
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await vows_screen(ctx)
            why = await vow_service.take_vow(s, ctx.content, ch, arg, ctx.ui.settings.timezone)
        if why is not None:
            return await vows_screen(ctx, _refusal(why))
        vow = ctx.content.vows[arg]
        return await vows_screen(ctx, texts.VOW_TAKEN.format(name=texts.esc(vow.name)))
    return await vows_screen(ctx)
