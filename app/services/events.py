"""Playing the events of a week (PLAN 4.4): choices, checks with luck and glitch offers, effects,
followups, the fight exits. All state lives on the character's flags, so a closed window resumes
exactly where it stopped and a closed window can never re-roll a failed check:

- `pending_events`: the queue of event ids still to play (front = the one on screen);
- `pending_check`: a rolled check waiting for the luck / glitch / accept decision;
- `pending_combat`: a fight offered by an event, waiting for the exits or the fight itself.

Every change of the character goes through `apply_effects`; luck and glitch spending too.
"""

from __future__ import annotations

import logging
import random
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.content.schemas import Event, parse_bus_key
from app.db import repo
from app.engine import checks, events as engine_events, exits
from app.engine import psychosis as psy_engine
from app.engine.checks import CheckResult
from app.engine.effects import AppliedEffects, apply_effects, apply_remove_cyberware
from app.engine.state import (
    FLAG_COMBAT,
    FLAG_GIG_RUN,
    FLAG_MAXTAC_DUTY,
    FLAG_PENDING_CHECK,
    FLAG_PENDING_COMBAT,
    FLAG_PENDING_EXIT,
    FLAG_PSYCHO_CAPTURE,
    FLAG_RECENT_EVENTS,
    FLAG_WEEK_LEDGER,
)
from app.narrator import narrator
from app.services import combat, followups, playerdata, uniqueevent
from app.services import gigs as gigs_service
from app.services import game_events as bus
from app.services.game import ActiveCharacter, save_active
from app.services.week import pending_events, set_pending

log = logging.getLogger(__name__)


@dataclass
class Resolution:
    """A finished event choice."""

    event: Event
    choice_idx: int
    ok: bool | None  # None = the choice had no check
    check: CheckResult | None
    text: str
    applied: AppliedEffects
    lines: list[str] = field(default_factory=list)  # followup lines: fights, jail, vows
    died: bool = False
    death_cause: str | None = None
    combat_danger: int | None = None  # a fight waits; the exits screen comes next
    faction: str | None = None
    late: dict | None = None  # Claude's retelling is asked after the window shows the authored text


@dataclass
class ExitResult:
    kind: str
    ok: bool | None  # None for a surrender
    check: CheckResult | None
    lines: list[str] = field(default_factory=list)
    avoided: bool = False
    died: bool = False
    death_cause: str | None = None
    note: str = ""
    fight: Any = None  # a services.combat.Step when the failed exit started the fight
    pending: bool = False  # the check failed and luck or a glitch may still save it (flags[pending_exit])


# -- pending state ---------------------------------------------------------------------------------


def check_to_dict(c: CheckResult) -> dict[str, Any]:
    return asdict(c)


def check_from_dict(d: dict[str, Any]) -> CheckResult:
    return CheckResult(**d)


def current_event(content: Content, ch: ActiveCharacter) -> Event | None:
    """The event on screen: the front of the queue (skipping ids that no longer exist)."""
    queue = pending_events(ch)
    while queue and queue[0] not in content.events:
        queue.pop(0)
        set_pending(ch, queue)
    return personal(content, ch, content.events[queue[0]]) if queue else None


def personal(content: Content, ch: ActiveCharacter, event: Event) -> Event:
    """A business story with "{biz}" replaced by the name of the business it is about (owner request)."""
    if event.business is None:
        return event
    import json

    from app.engine import biz as biz_engine
    from app.services import business

    b = biz_engine.focus_of(ch.state, event.id) or (biz_engine.list_of(ch.state) or [None])[0]
    name = business.title(content, b) if b else "твоё заведение"
    raw = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    return Event.model_validate(json.loads(raw.replace("{biz}", json.dumps(name, ensure_ascii=False)[1:-1])))


def pending_check(ch: ActiveCharacter) -> dict[str, Any] | None:
    return ch.state.flags.get(FLAG_PENDING_CHECK)


def pending_combat(ch: ActiveCharacter) -> dict[str, Any] | None:
    return ch.state.flags.get(FLAG_PENDING_COMBAT)


def has_pending(ch: ActiveCharacter) -> bool:
    """Anything the player must finish before the menu opens."""
    flags = ch.state.flags
    return bool(
        pending_events(ch) or pending_check(ch) or pending_combat(ch) or flags.get(FLAG_COMBAT)
        or flags.get(FLAG_PENDING_EXIT) or flags.get(FLAG_GIG_RUN)
        or flags.get(FLAG_PSYCHO_CAPTURE) or flags.get(FLAG_MAXTAC_DUTY)  # M6.6a: a verdict and a call wait for an answer
    )


def event_payload(ch: ActiveCharacter, event: Event, district_name: str) -> dict[str, Any]:
    """The structured input of the narrator (DESIGN.md section 14)."""
    return {
        "kind": "event_text",
        "character": {"name": ch.state.name, "district": district_name, "role": ch.state.role},
        "event": {"id": event.id, "title": event.title, "category": event.category},
    }


_event_texts: dict[tuple[int, str, int], str | None] = {}  # (character, event, week of life) -> Claude's text, None = authored


async def event_text(content: Content, ch: ActiveCharacter, event: Event) -> str | None:
    """Claude's retelling of an authored event for this character (PLAN 8.2 `event_text`, fast model), or None to show
    the authored text. Kept in memory per character, event and week, so a reopened window never asks again.
    Forced events (a warning, a birth) and events Claude wrote itself stay as they are."""
    if event.forced_only or not narrator.is_live() or not content.balance.event_text.retell_event:
        return None
    key = (ch.id, event.id, ch.state.weeks_lived)
    if key in _event_texts:
        return _event_texts[key]
    district = content.districts.get(ch.state.district)
    payload = event_payload(ch, event, district.name if district else ch.state.district)
    payload["event"]["text"] = event.text
    text = await narrator.narrate_or("event_text", payload, "", year=ch.state.current_year) or None
    if len(_event_texts) > 512:
        _event_texts.clear()
    _event_texts[key] = text
    return text


# -- choosing -------------------------------------------------------------------------------------------


async def choose(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, idx: int,
) -> Resolution | CheckResult:
    """Press choice `idx` of the current event.

    A choice without a check, or a passed check, resolves at once (a Resolution). A failed check is
    saved as pending and returned (a CheckResult): the player may spend luck, a glitch, or accept.
    """
    event = current_event(content, ch)
    if event is None or not 0 <= idx < len(event.choices):
        raise LookupError("no such event choice")
    choice = event.choices[idx]
    if choice.ability:  # owner request 26.10: only the owners of the chrome ability
        from app.engine import abilities

        if not abilities.has(ch.state, content, choice.ability):
            raise LookupError("no such ability")
    result = engine_events.roll_choice(ch.state, content, choice, rng)
    if result is None or result.ok:
        return await finalize(session, content, ch, rng, year, tz, event, idx, result)
    ch.state.flags[FLAG_PENDING_CHECK] = {
        "event": event.id, "choice": idx, "check": check_to_dict(result), "rev": 0,
    }
    await save_active(session, ch, tz)
    return result


def _pending(ch: ActiveCharacter, rev: int | None = None) -> dict[str, Any]:
    p = pending_check(ch)
    if p is None or p.get("kind") == "gamble" or (rev is not None and p.get("rev") != rev):
        raise LookupError("no pending check, or an old button")
    return p


async def spend_luck(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, rev: int,
) -> CheckResult:
    """Spend the luck points that turn the failed check into a pass."""
    p = _pending(ch, rev)
    result = check_from_dict(p["check"])
    offer = engine_events.luck_offer(result, ch.state, content)
    if offer is None:
        raise LookupError("luck cannot save this check")
    spent = checks.spend_luck(result, offer.points, ch.state.luck_pool, content.balance.luck)
    apply_effects(ch.state, spent.effects, content, rng, year)
    p.update(check=check_to_dict(spent.result), rev=int(p["rev"]) + 1)
    ch.state.flags[FLAG_PENDING_CHECK] = p
    await save_active(session, ch, tz)
    return spent.result


async def spend_glitch(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, rev: int,
) -> CheckResult:
    """Spend a glitch on a reroll of the die (once per check)."""
    p = _pending(ch, rev)
    result = check_from_dict(p["check"])
    if not checks.can_glitch_reroll(result, ch.state.glitches):
        raise LookupError("no glitch to spend")
    re = checks.glitch_reroll(result, ch.state.glitches, rng, content.balance.checks)
    apply_effects(ch.state, re.effects, content, rng, year)
    p.update(check=check_to_dict(re.result), rev=int(p["rev"]) + 1)
    ch.state.flags[FLAG_PENDING_CHECK] = p
    await save_active(session, ch, tz)
    return re.result


async def accept_check(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, rev: int,
) -> Resolution:
    """Take the result as it stands (a fail, or a pass bought with luck) and play the outcome."""
    p = _pending(ch, rev)
    event = personal(content, ch, content.events[p["event"]])
    return await finalize(
        session, content, ch, rng, year, tz, event, int(p["choice"]), check_from_dict(p["check"])
    )


async def finalize(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, event: Event, idx: int, check: CheckResult | None, *, heat_mult: float = 1.0,
) -> Resolution:
    """Apply the outcome of a choice: effects, followups, log, bus, queue. Saves the character."""
    choice = event.choices[idx]
    ok = None if check is None else check.ok
    outcome = engine_events.outcome_for(choice, ok)
    st = ch.state
    from app.engine import biz as biz_engine

    focus = (st.flags.get(biz_engine.FOCUS) or {}).get(event.id)
    if focus is not None:  # owner request: the effects of a business story act on that business
        st.flags[biz_engine.NOW] = focus
    eff = dict(outcome.effects)
    if choice.ability:  # owner request 26.10: a later version of the implant line pays more on its chrome choice
        from app.engine import abilities as ab_eng

        mine = ab_eng.mine_for(st, content, choice.ability)
        rank = ab_eng.ranks(st, content).get(mine or "", 1)
        eff = ab_eng.scale(content, eff, rank)
    applied = apply_effects(st, eff, content, rng, year, heat_mult=heat_mult)
    st.flags.pop(biz_engine.NOW, None)
    if focus is not None:
        fm = dict(st.flags.get(biz_engine.FOCUS) or {})
        fm.pop(event.id, None)
        if fm:
            st.flags[biz_engine.FOCUS] = fm
        else:
            st.flags.pop(biz_engine.FOCUS, None)
    await playerdata.apply_deltas(session, ch.player_tg_id, applied.player_deltas)
    note_ledger(ch, applied)
    if event.id.startswith("psyleg_"):  # owner request 24.10: a legend deed gets its own permanent post
        from app.services import psylegend

        await psylegend.on_resolved(session, ch, event.id, choice.label, ok)
    from app.engine import pressure as pr_eng  # owner request 25.10: a krysha agreed in an event holds at once

    for who, kd in content.balance.pressure.krysha.items():
        if st.flags.pop(pr_eng.KRYSHA_PREFIX + who, None):
            st.flags[pr_eng.F_KRYSHA] = {"who": who}
            from app.services import notices

            notices.push(ch, f"🛡 Теперь у тебя крыша: {kd.name}. Рэкет к тебе больше не ходит. Платить каждый месяц.")
    if st.flags.get("bw_avatar") and not st.flags.get("avatar_announced"):  # owner request 25.10: the city learns
        st.flags["avatar_announced"] = True
        from app.bot import texts as _t

        who = _t.esc(f"{st.name} {st.surname}".strip())
        await repo.log_event(session, "borg", {"text": (
            f"📡 <b>У Чёрного заслона новый Аватар.</b> {who} проходит коронацию помех. Сеть города на секунду "
            "замирает, а в каждом динамике Найт-Сити слышен один и тот же шум.")}, character_id=ch.id, public=True)
        from app.services import deeds  # owner request 26.10: the city remembers

        await deeds.add(session, ch.id, "avatar", f"{st.name} {st.surname}".strip() + ": коронация помех, новый Аватар Чёрного "
                        "заслона. В каждом динамике города слышен один и тот же шум.", district=st.district, w=3)
    if event.id == "dg_expose_3":  # owner request 24.10: the island falls, the deadly games stop for a while
        from app.services import dgame

        await dgame.on_resolved(session, content, ch, choice.label, ok)

    queue = pending_events(ch)
    if queue and queue[0] == event.id:
        queue.pop(0)
    set_pending(ch, queue)
    if uniqueevent.is_unique(event.id):  # a Claude-written event is played once and leaves the character (PLAN 8.5)
        uniqueevent.forget(content, ch, event.id)
    st.flags.pop(FLAG_PENDING_CHECK, None)
    recent = list(st.flags.get(FLAG_RECENT_EVENTS) or []) + [event.id]
    keep = content.balance.events.recent_memory
    st.flags[FLAG_RECENT_EVENTS] = recent[-keep:] if keep else []

    district = content.districts.get(st.district)
    payload = event_payload(ch, event, district.name if district else st.district)
    payload.update(kind="event_outcome", choice={"label": choice.label, "ok": ok},
                   effects=applied.narrator_effects())
    text = outcome.text  # owner request: the window does not wait for Claude; `late` asks for the retelling
    late = ({"payload": payload, "year": st.current_year}
            if narrator.is_live() and content.balance.event_text.retell_outcome else None)

    fu = await followups.handle(
        session, content, ch, applied.followups, rng, year, tz, defer_combat=event.faction is not None,
        cause="collector" if event.id == content.balance.debt.collector_event else None,
    )
    res = Resolution(
        event, idx, ok, check, text, applied, fu.lines, fu.died, fu.cause, fu.combat_danger,
        event.faction if fu.combat_danger else None, late,
    )
    if fu.died:
        return res
    if fu.combat_danger:
        pend = dict(st.flags.get(FLAG_PENDING_COMBAT) or {})  # an event without a faction: the followup chose one
        pend.update(src="event", event=event.id, danger=fu.combat_danger,
                    faction=event.faction or event.foe_faction or pend.get("faction"))
        if event.foe:
            pend["foe"] = event.foe
        st.flags[FLAG_PENDING_COMBAT] = pend
    await save_active(session, ch, tz)

    await repo.log_event(
        session, "event_resolved",
        {"event": event.id, "choice": idx, "ok": ok, "effects": applied.narrator_effects()},
        character_id=ch.id, public=event.public,
    )
    await bus.publish(
        "event_resolved", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch,
        category=event.category, nonviolent=engine_events.is_nonviolent(choice, bool(fu.combat_danger) or _fought(applied)),
    )
    if ok is not False:  # the deeds the choice stands for (a performance, a deal) count when it did not fail
        for key in choice.bus:
            name, params = parse_bus_key(key)
            await bus.publish(name, player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch, **params)
    return res


async def late_outcome(res: Resolution) -> str | None:
    """Claude's retelling of a resolved choice, asked after the window shows it (None: keep the authored text)."""
    if not res.late:
        return None
    text = await narrator.narrate_or("event_outcome", res.late["payload"], "", year=res.late.get("year"))
    return text or None


async def late_event_text(content: Content, ch: ActiveCharacter, event: Event) -> str | None:
    return await event_text(content, ch, event)


def cached_event_text(ch: ActiveCharacter, event: Event) -> tuple[bool, str | None]:
    """(known, text): Claude's text of this event is already at hand (or known to be authored)."""
    key = (ch.id, event.id, ch.state.weeks_lived)
    return (key in _event_texts, _event_texts.get(key))


def note_ledger(ch: ActiveCharacter, applied: AppliedEffects) -> None:
    """Count the eddies and the debt an event moved, for the money block of the next week report."""
    de, dd = int(applied.deltas.get("eddies", 0)), int(applied.deltas.get("debt", 0))
    if not (de or dd):
        return
    led = dict(ch.state.flags.get(FLAG_WEEK_LEDGER) or {})
    led["eddies"] = int(led.get("eddies", 0)) + de
    led["debt"] = int(led.get("debt", 0)) + dd
    ch.state.flags[FLAG_WEEK_LEDGER] = led


def _fought(applied: AppliedEffects) -> bool:
    return any(f.kind == "start_combat" for f in applied.followups)


# -- the fight exits ------------------------------------------------------------------------------------------


def exit_options(content: Content, ch: ActiveCharacter) -> list[exits.ExitOption]:
    pc = pending_combat(ch)
    if pc is None:
        return []
    return exits.build_exits(ch.state, content, pc["faction"], int(pc["danger"]))


async def accept_fight(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> "combat.Step":
    """Take the fight: the pending combat becomes a real fight (services/combat.py)."""
    pc = pending_combat(ch)
    if pc is None:
        raise LookupError("no pending fight")
    return await combat.begin(
        session, content, ch, rng, year, tz, faction=pc["faction"], danger=int(pc["danger"]), src=pc,
    )


def _exit_over(
    ch: ActiveCharacter, pc: dict[str, Any], result: str, content: Content, rng: random.Random, year: int,
) -> None:
    """The exits ended the fight before it began: a gig run needs to know how, a police raid cools or not."""
    from app.services import crime as crime_service

    crime_service.exit_over(content, ch, rng, year, str(pc.get("src", "")), result)
    if pc.get("chase"):  # owner request 26.10: a wave of the chase ended before the fight
        from app.services import wanted

        wanted.after_exit(content, ch, result)
    if pc.get("src") == "gig":
        gigs_service.fight_finished(ch, {"result": result, "kills": 0, "takedowns": 0, "ticks": 0, "dealt": 0,
                                         "taken": 0, "faction": pc["faction"], "danger": int(pc["danger"]),
                                         "skill": "firearms"})


async def attempt_exit(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, kind: str, step: int = 0,
) -> ExitResult:
    """Try pay / talk / flee / surrender against the pending fight's faction (PLAN 4.4, 5.6).

    A worked exit ends the matter (`avoided`); a failed one starts the fight with its penalty
    (`res.fight`): the money is taken anyway, the enemies hit first, or a free round.
    """
    pc = pending_combat(ch)
    if pc is None:
        raise LookupError("no pending fight")
    st = ch.state
    faction, danger = pc["faction"], int(pc["danger"])
    res = ExitResult(kind, None, None)

    if kind == exits.SURRENDER and pc.get("src") in psy_engine.CAPTURE_SRC:  # M6.6a: a psycho is taken alive
        st.flags.pop(FLAG_PENDING_COMBAT, None)
        from app.services import psychosis

        res.avoided, res.lines = True, await psychosis.begin_capture(session, content, ch, rng, year, tz)
        return res
    if kind == exits.SURRENDER:
        st.flags.pop(FLAG_PENDING_COMBAT, None)
        sr = exits.surrender(st, content, rng, faction)
        applied = apply_effects(st, dict(sr.effects), content, rng, year)
        fu_all = list(applied.followups)
        for key in sr.strip:
            fu_all.extend(apply_remove_cyberware(st, content, key, rng, year).followups)
        fu = await followups.handle(session, content, ch, fu_all, rng, year, tz)
        res.avoided, res.lines, res.died, res.death_cause = True, fu.lines, fu.died, fu.cause
        res.note = sr.outcome
        await repo.log_event(session, "surrender", {"faction": faction, "outcome": sr.outcome}, character_id=ch.id)
        if not res.died:
            _exit_over(ch, pc, "surrendered", content, rng, year)
    else:
        opt = exits.build_exit(st, content, faction, danger, kind, bribe_step=step)
        if not opt.possible:
            raise LookupError(f"exit {kind} is impossible")
        check = exits.roll_exit(st, content, rng, opt)
        if not check.ok and combat.can_rescue(content, ch, check):  # luck or a glitch may still save it
            st.flags[FLAG_PENDING_EXIT] = {"where": "pre", "kind": kind, "step": step, "check": asdict(check), "rev": 0}
            await save_active(session, ch, tz)
            res.check, res.pending = check, True
            return res
        return await finish_exit(session, content, ch, rng, year, tz, kind, step, check)
    if not res.died:
        await save_active(session, ch, tz)
    return res


async def finish_exit(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    kind: str, step: int, check: CheckResult,
) -> ExitResult:
    """The final check of a pay / talk / flee exit before the fight: a pass ends the matter, a fail starts the fight."""
    pc = pending_combat(ch)
    if pc is None:
        raise LookupError("no pending fight")
    st = ch.state
    faction, danger = pc["faction"], int(pc["danger"])
    opt = exits.build_exit(st, content, faction, danger, kind, bribe_step=step)
    res = ExitResult(kind, check.ok, check)
    if check.ok:
        st.flags.pop(FLAG_PENDING_COMBAT, None)
        if kind == exits.BRIBE:
            apply_effects(st, {"eddies": -opt.cost}, content, rng, year)
        combat.exit_succeeded(content, ch, rng, year, kind, faction)
        res.avoided = True
        _exit_over(ch, pc, "fled" if kind == exits.FLEE else "avoided", content, rng, year)
        await save_active(session, ch, tz)
        return res
    pre = ""
    if kind == exits.BRIBE:
        if exits.bribe_taken_anyway(content, rng):
            apply_effects(st, {"eddies": -opt.cost}, content, rng, year)
            res.note = "taken"
    elif kind == exits.TALK:
        pre = "talk_fail"
    else:
        pre = "flee_fail"
    res.fight = await combat.begin(session, content, ch, rng, year, tz, faction=faction, danger=danger, src=pc, pre=pre)
    if res.fight.end is not None and res.fight.end.died:
        res.died, res.death_cause = True, res.fight.end.cause
    return res
