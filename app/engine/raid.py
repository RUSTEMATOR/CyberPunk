"""The city raid (docs/EPIC_PLAYERS.md, MMO part): one boss, one hp scale for the whole group. Pure functions."""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.content.loader import Content
from app.engine import checks
from app.engine.checks import CheckResult
from app.engine.dice import roll
from app.engine.state import CharacterState

SHOOT, MELEE, HACK, SUPPORT = "shoot", "melee", "hack", "support"
APPROACHES: tuple[str, ...] = (SHOOT, MELEE, HACK, SUPPORT)
CHECKS: dict[str, tuple[str, str]] = {
    SHOOT: ("REF", "firearms"), MELEE: ("BODY", "melee"), HACK: ("INT", "netrunning"), SUPPORT: ("TECH", "medicine"),
}
CONTEXTS: dict[str, tuple[str, ...]] = {
    SHOOT: ("combat",), MELEE: ("combat",), HACK: ("hack",), SUPPORT: (),
}


@dataclass
class Hit:
    approach: str
    check: CheckResult
    damage: int          # what the boss lost
    weak: bool           # the boss is weak to this approach
    hp_loss: int         # the counterattack on a miss (limited to keep 1 hp)
    supported: int       # the support bonus that went into this check


def boss_hp(content: Content, players: int) -> int:
    cfg = content.balance.players.raid
    return cfg.hp_per_player * max(cfg.min_players, players)


def attack(
    state: CharacterState, content: Content, rng: random.Random, approach: str, weak: str, support: int,
) -> Hit:
    """One attack: stat + skill + d10 (+ the support bonus) against the raid dv.

    A pass deals base + per_margin x margin, x crit_mult on a crit, x weak_mult on the boss's weakness. A miss still
    scratches it (miss_damage) and the boss hits back (fail_hp_dice, never below 1 hp). Support deals half damage:
    its value is the bonus it leaves for the others."""
    cfg = content.balance.players.raid
    stat, skill = CHECKS[approach]
    res = checks.roll_check(state, content, rng, stat, skill, cfg.dv, CONTEXTS[approach], extra=support)
    is_weak = approach == weak
    if res.ok:
        dmg = cfg.base_damage + cfg.per_margin * max(0, res.margin)
        if res.crit == "success":
            dmg *= cfg.crit_mult
    else:
        dmg = cfg.miss_damage
    if approach == SUPPORT:
        dmg = dmg // 2
    if is_weak:
        dmg = int(dmg * cfg.weak_mult)
    hp_loss = 0 if res.ok else min(roll(cfg.fail_hp_dice, rng), max(0, state.hp - 1))
    return Hit(approach, res, max(1, dmg), is_weak, hp_loss, support)


@dataclass
class Share:
    tg: int
    name: str
    char: int | None
    dmg: int
    eddies: int
    cred: int
    top: bool


def shares(content: Content, hits: dict[str, dict], won: bool) -> list[Share]:
    """The reward of every attacker by his share of the damage; the best gets the top bonus (only on a win)."""
    cfg = content.balance.players.raid
    def pts(h: dict) -> int:
        return int(h.get("pts", h.get("dmg", 0)))

    total = sum(pts(h) for h in hits.values()) or 1
    full = cfg.reward_per_player * len(hits)
    pool = full if won else int(full * cfg.escape_share)
    best = max(hits.items(), key=lambda kv: pts(kv[1]))[0] if hits else None
    out: list[Share] = []
    for tg, h in sorted(hits.items(), key=lambda kv: -pts(kv[1])):
        top = won and tg == best
        out.append(Share(
            int(tg), str(h.get("name", "")), h.get("char"), int(h.get("dmg", 0)),
            eddies=pool * pts(h) // total,
            cred=(cfg.reward_cred + (cfg.top_cred if top else 0)) if won else 0, top=top,
        ))
    return out
