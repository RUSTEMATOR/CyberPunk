"""Running a player's megacorp (owner request 24.10: rich characters must struggle; resources and strategy).

The corp lives in world_state `megacorps[owner tg]` (services/bigmoney.py). Fields added here:
  vault (eddies of the corp, apart from the owner's wallet), army {rec, vet, elite}, build {key: level}, intel, morale 0..100, policy (balance.corpwar.policies),
  policy_press (the press it was set), press_n (presses lived), allies {gang key: true}, hostile [gang keys],
  crisis {key} (waits for the owner's answer), income_half (the next income is halved), wealth (vault + owner's eddies
  at the last press: the pressure of the city on the rich).
Districts held by corps: world_state `corp_turf` = {district key: owner tg}.

Every press of the owner: income into the vault, upkeep from the vault (fighters, districts, security, tributes),
morale, gang raids on the districts, an unanswered crisis takes its worst outcome, a new crisis may come.
"""

from __future__ import annotations

import functools
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.texts import esc
from app.content.loader import Content
from app.db import repo
from app.engine import checks
from app.engine.effects import apply_effects
from app.services import loud
from app.services.game import ActiveCharacter, save_active

TURF_KEY = "corp_turf"
CRISES_FILE = Path(__file__).resolve().parent.parent.parent / "content" / "corp_crises.yaml"


@functools.cache
def crises() -> dict[str, dict]:
    with CRISES_FILE.open(encoding="utf-8") as f:
        return {c["key"]: c for c in yaml.safe_load(f)}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.corpwar


def ensure(content: Content, corp: dict) -> dict:
    c = cfg(content)
    corp.setdefault("vault", 0)
    if not isinstance(corp.get("army"), dict):
        corp["army"] = {"rec": int(corp.pop("troops", c.troop_start) or 0), "vet": 0, "elite": 0}
    corp.pop("troops", None)
    corp.setdefault("build", {})
    corp.setdefault("intel", 0)
    corp.setdefault("morale", c.morale_start)
    corp.setdefault("policy", "balance")
    corp.setdefault("press_n", 0)
    corp.setdefault("allies", {})
    corp.setdefault("hostile", [])
    return corp


TIERS = ("rec", "vet", "elite")


def bld(corp: dict, key: str) -> int:
    return int((corp.get("build") or {}).get(key, 0) or 0)


def troops(corp: dict) -> int:
    a = corp.get("army") or {}
    return sum(int(a.get(t, 0) or 0) for t in TIERS)


def troop_cap(content: Content, corp: dict) -> int:
    c = cfg(content)
    return c.troop_max + c.barracks_per_level * bld(corp, "barracks")


def army_power(content: Content, corp: dict) -> int:
    tiers = cfg(content).tiers
    a = corp.get("army") or {}
    return sum(int(a.get(t, 0) or 0) * (tiers[t].power if t in tiers else 1) for t in TIERS)


def add_recruits(content: Content, corp: dict, n: int) -> int:
    n = max(0, min(n, troop_cap(content, corp) - troops(corp)))
    corp["army"]["rec"] = int(corp["army"].get("rec", 0)) + n
    return n


def lose(content: Content, corp: dict, n: int, clinic: bool = True) -> int:
    """`n` fighters fall; the clinic saves some. Recruits go first, the elite last. Returns the real loss."""
    if n <= 0:
        return 0
    if clinic:
        n -= n * min(100, cfg(content).clinic_save_pct * bld(corp, "clinic")) // 100
    lost = 0
    for t in TIERS:
        have = int(corp["army"].get(t, 0) or 0)
        k = min(have, n - lost)
        corp["army"][t] = have - k
        lost += k
        if lost >= n:
            break
    return lost


def policy(content: Content, corp: dict):  # noqa: ANN201
    pols = cfg(content).policies
    return pols.get(str(corp.get("policy", "balance"))) or next(iter(pols.values()))


def pressure(content: Content, corp: dict) -> float:
    c = cfg(content)
    return 1.0 + min(c.pressure_cap, int(corp.get("wealth", corp.get("vault", 0)) or 0) / 1_000_000 * c.pressure_per_million)


async def all_corps(session: AsyncSession) -> dict[str, dict]:
    from app.services import bigmoney

    return await bigmoney.corps(session)


async def save_corps(session: AsyncSession, all_: dict[str, dict]) -> None:
    from app.services import bigmoney

    await repo.ws_set(session, bigmoney.CORPS_KEY, all_)


async def turf(session: AsyncSession) -> dict[str, str]:
    return {str(k): str(v) for k, v in (await repo.ws_get(session, TURF_KEY, {}) or {}).items()}


def districts_of(t: dict[str, str], owner: str) -> list[str]:
    return sorted(d for d, o in t.items() if o == str(owner))


def local_gangs(content: Content, year: int, district: str) -> list[str]:
    d = content.districts.get(district)
    avail = content.available("gangs", year)
    return [g for g in (d.gangs if d else []) if g in avail]


def district_power(content: Content, district: str) -> int:
    c = cfg(content)
    d = content.districts.get(district)
    return c.district_power_base + c.district_power_per_danger * (d.danger if d else 2)


def power(content: Content, corp: dict, t: dict[str, str], owner: str) -> int:
    """The strength of the corp in a war and in its districts (before the die)."""
    c = cfg(content)
    from app.services import pcorp

    return (army_power(content, corp) + 3 * int(corp.get("security", 0) or 0) + c.walls_power * bld(corp, "walls")
            + 2 * len(districts_of(t, owner))
            + c.ally_power * len(corp.get("allies") or {}) + min(4, len(pcorp.staff_of(corp)))
            + int(corp.get("morale", 50)) // 20 + policy(content, corp).power)


def upkeep(content: Content, corp: dict, t: dict[str, str], owner: str) -> int:
    c = cfg(content)
    lvl = int(corp.get("security", 0) or 0)
    a = corp.get("army") or {}
    army = sum(int(a.get(k, 0) or 0) * c.tiers[k].upkeep for k in TIERS if k in c.tiers)
    build = sum(c.buildings[k].upkeep * lv for k, lv in (corp.get("build") or {}).items() if k in c.buildings)
    return (army + build + len(districts_of(t, owner)) * c.district_upkeep
            + c.security_upkeep[min(lvl, len(c.security_upkeep) - 1)] + c.ally_tribute * len(corp.get("allies") or {}))


def intel_cap(content: Content, corp: dict) -> int:
    return cfg(content).intel_max + 3 * bld(corp, "intel")


def _clamp_morale(corp: dict) -> None:
    corp["morale"] = max(0, min(100, int(corp.get("morale", 50))))


# -- effects on the corp ------------------------------------------------------------------------------------------------


async def apply_fx(session: AsyncSession, content: Content, ch: ActiveCharacter | None, owner: str, corp: dict,
                   fx: dict[str, Any], rng: random.Random, year: int) -> list[str]:
    """Crisis and war effects. The owner's own numbers (hp, heat) change only when `ch` is the owner in hand."""
    c = cfg(content)
    out: list[str] = []
    if "vault" in fx:
        corp["vault"] = max(0, int(corp["vault"]) + int(fx["vault"]))
    if "vault_pct" in fx:
        corp["vault"] = max(0, int(corp["vault"]) * (100 + int(fx["vault_pct"])) // 100)
    if "troops" in fx:
        n = int(fx["troops"])
        if n < 0:
            out.append(f"Бойцов −{lose(content, corp, -n)}.")
        else:
            add_recruits(content, corp, n)
    for k in ("intel", "integrity"):
        if k in fx:
            corp[k] = max(0, int(corp.get(k, 0) or 0) + int(fx[k]))
    corp["intel"] = min(intel_cap(content, corp), corp["intel"])
    if "morale" in fx:
        corp["morale"] = int(corp["morale"]) + int(fx["morale"])
        _clamp_morale(corp)
    if "security" in fx:
        corp["security"] = max(0, int(corp.get("security", 0) or 0) + int(fx["security"]))
    if fx.get("income_half"):
        corp["income_half"] = True
    t = await turf(session)
    if fx.get("lose_district"):
        mine = districts_of(t, owner)
        if mine:
            lost = rng.choice(mine)
            t.pop(lost, None)
            await repo.ws_set(session, TURF_KEY, t)
            d = content.districts.get(lost)
            out.append(f"Район «{d.name if d else lost}» потерян.")
    if fx.get("gang_hostile") or fx.get("ally_local"):
        near = [g for d in districts_of(t, owner) for g in local_gangs(content, year, d)]
        if not near:
            near = list(content.available("gangs", year))
        if near:
            g = rng.choice(near)
            if fx.get("ally_local"):
                corp["allies"] = {**(corp.get("allies") or {}), g: True}
                out.append(f"Союзник: {content.gangs[g].name}.")
            else:
                corp["hostile"] = sorted(set(corp.get("hostile") or []) | {g})
                out.append(f"Враг на улицах: {content.gangs[g].name}.")
    if fx.get("war"):
        from app.services import corpwar

        line = await corpwar.npc_declares(session, content, owner, corp, rng, year)
        if line:
            out.append(line)
    if ch is not None:
        own = {}
        if "owner_hp" in fx:
            own["hp"] = int(fx["owner_hp"])
        if "heat" in fx:
            own["heat"] = int(fx["heat"])
        if own:
            apply_effects(ch.state, own, content, rng, year)
            if ch.state.hp <= 0:
                ch.state.hp = 1
    return out


# -- the owner's press --------------------------------------------------------------------------------------------------


async def press(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> list[str]:
    all_ = await all_corps(session)
    owner = str(ch.player_tg_id)
    corp = all_.get(owner)
    if corp is None:
        return []
    c = cfg(content)
    ensure(content, corp)
    corp["press_n"] = int(corp["press_n"]) + 1
    corp["wealth"] = int(corp["vault"]) + max(0, ch.state.eddies)
    t = await turf(session)
    pol = policy(content, corp)
    name = corp["name"]
    out = [f"🏢 <b>{esc(name)}</b> · {esc(pol.name)}"]
    old = corp.pop("crisis", None)
    if old and old.get("key") in crises():
        cr = crises()[old["key"]]
        ig = cr.get("ignore") or {}
        extra = await apply_fx(session, content, ch, owner, corp, dict(ig.get("effects") or {}), rng, year)
        out.append(f"⚠️ «{esc(cr['title'])}» без ответа: {esc(ig.get('text', ''))} {esc(' '.join(extra))}".rstrip())
    from app.services import pcorp

    mine = districts_of(t, owner)
    income = rng.randint(*c.vault_income) + c.vault_income_per_staff * len(pcorp.staff_of(corp))
    income += sum(rng.randint(*c.district_income) for _ in mine)
    income = income * (100 + pol.income_pct) // 100
    if corp.pop("income_half", None):
        income //= 2
    if int(corp["morale"]) < c.low_morale:
        income //= 2
        out.append("😠 Лояльность низкая: люди работают вполсилы, доход вдвое меньше.")
    cost = upkeep(content, corp, t, owner)
    corp["vault"] = int(corp["vault"]) + income
    out.append(f"Доход {income} эдди в сейф, содержание {cost}.")
    if corp["vault"] >= cost:
        corp["vault"] -= cost
    else:
        corp["vault"] = 0
        gone = lose(content, corp, max(1, troops(corp) * c.unpaid_desert_pct // 100), clinic=False) if troops(corp) else 0
        corp["morale"] = int(corp["morale"]) + c.unpaid_morale
        corp["integrity"] = max(1, int(corp.get("integrity", 0) or 0) + c.unpaid_integrity)
        corp["allies"] = {}
        out.append(f"💸 Сейф пуст, содержание не оплачено: бойцов ушло {gone}, союзы с бандами разорваны, "
                   f"лояльность {c.unpaid_morale}, башня ветшает.")
    corp["morale"] = int(corp["morale"]) + pol.morale + c.pr_morale * bld(corp, "pr")
    _clamp_morale(corp)
    corp["intel"] = min(intel_cap(content, corp), int(corp["intel"]) + c.intel_per_level * bld(corp, "intel"))
    if pol.heat:
        apply_effects(ch.state, {"heat": pol.heat}, content, rng, year)
    pr = pressure(content, corp)
    out.extend(await _raids(session, content, owner, corp, t, rng, year, pr))
    chance = min(0.95, c.crisis_chance * pr * max(0, 100 - c.pr_crisis_pct * bld(corp, "pr")) / 100)
    if rng.random() < chance:
        key = rng.choice(sorted(crises()))
        corp["crisis"] = {"key": key}
        out.append(f"🚨 Кризис: <b>{esc(crises()[key]['title'])}</b>. Реши в «Управлении корпорацией» до следующего "
                   "месяца, иначе случится худшее.")
    out.append(f"Сейф {corp['vault']} · бойцов {troops(corp)} · разведка {corp['intel']} · лояльность {corp['morale']} · "
               f"районов {len(districts_of(t, owner))}")
    all_[owner] = corp
    await save_corps(session, all_)
    return out


async def _raids(session: AsyncSession, content: Content, owner: str, corp: dict, t: dict[str, str],
                 rng: random.Random, year: int, pr: float) -> list[str]:
    c = cfg(content)
    pol = policy(content, corp)
    mine = districts_of(t, owner)
    out: list[str] = []
    if not mine:
        return out
    hold = max(1, army_power(content, corp) // len(mine)) + cfg(content).walls_power * bld(corp, "walls")
    for d in mine:
        gangs = local_gangs(content, year, d)
        if not gangs or any(g in (corp.get("allies") or {}) for g in gangs):
            continue
        hostile = any(g in (corp.get("hostile") or []) for g in gangs)
        chance = c.raid_chance * (100 + pol.raid_pct) / 100 * pr * (2 if hostile else 1)
        if rng.random() >= min(0.9, chance):
            continue
        g = next((g for g in gangs if g in (corp.get("hostile") or [])), rng.choice(gangs))
        atk = district_power(content, d) + rng.randint(1, 10)
        dfn = hold + 2 * int(corp.get("security", 0) or 0) + pol.power + rng.randint(1, 10)
        dn = content.districts[d].name if d in content.districts else d
        if atk > dfn:
            t.pop(d, None)
            k = lose(content, corp, 2)
            corp["morale"] = max(0, int(corp["morale"]) - 5)
            out.append(f"🔥 {content.gangs[g].name} отбивают район «{dn}»: бойцов −{k}.")
        else:
            k = lose(content, corp, 1)
            out.append(f"🛡 Налёт {content.gangs[g].name} на «{dn}» отбит: бойцов −{k}.")
    await repo.ws_set(session, TURF_KEY, t)
    return out


# -- the owner's decisions ----------------------------------------------------------------------------------------------


@dataclass
class Result:
    blocked: str | None = None
    lines: list[str] = field(default_factory=list)
    check: object | None = None


async def _mine(session: AsyncSession, content: Content, ch: ActiveCharacter) -> tuple[dict, dict] | None:
    all_ = await all_corps(session)
    corp = all_.get(str(ch.player_tg_id))
    if corp is None:
        return None
    return all_, ensure(content, corp)


async def act(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              action: str, arg: str = "") -> Result:
    got = await _mine(session, content, ch)
    if got is None:
        return Result("nocorp")
    all_, corp = got
    owner = str(ch.player_tg_id)
    c = cfg(content)
    st = ch.state
    res = Result()
    if action == "hire":
        n = max(1, int(arg or 1))
        n = min(n, troop_cap(content, corp) - troops(corp))
        price = n * c.troop_price
        if n <= 0:
            return Result("troop_max")
        if corp["vault"] < price:
            return Result("vault")
        corp["vault"] -= price
        add_recruits(content, corp, n)
        res.lines.append(f"Нанято новобранцев: {n}, −{price} из сейфа.")
    elif action == "fire":
        n = min(int(corp["army"].get("rec", 0)), max(1, int(arg or 1)))
        if n <= 0:
            return Result("no_rec")
        corp["army"]["rec"] -= n
        corp["morale"] = max(0, int(corp["morale"]) - 2)
        res.lines.append(f"Распущено новобранцев: {n}.")
    elif action == "upgrade":
        tier = arg if arg in ("vet", "elite") else "vet"
        tc = c.tiers[tier]
        below = "rec" if tier == "vet" else "vet"
        if bld(corp, "armory") < tc.armory:
            return Result("armory")
        n = min(5, int(corp["army"].get(below, 0)))
        if n <= 0:
            return Result("no_" + below)
        n = min(n, int(corp["vault"]) // tc.upgrade_price)
        if n <= 0:
            return Result("vault")
        corp["vault"] -= n * tc.upgrade_price
        corp["army"][below] -= n
        corp["army"][tier] = int(corp["army"].get(tier, 0)) + n
        res.lines.append(f"Обучено: {n} → {tc.name}, −{n * tc.upgrade_price} из сейфа.")
    elif action == "build":
        b = c.buildings.get(arg)
        if b is None:
            return Result("none")
        lv = bld(corp, arg)
        if lv >= len(b.prices):
            return Result("build_max")
        price = b.prices[lv]
        if corp["vault"] < price:
            return Result("vault")
        corp["vault"] -= price
        corp["build"] = {**(corp.get("build") or {}), arg: lv + 1}
        res.lines.append(f"Построено: {b.name}, уровень {lv + 1}. −{price} из сейфа.")
    elif action == "intel":
        if corp["intel"] >= intel_cap(content, corp):
            return Result("intel_max")
        if corp["vault"] < c.intel_price:
            return Result("vault")
        corp["vault"] -= c.intel_price
        corp["intel"] += 1
        res.lines.append(f"Нетраннеры и информаторы: разведка +1, −{c.intel_price} из сейфа.")
    elif action == "policy":
        if arg not in c.policies:
            return Result("none")
        if corp.get("policy_press") == corp["press_n"] and corp.get("policy") != arg:
            return Result("policy_once")
        corp["policy"] = arg
        corp["policy_press"] = corp["press_n"]
        res.lines.append(f"Директива на месяц: «{c.policies[arg].name}».")
    elif action == "deposit":
        n = int(arg or 0)
        if n <= 0 or st.eddies < n:
            return Result("eddies")
        apply_effects(st, {"eddies": -n}, content, rng, year)
        corp["vault"] += n
        res.lines.append(f"В сейф корпорации: +{n}.")
    elif action == "withdraw":
        n = int(arg or 0)
        if n <= 0 or corp["vault"] < n:
            return Result("vault")
        corp["vault"] -= n
        apply_effects(st, {"eddies": n}, content, rng, year)
        corp["morale"] = max(0, int(corp["morale"]) - (5 if n >= 100000 else 0))
        res.lines.append(f"Из сейфа себе: {n}." + (" Совет директоров недоволен." if n >= 100000 else ""))
    elif action == "ally":
        if arg not in content.gangs:
            return Result("none")
        corp["allies"] = {**(corp.get("allies") or {}), arg: True}
        corp["hostile"] = [g for g in corp.get("hostile") or [] if g != arg]
        res.lines.append(f"Союз с {content.gangs[arg].name_ins or content.gangs[arg].name}: дань {c.ally_tribute} в месяц из сейфа.")
    elif action == "unally":
        al = dict(corp.get("allies") or {})
        al.pop(arg, None)
        corp["allies"] = al
        res.lines.append("Союз разорван.")
    elif action == "take":
        r = await _take_district(session, content, ch, owner, corp, arg, rng, year)
        if r.blocked:
            return r
        res = r
    elif action == "crisis":
        r = await _answer(session, content, ch, owner, corp, int(arg or 0), rng, year)
        if r.blocked:
            return r
        res = r
    else:
        return Result("none")
    all_[owner] = corp
    await save_corps(session, all_)
    await save_active(session, ch, tz)
    return res


async def _take_district(session: AsyncSession, content: Content, ch: ActiveCharacter, owner: str, corp: dict,
                         district: str, rng: random.Random, year: int) -> Result:
    c = cfg(content)
    if district not in content.districts:
        return Result("none")
    t = await turf(session)
    held = t.get(district)
    if held == owner:
        return Result("have")
    if held is not None:
        from app.services import corpwar

        if not await corpwar.at_war(session, f"p:{owner}", f"p:{held}"):
            return Result("held")
    if troops(corp) < c.district_need * 2:
        return Result("troops")
    res = Result()
    if held is not None:
        all_ = await all_corps(session)
        enemy = ensure(content, all_.get(held) or {})
        dfn = power(content, enemy, t, held) // max(1, len(districts_of(t, held))) + rng.randint(1, 10)
        foe = f"«{enemy.get('name', '?')}»"
    else:
        gangs = local_gangs(content, year, district)
        dfn = district_power(content, district) + rng.randint(1, 10)
        foe = content.gangs[gangs[0]].name if gangs else "местные"
    atk = army_power(content, corp) // 2 + 3 * int(corp.get("security", 0) or 0) + policy(content, corp).power + rng.randint(1, 10)
    dn = content.districts[district].name
    if atk > dfn:
        lost = lose(content, corp, rng.randint(1, 3))
        t[district] = owner
        await repo.ws_set(session, TURF_KEY, t)
        gangs = local_gangs(content, year, district)
        if held is None and gangs:
            corp["hostile"] = sorted(set(corp.get("hostile") or []) | {g for g in gangs if g not in (corp.get("allies") or {})})
        res.lines.append(f"Район «{dn}» взят: {foe} выбиты. Потери: {lost} бойцов. Теперь он приносит доход, но его "
                         "придётся держать.")
        await loud.post(session, ch.id, f"«{esc(corp['name'])}» захватывает район «{esc(dn)}». {esc(foe)} уходят с улиц.")
        if held is not None:
            from app.services import bigmoney

            bigmoney._mail(session, int(held), f"«{corp['name']}» отбивает у тебя район «{dn}».")
    else:
        lost = lose(content, corp, rng.randint(3, 6))
        corp["morale"] = max(0, int(corp["morale"]) - 10)
        res.lines.append(f"Штурм района «{dn}» отбит: {foe} сильнее. Потери: {lost} бойцов, лояльность −10.")
    res.lines.append(f"(твоя сила {atk} против {dfn})")
    return res


async def _answer(session: AsyncSession, content: Content, ch: ActiveCharacter, owner: str, corp: dict, idx: int,
                  rng: random.Random, year: int) -> Result:
    cur = corp.get("crisis")
    if not cur or cur.get("key") not in crises():
        return Result("nocrisis")
    cr = crises()[cur["key"]]
    opts = cr["options"]
    if not 0 <= idx < len(opts):
        return Result("none")
    o = opts[idx]
    res = Result()
    if o.get("check"):
        chk = o["check"]
        res.check = checks.roll_check(ch.state, content, rng, chk["stat"], chk.get("skill"), int(chk["dv"]))
        out = o["success"] if res.check.ok else o["fail"]  # type: ignore[attr-defined]
    else:
        out = o
    corp.pop("crisis", None)
    extra = await apply_fx(session, content, ch, owner, corp, dict(out.get("effects") or {}), rng, year)
    res.lines.append(str(out.get("text", "")))
    res.lines.extend(extra)
    return res
