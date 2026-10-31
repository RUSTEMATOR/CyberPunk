"""Vows on the bus (PLAN 4.8): breaks from events, streak milestones, taking a vow.

`VowListener` watches every bus event. A deed a held vow forbids (`engine.vows.FORBID_EVENTS`) runs
`apply_effects` with `vow_break:<key>`: the streak burns, the break effects land and the followup line
is parked as a notice. A `vow_week` that reaches a streak bonus parks a line about the bonus.
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.content.loader import Content
from app.engine import vows as engine_vows
from app.engine.effects import apply_effects
from app.services import followups, notices
from app.services import game_events as bus
from app.services import game
from app.services.game import ActiveCharacter, save_active


class VowListener:
    def __init__(self, content: Content, tz: str) -> None:
        self.content = content
        self.tz = tz

    async def __call__(self, ev: bus.GameEvent) -> None:
        ch, s = ev.ch, ev.session
        if ch is None or s is None or not ch.state.vows:
            return
        changed = False
        if ev.name == "vow_week":
            changed = self._milestones(ch, ev.params.get("vow"))
        broken = engine_vows.broken_vows(ch.state, self.content, ev.name, ev.params)
        if broken:
            if await self._break(s, ch, broken):
                return  # the break killed the character: the death is already saved
            changed = True
        if changed:
            await save_active(s, ch, self.tz)

    def _milestones(self, ch: ActiveCharacter, only: str | None) -> bool:
        any_ = False
        for key, weeks in ch.state.vows.items():
            if only is not None and key != only:
                continue
            vow = self.content.vows.get(key)
            bonus = engine_vows.bonus_at(vow, weeks) if vow is not None else None
            if vow is not None and bonus is not None:
                notices.push(ch, texts.VOW_MILESTONE.format(vow=texts.esc(vow.name), weeks=weeks))
                any_ = True
        return any_

    async def _break(self, s: AsyncSession, ch: ActiveCharacter, keys: list[str]) -> bool:
        """Break the vows. True when the character died of it."""
        year = await game.current_year(s, self.content, ch.player_tg_id)
        rng = random.Random()
        fx: dict[str, int | bool] = {f"vow_break:{k}": True for k in keys}
        applied = apply_effects(ch.state, fx, self.content, rng, year)
        out = await followups.handle(s, self.content, ch, applied.followups, rng, year, self.tz)
        for line in out.lines:
            notices.push(ch, line)
        return out.died


# -- taking a vow -----------------------------------------------------------------------------------------


async def take_vow(s: AsyncSession, content: Content, ch: ActiveCharacter, key: str, tz: str) -> str | None:
    """Take a vow with a fresh streak. Returns the refusal reason (engine.vows.TAKE_*) or None."""
    why = engine_vows.take(ch.state, content, key)
    if why is None:
        await save_active(s, ch, tz)
    return why
