"""'Прожить месяц': confirm, the report, the death screen (PLAN 4.2-4.4).

Screens: `week` (confirm, then the week runs), `weekrep` (the report), `death` (the life ended).
The week's events are played by `handlers/events.py` through `weekrep:next`.
"""

from __future__ import annotations

import random

from app.bot import texts, texts_world, views, views_world
from app.bot import texts_death as T_D
from app.bot import texts_life as T
from app.bot.handlers.menu import menu_screen
from app.bot.ui.callbacks import Ctx, goto, has_screen, on_screen
from app.bot.ui.screen import Button, Screen
from app.clock import utcnow
from app.db import session as db
from app.engine import ap as engine_ap
from app.engine import jail as jail_engine
from app.services import game, notices
from app.services import events as ev_service
from app.services import uniqueevent
from app.services import week as week_service


def death_screen(name: str, cause: str | None, lines: list[str] | None = None) -> Screen:
    """The end of a life: the cause, how it went (the save, the evacuation that did not come), then the legacy."""
    cause_name = texts.DEATH_CAUSE_NAMES.get(cause or "", cause or "?")
    parts = [T_D.DEATH_TITLE.format(name=texts.esc(name)), T_D.DEATH_CAUSE.format(cause=cause_name)]
    flavor = T_D.DEATH_FLAVOR.get(cause or "")
    if flavor:
        parts.append(flavor)
    parts.extend(lines or [])
    parts.append(T_D.DEATH_NOTE)
    return Screen("\n".join(parts), [[Button(T_D.BTN_DEATH_NEXT, "next")]], name="death")


@on_screen("death")
async def death_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    return await menu_screen(ctx)


async def run_week(ctx: Ctx) -> Screen:
    """Live the week for the pressing player and build the next screen."""
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        out = await week_service.live_week(s, ctx.content, ch, rng, tz, utcnow())
        name = ch.state.name
    assert out.report is not None
    if out.died:
        return death_screen(name, out.death_cause, out.notices)
    async with db.session_scope() as s:  # lines parked by the bus listeners during the week
        out.notices.extend(texts.esc_notice(n) for n in await notices.take(s, ch, tz))
    text = views.week_report_text(ctx.content, ch.state, out.report, notices=out.notices)
    more = bool(out.pending)
    if await uniqueevent.maybe_add(ctx.content, ctx.user_id, tz, rng):  # PLAN 8.5: a Claude-written event joins the queue
        more = True
    from app.services import saga  # the endless saga: its next step may join the queue

    if await saga.after_week(ctx.content, ctx.user_id, tz, rng):
        more = True
    report = report_screen(text, more)
    if out.beats or out.eras:  # the date reached a canon event or an era: its screen comes first (DESIGN.md 9a)
        return Screen(
            views_world.beat_window_text(ctx.content, out.beats, [e.key for e in out.eras]),
            [[Button(texts_world.BEAT_WINDOW_NEXT, "report")]],
            name="weekrep",
            state={"phase": "beat", "text": text, "more": more},
        )
    return report


def report_screen(text: str, more: bool) -> Screen:
    return Screen(
        text,
        [[Button(texts.WEEK_NEXT if more else texts.MENU, "next" if more else "menu")]],
        name="weekrep",
        state={"phase": "report"},
    )


@on_screen("week")
async def week_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await menu_screen(ctx)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
    if ch is None:
        return await menu_screen(ctx)
    if ev_service.has_pending(ch):
        return await goto(ctx, "evchoice", "resume") if has_screen("evchoice") else await menu_screen(ctx)
    if action == "open" and jail_engine.in_jail(ch.state):
        return Screen(
            T.JAIL_WEEK_CONFIRM.format(n=jail_engine.press_weeks(ch.state, ctx.content)),
            [[Button(texts.WEEK_LIVE, "go")], [Button(texts.BACK, "back")]],
            name="week",
        )
    if action == "open" and not engine_ap.is_ap_zero(ch.state):
        return Screen(
            texts.WEEK_CONFIRM.format(ap=ch.state.ap),
            [[Button(texts.WEEK_LIVE, "go")], [Button(texts.BACK, "back")]],
            name="week",
        )
    return await run_week(ctx)


@on_screen("weekrep")
async def weekrep_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "report" and ctx.state.get("text"):
        return report_screen(str(ctx.state["text"]), bool(ctx.state.get("more")))
    if action == "next" and has_screen("evchoice"):
        return await goto(ctx, "evchoice", "resume")
    return await menu_screen(ctx)
