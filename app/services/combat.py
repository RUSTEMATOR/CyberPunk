"""Combat service (PLAN 5.6): a fight on the character's flags, step by step.

The fight is a `CombatState` (engine/combat.py) kept in `flags["combat"]`, so a closed window loses
nothing and a double press cannot play a tick twice (every button carries the tick it was drawn on).
After every step the service writes the result into the character:

- hp and humanity (the overload cost) through `apply_effects`;
- the charge of the implants straight into `cyberware.charge` (it is a counter, not a stat);
- the end of the fight: xp, `faction_rep`, the betrayed flag, bus events, the log, and the 0 hp rule
  (`services/death.py`).

Where a fight comes from is in `cs.ctx["src"]`: `gig` (the gig run settles after it), `event` or
`followup`. Exits before the fight are in `services/events.py`; they call `begin` / `exit_won`.
"""

from __future__ import annotations

import logging
import random
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.engine import checks as engine_checks
from app.engine import combat as cb
from app.engine import events as engine_events
from app.engine import exits
from app.engine import psychosis as psy_engine
from app.engine.checks import CheckResult
from app.engine.effects import apply_effects, apply_remove_cyberware
from app.engine.state import FLAG_COMBAT, FLAG_PENDING_COMBAT, FLAG_PENDING_EXIT, FLAG_TRUCE_PREFIX
from app.services import death, followups
from app.services import game_events as bus
from app.services import gigs as gigs_service
from app.services.game import ActiveCharacter, save_active

log = logging.getLogger(__name__)


class StaleError(Exception):
    """The button belongs to an earlier tick or the fight is over: redraw the screen."""


@dataclass
class FightEnd:
    """The end of a fight, for the closing screen."""

    summary: dict[str, Any]
    lines: list[str] = field(default_factory=list)  # zero hp, rep and xp notes (text keys come from the view)
    rep: dict[str, int] = field(default_factory=dict)
    xp_skill: str = ""
    betrayed: bool = False
    zero_hp: bool = False
    died: bool = False
    pending: bool = False  # the death save failed and the glitch offer waits (services/death.py)
    cause: str | None = None
    src: str = ""


@dataclass
class ExitInfo:
    kind: str
    ok: bool | None
    check: Any = None
    cost: int = 0
    note: str = ""  # taken | surrender outcome
    lines: list[str] = field(default_factory=list)
    pending: bool = False  # the check failed and luck or a glitch may still save it


@dataclass
class Step:
    """What a press did: the fight (None once it is over), how it ended, the exit attempt if any."""

    cs: cb.CombatState
    end: FightEnd | None = None
    exit: ExitInfo | None = None
    lines: list[str] = field(default_factory=list)


# -- the state on the character ------------------------------------------------------------------------------------


def state_of(ch: ActiveCharacter) -> cb.CombatState | None:
    raw = ch.state.flags.get(FLAG_COMBAT)
    return cb.from_dict(raw) if raw else None


def _store(ch: ActiveCharacter, cs: cb.CombatState) -> None:
    ch.state.flags[FLAG_COMBAT] = cb.to_dict(cs)


def truce_active(content: Content, ch: ActiveCharacter, faction: str) -> bool:
    """A bribe or a talk with the faction worked in the last `truce_weeks` weeks."""
    since = ch.state.flags.get(FLAG_TRUCE_PREFIX + faction)
    if since is None:
        return False
    return ch.state.weeks_lived - int(since) <= content.balance.combat.faction_rep.truce_weeks


def default_faction(content: Content, ch: ActiveCharacter) -> str:
    """Who attacks in a fight without a named faction: a gang of the district that has fighters in the character's
    year (owner report 23.10: «Валентинос» came as NCPD officers), else any such gang, else the NCPD."""
    year = ch.state.current_year
    district = content.districts.get(ch.state.district)
    for g in (district.gangs if district is not None else []):
        if cb._faction_pool(content, g, year) or (g in content.gangs and content.gangs[g].fights_as):
            return g  # owner request 25.10: a gang abroad fights with borrowed archetypes in its own name
    for g in ("scavengers", "maelstrom", "tyger_claws", "valentinos", "sixth_street"):
        if g in content.gangs and cb._faction_pool(content, g, year):
            return g
    return "ncpd"


# -- syncing the fight with the character -----------------------------------------------------------------------------


def _sync(content: Content, ch: ActiveCharacter, cs: cb.CombatState, rng: random.Random, year: int) -> None:
    """hp, humanity and charge of the fight into the character."""
    st = ch.state
    fx: dict[str, int] = {}
    delta = cs.player.hp - st.hp
    if delta:
        fx["hp"] = delta
    if cs.humanity_due:
        fx["humanity"] = -cs.humanity_due
        cs.humanity_due = 0
    if fx:
        apply_effects(st, fx, content, rng, year)
    for imp in cs.player.implants:
        item = st.cyber_item(imp.key)
        if item is not None:
            item.charge = max(0, min(100, imp.charge))


async def _persist(session: AsyncSession, content: Content, ch: ActiveCharacter, cs: cb.CombatState,
                   rng: random.Random, year: int, tz: str) -> None:
    _sync(content, ch, cs, rng, year)
    _store(ch, cs)
    await save_active(session, ch, tz)


# -- starting ----------------------------------------------------------------------------------------------------------------


async def begin(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    *, faction: str, danger: int, src: dict[str, Any] | None = None, pre: str = "",
    foes: list[cb.Combatant] | None = None,
) -> Step:
    """Start a fight. `pre` is how the exits ended: "talk_fail" (the enemies get an attack bonus),
    "flee_fail" (a free round of enemy attacks), or "" for a plain start."""
    ctx = dict(src or {})
    ctx.pop("faction", None)
    ctx.pop("danger", None)
    ctx.setdefault("src", "event")
    if not foes and not ctx.get("squad") and not ctx.get("solo"):  # owner request 24.10: the hired NPCs fight at the player's side
        from app.services import city_life

        squad = await city_life.squad_for_fight(session, ch)
        if squad:
            ctx["squad"] = [{"npc": int(m.get("npc", 0) or 0), "name": str(m.get("name", "")),
                             "danger": int(m.get("danger", 2) or 2)} for m in squad]
    ch.state.flags.pop(FLAG_PENDING_COMBAT, None)
    ch.state.flags.pop("loot", None)  # owner request 26.10: the bodies of the last fight are left behind
    from app.engine import perks

    ctx["perks"] = perks.specials(ch.state)
    from app.engine import abilities as ab_eng  # owner request 26.10: the chrome build lines add their specials

    for k, v in ab_eng.specials(ch.state, content).items():
        ctx["perks"][k] = int(ctx["perks"].get(k, 0)) + v  # owner request 24.10: combat specials of the perk trees
    ctx["chrome_n"] = len(ch.state.cyberware)
    cs = cb.new_combat(ch.state, content, faction, danger, rng, year, ctx)
    if foes:  # M6.6b: ready-made enemies (another player's character on a MaxTac duty call)
        cs.enemies, cs.pack_size = list(foes), len(foes)
    elif ctx.get("foe") and cs.enemies:  # a named lone opponent of an event: the strongest of the pack, stronger
        lead = max(cs.enemies, key=lambda e: e.max_hp)
        lead.name = str(ctx["foe"])
        lead.max_hp = lead.hp = int(lead.max_hp * float(ctx.get("foe_mult") or 1.6))  # owner request 26.10: Raven as a boss
        cs.enemies, cs.pack_size = [lead], 1
    elif cs.ctx.get("ally") and len(cs.enemies) > 1:  # a merc of the exchange (gig backup) takes the last of the pack
        cs.enemies.pop()
        cs.pack_size = len(cs.enemies)
    if cs.ctx.get("squad"):
        cs.allies = [cb.build_ally(content, year, int(m.get("danger", 2) or 2), i, str(m.get("name", "Напарник")), rng)
                     for i, m in enumerate(cs.ctx["squad"])]
    if not foes and not ctx.get("solo"):  # owner request 25.10: players hired as bodyguards stand at the side
        from app.services import pally, pguard

        guards = await pguard.for_fight(session, content, ch)
        staff = []  # owner request 26.10: the staff of the player's megacorp fight at the side too, on their own skills
        if content.balance.pally.enabled:
            staff = await pally.staff_for_fight(session, content, ch, {g.player_tg_id for g in guards})
        if guards or staff:
            names = pally.join(content, cs, [(g, "guard") for g in guards] + [(m, "staff") for m in staff])
            if guards:
                cs.ctx["guards"] = names[: len(guards)]
            fname = (content.gangs[faction].name if faction in content.gangs else content.corps[faction].name
                     if faction in content.corps else faction)
            if guards:
                await pguard.notify(session, ch, guards, fname)
            await pally.go_live(session, content, ch, cs, fname)
    if not foes and not ctx.get("solo"):  # owner request 25.10: the character's own fighters stand at the side
        from app.services import army

        army.join(content, cs, ch, rng, year, war=bool(ctx.get("army_war")))
    if ctx.get("dyn_name") and cs.enemies:  # owner request: a non-canon crew wears borrowed fighters under its own name
        corp = ctx.get("dyn_kind") == "corp"
        grunt, boss = ("Охранник", "Командир") if corp else ("Боец", "Главарь")
        for e in cs.enemies:
            e.name = f"{grunt} «{ctx['dyn_name']}»"
        if ctx.get("lair"):  # the lair: the boss sits there
            lead = max(cs.enemies, key=lambda e: e.max_hp)
            lead.name = f"{boss} «{ctx['dyn_name']}»"
            lead.max_hp = lead.hp = int(lead.max_hp * 1.8)
    extra = int(ctx.get("extra", 0) or 0)
    if extra and cs.enemies and not foes:  # owner request 25.10: a district held by gangs sends bigger packs
        cap = 8 if ctx.get("army_war") else content.balance.wanted.extra_cap if ctx.get("chase") else 4
        for _ in range(min(extra, cap)):
            more = cb.build_pack(cs.faction, cs.danger, rng, content, year)
            cs.enemies.append(cb.build_enemy(more[-1], len(cs.enemies), content, f"{more[-1].name} {len(cs.enemies) + 1}"))
        cs.pack_size = len(cs.enemies)
        if ctx.get("dyn_name"):  # owner request 25.10: the extra fighters wear the same colours
            for e in cs.enemies:
                if "«" not in e.name:
                    e.name = f"{'Охранник' if ctx.get('dyn_kind') == 'corp' else 'Боец'} «{ctx['dyn_name']}»"
    cs.ctx["truce"] = False if ctx.get("dyn_name") else truce_active(content, ch, faction)
    as_faction = cs.ctx.pop("as_faction", None)
    if as_faction:  # M6.6: the NCPD pack, fought and exited as MaxTac (no truce with it)
        cs.faction, cs.ctx["truce"] = str(as_faction), False
    ef = content.balance.exits.fail
    if pre == "talk_fail":
        cs.enemy_bonus, cs.enemy_bonus_ticks = ef.talk_attack_bonus, ef.talk_attack_ticks
    elif pre == "flee_fail":
        cb.enemy_free_round(cs, content, rng, ef.flee_free_attacks)
    cs.ctx["pre"] = pre
    if not foes:
        await _auto_elite(session, content, ch, cs, rng, year)
    cb.check_end(cs)
    if cs.over:
        return await _finish(session, content, ch, rng, year, tz, cs)
    await _persist(session, content, ch, cs, rng, year, tz)
    return Step(cs)


def replay(cs: cb.CombatState, result: str) -> dict[str, Any]:
    """The last fight for a replay: who stood where at the start and what happened every tick (`cs.log`).
    Contract for the Mini App: docs/PLAN_LIVING.md stage 6."""
    def who(c: cb.Combatant) -> dict[str, Any]:
        return {"id": c.id, "name": c.name, "side": c.side, "max_hp": c.max_hp, "role": getattr(c, "role", "")}

    return {"faction": cs.faction, "danger": cs.danger, "result": result, "title": str(cs.ctx.get("title") or ""),
            "elite": bool(cs.ctx.get("elite")), "player": who(cs.player),
            "enemies": [who(e) for e in cs.enemies], "allies": [who(a) for a in cs.allies],
            "ticks": list(cs.log)[-60:]}


async def _auto_elite(session: AsyncSession, content: Content, ch: ActiveCharacter, cs: cb.CombatState,
                      rng: random.Random, year: int) -> None:
    """Owner request 25.10 «все мои бои заканчиваются за один удар»: a strong character's ordinary fight is elite:
    roles, intents, a health floor by the character's damage. Contract fights set up their own elite."""
    c = content.balance.elite_auto
    ctx = cs.ctx
    src = str(ctx.get("src", ""))
    if (not c.enabled or cs.danger < c.min_danger or src in c.exclude or ctx.get("duel") or ctx.get("army_war")
            or (src == "craid" and ctx.get("site") == "tower") or not cs.enemies or isinstance(ctx.get("elite"), dict)):
        return
    from app.services import corpraid, wear

    if not (await wear.outgrown(session, content, ch)
            or await corpraid.ladder(session, ch) >= content.balance.escort.min_ladder):
        return
    from app.engine import elite

    boss = str(ctx.get("foe") or "")
    if not boss and ctx.get("lair"):
        boss = max(cs.enemies, key=lambda e: e.max_hp).name
    room = c.room.get(min(cs.danger, max(c.room)), max(len(cs.enemies), 3))
    elite.setup(cs, content, rng, year, boss=boss, ticks=c.ticks, room=room, threat_tick=c.threat_tick)
    ctx["auto_elite"] = True


# -- free actions (no tick passes) -----------------------------------------------------------------------------------------


def _guard(ch: ActiveCharacter, tick: int) -> cb.CombatState:
    cs = state_of(ch)
    if cs is None or cs.over or cs.tick != tick:
        raise StaleError
    return cs


async def toggle_implant(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    tick: int, key: str,
) -> Step:
    cs = _guard(ch, tick)
    line = ""
    try:
        cb.toggle_implant(cs, content, key)
    except cb.CombatError as e:
        line = e.reason
    await _persist(session, content, ch, cs, rng, year, tz)
    return Step(cs, lines=[line] if line else [])


async def toggle_nonlethal(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, tick: int,
) -> Step:
    cs = _guard(ch, tick)
    cs.nonlethal = not cs.nonlethal
    await _persist(session, content, ch, cs, rng, year, tz)
    return Step(cs)


async def toggle_auto(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    tick: int, key: str,
) -> Step:
    cs = _guard(ch, tick)
    try:
        cb.toggle_auto(cs, content, key)
    except cb.CombatError:
        pass
    await _persist(session, content, ch, cs, rng, year, tz)
    return Step(cs)


async def order_ally(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    tick: int, aid: str, idx: int,
) -> Step:
    """Owner request 26.10: the employer orders a hired player's skill for the next tick (no tick passes)."""
    from app.engine import pally as pally_eng
    from app.services import pally

    cs = _guard(ch, tick)
    a = next((x for x in cs.allies if x.id == aid), None)
    line = ""
    skills = pally_eng.all_skills(a, content) if a is not None and a.active else []
    if 0 <= idx < len(skills) and pally.order(cs, aid, skills[idx]):
        name = (cs.ctx.get("pallies") or {}).get(aid, {}).get("name", "")
        line = f"{name}: на следующем ходу — {pally_eng.label(content, skills[idx])}."
    await _persist(session, content, ch, cs, rng, year, tz)
    return Step(cs, lines=[line] if line else [])


async def fire_shot(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    tick: int, key: str, count: int, target: int,
) -> Step:
    cs = _guard(ch, tick)
    line = ""
    try:
        cb.fire_shot(cs, cs.player, key, count, target, content, rng)
    except cb.CombatError as e:
        line = e.reason
    cb.check_end(cs)
    if cs.over:
        return await _finish(session, content, ch, rng, year, tz, cs)
    await _persist(session, content, ch, cs, rng, year, tz)
    return Step(cs, lines=[line] if line else [])


async def use_ability(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    tick: int, fam: str, target: int,
) -> Step:
    """Owner request 27.10: a chrome ability as a free action of the tick (engine/ab_power.py)."""
    from app.engine import ab_power

    cs = _guard(ch, tick)
    ab_power.prime(cs, ch.state, content)
    try:
        lines = ab_power.use(cs, content, rng, ch.state, fam, target)
    except cb.CombatError as e:
        lines = [e.reason]
    else:
        lines = [] if not cs.over else lines  # the screen shows it under «Сейчас» already
    cb.check_end(cs)
    if cs.over:
        out = await _finish(session, content, ch, rng, year, tz, cs)
        out.lines = lines + out.lines
        return out
    await _persist(session, content, ch, cs, rng, year, tz)
    return Step(cs, lines=lines)


# -- the tick ----------------------------------------------------------------------------------------------------------------


async def act(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    tick: int, action: cb.PlayerAction,
) -> Step:
    """Play the tick with the player's main action."""
    cs = _guard(ch, tick)
    line = ""
    from app.engine import ab_power  # owner request 27.10: regeneration and the other passive parts

    ab_power.prime(cs, ch.state, content)
    from app.services import pally  # owner request 26.10: the hired players' own choices for this tick

    await pally.pull_orders(session, ch, cs)
    if cs.ctx.get("boss_ctl"):  # owner request 27.10: the raid boss picks the fighter's move in person
        from app.services import pboss

        await pboss.pull_order(session, ch, cs)
    try:
        cb.resolve_tick(cs, content, rng, action)
    except cb.CombatError as e:
        line = e.reason
        await _persist(session, content, ch, cs, rng, year, tz)
        return Step(cs, lines=[line])
    from app.services import reinforce  # owner request 24.10: called help arrives on its turn

    await reinforce.tick(session, content, ch, cs, rng, year)
    await _announce_hacks(session, ch, cs)
    await pally.after_tick(session, content, ch, cs)
    if cs.ctx.get("boss_ctl"):
        from app.services import pboss

        await pboss.after_tick(session, content, ch, cs)
    if cs.over:
        return await _finish(session, content, ch, rng, year, tz, cs)
    await _persist(session, content, ch, cs, rng, year, tz)
    return Step(cs)


async def _announce_hacks(session: AsyncSession, ch: ActiveCharacter, cs: cb.CombatState) -> None:
    """`quickhack_used` on the bus: once per program the player ran in the last tick."""
    if not cs.log:
        return
    progs = {e["prog"] for e in cs.log[-1]["ev"] if e.get("k") == "hack" and e.get("by") == cb.PLAYER_ID}
    for _ in progs:
        await bus.publish("quickhack_used", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch)


# -- exits in the fight ----------------------------------------------------------------------------------------------------


def exit_options(content: Content, ch: ActiveCharacter, cs: cb.CombatState) -> list[exits.ExitOption]:
    opts = exits.build_exits(ch.state, content, cs.faction, cs.danger, pack_half_lost=cb.pack_half_lost(cs, content))
    return [_ab_dv(cs, o) for o in opts]


def _ab_dv(cs: cb.CombatState, opt: exits.ExitOption) -> exits.ExitOption:
    """Owner request 27.10: a chrome ability used in the fight makes fleeing or talking easier."""
    from dataclasses import replace

    from app.engine import ab_power

    if opt.dv is None:
        return opt
    cut = int(cs.ctx.get(ab_power.CTX_FLEE, 0) or 0) if opt.kind == exits.FLEE else \
        int(cs.ctx.get(ab_power.CTX_TALK, 0) or 0) if opt.kind in (exits.TALK, exits.BRIBE) else 0
    return replace(opt, dv=max(1, opt.dv - cut)) if cut else opt


def exit_succeeded(content: Content, ch: ActiveCharacter, rng: random.Random, year: int, kind: str, faction: str) -> None:
    """A bribe or a talk worked: a truce with the faction and a little standing."""
    if kind not in (exits.BRIBE, exits.TALK):
        return
    rep = content.balance.combat.faction_rep
    ch.state.flags[FLAG_TRUCE_PREFIX + faction] = ch.state.weeks_lived
    apply_effects(ch.state, {f"faction_rep:{faction}": rep.bribe_ok if kind == exits.BRIBE else rep.talk_ok},
                  content, rng, year)


async def exit_attempt(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    tick: int, kind: str, step: int = 0,
) -> Step:
    """Pay, talk, flee or surrender in the middle of the fight. A failed exit costs the tick."""
    cs = _guard(ch, tick)
    st = ch.state
    info = ExitInfo(kind, None)
    half = cb.pack_half_lost(cs, content)
    if kind == exits.SURRENDER and cs.ctx.get("src") in psy_engine.CAPTURE_SRC:  # M6.6a: MaxTac takes a psycho alive
        info.note = "psycho"
        cb.finish(cs, cb.SURRENDERED)
        out = await _finish(session, content, ch, rng, year, tz, cs)
        out.exit = info
        return out
    if kind == exits.SURRENDER:
        sr = exits.surrender(st, content, rng, cs.faction)
        applied = apply_effects(st, dict(sr.effects), content, rng, year)
        fu_all = list(applied.followups)
        for key in sr.strip:
            fu_all.extend(apply_remove_cyberware(st, content, key, rng, year).followups)
        cs.player.hp = st.hp  # the beating is already on the character
        fu = await followups.handle(session, content, ch, fu_all, rng, year, tz)
        info.note, info.lines = sr.outcome, fu.lines
        await repo.log_event(session, "surrender", {"faction": cs.faction, "outcome": sr.outcome}, character_id=ch.id)
        cb.finish(cs, cb.SURRENDERED)
        if fu.died:
            ch.state.flags.pop(FLAG_COMBAT, None)
            end = FightEnd(cb.summary(cs), died=True, cause=fu.cause, src=str(cs.ctx.get("src", "")))
            return Step(cs, end, info)
        out = await _finish(session, content, ch, rng, year, tz, cs, sync=False)
        out.exit = info
        return out
    opt = _ab_dv(cs, exits.build_exit(st, content, cs.faction, cs.danger, kind, pack_half_lost=half, bribe_step=step))
    if not opt.possible:
        raise StaleError
    check = exits.roll_exit(st, content, rng, opt)
    if not check.ok and can_rescue(content, ch, check):
        st.flags[FLAG_PENDING_EXIT] = {
            "where": "fight", "kind": kind, "step": step, "tick": cs.tick, "check": asdict(check), "rev": 0,
        }
        await save_active(session, ch, tz)
        info.check, info.pending = check, True
        return Step(cs, exit=info)
    return await _exit_resolve(session, content, ch, rng, year, tz, cs, kind, step, check)


async def _exit_resolve(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    cs: cb.CombatState, kind: str, step: int, check: CheckResult,
) -> Step:
    """The final check of an exit in the fight: a worked exit ends it, a failed one costs the tick."""
    st = ch.state
    opt = exits.build_exit(st, content, cs.faction, cs.danger, kind, pack_half_lost=cb.pack_half_lost(cs, content),
                           bribe_step=step)
    info = ExitInfo(kind, check.ok, check, cost=opt.cost)
    if check.ok:
        if kind == exits.BRIBE:
            apply_effects(st, {"eddies": -opt.cost}, content, rng, year)
        exit_succeeded(content, ch, rng, year, kind, cs.faction)
        cb.finish(cs, cb.FLED if kind == exits.FLEE else cb.AVOIDED)
        out = await _finish(session, content, ch, rng, year, tz, cs)
        out.exit = info
        return out
    if kind == exits.BRIBE and exits.bribe_taken_anyway(content, rng):
        apply_effects(st, {"eddies": -opt.cost}, content, rng, year)
        info.note = "taken"
    cb.resolve_tick(cs, content, rng, cb.PlayerAction(cb.A_EXIT_FAIL, exit_kind=kind))
    if cs.over:
        out = await _finish(session, content, ch, rng, year, tz, cs)
        out.exit = info
        return out
    await _persist(session, content, ch, cs, rng, year, tz)
    return Step(cs, exit=info)


# -- luck and glitch on a failed exit check ---------------------------------------------------------------------------------


def pending_exit(ch: ActiveCharacter) -> dict[str, Any] | None:
    return ch.state.flags.get(FLAG_PENDING_EXIT)


def can_rescue(content: Content, ch: ActiveCharacter, check: CheckResult) -> bool:
    """Luck can cover the miss, or a glitch can reroll the die."""
    return (engine_events.luck_offer(check, ch.state, content) is not None
            or engine_checks.can_glitch_reroll(check, ch.state.glitches))


def _pending(ch: ActiveCharacter, rev: int) -> dict[str, Any]:
    p = pending_exit(ch)
    if p is None or p.get("rev") != rev:
        raise StaleError
    return p


async def exit_luck(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, rev: int,
) -> CheckResult:
    p = _pending(ch, rev)
    check = CheckResult(**p["check"])
    offer = engine_events.luck_offer(check, ch.state, content)
    if offer is None:
        raise StaleError
    spent = engine_checks.spend_luck(check, offer.points, ch.state.luck_pool, content.balance.luck)
    apply_effects(ch.state, spent.effects, content, rng, year)
    p.update(check=asdict(spent.result), rev=int(p["rev"]) + 1)
    ch.state.flags[FLAG_PENDING_EXIT] = p
    await save_active(session, ch, tz)
    return spent.result


async def exit_glitch(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, rev: int,
) -> CheckResult:
    p = _pending(ch, rev)
    check = CheckResult(**p["check"])
    if not engine_checks.can_glitch_reroll(check, ch.state.glitches):
        raise StaleError
    re = engine_checks.glitch_reroll(check, ch.state.glitches, rng, content.balance.checks)
    apply_effects(ch.state, re.effects, content, rng, year)
    p.update(check=asdict(re.result), rev=int(p["rev"]) + 1)
    ch.state.flags[FLAG_PENDING_EXIT] = p
    await save_active(session, ch, tz)
    return re.result


async def exit_take(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, rev: int,
) -> Any:
    """Take the check as it stands. Returns a Step (in the fight) or an events.ExitResult (before it)."""
    p = _pending(ch, rev)
    check = CheckResult(**p["check"])
    ch.state.flags.pop(FLAG_PENDING_EXIT, None)
    if p["where"] == "fight":
        cs = state_of(ch)
        if cs is None or cs.over:
            raise StaleError
        return await _exit_resolve(session, content, ch, rng, year, tz, cs, p["kind"], int(p["step"]), check)
    from app.services import events as ev_service  # local import: events imports this module

    return await ev_service.finish_exit(session, content, ch, rng, year, tz, p["kind"], int(p["step"]), check)


# -- the end ------------------------------------------------------------------------------------------------------------------------------


async def after_fight(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    src: str, faction: str, result: str, ctx: dict[str, Any],
) -> list[str]:
    """What a fight of the police, the prison yard or cyberpsychosis changes once the character is known to live."""
    if src in ("raid", "hunt", "jail"):  # M9: the police and the prison yard
        from app.services import crime as crime_service

        lines = await crime_service.fight_over(session, content, ch, rng, year, tz, src, faction, result)
        if ctx.get("chase"):  # owner request 26.10: a wave of the chase
            from app.services import wanted

            lines += wanted.after_wave(content, ch, result)
        return lines
    if src == "arena":  # owner request 30.10: the fighting arena
        from app.services import arena

        return await arena.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "pboss":  # owner request 27.10: a player as the raid boss
        from app.services import pboss

        return await pboss.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "capri":  # owner request 26.10: project «Каприкорн»
        from app.services import capri

        return await capri.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "clones":  # owner request 26.10: the Ark of the clone corp
        from app.services import clones

        return await clones.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "ravenboss":  # owner request 26.10: Raven Tearz comes for the one who makes him trouble
        from app.services import blacktears

        return await blacktears.boss_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "corpop":  # owner request 26.10: an operation of the player's megacorp
        from app.services import corpforce

        return await corpforce.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "city":  # owner request: a fight with a city NPC (services/city.py)
        from app.services import city as city_service

        return await city_service.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "dgame":  # owner request 24.10: the final of the deadly games
        from app.services import dgame as dgame_service

        return await dgame_service.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "sortie":  # owner request 24.10: a fight on a sortie goes back to the map
        from app.services import sortie as sortie_service

        return await sortie_service.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "challenge":  # owner request 25.10: the ladder of challenges against an outgrown build
        from app.services import challenge as challenge_service

        return await challenge_service.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "graid":  # owner request 25.10: a solo raid on a rival gang
        from app.services import gangs as gangs_service

        return await gangs_service.raid_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "ctrl":  # owner request 25.10: gangs against megacorps
        from app.services import control

        return await control.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "vendetta":  # owner request 25.10: a vendetta on a hostile faction
        from app.services import enemies

        return await enemies.vendetta_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "pmcbattle":  # owner request 25.10: a battle of the own PMC
        from app.services import pmc

        return await pmc.battle_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "escort":  # owner request 25.10: an attempt on the person under protection
        from app.services import escort

        return await escort.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "exped":  # owner request 25.10: a fight of an expedition
        from app.services import expedition

        return await expedition.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "lgduel":  # owner request 25.10: a duel with a legend of the Hall
        from app.services import lgcity

        return await lgcity.duel_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "finale":  # owner request 25.10: a fight stage of an ending
        from app.services import finale

        return await finale.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "turf":  # owner request 25.10: the war for a district held by a player
        from app.services import turf

        return await turf.war_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "awar":  # owner request 25.10: the war of the character's own gang
        from app.services import army

        return await army.war_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "dyn_strike":  # owner request 25.10: a strike on a corp that comes and goes
        from app.services import corpraid

        return await corpraid.strike_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "tdun":  # owner request 26.10: the tower as a dungeon
        from app.services import dungeon

        return await dungeon.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "craid":  # owner request 25.10: a corp member raids a rival megacorp
        from app.services import corpraid

        return await corpraid.raid_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "nemesis":  # owner request 24.10: the nemesis of the pressure
        from app.services import pressure as pressure_service

        return await pressure_service.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "purge":  # owner request 24.10: an NPC crew of the Purge night
        from app.services import purge as purge_service

        return await purge_service.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src == "crisis":  # owner request 24.10: a fight of the city crisis
        from app.services import crisis as crisis_service

        return await crisis_service.fight_over(session, content, ch, rng, year, tz, result, ctx)
    if src in ("dyn", "dyn_lair"):  # owner request: the non-canon gangs and corps
        from app.services import dyn as dyn_service

        return await dyn_service.fight_over(session, content, ch, rng, year, tz, src, result, ctx)
    if src in psy_engine.PSYCHO_SRC:  # M6.6: the breakdown, a hunt, a duty call
        from app.services import psychosis as psy_service

        return await psy_service.fight_over(session, content, ch, rng, year, tz, src, result, ctx)
    return []


def _blackwall_price(content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> list[str]:
    """Humanity, a glitch, and a trace on the Net: at `hunt_at` NetWatch or MaxTac come for the character."""
    bc = content.balance.combat.blackwall_cost
    st = ch.state
    fx: dict[str, Any] = {"humanity": -bc.humanity} if bc.humanity else {}
    if content.balance.blackwall.fight_rot:  # owner request 24.10: the weapon deepens the taint
        fx["bw_rot"] = content.balance.blackwall.fight_rot
    lines = [f"🕳 Заслон взял свою цену: человечность −{bc.humanity}, порча +{content.balance.blackwall.fight_rot}."]
    if rng.random() < bc.glitch_chance:
        fx["glitches"] = 1
        lines.append("В голове остаётся чужой голос. Глюк +1.")
    if fx:
        apply_effects(st, fx, content, rng, year)
    trace = int(st.flags.get("blackwall_trace", 0) or 0) + bc.trace_per_fight
    if trace >= bc.hunt_at and not st.flags.get(FLAG_PENDING_COMBAT):
        ok = [f for f in bc.hunt_factions if cb._faction_pool(content, f, year)]  # NetWatch has no squads early
        faction = rng.choice(ok) if ok else "ncpd"
        st.flags[FLAG_PENDING_COMBAT] = {"src": "blackwall_hunt", "faction": faction, "danger": bc.hunt_danger}
        st.flags["blackwall_trace"] = 0
        who = {"netwatch": "NetWatch", "maxtac": "МаксТак"}.get(faction, "NCPD")
        lines.append(f"🕳 {who} засекает след Заслона и выходит на тебя. Отряд уже рядом.")
    else:
        st.flags["blackwall_trace"] = trace
        lines.append(f"След на Сети: {trace}/{bc.hunt_at}. Когда он станет полным, придёт NetWatch или МаксТак.")
    return lines


async def _finish(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    cs: cb.CombatState, *, sync: bool = True,
) -> Step:
    """Pay out a finished fight: hp, xp, faction_rep, the betrayed flag, bus, log, the 0 hp rule."""
    st = ch.state
    if sync:
        _sync(content, ch, cs, rng, year)
    from app.services import reinforce

    refund = reinforce.refund_if_missed(content, ch, cs, rng, year)
    summary = cb.summary(cs)
    result = cs.outcome
    rep_cfg = content.balance.combat.faction_rep
    end = FightEnd(summary, src=str(cs.ctx.get("src", "")))
    if refund:
        end.lines.append(refund)
    fx: dict[str, Any] = {}
    if result == cb.WON and cs.kills + cs.takedowns > 0:
        fx[f"skill_xp:{summary['skill']}"] = content.balance.combat.win_xp
        end.xp_skill = summary["skill"]
    downs = cs.kills * rep_cfg.kill + cs.takedowns * rep_cfg.takedown
    rep = max(rep_cfg.fight_floor, downs) if downs < 0 else 0
    if (cs.kills + cs.takedowns) and cs.ctx.get("truce") and cs.faction not in ("cyberpsycho",):
        fx[f"betray:{cs.faction}"] = True
        rep += rep_cfg.betray
        end.betrayed = True
        st.flags.pop(FLAG_TRUCE_PREFIX + cs.faction, None)
    if (rep and cs.ctx.get("src") != psy_engine.SRC_DUTY and not cs.ctx.get("dyn_name") and not cs.ctx.get("duel")
            and not cs.ctx.get("no_rep")):  # a crew in borrowed colours  # a MaxTac call is the job: no standing with cyberpsychos to lose
        fx[f"faction_rep:{cs.faction}"] = rep
    if fx:
        applied = apply_effects(st, fx, content, rng, year)
        end.rep = dict(applied.faction_rep)
    src = str(cs.ctx.get("src", ""))
    cs.ctx["kills"] = cs.kills  # a duty call against a player's fugitive: killed or taken alive
    if cs.ctx.get("elite"):  # owner request 25.10: did the person under protection make it
        from app.engine import elite

        cs.ctx["vip_alive"] = elite.vip_alive(cs)
    if cs.ctx.get("dg_boss"):  # owner request 26.10: the tower dungeon's boss keeps the damage the party dealt
        from app.services import dungeon

        cs.ctx["dg_share"] = dungeon.boss_share(cs)
    if src == "pboss":  # owner request 27.10: the raid boss keeps the wounds; taken down, the boss dies as a legend
        from app.services import pboss

        await pboss.on_end(session, content, ch, cs, result, rng, year, tz)
    after = {"kind": "fight", "src": src, "faction": cs.faction, "result": result, "ctx": dict(cs.ctx)}
    if result == cb.DOWNED and (cs.ctx.get("duel") or cs.ctx.get("nonlethal")):  # a duel with a legend, the tower dungeon never kill
        st.hp = max(1, st.hp)
    elif result == cb.DOWNED:
        zr = await death.resolve_zero_hp(
            session, content, ch, rng, year, tz, cause=death.cause_for_fight(src, cs.faction), source=src, after=after)
        end.lines.extend(zr.lines)
        end.zero_hp = True
        end.died, end.cause = zr.died, zr.cause
        end.pending = zr.pending
    st.flags.pop(FLAG_COMBAT, None)
    st.flags.pop(FLAG_PENDING_COMBAT, None)
    if not end.died and not end.pending and not cs.ctx.get("duel") and src not in ("gym", "arena"):
        from app.engine import injury  # owner request 26.10: a fight can cost a limb or an implant

        heavy = cs.taken * 100 >= content.balance.injury.heavy_pct * max(1, cs.player.max_hp)
        part, what, ifx = injury.roll(st, content, rng, downed=result == cb.DOWNED, heavy=heavy)
        if part:
            if ifx:
                apply_effects(st, ifx, content, rng, year)
            end.lines.append(f"🩸 {what}")
    from app.services import pally  # owner request 26.10: the hired players' windows close; the auto mode learns

    end.lines.extend(await pally.end_live(session, content, ch, cs, result))
    pally.record_style(ch, cs)
    from app.services import loot  # owner request 26.10: the bodies can be searched

    if result == cb.WON and not end.died:
        loot.offer(content, st, cs)
    else:
        st.flags.pop(loot.FLAG, None)
    fallen = [int(m.get("npc", 0) or 0) for m, a in zip(cs.ctx.get("squad") or [], cs.allies) if a.out]
    if fallen:  # owner request 24.10: a squad member who fell dies for real
        from app.services import city_life

        end.lines.extend(await city_life.squad_losses(session, content, ch, fallen))
    if cs.ctx.get("army") or cs.faction in content.gangs:  # owner request 25.10: own fighters: losses, growth, defectors
        from app.services import army

        end.lines.extend(army.fight_end(content, ch, cs, result, rng))
    if cs.ctx.get("blackwall") and not end.died:  # owner request 23.10: the price of a Blackwall weapon
        end.lines.extend(_blackwall_price(content, ch, rng, year))
    if src == "gig":
        gigs_service.fight_finished(ch, summary)
    elif not end.died and not end.pending:  # a pending glitch offer runs the rest when it is answered
        end.lines.extend(await after_fight(session, content, ch, rng, year, tz, src, cs.faction, result, cs.ctx))
    if result == cb.WON and not end.died and not cs.ctx.get("chase"):  # owner request 26.10: heat only with witnesses
        from app.services import wanted

        alive = sum(1 for e in cs.enemies if getattr(e, "out", None) in (cb.OUT_TAKEN, cb.OUT_FLED))
        end.lines.extend(wanted.witness(content, ch, rng, year, src, alive))
    await repo.log_event(
        session, "fight_end",
        {"faction": cs.faction, "danger": cs.danger, "result": result, "kills": cs.kills, "takedowns": cs.takedowns,
         "ticks": summary["ticks"], "dealt": cs.dealt, "taken": cs.taken, "src": cs.ctx.get("src")},
        character_id=ch.id,
    )
    from app.services import playerdata  # owner request 26.10: the last fight, tick by tick, for the replay (Mini App)

    await playerdata.put(session, ch.player_tg_id, "last_fight", replay(cs, result))
    if not end.died:
        await save_active(session, ch, tz)
        kw = {"player_tg_id": ch.player_tg_id, "character_id": ch.id, "session": session, "ch": ch}
        if result == cb.WON:
            await bus.publish("fight_won", min_danger=cs.danger, **kw)
        for _ in range(cs.takedowns):
            await bus.publish("takedown", **kw)
    return Step(cs, end)
