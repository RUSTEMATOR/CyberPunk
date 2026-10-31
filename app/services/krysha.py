"""The krysha keeps the racket away (owner request 25.10): while a protection holds (engine/pressure.py, flag
`krysha`), the events of balance.pressure.racket_events are never drawn (engine/events.matches, the gang events of a
district held by gangs)."""

from __future__ import annotations

from app.content.loader import Content
from app.engine import pressure as pr_eng


def blocks(content: Content, state, event_id: str) -> bool:  # noqa: ANN001
    """A racket event never comes while a krysha holds."""
    return event_id in content.balance.pressure.racket_events and pr_eng.krysha(state, content)[1] is not None
