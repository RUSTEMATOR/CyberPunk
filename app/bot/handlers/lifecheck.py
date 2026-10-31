"""The checked actions of M9 in the window: the luck / glitch / accept screen and the result dispatch.

A checked action (promotion, crime, escape) rolls through `services/lifecheck.py`. If the miss can still
be saved, the screen `lifechk` shows the check with the buttons Luck, Glitch, Accept; accepting settles
the action and the result goes to the renderer its module registered with `register_renderer`.
"""

from __future__ import annotations

import random
from typing import Callable

from app.bot import texts, views
from app.bot import texts_life as T
from app.bot.handlers.menu import menu_screen
from app.bot.handlers.week import death_screen
from app.bot.ui.callbacks import Ctx, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import checks, events as engine_events
from app.services import game, lifecheck
from app.services.game import ActiveCharacter
from app.services.lifecheck import LifeResult

Renderer = Callable[[Content, ActiveCharacter, LifeResult], Screen]
_renderers: dict[str, Renderer] = {}
_heads: dict[str, Callable[[Content, dict], str]] = {}


def register_renderer(action: str, fn: Renderer, head: Callable[[Content, dict], str] | None = None) -> None:
    _renderers[action] = fn
    if head is not None:
        _heads[action] = head


def result_screen(content: Content, ch: ActiveCharacter, res: LifeResult) -> Screen:
    if res.died:
        return death_screen(ch.state.name, res.death_cause, res.lines)
    return _renderers[res.action](content, ch, res)


def check_screen(content: Content, ch: ActiveCharacter) -> Screen:
    """The rolled check waiting for the decision (also the resume of a closed window)."""
    p = lifecheck.pending(ch)
    assert p is not None
    check = lifecheck.check_of(p)
    rev = int(p["rev"])
    st = ch.state
    action = str(p["action"])
    head = _heads[action](content, dict(p["args"])) if action in _heads else T.CHK_HEAD.get(action, "")
    lines = [head, "", views.check_line(check)]
    rows: list[list[Button]] = []
    if check.ok:
        lines.append(texts.CHECK_PASS_LUCK)
        rows.append([Button(texts.EVENT_CONTINUE, "take", str(rev))])
    else:
        offer = engine_events.luck_offer(check, st, content)
        can_glitch = checks.can_glitch_reroll(check, st.glitches)
        lines.append(texts.CHECK_FAIL if (offer or can_glitch) else texts.CHECK_FAIL_NOTHING)
        if offer:
            rows.append([Button(texts.EVENT_LUCK.format(points=offer.points, pool=offer.pool), "luck", str(rev))])
        if can_glitch:
            rows.append([Button(texts.EVENT_GLITCH.format(glitches=st.glitches), "glitch", str(rev))])
        rows.append([Button(texts.EVENT_ACCEPT_FAIL, "take", str(rev))])
    return Screen("\n".join(lines), rows, name="lifechk", state={"rev": rev})


def after_start(content: Content, ch: ActiveCharacter, got: "LifeResult | checks.CheckResult") -> Screen:
    """The screen after `lifecheck.start`: the decision, or the result."""
    if isinstance(got, LifeResult):
        return result_screen(content, ch, got)
    return check_screen(content, ch)


@on_screen("lifechk")
async def lifechk_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    if arg is None or not arg.lstrip("-").isdigit():
        return await menu_screen(ctx)
    rev = int(arg)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        try:
            if action == "luck":
                await lifecheck.luck(s, ctx.content, ch, rng, year, tz, rev)
                return check_screen(ctx.content, ch)
            if action == "glitch":
                await lifecheck.glitch(s, ctx.content, ch, rng, year, tz, rev)
                return check_screen(ctx.content, ch)
            if action == "take":
                res = await lifecheck.accept(s, ctx.content, ch, rng, year, tz, rev)
                return result_screen(ctx.content, ch, res)
        except LookupError:
            pass
    return await menu_screen(ctx)
