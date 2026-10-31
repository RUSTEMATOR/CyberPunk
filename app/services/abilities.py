"""Using a chrome ability (owner request 26.10, docs/PLAN_CHROME.md): AP, a cooldown in Kyiv days, one of its events
plays at once. flag `ab_cd` = {ability: iso date it is free again}."""

from __future__ import annotations

import random
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.engine import abilities as eng
from app.services.game import ActiveCharacter, save_active

FLAG_CD = "ab_cd"


def free_on(ch: ActiveCharacter, key: str) -> str:
    return str((ch.state.flags.get(FLAG_CD) or {}).get(key, ""))


def ready(ch: ActiveCharacter, key: str, tz: str) -> bool:
    d = free_on(ch, key)
    return not d or d <= kyiv_date(tz)


async def use(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, tz: str,
              key: str) -> str | None:
    """Queue the ability's event at the front; a block reason or None (then play the event)."""
    from app.engine import events as ev_eng
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services.week import pending_events, set_pending

    ab = content.balance.chrome_abilities.get(key)
    if ab is None or key not in eng.owned(ch.state, content):
        return "gone"
    if jail_eng.in_jail(ch.state):
        return "jail"
    if not ready(ch, key, tz):
        return "cd"
    # owner request 26.10: the own scene first, then by turns the own scenes and the scenes of the family
    fam = eng.family(content, key)
    own = list(ab.events)
    extra = list(content.balance.chrome_abilities[fam].events) if fam != key and fam in content.balance.chrome_abilities else []
    seen = set(ch.state.flags.get("ab_seen") or [])
    first = [e for e in own if e not in seen]

    from app.engine import derived

    full = ch.state.hp >= derived.max_hp(ch.state, content)

    def heals(eid: str) -> bool:
        """Owner request 27.10: a healing scene is not spent on a character who is not hurt."""
        ev = content.events[eid]
        return any(c.ability and int((c.effects or {}).get("hp", 0) or 0) > 0 for c in ev.choices)

    def fit(ids: list[str]) -> list[str]:
        return [e for e in ids if e in content.events and ev_eng.matches(content.events[e], ch.state, content)
                and not (full and heals(e))]

    pool = fit(first) or fit(own + extra)
    if not pool:
        return "full" if full and any(e in content.events and heals(e) for e in own + extra) else "none"
    if not ap_services.can_spend(ch, ab.ap):
        return "ap"
    ap_services.spend(ch, content, ab.ap)
    cd = dict(ch.state.flags.get(FLAG_CD) or {})
    days = eng.cooldown(content, key, eng.ranks(ch.state, content).get(key, 1))
    cd[key] = (date.fromisoformat(kyiv_date(tz)) + timedelta(days=days)).isoformat()
    ch.state.flags[FLAG_CD] = cd
    last = str((ch.state.flags.get("ab_last") or {}).get(key, ""))
    pick = rng.choice([e for e in pool if e != last] or pool)
    ch.state.flags["ab_last"] = {**(ch.state.flags.get("ab_last") or {}), key: pick}
    if pick in own and pick not in seen:
        ch.state.flags["ab_seen"] = sorted(seen | {pick})
    set_pending(ch, [pick] + [e for e in pending_events(ch) if e != pick])
    await save_active(session, ch, tz)
    return None
