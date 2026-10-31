"""Owner request 24.10: every deed in the game is character experience (engine/perks.py).

A bus subscriber: each published deed of a loaded character adds `perks.action_xp[event]` (or the default) to the
character's level record. Action points give experience in services/ap.py.
"""

from __future__ import annotations

from app.content.loader import Content
from app.engine import perks
from app.services import game_events as bus
from app.services.game import ActiveCharacter, save_active


class LevelListener:
    def __init__(self, content: Content, tz: str) -> None:
        self.content = content
        self.tz = tz

    async def __call__(self, ev: bus.GameEvent) -> None:
        ch: ActiveCharacter | None = ev.ch
        if ch is None or ev.session is None:
            return
        c = perks.cfg(self.content)
        n = int(c.action_xp.get(ev.name, c.default_action_xp))
        if n <= 0:
            return
        perks.add_xp(ch.state, self.content, n)
        await save_active(ev.session, ch, self.tz)
