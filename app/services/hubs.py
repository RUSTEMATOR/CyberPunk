"""Shared homes, crew headquarters and the megacorp tower (owner request 23.10).

- A shared home: a host with a home of tier 2+ invites players; whoever accepts moves into the host's district and
  home and the rent splits among the residents (flag `cohab` = {hub, n}; engine/week.housing_costs). Residents fortify
  the home together. A resident who leaves or is sent away keeps the same kind of home on a lease of their own.
  world_state `hubs` = {"home:<host char id>": {id, host, housing, district, members: [char ids], invites: [char ids],
  fort}}. The hub is checked against the database lazily: a host or a resident who moved away drops out.
- A crew headquarters: crew.data["hq"] = {tier, fort}; the leader rents it and pays every press (then the crew bank);
  it shelters every member in the Purge and makes the crew business pay more.
- The megacorp tower: the owner of a megacorp (services/bigmoney.py) and the owner's crew shelter there; its Purge
  defense grows with the corp's security.
In the Purge a hiding character picks a shelter (purge roster `hub`); its defense and every other member hiding there
add to the personal defense (services/purge.defense).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import Character, PlayerMail
from app.engine.effects import apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

KEY = "hubs"
FLAG = "cohab"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.hubs


def _mail(session: AsyncSession, tg: int, char_id: int | None, text: str) -> None:
    session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name="Дом", kind="city", to_char_id=char_id,
                           payload={"text": text}))


async def _all(session: AsyncSession) -> dict[str, dict]:
    got = await repo.ws_get(session, KEY, {})
    return {str(k): dict(v) for k, v in got.items()} if isinstance(got, dict) else {}


async def _save(session: AsyncSession, hubs: dict[str, dict]) -> None:
    await repo.ws_set(session, KEY, hubs)


def _tier(content: Content, housing: str) -> int:
    h = content.housing.get(housing)
    return h.tier if h else 0


def capacity(content: Content, housing: str) -> int:
    return int(cfg(content).capacity.get(_tier(content, housing), 1))


async def _rows(session: AsyncSession, ids: list[int]) -> dict[int, Character]:
    if not ids:
        return {}
    res = await session.execute(select(Character).where(Character.id.in_(ids)))
    return {r.id: r for r in res.scalars()}


async def _validate(session: AsyncSession, hub: dict) -> dict | None:
    """Drop residents who died or moved away; None when the host is gone (the hub is over)."""
    rows = await _rows(session, [int(x) for x in hub.get("members", [])] + [int(x) for x in hub.get("invites", [])])
    host = rows.get(int(hub["host"]))
    if host is None or not host.alive or host.housing != hub["housing"]:
        return None
    hub["members"] = [m for m in hub.get("members", []) if (r := rows.get(int(m))) is not None and r.alive
                      and r.housing == hub["housing"]]
    if int(hub["host"]) not in hub["members"]:
        hub["members"].insert(0, int(hub["host"]))
    hub["invites"] = [i for i in hub.get("invites", []) if (r := rows.get(int(i))) is not None and r.alive]
    return hub


async def home_of(session: AsyncSession, ch: ActiveCharacter) -> dict | None:
    """The shared home the character lives in (checked against the database), or None."""
    hubs = await _all(session)
    found = None
    changed = False
    for hid, hub in list(hubs.items()):
        if ch.id not in [int(x) for x in hub.get("members", [])]:
            continue
        ok = await _validate(session, hub)
        changed = True
        if ok is None:
            hubs.pop(hid)
            continue
        hubs[hid] = ok
        if ch.id in ok["members"]:
            found = ok
    if changed:
        await _save(session, hubs)
    return found


async def sync_self(session: AsyncSession, content: Content, ch: ActiveCharacter) -> dict | None:
    """Put the character's `cohab` flag in line with the hub (the rent split and the mods)."""
    hub = await home_of(session, ch)
    n = len(hub["members"]) if hub else 0
    if hub and n >= 2:
        ch.state.flags[FLAG] = {"hub": hub["id"], "n": n}
    else:
        ch.state.flags.pop(FLAG, None)
    return hub


async def invites_for(session: AsyncSession, ch: ActiveCharacter) -> list[dict]:
    hubs = await _all(session)
    return [h for h in hubs.values() if ch.id in [int(x) for x in h.get("invites", [])]]


async def candidates(session: AsyncSession, ch: ActiveCharacter) -> list[tuple[int, str]]:
    rows = await repo.living_characters(session)
    return [(r.id, f"{r.name} {r.surname or ''}".strip()) for r in rows if r.id != ch.id]


@dataclass
class HubResult:
    blocked: str | None = None
    text: str = ""


async def invite(session: AsyncSession, content: Content, ch: ActiveCharacter, target_id: int, tz: str) -> HubResult:
    st = ch.state
    if _tier(content, st.housing) < 2:
        return HubResult("small")
    hubs = await _all(session)
    hid = f"home:{ch.id}"
    mine = await home_of(session, ch)
    if mine is not None and int(mine["host"]) != ch.id:
        return HubResult("guest")
    hub = hubs.get(hid) or {"id": hid, "host": ch.id, "housing": st.housing, "district": st.district,
                            "members": [ch.id], "invites": [], "fort": 0}
    hub["housing"], hub["district"] = st.housing, st.district
    if len(hub["members"]) + len(hub["invites"]) >= capacity(content, st.housing):
        return HubResult("full")
    rows = await _rows(session, [target_id])
    other = rows.get(target_id)
    if other is None or not other.alive or target_id == ch.id:
        return HubResult("gone")
    if target_id in hub["members"] or target_id in hub["invites"]:
        return HubResult("already")
    from app.engine import place

    if place.of_district(content, other.district) != place.place_of(st, content):  # owner request 30.10
        return HubResult("away")
    hub["invites"].append(target_id)
    hubs[hid] = hub
    await _save(session, hubs)
    home = content.housing[st.housing].name
    _mail(session, other.player_tg_id, other.id, f"{st.name} зовёт жить вместе: «{home}». Аренда пополам, общая "
                                                 "оборона в Судную ночь. Ответить: «Имущество» → «Общий дом и штаб».")
    return HubResult(None, f"Приглашение ушло: {other.name}.")


async def answer(session: AsyncSession, content: Content, ch: ActiveCharacter, hid: str, accept: bool, rng: random.Random,
                 year: int, tz: str) -> HubResult:
    hubs = await _all(session)
    hub = hubs.get(hid)
    if hub is None or ch.id not in [int(x) for x in hub.get("invites", [])]:
        return HubResult("gone")
    hub["invites"] = [i for i in hub["invites"] if int(i) != ch.id]
    if not accept:
        hubs[hid] = hub
        await _save(session, hubs)
        return HubResult(None, "Ты вежливо отказываешься.")
    ok = await _validate(session, hub)
    if ok is None:
        hubs.pop(hid, None)
        await _save(session, hubs)
        return HubResult("gone")
    if len(ok["members"]) >= capacity(content, ok["housing"]):
        hubs[hid] = ok
        await _save(session, hubs)
        return HubResult("full")
    from app.engine import place

    if place.of_district(content, str(ok.get("district") or "")) != place.place_of(ch.state, content):  # owner request 30.10
        hubs[hid] = {**ok, "invites": [*ok.get("invites", []), ch.id]}  # the invitation waits for the move
        await _save(session, hubs)
        return HubResult("away")
    if int(ch.state.flags.get("jail_weeks", 0) or 0) > 0:
        return HubResult("jail")
    if not ap_services.can_spend(ch, cfg(content).move_in_ap):
        return HubResult("ap")
    ap_services.spend(ch, content, cfg(content).move_in_ap)
    for other_id, other in list(hubs.items()):  # leave a shared home of one's own first
        if other_id != hid and ch.id in [int(x) for x in other.get("members", [])]:
            other["members"] = [m for m in other["members"] if int(m) != ch.id]
            hubs[other_id] = other
    ch.state.district = ok["district"]
    ch.state.housing = ok["housing"]
    ok["members"].append(ch.id)
    hubs[hid] = ok
    await _save(session, hubs)
    await save_active(session, ch, tz)  # the row must show the new home before the hub is checked
    await session.flush()
    await sync_self(session, content, ch)
    await save_active(session, ch, tz)
    host = (await _rows(session, [int(ok["host"])])).get(int(ok["host"]))
    if host is not None:
        _mail(session, host.player_tg_id, host.id, f"{ch.state.name} переезжает к тебе. Аренда делится на всех.")
    home = content.housing[ok["housing"]].name
    return HubResult(None, f"Ты переезжаешь: «{home}». Аренда теперь делится на {len(ok['members'])}.")


async def leave(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str) -> HubResult:
    """A resident leaves (keeps the same kind of home on a lease of their own); the host ends the shared home."""
    hubs = await _all(session)
    hub = await home_of(session, ch)
    if hub is None:
        return HubResult("none")
    hubs = await _all(session)
    if int(hub["host"]) == ch.id:
        hubs.pop(hub["id"], None)
        rows = await _rows(session, [int(m) for m in hub["members"] if int(m) != ch.id])
        for r in rows.values():
            _mail(session, r.player_tg_id, r.id, f"{ch.state.name} больше не делит с тобой дом. Аренда теперь твоя целиком.")
        text = "Общий дом распущен. Каждый платит за себя."
    else:
        hub["members"] = [m for m in hub["members"] if int(m) != ch.id]
        hubs[hub["id"]] = hub
        text = "Ты съезжаешь и снимаешь такое же жильё на себя. Аренда теперь твоя целиком."
    await _save(session, hubs)
    ch.state.flags.pop(FLAG, None)
    await save_active(session, ch, tz)
    return HubResult(None, text)


async def kick(session: AsyncSession, content: Content, ch: ActiveCharacter, member_id: int) -> HubResult:
    hubs = await _all(session)
    hub = hubs.get(f"home:{ch.id}")
    if hub is None or member_id == ch.id or member_id not in [int(m) for m in hub.get("members", [])]:
        return HubResult("gone")
    hub["members"] = [m for m in hub["members"] if int(m) != member_id]
    hubs[hub["id"]] = hub
    await _save(session, hubs)
    r = (await _rows(session, [member_id])).get(member_id)
    if r is not None:
        _mail(session, r.player_tg_id, r.id, f"{ch.state.name} просит тебя съехать. Жильё остаётся за тобой, но аренда — "
                                             "целиком твоя.")
    return HubResult(None, "Сосед съезжает.")


def fort_price(content: Content, level: int) -> int | None:
    prices = cfg(content).fort_price
    return prices[level] if level < len(prices) else None


async def fortify_home(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                       tz: str) -> HubResult:
    hub = await home_of(session, ch)
    if hub is None:
        return HubResult("none")
    price = fort_price(content, int(hub.get("fort", 0)))
    if price is None:
        return HubResult("max")
    if ch.state.eddies < price:
        return HubResult("eddies")
    apply_effects(ch.state, {"eddies": -price}, content, rng, year)
    hubs = await _all(session)
    hub["fort"] = int(hub.get("fort", 0)) + 1
    hubs[hub["id"]] = hub
    await _save(session, hubs)
    await save_active(session, ch, tz)
    return HubResult(None, f"Дом укреплён до уровня {hub['fort']}: оборона в Судную ночь +{cfg(content).fort_def} для всех жильцов.")


# -- the crew headquarters ---------------------------------------------------------------------------------------------


def hq_tier(content: Content, key: str):  # noqa: ANN201
    return next((t for t in cfg(content).hq if t.key == key), None)


def hq_of(crew) -> dict | None:  # noqa: ANN001
    h = (crew.data or {}).get("hq") if crew is not None else None
    return dict(h) if h else None


def _set_hq(crew, hq: dict | None) -> None:  # noqa: ANN001
    data = dict(crew.data or {})
    if hq is None:
        data.pop("hq", None)
    else:
        data["hq"] = hq
    crew.data = data


async def hq_rent(session: AsyncSession, content: Content, ch: ActiveCharacter, key: str, rng: random.Random, year: int,
                  tz: str) -> HubResult:
    from app.services import crews

    crew = await crews.crew_of(session, ch.player_tg_id)
    if crew is None:
        return HubResult("nocrew")
    if crew.leader_tg != ch.player_tg_id:
        return HubResult("leader")
    t = hq_tier(content, key)
    if t is None:
        return HubResult("gone")
    first = t.weekly * content.balance.time.weeks_per_press
    if ch.state.eddies < first:
        return HubResult("eddies")
    old = hq_of(crew)
    apply_effects(ch.state, {"eddies": -first}, content, rng, year)
    _set_hq(crew, {"tier": key, "fort": int(old.get("fort", 0)) if old else 0})
    await session.flush()
    await save_active(session, ch, tz)
    for tg in await crews.members(session, crew.id):
        if tg != ch.player_tg_id:
            _mail(session, tg, None, f"У банды «{crew.name}» новый штаб: «{t.name}». Прятаться в Судную ночь можно там.")
    return HubResult(None, f"Штаб «{t.name}» снят. Первый месяц оплачен: {first} эдди.")


async def hq_drop(session: AsyncSession, ch: ActiveCharacter) -> HubResult:
    from app.services import crews

    crew = await crews.crew_of(session, ch.player_tg_id)
    if crew is None or crew.leader_tg != ch.player_tg_id or hq_of(crew) is None:
        return HubResult("leader")
    _set_hq(crew, None)
    await session.flush()
    return HubResult(None, "Штаб сдан. Банда снова собирается где придётся.")


async def hq_fortify(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str) -> HubResult:
    from app.services import crews

    crew = await crews.crew_of(session, ch.player_tg_id)
    hq = hq_of(crew)
    if crew is None or hq is None:
        return HubResult("nohq")
    price = fort_price(content, int(hq.get("fort", 0)))
    if price is None:
        return HubResult("max")
    if ch.state.eddies < price:
        return HubResult("eddies")
    apply_effects(ch.state, {"eddies": -price}, content, rng, year)
    hq["fort"] = int(hq.get("fort", 0)) + 1
    _set_hq(crew, hq)
    await session.flush()
    await save_active(session, ch, tz)
    return HubResult(None, f"Штаб укреплён до уровня {hq['fort']}: оборона +{cfg(content).fort_def} для всей банды.")


def hq_biz_pct(content: Content, crew) -> int:  # noqa: ANN001
    hq = hq_of(crew)
    t = hq_tier(content, str(hq.get("tier"))) if hq else None
    return t.biz_pct if t else 0


async def press_lines(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random,
                      year: int) -> list[str]:
    """After the leader's press: the rent of the headquarters (the leader first, then the crew bank)."""
    from app.services import crews

    out: list[str] = []
    await sync_self(session, content, ch)
    crew = await crews.crew_of(session, ch.player_tg_id)
    hq = hq_of(crew)
    if crew is None or hq is None or crew.leader_tg != ch.player_tg_id:
        return out
    t = hq_tier(content, str(hq.get("tier")))
    if t is None:
        return out
    cost = t.weekly * content.balance.time.weeks_per_press
    if ch.state.eddies >= cost:
        apply_effects(ch.state, {"eddies": -cost}, content, rng, year)
        out.append(f"🏚 Штаб «{t.name}»: аренда за месяц {cost} эдди.")
    elif crew.bank >= cost:
        crew.bank -= cost
        out.append(f"🏚 Штаб «{t.name}»: аренду {cost} эдди взяли из общака — у главаря не хватило.")
    else:
        _set_hq(crew, None)
        out.append(f"🏚 Аренду штаба «{t.name}» платить нечем. Банду выселили, укрепления остались новому хозяину.")
        for tg in await crews.members(session, crew.id):
            if tg != ch.player_tg_id:
                _mail(session, tg, None, f"Банда «{crew.name}» потеряла штаб: аренду не заплатили.")
    await session.flush()
    return out


# -- shelters for the Purge ----------------------------------------------------------------------------------------------


@dataclass
class Shelter:
    id: str
    name: str
    defense: int


async def shelters(session: AsyncSession, content: Content, ch: ActiveCharacter) -> list[Shelter]:
    """Where the character can hide in the Purge besides the own home."""
    from app.services import bigmoney, crews

    c = cfg(content)
    out: list[Shelter] = []
    hub = await home_of(session, ch)
    if hub is not None and len(hub["members"]) >= 2:
        h = content.housing.get(hub["housing"])
        out.append(Shelter(hub["id"], f"Общий дом: {h.name if h else '?'}", int(hub.get("fort", 0)) * c.fort_def))
    crew = await crews.crew_of(session, ch.player_tg_id)
    hq = hq_of(crew)
    if crew is not None and hq is not None:
        t = hq_tier(content, str(hq.get("tier")))
        if t is not None:
            out.append(Shelter(f"crew:{crew.id}", f"Штаб «{t.name}»", t.base_def + int(hq.get("fort", 0)) * c.fort_def))
    owners = {str(ch.player_tg_id)}
    if crew is not None:
        owners |= {str(x) for x in await crews.members(session, crew.id)}
    corps = await bigmoney.corps(session)
    from app.services import pcorp

    for tg, corp in corps.items():
        if tg in owners or str(ch.player_tg_id) in pcorp.staff_of(corp):  # owner request 24.10: and its staff
            sec = int(corp.get("security", 0) or 0)
            out.append(Shelter(f"corp:{tg}", f"Башня «{corp['name']}»", c.tower_base_def + sec * c.tower_def_per_security))
    return out


def together_bonus(content: Content, roster: dict, cid: str, hub_id: str) -> int:
    c = cfg(content)
    others = sum(1 for k, r in roster.items() if k != cid and r.get("mode") == "hide" and r.get("hub") == hub_id)
    return min(c.together_cap, others * c.together_def)
