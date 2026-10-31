"""Game-event bus (architecture rule 13). A stub for now: publish and subscribe in process.

Gameplay handlers publish what happened (`gig_done`, `crime_done`, `family_time`, `meditate`, ...);
daily quests, vows and role affinity (later milestones) subscribe. Event names are the keys of
`schemas.BUS_EVENTS`, so a typo fails loudly instead of silently feeding nobody.

Subscribers get the publisher's `AsyncSession` (when there is one) so their writes land in the same
unit of work. When the publisher holds the loaded character it passes it as `ch`: the character
subscribers (vows, roles, reputations) change THAT object and save it, so the publisher's copy never
goes stale. A failing subscriber is logged and skipped; it never breaks the action that published.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.schemas import BUS_EVENTS

log = logging.getLogger(__name__)

ANY = "*"  # subscribe to every event


@dataclass(frozen=True)
class GameEvent:
    name: str
    player_tg_id: int
    character_id: int | None = None
    params: dict[str, Any] = field(default_factory=dict)  # approach, type, outcome, kind, ...
    session: AsyncSession | None = None
    ch: Any = None  # the publisher's ActiveCharacter (services.game), if it has one loaded


Handler = Callable[[GameEvent], "Awaitable[None] | None"]

_subscribers: dict[str, list[Handler]] = {}


def subscribe(name: str, handler: Handler) -> Callable[[], None]:
    """Register `handler` for an event name (or ANY). Returns a function that unsubscribes it."""
    if name != ANY and name not in BUS_EVENTS:
        raise ValueError(f"unknown bus event {name!r}; allowed: {sorted(BUS_EVENTS)}")
    _subscribers.setdefault(name, []).append(handler)

    def unsubscribe() -> None:
        handlers = _subscribers.get(name, [])
        if handler in handlers:
            handlers.remove(handler)

    return unsubscribe


async def publish(
    name: str, *, player_tg_id: int, character_id: int | None = None,
    session: AsyncSession | None = None, ch: Any = None, **params: Any,
) -> int:
    """Deliver an event to its subscribers in subscription order. Returns how many handled it."""
    if name not in BUS_EVENTS:
        raise ValueError(f"unknown bus event {name!r}; allowed: {sorted(BUS_EVENTS)}")
    event = GameEvent(name, player_tg_id, character_id, dict(params), session, ch)
    handled = 0
    for handler in list(_subscribers.get(name, ())) + list(_subscribers.get(ANY, ())):
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
            handled += 1
        except Exception:  # a subscriber must not break the action that published
            log.exception("game event %s: subscriber %r failed", name, handler)
    return handled


def clear() -> None:
    """Drop every subscriber (scratch scripts)."""
    _subscribers.clear()
