"""Career paths (owner request): choose a path, do its deeds, the mayor's seat, the fixer's contracts to players.

The rules are in engine/paths.py; the numbers in balance.yaml `paths`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import PlayerMail
from app.engine import paths as pe
from app.engine import players as pl
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services import game
from app.services import players as psvc
from app.services.game import ActiveCharacter, save_active

CONTRACT = "contract"  # the offer kind of a fixer's job for another player
MAYOR_KEY = "mayor"    # world_state: {"char", "tg", "name"} of the one in the seat

R_SWITCH = "switch"   # not enough money for a new path
R_SAME = "same"


def _jailed(ch: ActiveCharacter) -> bool:
    return int(ch.state.flags.get("jail_weeks", 0) or 0) > 0


def _name(ch: ActiveCharacter) -> str:
    return f"{ch.state.name} {ch.state.surname}".strip()


async def choose(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str,
                 key: str) -> str | None:
    if pe.path_def(content, key) is None:
        return pe.R_GONE
    cur = pe.record(ch.state).get("key")
    if cur == key:
        return R_SAME
    price = content.balance.paths.switch_price if cur else 0
    if ch.state.eddies < price:
        return R_SWITCH
    if price:
        apply_effects(ch.state, {"eddies": -price}, content, random.Random(), year)
    if cur and pe.record(ch.state).get("seat"):
        await _drop_seat(session, ch.id)
    pe.choose(ch.state, content, key)
    await save_active(session, ch, tz)
    return None


async def _drop_seat(session: AsyncSession, char_id: int) -> None:
    seat = await repo.ws_get(session, MAYOR_KEY)
    if isinstance(seat, dict) and int(seat.get("char", 0) or 0) == char_id:
        await repo.ws_set(session, MAYOR_KEY, None)


async def mayor(session: AsyncSession) -> dict | None:
    seat = await repo.ws_get(session, MAYOR_KEY)
    return seat if isinstance(seat, dict) and seat.get("char") else None


@dataclass
class ActResult:
    blocked: str | None = None
    ok: bool = False
    check: object | None = None
    applied: AppliedEffects | None = None
    pay: int = 0
    seat_from: str = ""      # the name of the mayor who lost the seat
    seat_won: bool = False


async def act(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              key: str) -> ActResult:
    p = pe.path_of(ch.state, content)
    a = pe.action(content, p.key, key) if p is not None else None
    if p is None or a is None:
        return ActResult(blocked=pe.R_GONE)
    why = pe.action_blocked(ch.state, content, a, jailed=_jailed(ch))
    if why is not None:
        return ActResult(blocked=why)
    n = pe.rank(ch.state, content)
    if a.ap:
        ap_services.spend(ch, content, a.ap)
    from app.engine import checks

    r = checks.roll_check(ch.state, content, rng, a.stat, a.skill, a.dv, tuple(a.contexts))
    res = ActResult(ok=bool(r.ok), check=r)
    fx: dict[str, int | bool] = {}
    if a.price:
        fx["eddies"] = -a.price
    if r.ok:
        res.pay = pe.pay(content, a, n, rng)
        fx["eddies"] = int(fx.get("eddies", 0)) + res.pay
        for k, v in a.effects.items():
            fx[k] = v
        if a.xp:
            fx[f"path_xp:{p.key}"] = a.xp
    else:
        for k, v in a.fail_effects.items():
            fx[k] = v
        if a.fail_xp:
            fx[f"path_xp:{p.key}"] = a.fail_xp
    fx = {k: v for k, v in fx.items() if v}
    if r.ok and a.seat:  # the seat of the city: whoever held it loses it
        old = await mayor(session)
        if old is not None and int(old.get("char", 0) or 0) != ch.id:
            res.seat_from = str(old.get("name", ""))
            session.add(PlayerMail(
                to_tg_id=int(old.get("tg", 0) or 0), from_tg_id=0, from_name="Город", kind="city",
                to_char_id=int(old["char"]),
                payload={"text": f"Выборы проиграны: в кресле мэра теперь {_name(ch)}.", "seat_lost": True},
            ))
        await repo.ws_set(session, MAYOR_KEY, {"char": ch.id, "tg": ch.player_tg_id, "name": _name(ch)})
        pe.set_seat(ch.state, True)
        res.seat_won = True
    res.applied = apply_effects(ch.state, fx, content, rng, year) if fx else None
    await save_active(session, ch, tz)
    await session.flush()
    return res


# -- the fixer's contracts ------------------------------------------------------------------------------------------------


async def send_contract(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str,
                        char_id: int, job_key: str, approach: str, share: int) -> tuple[str | None, psvc.Other | None]:
    cfg = content.balance.paths.contract
    job = pe.contract_job(content, job_key)
    if not pe.is_fixer(ch.state, content) or job is None or approach not in pl.JOB_APPROACHES or share not in cfg.shares:
        return pe.R_GONE, None
    if job.min_rank > pe.rank(ch.state, content):
        return pe.R_RANK, None
    target = await psvc.other(session, content, ch, char_id, tz)
    if target is None:
        return pe.R_GONE, None
    res = await session.execute(select(PlayerMail).where(
        PlayerMail.from_tg_id == ch.player_tg_id, PlayerMail.to_tg_id == target.tg_id, PlayerMail.kind == pl.OFFER,
        PlayerMail.delivered_at.is_(None)))
    if any((m.payload or {}).get("offer") == CONTRACT for m in res.scalars()):
        return pl.BLOCK_PENDING, target
    session.add(PlayerMail(
        to_tg_id=target.tg_id, from_tg_id=ch.player_tg_id, from_char_id=ch.id, to_char_id=target.char_id,
        from_name=_name(ch), from_year=year, kind=pl.OFFER,
        payload={"offer": CONTRACT, "job": job.key, "approach": approach, "share": share,
                 "rank": pe.rank(ch.state, content), "g": psvc.gender(ch)},
    ))
    await session.flush()
    return None, target


@dataclass
class ContractResult:
    blocked: str | None = None
    roll: pe.ContractRoll | None = None
    applied: AppliedEffects | None = None
    from_name: str = ""
    from_tg: int = 0
    job: str = ""
    cut: int = 0


async def answer_contract(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                          tz: str, mail_id: int) -> ContractResult | None:
    """The executor takes the contract: the check is rolled now with the fixer's plan."""
    m = await session.get(PlayerMail, mail_id)
    if m is None or m.to_tg_id != ch.player_tg_id or m.kind != pl.OFFER or m.delivered_at is not None:
        return None
    p = dict(m.payload or {})
    if p.get("offer") != CONTRACT:
        return None
    cfg = content.balance.paths.contract
    res = ContractResult(from_name=m.from_name, from_tg=m.from_tg_id, job=str(p.get("job", "")))
    job = pe.contract_job(content, res.job)
    approach = str(p.get("approach", "loud"))
    m.delivered_at = psvc._utcnow()
    if job is None or approach not in pl.JOB_APPROACHES:
        res.blocked = pl.BLOCK_GONE
        await session.flush()
        return res
    if _jailed(ch):
        res.blocked = pl.BLOCK_JAIL
        m.delivered_at = None
        return res
    r = pe.contract_roll(ch.state, content, rng, job, approach, int(p.get("rank", 1) or 1))
    res.roll = r
    fixer_cut, mine = pe.split(job.pool, int(p.get("share", 50) or 50))
    if r.ok:
        fx: dict[str, int | bool] = {"eddies": mine, "street_cred": cfg.cred}
        res.cut = mine
    else:
        lo, hi = cfg.fail_hp
        fx = {"hp": -min(rng.randint(lo, hi), max(0, ch.state.hp - 1)), "heat": cfg.fail_heat}
    fx = {k: v for k, v in fx.items() if v}
    res.applied = apply_effects(ch.state, fx, content, rng, year)
    await save_active(session, ch, tz)
    session.add(PlayerMail(
        to_tg_id=m.from_tg_id, from_tg_id=ch.player_tg_id, from_char_id=ch.id, to_char_id=m.from_char_id,
        from_name=_name(ch), from_year=year, kind=pl.REPLY,
        payload={"offer": CONTRACT, "accepted": True, "g": psvc.gender(ch), "job": job.key, "ok": r.ok,
                 "cut": fixer_cut if r.ok else 0, "xp": cfg.xp if r.ok else cfg.fail_xp},
    ))
    await session.flush()
    return res


def reply_effects(p: dict) -> dict[str, int | bool]:
    """What the answer of the executor brings to the fixer."""
    fx: dict[str, int | bool] = {"eddies": int(p.get("cut", 0) or 0), "path_xp:fixer": int(p.get("xp", 0) or 0)}
    if p.get("ok"):
        fx["street_cred"] = 1
    return {k: v for k, v in fx.items() if v}


async def candidates(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str) -> list[psvc.Other]:
    return await psvc.others(session, content, ch, tz)


async def year_of(session: AsyncSession, content: Content) -> int:
    return await game.current_year(session, content)
