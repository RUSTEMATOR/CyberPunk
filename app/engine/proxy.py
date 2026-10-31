"""A hired player does the employer's gig in his place (owner request 29.10, docs/PLAN_PROXY.md). Pure rules: who gets
which part of a gig's result, what the hired player is paid for his time, and the automatic fight."""

from __future__ import annotations

import random
from typing import Any

from app.content.loader import Content
from app.engine import combat as cb
from app.engine import pally as pally_eng
from app.engine.state import CharacterState

# Rewards that go to the employer when positive; a loss of the same kind stays with the one who worked.
REWARD_KEYS = {"eddies", "street_cred", "fame", "status"}
# Only the hired player lives through these.
WORKER_KEYS = {"hp", "humanity", "mood"}


def split_effects(fx: dict[str, int | bool]) -> tuple[dict[str, int | bool], dict[str, int | bool]]:
    """(employer's part, hired player's part) of the effects of a gig.

    Money, cred, fame and the fixer's opinion are the employer's (his gig, his fixer); the hired player takes the wounds,
    lost humanity, mood, heat, the fall of a faction's opinion and the experience of the skills he used."""
    boss: dict[str, int | bool] = {}
    hand: dict[str, int | bool] = {}
    for k, v in fx.items():
        if isinstance(v, bool):
            hand[k] = v
            continue
        n = int(v)
        if not n:
            continue
        base = k.split(":", 1)[0]
        if base in WORKER_KEYS or base == "skill_xp":
            hand[k] = n
        elif base == "heat":
            (hand if n > 0 else boss)[k] = n
        elif base == "rel_fixer":
            boss[k] = n
        elif base in REWARD_KEYS or base == "faction_rep":
            (boss if n > 0 else hand)[k] = n
        else:
            (boss if n > 0 else hand)[k] = n
    return boss, hand


def days_of(content: Content, tier: int) -> int:
    d = content.balance.players.proxy.days
    return d[max(1, min(len(d), tier)) - 1]


def fee(content: Content, wage_month: int, tier: int) -> int:
    """The pay of the hired player for the time of the sending: a month's wage in proportion to the days of work."""
    c = content.balance.players.proxy
    return max(c.fee_min, wage_month * days_of(content, tier) // 30)


def rec_of(st: CharacterState) -> dict[str, Any]:
    """How the hired player picks skills when nobody gives orders: his own mode and fight style (as in the employer's fight)."""
    mode = str(st.flags.get(pally_eng.FLAG_MODE) or pally_eng.PLAY)
    return {"mode": mode if mode in pally_eng.MODES else pally_eng.PLAY,
            "style": dict(st.flags.get(pally_eng.FLAG_STYLE) or {}), "seq": 0}


def _action(cs: cb.CombatState, content: Content, rng: random.Random, rec: dict[str, Any]) -> cb.PlayerAction:
    p = cs.player
    foes = cs.active_enemies()
    if not foes:
        return cb.PlayerAction(cb.A_WAIT)
    c = content.balance.players.proxy
    if p.hp * 100 < p.max_hp * c.heal_hp_pct and cb.med_left(cs, content) > 0 and p.hp < p.max_hp:
        return cb.PlayerAction(cb.A_HEAL)
    skill = pally_eng.choose(cs, p, rec, content, rng) or ""
    kind, _, key = skill.partition(":")
    if kind == "imp":
        try:
            cb.activate(cs, p, key, content)
        except cb.CombatError:
            pass
        skill = cb.RANGED if cb.RANGED in p.attacks else cb.MELEE
    if kind == "hack":
        idx = foes[rng.randrange(len(foes))][0]
        return cb.PlayerAction(cb.A_HACK, target=idx, program=key)
    if skill == cb.RANGED and cb.RANGED in p.attacks:
        return cb.PlayerAction(cb.A_SHOOT, target=foes[0][0])
    if cb.MELEE in p.attacks:
        return cb.PlayerAction(cb.A_MELEE, target=foes[0][0])
    if cb.RANGED in p.attacks:
        return cb.PlayerAction(cb.A_SHOOT, target=foes[0][0])
    return cb.PlayerAction(cb.A_WAIT)


def auto_fight(cs: cb.CombatState, content: Content, rng: random.Random, rec: dict[str, Any]) -> dict[str, Any]:
    """Play the fight to its end without a player. Retreats below `retreat_hp_pct`; never kills the hired player
    (a downed one keeps 1 health and the fight counts as lost)."""
    c = content.balance.players.proxy
    for imp in cs.player.implants:  # reactive and duration chrome the character carries is switched on at the start
        try:
            if cb.spec_of(cs.player, imp, content).mode == "duration":
                cb.activate(cs, cs.player, imp.key, content)
        except cb.CombatError:
            pass
    ticks = 0
    worst = 0  # the hardest tick of damage so far: the hired player pulls out before the next one can finish him
    while not cs.outcome and ticks < c.max_ticks:
        p = cs.player
        can_heal = cb.med_left(cs, content) > 0 and p.hp < p.max_hp
        low = p.hp * 100 < p.max_hp * c.retreat_hp_pct or (worst > 0 and p.hp <= worst * 1.1)
        if low and not can_heal:
            cb.finish(cs, cb.FLED)
            break
        before = cs.taken
        try:
            cb.resolve_tick(cs, content, rng, cb.PlayerAction(cb.A_HEAL) if low else _action(cs, content, rng, rec))
        except cb.CombatError:
            try:
                cb.resolve_tick(cs, content, rng, cb.PlayerAction(cb.A_WAIT))
            except cb.CombatError:
                break
        worst = max(worst, cs.taken - before)
        ticks += 1
    if not cs.outcome:  # the fight dragged on: the hired player pulls out
        cb.finish(cs, cb.FLED)
    return cb.summary(cs)
