"""The character's own fighters (owner request 25.10, engine/army.py): recruitment, the fighters in every fight, the
month's wages and racket, the war on a gang."""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.db.models import CityNpc
from app.engine import army as eng
from app.engine import checks
from app.engine import combat as cb
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

SRC_WAR = "awar"
FLAG_DAY = "army_day"  # {"d": kyiv date, "street": n, "war": n}


def _day(ch: ActiveCharacter, tz: str) -> dict:
    r = dict(ch.state.flags.get(FLAG_DAY) or {})
    today = kyiv_date(tz)
    return r if r.get("d") == today else {"d": today}


def _blocked(ch: ActiveCharacter) -> str | None:
    from app.engine import jail as jail_eng
    from app.services import events as ev_service

    if jail_eng.in_jail(ch.state):
        return "jail"
    if ev_service.has_pending(ch):
        return "busy"
    return None


# -- recruitment -----------------------------------------------------------------------------------------------------


async def street(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                 tz: str) -> tuple[str | None, list[str], object | None]:
    """Recruitment on the street: AP, money and a check; one to three green fighters."""
    from app.services import ap as ap_services

    c = eng.cfg(content)
    st = ch.state
    r = _blocked(ch)
    if r:
        return r, [], None
    day = _day(ch, tz)
    if int(day.get("street", 0)) >= c.street_per_day:
        return "today", [], None
    if st.eddies < c.street_price:
        return "eddies", [], None
    if not ap_services.can_spend(ch, c.street_ap):
        return "ap", [], None
    ap_services.spend(ch, content, c.street_ap)
    apply_effects(st, {"eddies": -c.street_price}, content, rng, year)
    day["street"] = int(day.get("street", 0)) + 1
    st.flags[FLAG_DAY] = day
    chk = checks.roll_check(st, content, rng, "COOL", "persuasion", c.street_dv, ("social",))
    lines: list[str] = []
    if chk.ok:
        n = min(c.street_max, 1 + max(0, chk.margin) // c.street_per_margin)
        rec = eng.rec(st)
        new = [eng.make_soldier(content, rec, rng, rng.choice([1, 1, 2])) for _ in range(n)]
        rec["soldiers"] = list(rec.get("soldiers") or []) + new
        eng.save(st, rec)
        lines.append(f"Ты ставишь выпивку в подворотне и говоришь о деньгах и уважении. К тебе идут: "
                     + ", ".join(s["name"] for s in new) + ".")
    else:
        lines.append("Слушают, кивают, берут выпивку и расходятся. Сегодня никто не пошёл.")
    await save_active(session, ch, tz)
    return None, lines, chk


def npc_price(content: Content, danger: int) -> int:
    from app.engine import companion as comp

    return int(comp.price(content, danger) * eng.cfg(content).npc_price_mult)


async def recruit_npc(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                      tz: str, npc_id: int) -> tuple[str | None, str]:
    """A city resident joins for good: armed, likes the character, paid once."""
    from app.engine import city as ce
    from app.services import city, city_life

    npc = await session.get(CityNpc, npc_id)
    if npc is None or not npc.alive:
        return "gone", ""
    arch = ce.archetype(content, npc.archetype)
    if arch is None or not arch.armed:
        return "not", ""
    if not city_life.can_hire(content, ch, npc):
        return "att", ""
    if any(int(s.get("npc", 0) or 0) == npc.id for s in eng.soldiers(ch.state)):
        return "have", ""
    price = npc_price(content, arch.danger)
    if ch.state.eddies < price:
        return "eddies", ""
    apply_effects(ch.state, {"eddies": -price}, content, rng, year)
    rec = eng.rec(ch.state)
    s = eng.make_soldier(content, rec, rng, int(arch.danger or 2), name=npc.name, npc=npc.id)
    rec["soldiers"] = list(rec.get("soldiers") or []) + [s]
    eng.save(ch.state, rec)
    city.remember(content, npc, ch.id, f"{ch.state.name}: взял в свои люди", 10)
    await save_active(session, ch, tz)
    return None, f"{npc.name} теперь в твоих людях. Навсегда, пока жив. −{price} эдди."


# -- the fight ---------------------------------------------------------------------------------------------------------


def join(content: Content, cs: cb.CombatState, ch: ActiveCharacter, rng: random.Random, year: int, war: bool = False) -> int:
    """The strongest fighters stand at the character's side; ctx["army"] = [[ally index, soldier id]]."""
    c = eng.cfg(content)
    roster = eng.soldiers(ch.state)
    t = eng.tier(content, len(roster))
    if t is None:
        return 0
    n = t.in_fight * (c.war_ally_mult if war else 1)
    n = min(n, max(0, c.ally_cap - len(cs.allies)))
    picked = eng.strongest(ch.state, n)
    pairs: list[list[int]] = []
    for s in picked:
        idx = len(cs.allies)
        a = cb.build_ally(content, year, int(s.get("danger", 1)), idx, str(s.get("name", "Боец")), rng)
        cs.allies.append(a)
        pairs.append([idx, int(s["id"])])
    if pairs:
        cs.ctx["army"] = pairs
    return len(pairs)


def fight_end(content: Content, ch: ActiveCharacter, cs: cb.CombatState, result: str, rng: random.Random) -> list[str]:
    """After any fight with the fighters in it: the dead, the experience, a defector of a beaten gang."""
    c = eng.cfg(content)
    pairs = cs.ctx.get("army") or []
    fought = [int(sid) for _, sid in pairs]
    fallen = [int(sid) for idx, sid in pairs if int(idx) < len(cs.allies) and cs.allies[int(idx)].out]
    won = result == cb.WON
    lines = eng.after_fight(content, ch.state, fought, fallen, won) if pairs else []
    if won and cs.faction in content.gangs and str(cs.ctx.get("src")) != SRC_WAR and rng.random() < c.defector_chance:
        rec = eng.rec(ch.state)
        s = eng.make_soldier(content, rec, rng, rng.choice([1, 2]))
        rec["soldiers"] = list(rec.get("soldiers") or []) + [s]
        eng.save(ch.state, rec)
        lines.append(f"🤝 Из побеждённых к тебе просится {s['name']} и остаётся в твоих людях.")
    return lines


# -- the month ---------------------------------------------------------------------------------------------------------


def pay(content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> list[str]:
    """At the press: the racket comes in, the wages go out; without the money a share of the fighters leaves."""
    st = ch.state
    from app.engine import upkeep

    pmc = sum(u.soldiers * lvl for u, lvl in upkeep.corp_ups(st, content))  # owner request 25.10: the corp's private army
    joined: list[str] = []
    if pmc:
        rec = eng.rec(st)
        new = [eng.make_soldier(content, rec, rng, 3) for _ in range(pmc)]
        rec["soldiers"] = list(rec.get("soldiers") or []) + new
        eng.save(st, rec)
        joined = [f"🏢 Частная армия корпорации присылает бойцов: {', '.join(s['name'] for s in new)}."]
    n = len(eng.soldiers(st))
    if not n:
        return []
    income, wage = eng.racket(content, st), eng.wage(content, st)
    if income:
        apply_effects(st, {"eddies": income}, content, rng, year)
    if st.eddies < wage:
        gone = eng.desert(st, eng.cfg(content).unpaid_desert, rng)
        return joined + [f"⚔️ {eng.title(content, st)}: нечем платить ({wage} эдди). Уходят {gone} из {n}."]
    apply_effects(st, {"eddies": -wage}, content, rng, year)
    return joined + [f"⚔️ {eng.title(content, st)}: бойцов {n}. Рэкет +{income}, жалованье −{wage} эдди."]


# -- the war -----------------------------------------------------------------------------------------------------------


def war_targets(content: Content, ch: ActiveCharacter, year: int) -> list[str]:
    from app.services import gangs

    return gangs.raid_targets(content, ch, year)


def war_danger(content: Content, ch: ActiveCharacter) -> int:
    return 5


async def war(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              target: str):  # noqa: ANN201
    from app.services import ap as ap_services
    from app.services import combat

    c = eng.cfg(content)
    roster = eng.soldiers(ch.state)
    t = eng.tier(content, len(roster))
    tiers = [x.key for x in c.tiers]
    if t is None or tiers.index(t.key) < 1:
        return "small"
    if target not in war_targets(content, ch, year):
        return "gone"
    r = _blocked(ch)
    if r:
        return r
    day = _day(ch, tz)
    if int(day.get("war", 0)) >= c.war_per_day:
        return "today"
    if not ap_services.can_spend(ch, c.war_ap):
        return "ap"
    ap_services.spend(ch, content, c.war_ap)
    day["war"] = int(day.get("war", 0)) + 1
    ch.state.flags[FLAG_DAY] = day
    await save_active(session, ch, tz)
    tix = tiers.index(t.key)
    return await combat.begin(session, content, ch, rng, year, tz, faction=target, danger=war_danger(content, ch),
                              src={"src": SRC_WAR, "target": target, "tier": tix, "army_war": True,
                                   "extra": c.war_extra_per_tier * tix})


async def war_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                   tz: str, result: str, ctx: dict) -> list[str]:
    c = eng.cfg(content)
    target = str(ctx.get("target", ""))
    g = content.gangs.get(target)
    if g is None:
        return []
    tix = int(ctx.get("tier", 1) or 1)
    if result != cb.WON:
        apply_effects(ch.state, {f"faction_rep:{target}": -5, "enmity": 1}, content, rng, year)
        await save_active(session, ch, tz)
        return [f"Война с «{g.name}» проиграна. Твои отходят, считая потери."]
    lo, hi = c.war_loot
    loot = rng.randint(lo, hi) * tix
    from app.services import ambition  # owner request 25.10: the ambition «king of the streets» counts the wars

    ambition.count(ch.state, "wars")
    from app.services import season  # owner request 25.10

    await season.add_ch(session, content, ch, "war", content.balance.season.pts.get("war", 0))
    rec = eng.rec(ch.state)
    new = [eng.make_soldier(content, rec, rng, rng.choice([1, 2, 2])) for _ in range(rng.randint(*c.war_defectors))]
    rec["soldiers"] = list(rec.get("soldiers") or []) + new
    eng.save(ch.state, rec)
    apply_effects(ch.state, {"eddies": loot, "street_cred": 2 + tix, "fame": 1, f"faction_rep:{target}": -15, "heat": 2,
                             "enmity": 1}, content, rng, year)
    await save_active(session, ch, tz)
    from app.services import control, loud

    where = control.gang_district(content, target, ch.state.district)
    if where:
        await control.adjust(session, content, where, gang=c.war_gang)
    await loud.post(session, ch.id, f"⚔️ {eng.title(content, ch.state)} ({loud.who(ch.state.name, ch.state.surname)}) "
                                    f"разносит точки «{g.name}».")
    return [f"Война выиграна: точки «{g.name}» твои, касса +{loot} эдди. Перебежчики: "
            + ", ".join(s["name"] for s in new) + "."]
