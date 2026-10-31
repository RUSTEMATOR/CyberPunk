"""A checked action of M9 with the luck / glitch decision: a promotion, a crime, a prison escape.

The shape is the one of the gamble (services/activities.py): AP is spent when the die is rolled, a
failed check that luck or a glitch could still save waits in `flags["pending_check"]` with `kind:
"life"`, so closing the window cannot buy a re-roll. Each action registers a settler that applies the
outcome once the check is final; `LifeResult` carries what the screen needs.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import checks
from app.engine import events as engine_events
from app.engine.checks import CheckResult
from app.engine.effects import AppliedEffects, apply_effects
from app.engine.state import FLAG_PENDING_CHECK
from app.services.game import ActiveCharacter, save_active

KIND = "life"


@dataclass
class LifeResult:
    action: str  # promo | crime | escape | ...
    ok: bool | None = None
    check: CheckResult | None = None
    applied: AppliedEffects | None = None
    lines: list[str] = field(default_factory=list)  # followup lines (vows, humanity warnings)
    died: bool = False
    death_cause: str | None = None
    data: dict[str, Any] = field(default_factory=dict)  # facts of the action for its screen
    blocked: str | None = None
    no_ap: bool = False

    @property
    def done(self) -> bool:
        return self.blocked is None and not self.no_ap


Settler = Callable[..., Awaitable[LifeResult]]
_settlers: dict[str, Settler] = {}


def register(action: str, settler: Settler) -> None:
    """`settler(session, content, ch, rng, year, tz, args, check) -> LifeResult` applies the final outcome."""
    _settlers[action] = settler


def pending(ch: ActiveCharacter) -> dict[str, Any] | None:
    p = ch.state.flags.get(FLAG_PENDING_CHECK)
    return p if p is not None and p.get("kind") == KIND else None


def check_of(p: dict[str, Any]) -> CheckResult:
    return CheckResult(**p["check"])


def can_rescue(content: Content, ch: ActiveCharacter, check: CheckResult) -> bool:
    return (engine_events.luck_offer(check, ch.state, content) is not None
            or checks.can_glitch_reroll(check, ch.state.glitches))


async def start(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    action: str, check: CheckResult, args: dict[str, Any],
) -> LifeResult | CheckResult:
    """A rolled check: settled now when it passed or nothing can save it, else parked as pending."""
    if check.ok or not can_rescue(content, ch, check):
        return await settle(session, content, ch, rng, year, tz, action, args, check)
    ch.state.flags[FLAG_PENDING_CHECK] = {"kind": KIND, "action": action, "args": args, "check": asdict(check), "rev": 0}
    await save_active(session, ch, tz)
    return check


def _get(ch: ActiveCharacter, rev: int) -> dict[str, Any]:
    p = pending(ch)
    if p is None or p.get("rev") != rev:
        raise LookupError("no pending check, or an old button")
    return p


async def luck(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, rev: int,
) -> CheckResult:
    p = _get(ch, rev)
    result = check_of(p)
    offer = engine_events.luck_offer(result, ch.state, content)
    if offer is None:
        raise LookupError("luck cannot save this roll")
    spent = checks.spend_luck(result, offer.points, ch.state.luck_pool, content.balance.luck)
    apply_effects(ch.state, spent.effects, content, rng, year)
    p.update(check=asdict(spent.result), rev=int(p["rev"]) + 1)
    ch.state.flags[FLAG_PENDING_CHECK] = p
    await save_active(session, ch, tz)
    return spent.result


async def glitch(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, rev: int,
) -> CheckResult:
    p = _get(ch, rev)
    result = check_of(p)
    if not checks.can_glitch_reroll(result, ch.state.glitches):
        raise LookupError("no glitch to spend")
    re = checks.glitch_reroll(result, ch.state.glitches, rng, content.balance.checks)
    apply_effects(ch.state, re.effects, content, rng, year)
    p.update(check=asdict(re.result), rev=int(p["rev"]) + 1)
    ch.state.flags[FLAG_PENDING_CHECK] = p
    await save_active(session, ch, tz)
    return re.result


async def accept(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, rev: int,
) -> LifeResult:
    p = _get(ch, rev)
    return await settle(session, content, ch, rng, year, tz, str(p["action"]), dict(p["args"]), check_of(p))


async def settle(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    action: str, args: dict[str, Any], check: CheckResult,
) -> LifeResult:
    ch.state.flags.pop(FLAG_PENDING_CHECK, None)
    return await _settlers[action](session, content, ch, rng, year, tz, args, check)
