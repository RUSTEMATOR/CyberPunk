"""Wires the bus subscribers (architecture rule 13): quests, and later vows, roles, reputations.

`register(content, tz)` is idempotent: a second call replaces the first set (scratch scripts and
restarts in one process). The order is the order of the pipeline: quests count the event first, then
the character listeners change the character.
"""

from __future__ import annotations

from typing import Callable

from app.content.loader import Content
from app.services import game_events as bus
from app.services.quests import QuestListener
from app.services.reputation import ReputationListener
from app.services.roles import RoleListener
from app.services.vows import VowListener
from app.services.players import PlayerQuestListener

_unsubscribe: list[Callable[[], None]] = []


def register(content: Content, tz: str) -> None:
    for off in _unsubscribe:
        off()
    _unsubscribe.clear()
    _unsubscribe.append(bus.subscribe(bus.ANY, QuestListener(content, tz)))
    _unsubscribe.append(bus.subscribe(bus.ANY, VowListener(content, tz)))
    _unsubscribe.append(bus.subscribe(bus.ANY, RoleListener(content, tz)))
    _unsubscribe.append(bus.subscribe(bus.ANY, ReputationListener(content, tz)))
    _unsubscribe.append(bus.subscribe(bus.ANY, PlayerQuestListener(content, tz)))
    from app.services.retention import GroupGoalListener, OnboardingListener

    _unsubscribe.append(bus.subscribe(bus.ANY, OnboardingListener(content, tz)))
    _unsubscribe.append(bus.subscribe(bus.ANY, GroupGoalListener(content, tz)))
    from app.services.levels import LevelListener  # owner request 24.10: every deed is character experience

    _unsubscribe.append(bus.subscribe(bus.ANY, LevelListener(content, tz)))
