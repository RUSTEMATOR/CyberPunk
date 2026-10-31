"""Enemy decisions (PLAN 5.5b): a pure function `(combat state, enemy index, rng) -> EnemyAction`.

Profiles (`Enemy.ai`, numbers in `balance.enemy_ai`):
  brute     turns every implant on as soon as the charge allows, never cancels; attacks.
  tactical  turns duration implants on when its hp falls below a share of max; cancels a nearly empty
            implant early when the player runs nothing; attacks.
  netrunner stays at the back (the defense bonus is in the Combatant); hacks while RAM lasts, then shoots.
  coward    attacks, but runs below a share of max hp.

Free actions (implants) go first, the main action after. Whether an op can really be done is checked by
`engine/combat.py` when it is applied; a refused op is skipped.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.content.loader import Content
from app.engine import combat as cb
from app.engine import dice

FreeOp = tuple[str, str, int]  # (activate | cancel | shot, implant key, shot count)


@dataclass
class EnemyAction:
    free: list[FreeOp] = field(default_factory=list)
    kind: str = "attack"  # attack | hack | flee
    attack: str = cb.RANGED  # ranged | melee, for kind=attack
    program: str = ""  # quickhack key, for kind=hack


def _main_attack(e: cb.Combatant) -> str:
    return cb.RANGED if cb.RANGED in e.attacks else cb.MELEE


def _implant_ops(cs: cb.CombatState, e: cb.Combatant, content: Content, profile: str) -> list[FreeOp]:
    ai = content.balance.enemy_ai
    ops: list[FreeOp] = []
    player_runs = any(i.running for i in cs.player.implants)
    for imp in e.implants:
        spec = cb.spec_of(e, imp, content)
        if spec.mode == "duration":
            if imp.running:
                if (profile == "tactical" and spec.can_cancel and imp.charge < ai.tactical.cancel_charge_below
                        and not player_runs):
                    ops.append(("cancel", imp.key, 0))
                continue
            if cb.activation_block(e, imp, content):
                continue
            if profile == "brute" or profile == "netrunner" or profile == "coward":
                ops.append(("activate", imp.key, 0))
            elif profile == "tactical" and (e.hp < e.max_hp * ai.tactical.activate_hp_below_pct or player_runs):
                ops.append(("activate", imp.key, 0))
        elif spec.mode == "shot":
            if not cb.shot_block(e, imp, content, 1):
                ops.append(("shot", imp.key, min(spec.effects.targets, imp.charge // spec.cost) or 1))
    return ops


def decide(cs: cb.CombatState, idx: int, content: Content, rng: random.Random) -> EnemyAction:
    e = cs.enemies[idx]
    ai = content.balance.enemy_ai
    action = EnemyAction(free=_implant_ops(cs, e, content, e.ai), attack=_main_attack(e))
    if e.ai == "coward" and e.hp < e.max_hp * ai.coward.flee_hp_below_pct:
        action.kind = "flee"
        return action
    if e.ai == "netrunner":
        options = [
            k for k in e.quickhacks
            if k in content.quickhacks
            and cb.hack_block(e, k, cs.player, content) == ""
        ]
        if options:
            action.kind = "hack"
            # first target: a running implant of the player (drain it or switch it off)
            if any(i.running for i in cs.player.implants):
                hit = [k for k in options if content.quickhacks[k].effects.drain_charge
                       or content.quickhacks[k].effects.disable_ticks]
                options = hit or options
            action.program = dice.choice(rng, options)
    return action
