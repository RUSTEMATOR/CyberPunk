"""The catalog of small deeds (content/actions*.yaml): leisure, more crime, gang tasks.

One deed: AP and eddies, an optional check, then one weighted outcome (of `success`, or `fail` after a missed check).
Effects go through `apply_effects` and followups (jail, a fight, death); `faction_rep:my_gang` hits the character's gang.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.content.schemas import Action, ActionOutcome, parse_bus_key
from app.db import repo
from app.engine import checks
from app.engine import events as engine_events
from app.engine.checks import CheckResult
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services import followups
from app.services import game_events as bus
from app.services.game import ActiveCharacter, save_active

FLAG_DAILY = "actions_daily"  # {action key: Kyiv date of the last time}
GANG_PARAM = "my_gang"

BLOCK_CONDITIONS = "conditions"
BLOCK_EDDIES = "eddies"
BLOCK_TODAY = "today"
BLOCK_JAIL = "jail"


def available(content: Content, ch: ActiveCharacter, section: str, year: int) -> list[Action]:
    """The deeds of a section whose conditions hold now (time windows included)."""
    from app.engine import derived

    flags = derived.event_flags(ch.state, content)
    out = []
    for a in content.available("actions", year).values():
        if a.section == section and engine_events.matches(a, ch.state, content, flags):  # type: ignore[arg-type]
            out.append(a)
    return out


def _today(tz: str) -> str:
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def blocked(content: Content, ch: ActiveCharacter, action: Action, tz: str) -> str | None:
    from app.engine import jail as jail_engine

    if jail_engine.in_jail(ch.state):
        return BLOCK_JAIL
    if not engine_events.matches(action, ch.state, content):  # type: ignore[arg-type]
        return BLOCK_CONDITIONS
    if ch.state.eddies < action.cost:
        return BLOCK_EDDIES
    if action.daily and (ch.state.flags.get(FLAG_DAILY) or {}).get(action.key) == _today(tz):
        return BLOCK_TODAY
    return None


def _resolve_gang(ch: ActiveCharacter, effects: dict) -> dict:
    gang = (ch.state.flags.get("gang") or {}).get("key")
    out = {}
    for k, v in effects.items():
        if k.endswith(":" + GANG_PARAM):
            if gang:
                out[k.replace(GANG_PARAM, gang)] = v
            continue
        out[k] = v
    return out


def _pick(rng: random.Random, outcomes: list[ActionOutcome]) -> ActionOutcome:
    total = sum(o.weight for o in outcomes)
    x = rng.uniform(0, total)
    for o in outcomes:
        x -= o.weight
        if x <= 0:
            return o
    return outcomes[-1]


@dataclass
class ActionResult:
    action: Action
    blocked: str | None = None
    no_ap: bool = False
    check: CheckResult | None = None
    text: str = ""
    applied: AppliedEffects | None = None
    lines: list[str] = field(default_factory=list)
    died: bool = False
    death_cause: str | None = None
    queued_event: str | None = None  # an event that plays right after (the result screen leads to it)


async def perform(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, key: str,
) -> ActionResult | None:
    action = content.actions.get(key)
    if action is None:
        return None
    res = ActionResult(action)
    res.blocked = blocked(content, ch, action, tz)
    if res.blocked is not None:
        return res
    if not ap_services.can_spend(ch, action.ap):
        res.no_ap = True
        return res
    if action.ap:
        ap_services.spend(ch, content, action.ap)
    ok = True
    if action.check is not None:
        c = action.check
        res.check = checks.roll_check(ch.state, content, rng, c.stat, c.skill, c.dv, ())
        ok = res.check.ok
    outcome = _pick(rng, action.success if ok else action.fail)
    res.text = outcome.text
    fx = _resolve_gang(ch, dict(outcome.effects))
    if action.cost:
        fx["eddies"] = int(fx.get("eddies", 0)) - action.cost
    res.applied = apply_effects(ch.state, fx, content, rng, year)
    if action.daily:
        daily = dict(ch.state.flags.get(FLAG_DAILY) or {})
        daily[action.key] = _today(tz)
        ch.state.flags[FLAG_DAILY] = daily
    fu = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
    res.lines, res.died, res.death_cause = fu.lines, fu.died, fu.cause
    if fu.died:
        return res
    if outcome.event is not None and outcome.event in content.events:
        from app.services.week import pending_events, set_pending

        set_pending(ch, [outcome.event] + [e for e in pending_events(ch) if e != outcome.event])
        res.queued_event = outcome.event
    await save_active(session, ch, tz)
    await repo.log_event(session, "action", {"key": action.key, "ok": ok}, character_id=ch.id)
    if outcome.loud:
        from app.services import loud

        await loud.post(session, ch.id, outcome.loud.format(name=loud.who(ch.state.name, ch.state.surname)))
    await bus.publish("action_done", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch,
                      section=action.section, action=action.key, ok=ok)
    if ok:
        for b in action.bus:
            name, params = parse_bus_key(b)
            if name == "crime_done":
                params = {**params, "success": True}
            await bus.publish(name, player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch, **params)
    return res
