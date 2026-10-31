"""The Blackwall path (owner request 24.10; engine/blackwall.py): the chain of the Avatars, tainting the city, the cult.

The city taint lives in world_state `bw_city` = {district key: level 0..level_max}. Every real day NetWatch cleans a
bit of it (not in a district where an Avatar lives), and a district tainted enough breaks through: a public post.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.db import repo
from app.engine import blackwall as eng
from app.engine import jail as jail_eng
from app.engine import net as net_eng
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active
from app.services.week import pending_events, set_pending

WS_CITY = "bw_city"


async def city(session: AsyncSession) -> dict[str, int]:
    raw = await repo.ws_get(session, WS_CITY, None)
    return {str(k): int(v) for k, v in (raw or {}).items() if int(v) > 0}


def total(c: dict[str, int]) -> int:
    return sum(c.values())


async def city_total(session: AsyncSession) -> int:
    return total(await city(session))


async def _bump(session: AsyncSession, content: Content, district: str, n: int) -> int:
    c = await city(session)
    lvl = max(0, min(eng.cfg(content).level_max, c.get(district, 0) + n))
    c[district] = lvl
    await repo.ws_set(session, WS_CITY, {k: v for k, v in c.items() if v > 0})
    return lvl


@dataclass
class Result:
    blocked: str | None = None
    text: str = ""
    check: object | None = None
    applied: AppliedEffects | None = None
    event: str | None = None
    level: int = 0


def _common_block(ch: ActiveCharacter) -> str | None:
    st = ch.state
    if jail_eng.in_jail(st):
        return "jail"
    return None  # owner request 25.10: the Net is one for the whole world, the Blackwall reaches every city


async def follow(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str) -> Result:
    """Queue the next step of the Avatar chain."""
    st = ch.state
    why = _common_block(ch)
    if why:
        return Result(why)
    if not eng.touched(st):
        return Result("silent")
    step = eng.next_step(st, content, await city_total(session))
    if step is None:
        return Result("done")
    if not step.ready:
        return Result("needs")
    # owner request 25.10: no real-day limit; one step per lived month and the AP of the game
    if st.flags.get(eng.FLAG_DAY) == f"w{st.weeks_lived}":
        return Result("month")
    if step.event not in content.events:
        return Result("none")
    from app.services import ap as ap_services

    cost = eng.cfg(content).follow_ap
    if not ap_services.can_spend(ch, cost):
        return Result("ap")
    ap_services.spend(ch, content, cost)
    st.flags[eng.FLAG_DAY] = f"w{st.weeks_lived}"
    set_pending(ch, [step.event] + [e for e in pending_events(ch) if e != step.event])
    await save_active(session, ch, tz)
    return Result(event=step.event)


async def corrupt(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                  way: str) -> Result:
    """Taint the district the character lives in."""
    st = ch.state
    why = _common_block(ch)
    if why:
        return Result(why)
    if not eng.touched(st):
        return Result("silent")
    cfg = eng.cfg(content)
    w = cfg.corrupt.get(way)
    if w is None:
        return Result("none")
    if w.needs == "deck" and net_eng.deck_of(st, content) is None:
        return Result("deck")
    if w.needs == "cult" and not eng.in_cult(st):
        return Result("cult")
    lvl = (await city(session)).get(st.district, 0)
    if lvl >= cfg.level_max:
        return Result("max")
    if not ap_services.can_spend(ch, w.ap):
        return Result("ap")
    ap_services.spend(ch, content, w.ap)
    plan = eng.plan_corrupt(st, content, rng, way, lvl)
    if plan is None:
        return Result("none")
    applied = apply_effects(st, dict(plan.effects), content, rng, year)
    if st.hp <= 0:
        st.hp = 1
    if plan.ok:
        lvl = await _bump(session, content, st.district, 1)
    await save_active(session, ch, tz)
    return Result(None, plan.text, plan.check, applied, level=lvl)


async def surge(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str) -> Result:
    """The Avatar breaks the Blackwall through in its district: no check, once a real day."""
    st = ch.state
    why = _common_block(ch)
    if why:
        return Result(why)
    if not eng.is_avatar(st):
        return Result("avatar")
    today = kyiv_date(tz)
    if st.flags.get(eng.FLAG_SURGE) == today:
        return Result("day")
    cfg = eng.cfg(content)
    if not ap_services.can_spend(ch, cfg.surge_ap):
        return Result("ap")
    ap_services.spend(ch, content, cfg.surge_ap)
    st.flags[eng.FLAG_SURGE] = today
    lvl = await _bump(session, content, st.district, cfg.surge_levels)
    applied = apply_effects(st, {"heat": 1, "fame": 1}, content, rng, year)
    await save_active(session, ch, tz)
    text = ("Ты поднимаешь руку, и район отвечает. Экраны, фонари, импланты прохожих вздрагивают в одном ритме. "
            "Заслон в этих кварталах теперь тоньше бумаги.")
    return Result(None, text, None, applied, level=lvl)


async def join(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
               tz: str) -> Result:
    st = ch.state
    why = _common_block(ch)
    if why:
        return Result(why)
    if eng.in_cult(st):
        return Result("member")
    if not eng.touched(st):
        return Result("silent")
    applied = apply_effects(st, {"flag_set:bw_cult_in": True, "bw_favor": 1, "bw_rot": 2}, content, rng, year)
    await save_active(session, ch, tz)
    text = ("Ты находишь подвал под закрытым кинотеатром. Люди в серых балахонах расступаются, архиерей касается твоего "
            "нейропорта холодным проводом. «Добро пожаловать в Церковь Тихого Шума. Здесь тебя слышат»." )
    return Result(None, text, None, applied)


async def vigil(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str) -> Result:
    st = ch.state
    why = _common_block(ch)
    if why:
        return Result(why)
    if not eng.in_cult(st):
        return Result("cult")
    today = kyiv_date(tz)
    if st.flags.get(eng.FLAG_VIGIL) == today:
        return Result("day")
    cfg = eng.cfg(content)
    if not ap_services.can_spend(ch, cfg.vigil_ap):
        return Result("ap")
    ap_services.spend(ch, content, cfg.vigil_ap)
    st.flags[eng.FLAG_VIGIL] = today
    fx = {"bw_rot": cfg.vigil_rot, "bw_favor": cfg.vigil_favor, **cfg.vigil_fx}
    applied = apply_effects(st, fx, content, rng, year)
    await save_active(session, ch, tz)
    text = rng.choice([
        "Свечи, белый шум из старого динамика, сто человек дышат в такт. К утру шум начинает отвечать тебе по имени.",
        "Архиерей читает код вслух, как молитву. Строки впиваются в память. Ты выходишь на рассвет, и фонари гаснут "
        "по пути, один за другим.",
        "Ты лежишь на холодном полу с проводом в нейропорте, и Заслон гладит тебя изнутри, как кошку. Это почти не "
        "страшно.",
    ])
    return Result(None, text, None, applied)


async def tithe(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str) -> Result:
    st = ch.state
    if not eng.in_cult(st):
        return Result("cult")
    cfg = eng.cfg(content)
    if st.eddies < cfg.tithe_eddies:
        return Result("eddies")
    applied = apply_effects(st, {"eddies": -cfg.tithe_eddies, "bw_favor": cfg.tithe_favor}, content, rng, year)
    await save_active(session, ch, tz)
    return Result(None, "Эдди уходят на счёт без имени. Архиерей кивает: Церковь помнит щедрых.", None, applied)


async def buy(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              key: str) -> Result:
    st = ch.state
    item = eng.cfg(content).shop.get(key)
    if item is None:
        return Result("none")
    if eng.cult_rank(st, content) < item.rank:
        return Result("rank")
    if st.eddies < item.price:
        return Result("eddies")
    if item.kind == "cyberware":
        if any(cw.key == item.key for cw in st.cyberware):
            return Result("have")
        cw = content.cyberware.get(item.key)
        fx = {"eddies": -item.price, f"cyberware_grant:{item.key}": True, "flag_set:bw_owned": True,
              "bw_rot": item.rot, "humanity": -max(1, (cw.humanity_cost if cw else 9) // 3)}
        text = "Риппер культа работает без перчаток и без анестезии. Скрипт входит в тело, и в голове становится на один голос больше."
    else:
        if any(i.kind == "weapon" and i.key == item.key for i in st.inventory):
            return Result("have")
        fx = {"eddies": -item.price, f"item_grant:{item.key}": True, "flag_set:bw_owned": True, "bw_rot": item.rot,
              "humanity": -2}
        text = "Оружие выносят из алтарной комнаты, завёрнутое в серую ткань. Оно тёплое. В ушах кто-то вздыхает с облегчением."
    applied = apply_effects(st, fx, content, rng, year)
    await save_active(session, ch, tz)
    return Result(None, text, None, applied)


async def cleanse(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  tz: str) -> Result:
    st = ch.state
    if eng.is_avatar(st):
        return Result("avatar_bound")
    cfg = eng.cfg(content)
    if eng.rot(st) <= 0:
        return Result("clean")
    if st.eddies < cfg.cleanse_eddies:
        return Result("eddies")
    applied = apply_effects(st, {"eddies": -cfg.cleanse_eddies, "bw_rot": -cfg.cleanse_rot,
                                 "humanity": cfg.cleanse_humanity, "hp": -5}, content, rng, year)
    if st.hp <= 0:
        st.hp = 1
    await save_active(session, ch, tz)
    return Result(None, "Подпольный нейрохирург из бывших NetWatch чистит нейропорт пылесосом для кода. Больно, дорого и "
                        "ненадолго. Но голоса стихают.", None, applied)


async def daily(session: AsyncSession, content: Content, rng: random.Random) -> list[str]:
    """NetWatch cleans the city; a tainted district breaks through (a public post)."""
    cfg = eng.cfg(content)
    c = await city(session)
    if not c:
        return []
    avatar_homes = {ch.district for ch in await repo.living_characters(session) if (ch.flags or {}).get(eng.FLAG_AVATAR)}
    posts: list[str] = []
    for d, lvl in list(c.items()):
        if lvl >= cfg.breach_from and cfg.breaches and rng.randint(1, 100) <= lvl * cfg.breach_pct_per_level:
            dist = content.districts.get(d)
            posts.append(rng.choice(cfg.breaches).format(district=dist.name if dist else d))
        if d not in avatar_homes and rng.random() < cfg.decay_chance:
            c[d] = lvl - 1
    await repo.ws_set(session, WS_CITY, {k: v for k, v in c.items() if v > 0})
    return posts
