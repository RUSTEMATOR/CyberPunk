"""How much a megacorp still stands (owner request 25.10 «непонятно, какой урон корпорации наношу … начать выносить
корпорации в городе»).

world_state `corp_stand` = {corp key: {"hp", "down_until" (iso date or ""), "dmg": {tg: damage}, "names": {tg: name}}}.
- Every won raid (services/corpraid.raid_over) takes `dmg[site]` off the corp's standing (scaled by the raid's level).
- At zero the corp falls for `down_days`: the city hears of it, its district slips out of its hands, everyone who hit it
  gets a share of `fall_bonus` by the damage dealt; raids on it wait until it is back.
- Every day a standing corp heals `regen`; a fallen one rises with `rise_hp` when its days are over.
The non-canon corps that come and go (services/dyn.py) are hit by their `power` instead (corpraid.strike_dyn).
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.content.loader import Content
from app.db import repo
from app.db.models import PlayerMail
from app.services.game import ActiveCharacter

KEY = "corp_stand"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.corp_stand


async def book(session: AsyncSession) -> dict:
    got = await repo.ws_get(session, KEY, {})
    return dict(got) if isinstance(got, dict) else {}


def rec_of(content: Content, b: dict, key: str) -> dict:
    r = dict(b.get(key) or {})
    r.setdefault("hp", cfg(content).max_hp)
    r.setdefault("down_until", "")
    r.setdefault("dmg", {})
    r.setdefault("names", {})
    return r


def down(r: dict) -> bool:
    return bool(r.get("down_until")) and str(r["down_until"]) >= utcnow().date().isoformat()


def bar(content: Content, r: dict) -> str:
    if down(r):
        return f"лежит до {date.fromisoformat(str(r['down_until'])).strftime('%d.%m')}"
    return f"стойкость {int(r['hp'])}/{cfg(content).max_hp}"


def damage_of(content: Content, site: str, lv: int) -> int:
    base = int(cfg(content).dmg.get(site, 3))
    return base + (base * max(0, lv - 2) // 2 if lv >= 2 else 0)


async def hit(session: AsyncSession, content: Content, ch: ActiveCharacter, key: str, site: str, lv: int,
              rng: random.Random, year: int) -> list[str]:
    from app.services import control, loud

    c = cfg(content)
    b = await book(session)
    r = rec_of(content, b, key)
    corp = content.corps[key]
    if down(r):
        return []
    n = damage_of(content, site, lv)
    r["hp"] = max(0, int(r["hp"]) - n)
    tg = str(ch.player_tg_id)
    r["dmg"][tg] = int(r["dmg"].get(tg, 0)) + n
    r["names"][tg] = f"{ch.state.name} {ch.state.surname}".strip()
    lines = [f"Урон по {corp.name}: −{n}, {bar(content, r)}."]
    if r["hp"] <= 0:
        r["down_until"] = (utcnow().date() + timedelta(days=c.down_days)).isoformat()
        total = max(1, sum(int(v) for v in r["dmg"].values()))
        names = []
        for who, d in sorted(r["dmg"].items(), key=lambda x: -int(x[1])):
            share = int(c.fall_bonus * int(d) / total)
            session.add(PlayerMail(to_tg_id=int(who), from_tg_id=0, from_name="Найт-Сити", kind="city",
                                   payload={"text": f"{corp.name} рухнула. Твоя доля в этом: {int(d)} урона, "
                                                    f"трофеи +{share} эдди.", "eddies": share, "fame": c.fall_fame}))
            names.append(str(r["names"].get(who, "")))
        r["dmg"], r["names"] = {}, {}
        where = control.corp_district(content, key, ch.state.district)
        if where:
            await control.adjust(session, content, where, corp=c.fall_corp_control)
        from app.engine import grammar  # owner request 26.10

        await loud.post(session, ch.id, "🏢 " + grammar.say(content, rng, "corpfall_post", corp=corp.name,
                                                             names=", ".join(x for x in names if x), days=c.down_days),
                        kind="corp_fall", w=3)
        lines.append(f"🏢 {corp.name} рухнула на {c.down_days} дн. Трофеи по доле урона придут почтой. "
                     + (f"Хватка корпорации в районе {content.districts[where].name} ослабла." if where else ""))
    b[key] = r
    await repo.ws_set(session, KEY, b)
    return lines


async def daily(session: AsyncSession, content: Content) -> list[str]:
    c = cfg(content)
    b = await book(session)
    out: list[str] = []
    today = utcnow().date().isoformat()
    for key, raw in list(b.items()):
        r = rec_of(content, b, key)
        if r["down_until"] and str(r["down_until"]) < today:
            r.update(down_until="", hp=c.rise_hp)
            corp = content.corps.get(key)
            if corp is not None:
                out.append(f"{corp.name} снова на ногах: новая охрана, новые счета, старые обиды.")
        elif not down(r):
            r["hp"] = min(c.max_hp, int(r["hp"]) + c.regen)
        b[key] = r
    await repo.ws_set(session, KEY, b)
    return out
