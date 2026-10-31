"""The price of power (owner request 25.10, docs/PLAN_ENDGAME2.md part 5): every lived month the body of a character
the city can no longer hold wears (engine/wear.py); the ripper's maintenance takes the wear off for money."""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import wear as eng
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active


async def outgrown(session: AsyncSession, content: Content, ch: ActiveCharacter) -> bool:
    from app.engine import challenge as chal_eng
    from app.services import challenge

    rec = (await challenge.book(session)).get(str(ch.id)) or {}
    return int(rec.get("level", 1) or 1) >= eng.cfg(content).from_level or chal_eng.overpowered(rec.get("report") or {}, content)


def describe(content: Content, lvl: int) -> str:
    m = eng.mods(content, lvl)
    parts = []
    if m.get("check.combat"):
        parts.append(f"бой {m['check.combat']}")
    if m.get("hp_max"):
        parts.append(f"здоровье {m['hp_max']}")
    if m.get("stat.REF"):
        parts.append(f"РЕА {m['stat.REF']}")
    return ", ".join(parts)


async def press_hook(session: AsyncSession, content: Content, ch: ActiveCharacter) -> list[str]:
    c = eng.cfg(content)
    st = ch.state
    lvl = eng.level(st)
    if not c.enabled or not await outgrown(session, content, ch):
        return []
    if lvl < c.max:
        lvl = min(c.max, lvl + c.per_month)
        st.flags[eng.FLAG] = lvl
    return [f"🦴 Износ тела {lvl}/{c.max}: {describe(content, lvl)}. Сила не бесплатна. Риппер снимет износ за "
            f"{eng.price(content, lvl)} эдди."]


async def maintain(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                   tz: str) -> str | None:
    lvl = eng.level(ch.state)
    if lvl <= 0:
        return "clean"
    cost = eng.price(content, lvl)
    if ch.state.eddies < cost:
        return "eddies"
    apply_effects(ch.state, {"eddies": -cost}, content, rng, year)
    ch.state.flags.pop(eng.FLAG, None)
    await save_active(session, ch, tz)
    return None
