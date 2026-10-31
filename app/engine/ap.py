"""Action points (PLAN 4.1, DESIGN.md section 4).

Every action that costs AP goes through `spend_ap`. Besides the AP itself it does the one side
effect that spending has: each spent AP is a few hours of rest, so active implants (Sandevistan,
Berserk, Kerenzikov, rockets) get `cyber.active.recharge_per_ap` percent of charge back.
AP is not a key of `apply_effects` (it is not a scale of the character but of the week), so it is
changed here and only here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.content.loader import Content
from app.engine.dice import clamp
from app.engine.state import CharacterState


class NotEnoughAp(Exception):
    """Raised when an action costs more AP than the character has."""

    def __init__(self, need: int, have: int) -> None:
        super().__init__(f"need {need} AP, have {have}")
        self.need = need
        self.have = have


@dataclass
class ApSpend:
    spent: int
    left: int
    recharged: dict[str, int] = field(default_factory=dict)  # implant key -> charge points gained


def can_spend(state: CharacterState, cost: int) -> bool:
    return cost <= 0 or state.ap >= cost


def is_ap_zero(state: CharacterState) -> bool:
    """The menu shows only the profile and 'live a week' at 0 AP (PLAN 4.1)."""
    return state.ap <= 0


def spend_ap(state: CharacterState, content: Content, cost: int) -> ApSpend:
    """Take `cost` AP and recharge the active implants. Raises NotEnoughAp, changes nothing then."""
    if cost <= 0:
        return ApSpend(0, state.ap)
    if state.ap < cost:
        raise NotEnoughAp(cost, state.ap)
    state.ap -= cost
    from app.engine import perks  # owner request 24.10: every action is character experience

    perks.add_xp(state, content, perks.cfg(content).ap_xp * cost)
    gain = content.balance.cyber.active.recharge_per_ap * cost
    recharged: dict[str, int] = {}
    for item in state.cyberware:
        cw = content.cyberware.get(item.key)
        if cw is None or cw.active is None:
            continue
        new = clamp(item.charge + gain, 0, 100)
        if new != item.charge:
            recharged[item.key] = new - item.charge
            item.charge = new
    return ApSpend(cost, state.ap, recharged)
