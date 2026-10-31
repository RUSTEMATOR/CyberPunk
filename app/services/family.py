"""The family (owner request 23.10): looking for a partner and the family fund that passes to the heir whole."""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.engine import checks
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

FUND_KEY = "family_fund"   # world_state: {player tg id: eddies}


@dataclass
class FindResult:
    blocked: str | None = None
    ok: bool = False
    check: object | None = None
    name: str = ""
    applied: AppliedEffects | None = None


async def find_partner(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                       tz: str) -> FindResult:
    st = ch.state
    if st.npcs_of("partner"):
        return FindResult(blocked="have")
    if int(st.flags.get("jail_weeks", 0) or 0) > 0:
        return FindResult(blocked="jail")
    if not ap_services.can_spend(ch, content.balance.relations.action_ap):
        return FindResult(blocked="ap")
    ap_services.spend(ch, content, content.balance.relations.action_ap)
    chk = checks.roll_check(st, content, rng, "EMP", "persuasion", content.balance.relations.find_dv, ("social",))
    res = FindResult(ok=chk.ok, check=chk)
    if chk.ok:
        res.applied = apply_effects(st, {"add_npc:partner": True, "mood": 4}, content, rng, year)
        partners = st.npcs_of("partner")
        res.name = partners[-1].name if partners else ""
    else:
        res.applied = apply_effects(st, {"mood": -2}, content, rng, year)
    await save_active(session, ch, tz)
    return res


async def fund_of(session: AsyncSession, tg_id: int) -> int:
    got = await repo.ws_get(session, FUND_KEY, {}) or {}
    return int(got.get(str(tg_id), 0) or 0)


async def deposit(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  tz: str, amount: int) -> int | None:
    """Put eddies into the family fund. None when there is not enough money."""
    if amount <= 0 or ch.state.eddies < amount:
        return None
    apply_effects(ch.state, {"eddies": -amount}, content, rng, year)
    funds = dict(await repo.ws_get(session, FUND_KEY, {}) or {})
    funds[str(ch.player_tg_id)] = int(funds.get(str(ch.player_tg_id), 0) or 0) + amount
    await repo.ws_set(session, FUND_KEY, funds)
    await save_active(session, ch, tz)
    return int(funds[str(ch.player_tg_id)])


async def take_for_heir(session: AsyncSession, tg_id: int) -> int:
    """The heir opens the fund: all of it, once."""
    funds = dict(await repo.ws_get(session, FUND_KEY, {}) or {})
    amount = int(funds.pop(str(tg_id), 0) or 0)
    if amount:
        await repo.ws_set(session, FUND_KEY, funds)
    return amount
