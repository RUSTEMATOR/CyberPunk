"""The bodyguard of a corporate head (owner request 25.10 «за огромные деньги становиться телохранителями глав
корпораций», docs/PLAN_ELITE.md part 3).

For strong characters (the challenge ladder from `min_ladder`, or the city measures them outgrown): a contract with a
head of a megacorp for `months` lived months. The advance on signing, the pay every lived month, the bonus at the end.
Every month an attempt on the person: an elite fight «protect the person» (engine/elite.py). A month left without the
fight, or the person dead, breaks the contract: no more pay and the corp's standing falls.

flag `escort` = {"corp", "vip", "title", "monthly", "left" (months), "due" (an attempt waits), "won"}
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

FLAG = "escort"
SRC = "escort"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.escort


def current(ch: ActiveCharacter) -> dict | None:
    r = ch.state.flags.get(FLAG)
    return dict(r) if isinstance(r, dict) and r.get("corp") else None


async def ladder(session: AsyncSession, ch: ActiveCharacter) -> int:
    from app.services import corpraid

    return await corpraid.ladder(session, ch)


async def eligible(session: AsyncSession, content: Content, ch: ActiveCharacter) -> bool:
    from app.services import wear

    return await ladder(session, ch) >= cfg(content).min_ladder or await wear.outgrown(session, content, ch)


FLAG_DEAD = "escort_dead"  # names of the heads who died on the character's watch: they never come back
PER_CORP = 3


def roster(content: Content, corp: str) -> list[tuple[str, str]]:
    """Owner report 26.10 «с именами явно баг»: every megacorp has its own fixed heads (name, title), a name never
    serves two corps and never changes its post."""
    c = cfg(content)
    keys = sorted(k for k, v in content.corps.items() if v.kind == "megacorp")
    if corp not in keys or not c.vip_names or not c.titles:
        return []
    i = keys.index(corp)
    names = c.vip_names[i * PER_CORP:(i + 1) * PER_CORP]
    return [(n, c.titles[(i + j) % len(c.titles)]) for j, n in enumerate(names)]


def offers(content: Content, ch: ActiveCharacter, year: int, lv: int) -> list[dict]:
    """One offer per megacorp of the year; the same for the whole lived month (seeded by the week)."""
    c = cfg(content)
    rng = random.Random(f"{ch.id}:{ch.state.weeks_lived // max(1, content.balance.time.weeks_per_press)}")
    dead = set(ch.state.flags.get(FLAG_DEAD) or [])
    out = []
    for key, corp in content.available("corps", year).items():
        if corp.kind != "megacorp":
            continue
        monthly = c.monthly * max(1, lv) * rng.choice([8, 9, 10, 11, 12]) // 10
        alive = [p for p in roster(content, key) if p[0] not in dead]
        if not alive:  # every head of this corp died on your watch: nobody left who would hire you
            continue
        vip, title = rng.choice(alive)
        out.append({"corp": key, "vip": vip, "title": title, "monthly": monthly})
    return out


async def sign(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               corp: str) -> str | None:
    c = cfg(content)
    if current(ch) is not None:
        return "busy"
    if not await eligible(session, content, ch):
        return "small"
    o = next((x for x in offers(content, ch, year, await ladder(session, ch)) if x["corp"] == corp), None)
    if o is None:
        return "gone"
    advance = o["monthly"] * c.advance_pct // 100
    apply_effects(ch.state, {"eddies": advance}, content, rng, year)
    ch.state.flags[FLAG] = {**o, "left": c.months, "due": True, "won": 0}
    await save_active(session, ch, tz)
    return None


async def _fail(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                why: str) -> list[str]:
    from app.services import loud

    r = current(ch) or {}
    ch.state.flags.pop(FLAG, None)
    corp = content.corps.get(str(r.get("corp")))
    apply_effects(ch.state, {f"faction_rep:{r.get('corp')}": cfg(content).fail_rep, "street_cred": -2}, content, rng, year)
    name = f"{r.get('title', '')} {corp.name if corp else ''} {r.get('vip', '')}".strip()
    if r.get("vip"):
        ch.state.flags[FLAG_DEAD] = sorted(set(ch.state.flags.get(FLAG_DEAD) or []) | {str(r["vip"])})
    await loud.post(session, ch.id, f"🕴 Охрана не уберегла: {name}. Телохранитель — "
                                    f"{loud.who(ch.state.name, ch.state.surname)}.", kind="escort_fail", w=2)
    return [f"🕴 Контракт сорван: {why}. {corp.name if corp else 'Корпорация'} это запомнит."]


async def press_hook(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random,
                     year: int) -> list[str]:
    """Every lived month: an attempt left unanswered breaks the contract; otherwise the pay and a new attempt."""
    from app.services import loud, season

    r = current(ch)
    if r is None:
        return []
    c = cfg(content)
    if r.get("due"):
        return await _fail(session, content, ch, rng, year, f"покушение прошло без охраны ({r.get('vip')})")
    pay = int(r["monthly"])
    apply_effects(ch.state, {"eddies": pay}, content, rng, year)
    r["left"] = int(r.get("left", 1)) - 1
    corp = content.corps.get(str(r["corp"]))
    if r["left"] <= 0:
        bonus = pay * c.bonus_pct // 100
        apply_effects(ch.state, {"eddies": bonus, f"faction_rep:{r['corp']}": c.done_rep, "fame": 2}, content, rng, year)
        ch.state.flags.pop(FLAG, None)
        await season.add_ch(session, content, ch, "escort", content.balance.season.pts.get("escort", 0))
        await loud.post(session, ch.id, f"🕴 {loud.who(ch.state.name, ch.state.surname)} доводит контракт до конца: "
                                        f"{r.get('title', '')} {corp.name if corp else ''} {r.get('vip')} без единой "
                                        "царапины.", kind="escort", w=2)
        return [f"🕴 Охрана: +{pay} эдди за месяц и премия +{bonus}. Контракт с {corp.name if corp else ''} закрыт."]
    r["due"] = True
    ch.state.flags[FLAG] = r
    return [f"🕴 Охрана: +{pay} эдди за месяц. Готовится новое покушение ({r.get('vip')}): «Работа» → «Контракты элиты»."]


def _attackers(content: Content, year: int, corp: str, rng: random.Random) -> str:
    from app.engine import combat as cb

    pool = [k for k, c in content.available("corps", year).items()
            if c.kind == "megacorp" and k != corp and cb._faction_pool(content, k, year)]  # noqa: SLF001
    return rng.choice(pool) if pool else "militech"


async def fight(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str):  # noqa: ANN201
    from app.engine import elite
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services import combat
    from app.services import events as ev_service
    from app.services.combat import _store

    r = current(ch)
    if r is None or not r.get("due"):
        return "none"
    if jail_eng.in_jail(ch.state):
        return "jail"
    if ev_service.has_pending(ch):
        return "busy"
    if not ap_services.can_spend(ch, cfg(content).fight_ap):
        return "ap"
    ap_services.spend(ch, content, cfg(content).fight_ap)
    await save_active(session, ch, tz)
    foe = _attackers(content, year, str(r["corp"]), rng)
    step = await combat.begin(session, content, ch, rng, year, tz, faction=foe, danger=5,
                              src={"src": SRC, "solo": True, "title": f"покушение: {r.get('vip')}"})
    elite.setup(step.cs, content, rng, year, boss="Командир ударной группы", vip=str(r.get("vip")))
    _store(ch, step.cs)
    await save_active(session, ch, tz)
    return step


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb

    r = current(ch)
    if r is None:
        return []
    alive = bool(ctx.get("vip_alive", True))
    if result != cb.WON or not alive:
        lines = await _fail(session, content, ch, rng, year,
                            f"{r.get('vip')} погиб" if not alive else "нападавшие добрались до охраняемого")
        await save_active(session, ch, tz)
        return lines
    r["due"] = False
    r["won"] = int(r.get("won", 0)) + 1
    ch.state.flags[FLAG] = r
    apply_effects(ch.state, {"street_cred": 1}, content, rng, year)
    await save_active(session, ch, tz)
    return [f"🕴 Покушение отбито: {r.get('vip')} жив. Оплата — в конце прожитого месяца."]
