"""A sortie through a piece of the district (owner request 24.10): start, walk, deal with what is there, leave.

The map and the moves are pure (app/engine/sortie.py); here the checks, the fights (services/combat.begin with
src `sortie`, back through `fight_over`), the pocket of eddies and the payout at the exit.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.engine import checks
from app.engine import combat as cb
from app.engine import sortie as eng
from app.engine.effects import AppliedEffects, apply_effects
from app.engine.state import FLAG_COMBAT, FLAG_PENDING_COMBAT
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

SRC = "sortie"

# the goal by kind: (title, text, check (stat, skill, dv) or None, eddies range, extra effects)
GOALS: dict[str, tuple[str, str, tuple[str, str | None, int] | None, tuple[int, int], dict[str, Any]]] = {
    "hideout": ("Старый схрон", "Под плиткой в подъезде, где вы прятались ещё детьми, лежит жестяная коробка.",
                None, (1000, 2500), {"mood": 5}),
    "cash": ("Касса барыги", "Барыга отлучился, а сейф за стойкой открыт наполовину.",
             ("REF", "stealth", 14), (2000, 5000), {"heat": 1}),
    "wreck": ("Разбитый грузовик", "Брошенный грузовик курьеров: внутри детали, которые стоят денег.",
              ("TECH", "tech", 13), (1500, 3000), {"vehicle_fix": True}),
    "shard": ("Флешка с компроматом", "В урне у офиса кто-то выбросил флешку с отчётами, которые стоят карьеры.",
              ("INT", None, 14), (2500, 4500), {"status": 1}),
    "node": ("Узел доступа", "Распределительный щит городской сети. Подключиться — и чужие счета как на ладони.",
             ("INT", "netrunning", 14), (2000, 4000), {"skill_xp:netrunning": 3}),
    "wall": ("Стена для граффити", "Пустая стена напротив полицейского участка. Сюда смотрит весь район.",
             ("COOL", None, 12), (500, 1200), {"fame": 1, "mood": 6}),
}

TITLES = {"gang": "Бандиты на углу", "harass": "К прохожему пристают", "stash": "Тайник", "trap": "Ловушка",
          "trader": "Торговец из-под полы", "loot": "Брошенная сумка"}
TEXTS = {
    "gang": "Трое у разбитого фонаря, руки в карманах. Смотрят на тебя так, будто ты им уже должен.",
    "harass": "Двое прижали к стене парня с пакетом лапши и выворачивают ему карманы. Он смотрит на тебя.",
    "stash": "Под вентиляционной решёткой кто-то оставил метку мелом. Значит, там тайник.",
    "trader": "Женщина в капюшоне открывает полу куртки: стимы, батарейки, сложенная карта района.",
}
OPTIONS: dict[str, list[tuple[str, str]]] = {
    "gang": [("fight", "Напасть"), ("sneak", "Прокрасться мимо"), ("back", "Отойти назад")],
    "harass": [("fight", "Вмешаться"), ("talk", "Уговорить отстать"), ("ignore", "Не лезть")],
    "stash": [("open", "Вскрыть"), ("skip", "Оставить")],
    "trader": [("stim", "Стим"), ("map", "Карта района"), ("skip", "Дальше")],
    "goal": [("goal", "Взяться"), ("skip", "Не сейчас")],
}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.sortie


def run_of(ch: ActiveCharacter) -> dict[str, Any] | None:
    r = ch.state.flags.get(eng.FLAG)
    return r if isinstance(r, dict) else None


@dataclass
class Result:
    blocked: str | None = None
    lines: list[str] = field(default_factory=list)
    check: object | None = None
    applied: AppliedEffects | None = None
    step: object | None = None  # a combat Step when a fight started
    ended: str = ""  # the summary when the sortie is over


def _danger(content: Content, district: str) -> int:
    d = content.districts.get(district)
    return max(1, min(4, d.danger if d else 2))


def _gang(content: Content, district: str, year: int, rng: random.Random) -> str:
    gangs = content.available("gangs", year)
    d = content.districts.get(district)
    here = [g for g in (d.gangs if d else []) if g in gangs]
    pool = here or list(gangs)
    return rng.choice(pool) if pool else "ncpd"


def goal_kind(content: Content, ch: ActiveCharacter) -> str:
    p = content.presets.get(ch.state.preset or "")
    return eng.GOALS.get(getattr(p, "group", "") or "", "hideout")


async def start(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str) -> Result:
    st = ch.state
    if run_of(ch) is not None:
        return Result()
    if int(st.flags.get("jail_weeks", 0) or 0) > 0:
        return Result("jail")
    from app.engine import abroad as ae

    if st.flags.get("abroad") and not ae.in_city(st, content):  # owner request 25.10: a sortie in the city abroad
        return Result("abroad")
    if st.flags.get(FLAG_COMBAT) or st.flags.get(FLAG_PENDING_COMBAT):
        return Result("busy")
    c = cfg(content)
    if not ap_services.can_spend(ch, c.ap):
        return Result("ap")
    ap_services.spend(ch, content, c.ap)
    st.flags[eng.FLAG] = eng.generate(rng, c.size, c.steps, dict(c.pois), st.district, goal_kind(content, ch))
    await save_active(session, ch, tz)
    return Result()


def _enter(content: Content, ch: ActiveCharacter, run: dict[str, Any], rng: random.Random, year: int) -> list[str]:
    """What happens by itself on entering a cell: a trap springs, a dropped bag is picked up."""
    c = cfg(content)
    p = eng.here(run)
    if p is None or p["k"] not in ("trap", "loot"):
        return []
    eng.resolve_here(run)
    if p["k"] == "loot":
        got = rng.randint(*c.loot_eddies)
        run["eddies"] += got
        return [f"💰 Брошенная сумка в мусорном баке: {got} эдди в карман."]
    chk = checks.roll_check(ch.state, content, rng, "INT", None, 12)
    if chk.ok:
        got = rng.randint(100, 300)
        run["eddies"] += got
        return [f"⚠️ Растяжка у самого пола. Ты замечаешь её вовремя и снимаешь детонатор: продашь за {got} эдди."]
    apply_effects(ch.state, {"hp": -c.trap_hp}, content, rng, year)
    return [f"⚠️ Растяжка у самого пола. Щелчок, вспышка, осколки. Здоровье −{c.trap_hp}."]


async def go(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
             d: str) -> Result:
    run = run_of(ch)
    if run is None:
        return Result("none")
    p = eng.here(run)
    if p is not None and p["k"] in ("gang", "harass"):
        return Result("blocked")  # a gang or a harassment in the way: deal with it or step back
    if run["steps"] <= 0:
        return await leave(session, content, ch, rng, year, tz, forced=True)
    if not eng.move(run, d):
        return Result("edge")
    res = Result(lines=_enter(content, ch, run, rng, year))
    ch.state.flags[eng.FLAG] = run
    if ch.state.hp <= 0:
        ch.state.hp = 1
    await save_active(session, ch, tz)
    return res


def _close(content: Content, ch: ActiveCharacter, run: dict[str, Any], rng: random.Random, year: int,
           keep_pct: int) -> str:
    c = cfg(content)
    st = ch.state
    pocket = run["eddies"] * keep_pct // 100
    fx: dict[str, Any] = {}
    lines = []
    if pocket:
        fx["eddies"] = pocket
    if keep_pct == 100 and eng.cleared(run) and any(p["k"] in eng.HOSTILE for p in run["pois"].values()):
        bonus = rng.randint(*c.clear_eddies)
        fx["eddies"] = int(fx.get("eddies", 0)) + bonus
        fx["street_cred"] = c.clear_cred
        lines.append(f"Кусок района зачищен: улица благодарна. Бонус {bonus} эдди, уличная репутация +{c.clear_cred}.")
    if fx:
        apply_effects(st, fx, content, rng, year)
    st.flags.pop(eng.FLAG, None)
    head = (f"Ты выходишь с добычей: {pocket} эдди." if keep_pct == 100 else
            f"Уходишь не через выход: половина добычи потеряна по дороге. На руках {pocket} эдди.")
    return "\n".join([head] + lines)


async def leave(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                forced: bool = False) -> Result:
    run = run_of(ch)
    if run is None:
        return Result("none")
    keep = 100 if eng.at_exit(run) and not forced else cfg(content).early_keep_pct
    res = Result(ended=("Темнеет. Пора уходить, пока улица не стала охотиться на тебя.\n" if forced else "")
                 + _close(content, ch, run, rng, year, keep))
    await repo.log_event(session, "sortie", {"district": run["district"], "eddies": run["eddies"], "keep": keep,
                                             "cleared": eng.cleared(run), "goal": run["goal"]["done"]},
                         character_id=ch.id)
    await save_active(session, ch, tz)
    return res


async def _fight(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                 tz: str, run: dict[str, Any], kind: str, danger: int) -> Result:
    from app.services import combat as combat_svc

    x, y = run["pos"]
    run["fight"] = eng.key(x, y)
    ch.state.flags[eng.FLAG] = run
    faction = _gang(content, run["district"], year, rng)
    step = await combat_svc.begin(session, content, ch, rng, year, tz, faction=faction, danger=danger,
                                  src={"src": SRC, "kind": kind})
    return Result(step=step)


async def act(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              option: str) -> Result:
    c = cfg(content)
    st = ch.state
    run = run_of(ch)
    if run is None:
        return Result("none")
    p = eng.here(run)
    if p is None:
        return Result("none")
    k = p["k"]
    d = _danger(content, run["district"])
    res = Result()
    fx: dict[str, Any] = {}
    if k == "gang":
        if option == "fight":
            return await _fight(session, content, ch, rng, year, tz, run, k, d)
        if option == "back":
            eng.step_back(run)
            res.lines.append("Ты сворачиваешь обратно, пока на тебя не обратили внимания.")
        elif option == "sneak":
            res.check = checks.roll_check(st, content, rng, "REF", "stealth", 12 + d, ("gig_quiet",))
            if res.check.ok:  # type: ignore[attr-defined]
                eng.resolve_here(run)
                res.lines.append("Тень от вывески, шаг в такт музыке из окна. Они так и не поворачивают головы.")
            else:
                res.lines.append("Под ногой хрустит стекло. «Эй, ты!»")
                return await _with_lines(await _fight(session, content, ch, rng, year, tz, run, k, d), res.lines)
    elif k == "harass":
        if option == "fight":
            return await _fight(session, content, ch, rng, year, tz, run, k, max(1, d - 1))
        if option == "talk":
            res.check = checks.roll_check(st, content, rng, "COOL", "persuasion", 12 + d, ("social",))
            if res.check.ok:  # type: ignore[attr-defined]
                eng.resolve_here(run)
                fx.update({"humanity": 1, "street_cred": 1})
                res.lines.append("Ты говоришь тихо и спокойно. Они переглядываются и уходят. Парень с лапшой кивает "
                                 "тебе на прощание.")
            else:
                res.lines.append("«Тебе что, больше всех надо?» Разговор окончен.")
                return await _with_lines(await _fight(session, content, ch, rng, year, tz, run, k, max(1, d - 1)),
                                         res.lines)
        elif option == "ignore":
            eng.resolve_here(run)
            fx["humanity"] = -1
            res.lines.append("Ты проходишь мимо. За спиной кто-то всхлипывает. Город учит не оборачиваться.")
    elif k == "stash":
        if option == "open":
            res.check = checks.roll_check(st, content, rng, "TECH", "tech", 12)
            eng.resolve_here(run)
            if res.check.ok:  # type: ignore[attr-defined]
                got = rng.randint(*c.stash_eddies)
                run["eddies"] += got
                res.lines.append(f"Решётка поддаётся. Внутри пачка кредитных чипов: {got} эдди в карман.")
            else:
                fx["hp"] = -c.stash_trap_hp
                res.lines.append(f"Внутри была ловушка с шокером. Здоровье −{c.stash_trap_hp}, тайник пуст.")
        else:
            eng.resolve_here(run)
            res.lines.append("Пусть лежит. Чужие тайники иногда стреляют.")
    elif k == "trader":
        if option == "stim":
            if st.eddies < c.stim_price:
                return Result("eddies")
            fx.update({"eddies": -c.stim_price, "hp": c.stim_hp})
            res.lines.append(f"Стим в шею. Боль отступает. Здоровье +{c.stim_hp}.")
        elif option == "map":
            if st.eddies < c.map_price:
                return Result("eddies")
            fx["eddies"] = -c.map_price
            eng.reveal_all(run)
            res.lines.append("Карта нарисована от руки, но точная: теперь видно весь кусок района.")
        else:
            eng.resolve_here(run)
            res.lines.append("Торговка пожимает плечами и растворяется в переулке.")
    elif k == "goal":
        title, text, chk, rng_eddies, extra = GOALS[run["goal"]["k"]]
        if option != "goal":
            eng.step_back(run)
            res.lines.append("Не сейчас. Цель никуда не денется до конца вылазки.")
        else:
            ok = True
            if chk is not None:
                res.check = checks.roll_check(st, content, rng, chk[0], chk[1], chk[2])
                ok = bool(res.check.ok)  # type: ignore[attr-defined]
            eng.resolve_here(run)
            if ok:
                got = rng.randint(*rng_eddies)
                run["eddies"] += got
                for key, val in extra.items():
                    if key == "vehicle_fix":
                        v = st.flags.get("vehicle")
                        if isinstance(v, dict):
                            v = dict(v)
                            v["dmg"] = 0
                            st.flags["vehicle"] = v
                    else:
                        fx[key] = val
                res.lines.append(f"🎯 {title}: получилось. {got} эдди в карман.")
            else:
                fx["heat"] = 1
                res.lines.append(f"🎯 {title}: не вышло. Кто-то видел попытку. Розыск +1.")
    if fx:
        res.applied = apply_effects(st, fx, content, rng, year)
    st.flags[eng.FLAG] = run
    if st.hp <= 0:
        st.hp = 1
    await save_active(session, ch, tz)
    return res


async def _with_lines(res: Result, lines: list[str]) -> Result:
    res.lines = lines + res.lines
    return res


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict[str, Any]) -> list[str]:
    run = run_of(ch)
    if run is None:
        return []
    c = cfg(content)
    run["fight"] = None
    lines: list[str] = []
    if result == cb.WON:
        eng.resolve_here(run)
        got = rng.randint(*c.fight_eddies) * _danger(content, run["district"])
        run["eddies"] += got
        lines.append(f"Улица снова твоя. С побеждённых: {got} эдди в карман вылазки.")
        if ctx.get("kind") == "harass":
            apply_effects(ch.state, {"humanity": 2, "street_cred": 1}, content, rng, year)
            lines.append("Парень с пакетом лапши жмёт тебе руку и исчезает. Человечность +2.")
        ch.state.flags[eng.FLAG] = run
    elif result in (cb.FLED, cb.AVOIDED):
        eng.step_back(run)
        ch.state.flags[eng.FLAG] = run
        lines.append("Ты отходишь на соседнюю улицу. Они остались на месте.")
    else:
        ch.state.flags.pop(eng.FLAG, None)
        lines.append(f"Вылазка сорвана: добыча из карманов ({run['eddies']} эдди) потеряна.")
    return lines
