"""Big money and the shared city (owner request, docs/PLAN_ECONOMY.md stage 6): the auction in the group, influence
on the city, the empire and the players' own megacorps (which other players can raze).

World state:
- `auction` = {id, kind, key, name, price, start, end, top_tg, top_name, top_amount, bids};
- `megacorps` = {owner tg: {name, integrity, security, founded}}; `megacorp_hits` = {"attacker:owner": unix time}.
Money of another player is never touched from here: refunds and lots go by mail (PlayerMail kind `city`).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.texts import esc
from app.content.loader import Content
from app.db import repo
from app.db.models import PlayerMail
from app.engine import checks
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services import loud
from app.services.game import ActiveCharacter, save_active

AUCTION_KEY = "auction"
CORPS_KEY = "megacorps"
HITS_KEY = "megacorp_hits"

R_EDDIES = "eddies"
R_LOW = "low"
R_NONE = "none"
R_OWN = "own"
R_GONE = "gone"
R_HAVE = "have"
R_NO_AP = "no_ap"
R_COOLDOWN = "cooldown"
R_JAIL = "jail"
R_ORDER = "order"
R_NAME = "name"
R_STAFF = "staff"

GRANT = {"weapons": "item_grant", "armor": "item_grant", "cyberware": "cyberware_grant", "vehicles": "vehicle_grant"}


def q(name: str) -> str:
    """A name in quotes, unless it already carries its own («Винтовка «Гром»» reads badly)."""
    return name if "«" in name else f"«{name}»"


def _mail(session: AsyncSession, tg: int, text: str, **payload: object) -> None:
    session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name="Город", kind="city",
                           payload={"text": text, **payload}))


# -- auction -------------------------------------------------------------------------------------------------------------


def _lot_pool(content: Content) -> list[tuple[str, str, str, int]]:
    out = []
    for kind in ("weapons", "armor", "cyberware", "vehicles"):
        for key, rec in getattr(content, kind).items():
            if getattr(rec, "not_for_sale", False) and (key.startswith("lg_") or kind == "vehicles"):
                out.append((kind, key, rec.name, int(rec.price)))
    return out


async def auction(session: AsyncSession) -> dict | None:
    a = await repo.ws_get(session, AUCTION_KEY)
    return dict(a) if a else None


def min_bid(content: Content, a: dict) -> int:
    cfg = content.balance.economy.auction
    top = int(a.get("top_amount", 0) or 0)
    if top <= 0:
        return int(a["start_price"])
    return top + max(100, top * cfg.min_step_pct // 100)


async def auction_tick(session: AsyncSession, content: Content) -> list[str]:
    """Close a finished lot (the winner gets it by mail) and open the next one. Returns posts for the group."""
    cfg = content.balance.economy.auction
    now = time.time()
    posts: list[str] = []
    a = await auction(session)
    if a is not None and now >= float(a["end"]):
        if a.get("top_tg"):
            grant = f"{GRANT[a['kind']]}:{a['key']}"
            _mail(session, int(a["top_tg"]), f"Аукцион: лот {q(a['name'])} твой за {a['top_amount']} эдди.", grant=grant)
            posts.append(f"🔨 <b>Аукцион закрыт.</b> {q(esc(a['name']))} уходит: {esc(str(a.get('top_name', '')))}, "
                         f"{a['top_amount']} эдди. Лот ждёт в /play.")
        else:
            posts.append(f"🔨 Аукцион закрыт: {q(esc(a['name']))} никто не взял.")
        a = None
        await repo.ws_set(session, AUCTION_KEY, None)
    if a is None:
        pool = _lot_pool(content)
        if not pool:
            return posts
        rng = random.Random()
        kind, key, name, price = rng.choice(pool)
        start = max(1000, int(price * cfg.start_price_pct))
        a = {"id": int(now), "kind": kind, "key": key, "name": name, "price": price, "start_price": start,
             "start": now, "end": now + cfg.duration_hours * 3600, "top_tg": None, "top_name": "", "top_amount": 0,
             "bids": 0}
        await repo.ws_set(session, AUCTION_KEY, a)
        posts.append(f"🔨 <b>Аукцион Найт-Сити.</b> Лот: {q(esc(name))}. Стартовая цена {start} эдди, торги "
                     f"{cfg.duration_hours} ч. Ставки — /auction. Деньги замораживаются, перебитым возвращаются письмом.")
    return posts


@dataclass
class BidResult:
    blocked: str | None = None
    amount: int = 0
    need: int = 0
    post: str | None = None


async def bid(session: AsyncSession, content: Content, ch: ActiveCharacter, tg: int, name: str, amount: int,
              tz: str) -> BidResult:
    a = await auction(session)
    if a is None or time.time() >= float(a["end"]):
        return BidResult(R_NONE)
    need = min_bid(content, a)
    if amount < need:
        return BidResult(R_LOW, need=need)
    if ch.state.eddies < amount:
        return BidResult(R_EDDIES, need=need)
    year = ch.state.current_year
    apply_effects(ch.state, {"eddies": -amount}, content, random.Random(), year)
    prev_tg, prev_amount = a.get("top_tg"), int(a.get("top_amount", 0) or 0)
    if prev_tg:
        _mail(session, int(prev_tg), f"Аукцион: твою ставку на {q(a['name'])} перебили. Возврат {prev_amount} эдди.",
              eddies=prev_amount)
    a.update({"top_tg": tg, "top_name": name, "top_amount": amount, "bids": int(a.get("bids", 0)) + 1})
    left_h = max(0, int((float(a["end"]) - time.time()) // 3600))
    if left_h < 1:  # a bid in the last hour extends the auction by an hour: no sniping
        a["end"] = time.time() + 3600
    await repo.ws_set(session, AUCTION_KEY, a)
    await save_active(session, ch, tz)
    post = f"🔨 Новая ставка на {q(esc(a['name']))}: {esc(name)} — {amount} эдди. Перебить — /auction."
    return BidResult(None, amount, need, post)


# -- influence -------------------------------------------------------------------------------------------------------------


@dataclass
class InfluenceResult:
    blocked: str | None = None
    text: str = ""
    applied: AppliedEffects | None = None
    price: int = 0


async def influence(
    session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, action: str, arg: str | None,
) -> InfluenceResult:
    cfg = content.balance.economy.influence
    st = ch.state
    rng = random.Random()
    who = loud.who(st.name, st.surname)
    cost = int(cfg.ap.get(action, 0) or 0)  # owner report 25.10: the deeds of influence spent no AP
    if cost and not ap_services.can_spend(ch, cost):
        return InfluenceResult(R_NO_AP)
    res = await _influence(session, content, ch, year, action, arg, cfg, st, rng, who)
    if res.blocked is None:
        if cost:
            ap_services.spend(ch, content, cost)
        await save_active(session, ch, tz)
    return res


async def _influence(session, content, ch, year, action, arg, cfg, st, rng, who) -> InfluenceResult:  # noqa: ANN001, PLR0913
    if action == "war" and arg:
        if arg not in content.available("gangs", year):
            return InfluenceResult(R_GONE)
        if st.eddies < cfg.war_price:
            return InfluenceResult(R_EDDIES, price=cfg.war_price)
        from app.services import worldfx

        wars = dict(await repo.ws_get(session, worldfx.WARS_KEY, {}) or {})
        wars[arg] = max(int(wars.get(arg, 0) or 0), cfg.war_days)
        await repo.ws_set(session, worldfx.WARS_KEY, wars)
        res = InfluenceResult(None, price=cfg.war_price)
        res.applied = apply_effects(st, {"eddies": -cfg.war_price, "heat": 1, "street_cred": 2}, content, rng, year)
        gang = content.gangs[arg].name
        await loud.post(session, ch.id, f"{who} оплачивает войну против банды «{esc(gang)}»: на улицах {cfg.war_days} дней "
                                        "будет жарко.")
        res.text = f"Деньги ушли нужным людям. Против банды «{esc(gang)}» началась война."
    elif action == "silence":
        if st.heat <= 0:
            return InfluenceResult(R_NONE)
        price = cfg.silence_per_heat * st.heat
        if st.eddies < price:
            return InfluenceResult(R_EDDIES, price=price)
        res = InfluenceResult(None, price=price)
        res.applied = apply_effects(st, {"eddies": -price, "heat": -st.heat}, content, rng, year)
        res.text = "Дело теряется в архиве, камеры «ломаются», свидетели всё забывают. Розыск снят."
    elif action == "fixer":
        from app.services import game

        fixer = game.district_fixer(content, st.district, year)
        if fixer is None:
            return InfluenceResult(R_GONE)
        if st.eddies < cfg.fixer_bribe:
            return InfluenceResult(R_EDDIES, price=cfg.fixer_bribe)
        res = InfluenceResult(None, price=cfg.fixer_bribe)
        res.applied = apply_effects(st, {"eddies": -cfg.fixer_bribe, f"rel_fixer:{fixer.key}": cfg.fixer_rel},
                                    content, rng, year)
        res.text = f"{esc(fixer.name)} пересчитывает конверт и улыбается. Заказы станут лучше."
    elif action in ("charity", "pr"):
        price = cfg.charity_price if action == "charity" else cfg.pr_price
        if st.eddies < price:
            return InfluenceResult(R_EDDIES, price=price)
        fx = dict(cfg.charity_effects if action == "charity" else cfg.pr_effects)
        fx["eddies"] = int(fx.get("eddies", 0)) - price
        res = InfluenceResult(None, price=price)
        res.applied = apply_effects(st, fx, content, rng, year)
        if action == "charity":
            await loud.post(session, ch.id, f"{who} устраивает благотворительный вечер: приюты Хейвуда получили деньги, "
                                            "а город — повод поговорить.")
            res.text = "Вечер удался: фотографы, речи, чеки для приютов. О тебе говорят хорошо."
        else:
            res.text = "Статьи, интервью и пара роликов. Город запоминает твоё имя."
    else:
        return InfluenceResult(R_GONE)
    return res


# -- the empire and the megacorps ------------------------------------------------------------------------------------------


async def corps(session: AsyncSession) -> dict[str, dict]:
    return {str(k): dict(v) for k, v in (await repo.ws_get(session, CORPS_KEY, {}) or {}).items()}


async def buy_step(
    session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, corp_name: str | None = None,
) -> tuple[str | None, str]:
    """The next step of the empire. The last one founds a megacorp named `corp_name`. Returns (blocked, step name)."""
    st = ch.state
    steps = content.balance.economy.empire
    n = int(st.flags.get("empire", 0) or 0)
    if n >= len(steps):
        return R_HAVE, ""
    step = steps[n]
    if st.eddies < step.price:
        return R_EDDIES, step.name
    if step.key == "megacorp":
        name = (corp_name or "").strip()
        if not name or len(name) > content.balance.economy.megacorp.name_max_chars:
            return R_NAME, step.name
        all_ = await corps(session)
        all_[str(ch.player_tg_id)] = {"name": name, "integrity": content.balance.economy.megacorp.integrity_max,
                                      "security": 0, "founded": time.time(), "owner_name": f"{st.name} {st.surname}".strip()}
        await repo.ws_set(session, CORPS_KEY, all_)
    apply_effects(st, {"eddies": -step.price, "status": 2, "fame": 1}, content, random.Random(), year)
    st.flags["empire"] = n + 1
    await save_active(session, ch, tz)
    who = loud.who(st.name, st.surname)
    if step.key == "megacorp":
        await loud.post(session, ch.id, f"В Найт-Сити новая мегакорпорация: «{esc(corp_name or '')}». Основатель — {who}. "
                                        "Башня строится, конкуренты точат ножи.")
    else:
        await loud.post(session, ch.id, f"{who}: «{esc(step.name)}». Титул — {esc(step.title)}.")
    return None, step.name


async def sell_step(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str) -> tuple[str | None, str, int]:
    """Owner report 23.10: the top step of the empire can be given up for `empire_sell_pct` of its price."""
    st = ch.state
    steps = content.balance.economy.empire
    n = int(st.flags.get("empire", 0) or 0)
    if n <= 0:
        return R_NONE, "", 0
    step = steps[n - 1]
    back = step.price * content.balance.economy.empire_sell_pct // 100
    if step.key == "megacorp":
        all_ = await corps(session)
        corp = all_.pop(str(ch.player_tg_id), None)
        await repo.ws_set(session, CORPS_KEY, all_)
        if corp is not None:
            from app.services import pcorp

            pcorp.notify_gone(session, corp, "её продали")
            await loud.post(session, ch.id, f"{loud.who(st.name, st.surname)} продаёт корпорацию «{esc(corp['name'])}». "
                                            "Вывеску на башне меняют за ночь.")
    st.flags["empire"] = n - 1
    apply_effects(st, {"eddies": back}, content, random.Random(), year)
    await save_active(session, ch, tz)
    return None, step.name, back


async def upgrade_security(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str) -> tuple[str | None, int]:
    cfg = content.balance.economy.megacorp
    all_ = await corps(session)
    corp = all_.get(str(ch.player_tg_id))
    if corp is None:
        return R_NONE, 0
    lvl = int(corp.get("security", 0) or 0)
    if lvl + 1 >= len(cfg.security_price):
        return R_HAVE, 0
    price = cfg.security_price[lvl + 1]
    if ch.state.eddies < price:
        return R_EDDIES, price
    apply_effects(ch.state, {"eddies": -price}, content, random.Random(), year)
    corp["security"] = lvl + 1
    all_[str(ch.player_tg_id)] = corp
    await repo.ws_set(session, CORPS_KEY, all_)
    await save_active(session, ch, tz)
    return None, price


async def repair(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str) -> tuple[str | None, int]:
    cfg = content.balance.economy.megacorp
    all_ = await corps(session)
    corp = all_.get(str(ch.player_tg_id))
    if corp is None:
        return R_NONE, 0
    missing = cfg.integrity_max - int(corp.get("integrity", 0) or 0)
    if missing <= 0:
        return R_HAVE, 0
    can = min(missing, max(0, ch.state.eddies) // cfg.repair_per_point)
    if can <= 0:
        return R_EDDIES, cfg.repair_per_point
    price = can * cfg.repair_per_point
    apply_effects(ch.state, {"eddies": -price}, content, random.Random(), year)
    corp["integrity"] = int(corp.get("integrity", 0) or 0) + can
    all_[str(ch.player_tg_id)] = corp
    await repo.ws_set(session, CORPS_KEY, all_)
    await save_active(session, ch, tz)
    return None, price


@dataclass
class AttackResult:
    blocked: str | None = None
    ok: bool = False
    damage: int = 0
    razed: bool = False
    corp: str = ""
    check: object = None
    applied: AppliedEffects | None = None


async def attack(
    session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, owner_tg: str, approach: str,
    rng: random.Random,
) -> AttackResult:
    """Hit a player's megacorp: a storm (REF + firearms) or a hack (INT + netrunning) against its security."""
    cfg = content.balance.economy.megacorp
    st = ch.state
    if int(st.flags.get("jail_weeks", 0) or 0) > 0:
        return AttackResult(R_JAIL)
    if str(ch.player_tg_id) == str(owner_tg):
        return AttackResult(R_OWN)
    all_ = await corps(session)
    corp = all_.get(str(owner_tg))
    if corp is None:
        return AttackResult(R_GONE)
    from app.services import pcorp

    if str(ch.player_tg_id) in pcorp.staff_of(corp):  # owner request 24.10: the staff does not storm its own tower
        return AttackResult(R_STAFF)
    hits = dict(await repo.ws_get(session, HITS_KEY, {}) or {})
    hkey = f"{ch.player_tg_id}:{owner_tg}"
    if time.time() - float(hits.get(hkey, 0) or 0) < cfg.attack_cooldown_h * 3600:
        return AttackResult(R_COOLDOWN, corp=corp["name"])
    if not ap_services.can_spend(ch, cfg.attack_ap):
        return AttackResult(R_NO_AP, corp=corp["name"])
    ap_services.spend(ch, content, cfg.attack_ap)
    lvl = int(corp.get("security", 0) or 0)
    dv = cfg.security_dv[min(lvl, len(cfg.security_dv) - 1)] + pcorp.staff_dv(content, corp) + up_sum(content, corp, "sec_dv")
    stat, skill = ("INT", "netrunning") if approach == "hack" else ("REF", "firearms")
    res = AttackResult(corp=corp["name"])
    res.check = checks.roll_check(st, content, rng, stat, skill, dv, ("combat",) if approach != "hack" else ("gig_hack",))
    hits[hkey] = time.time()
    await repo.ws_set(session, HITS_KEY, hits)
    who = loud.who(st.name, st.surname)
    if res.check.ok:
        res.ok = True
        res.damage = max(1, rng.randint(*cfg.attack_damage) * (100 - min(80, up_sum(content, corp, "dmg_cut_pct"))) // 100)
        corp["integrity"] = int(corp.get("integrity", 0) or 0) - res.damage
        res.applied = apply_effects(st, {"eddies": cfg.attack_reward, "heat": cfg.attack_heat, "street_cred": 2,
                                         "fame": 1}, content, rng, year)
        if corp["integrity"] <= 0:
            res.razed = True
            all_.pop(str(owner_tg), None)
            pcorp.notify_gone(session, corp, "башню разнесли")
            _mail(session, int(owner_tg), f"Твоя корпорация «{corp['name']}» разнесена в ноль. Империя откатилась на шаг.",
                  empire_to=len(content.balance.economy.empire) - 1)
            await loud.post(session, ch.id, f"Корпорация «{esc(corp['name'])}» разнесена в ноль. Последний удар: {who}. "
                                            "Башня горит, акции — в пыль.")
        else:
            all_[str(owner_tg)] = corp
            _mail(session, int(owner_tg), f"На «{corp['name']}» напали: целостность −{res.damage} "
                                          f"(осталось {corp['integrity']}). Пора чинить и усиливать охрану.")
            await loud.post(session, ch.id, f"Удар по корпорации «{esc(corp['name'])}»: {who} пробивает охрану, "
                                            f"целостность −{res.damage}.")
        await repo.ws_set(session, CORPS_KEY, all_)
    else:
        res.applied = apply_effects(st, {"hp": -cfg.attack_fail_hp, "heat": 1}, content, rng, year)
    await save_active(session, ch, tz)
    return res


def up_sum(content: Content, corp: dict, field: str) -> int:
    """Owner request 25.10: the sum of a department effect over the corp's levels."""
    have = corp.get("up") or {}
    return sum(int(getattr(u, field, 0) or 0) * int(have.get(u.key, 0) or 0) for u in content.balance.economy.megacorp.upgrades)


async def corp_upgrade(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str,
                       key: str) -> tuple[str | None, int, str]:
    """Buy the next level of a department. Returns (blocked, price, name)."""
    up = next((u for u in content.balance.economy.megacorp.upgrades if u.key == key), None)
    all_ = await corps(session)
    corp = all_.get(str(ch.player_tg_id))
    if corp is None:
        return R_NONE, 0, ""
    if up is None:
        return R_GONE, 0, ""
    have = dict(corp.get("up") or {})
    lvl = int(have.get(key, 0) or 0)
    if lvl >= len(up.prices):
        return R_HAVE, 0, up.name
    price = up.prices[lvl]
    if ch.state.eddies < price:
        return R_EDDIES, price, up.name
    apply_effects(ch.state, {"eddies": -price, "status": 1}, content, random.Random(), year)
    have[key] = lvl + 1
    corp["up"] = have
    all_[str(ch.player_tg_id)] = corp
    await repo.ws_set(session, CORPS_KEY, all_)
    ch.state.flags["corp_up"] = have
    await save_active(session, ch, tz)
    await loud.post(session, ch.id, f"Корпорация «{esc(corp['name'])}» открывает новый отдел: {esc(up.name)}"
                                    + (f" (уровень {lvl + 1})" if lvl else "") + ".")
    return None, price, up.name


async def dissolve_lost(session: AsyncSession, ch: ActiveCharacter) -> str | None:
    """The owner could not pay the upkeep of the megacorp: it is gone from the shared city."""
    if not ch.state.flags.pop("megacorp_lost", None):
        return None
    all_ = await corps(session)
    corp = all_.pop(str(ch.player_tg_id), None)
    await repo.ws_set(session, CORPS_KEY, all_)
    if corp is not None:
        from app.services import pcorp

        pcorp.notify_gone(session, corp, "она обанкротилась")
        await loud.post(session, ch.id, f"Корпорация «{esc(corp['name'])}» обанкротилась: содержать её было нечем.")
        return corp["name"]
    return None
