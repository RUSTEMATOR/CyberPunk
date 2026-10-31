"""AP spending for every action (PLAN 4.1). Handlers and services call `spend`, never touch `state.ap`."""

from __future__ import annotations

from app.content.loader import Content
from app.engine import ap as engine_ap
from app.engine.ap import ApSpend, NotEnoughAp
from app.services.game import ActiveCharacter

__all__ = ["ApSpend", "NotEnoughAp", "spend", "can_spend", "AP_ZERO_SECTIONS"]

# Menu sections that stay open at 0 AP (PLAN 4.1).
AP_ZERO_SECTIONS: tuple[str, ...] = ("profile", "daily", "week")


def can_spend(ch: ActiveCharacter, cost: int) -> bool:
    return engine_ap.can_spend(ch.state, cost)


def spend(ch: ActiveCharacter, content: Content, cost: int) -> ApSpend:
    """Take `cost` AP from the character (in memory; the caller saves) and recharge active implants.

    Raises NotEnoughAp and changes nothing when the character has too few.
    """
    out = engine_ap.spend_ap(ch.state, content, cost)
    from app.services import wanted  # owner request 26.10: actions while wanted bring the police sooner

    wanted.count_ap(ch, cost)
    return out
