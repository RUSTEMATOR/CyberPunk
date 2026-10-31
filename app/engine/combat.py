"""Combat by ticks (PLAN 5.5a, 5.6; DESIGN.md sections 6 and 8).

Player and enemies are one type, `Combatant`; implants, charge, quickhacks and modifiers are
computed by the same code for both. What differs is who picks the action: the player by buttons,
an enemy by `engine/enemy_ai.py`.

Pure: no clock, no DB, no Telegram, randomness only through the `rng`. The state of a fight is a
`CombatState` (JSON-safe via `to_dict` / `from_dict`); the service keeps it on the character and
applies hp, humanity and charge to the character after every step (through `apply_effects`).

The engine writes no player text: it records structured events (`cs.cur_events`, `cs.log`) and the
view layer turns them into Russian.

One tick:
 1. the player's free actions (implants on / off, rockets), any number, no tick passes;
 2. the main action: attack, cover, quickhack, or a failed exit; running implants burn their charge
    first (the effect works in the tick where the charge was burned), then the actions;
 3. kills and takedowns recharge implants; morale; the enemies act (same code); end of the tick:
    recharge of idle implants, RAM, statuses; after `combat.max_rounds` ticks the reinforcements come.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

from app.content.loader import Content
from app.content.schemas import ActiveSpec, Enemy, Quickhack
from app.engine import derived, dice
from app.engine.dice import clamp, half_up
from app.engine.state import CharacterState

PLAYER_ID = "p"
RANGED, MELEE, MELEE_NL = "ranged", "melee", "melee_nl"
OUT_KILLED, OUT_TAKEN, OUT_FLED = "killed", "taken", "fled"

# Fight results (balance.gigs.after_fight keys): won / fled / avoided / surrendered / downed.
WON, FLED, AVOIDED, SURRENDERED, DOWNED = "won", "fled", "avoided", "surrendered", "downed"

# Main actions of a tick.
A_SHOOT, A_MELEE, A_COVER, A_HACK, A_EXIT_FAIL, A_WAIT = "shoot", "melee", "cover", "hack", "exit_fail", "wait"
A_GUARD = "guard"  # owner request 25.10: the elite fight, take the blows meant for the person under protection
A_HEAL = "heal"  # owner request 24.10: the med injector

MAX_LOG_TICKS = 3  # full ticks kept; older ones are folded into `old`
SPECIAL_FACTIONS = {"maxtac": "ncpd"}  # exit-table factions that fight with another faction's archetypes


class CombatError(Exception):
    """A button that cannot work right now. `reason` is a key for the view."""

    def __init__(self, reason: str, **info: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.info = info


# -- data ---------------------------------------------------------------------------------------------------


@dataclass
class ImplantState:
    key: str
    charge: int = 100
    running: bool = False
    auto: bool = True  # reactive implants: the auto trigger (a switch only where `trigger_toggle`)
    disabled_ticks: int = 0  # switched off by a hack
    ran: bool = False  # worked this tick: it does not recharge in it


@dataclass
class Attack:
    kind: str  # ranged | melee
    source: str  # weapon key, implant key, or "" for fists
    dice: str
    base: int  # player: stat + skill; enemy: the archetype's attack
    attempts: int = 1
    close_only: bool = False
    lethal: bool = True
    targets: int = 1  # melee implants like the monowire hit up to this many enemies per attempt


@dataclass
class Combatant:
    id: str
    name: str
    side: str  # player | enemy
    danger: int
    hp: int
    max_hp: int
    archetype: str = ""
    ai: str = ""
    leader: bool = False
    defense: int = 10  # dv to hit it (the player: base + REF)
    armor: int = 0  # armor value, before combat.armor_effect_pct
    damage_reduction: int = 0
    ice: int = 0
    hack_base: int = 0  # player: INT + netrunning (+ deck bonus) = the quickhack roll and the defense roll
    check_bonus: int = 0  # check.combat
    ranged_damage: int = 0
    melee_damage: int = 0
    attacks: dict[str, Attack] = field(default_factory=dict)
    # owner report 23.10: every weapon at hand by its key, so the player picks what to shoot or strike with
    arsenal: dict[str, Attack] = field(default_factory=dict)
    has_chrome: bool = True  # quickhacks other than environmental need chrome to grab
    implants: list[ImplantState] = field(default_factory=list)
    ram: int = 0
    ram_max: int = 0
    ram_recharge: int = 0
    quickhack_cost: int = 0  # mod, negative = cheaper
    quickhack_targets: int = 0  # mod, extra targets
    quickhacks: list[str] = field(default_factory=list)
    mods: dict[str, int] = field(default_factory=dict)
    # statuses
    out: str = ""  # killed | taken | fled
    stun: int = 0
    penalty: int = 0
    penalty_ticks: int = 0
    next_bonus: int = 0  # bonus to the next attack (a reactive implant fired)
    cover: bool = False
    humanity: int = 0  # player only
    wound_ratio: float = 0.5
    wound_penalty: int = 0
    scan: bool = False  # player only: the optics read enemy chrome
    # owner request 25.10: the elite fight (engine/elite.py)
    role: str = ""         # shield | sniper | medic | boss
    intent: str = ""       # what it does on the next tick
    barrier: int = 0
    barrier_max: int = 0
    phase: int = 0
    resist: str = ""       # ranged | melee | hack: half damage of this kind
    aim: int = 0

    @property
    def is_player(self) -> bool:
        return self.side == "player"

    @property
    def active(self) -> bool:
        """Still in the fight: not killed, knocked out or fled (and, for the player, still on his feet)."""
        return not self.out and self.hp > 0

    def implant(self, key: str) -> ImplantState | None:
        for imp in self.implants:
            if imp.key == key:
                return imp
        return None


@dataclass
class CombatState:
    faction: str
    danger: int
    player: Combatant
    enemies: list[Combatant]
    tick: int = 1
    nonlethal: bool = False
    outcome: str = ""  # "" while the fight goes on, else one of the results
    reinforced: bool = False
    shot_used: bool = False
    pack_size: int = 0
    enemy_bonus: int = 0  # attack bonus of the enemies (a failed talk) ...
    enemy_bonus_ticks: int = 0  # ... for this many ticks
    kills: int = 0
    takedowns: int = 0
    dealt: int = 0
    taken: int = 0
    used: dict[str, int] = field(default_factory=lambda: {RANGED: 0, MELEE: 0, "hack": 0})
    humanity_due: int = 0  # overload cost not yet charged to the character
    cur_events: list[dict[str, Any]] = field(default_factory=list)
    cur_dealt: int = 0
    cur_taken: int = 0
    log: list[dict[str, Any]] = field(default_factory=list)
    old: dict[str, int] = field(default_factory=dict)  # ticks folded away: from, to, dealt, taken
    ctx: dict[str, Any] = field(default_factory=dict)  # who started the fight (gig, event) and the intro note
    allies: list[Combatant] = field(default_factory=list)  # owner request 24.10: hired NPCs who fight at your side
    med_max: int = 0                                     # owner request 24.10: med charges of this fight
    med_used: list[int] = field(default_factory=list)    # ticks a charge was used; it comes back after recharge ticks

    # -- helpers -------------------------------------------------------------------------------------------

    def active_enemies(self) -> list[tuple[int, Combatant]]:
        return [(i, e) for i, e in enumerate(self.enemies) if e.active]

    def combatant(self, cid: str) -> Combatant:
        if cid == PLAYER_ID:
            return self.player
        if cid.startswith("a"):
            return self.allies[int(cid[1:])]
        return self.enemies[int(cid[1:])]

    def active_allies(self) -> list[Combatant]:
        return [a for a in self.allies if a.active]

    @property
    def over(self) -> bool:
        return bool(self.outcome)


def to_dict(cs: CombatState) -> dict[str, Any]:
    return asdict(cs)


def _load(cls: type, d: dict[str, Any]) -> Any:
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


def _load_combatant(d: dict[str, Any]) -> Combatant:
    d = dict(d)
    d["attacks"] = {k: _load(Attack, v) for k, v in (d.get("attacks") or {}).items()}
    d["arsenal"] = {k: _load(Attack, v) for k, v in (d.get("arsenal") or {}).items()}
    d["implants"] = [_load(ImplantState, v) for v in d.get("implants") or []]
    return _load(Combatant, d)


def from_dict(d: dict[str, Any]) -> CombatState:
    d = dict(d)
    d["player"] = _load_combatant(d["player"])
    d["enemies"] = [_load_combatant(e) for e in d.get("enemies") or []]
    d["allies"] = [_load_combatant(e) for e in d.get("allies") or []]
    return _load(CombatState, d)


# -- building the pack and the fighters (PLAN 5.5a) ---------------------------------------------------------


def _faction_pool(content: Content, faction: str, year: int) -> list[Enemy]:
    fac = SPECIAL_FACTIONS.get(faction, faction)
    pool = [e for e in content.available("enemies", year).values() if fac in e.factions]
    if faction == "maxtac":
        pool = [e for e in pool if e.danger >= 4]
    return pool


def _nearest(pool: list[Enemy], danger: int, rng: random.Random) -> list[Enemy]:
    """Archetypes at the danger level, else the closest lower level, else the closest higher."""
    if not pool:
        return []
    for d in range(danger, 0, -1):
        hit = [e for e in pool if e.danger == d]
        if hit:
            return hit
    low = min(e.danger for e in pool)
    return [e for e in pool if e.danger == low]


def build_pack(faction: str, danger: int, rng: random.Random, content: Content, year: int) -> list[Enemy]:
    """The archetypes of a fight (DESIGN.md "Противники"): a leader at the danger level, the rest lower.

    The size comes from `combat.enemy_count_by_danger` (a cyberpsycho is always alone). A faction
    without an archetype in `year` falls back to the NCPD, so a fight is never empty.
    """
    cb = content.balance.combat
    danger = clamp(danger, 1, 5)
    pool = _faction_pool(content, faction, year)
    if not pool and faction in content.gangs:  # owner report 23.10: a gang without fighters this year is not the NCPD
        fa = content.gangs[faction].fights_as  # owner request 25.10: a gang abroad borrows its look-alike's fighters
        for alt in ((fa,) if fa else ()) + ("scavengers", "maelstrom", "tyger_claws", "valentinos", "sixth_street"):
            pool = _faction_pool(content, alt, year)
            if pool:
                break
    if not pool:
        pool = _faction_pool(content, "ncpd", year)
    if not pool:
        raise ValueError(f"no enemy archetypes for {faction!r} in {year}")
    count = 1 if faction == "cyberpsycho" else max(1, dice.roll(cb.enemy_count_by_danger[danger], rng))
    top = _nearest(pool, danger, rng)
    leaders = [e for e in top if e.leader] or top
    lead = dice.choice(rng, leaders)
    pack = [lead]
    lower = sorted({e.danger for e in pool if e.danger < lead.danger and not e.leader}, reverse=True)[:2]
    for _ in range(count - 1):
        if lower:
            level = dice.choice(rng, lower)
            cands = [e for e in pool if e.danger == level and not e.leader]
        else:  # nothing below the leader: his own level, else more of his kind
            cands = [e for e in pool if e.danger == lead.danger and not e.leader] or [lead]
        pack.append(dice.choice(rng, cands))
    return pack


def _implant_states(keys: list[str], content: Content, charges: dict[str, int] | None = None) -> list[ImplantState]:
    out: list[ImplantState] = []
    for key in keys:
        cw = content.cyberware.get(key)
        if cw is not None and cw.active is not None:
            out.append(ImplantState(key, (charges or {}).get(key, 100)))
    return out


def _melee_pool_attack(content: Content, base: int, dice_expr: str, source: str, lethal: bool) -> Attack:
    return Attack(MELEE, source, dice_expr, base, 1, False, lethal)


def build_player(state: CharacterState, content: Content) -> Combatant:
    """The character as a fighter. Everything numeric comes from `derived` (rule 10)."""
    cb = content.balance.combat
    mods = derived.collect_mods(state, content)
    ref = derived.stat_total(state, content, "REF")
    body = derived.stat_total(state, content, "BODY")
    attacks: dict[str, Attack] = {}
    # owner request 23.10: up to three weapons at hand; the fight takes the strongest gun and the strongest blade
    held = [content.weapons[it.key] for it in state.inventory
            if it.kind == "weapon" and it.equipped and it.key in content.weapons]
    guns = [w for w in held if w.skill == "firearms"]
    arsenal: dict[str, Attack] = {}
    for w in guns:
        arsenal[w.key] = Attack(RANGED, w.key, w.damage, ref + derived.skill_total(state, content, "firearms"),
                                1 + w.extra_attempts, w.close_range_only, True)
    for w in held:
        if w.skill == "melee":
            arsenal[w.key] = Attack(MELEE, w.key, w.damage, body + derived.skill_total(state, content, "melee"), 1, False, True)
    if guns:
        weapon = max(guns, key=lambda w: dice.dice_average(w.damage) * (1 + w.extra_attempts))
        attacks[RANGED] = Attack(
            RANGED, weapon.key, weapon.damage, ref + derived.skill_total(state, content, "firearms"),
            1 + weapon.extra_attempts, weapon.close_range_only, True,
        )
    melee_base = body + derived.skill_total(state, content, "melee")
    candidates: list[Attack] = []
    for weapon in (w for w in held if w.skill == "melee"):
        candidates.append(Attack(MELEE, weapon.key, weapon.damage, melee_base, 1, False, True))
    nonlethal: list[Attack] = []
    for item in state.cyberware:
        cw = content.cyberware.get(item.key)
        if cw is None or cw.weapon_damage is None:
            continue
        lethal = item.key not in cb.nonlethal_cyberware
        atk = Attack(MELEE, item.key, cw.weapon_damage, melee_base, 1, False, lethal, cw.weapon_targets)
        (candidates if lethal else nonlethal).append(atk)
    fists = Attack(MELEE, "", cb.unarmed_damage, melee_base, 1, False, False)
    lethal_fists = Attack(MELEE, "", cb.unarmed_damage, melee_base, 1, False, True)
    attacks[MELEE] = max(candidates, key=lambda a: dice.dice_average(a.dice)) if candidates else lethal_fists
    nl_pool = nonlethal + [fists]
    attacks[MELEE_NL] = max(nl_pool, key=lambda a: dice.dice_average(a.dice))

    deck = None
    for item in state.cyberware:
        cw = content.cyberware.get(item.key)
        if cw is not None and cw.deck is not None:
            deck = cw.deck
    ram_max = (deck.ram_max if deck else 0) + (mods.get("ram_max", 0) if deck else 0)
    ram_recharge = (deck.ram_recharge if deck else 0) + (mods.get("ram_recharge", 0) if deck else 0)
    hack_base = derived.stat_total(state, content, "INT") + derived.skill_total(state, content, "netrunning")
    quickhacks = [i.key for i in state.inventory if i.kind == "quickhack"]
    for item in state.cyberware:  # owner report 23.10: a Blackwall script brings its own program into the deck
        cw = content.cyberware.get(item.key)
        for key in (cw.grants_hacks if cw is not None else []):
            if key in content.quickhacks and key not in quickhacks:
                quickhacks.append(key)
    for key in cb.deck_builtin_hacks:  # owner report 23.10: a deck is never empty, the basics come with it
        if key in content.quickhacks and key not in quickhacks:
            quickhacks.append(key)
    maxhp = derived.max_hp(state, content)
    return Combatant(
        id=PLAYER_ID, name=state.name, side="player", danger=0, hp=min(state.hp, maxhp), max_hp=maxhp,
        defense=cb.player_defense_base + ref,
        armor=derived.armor_value(state, content),
        damage_reduction=mods.get("damage_reduction", 0),
        ice=content.balance.netrun.base_ice + mods.get("ice", 0),
        hack_base=hack_base + (deck.hack_bonus if deck else 0),
        check_bonus=mods.get("check.combat", 0),
        ranged_damage=mods.get("ranged_damage", 0), melee_damage=mods.get("melee_damage", 0),
        attacks=attacks, arsenal=arsenal, has_chrome=bool(state.cyberware),
        implants=_implant_states([i.key for i in state.cyberware], content, {i.key: i.charge for i in state.cyberware}),
        ram=half_up(ram_max * content.balance.netrun.ram_start_pct), ram_max=ram_max, ram_recharge=ram_recharge,
        quickhack_cost=mods.get("quickhack_cost", 0), quickhack_targets=mods.get("quickhack_targets", 0),
        quickhacks=quickhacks if deck else [], mods=mods,
        humanity=state.humanity, wound_ratio=content.balance.character.heavy_wound_ratio,
        wound_penalty=content.balance.character.heavy_wound_penalty,
        scan=mods.get("enemy_scan", 0) > 0,
    )


def build_enemy(enemy: Enemy, idx: int, content: Content, name: str | None = None) -> Combatant:
    """An archetype as a fighter: same type as the player, numbers from the archetype and its gear."""
    cb = content.balance.combat
    weapon = content.weapons.get(enemy.weapon or "")
    armor = content.armor.get(enemy.armor or "")
    sources: list[dict[str, int]] = [content.cyberware[k].mods for k in enemy.cyberware if k in content.cyberware]
    if weapon is not None:
        sources.append(weapon.mods)
    if armor is not None:
        sources.append(armor.mods)
    mods = derived.sum_mods(sources, content)
    table = cb.enemy_damage_by_danger[enemy.danger]
    attacks: dict[str, Attack] = {}
    if weapon is not None and weapon.skill == "firearms":
        attacks[RANGED] = Attack(RANGED, weapon.key, table, enemy.attack, 1 + weapon.extra_attempts, weapon.close_range_only, True)
    melee_src = ""
    if weapon is not None and weapon.skill == "melee":
        melee_src = weapon.key
    for key in enemy.cyberware:
        cw = content.cyberware.get(key)
        if cw is not None and cw.weapon_damage is not None:
            melee_src = key
    if melee_src or RANGED not in attacks:
        attacks[MELEE] = Attack(MELEE, melee_src, table, enemy.attack, 1, False, True)
    hp = enemy.hp if enemy.hp is not None else cb.enemy_hp_base + cb.enemy_hp_per_danger * enemy.danger
    deck = None
    for key in enemy.cyberware:
        cw = content.cyberware.get(key)
        if cw is not None and cw.deck is not None:
            deck = cw.deck
    ram_max = (deck.ram_max + mods.get("ram_max", 0)) if deck else 0
    return Combatant(
        id=f"e{idx}", name=name or enemy.name, side="enemy", danger=enemy.danger, hp=hp, max_hp=hp,
        archetype=enemy.key, ai=enemy.ai, leader=enemy.leader,
        defense=enemy.defense + (content.balance.enemy_ai.netrunner.defense_bonus if enemy.ai == "netrunner" else 0),
        armor=(armor.defense if armor else 0) + mods.get("armor", 0),
        damage_reduction=mods.get("damage_reduction", 0),
        ice=content.balance.netrun.base_ice + mods.get("ice", 0),
        check_bonus=0, ranged_damage=mods.get("ranged_damage", 0), melee_damage=mods.get("melee_damage", 0),
        attacks=attacks, has_chrome=bool(enemy.cyberware),
        implants=_implant_states(list(enemy.cyberware), content),
        ram=half_up(ram_max * content.balance.netrun.ram_start_pct), ram_max=ram_max,
        ram_recharge=((deck.ram_recharge + mods.get("ram_recharge", 0)) if deck else 0),
        quickhack_cost=mods.get("quickhack_cost", 0), quickhack_targets=mods.get("quickhack_targets", 0),
        quickhacks=list(enemy.quickhacks) if deck else [], mods=mods,
    )


def build_ally(content: Content, year: int, danger: int, idx: int, name: str, rng: random.Random) -> Combatant:
    """Owner request 24.10: a hired NPC as a fighter at the player's side: the gear of an archetype of its danger."""
    pool = [e for e in content.available("enemies", year).values()
            if not set(e.factions) & {"maxtac", "cyberpsycho"} and not e.leader]
    near = _nearest(pool, max(1, int(danger)), rng) or list(pool)
    c = build_enemy(rng.choice(near), idx, content, name)
    c.id, c.side, c.ai, c.leader, c.quickhacks = f"a{idx}", "ally", "", False, []
    return c


def _numbered(names: list[str]) -> list[str]:
    """The same archetype twice reads 'Name 1', 'Name 2'."""
    totals = {n: names.count(n) for n in names}
    seen: dict[str, int] = {}
    out: list[str] = []
    for n in names:
        if totals[n] > 1:
            seen[n] = seen.get(n, 0) + 1
            out.append(f"{n} {seen[n]}")
        else:
            out.append(n)
    return out


def new_combat(
    state: CharacterState, content: Content, faction: str, danger: int, rng: random.Random, year: int,
    ctx: dict[str, Any] | None = None,
) -> CombatState:
    pack = build_pack(faction, danger, rng, content, year)
    extra = int((ctx or {}).get("extra_foes", 0) or 0)  # owner request 29.10: a loud contract above tier 5 brings more guns
    if extra > 0 and faction != "cyberpsycho":
        pack += build_pack(faction, danger, rng, content, year)[:extra]
        pack = pack[:6]
    if faction in content.gangs and not _faction_pool(content, faction, year):  # borrowed fighters wear the gang's name
        gname = content.gangs[faction].name
        names = _numbered([f"Боец «{gname}»" if not e.leader else f"Главарь «{gname}»" for e in pack])
    else:
        names = _numbered([e.name for e in pack])
    enemies = [build_enemy(e, i, content, names[i]) for i, e in enumerate(pack)]
    return CombatState(
        faction=faction, danger=danger, player=build_player(state, content), enemies=enemies,
        pack_size=len(enemies), ctx=dict(ctx or {}),
        med_max=max(0, content.balance.combat.med_charges + derived.mod(state, content, "med_charges")),
    )


def med_left(cs: CombatState, content: Content) -> int:
    """Charges ready now: the used ones come back `med_recharge_ticks` turns later."""
    back = content.balance.combat.med_recharge_ticks
    return max(0, cs.med_max - sum(1 for t in cs.med_used if cs.tick - t < back))


def med_next(cs: CombatState, content: Content) -> int:
    """Turns until the next charge comes back (0 when none is recharging)."""
    back = content.balance.combat.med_recharge_ticks
    waiting = [back - (cs.tick - t) for t in cs.med_used if cs.tick - t < back]
    return min(waiting) if waiting else 0


# -- active implants -----------------------------------------------------------------------------------------------


def spec_of(c: Combatant, imp: ImplantState, content: Content) -> ActiveSpec:
    spec = derived.active_spec_with_mods(content, imp.key, c.mods)
    assert spec is not None
    return spec


def drain_per_tick(spec: ActiveSpec) -> int:
    """Charge burned per tick by a duration implant: ceil(100 / duration_ticks)."""
    return math.ceil(100 / spec.duration_ticks)


def _fx(c: Combatant, content: Content) -> dict[str, int]:
    """Sum of the effects of the implants that are running."""
    total = {"extra_actions": 0, "check_bonus": 0, "melee_bonus": 0, "damage_reduction": 0, "enemy_attack_penalty": 0}
    for imp in c.implants:
        if imp.running:
            e = spec_of(c, imp, content).effects
            total["extra_actions"] += e.extra_actions
            total["check_bonus"] += e.check_bonus
            total["melee_bonus"] += e.melee_bonus
            total["damage_reduction"] += e.damage_reduction
            total["enemy_attack_penalty"] += e.enemy_attack_penalty
    return total


def activation_block(c: Combatant, imp: ImplantState, content: Content) -> str:
    """Why a duration implant cannot be switched on now, '' when it can."""
    spec = spec_of(c, imp, content)
    if spec.mode != "duration":
        return "not_duration"
    if imp.running:
        return "running"
    if imp.disabled_ticks > 0:
        return "disabled"
    if imp.charge < spec.min_charge:
        return "charge"
    return ""


def _emit(cs: CombatState, **ev: Any) -> None:
    cs.cur_events.append(ev)


def activate(cs: CombatState, c: Combatant, key: str, content: Content) -> None:
    imp = c.implant(key)
    if imp is None:
        raise CombatError("no_implant")
    why = activation_block(c, imp, content)
    if why:
        raise CombatError(why)
    imp.running = True
    _emit(cs, k="activate", by=c.id, imp=key)
    act = content.balance.cyber.active
    if c.is_player and c.humanity <= act.overload_humanity_threshold:
        cs.humanity_due += act.overload_humanity_cost
        c.humanity = max(0, c.humanity - act.overload_humanity_cost)
        _emit(cs, k="overload", by=c.id, imp=key)


def cancel(cs: CombatState, c: Combatant, key: str, content: Content) -> None:
    imp = c.implant(key)
    if imp is None or not imp.running:
        raise CombatError("not_running")
    if not spec_of(c, imp, content).can_cancel:
        raise CombatError("no_cancel")
    imp.running = False
    _emit(cs, k="cancel", by=c.id, imp=key)


def toggle_implant(cs: CombatState, content: Content, key: str) -> None:
    """The player's free button: switch a duration implant on, or off where the model allows it."""
    _require_open(cs)
    imp = cs.player.implant(key)
    if imp is None:
        raise CombatError("no_implant")
    if imp.running:
        cancel(cs, cs.player, key, content)
    else:
        activate(cs, cs.player, key, content)


def toggle_auto(cs: CombatState, content: Content, key: str) -> None:
    _require_open(cs)
    imp = cs.player.implant(key)
    if imp is None or not spec_of(cs.player, imp, content).trigger_toggle:
        raise CombatError("no_toggle")
    imp.auto = not imp.auto


def shot_block(c: Combatant, imp: ImplantState, content: Content, n: int) -> str:
    spec = spec_of(c, imp, content)
    if spec.mode != "shot":
        return "not_shot"
    if imp.disabled_ticks > 0:
        return "disabled"
    if n < 1 or n > spec.effects.targets:
        return "count"
    if imp.charge < spec.min_charge or imp.charge < spec.cost * n:
        return "charge"
    return ""


def fire_shot(
    cs: CombatState, c: Combatant, key: str, n: int, target: int, content: Content, rng: random.Random,
) -> None:
    """Rockets: `n` shots, each takes `cost` charge and hits without a roll. One volley per tick."""
    cb = content.balance.combat
    imp = c.implant(key)
    if imp is None:
        raise CombatError("no_implant")
    if c.is_player and cs.shot_used:
        raise CombatError("shot_used")
    why = shot_block(c, imp, content, n)
    if why:
        raise CombatError(why)
    spec = spec_of(c, imp, content)
    imp.charge -= spec.cost * n
    if c.is_player:
        cs.shot_used = True
    foes = [cs.player] if not c.is_player else [e for _, e in cs.active_enemies()]
    if not foes:
        raise CombatError("no_target")
    if c.is_player:
        first = cs.enemies[target] if 0 <= target < len(cs.enemies) and cs.enemies[target].active else foes[0]
        order = [first] + [e for e in foes if e is not first]
    else:
        order = foes
    _emit(cs, k="shot", by=c.id, imp=key, n=n)
    for i in range(n):
        victim = order[i] if i < len(order) else order[0]
        if not victim.active:
            alive = [v for v in order if v.active]
            if not alive:
                break
            victim = alive[0]
        _hit(cs, content, rng, c, victim, spec.effects.damage or cb.unarmed_damage, 0, True, kind="rocket")


# -- hits and damage -----------------------------------------------------------------------------------------------


def armor_effect(c: Combatant, content: Content) -> int:
    return half_up(c.armor * content.balance.combat.armor_effect_pct)


def _wound(c: Combatant) -> int:
    return c.wound_penalty if c.is_player and c.hp < c.max_hp * c.wound_ratio else 0


def _dodge(cs: CombatState, defender: Combatant, content: Content) -> bool:
    """A reactive implant turns a hit into a miss (Kerenzikov) and loads the next attack."""
    for imp in defender.implants:
        spec = spec_of(defender, imp, content)
        if spec.mode != "reactive" or not spec.effects.dodge:
            continue
        if not imp.auto or imp.disabled_ticks > 0 or imp.charge < spec.min_charge:
            continue
        if cs.ctx.get("elite") and defender.side == "enemy":  # owner request 25.10: an elite dodge needs a tick to recover
            last = (cs.ctx.setdefault("_dodged", {})).get(defender.id)
            if last is not None and cs.tick - int(last) < 3:
                continue
            cs.ctx["_dodged"][defender.id] = cs.tick
        imp.charge = max(0, imp.charge - spec.cost)
        imp.ran = True
        defender.next_bonus += spec.effects.next_attack_bonus
        _emit(cs, k="dodge", by=defender.id, imp=imp.key)
        return True
    return False


def _on_down(cs: CombatState, attacker: Combatant, victim: Combatant, lethal: bool, content: Content) -> None:
    """A kill or a takedown: counters and the recharge of the attacker's implants (running ones too)."""
    victim.out = OUT_KILLED if lethal else OUT_TAKEN
    if attacker.is_player:
        if lethal:
            cs.kills += 1
        else:
            cs.takedowns += 1
        if cs.ctx.get("perks"):
            _perk_kill(cs, attacker, content)
    for imp in attacker.implants:
        spec = spec_of(attacker, imp, content)
        gain = spec.recharge_on_kill if lethal else spec.recharge_on_takedown
        if gain:
            imp.charge = clamp(imp.charge + gain, 0, 100)


def _perk_kill(cs: CombatState, p: Combatant, content: Content) -> None:
    """Owner request 24.10: perks that fire on a kill (Обжора войны, Жажда крови, Эджраннер)."""
    sp = cs.ctx.get("perks") or {}
    g = int(sp.get("glutton", 0))
    if g and cs.med_used and int(cs.ctx.get("glut", 0) or 0) < g:
        cs.med_used.pop(0)
        cs.ctx["glut"] = int(cs.ctx.get("glut", 0) or 0) + 1
        _emit(cs, k="glutton", by=p.id)
    b = int(sp.get("bloodlust", 0))
    if b and 0 < p.hp < p.max_hp:
        heal = min(p.max_hp - p.hp, max(1, p.max_hp * 5 * b // 100))
        p.hp += heal
        _emit(cs, k="bloodlust", by=p.id, n=heal)
    if sp.get("edgerunner") and int(cs.ctx.get("edge", 0) or 0) < 3:
        cs.ctx["edge"] = int(cs.ctx.get("edge", 0) or 0) + 1
        _emit(cs, k="edgerunner", by=p.id, n=cs.ctx["edge"])


def _damage_roll(
    attacker: Combatant, defender: Combatant, dice_expr: str, bonus: int, content: Content, rng: random.Random,
    pierce: bool = False, scale: float = 1.0,
) -> int:
    cb = content.balance.combat
    raw = dice.roll(dice_expr, rng)
    if scale != 1.0:
        raw = half_up(raw * scale)
    raw += bonus
    cut = 0 if pierce else armor_effect(defender, content) + defender.damage_reduction + _fx(defender, content)["damage_reduction"]
    return max(cb.min_damage, raw - cut)


def _hit(
    cs: CombatState, content: Content, rng: random.Random, attacker: Combatant, defender: Combatant,
    dice_expr: str, bonus: int, lethal: bool, *, kind: str, pierce: bool = False, scale: float = 1.0,
    dodgeable: bool = False,
) -> int:
    """Damage that already hit (rockets, a hacker's damage, or a melee / ranged hit after the roll)."""
    if dodgeable and _dodge(cs, defender, content):
        return 0
    dmg = _damage_roll(attacker, defender, dice_expr, bonus, content, rng, pierce, scale)
    if cs.ctx.get("elite") and defender.side == "enemy":  # owner request 25.10: shields, resistances, barriers
        from app.engine import elite

        dmg = elite.absorb(cs, defender, dmg, kind)
    if attacker.side == "enemy" and cs.ctx.get("_mult"):  # owner request 25.10: an elite in rage
        dmg = int(dmg * float(cs.ctx["_mult"]))
    if attacker.side == "enemy" and cs.ctx.get("_pct"):  # owner request 25.10: an aimed shot or a heavy blow
        dmg = max(dmg, int(defender.max_hp * float(cs.ctx["_pct"])))
    if defender.is_player and attacker.side == "enemy":  # owner request 27.10: a chrome ability takes the blow
        from app.engine import ab_power

        cut = ab_power.dr_bonus(cs)
        if cut:
            dmg = max(content.balance.combat.min_damage, dmg - cut)
    defender.hp = max(0, defender.hp - dmg)
    if attacker.side != "enemy":
        cs.dealt += dmg
        cs.cur_dealt += dmg
    elif defender.is_player:
        cs.taken += dmg
        cs.cur_taken += dmg
    down = ""
    if defender.hp <= 0 and not defender.out:
        if defender.side == "ally":  # owner request 24.10: a squad member falls
            defender.out = OUT_KILLED
            down = "ally"
        elif not defender.is_player:
            _on_down(cs, attacker, defender, lethal, content)
            down = "kill" if lethal else "takedown"
        else:
            down = "downed"
    _emit(cs, k="hit", by=attacker.id, to=defender.id, kind=kind, dmg=dmg, down=down)
    if cs.ctx.get("elite") and defender.role == "boss" and defender.active:
        from app.engine import elite

        elite.after_damage(cs, defender, content, rng)
    return dmg


def _attack_roll(
    cs: CombatState, content: Content, rng: random.Random, attacker: Combatant, defender: Combatant,
    atk: Attack, fx_att: dict[str, int], first: bool,
) -> bool:
    """One attack roll: base + d10 + bonuses against the defender's defense. Returns whether it hit."""
    roll = dice.d10_detail(rng, content.balance.checks)
    bonus = attacker.check_bonus + fx_att["check_bonus"] - _wound(attacker)
    if atk.kind == MELEE:
        bonus += fx_att["melee_bonus"]
    if first:
        bonus += attacker.next_bonus
        attacker.next_bonus = 0
    if attacker.penalty_ticks > 0:
        bonus -= attacker.penalty
    bonus -= _fx(defender, content)["enemy_attack_penalty"]
    if attacker.side != "enemy":
        dv = defender.defense
    else:
        bonus += cs.enemy_bonus if cs.enemy_bonus_ticks > 0 else 0
        dv = defender.defense + (content.balance.combat.cover_bonus if defender.cover else 0)
        if defender.is_player:  # owner request 27.10: a chrome ability (cloak, prediction...) makes you harder to hit
            from app.engine import ab_power

            dv += ab_power.def_bonus(cs)
    total = atk.base + roll.value + bonus
    hit = total >= dv
    _emit(cs, k="roll", by=attacker.id, to=defender.id, kind=atk.kind, total=total, dv=dv, hit=hit, crit=roll.crit or "",
          src=atk.source if attacker.is_player else "")
    return hit


def _attack_damage_bonus(attacker: Combatant, atk: Attack) -> int:
    return attacker.ranged_damage if atk.kind == RANGED else attacker.melee_damage


def _next_target(cs: CombatState, preferred: int) -> int | None:
    if 0 <= preferred < len(cs.enemies) and cs.enemies[preferred].active:
        return preferred
    for i, e in cs.active_enemies():
        return i
    return None


def _attacks_of(cs: CombatState, c: Combatant, kind: str, content: Content) -> tuple[Attack, int]:
    fx = _fx(c, content)
    atk = c.attacks[kind]
    return atk, atk.attempts + fx["extra_actions"]


def _strike(
    cs: CombatState, content: Content, rng: random.Random, attacker: Combatant, defender: Combatant,
    atk: Attack, fx: dict[str, int], first: bool,
) -> bool:
    """Roll, dodge, damage. Returns True when it was a hit that went through."""
    cb = content.balance.combat
    if not _attack_roll(cs, content, rng, attacker, defender, atk, fx, first):
        return False
    precise = attacker.side == "enemy" and bool(cs.ctx.get("_pct"))  # owner request 25.10: no dodging an aimed shot
    if not precise and _dodge(cs, defender, content):
        return False
    sp = cs.ctx.get("perks") or {}  # owner request 24.10: the perk trees in the fight
    if defender.is_player and sp and attacker.side == "enemy" and not precise:
        dodged = int(cs.ctx.get("ghosted", 0) or 0)
        free = (2 if sp.get("ninja") else 0) + int(sp.get("ghost", 0))
        if dodged < free:
            cs.ctx["ghosted"] = dodged + 1
            _emit(cs, k="ghost", by=defender.id)
            return False
        blades = sum(1 for k, a in defender.arsenal.items() if a.kind == MELEE and _one_handed(content, k, MELEE))
        if (sp.get("deflect") and atk.kind == RANGED and any(a.kind == MELEE for a in defender.arsenal.values())
                and rng.random() < 0.15 * int(sp["deflect"]) + (0.1 if blades >= 2 and sp.get("dual_blades") else 0)):
            _emit(cs, k="deflect", by=defender.id)
            if int(sp["deflect"]) >= 3 and attacker.active:
                _hit(cs, content, rng, defender, attacker, atk.dice, 0, True, kind=RANGED, scale=0.5)
            return False
    scale = 1.0
    if atk.close_only and cs.tick > cb.close_range_ticks:
        scale = cb.far_range_damage_pct
    if attacker.is_player and cs.ctx.get("qte_pct"):  # owner request 30.10: a quick reaction in time
        scale *= 1 + int(cs.ctx["qte_pct"]) / 100
    bonus = _attack_damage_bonus(attacker, atk)
    pierce = False
    if attacker.is_player and sp:
        bonus += 2 * int(cs.ctx.get("edge", 0) or 0) if sp.get("edgerunner") else 0
        if sp.get("bolt") and atk.kind == RANGED and int(cs.ctx.get("bolt_n", 0) or 0) < int(sp["bolt"]):
            cs.ctx["bolt_n"] = int(cs.ctx.get("bolt_n", 0) or 0) + 1
            pierce = True
            _emit(cs, k="bolt", by=attacker.id)
        if sp.get("deadeye") and atk.kind == RANGED and not cs.ctx.get("deadeye_used"):
            cs.ctx["deadeye_used"] = True
            scale *= 1 + 0.5 * int(sp["deadeye"])
            _emit(cs, k="deadeye", by=attacker.id)
        if sp.get("fury") and atk.kind == MELEE and attacker.hp * 2 < attacker.max_hp:
            bonus += 3 * int(sp["fury"])
    chal = cs.ctx.get("challenge") if attacker.side == "enemy" else None  # owner request 25.10: the counters of a challenge
    if chal and chal.get("counter") == "pierce":
        pierce = True
    if chal and chal.get("counter") == "heavy":
        scale *= 1.5
    wsp = content.weapons[atk.source].special if attacker.is_player and atk.source in content.weapons else None
    if wsp is not None and wsp.pierce:
        pierce = True
    if attacker.side == "enemy" and (cs.ctx.get("cover") or {}).get(defender.id) == "tick":  # covering: braced
        scale *= content.balance.pally.cover_dmg_pct / 100
    _hit(cs, content, rng, attacker, defender, atk.dice, bonus, atk.lethal, kind=atk.kind, scale=scale, pierce=pierce)
    if wsp is not None:  # owner request 25.10: the special weapons really do what their cards say
        _weapon_special(cs, content, rng, attacker, defender, atk, wsp)
    if chal and chal.get("counter") == "stun" and defender.is_player and defender.hp > 0 and rng.random() < 0.2:
        defender.stun = max(defender.stun, 1)
        _emit(cs, k="chal_stun", to=defender.id)
    if attacker.is_player and sp and atk.kind == MELEE and defender.active:
        if sp.get("finisher") and 0 < defender.hp * 4 <= defender.max_hp:  # owner request 24.10: Добивание
            defender.hp = 0
            _on_down(cs, attacker, defender, atk.lethal, content)
            _emit(cs, k="finisher", by=attacker.id, to=defender.id)
        elif sp.get("quake") and rng.random() < 0.25:  # Землетрясение
            defender.stun = max(defender.stun, 1)
            _emit(cs, k="quake", by=attacker.id, to=defender.id)
    return True


def _weapon_special(cs: CombatState, content: Content, rng: random.Random, attacker: Combatant, defender: Combatant,
                    atk: Attack, wsp: Any) -> None:
    """The effect of a special weapon after a hit that went through."""
    src = atk.source
    if wsp.shock_chrome and defender.active and defender.has_chrome:
        _emit(cs, k="wsp", src=src, to=defender.id, what="shock")
        _hit(cs, content, rng, attacker, defender, wsp.shock_chrome, 0, atk.lethal, kind=atk.kind, pierce=True)
    if wsp.stun_chance and defender.active and rng.random() < wsp.stun_chance:
        defender.stun = max(defender.stun, 1)
        _emit(cs, k="wsp", src=src, to=defender.id, what="stun")
    if wsp.blind and defender.active:
        defender.penalty = max(defender.penalty, wsp.blind)
        defender.penalty_ticks = max(defender.penalty_ticks, 2)
        _emit(cs, k="wsp", src=src, to=defender.id, what="blind", n=wsp.blind)
    if wsp.drain and attacker.hp < attacker.max_hp:
        n = min(wsp.drain, attacker.max_hp - attacker.hp)
        attacker.hp += n
        _emit(cs, k="wsp", src=src, to=attacker.id, what="drain", n=n)
    if wsp.splash:
        others = [e for _, e in cs.active_enemies() if e is not defender]
        if others:
            other = rng.choice(others)
            _emit(cs, k="wsp", src=src, to=other.id, what="splash")
            _hit(cs, content, rng, attacker, other, wsp.splash, 0, atk.lethal, kind=atk.kind)


# -- quickhacks ---------------------------------------------------------------------------------------------------


def hack_cost(c: Combatant, qh: Quickhack) -> int:
    return max(1, qh.ram_cost + c.quickhack_cost)


def hack_dv(qh: Quickhack, target: Combatant) -> int:
    return qh.dv_base + qh.dv_per_danger * target.danger + target.ice


def hack_block(c: Combatant, key: str, target: Combatant | None, content: Content) -> str:
    qh = content.quickhacks.get(key)
    if qh is None or key not in c.quickhacks:
        return "no_program"
    if hack_cost(c, qh) > c.ram:
        return "ram"
    if target is not None and not target.has_chrome and not qh.environmental:
        return "no_chrome"
    return ""


def _apply_hack(cs: CombatState, target: Combatant, qh: Quickhack, content: Content, rng: random.Random, by: Combatant) -> dict[str, Any]:
    e = qh.effects
    done: dict[str, Any] = {}
    if e.damage:
        dmg = _hit(cs, content, rng, by, target, e.damage, 0, True, kind="hack", pierce=True)
        done["damage"] = dmg
    if e.stun_ticks and target.active:
        target.stun = max(target.stun, e.stun_ticks)
        done["stun"] = e.stun_ticks
    if e.attack_penalty and target.active:
        target.penalty, target.penalty_ticks = e.attack_penalty, e.penalty_ticks
        done["penalty"] = e.attack_penalty
    if e.drain_charge:
        for imp in target.implants:
            imp.charge = max(0, imp.charge - e.drain_charge)
            if imp.running and imp.charge <= 0:
                imp.running = False
        if target.implants:
            done["drain"] = e.drain_charge
    if e.disable_ticks:
        hit_any = False
        for imp in target.implants:
            if imp.running:
                imp.running = False
                imp.disabled_ticks = e.disable_ticks
                hit_any = True
        done["disable"] = e.disable_ticks if hit_any else 0
    return done


def hack(
    cs: CombatState, content: Content, rng: random.Random, by: Combatant, key: str, targets: list[Combatant],
) -> None:
    """A quickhack: RAM is spent whatever happens; the roll is INT + netrunning + deck against the program's dv
    (+ the target's ice). The enemy's hack against the player is a defense roll of the player."""
    qh = content.quickhacks[key]
    primary = targets[0]
    why = hack_block(by, key, primary, content)
    if why:
        raise CombatError(why)
    by.ram -= hack_cost(by, qh)
    nets = content.balance.netrun
    for target in targets:
        if not target.has_chrome and not qh.environmental:
            continue
        pen = content.balance.chrome_free.quickhack_penalty if (not target.has_chrome and qh.environmental) else 0
        roll = dice.d10_detail(rng, content.balance.checks)
        if by.is_player or by.side == "ally":  # owner request 26.10: a hired player hacks like a player
            total = by.hack_base + roll.value - pen - _wound(by)
            dv = hack_dv(qh, target)
            ok = total >= dv
        else:
            # the player defends: INT + netrunning + ice + d10 against the enemy's dv by danger
            total = target.hack_base + target.ice + roll.value
            dv = nets.enemy_hack_dv_by_danger[clamp(by.danger, 1, 5)] + pen
            ok = total < dv
        done: dict[str, Any] = {}
        if ok:
            done = _apply_hack(cs, target, qh, content, rng, by)
        _emit(cs, k="hack", by=by.id, to=target.id, prog=key, ok=ok, total=total, dv=dv, **{"fx": done})


def hack_targets(cs: CombatState, by: Combatant, primary: Combatant, qh: Quickhack) -> list[Combatant]:
    """The primary target plus the second one of a spreading program and the extra targets of the deck."""
    extra = (1 if qh.effects.spread else 0) + max(0, by.quickhack_targets)
    out = [primary]
    if by.is_player or by.side == "ally":
        for _, e in cs.active_enemies():
            if len(out) > extra:
                break
            if e is not primary:
                out.append(e)
    return out


# -- the tick ------------------------------------------------------------------------------------------------------------


def _require_open(cs: CombatState) -> None:
    if cs.outcome:
        raise CombatError("over")


def _begin_actions(cs: CombatState, c: Combatant, content: Content) -> None:
    """Running implants burn their charge. The effect works in this tick even when the charge hits 0."""
    for imp in c.implants:
        imp.ran = False
        if imp.running:
            spec = spec_of(c, imp, content)
            imp.charge = max(0, imp.charge - drain_per_tick(spec))
            imp.ran = True


def _end_actions(cs: CombatState, c: Combatant, content: Content) -> None:
    """An implant with no charge left switches off (kills in the tick may have refilled it)."""
    for imp in c.implants:
        if imp.running and imp.charge <= 0:
            imp.running = False
            _emit(cs, k="expired", by=c.id, imp=imp.key)


def alive_enemies(cs: CombatState) -> list[Combatant]:
    return [e for _, e in cs.active_enemies()]


def gone_count(cs: CombatState) -> int:
    return sum(1 for e in cs.enemies if e.out)


def pack_half_lost(cs: CombatState, content: Content) -> bool:
    """The pack lost `combat.morale_break_fraction` of its size: exits get easier, the rest may run."""
    return cs.pack_size > 0 and gone_count(cs) / cs.pack_size >= content.balance.combat.morale_break_fraction


def check_end(cs: CombatState) -> None:
    if cs.outcome:
        return
    if cs.player.hp <= 0:
        cs.outcome = DOWNED
    elif not any(e.active for e in cs.enemies):
        if cs.ctx.get("elite"):  # owner request 25.10: the next wave comes in
            from app.engine import elite

            if elite.next_wave(cs):
                return
        cs.outcome = WON


def _blackwall(cs: CombatState, content: Content, rng: random.Random, p: Combatant, idx: int, atk: Attack,
               dmg: int) -> None:
    """Owner request 23.10: a hit of a Blackwall weapon. The static jumps to another enemy, the struck one freezes,
    the rest may run in terror. Every strike is counted for the price (services/combat.py after the fight)."""
    bw = content.balance.combat.blackwall.get(atk.source)
    if bw is None:
        return
    cs.ctx["blackwall"] = int(cs.ctx.get("blackwall", 0) or 0) + 1
    struck = cs.enemies[idx]
    _emit(cs, k="blackwall", by=p.id, to=struck.id)
    if bw.stun and struck.active:
        struck.stun = max(struck.stun, bw.stun)
    others = [(i, e) for i, e in cs.active_enemies() if i != idx]
    if others and bw.echo_pct:
        j, e = rng.choice(others)
        _hit(cs, content, rng, p, e, "1d1", max(0, dmg * bw.echo_pct // 100 - 1), True, kind="blackwall", pierce=True)
    for _, e in cs.active_enemies():
        if cs.ctx.get("elite"):  # owner request 25.10: the elite does not run, it stands and fights
            break
        if e is not struck and rng.random() < bw.fear:
            e.out = OUT_FLED
            _emit(cs, k="flee", by=e.id, why="blackwall")


def _morale(cs: CombatState, content: Content, rng: random.Random) -> None:
    cb = content.balance.combat
    if cs.ctx.get("elite"):  # owner request 25.10: the elite does not run
        return
    if not pack_half_lost(cs, content):
        return
    for _, e in cs.active_enemies():
        if rng.random() < cb.morale_flee_chance:
            e.out = OUT_FLED
            _emit(cs, k="flee", by=e.id, why="morale")


ONE_HANDED = {RANGED: ("pistol", "smg"), MELEE: ("melee",)}


def _one_handed(content: Content, key: str, kind: str) -> bool:
    w = content.weapons.get(key)
    if w is None:
        return False
    t = w.type or ("pistol" if w.skill == "firearms" else "melee")
    return t in ONE_HANDED.get(kind, ())


def offhand(content: Content, p: Combatant, atk: Attack) -> Attack | None:
    """Owner request 24.10: two one-handed guns (pistols, SMGs) or two blades at hand: the second one strikes too."""
    if atk.kind not in (RANGED, MELEE) or not _one_handed(content, atk.source, atk.kind):
        return None
    for key, a in p.arsenal.items():
        if key != atk.source and a.kind == atk.kind and _one_handed(content, key, atk.kind):
            return a
    return None


def _offhand_strikes(cs: CombatState, content: Content, rng: random.Random, p: Combatant, atk: Attack,
                     fx: dict[str, int], idx: int | None) -> None:
    off = offhand(content, p, atk)
    if off is None:
        return
    sp = cs.ctx.get("perks") or {}
    skill = int(sp.get("dual_guns" if off.kind == RANGED else "dual_blades", 0))
    pen = 0 if skill else content.balance.combat.dual_penalty
    _emit(cs, k="dual", by=p.id, src=off.source, kind=off.kind)
    hand = replace(off, base=off.base - pen, attempts=1)
    for _ in range(1 + (1 if skill >= 2 else 0)):
        j = _next_target(cs, idx if idx is not None else 0)
        if j is None:
            break
        before = cs.enemies[j].hp
        _strike(cs, content, rng, p, cs.enemies[j], hand, fx, False)
        if cs.enemies[j].hp < before:
            _blackwall(cs, content, rng, p, j, hand, before - cs.enemies[j].hp)


def _player_phase(cs: CombatState, content: Content, rng: random.Random, action: "PlayerAction") -> None:
    p = cs.player
    sp = cs.ctx.get("perks") or {}
    if sp.get("emp") and not cs.ctx.get("emp_done"):  # owner request 24.10: Бомба с часовым механизмом
        cs.ctx["emp_done"] = True
        n = int(cs.ctx.get("chrome_n", 0) or 0) // 2
        foes = cs.active_enemies()
        if n > 0 and foes:
            _, e = rng.choice(foes)
            _emit(cs, k="emp", by=p.id, to=e.id)
            _hit(cs, content, rng, p, e, f"{min(n, 10)}d6", 0, True, kind=RANGED, pierce=True)
            if e.active:
                e.stun = max(e.stun, 1)
    if action.kind in (A_SHOOT, A_MELEE):
        kind = RANGED if action.kind == A_SHOOT else (MELEE_NL if cs.nonlethal else MELEE)
        chosen = p.arsenal.get(action.weapon) if action.weapon else None
        if chosen is not None and (chosen.kind == RANGED) == (action.kind == A_SHOOT) and kind != MELEE_NL:
            atk, attempts = chosen, chosen.attempts + _fx(p, content)["extra_actions"]
        else:
            if kind not in p.attacks:
                raise CombatError("no_weapon")
            atk, attempts = _attacks_of(cs, p, kind, content)
        fx = _fx(p, content)
        idx: int | None = action.target
        first = True
        q = content.balance.combat.qte
        grade = max(0, min(2, int(action.qte or 0))) if q.enabled else 0
        cs.ctx["qte_pct"] = (q.good_pct, q.perfect_pct)[grade - 1] if grade else 0
        for _ in range(attempts):
            idx = _next_target(cs, idx if idx is not None else 0)
            if idx is None:
                break
            before = cs.enemies[idx].hp
            _strike(cs, content, rng, p, cs.enemies[idx], atk, fx, first)
            if cs.enemies[idx].hp < before:
                _blackwall(cs, content, rng, p, idx, atk, before - cs.enemies[idx].hp)
            first = False
            cleave = int((cs.ctx.get("perks") or {}).get("cleave", 0)) if atk.kind == MELEE else 0
            if atk.kind == MELEE and atk.targets + cleave > 1:
                # weapon_targets (and the Таран perk): every extra victim gets its own hit roll and damage roll
                extra = [i for i, e in cs.active_enemies() if i != idx][: atk.targets + cleave - 1]
                for j in extra:
                    _strike(cs, content, rng, p, cs.enemies[j], atk, fx, False)
            cs.used[RANGED if atk.kind == RANGED else MELEE] += 1
        _offhand_strikes(cs, content, rng, p, atk, fx, idx)
        cs.ctx.pop("qte_pct", None)
    elif action.kind == A_COVER:
        p.cover = True
        _emit(cs, k="cover", by=p.id)
    elif action.kind == A_GUARD:  # owner request 25.10: stand between the person and the guns
        cs.ctx["guarding"] = True
        p.cover = True
        _emit(cs, k="el_guarding", by=p.id)
    elif action.kind == A_HEAL:
        if med_left(cs, content) <= 0:
            raise CombatError("no_med")
        if p.hp >= p.max_hp:
            raise CombatError("full_hp")
        pct = content.balance.combat.med_heal_pct + 10 * int(sp.get("first_aid", 0))  # owner request 24.10: perks
        if sp.get("transfusion") and med_left(cs, content) == 1:
            pct += 30
        heal = max(1, half_up(p.max_hp * pct / 100))
        heal = min(heal, p.max_hp - p.hp)
        p.hp += heal
        cs.med_used.append(cs.tick)
        cs.med_used = [t for t in cs.med_used if cs.tick - t < content.balance.combat.med_recharge_ticks]
        _emit(cs, k="heal", by=p.id, n=heal)
    elif action.kind == A_HACK:
        idx = _next_target(cs, action.target)
        if idx is None:
            raise CombatError("no_target")
        qh = content.quickhacks.get(action.program)
        if qh is None:
            raise CombatError("no_program")
        hack(cs, content, rng, p, action.program, hack_targets(cs, p, cs.enemies[idx], qh))
        cs.used["hack"] += 1
    elif action.kind == A_EXIT_FAIL:
        eb = content.balance.exits.fail
        if action.exit_kind == "talk":
            cs.enemy_bonus, cs.enemy_bonus_ticks = eb.talk_attack_bonus, eb.talk_attack_ticks
        _emit(cs, k="exit_fail", kind=action.exit_kind)


def _ally_phase(cs: CombatState, content: Content, rng: random.Random) -> None:
    """Owner request 24.10: every standing squad member strikes an enemy of its own choice."""
    pallies = cs.ctx.get("pallies") or {}
    cover = cs.ctx.get("cover") or {}
    for a in cs.active_allies():
        targets = cs.active_enemies()
        if not targets:
            break
        if cover.get(a.id) == "tick":  # owner request 29.10: the hired player shields the employer instead of striking
            _emit(cs, k="guard_up", by=a.id)
            continue
        if a.id in pallies:  # owner request 26.10: a hired player uses their own skills
            from app.engine import pally

            pally.act(cs, a, pallies[a.id], content, rng)
            check_end(cs)
            if cs.outcome:
                break
            continue
        idx, e = rng.choice(targets)
        kind = RANGED if RANGED in a.attacks else MELEE
        if kind not in a.attacks:
            continue
        atk, attempts = _attacks_of(cs, a, kind, content)
        fx = _fx(a, content)
        for n in range(attempts):
            if not e.active:
                break
            _strike(cs, content, rng, a, e, atk, fx, n == 0)
        check_end(cs)
        if cs.outcome:
            break


def _enemy_phase(cs: CombatState, content: Content, rng: random.Random) -> None:
    from app.engine import enemy_ai

    p = cs.player
    for idx, e in cs.active_enemies():
        if p.hp <= 0:
            break
        if e.stun > 0:
            e.stun -= 1
            _emit(cs, k="stunned", by=e.id)
            continue
        mult = 1.0
        if cs.ctx.get("elite"):  # owner request 25.10: the elite roles act on their intents (26.10: and the rank and file)
            from app.engine import elite

            r = elite.enemy_turn(cs, idx, e, content, rng)
            if r is None:
                check_end(cs)
                continue
            mult = r
        order = (cs.ctx.get("boss_orders") or {}).pop(e.id, None) if e.id in (cs.ctx.get("boss_ctl") or {}) else None
        plan = _boss_plan(cs, e, str(order), content, rng) if order else enemy_ai.decide(cs, idx, content, rng)
        for op, key, n in plan.free:
            try:
                if op == "activate":
                    activate(cs, e, key, content)
                elif op == "cancel":
                    cancel(cs, e, key, content)
                elif op == "shot":
                    fire_shot(cs, e, key, n, 0, content, rng)
            except CombatError:
                continue
        _begin_actions(cs, e, content)
        if plan.kind == "flee":
            e.out = OUT_FLED
            _emit(cs, k="flee", by=e.id, why="coward")
        elif plan.kind == "hack" and plan.program:
            try:
                hack(cs, content, rng, e, plan.program, [p])
            except CombatError:
                plan = _fallback_attack(e)
        if plan.kind == "attack":
            if mult != 1.0:
                cs.ctx["_mult"] = mult
            _enemy_attack(cs, content, rng, e, plan.attack)
            cs.ctx.pop("_mult", None)
        cs.ctx.pop("_pct", None)
        _end_actions(cs, e, content)
        check_end(cs)
        if cs.outcome:
            break


def boss_skills(cs: CombatState, e: Combatant, content: Content) -> list[str]:
    """Owner request 27.10: what a player playing the enemy (the raid boss, services/pboss.py) may do this tick."""
    out: list[str] = []
    for imp in e.implants:
        if spec_of(e, imp, content).mode == "duration" and not activation_block(e, imp, content):
            out.append(f"imp:{imp.key}")
    for key in e.quickhacks:
        if not hack_block(e, key, cs.player, content):
            out.append(f"hack:{key}")
    out += [k for k in (RANGED, MELEE) if k in e.attacks]
    out.append("flee")
    return out


def _boss_plan(cs: CombatState, e: Combatant, order: str, content: Content, rng: random.Random) -> Any:
    """The move a player chose for the enemy fighter (the raid boss): a strike, an implant then a strike, a program,
    or an escape (balance.pboss.flee_chance; a failed escape still strikes)."""
    from app.engine.enemy_ai import EnemyAction

    kind, _, key = order.partition(":")
    main = RANGED if RANGED in e.attacks else MELEE
    if order in (RANGED, MELEE):
        return EnemyAction([], "attack", order)
    if kind == "imp":
        return EnemyAction([("activate", key, 0)], "attack", main)
    if kind == "hack" and key in e.quickhacks:
        return EnemyAction([], "hack", main, key)
    if order == "flee":
        if rng.random() < content.balance.pboss.flee_chance:
            return EnemyAction([], "flee", main)
        _emit(cs, k="flee_fail", by=e.id)
        return EnemyAction([], "attack", main)
    return EnemyAction([], "attack", main)


def _fallback_attack(e: Combatant) -> Any:
    from app.engine.enemy_ai import EnemyAction

    return EnemyAction([], "attack", RANGED if RANGED in e.attacks else MELEE)


def _enemy_attack(cs: CombatState, content: Content, rng: random.Random, e: Combatant, kind: str) -> None:
    if kind not in e.attacks:
        kind = RANGED if RANGED in e.attacks else MELEE
    atk, attempts = _attacks_of(cs, e, kind, content)
    fx = _fx(e, content)
    first = True
    target = cs.player
    allies = cs.active_allies()
    vip = None
    if cs.ctx.get("elite"):  # owner request 25.10: the person under protection draws the blows
        from app.engine import elite

        vip = elite.vip_target(cs, rng, e) or elite.focus_target(cs, e)
    if vip is not None:
        target = vip
    elif allies and rng.random() < content.balance.combat.ally_target_chance:  # owner request 24.10: the squad draws fire
        target = rng.choice(allies)
    if target is cs.player:  # owner request 29.10: a hired player covers the employer and takes the blows
        guard = cover_guard(cs)
        if guard is not None:
            target = guard
            _emit(cs, k="guard", by=guard.id, att=e.id)
    for _ in range(attempts):
        if cs.player.hp <= 0 or not target.active and target is not cs.player:
            break
        _strike(cs, content, rng, e, target, atk, fx, first)
        first = False


def cover_guard(cs: CombatState) -> "Combatant | None":
    """The hired player who stands between the enemies and the employer now (owner request 29.10), if any."""
    for aid, mode in (cs.ctx.get("cover") or {}).items():
        if mode not in ("tick", "hold"):
            continue
        a = next((x for x in cs.allies if x.id == aid), None)
        if a is not None and a.active:
            return a
    return None


def _end_cover(cs: CombatState) -> None:
    cover = dict(cs.ctx.get("cover") or {})
    if not cover:
        return
    for aid, mode in list(cover.items()):
        a = next((x for x in cs.allies if x.id == aid), None)
        if mode == "tick" or a is None or not a.active:
            cover.pop(aid)
    cs.ctx["cover"] = cover


def _end_of_tick(cs: CombatState, content: Content) -> None:
    from app.engine import ab_power  # owner request 27.10: regeneration heals every tick

    ab_power.end_of_tick(cs)
    _end_cover(cs)
    sw = int((cs.ctx.get("perks") or {}).get("second_wind", 0))
    p = cs.player
    if sw and not cs.ctx.get("sw_used") and 0 < p.hp * 4 <= p.max_hp:  # owner request 24.10: Второе дыхание
        cs.ctx["sw_used"] = True
        heal = max(1, p.max_hp * 20 * sw // 100)
        p.hp = min(p.max_hp, p.hp + heal)
        _emit(cs, k="second_wind", by=p.id, n=heal)
    for c in [cs.player, *cs.enemies, *cs.allies]:
        if c.out and not c.is_player:
            continue
        for imp in c.implants:
            if imp.disabled_ticks > 0:
                imp.disabled_ticks -= 1
            if imp.running or imp.ran:
                continue
            spec = spec_of(c, imp, content)
            imp.charge = clamp(imp.charge + spec.recharge_per_tick, 0, 100)
        c.ram = min(c.ram_max, c.ram + c.ram_recharge)
        if c.penalty_ticks > 0:
            c.penalty_ticks -= 1
            if c.penalty_ticks == 0:
                c.penalty = 0
    if cs.enemy_bonus_ticks > 0:
        cs.enemy_bonus_ticks -= 1
        if cs.enemy_bonus_ticks == 0:
            cs.enemy_bonus = 0
    cs.shot_used = False
    cs.player.cover = False
    cs.ctx.pop("guarding", None)
    if cs.ctx.get("elite"):  # owner request 25.10: the intents of the next tick
        from app.engine import elite

        elite.plan_intents(cs)
    # log: the full tick, the older ones folded into one line
    cs.log.append({"tick": cs.tick, "ev": cs.cur_events, "dealt": cs.cur_dealt, "taken": cs.cur_taken})
    cs.cur_events, cs.cur_dealt, cs.cur_taken = [], 0, 0
    while len(cs.log) > MAX_LOG_TICKS:
        gone = cs.log.pop(0)
        old = cs.old or {"from": gone["tick"], "to": gone["tick"], "dealt": 0, "taken": 0}
        old["to"] = gone["tick"]
        old["dealt"] += gone["dealt"]
        old["taken"] += gone["taken"]
        cs.old = old
    cs.tick += 1
    limit = content.balance.combat.max_rounds * (3 if cs.ctx.get("elite") or cs.ctx.get("long") else 1)  # owner request 25.10: elite fights are long
    if not cs.outcome and cs.tick > limit and cs.ctx.get("src") == "arena":  # owner request 30.10: no help comes to a
        p = cs.player                                                      # ring: the judges decide on points
        mine = p.hp / max(1, p.max_hp)
        theirs = sum(max(0, e.hp) for e in cs.enemies) / max(1, sum(e.max_hp for e in cs.enemies))
        cs.ctx["points"] = True
        cs.outcome = WON if mine >= theirs else DOWNED
        return
    if not cs.outcome and cs.tick > limit and not cs.reinforced:
        cs.reinforced = True
        _emit(cs, k="reinforce")


@dataclass(frozen=True)
class PlayerAction:
    kind: str
    target: int = 0
    program: str = ""
    exit_kind: str = ""
    weapon: str = ""  # owner report 23.10: the weapon at hand to use (a key of the player's arsenal)
    qte: int = 0      # owner request 30.10: 0 none, 1 good, 2 perfect (a quick reaction of the Mini App, set by the service)


def _run_tick(cs: CombatState, content: Content, rng: random.Random, action: PlayerAction | None) -> None:
    p = cs.player
    p.cover = False
    if action is not None:
        _begin_actions(cs, p, content)
        _player_phase(cs, content, rng, action)
        _end_actions(cs, p, content)
        check_end(cs)
        if not cs.outcome:
            _morale(cs, content, rng)
            check_end(cs)
    if not cs.outcome and cs.allies:
        _ally_phase(cs, content, rng)
        check_end(cs)
    if not cs.outcome:
        _enemy_phase(cs, content, rng)
        check_end(cs)
    _end_of_tick(cs, content)


def resolve_tick(cs: CombatState, content: Content, rng: random.Random, action: PlayerAction) -> None:
    """Play one tick with the player's main action. A stunned player's ticks pass by themselves."""
    _require_open(cs)
    if cs.ctx.get("join"):  # M6.6: MaxTac lands in a cyberpsychosis breakdown
        from app.engine import psychosis

        psychosis.maybe_join(cs, content, rng)
    if cs.reinforced and action.kind not in (A_WAIT, A_EXIT_FAIL):
        raise CombatError("reinforced")
    if action.kind in (A_SHOOT, A_MELEE) and (RANGED if action.kind == A_SHOOT else MELEE) not in cs.player.attacks:
        raise CombatError("no_weapon")
    if action.kind == A_HEAL:  # checked before the tick starts, so nothing of the tick is spent on a refused button
        if med_left(cs, content) <= 0:
            raise CombatError("no_med")
        if cs.player.hp >= cs.player.max_hp:
            raise CombatError("full_hp")
    _run_tick(cs, content, rng, action)
    guard = 0
    while not cs.outcome and cs.player.stun > 0 and guard < 10:
        cs.player.stun -= 1
        _emit(cs, k="stunned", by=PLAYER_ID)
        _run_tick(cs, content, rng, None)
        guard += 1


def enemy_free_round(cs: CombatState, content: Content, rng: random.Random, rounds: int = 1) -> None:
    """The enemies act before the player does (a failed flee or talk before the fight): no tick passes."""
    for _ in range(rounds):
        if cs.outcome:
            break
        _enemy_phase(cs, content, rng)
        check_end(cs)
    for c in [cs.player, *cs.enemies]:
        for imp in c.implants:
            imp.ran = False


def finish(cs: CombatState, result: str) -> None:
    """End the fight by a choice of the player: fled, avoided (paid or talked), surrendered."""
    if not cs.outcome:
        cs.outcome = result


def _skill_used(cs: CombatState) -> str:
    """The skill the player used most in the fight (it gets the win xp)."""
    pairs = [("firearms", cs.used.get(RANGED, 0)), ("melee", cs.used.get(MELEE, 0)), ("netrunning", cs.used.get("hack", 0))]
    return max(pairs, key=lambda p: p[1])[0]


def summary(cs: CombatState) -> dict[str, Any]:
    """What the service and the gig need to know after the fight."""
    return {
        "result": cs.outcome, "kills": cs.kills, "takedowns": cs.takedowns, "ticks": min(cs.tick - 1, 999),
        "dealt": cs.dealt, "taken": cs.taken, "faction": cs.faction, "danger": cs.danger,
        "skill": _skill_used(cs),
    }
