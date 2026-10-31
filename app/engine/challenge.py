"""Challenges (owner request 25.10): a fighter who outgrew the city meets threats that double every time.

Pure. `power` measures a build by simulating plain fights against the hardest enemies of the game (Militech and MaxTac
at danger 5): the share won and the health left. `scale` makes the enemies of a challenge of level n: health ×2^(n-1),
to-hit +2 and damage +3 per level, and the counter Claude picked against the build:
  pierce  — their hits ignore armor and damage reduction;
  swarm   — two more fighters per level (up to 8 in all);
  jammer  — the player's attacks −3 the whole fight (optics jammed);
  stun    — every enemy hit may stun the player (20%);
  heavy   — their damage ×1.5.
"""

from __future__ import annotations

import random
import statistics
from typing import Any

from app.content.loader import Content
from app.engine import combat as cb
from app.engine.state import CharacterState

COUNTERS = ("pierce", "swarm", "jammer", "stun", "heavy")
COUNTER_RU = {"pierce": "бронебойные: броня и снижение урона не помогают",
              "swarm": "толпа: бойцов больше с каждым уровнем",
              "jammer": "глушилки: твои атаки −3 весь бой",
              "stun": "шокеры: каждое их попадание может оглушить",
              "heavy": "тяжёлое оружие: урон в полтора раза выше"}
SIM_FOES = (("militech", 5), ("maxtac", 5))


def _auto_fight(state: CharacterState, content: Content, faction: str, danger: int, rng: random.Random,
                year: int) -> tuple[bool, float]:
    from app.engine import perks

    ctx = {"perks": perks.specials(state), "chrome_n": len(state.cyberware), "solo": True}
    cs = cb.new_combat(state, content, faction, danger, rng, year, ctx)
    kind = cb.A_SHOOT if cb.RANGED in cs.player.attacks else cb.A_MELEE
    for _ in range(40):
        if cs.outcome:
            break
        if cs.reinforced:
            cb.resolve_tick(cs, content, rng, cb.PlayerAction(kind=cb.A_WAIT))
            continue
        tgt = next((i for i, e in enumerate(cs.enemies) if e.active), 0)
        try:
            cb.resolve_tick(cs, content, rng, cb.PlayerAction(kind=kind, target=tgt))
        except cb.CombatError:
            break
    return cs.outcome == cb.WON, max(0, cs.player.hp) / max(1, cs.player.max_hp)


def power(state: CharacterState, content: Content, year: int, runs: int = 12, seed: int = 0) -> dict[str, Any]:
    """{'win': share of plain fights won against the hardest enemies, 'hp': health left on average, 'score': 0..200}."""
    wins: list[float] = []
    hps: list[float] = []
    for faction, danger in SIM_FOES:
        for i in range(runs):
            won, hp = _auto_fight(state, content, faction, danger, random.Random(seed * 1000 + i), year)
            wins.append(1.0 if won else 0.0)
            hps.append(hp)
    win, hp = statistics.mean(wins), statistics.mean(hps)
    return {"win": round(win, 2), "hp": round(hp, 2), "score": int(100 * win + 100 * hp)}


def overpowered(report: dict[str, Any], content: Content) -> bool:
    c = content.balance.challenge
    return report.get("score", 0) >= c.score_from


def build_summary(state: CharacterState, content: Content) -> dict[str, Any]:
    """What Claude sees of the build: the numbers that make it strong."""
    from app.engine import derived, perks

    p = cb.build_player(state, content)
    return {
        "hp": p.max_hp, "armor": cb.armor_effect(p, content), "damage_reduction": p.damage_reduction,
        "attack_bonus": p.check_bonus,
        "attacks": {k: a.dice for k, a in p.attacks.items() if k in (cb.RANGED, cb.MELEE)},
        "weapons": [content.weapons[k].name for k in p.arsenal if k in content.weapons],
        "implants": len(state.cyberware), "level": int(perks.rec(state, content).get("level", 1)),
        "perks": list(perks.specials(state)), "humanity": state.humanity,
        "melee_damage": derived.mod(state, content, "melee_damage"),
        "ranged_damage": derived.mod(state, content, "ranged_damage"),
    }


def mult(level: int) -> int:
    return 2 ** max(0, level - 1)


def scale(cs: cb.CombatState, content: Content, level: int, counter: str, rng: random.Random, year: int,
          foe: str = "") -> None:
    """Turn a fresh fight into the challenge of `level`; the strongest enemy gets the name of the boss."""
    if isinstance(cs.ctx.get("elite"), dict):  # owner request 25.10: an elite fight sets its own numbers
        return
    c = content.balance.challenge
    m = mult(level)
    extra = level - 1
    if foe and cs.enemies:
        max(cs.enemies, key=lambda e: e.max_hp).name = foe
    if counter == "swarm" and cs.enemies:
        for i in range(min(2 * level, c.swarm_max - len(cs.enemies))):
            pack = cb.build_pack(cs.faction, cs.danger, rng, content, year)
            e = cb.build_enemy(pack[-1], len(cs.enemies), content, f"Боец вызова {len(cs.enemies) + 1}")
            cs.enemies.append(e)
        cs.pack_size = len(cs.enemies)
    for e in cs.enemies:
        e.max_hp = e.hp = int(e.max_hp * m)
        for a in e.attacks.values():
            a.base += 2 * extra
            bonus = 3 * extra
            if bonus:
                a.dice = f"{a.dice}+{bonus}" if "+" not in a.dice else a.dice.split("+")[0] + f"+{int(a.dice.split('+')[1]) + bonus}"
        e.armor += extra
    cs.ctx["challenge"] = {"level": level, "counter": counter}
    if counter == "jammer":
        cs.player.check_bonus -= 3


# -- owner request 25.10 (2): three stages, the rich get theirs ------------------------------------------------------------

FIGHT_STAGES = ("fight", "track", "boss")
WEALTH_STAGES = ("assets", "hit", "board")
STAGE_RU = {"fight": "Бой с отрядом", "track": "Выследить главаря", "boss": "Бой с главарём",
            "assets": "Удар по активам", "hit": "Покушение", "board": "Совет или суд"}


def empire_step(state: CharacterState) -> int:
    return int(state.flags.get("empire", 0) or 0)


def wealthy(state: CharacterState, content: Content) -> bool:
    """Rich enough for a challenge of the fortune: money on hand, a board seat or one's own megacorp."""
    c = content.balance.challenge
    steps = [s.key for s in content.balance.economy.empire]
    board = steps.index("board") + 1 if "board" in steps else 99
    return state.eddies >= c.wealth_from or empire_step(state) >= board


def wealth_summary(state: CharacterState, content: Content) -> dict[str, Any]:
    steps = content.balance.economy.empire
    n = empire_step(state)
    from app.engine import biz

    return {"eddies": state.eddies, "empire": steps[n - 1].name if 0 < n <= len(steps) else "",
            "businesses": [b.get("name") or b.get("kind") for b in biz.list_of(state)][:6], "debt": state.debt_total}


def check_dv(content: Content, level: int) -> int:
    c = content.balance.challenge
    return min(c.check_dv_cap, c.check_dv + 2 * level)


def daily_hit(content: Content, track: str, level: int) -> dict[str, int]:
    """What the open challenge takes every day, by mail."""
    c = content.balance.challenge
    if track == "wealth":
        return {"pct": min(c.w_pct_cap, c.w_pct + level)}  # a positive pct is a loss (services/players.py)
    return {"hp": -min(c.hit_hp_cap, c.hit_hp * level), "eddies": -min(c.hit_eddies_cap, c.hit_eddies * mult(level))}


def escrow(content: Content, level: int, eddies: int) -> int:
    c = content.balance.challenge
    return max(0, eddies * min(c.escrow_cap_pct, c.escrow_pct * mult(level)) // 100)


def pay_price(content: Content, level: int, stage: str) -> int:
    c = content.balance.challenge
    return c.w_pay * mult(level) * (2 if stage == "board" else 1)


def fail_pct(content: Content, level: int) -> int:
    c = content.balance.challenge
    return min(c.w_fail_cap, c.w_fail_pct + 2 * (level - 1))
