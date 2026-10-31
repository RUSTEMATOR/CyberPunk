"""Status and fame from the deeds on the bus (PLAN 4.9, DESIGN.md 5).

`reputation.bus_gains` in balance.yaml maps a bus key to the status / fame one occurrence gives (a
career promotion, a performance, a story, a deal). The most specific key that fits wins. The change
goes through `apply_effects`, so the caps and the character fields stay in one place. Street cred
is not here: it comes from gigs, fights and events through their own effects.
"""

from __future__ import annotations

import random

from app.content.loader import Content
from app.engine import daily as engine_daily
from app.engine.effects import apply_effects
from app.services import game
from app.services import game_events as bus
from app.services.game import save_active


def best_gain(content: Content, event: str, params: dict) -> dict[str, int]:
    """The status / fame of the most specific `bus_gains` key that fits the event ({} when none)."""
    table = content.balance.reputation.bus_gains
    best: tuple[int, int, str] | None = None  # (specificity, total, key)
    for key, gain in table.items():
        spec = engine_daily.key_matches(key, event, params)
        if spec is None:
            continue
        cand = (spec, gain.status + gain.fame, key)
        if best is None or cand > best:
            best = cand
    if best is None:
        return {}
    gain = table[best[2]]
    fx = {"status": gain.status, "fame": gain.fame}
    return {k: v for k, v in fx.items() if v}


class ReputationListener:
    def __init__(self, content: Content, tz: str) -> None:
        self.content = content
        self.tz = tz

    async def __call__(self, ev: bus.GameEvent) -> None:
        ch, s = ev.ch, ev.session
        if ch is None or s is None:
            return
        fx = best_gain(self.content, ev.name, ev.params)
        if not fx:
            return
        year = await game.current_year(s, self.content, ch.player_tg_id)
        apply_effects(ch.state, dict(fx), self.content, random.Random(), year)
        await save_active(s, ch, self.tz)
