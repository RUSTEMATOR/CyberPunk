"""Send the fighters out without the character (owner request 25.10 «отправлять бойцов в бой без тебя»).

Who goes: the own army (the best five, half, or everyone) and the hired city fighters (the squad). The mission is
played out by the power of the group against its difficulty: the chance, the loot, the losses. The fallen die for
real (an army fighter leaves the roster, a hired city fighter dies in the city). Up to `per_day` missions a day.
flag `dispatch_day` = {"d", "n"}.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.engine import army as army_eng
from app.engine import companion as comp
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

FLAG_DAY = "dispatch_day"
SIZES = {"five": "Лучшая пятёрка", "half": "Половина", "all": "Все"}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.dispatch


def mission(content: Content, key: str):  # noqa: ANN201
    return next((m for m in cfg(content).missions if m.key == key), None)


def group(content: Content, ch: ActiveCharacter, size: str) -> tuple[list[dict], list[dict]]:
    """(army fighters, hired city fighters) sent with this size."""
    roster = army_eng.strongest(ch.state, 10_000)
    n = {"five": 5, "half": max(1, len(roster) // 2), "all": len(roster)}.get(size, 5)
    return roster[:n], comp.active_members(ch.state)


def power(content: Content, soldiers: list[dict], squad: list[dict]) -> int:
    c = cfg(content)
    p = sum(int(s.get("danger", 1)) * (1 + c.xp_mult * int(s.get("xp", 0) or 0)) for s in soldiers)
    p += sum(int(m.get("danger", 2)) * c.squad_mult for m in squad)
    return int(round(p))


def who_text(soldiers: list[dict], squad: list[dict]) -> str:
    parts = ([f"бойцы ({len(soldiers)})"] if soldiers else []) + ([f"наёмники ({len(squad)})"] if squad else [])
    return " и ".join(parts)


def chance(pw: int, difficulty: int) -> float:
    return max(0.1, min(0.95, pw / max(1, pw + difficulty) * 1.4))


@dataclass
class Result:
    blocked: str | None = None
    ok: bool = False
    lines: list[str] = field(default_factory=list)


def done_today(ch: ActiveCharacter, tz: str) -> int:
    r = ch.state.flags.get(FLAG_DAY) or {}
    return int(r.get("n", 0)) if r.get("d") == kyiv_date(tz) else 0


async def send(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               key: str, size: str) -> Result:
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services import city_life, control, season

    c = cfg(content)
    m = mission(content, key)
    if m is None or size not in SIZES:
        return Result("gone")
    soldiers, squad = group(content, ch, size)
    if not soldiers and not squad:
        return Result("nobody")
    if jail_eng.in_jail(ch.state):
        return Result("jail")
    if done_today(ch, tz) >= c.per_day:
        return Result("today")
    w = None
    if m.needs == "pmc":
        from app.services import pmc

        w = await pmc.conflict(session, content, year)
        if not pmc.signed_side(ch, w):
            return Result("nopmc")
    if not ap_services.can_spend(ch, c.ap):
        return Result("ap")
    ap_services.spend(ch, content, c.ap)
    ch.state.flags[FLAG_DAY] = {"d": kyiv_date(tz), "n": done_today(ch, tz) + 1}
    pw = power(content, soldiers, squad)
    p_win = chance(pw, m.difficulty)
    ok = rng.random() < p_win
    risk = min(0.5, m.loss * m.difficulty / max(1, pw + m.difficulty) * (1 if ok else 2) * 2)
    fallen = [int(s["id"]) for s in soldiers if rng.random() < risk]
    fallen_npc = [int(x.get("npc", 0) or 0) for x in squad if rng.random() < risk * 0.7]
    who = who_text(soldiers, squad)
    lines = [f"🎯 {m.name}: ушли {who}. Сила {pw} против {m.difficulty}, шанс {round(p_win * 100)}%."]
    lines += army_eng.after_fight(content, ch.state, [int(s["id"]) for s in soldiers], fallen, ok) if soldiers else []
    if fallen_npc:
        lines += await city_life.squad_losses(session, content, ch, fallen_npc)
    if ok:
        lo, hi = m.reward
        loot = int(rng.randint(lo, hi) * (1 + min(1.0, max(0.0, pw / m.difficulty - 1)) / 2))
        fx: dict = {"eddies": loot, "street_cred": 1}
        if key == "corp":
            fx["heat"] = 1
        apply_effects(ch.state, fx, content, rng, year)
        lines.append(f"✅ Дело сделано: +{loot} эдди." + (" Розыск +1." if key == "corp" else ""))
        d = content.districts.get(ch.state.district)
        if key == "gang" and d is not None and d.city == "nc":
            await control.adjust(session, content, ch.state.district, gang=-5)
        elif key == "corp" and d is not None and d.city == "nc":
            await control.adjust(session, content, ch.state.district, corp=-5)
        elif key == "front" and w is not None:
            from app.db import repo
            from app.services import pmc

            side = pmc.signed_side(ch, w)
            w["front"] = max(-100, min(100, int(w["front"]) + (-1 if side == "a" else 1) * pmc.cfg(content).front_win // 2))
            await repo.ws_set(session, pmc.KEY, w)
            lines.append(f"Фронт: {pmc.front_word(content, w)}.")
            lines += await pmc._maybe_end(session, content, w, year, rng)  # noqa: SLF001
        await season.add_ch(session, content, ch, "dispatch", content.balance.season.pts.get("dispatch", 0))
    else:
        lines.append("❌ Не вышло: твои отходят ни с чем.")
    if not fallen and not fallen_npc:
        lines.append("Все вернулись живыми.")
    await save_active(session, ch, tz)
    return Result(None, ok, lines)
