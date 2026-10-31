"""Relations as a service (PLAN 9.1-9.2): one action on one NPC or fixer, AP, effects, log, bus.

The NPC is picked by its index in `state.npcs`; the screen keeps the name next to it, so a list that
changed since the button was drawn is noticed (`find`). Everything changes the character through
`apply_effects` / `apply_npc_relation`; AP through `services.ap`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.content.schemas import SKILLS
from app.db import repo
from app.engine import relations as rel
from app.engine.effects import AppliedEffects, apply_effects, apply_npc_relation
from app.engine.state import FLAG_PARTNER_MOVED_IN, NpcState
from app.services import ap as ap_services
from app.services import followups
from app.services import game_events as bus
from app.services import romance
from app.services.game import ActiveCharacter, save_active


@dataclass
class RelResult:
    action: str
    blocked: str | None = None
    no_ap: bool = False
    npc: str = ""
    kind: str = ""
    other: str = ""
    rel_delta: int = 0
    money: int = 0
    enemy: bool = False
    applied: AppliedEffects | None = None
    skill: str = ""  # teach: the skill the lesson was about
    lines: list[str] = field(default_factory=list)  # followup lines
    died: bool = False
    death_cause: str | None = None

    @property
    def done(self) -> bool:
        return self.blocked is None and not self.no_ap


def find(ch: ActiveCharacter, idx: int, name: str | None = None) -> NpcState | None:
    """The NPC at `idx` (and with this name, when given), else None."""
    if not 0 <= idx < len(ch.state.npcs):
        return None
    npc = ch.state.npcs[idx]
    return npc if name is None or npc.name == name else None


async def perform(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    idx: int, action: str, *, other_idx: int | None = None,
) -> RelResult:
    st = ch.state
    npc = find(ch, idx)
    res = RelResult(action)
    if npc is None:
        res.blocked = rel.BLOCK_DEAD
        return res
    res.npc, res.kind = npc.name, npc.kind
    other = find(ch, other_idx) if other_idx is not None else None
    if action not in rel.ACTIONS_BY_KIND.get(npc.kind, ()) and not (
        action == rel.MOVE_OUT and npc.kind == "partner"
    ):
        res.blocked = rel.BLOCK_DEAD
        return res
    if action == rel.INTRO and (other is None or other is npc or not other.alive):
        res.blocked = rel.BLOCK_NO_OTHER
        return res
    res.blocked = rel.blocked(st, content, action, npc, other=other)
    if res.blocked is not None:
        return res
    p = rel.plan(st, content, rng, action, npc)
    if not ap_services.can_spend(ch, p.ap):
        res.no_ap = True
        return res
    ap_services.spend(ch, content, p.ap)
    res.applied = apply_effects(st, dict(p.effects), content, rng, year)
    res.rel_delta = apply_npc_relation(st, content, npc, p.rel) if p.rel else 0
    if p.other_rel and other is not None:
        apply_npc_relation(st, content, other, p.other_rel)
        res.other = other.name
    npc.data.update(p.data)
    if npc.kind == "partner" and action in romance.TOGETHER:
        romance.mark_seen(st, npc)
    res.money, res.enemy = p.money, p.enemy
    if p.moved_in is not None:
        if p.moved_in:
            st.flags[FLAG_PARTNER_MOVED_IN] = True
        else:
            st.flags.pop(FLAG_PARTNER_MOVED_IN, None)
    if action == rel.TEACH:
        res.skill = str(npc.data.get("skill", ""))
        if res.skill in SKILLS:
            gain = apply_effects(st, {f"skill_xp:{res.skill}": content.balance.relations.protege_lesson_xp},
                                 content, rng, year)
            res.applied.skill_xp.update(gain.skill_xp)
            res.applied.level_ups.extend(gain.level_ups)
    if p.remove:
        st.npcs.remove(npc)
    fu = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
    res.lines, res.died, res.death_cause = fu.lines, fu.died, fu.cause
    if fu.died:
        return res
    await save_active(session, ch, tz)
    await repo.log_event(
        session, "relation", {"action": action, "npc": res.npc, "kind": res.kind, "rel": res.rel_delta},
        character_id=ch.id,
    )
    if p.bus:
        await bus.publish(p.bus, player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch)
    return res


async def gift_fixer(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    fixer_key: str,
) -> RelResult:
    """A gift to a fixer: the same price and effect as a gift to a friend, on `fixer_rel`."""
    cfg = content.balance.relations
    res = RelResult("gift", kind="fixer", npc=fixer_key)
    if ch.state.eddies < cfg.gift_cost:
        res.blocked = rel.BLOCK_EDDIES
        return res
    if rel.in_jail(ch.state):
        res.blocked = rel.BLOCK_JAIL
        return res
    if not ap_services.can_spend(ch, cfg.action_ap):
        res.no_ap = True
        return res
    ap_services.spend(ch, content, cfg.action_ap)
    res.applied = apply_effects(
        ch.state, {"eddies": -cfg.gift_cost, "mood": cfg.gift_mood, f"rel_fixer:{fixer_key}": cfg.gift_rel},
        content, rng, year,
    )
    res.rel_delta = res.applied.fixer_rel.get(fixer_key, 0)
    await save_active(session, ch, tz)
    await repo.log_event(session, "relation", {"action": "gift", "fixer": fixer_key}, character_id=ch.id)
    return res


# -- the protege (PLAN 9.2) ---------------------------------------------------------------------------------

PRO_CRED = "cred"
PRO_HAVE = "have"


def protege_of(ch: ActiveCharacter) -> tuple[int, NpcState] | None:
    for i, n in enumerate(ch.state.npcs):
        if n.kind == "protege" and n.alive:
            return i, n
    return None


def protege_blocked(ch: ActiveCharacter, content: Content) -> str | None:
    cfg = content.balance.relations
    if rel.in_jail(ch.state):
        return rel.BLOCK_JAIL
    if protege_of(ch) is not None:
        return PRO_HAVE
    if ch.state.street_cred < cfg.protege_min_cred:
        return PRO_CRED
    return None


async def take_protege(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> RelResult:
    """Take a young NPC as a student: needs street cred, one protege at a time, `protege_ap`."""
    cfg = content.balance.relations
    res = RelResult("take")
    res.blocked = protege_blocked(ch, content)
    if res.blocked is not None:
        return res
    if not ap_services.can_spend(ch, cfg.protege_ap):
        res.no_ap = True
        return res
    ap_services.spend(ch, content, cfg.protege_ap)
    res.applied = apply_effects(ch.state, {"add_npc:protege": True}, content, rng, year)
    npc = res.applied.npcs_added[0]
    npc.data["age"] = rng.randint(cfg.protege_age_min, cfg.protege_age_max)
    npc.data["skill"] = rng.choice(SKILLS)
    npc.data["lessons"] = 0
    res.npc, res.kind, res.skill = npc.name, npc.kind, str(npc.data["skill"])
    await save_active(session, ch, tz)
    await repo.log_event(session, "relation", {"action": "take_protege", "npc": npc.name}, character_id=ch.id)
    return res


async def adopt(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str) -> RelResult:
    """Owner request 25.10: a child without a partner: the orphanage, a fee and an AP."""
    cfg = content.balance.relations
    st = ch.state
    res = RelResult("adopt", kind="child")
    res.blocked = rel.baby_blocked(st, content, cfg.adopt_cost)
    if res.blocked is not None:
        return res
    if not ap_services.can_spend(ch, cfg.adopt_ap):
        res.no_ap = True
        return res
    ap_services.spend(ch, content, cfg.adopt_ap)
    res.applied = apply_effects(st, {"add_npc:child": True, "eddies": -cfg.adopt_cost, "mood": cfg.baby_mood},
                                content, rng, year)
    kid = res.applied.npcs_added[-1] if res.applied.npcs_added else None
    if kid is not None:
        age = rng.randint(*cfg.adopt_age)
        kid.data["born_week"] = st.weeks_lived - age * content.balance.time.weeks_per_year
        kid.data["adopted"] = True
        res.npc = kid.name
        res.money = age
    await save_active(session, ch, tz)
    await repo.log_event(session, "relation", {"action": "adopt", "npc": res.npc}, character_id=ch.id)
    await bus.publish("family_time", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch)
    return res
