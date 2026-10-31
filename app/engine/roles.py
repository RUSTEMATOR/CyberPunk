"""Emergent roles (PLAN 4.10, DESIGN.md 5b): affinity grows from bus events, cools every week,
ranks and perks follow. Pure: no clock, no DB, no randomness.

The perks are already in `derived.collect_mods` (they work at the rank of their own role). This
module only moves the affinity and tells the service what changed, so it can show a notice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.content.loader import Content
from app.engine import derived
from app.engine.daily import best_key_weight
from app.engine.state import CharacterState


# Owner report 24.10: players «randomly became monks» — meditation, martial arts and community service are also
# plain self-care, so the monk role grew for everyone. It grows only for a deliberate path (DESIGN.md 7a): a held vow
# or a monastery / commune origin. A clean body alone is not a choice (anyone without chrome yet has it).
MONK = "monk"
MONK_ORIGINS = ("monastery_novice", "preacher_commune")


def on_monk_path(state: CharacterState) -> bool:
    return bool(state.vows or state.origin in MONK_ORIGINS)


def role_open(state: CharacterState, key: str) -> bool:
    return key != MONK or on_monk_path(state)


@dataclass
class RankUp:
    role: str
    rank: int
    perk: str | None  # the perk this rank opened, if any


@dataclass
class RoleChange:
    gained: dict[str, int] = field(default_factory=dict)  # role -> affinity points added
    rank_ups: list[RankUp] = field(default_factory=list)
    new_main: str | None = None  # the role the city calls the character now (changed from the old one)

    @property
    def empty(self) -> bool:
        return not (self.gained or self.rank_ups or self.new_main)


def _ranks(state: CharacterState, content: Content) -> dict[str, int]:
    return derived.role_ranks(state, content)


def _rank_ups(content: Content, before: dict[str, int], after: dict[str, int]) -> list[RankUp]:
    ups: list[RankUp] = []
    for key, rank in sorted(after.items()):
        old = before.get(key, 0)
        if rank <= old:
            continue
        role = content.roles[key]
        perk = next((p.name for p in role.perks if old < p.rank <= rank), None)
        ups.append(RankUp(key, rank, perk))
    return ups


def sync_main_role(state: CharacterState, content: Content) -> str | None:
    """Set `state.role` to the earned main role. Returns the new key when it changed to another role.

    The role is named only from rank 1 (a few lucky points do not make a calling) and a new leader
    must be `roles.main_switch_pct` ahead of the old main role, so two close roles do not flip-flop.
    """
    rows = derived.ranked_roles(state, content)
    if not rows:
        return None
    leader, aff, rank = rows[0]
    if rank < 1 or leader == state.role:
        return None
    cur = state.role
    if cur:
        cur_aff = float(state.role_affinity.get(cur, 0.0))
        margin = 1 + content.balance.roles.main_switch_pct / 100
        if cur_aff > 0 and aff < cur_aff * margin:
            return None
    state.role = leader
    return leader


def grow(state: CharacterState, content: Content, event: str, params: dict[str, Any]) -> RoleChange:
    """Add the affinity this bus event is worth to every role and report ranks and the main role."""
    cap = float(content.balance.roles.affinity_max)
    before = _ranks(state, content)
    change = RoleChange()
    for key, role in content.roles.items():
        if role.is_placeholder or not role_open(state, key):
            continue
        weight = best_key_weight(dict(role.affinity), event, params)
        if weight <= 0:
            continue
        old = float(state.role_affinity.get(key, 0.0))
        new = min(cap, old + weight)
        if new > old:
            state.role_affinity[key] = new
            change.gained[key] = int(new - old)
    if not change.gained:
        return change
    change.rank_ups = _rank_ups(content, before, _ranks(state, content))
    change.new_main = sync_main_role(state, content)
    return change


def decay(state: CharacterState, content: Content) -> RoleChange:
    """The weekly cooling: every role loses `decay_pct` percent; tiny rests are forgotten."""
    floor = content.balance.roles.affinity_floor
    for key in list(state.role_affinity):
        role = content.roles.get(key)
        if role is None or role.is_placeholder or role.decay_pct is None:
            continue
        new = float(state.role_affinity[key]) * (1 - role.decay_pct / 100)
        if new < floor:
            del state.role_affinity[key]
        else:
            state.role_affinity[key] = new
    change = RoleChange()
    change.new_main = sync_main_role(state, content)
    return change
