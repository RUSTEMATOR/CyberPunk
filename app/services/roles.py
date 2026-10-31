"""Role affinity on the bus (PLAN 4.10, DESIGN.md 5b).

`RoleListener` adds the affinity every bus event is worth (`roles.yaml`), saves the character and parks
notices in the window: a new rank (with the perk it opened) and "Город начинает звать тебя ...".
The weekly cooling lives in `engine/week.py::decay_role_affinity`; its notice is built by `main_notice`.
"""

from __future__ import annotations

from app.bot import texts, views
from app.content.loader import Content
from app.engine import roles as engine_roles
from app.engine.state import CharacterState
from app.services import game_events as bus
from app.services import notices
from app.services.game import ActiveCharacter, save_active


def main_notice(content: Content, role_key: str) -> str:
    role = content.roles.get(role_key)
    name = texts.ROLE_INSTRUMENTAL.get(role_key) or (role.name if role else role_key)
    return texts.ROLE_CALLED.format(role=texts.esc(name))


def rank_notice(content: Content, up: engine_roles.RankUp) -> str:
    role = content.roles[up.role]
    if up.perk:
        perk = next(p for p in role.perks if p.name == up.perk)
        return texts.ROLE_PERK.format(
            role=texts.esc(role.name), rank=up.rank, perk=texts.esc(perk.name), mods=views.mods_text(dict(perk.mods)),
        )
    return texts.ROLE_RANK_UP.format(role=texts.esc(role.name), rank=up.rank)


def push_change(content: Content, st: CharacterState, change: engine_roles.RoleChange) -> None:
    for up in change.rank_ups:
        notices.push_state(st, rank_notice(content, up))
    if change.new_main:
        notices.push_state(st, main_notice(content, change.new_main))


class RoleListener:
    def __init__(self, content: Content, tz: str) -> None:
        self.content = content
        self.tz = tz

    async def __call__(self, ev: bus.GameEvent) -> None:
        ch: ActiveCharacter | None = ev.ch
        if ch is None or ev.session is None:
            return
        change = engine_roles.grow(ch.state, self.content, ev.name, ev.params)
        if change.empty:
            return
        push_change(self.content, ch.state, change)
        await save_active(ev.session, ch, self.tz)
