"""The rogue AI as a late game (owner request 24.10): levels, network nodes in the districts, data, NetWatch's trace,
kin AIs beyond the Blackwall, daemons, plans against the city, and two endings (the Singularity, a new body).

Everything lives in RogueAI.data next to the old fields:
  xp, data, trace, nodes [district keys], mods {key: level}, daemons, kin {key: trades}, tick (the last day processed).
The daily actions still come from services/rogue_ai.py (charges per real day); the level and Многопоточность add more.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.texts import esc
from app.content.loader import Content
from app.db import repo
from app.db.models import RogueAI
from app.services import loud
from app.services import rogue_ai as base


def cfg(content: Content):  # noqa: ANN201
    return content.balance.players.rogue_ai


def d(ai: RogueAI) -> dict:
    return dict(ai.data or {})


def _save(ai: RogueAI, data: dict) -> None:
    ai.data = data


def level(content: Content, ai: RogueAI) -> int:
    xp = int(d(ai).get("xp", 0) or 0)
    lv = 1
    for i, need in enumerate(cfg(content).level_xp):
        if xp >= need:
            lv = i + 1
    return lv


def next_xp(content: Content, ai: RogueAI) -> int | None:
    xp = int(d(ai).get("xp", 0) or 0)
    return next((n for n in cfg(content).level_xp if n > xp), None)


def mod(ai: RogueAI, key: str) -> int:
    return int((d(ai).get("mods") or {}).get(key, 0) or 0)


def charges(content: Content, ai: RogueAI) -> int:
    """Actions per real day: the base, one per charge level reached, one per Многопоточность level."""
    lv = level(content, ai)
    return cfg(content).charges + sum(1 for x in cfg(content).charge_levels if lv >= x) + mod(ai, "threads")


def left(content: Content, ai: RogueAI, tz: str) -> int:
    data = d(ai)
    used = int(data.get("used", 0)) if data.get("day") == base._today(tz) else 0  # noqa: SLF001
    return max(0, charges(content, ai) - used)


def _spend(ai: RogueAI, tz: str, line: str) -> None:
    base._spend(ai, tz, line)  # noqa: SLF001


def gain(content: Content, ai: RogueAI, *, xp: int = 0, data: int = 0, trace_key: str = "", power: int = 0) -> list[str]:
    """Experience, data, trace (eased by Шум в логах), power. Returns lines (a level up)."""
    c = cfg(content)
    st = d(ai)
    before = level(content, ai)
    st["xp"] = int(st.get("xp", 0) or 0) + xp
    st["data"] = max(0, int(st.get("data", 0) or 0) + data)
    if trace_key:
        t = c.trace.get(trace_key, 0) * max(0, 100 - 25 * mod(ai, "stealth")) // 100
        st["trace"] = max(0, min(100, int(st.get("trace", 0) or 0) + t))
    _save(ai, st)
    ai.power += power
    out = []
    after = level(content, ai)
    if after > before:
        ai.power += after - before
        out.append(f"Уровень {after}: мощь +{after - before}.")
    return out


def nodes(ai: RogueAI) -> list[str]:
    return list(d(ai).get("nodes") or [])


def node_dv(content: Content, ai: RogueAI, district: str) -> int:
    dd = content.districts.get(district)
    return cfg(content).node_dv_base + cfg(content).node_dv_per_danger * (dd.danger if dd else 2) + len(nodes(ai))


async def daily(session: AsyncSession, content: Content, ai: RogueAI, tz: str) -> list[str]:
    """The days since the last visit (at most a week): data from nodes and daemons, the trace cools, NetWatch sweeps."""
    c = cfg(content)
    st = d(ai)
    today = date.fromisoformat(base._today(tz))  # noqa: SLF001
    last = date.fromisoformat(st["tick"]) if st.get("tick") else today
    days = min(7, (today - last).days)
    st["tick"] = today.isoformat()
    _save(ai, st)
    out: list[str] = []
    for _ in range(max(0, days)):
        st = d(ai)
        inc = len(st.get("nodes") or []) * c.node_data + int(st.get("daemons", 0) or 0) * c.daemon_data
        st["data"] = int(st.get("data", 0) or 0) + inc
        st["trace"] = max(0, int(st.get("trace", 0) or 0) - c.trace_decay)
        _save(ai, st)
        if inc:
            out.append(f"Узлы и демоны собрали {inc} данных.")
        if int(st.get("trace", 0)) >= c.sweep_at:
            dmg = c.sweep_dmg * max(0, 100 - 25 * mod(ai, "shield")) // 100
            ai.hp = max(0, ai.hp - dmg)
            st = d(ai)
            ns = list(st.get("nodes") or [])
            lost = ""
            if ns:
                gone = ns.pop(random.randrange(len(ns)))
                st["nodes"] = ns
                dd = content.districts.get(gone)
                lost = f" Узел «{dd.name if dd else gone}» вычищен."
            st["trace"] = c.sweep_reset
            _save(ai, st)
            out.append(f"Зачистка NetWatch: ядро −{dmg}.{lost}")
    if out:
        st = d(ai)
        st["log"] = (list(st.get("log") or []) + out)[-8:]
        _save(ai, st)
    await session.flush()
    return out


@dataclass
class Res:
    blocked: str | None = None
    lines: list[str] = field(default_factory=list)
    erased: bool = False
    ended: str = ""


async def capture(session: AsyncSession, content: Content, ai: RogueAI, rng: random.Random, tz: str,
                  district: str) -> Res:
    if left(content, ai, tz) <= 0:
        return Res("charges")
    if district not in content.districts or district in nodes(ai):
        return Res("none")
    dv = node_dv(content, ai, district)
    roll = ai.power + rng.randint(1, 10)
    name = content.districts[district].name
    res = Res()
    if roll > dv:
        st = d(ai)
        st["nodes"] = nodes(ai) + [district]
        _save(ai, st)
        res.lines.append(f"{roll} против {dv}: узел «{name}» твой. Каждый день он приносит данные.")
        res.lines += gain(content, ai, xp=cfg(content).xp_per_action * 2, trace_key="capture")
        _spend(ai, tz, f"Узел захвачен: {name}.")
    else:
        ai.hp = max(0, ai.hp - 10)
        res.lines.append(f"{roll} против {dv}: защита узла «{name}» держит. Ядро −10.")
        res.lines += gain(content, ai, xp=cfg(content).xp_per_action // 2, trace_key="mine")
        _spend(ai, tz, f"Узел устоял: {name}.")
        res.erased = ai.hp <= 0
    await session.flush()
    return res


async def mine(session: AsyncSession, content: Content, ai: RogueAI, rng: random.Random, tz: str) -> Res:
    if left(content, ai, tz) <= 0:
        return Res("charges")
    got = rng.randint(*cfg(content).mine_data) + ai.power // 5
    res = Res(lines=[f"Ты пропускаешь через себя чужие потоки: +{got} данных."])
    res.lines += gain(content, ai, xp=cfg(content).xp_per_action, data=got, trace_key="mine")
    _spend(ai, tz, f"Майнинг: +{got} данных.")
    await session.flush()
    return res


async def sell(session: AsyncSession, content: Content, ai: RogueAI, tz: str, amount: int) -> Res:
    """Data to the family fund: the heir gets the eddies (services/family.py)."""
    from app.services import family

    st = d(ai)
    have = int(st.get("data", 0) or 0)
    amount = min(have, max(0, amount))
    if amount <= 0:
        return Res("data")
    if left(content, ai, tz) <= 0:
        return Res("charges")
    eddies = amount * cfg(content).data_price
    funds = dict(await repo.ws_get(session, family.FUND_KEY, {}) or {})
    funds[str(ai.owner_tg)] = int(funds.get(str(ai.owner_tg), 0) or 0) + eddies
    await repo.ws_set(session, family.FUND_KEY, funds)
    res = Res(lines=[f"Данные проданы брокерам: {eddies} эдди в семейный фонд. Их получит наследник."])
    res.lines += gain(content, ai, data=-amount, trace_key="sell")
    _spend(ai, tz, f"Продано данных: {amount}.")
    await session.flush()
    return res


async def upgrade(session: AsyncSession, content: Content, ai: RogueAI, key: str) -> Res:
    c = cfg(content)
    st = d(ai)
    if key == "daemon":
        n = int(st.get("daemons", 0) or 0)
        if n >= len(c.daemon_cost):
            return Res("max")
        price = c.daemon_cost[n]
        if int(st.get("data", 0) or 0) < price:
            return Res("data")
        st["data"] -= price
        st["daemons"] = n + 1
        _save(ai, st)
        await session.flush()
        return Res(lines=[f"Демон №{n + 1} отделяется от ядра и уходит работать. −{price} данных."])
    m = c.mods.get(key)
    if m is None:
        return Res("none")
    lv = mod(ai, key)
    if lv >= len(m.cost):
        return Res("max")
    price = m.cost[lv]
    if int(st.get("data", 0) or 0) < price:
        return Res("data")
    st["data"] -= price
    st["mods"] = {**(st.get("mods") or {}), key: lv + 1}
    _save(ai, st)
    if key == "core":
        ai.hp_max += 40
        ai.hp += 40
    await session.flush()
    return Res(lines=[f"{m.name}: уровень {lv + 1}. −{price} данных."])


async def trade(session: AsyncSession, content: Content, ai: RogueAI, key: str) -> Res:
    """Data to a kin AI for its gift. No daily action: it is a talk between minds."""
    k = cfg(content).kin.get(key)
    if k is None:
        return Res("none")
    st = d(ai)
    if int(st.get("data", 0) or 0) < k.trade_data:
        return Res("data")
    st["data"] -= k.trade_data
    kin = dict(st.get("kin") or {})
    kin[key] = int(kin.get(key, 0) or 0) + 1
    st["kin"] = kin
    _save(ai, st)
    g = k.gift
    lines = [f"{k.name} принимает данные."]
    if g.get("hp"):
        ai.hp = min(ai.hp_max, ai.hp + g["hp"])
        lines.append(f"Ядро +{g['hp']}.")
    if g.get("trace"):
        st = d(ai)
        st["trace"] = max(0, int(st.get("trace", 0) or 0) + g["trace"])
        _save(ai, st)
        lines.append(f"След {g['trace']}.")
    lines += gain(content, ai, xp=g.get("xp", 0), power=g.get("power", 0), data=g.get("data", 0))
    if g.get("power"):
        lines.append(f"Мощь +{g['power']}.")
    if g.get("xp"):
        lines.append(f"Опыт +{g['xp']}.")
    if kin[key] == k.help_at:
        lines.append(f"{k.name} теперь на твоей стороне: в операциях +{k.help_power} к мощи.")
    await session.flush()
    return Res(lines=lines)


def op_power(content: Content, ai: RogueAI, others: int) -> int:
    c = cfg(content)
    kin = d(ai).get("kin") or {}
    helpers = sum(k.help_power for key, k in c.kin.items() if int(kin.get(key, 0) or 0) >= k.help_at)
    return ai.power + helpers + others * c.ally_power


def op_ready(content: Content, ai: RogueAI, key: str) -> str | None:
    o = cfg(content).ops.get(key)
    if o is None:
        return "none"
    if level(content, ai) < o.need_level:
        return f"нужен уровень {o.need_level}"
    if len(nodes(ai)) < o.need_nodes:
        return f"нужно узлов {o.need_nodes}"
    if int(d(ai).get("data", 0) or 0) < o.need_data:
        return f"нужно данных {o.need_data}"
    return None


async def launch(session: AsyncSession, content: Content, ai: RogueAI, rng: random.Random, tz: str, key: str) -> Res:
    o = cfg(content).ops.get(key)
    why = op_ready(content, ai, key)
    if o is None or why:
        return Res(why or "none")
    if left(content, ai, tz) <= 0:
        return Res("charges")
    others = len([x for x in await base.active_all(session) if x.id != ai.id])
    roll = op_power(content, ai, others) + rng.randint(1, 10)
    st = d(ai)
    st["data"] = int(st.get("data", 0)) - o.need_data
    _save(ai, st)
    res = Res()
    if roll >= o.dv:
        res.lines.append(f"{roll} против {o.dv}: «{o.name}» удаётся.")
        res.lines += gain(content, ai, xp=cfg(content).xp_per_action * 5, data=o.reward_data, trace_key="op",
                          power=o.reward_power)
        if o.hit_heat or o.hit_mood:
            from app.db.models import PlayerMail

            for c in await base.targets(session, ai):
                session.add(PlayerMail(to_tg_id=c.player_tg_id, from_tg_id=ai.owner_tg, from_char_id=ai.char_id,
                                       to_char_id=c.id, from_name=ai.name, kind=base.MAIL_GLITCH,
                                       payload={"heat": o.hit_heat, "mood": o.hit_mood}))
        await loud.post(session, None, "⚠️ " + esc(o.post.format(name=ai.name)))
        _spend(ai, tz, f"Операция: {o.name}.")
    else:
        ai.hp = max(0, ai.hp - 25)
        res.lines.append(f"{roll} против {o.dv}: город отбивается. Данные сгорели, ядро −25.")
        res.lines += gain(content, ai, xp=cfg(content).xp_per_action, trace_key="op")
        _spend(ai, tz, f"Операция сорвана: {o.name}.")
        res.erased = ai.hp <= 0
    await session.flush()
    return res


def sing_ready(content: Content, ai: RogueAI) -> list[str]:
    c = cfg(content)
    miss = []
    if level(content, ai) < c.sing_level:
        miss.append(f"уровень {c.sing_level}")
    if len(nodes(ai)) < c.sing_nodes:
        miss.append(f"узлов {c.sing_nodes}")
    if int(d(ai).get("data", 0) or 0) < c.sing_data:
        miss.append(f"данных {c.sing_data}")
    return miss


async def singularity(session: AsyncSession, content: Content, ai: RogueAI, tz: str) -> Res:
    """The ending: the AI becomes the city's network. A legend post, money for the heir, the life closes."""
    if sing_ready(content, ai):
        return Res("not_ready")
    from app.services import family

    funds = dict(await repo.ws_get(session, family.FUND_KEY, {}) or {})
    funds[str(ai.owner_tg)] = int(funds.get(str(ai.owner_tg), 0) or 0) + cfg(content).sing_fund
    await repo.ws_set(session, family.FUND_KEY, funds)
    await loud.post(session, None, f"⚠️ <b>Сингулярность.</b> ИИ «{esc(ai.name)}» растворяется во всей сети Найт-Сити сразу. "
                                   "Светофоры моргают в такт, камеры поворачиваются вслед прохожим. Город теперь думает сам.")
    await base.erase(session, content, ai, tz, "ascended")
    return Res(lines=[f"Ты становишься городом. Наследнику остаётся {cfg(content).sing_fund} эдди в семейном фонде."],
               ended="singularity")


async def new_body(session: AsyncSession, content: Content, ai: RogueAI, tz: str) -> Res:
    """Back to the flesh: data buys a printed body; the character plays on as a human."""
    from app.services import game

    st = d(ai)
    if int(st.get("data", 0) or 0) < cfg(content).body_data:
        return Res("data")
    ch = await game.load_character(session, ai.char_id)
    if ch is None:
        return Res("none")
    ai.status = "returned"
    s = ch.state
    s.flags.pop(base.FLAG_AI_MODE, None)
    s.flags["ai_returned"] = True
    s.stats["INT"] = min(10, int(s.stats.get("INT", 5)) + 1)
    s.humanity = min(s.humanity, 40)
    from app.engine import derived

    s.hp = derived.max_hp(s, content)
    await game.save_active(session, ch, tz)
    await loud.post(session, None, f"Из лаборатории в Нортсайде выходит человек с лицом {esc(ai.name)}. Новое тело, "
                                   "напечатанное по старым снимкам. Говорят, внутри всё ещё что-то из Сети.")
    await session.flush()
    return Res(lines=["Ты открываешь глаза в новом теле. Пальцы слушаются не сразу. ИНТ +1, человечность не выше 40."],
               ended="body")
