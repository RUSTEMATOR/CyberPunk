"""The Blackwall path (owner request 24.10). Pure.

Becoming an AI is no longer a single step: the character goes through the taint (flag `bw_rot`, 0..rot_max) and the
chain of the Blackwall Avatars (forced events `bwq_1`..`bwq_5`, flags of the same names). Only an Avatar (flag
`bw_avatar`) may step beyond the Blackwall and become an AI (bot/handlers/net.can_ascend).

- The taint grows from Blackwall scripts and weapons, dives to the Blackwall, fights with Blackwall weapons, cult rites,
  tainting districts and the chain itself. Each tier (bw_r1..bw_r4 flags, for event conditions) gives power and takes
  humanity every week.
- The cult of the Blackwall: flag `bw_cult_in` (joined), `bw_favor` (favor points) -> rank.
- The power of the Blackwall = scripts, weapons, cult rank, taint, Avatar status. The chain asks for it.
- The city taint lives in world_state (services/blackwall.py); this module only reads the numbers given to it.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from app.content.loader import Content
from app.engine import checks
from app.engine.checks import CheckResult
from app.engine.state import CharacterState

FLAG_ROT = "bw_rot"
FLAG_FAVOR = "bw_favor"
FLAG_CULT = "bw_cult_in"
FLAG_AVATAR = "bw_avatar"
FLAG_DAY = "bwq_day"          # the Kyiv date of the last chain step (one step a real day)
FLAG_VIGIL = "bw_vigil_day"   # the Kyiv date of the last vigil
FLAG_SURGE = "bw_surge_day"   # the Kyiv date of the last Avatar surge
STEP_FLAGS = ("bwq_1", "bwq_2", "bwq_3", "bwq_4", "bwq_5")
TIER_FLAGS = ("bw_r1", "bw_r2", "bw_r3", "bw_r4")


def cfg(content: Content):  # noqa: ANN201
    return content.balance.blackwall


def rot(state: CharacterState) -> int:
    return int(state.flags.get(FLAG_ROT, 0) or 0)


def tier_of(content: Content, n: int) -> int:
    return sum(1 for t in cfg(content).tiers if n >= t)


def tier(state: CharacterState, content: Content) -> int:
    return tier_of(content, rot(state))


def _sync_tiers(state: CharacterState, content: Content) -> None:
    t = tier(state, content)
    for i, f in enumerate(TIER_FLAGS, start=1):
        if t >= i:
            state.flags[f] = True
        else:
            state.flags.pop(f, None)


def add_rot(state: CharacterState, content: Content, n: int) -> int:
    """Change the taint (clamped). An Avatar never drops below the last tier."""
    c = cfg(content)
    low = c.tiers[-1] if state.flags.get(FLAG_AVATAR) else 0
    state.flags[FLAG_ROT] = max(low, min(c.rot_max, rot(state) + n))
    _sync_tiers(state, content)
    return rot(state)


def favor(state: CharacterState) -> int:
    return int(state.flags.get(FLAG_FAVOR, 0) or 0)


def add_favor(state: CharacterState, n: int) -> int:
    state.flags[FLAG_FAVOR] = max(0, favor(state) + n)
    return favor(state)


def in_cult(state: CharacterState) -> bool:
    return bool(state.flags.get(FLAG_CULT))


def cult_rank(state: CharacterState, content: Content) -> int:
    """0 = not a member, 1..len(favor_ranks)."""
    if not in_cult(state):
        return 0
    return sum(1 for need in cfg(content).favor_ranks if favor(state) >= need)


def rank_name(content: Content, rank: int) -> str:
    names = cfg(content).rank_names
    return names[min(rank, len(names)) - 1] if rank > 0 and names else ""


def is_avatar(state: CharacterState) -> bool:
    return bool(state.flags.get(FLAG_AVATAR))


def touched(state: CharacterState) -> bool:
    """The Blackwall knows this character: the window opens."""
    f = state.flags
    return bool(rot(state) > 0 or f.get("blackwall_contact") or f.get("bw_owned") or f.get(FLAG_CULT) or f.get("bwq_1"))


def scripts(state: CharacterState) -> int:
    return sum(1 for cw in state.cyberware if cw.key.startswith("bw_"))


def weapons(state: CharacterState, content: Content) -> int:
    bw = content.balance.combat.blackwall
    return sum(1 for i in state.inventory if i.kind == "weapon" and i.key in bw)


def arms(state: CharacterState, content: Content) -> int:
    return scripts(state) + weapons(state, content)


def power(state: CharacterState, content: Content) -> int:
    c = cfg(content)
    return (scripts(state) * c.power_script + weapons(state, content) * c.power_weapon
            + cult_rank(state, content) * c.power_rank + rot(state) // max(1, c.power_rot_div)
            + (c.power_avatar if is_avatar(state) else 0))


def stage(state: CharacterState) -> int:
    """How many chain steps are done (0..5)."""
    return sum(1 for f in STEP_FLAGS if state.flags.get(f))


@dataclass
class Need:
    label: str
    have: int
    need: int

    @property
    def ok(self) -> bool:
        return self.have >= self.need


@dataclass
class NextStep:
    index: int                  # 0-based index in cfg.steps
    event: str
    title: str
    needs: list[Need] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return all(n.ok for n in self.needs)


def next_step(state: CharacterState, content: Content, city_total: int) -> NextStep | None:
    """The next chain step and its requirements; None when the chain is done (or the character is an Avatar)."""
    if is_avatar(state):
        return None
    c = cfg(content)
    i = stage(state)
    if i >= len(c.steps):
        return None
    s = c.steps[i]
    needs = []
    if s.rot:
        needs.append(Need("порча", rot(state), s.rot))
    if s.city:
        needs.append(Need("порча города", city_total, s.city))
    if s.power:
        needs.append(Need("мощь Заслона", power(state, content), s.power))
    if s.arms:
        needs.append(Need("скрипты и оружие Заслона", arms(state, content), s.arms))
    return NextStep(i, s.event, s.title, needs)


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    c = cfg(content)
    t = tier(state, content)
    if t > 0 and len(c.tier_mods) >= t:
        m = dict(c.tier_mods[t - 1])
        from app.engine import bwtree

        if bwtree.quiet(state):  # owner request 25.10: «Тишина за Заслоном» keeps the power, drops the price
            m = {k: v for k, v in m.items() if v > 0}
        yield "bw_rot", m
    if is_avatar(state) and c.avatar_mods:
        yield "bw_avatar", dict(c.avatar_mods)


def weekly_fx(state: CharacterState, content: Content) -> dict[str, int]:
    """The taint eats humanity every week (not with «Тишина за Заслоном» of the Blackwall branch)."""
    from app.engine import bwtree

    if bwtree.quiet(state):
        return {}
    c = cfg(content)
    if is_avatar(state):
        return {"humanity": c.avatar_weekly_humanity} if c.avatar_weekly_humanity else {}
    t = tier(state, content)
    h = c.weekly_humanity[min(t, len(c.weekly_humanity) - 1)] if c.weekly_humanity else 0
    return {"humanity": h} if h else {}


@dataclass
class CorruptPlan:
    ok: bool
    check: CheckResult | None
    effects: dict[str, int]
    text: str


def plan_corrupt(state: CharacterState, content: Content, rng: random.Random, way: str, level: int) -> CorruptPlan | None:
    """Taint the district the character is in: a check against a dv that grows with the district's level."""
    c = cfg(content)
    w = c.corrupt.get(way)
    if w is None:
        return None
    dv = c.corrupt_dv + c.corrupt_dv_per_level * level
    chk = checks.roll_check(state, content, rng, w.stat, w.skill, dv, extra=tier(state, content))
    if chk.ok:
        fx = {"bw_rot": c.corrupt_rot, "heat": c.corrupt_heat}
        if in_cult(state):
            fx["bw_favor"] = c.corrupt_favor
        return CorruptPlan(True, chk, fx, w.text)
    return CorruptPlan(False, chk, {"hp": -c.corrupt_fail_hp, "heat": c.corrupt_heat}, w.fail_text)


def whisper(state: CharacterState, content: Content, rng: random.Random) -> str | None:
    """Owner request: the Blackwall is always near. A rare line in the menu, more often with the taint."""
    c = cfg(content)
    if not c.whispers:
        return None
    p = c.whisper_base + c.whisper_per_rot * rot(state) + (0.1 if is_avatar(state) else 0)
    return rng.choice(c.whispers) if rng.random() < p else None


def ai_power_bonus(state: CharacterState, content: Content) -> int:
    """The AI born from an Avatar keeps the power it gathered."""
    return power(state, content) + scripts(state) * cfg(content).ai_power_per_script

