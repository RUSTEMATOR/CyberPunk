"""Districts held by players (owner request 25.10, docs/PLAN_ENDGAME2.md part 1): the game of the top becomes a game of
the city. world_state `turf` = {district: {owner, char, name, army, tax, law, since}}.

- Taking a district: the own army has `need_army` fighters, the gangs and the corps of the district are both at most
  `max_meter`, a war for the district (a big fight against its strongest gang).
- The owner sets the tax (the income every lived month; a high tax feeds the gangs) and the law of the district (its
  effects reach everyone who lives there).
- The owner's people hold both forces back every day. Another player with an army takes the district by a war against
  the owner's army.
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.content.loader import Content
from app.db import repo
from app.db.models import PlayerMail
from app.engine import army as army_eng
from app.engine import control as ce
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

KEY = "turf"
SRC = "turf"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.turf


async def book(session: AsyncSession) -> dict[str, dict]:
    return {str(k): dict(v) for k, v in (await repo.ws_get(session, KEY, {}) or {}).items()}


async def _save(session: AsyncSession, b: dict) -> None:
    await repo.ws_set(session, KEY, b)


def law(content: Content, key: str):  # noqa: ANN201
    return next((x for x in cfg(content).laws if x.key == key), None)


def owned_by(b: dict, tg: int) -> list[str]:
    return [d for d, r in b.items() if int(r.get("owner", 0) or 0) == int(tg)]


def can_take(content: Content, ch: ActiveCharacter, meters: dict | None, holder: dict | None) -> str | None:
    c = cfg(content)
    d = content.districts.get(ch.state.district)
    if d is None or d.city != "nc":
        return "city"
    if holder is not None and int(holder.get("owner", 0) or 0) == ch.player_tg_id:
        return "mine"
    if len(army_eng.soldiers(ch.state)) < c.need_army:
        return "army"
    if holder is None and meters is not None and (meters[ce.GANG] > c.max_meter or meters[ce.CORP] > c.max_meter):
        return "meters"
    return None


async def take(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str):  # noqa: ANN201
    """Start the war for the district the character lives in. A Step or a block reason."""
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services import combat, control
    from app.services import events as ev_service

    c = cfg(content)
    b = await book(session)
    district = ch.state.district
    holder = b.get(district)
    meters = (await control.load(session, content)).get(district)
    why = can_take(content, ch, meters, holder)
    if why:
        return why
    if jail_eng.in_jail(ch.state):
        return "jail"
    if ev_service.has_pending(ch):
        return "busy"
    if not ap_services.can_spend(ch, c.war_ap):
        return "ap"
    ap_services.spend(ch, content, c.war_ap)
    await save_active(session, ch, tz)
    d = content.districts[district]
    avail = content.available("gangs", year)
    faction = next((g for g in d.gangs if g in avail), "maelstrom")
    src = {"src": SRC, "district": district, "army_war": True, "extra": c.war_extra}
    if holder is not None:  # the owner's army defends: its fighters wear the owner's colours
        defenders = int(holder.get("size", 10) or 10)
        src.update(extra=min(8, c.war_extra + defenders // c.defenders_per_extra), dyn_name=str(holder.get("army") or holder.get("name")),
                   dyn_kind="gang", lair=True, holder=int(holder.get("owner", 0) or 0))
    return await combat.begin(session, content, ch, rng, year, tz, faction=faction, danger=5, src=src)


async def war_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                   result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb
    from app.services import control, loud, season

    district = str(ctx.get("district", ""))
    if district not in content.districts:
        return []
    dname = content.districts[district].name
    if result != cb.WON:
        holder = int(ctx.get("holder", 0) or 0)
        if holder:
            session.add(PlayerMail(to_tg_id=holder, from_tg_id=0, from_name="Город", kind="city",
                                   payload={"text": f"На район «{dname}» напал {ch.state.name} {ch.state.surname}. Твои отбились."}))
        return [f"Война за район «{dname}» проиграна. Твои отходят."]
    b = await book(session)
    old = b.get(district)
    title = army_eng.title(content, ch.state) or "Твои люди"
    b[district] = {"owner": ch.player_tg_id, "char": ch.id, "name": f"{ch.state.name} {ch.state.surname}".strip(),
                   "army": army_eng.rec(ch.state).get("name") or title, "size": len(army_eng.soldiers(ch.state)),
                   "tax": 1, "law": cfg(content).laws[0].key if cfg(content).laws else "", "since": utcnow().date().isoformat()}
    await _save(session, b)
    await control.adjust(session, content, district, gang=cfg(content).take_gang, corp=cfg(content).take_corp)
    if old is not None and int(old.get("owner", 0) or 0) != ch.player_tg_id:
        session.add(PlayerMail(to_tg_id=int(old["owner"]), from_tg_id=0, from_name="Город", kind="city",
                               payload={"text": f"Район «{dname}» потерян: его взял {b[district]['name']}."}))
    await season.add_ch(session, content, ch, "turf", content.balance.season.pts.get("turf", 0))
    await loud.post(session, ch.id, f"👑 Район «{dname}» теперь держит {loud.who(ch.state.name, ch.state.surname)} "
                                    f"({b[district]['army']}).", kind="turf", w=3)
    return [f"Район «{dname}» твой. Налог и закон — в «Улицах»."]


async def set_rule(session: AsyncSession, content: Content, ch: ActiveCharacter, what: str, value: str) -> str | None:
    b = await book(session)
    r = b.get(ch.state.district)
    if r is None or int(r.get("owner", 0) or 0) != ch.player_tg_id:
        return "notmine"
    if what == "tax" and value.isdigit() and 0 <= int(value) < len(cfg(content).tax_income):
        r["tax"] = int(value)
    elif what == "law" and law(content, value) is not None:
        r["law"] = value
    else:
        return "gone"
    b[ch.state.district] = r
    await _save(session, b)
    return None


def _whole(x: float, rng: random.Random) -> int:
    n = int(x)
    frac = abs(x - n)
    return n + ((1 if x > 0 else -1) if rng.random() < frac else 0)


def daily(content: Content, b: dict, state: dict, rng: random.Random) -> None:
    """The owner's people, the tax and the law move the meters of an owned district (called by control.daily)."""
    c = cfg(content)
    for district, r in b.items():
        if district not in state:
            continue
        lw = law(content, str(r.get("law", "")))
        tax = int(r.get("tax", 0) or 0)
        g = -c.hold_per_day + (c.tax_gang[tax] if tax < len(c.tax_gang) else 0) + (lw.gang_per_day if lw else 0)
        k = -c.hold_per_day + (lw.corp_per_day if lw else 0)
        ce.shift(state, district, gang=_whole(g, rng), corp=_whole(k, rng))


async def press_hook(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random,
                     year: int) -> list[str]:
    """Every lived month: the owner's tax; the law of the district the character lives in."""
    lines = await pay(session, content, ch, rng, year)
    b = await book(session)
    r = b.get(ch.state.district)
    if r is not None:
        lw = law(content, str(r.get("law", "")))
        if lw is not None and lw.mood_per_press:
            apply_effects(ch.state, {"mood": lw.mood_per_press}, content, rng, year)
            if int(r.get("owner", 0) or 0) != ch.player_tg_id:
                lines.append(f"👑 Район держит {r.get('name')}: «{lw.name}» ({'+' if lw.mood_per_press > 0 else ''}"
                             f"{lw.mood_per_press} настроение).")
    return lines


async def pay(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> list[str]:
    """At the owner's press: the tax of every held district; the army size the defence counts on is refreshed."""
    c = cfg(content)
    b = await book(session)
    mine = owned_by(b, ch.player_tg_id)
    if not mine:
        return []
    lost = [d for d in mine if int(b[d].get("char", 0) or 0) != ch.id]  # the holder died: a new life holds nothing
    for d in lost:
        b.pop(d, None)
    mine = [d for d in mine if d not in lost]
    if not mine:
        await _save(session, b)
        return []
    total = 0
    for d in mine:
        r = b[d]
        tax = int(r.get("tax", 0) or 0)
        lw = law(content, str(r.get("law", "")))
        danger = content.districts[d].danger if d in content.districts else 3
        income = c.tax_income[tax] * (6 - danger) if tax < len(c.tax_income) else 0
        if lw is not None:
            income = income * (100 + lw.tax_pct) // 100
        total += income
        r["size"] = len(army_eng.soldiers(ch.state))
    await _save(session, b)
    if total:
        apply_effects(ch.state, {"eddies": total}, content, rng, year)
    names = ", ".join(content.districts[d].name for d in mine if d in content.districts)
    return [f"👑 Районы ({names}): налог +{total} эдди."]


def law_effect(content: Content, law_key: str) -> dict:
    lw = law(content, law_key)
    return {"heat_mult": lw.heat_mult, "mood": lw.mood_per_press} if lw is not None else {}
