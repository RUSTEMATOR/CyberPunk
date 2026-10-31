"""Doing the activities of the week (PLAN 4.5): AP, eddies, effects, followups, log, bus.

`perform` runs every one-step activity; the gamble has two steps (a rolled check waits for the
luck / glitch / accept decision, like an event check) and lives in `start_gamble`, `gamble_luck`,
`gamble_glitch`, `gamble_accept`. The stake is taken when the die is rolled, so a closed window
can never dodge a loss. The rolled check sits in the flag `pending_check` with `kind: gamble`.
Everything changes the character through `apply_effects`; AP through `services.ap.spend`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.content.loader import Content
from app.db import repo
from app.engine import activities as act
from app.engine import checks
from app.engine import events as engine_events
from app.engine.checks import CheckResult
from app.engine.dice import D10
from app.engine.effects import AppliedEffects, apply_effects
from app.engine.state import FLAG_ADDICTION, FLAG_PENDING_CHECK, FLAG_RECENT_EVENTS
from app.narrator import narrator
from app.services import ap as ap_services
from app.services import followups
from app.services import game_events as bus
from app.services.game import ActiveCharacter, save_active
from app.services.week import pending_events, set_pending

ADDICTION_EVENT = "braindance_addiction"
GAMBLE_KIND = "gamble"


@dataclass
class ActivityResult:
    activity: str
    blocked: str | None = None  # an act.BLOCK_* reason; nothing was spent
    no_ap: bool = False
    plan: act.Plan | None = None
    applied: AppliedEffects | None = None
    check: CheckResult | None = None
    text: str = ""  # the flavour line
    lines: list[str] = field(default_factory=list)  # followup lines (vows, humanity warnings)
    queued_event: str | None = None  # an event that now waits in the queue
    died: bool = False
    death_cause: str | None = None
    # gamble
    stake: int = 0
    won: bool | None = None
    payout: int = 0
    late: dict | None = None  # Claude's line is asked after the window shows the authored one

    @property
    def done(self) -> bool:
        return self.blocked is None and not self.no_ap


# -- one-step activities ------------------------------------------------------------------------------


def _flavour(activity: str, rng: random.Random, key: str | None = None) -> str:
    pool = texts.ACT_FLAVOUR.get(key or activity) or [""]
    return rng.choice(pool)


def _queue_front(ch: ActiveCharacter, event_id: str) -> None:
    queue = [e for e in pending_events(ch) if e != event_id]
    set_pending(ch, [event_id] + queue)


async def perform(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, activity: str, *, skill: str | None = None, narrate: bool = True,
) -> ActivityResult:
    """Do one activity. Returns `blocked` / `no_ap` without changing anything when it cannot start."""
    st = ch.state
    res = ActivityResult(activity)
    reason = act.blocked(st, content, activity, skill=skill)
    if reason is not None:
        res.blocked = reason
        return res
    if not ap_services.can_spend(ch, act.ap_cost(content, activity)):
        res.no_ap = True
        return res

    queued = pending_events(ch)
    recent = list(st.flags.get(FLAG_RECENT_EVENTS) or [])
    if activity in (act.TRAIN, act.STUDY):
        if skill is None:
            raise ValueError("train / study need a skill")
        plan = act.plan_train(content, skill, study=activity == act.STUDY)
    elif activity == act.BAR:
        plan = act.plan_bar(st, content, year, rng, recent, queued)
    elif activity == act.BRAINDANCE:
        plan = act.plan_braindance(st, content, rng)
    elif activity == act.THERAPY:
        plan = act.plan_therapy(st, content, rng)
    elif activity == act.REST:
        plan = act.plan_rest(content)
    elif activity == act.MEDITATION:
        plan = act.plan_meditation(st, content, rng)
    elif activity == act.MARTIAL_ARTS:
        plan = act.plan_martial_arts(content)
    elif activity == act.SERVICE:
        plan = act.plan_service(content)
    else:
        raise ValueError(f"{activity!r} is not a one-step activity")

    if act.jail_free(st, content, activity):  # owner request 24.10: free in a cell
        plan.effects = {k: v for k, v in plan.effects.items() if k != "eddies"}
    ap_services.spend(ch, content, plan.ap)
    applied = apply_effects(st, dict(plan.effects), content, rng, year)
    res.plan, res.applied, res.check = plan, applied, plan.check

    if plan.addiction is not None and not plan.overdose:
        st.flags[FLAG_ADDICTION] = plan.addiction
    if plan.bar_event is not None:
        _queue_front(ch, plan.bar_event)
        res.queued_event = plan.bar_event
    if plan.addiction_event and ADDICTION_EVENT in content.events and ADDICTION_EVENT not in recent:
        _queue_front(ch, ADDICTION_EVENT)
        res.queued_event = ADDICTION_EVENT

    district = content.districts.get(st.district)
    flavour = _flavour(activity, rng, "meditation_fail" if plan.check is not None and not plan.check.ok else None)
    payload: dict[str, Any] = {
        "kind": "activity_text", "activity": activity,
        "character": {"name": st.name, "district": district.name if district else st.district, "role": st.role},
        "effects": applied.narrator_effects(),
    }
    res.text = flavour  # owner request: the window does not wait for Claude (see `late_text`)
    res.late = payload if narrate and narrator.is_live() else None

    fu = await followups.handle(session, content, ch, applied.followups, rng, year, tz)
    res.lines, res.died, res.death_cause = fu.lines, fu.died, fu.cause
    if not fu.died:
        await save_active(session, ch, tz)
    await repo.log_event(
        session, "activity",
        {"activity": activity, "skill": skill, "effects": applied.narrator_effects(),
         "queued_event": res.queued_event, "overdose": plan.overdose},
        character_id=ch.id,
    )
    if fu.died:
        return res
    name = act.BUS_NAME.get(activity)
    if name is not None:
        extra = {"skill": skill} if activity in (act.TRAIN, act.STUDY) else {}
        await bus.publish(name, player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch, **extra)
    return res


@dataclass
class ManyResult:
    """Many sessions of one activity at once (owner request: put N AP into one deed)."""

    last: ActivityResult
    done: int = 0
    asked: int = 0
    applied: AppliedEffects | None = None
    stopped: str | None = None  # why it stopped early: an act.BLOCK_* reason, "no_ap", "event", "died"


async def perform_many(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, activity: str, times: int, *, skill: str | None = None,
) -> ManyResult:
    """Up to `times` sessions in a row; stops at the first that cannot start, at a queued event or at death.
    Only the last session is narrated by Claude (one call, not a dozen)."""
    from app.engine.effects import merge_applied

    results: list[ActivityResult] = []
    stopped: str | None = None
    for i in range(max(1, times)):
        r = await perform(session, content, ch, rng, year, tz, activity, skill=skill, narrate=i == times - 1)
        if not r.done:
            if not results:
                return ManyResult(last=r, asked=times)
            stopped = r.blocked or "no_ap"
            break
        results.append(r)
        if r.died:
            stopped = "died"
            break
        if r.queued_event is not None:
            stopped = "event"
            break
    last = results[-1]
    merged = merge_applied([r.applied for r in results if r.applied is not None])
    return ManyResult(last=last, done=len(results), asked=times, applied=merged, stopped=stopped if len(results) < times else None)


# -- gambling -------------------------------------------------------------------------------------------


def pending_gamble(ch: ActiveCharacter) -> dict[str, Any] | None:
    p = ch.state.flags.get(FLAG_PENDING_CHECK)
    return p if p is not None and p.get("kind") == GAMBLE_KIND else None


def gamble_can_start(content: Content, ch: ActiveCharacter, stake: int) -> bool:
    """Whether `start_gamble` with this stake would roll (not blocked, enough AP): the slot machine spins only then."""
    return act.gamble_blocked(ch.state, content, stake) is None and ap_services.can_spend(ch, act.ap_cost(content, act.GAMBLE))


def gamble_can_reroll(ch: ActiveCharacter, rev: int) -> bool:
    """Whether `gamble_glitch` with this revision would reroll."""
    p = pending_gamble(ch)
    return p is not None and p.get("rev") == rev and checks.can_glitch_reroll(check_of(p), ch.state.glitches)


def _gamble(ch: ActiveCharacter, rev: int) -> dict[str, Any]:
    p = pending_gamble(ch)
    if p is None or p.get("rev") != rev:
        raise LookupError("no pending gamble, or an old button")
    return p


async def start_gamble(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, stake: int, die: D10 | None = None,
) -> ActivityResult | CheckResult:
    """Pay the stake and roll. A pass is settled at once (ActivityResult); a miss waits as pending
    (CheckResult) for luck, a glitch, or acceptance. `die` is the slot machine's roll (None rolls here)."""
    st = ch.state
    res = ActivityResult(act.GAMBLE, stake=stake)
    reason = act.gamble_blocked(st, content, stake)
    if reason is not None:
        res.blocked = reason
        return res
    if not ap_services.can_spend(ch, act.ap_cost(content, act.GAMBLE)):
        res.no_ap = True
        return res
    ap_services.spend(ch, content, act.ap_cost(content, act.GAMBLE))
    apply_effects(st, {"eddies": -stake}, content, rng, year)
    check = act.roll_gamble(st, content, rng, die)
    if check.ok:
        return await _settle_gamble(session, content, ch, rng, year, tz, stake, check)
    st.flags[FLAG_PENDING_CHECK] = {
        "kind": GAMBLE_KIND, "stake": stake, "check": _to_dict(check), "rev": 0,
    }
    await save_active(session, ch, tz)
    return check


def _to_dict(c: CheckResult) -> dict[str, Any]:
    from dataclasses import asdict
    return asdict(c)


def check_of(pending: dict[str, Any]) -> CheckResult:
    return CheckResult(**pending["check"])


async def gamble_luck(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, rev: int,
) -> CheckResult:
    p = _gamble(ch, rev)
    result = check_of(p)
    offer = engine_events.luck_offer(result, ch.state, content)
    if offer is None:
        raise LookupError("luck cannot save this roll")
    spent = checks.spend_luck(result, offer.points, ch.state.luck_pool, content.balance.luck)
    apply_effects(ch.state, spent.effects, content, rng, year)
    p.update(check=_to_dict(spent.result), rev=int(p["rev"]) + 1)
    ch.state.flags[FLAG_PENDING_CHECK] = p
    await save_active(session, ch, tz)
    return spent.result


async def gamble_glitch(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, rev: int, die: D10 | None = None,
) -> CheckResult:
    p = _gamble(ch, rev)
    result = check_of(p)
    if not checks.can_glitch_reroll(result, ch.state.glitches):
        raise LookupError("no glitch to spend")
    re = checks.glitch_reroll(result, ch.state.glitches, rng, content.balance.checks, die)
    apply_effects(ch.state, re.effects, content, rng, year)
    p.update(check=_to_dict(re.result), rev=int(p["rev"]) + 1)
    ch.state.flags[FLAG_PENDING_CHECK] = p
    await save_active(session, ch, tz)
    return re.result


async def gamble_accept(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, rev: int,
) -> ActivityResult:
    p = _gamble(ch, rev)
    return await _settle_gamble(session, content, ch, rng, year, tz, int(p["stake"]), check_of(p))


async def _settle_gamble(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, stake: int, check: CheckResult,
) -> ActivityResult:
    st = ch.state
    payout = act.gamble_payout(content, stake, check.ok)
    st.flags.pop(FLAG_PENDING_CHECK, None)
    applied = apply_effects(st, {"eddies": payout} if payout else {}, content, rng, year)
    res = ActivityResult(
        act.GAMBLE, check=check, applied=applied, stake=stake, won=check.ok, payout=payout,
        text=_flavour(act.GAMBLE, rng, "gamble_win" if check.ok else "gamble_lose"),
    )
    await save_active(session, ch, tz)
    await repo.log_event(
        session, "activity", {"activity": act.GAMBLE, "stake": stake, "won": check.ok, "payout": payout},
        character_id=ch.id,
    )
    return res



async def late_text(res: ActivityResult) -> str | None:
    """Claude's line for a finished activity, asked after the window shows the authored one."""
    if not res.late:
        return None
    return await narrator.narrate_or("activity_text", res.late, "") or None
