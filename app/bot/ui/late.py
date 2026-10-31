"""A screen now, a better one later (owner request: the bot must not wait for Claude).

The handler returns the screen with the authored text at once; `attach` marks it with a token and runs `job` in the
background. When the job builds a new screen (Claude's text arrived), it replaces the window, but only while the window
still shows the marked screen.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable

from app.bot.ui.screen import Screen

log = logging.getLogger(__name__)


def attach(ctx, screen: Screen, job: Callable[[], Awaitable[Screen | None]]) -> Screen:  # noqa: ANN001
    token = uuid.uuid4().hex[:12]
    screen.state = {**(screen.state or {}), "late": token}

    async def run() -> None:
        try:
            better = await job()
            if better is not None:
                await ctx.ui.windows.refine(ctx.user_id, token, better)
        except Exception:  # a late text is a bonus: never an error for the player
            log.exception("late: the refined screen failed")

    ctx.ui.spawn(run())
    return screen
