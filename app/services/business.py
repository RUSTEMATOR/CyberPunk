"""Own businesses (owner request): several at once, each named by the owner. Buy, upgrade, advertise, hire a guard,
rename, sell. The weekly income and the incidents come with the lived week (`week_hook`). State: engine/biz.py."""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.content.schemas import BusinessKind
from app.engine import biz as be
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

FLAG = be.OLD

BLOCK_HAVE = "have"
BLOCK_NONE = "none"
BLOCK_EDDIES = "eddies"
BLOCK_MAX = "max"
BLOCK_GONE = "gone"
BLOCK_NAME = "name"

INCIDENTS: list[tuple[str, float, str]] = [
    # (key, weight, text with {loss})
    ("racket", 3, "Пришли люди «за защитой». Пришлось заплатить {loss} эдди."),
    ("inspection", 2, "Санинспекция нашла нарушения. Штраф {loss} эдди."),
    ("robbery", 2, "Ночью вскрыли кассу. Пропало {loss} эдди."),
    ("fire", 1, "Пожар в подсобке. Ремонт обошёлся в {loss} эдди, а заведение откатилось на уровень ниже."),
]


def kind_of(content: Content, key: str) -> BusinessKind | None:
    return next((k for k in content.balance.players.business.kinds if k.key == key), None)


def all_of(ch: ActiveCharacter) -> list[dict]:
    return be.list_of(ch.state)


def state_of(ch: ActiveCharacter, biz_id: int | None = None) -> dict | None:
    """One business: by id, or the first one."""
    items = all_of(ch)
    if biz_id is None:
        return items[0] if items else None
    return next((b for b in items if int(b.get("id", 0)) == biz_id), None)


def title(content: Content, b: dict) -> str:
    """«Name» (kind) or just the kind's name when unnamed."""
    kind = kind_of(content, str(b.get("kind", "")))
    kname = kind.name if kind else str(b.get("kind", ""))
    return f"«{b['name']}» ({kname.lower()})" if b.get("name") else kname


RAID_EVENT = "biz_ncpd_raid"
AUDIT_EVENT = "biz_tax_audit"
# owner request 24.10: the trouble a low resource brings (content/events_business2.yaml)
TROUBLES: dict[str, list[str]] = {
    "stock": ["biz_supplier_shady", "biz_stock_empty"],
    "staff": ["biz_staff_theft", "biz_strike", "biz_manager_quits"],
    "cond": ["biz_inspection_hard", "biz_ceiling", "biz_wiring_fire"],
}


def scheme_of(content: Content, b: dict):  # noqa: ANN201
    key = b.get("scheme")
    return next((x for x in content.balance.players.business.schemes if x.key == key), None) if key else None


def schemes_for(content: Content, b: dict) -> list:
    return [x for x in content.balance.players.business.schemes if not x.kinds or b.get("kind") in x.kinds]


def _queue_story(ch: ActiveCharacter, content: Content, b: dict, rng: random.Random, event_id: str | None = None) -> None:
    """Put one story of this business in front of the press's events (at most one business story waits)."""
    from app.engine import biz as biz_engine
    from app.engine.state import FLAG_PENDING_EVENTS

    queue = list(ch.state.flags.get(FLAG_PENDING_EVENTS) or [])
    if any(e in content.events and content.events[e].business is not None for e in queue):
        return
    if event_id is None:
        recent = set(ch.state.flags.get("recent_events") or [])
        pool = [e for e in content.events.values() if e.business is not None and e.id != RAID_EVENT
                and (not e.business or b.get("kind") in e.business) and e.id not in recent]
        if not pool:
            return
        event_id = rng.choices(pool, weights=[max(1, e.weight) for e in pool])[0].id
    if event_id not in content.events:
        return
    fm = dict(ch.state.flags.get(biz_engine.FOCUS) or {})
    fm[event_id] = int(b.get("id", 0))
    ch.state.flags[biz_engine.FOCUS] = fm
    ch.state.flags[FLAG_PENDING_EVENTS] = queue + [event_id]


def upkeep_of(content: Content, ch: ActiveCharacter, kind: BusinessKind, level: int) -> int:
    """A week's upkeep with the overhead of the chain (owner request 23.10)."""
    n = len(all_of(ch))
    return int(kind.upkeep * level * (1 + content.balance.players.business.network_overhead_pct * max(0, n - 1) / 100))


def upgrade_cost(content: Content, kind: BusinessKind, level: int) -> int:
    return kind.price * content.balance.players.business.upgrade_pct * level // 100


@dataclass
class BizResult:
    blocked: str | None = None
    amount: int = 0
    biz_id: int = 0


def _put(ch: ActiveCharacter, b: dict | None, biz_id: int) -> None:
    items = [x for x in all_of(ch) if int(x.get("id", 0)) != biz_id]
    if b is not None:
        b["managed"] = ch.state.weeks_lived  # owner request 24.10: any care for the business counts as management
        items.append(b)
        items.sort(key=lambda x: int(x.get("id", 0)))
    be.save(ch.state, items)


async def buy(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              key: str) -> BizResult:
    cfg = content.balance.players.business
    items = all_of(ch)
    if len(items) >= cfg.max_owned:
        return BizResult(BLOCK_HAVE)
    kind = kind_of(content, key)
    if kind is None:
        return BizResult(BLOCK_GONE)
    if ch.state.eddies < kind.price:
        return BizResult(BLOCK_EDDIES)
    apply_effects(ch.state, {"eddies": -kind.price, "status": 1}, content, rng, year)
    bid = be.next_id(items)
    _put(ch, {"id": bid, "kind": key, "name": "", "level": 1, "invested": kind.price, "ads_until": 0,
              "guard_until": 0, **cfg.ops_start, "managed": ch.state.weeks_lived}, bid)
    await save_active(session, ch, tz)  # the city hears of it when it gets its name (`rename` / `announce`)
    return BizResult(None, kind.price, bid)


async def announce(session: AsyncSession, content: Content, ch: ActiveCharacter, biz_id: int) -> None:
    """The opening in the loud feed, once, under the name the owner gave (or the kind)."""
    from app.bot.texts import esc
    from app.services import loud

    b = state_of(ch, biz_id)
    if b is None or b.get("announced"):
        return
    b["announced"] = True
    _put(ch, b, biz_id)
    await loud.post(session, ch.id, f"Новое заведение в городе: {esc(title(content, b))}. "
                                    f"Владелец: {loud.who(ch.state.name, ch.state.surname)}.")


async def rename(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, biz_id: int,
                 text: str) -> BizResult:
    b = state_of(ch, biz_id)
    if b is None:
        return BizResult(BLOCK_NONE)
    name = be.clean_name(text)
    if len(name) < 2:
        return BizResult(BLOCK_NAME)
    old = title(content, b)
    first = not b.get("announced")
    b["name"] = name
    _put(ch, b, biz_id)
    if first:
        await announce(session, content, ch, biz_id)
    else:
        from app.bot.texts import esc
        from app.services import loud

        await loud.post(session, ch.id, f"{loud.who(ch.state.name, ch.state.surname)} вешает новую вывеску: {esc(old)} "
                                        f"теперь называется «{esc(name)}».")
    await save_active(session, ch, tz)
    return BizResult(None, 0, biz_id)


async def upgrade(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  tz: str, biz_id: int) -> BizResult:
    b = state_of(ch, biz_id)
    if b is None:
        return BizResult(BLOCK_NONE)
    kind = kind_of(content, b["kind"])
    if kind is None:
        return BizResult(BLOCK_GONE)
    if int(b["level"]) >= content.balance.players.business.max_level:
        return BizResult(BLOCK_MAX)
    cost = upgrade_cost(content, kind, int(b["level"]))
    if ch.state.eddies < cost:
        return BizResult(BLOCK_EDDIES, cost)
    apply_effects(ch.state, {"eddies": -cost, "status": 1}, content, rng, year)
    b["level"] = int(b["level"]) + 1
    b["invested"] = int(b.get("invested", 0)) + cost
    _put(ch, b, biz_id)
    await save_active(session, ch, tz)
    return BizResult(None, cost, biz_id)


async def boost(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str, biz_id: int, what: str, terms: int = 1) -> BizResult:
    """`ads` (more income) or `guard` (fewer incidents) for `terms` periods at once (owner request: a custom length).
    Bought on top of a running one, the new weeks are added to the end."""
    cfg = content.balance.players.business
    b = state_of(ch, biz_id)
    if b is None:
        return BizResult(BLOCK_NONE)
    terms = max(1, min(terms, 52))
    cost, weeks = (cfg.ads_cost, cfg.ads_weeks) if what == "ads" else (cfg.guard_cost, cfg.guard_weeks)
    cost, weeks = cost * terms, weeks * terms
    if ch.state.eddies < cost:
        return BizResult(BLOCK_EDDIES, cost)
    apply_effects(ch.state, {"eddies": -cost}, content, rng, year)
    b[f"{what}_until"] = max(int(b.get(f"{what}_until", 0)), ch.state.weeks_lived) + weeks
    _put(ch, b, biz_id)
    await save_active(session, ch, tz)
    return BizResult(None, cost, biz_id)


async def sell(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
               tz: str, biz_id: int) -> BizResult:
    b = state_of(ch, biz_id)
    if b is None:
        return BizResult(BLOCK_NONE)
    amount = int(b.get("invested", 0)) * content.balance.players.business.sell_pct // 100
    apply_effects(ch.state, {"eddies": amount}, content, rng, year)
    _put(ch, None, biz_id)
    await save_active(session, ch, tz)
    return BizResult(None, amount, biz_id)


def week_hook(content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> list[str]:
    """The week's income (or loss) of every business and maybe an incident. Lines for the week report."""
    cfg = content.balance.players.business
    items = all_of(ch)
    if not items:
        return []
    now = ch.state.weeks_lived
    lines: list[str] = []
    summ = dict(ch.state.flags.get("_biz_sum") or {})
    total = 0
    overhead = 1 + cfg.network_overhead_pct * max(0, len(items) - 1) / 100  # a chain costs more to run
    for b in items:
        kind = kind_of(content, b["kind"])
        if kind is None:
            continue
        lvl = int(b["level"])
        mult = rng.uniform(*cfg.swing) * (cfg.ads_mult if int(b.get("ads_until", 0)) >= now else 1.0)
        if int(b.get("boost_until", -1)) >= now:  # owner request: a story's streak of luck or slump
            mult *= cfg.boost_mult
        if int(b.get("slump_until", -1)) >= now:
            mult *= cfg.slump_mult
        mult *= 1 + min(cfg.fame_max, int(b.get("fame", 0))) * cfg.fame_pct / 100
        # owner request 24.10: the business must be run. Stock runs out, staff loses heart, the place wears down.
        o = be.ops(b, cfg.ops_start)
        notes: list[str] = []
        if o["stock"] <= 0:
            mult *= cfg.stock_out_mult
            notes.append("склад пуст")
        fl = cfg.ops_floor  # owner report 25.10: the running of a business was eating the whole income
        mult *= (fl + (1 - fl) * o["staff"] / 100) * (fl + (1 - fl) * o["cond"] / 100)
        if b.get("managed") is None:
            b["managed"] = now
        if now - int(b["managed"]) > cfg.neglect_weeks:
            mult *= 1 - cfg.neglect_pct / 100
            notes.append("без хозяина")
        b["stock"] = max(0, o["stock"] - cfg.stock_use)
        b["staff"] = max(0, o["staff"] - cfg.staff_decay)
        b["cond"] = max(0, o["cond"] - cfg.wear)
        net = int(kind.income * lvl * mult) - int(kind.upkeep * lvl * overhead)
        sch = scheme_of(content, b)
        if sch is not None:  # owner request: a criminal scheme through the business
            net += sch.income * lvl
            if rng.random() < sch.heat_chance:
                apply_effects(ch.state, {"heat": 1}, content, rng, year)
                notes.append("схема засвечена, розыск +1")
            if rng.random() < sch.raid_chance:
                _queue_story(ch, content, b, rng, RAID_EVENT)
        for note in notes:  # owner report 24.10: the press folds the weeks into one summary (services/week._press_lines)
            summ.setdefault("notes", {}).setdefault(note, {})[str(b["id"])] = title(content, b)
        low = [k for k in be.OPS if int(b.get(k, 100)) < cfg.low_mark]
        if low and rng.random() < cfg.trouble_chance:  # a low resource brings its own trouble
            pool = [e for e in TROUBLES[rng.choice(low)] if e in content.events]
            if pool:
                _queue_story(ch, content, b, rng, rng.choice(pool))
        if rng.random() < cfg.story_chance:
            _queue_story(ch, content, b, rng)
        risk = kind.risk * (cfg.guard_risk_mult if int(b.get("guard_until", 0)) >= now else 1.0)
        if rng.random() < risk:
            key, _, text = rng.choices(INCIDENTS, weights=[w for _, w, _ in INCIDENTS])[0]
            loss = int(kind.income * lvl * rng.uniform(*cfg.incident_loss))
            net -= loss
            summ["inc"] = int(summ.get("inc", 0)) + 1
            summ["inc_loss"] = int(summ.get("inc_loss", 0)) + loss
            if len(lines) < 1:
                lines.append(f"{title(content, b)}: " + text.format(loss=loss))
            if key == "fire" and lvl > 1:
                b["level"] = lvl - 1
        total += net
    be.save(ch.state, items)
    if total > cfg.audit_from and rng.random() < cfg.audit_chance and items:  # owner request 24.10: big money draws eyes
        _queue_story(ch, content, rng.choice(items), rng, AUDIT_EVENT)
    ch.state.flags["_biz_net"] = int(ch.state.flags.get("_biz_net", 0) or 0) + total  # the press sums the weeks
    summ["weeks"] = int(summ.get("weeks", 0)) + 1
    summ["n"] = len(items)
    ch.state.flags["_biz_sum"] = summ
    apply_effects(ch.state, {"eddies": total}, content, rng, year)
    return lines


# -- schemes and visitors (owner request: a business is a place where things happen) ----------------------------------


def manage_cost(content: Content, b: dict, what: str) -> int:
    """Owner request 24.10: the price of a restock, a staff bonus or a repair for this business."""
    cfg = content.balance.players.business
    kind = kind_of(content, b["kind"])
    if kind is None:
        return 0
    lvl = int(b.get("level", 1))
    if what == "stock":
        return max(50, kind.income * lvl * cfg.restock_pct // 100)
    if what == "staff":
        return max(50, kind.upkeep * lvl * cfg.bonus_pct // 100)
    return max(100, kind.price * lvl * cfg.repair_pct // 100)


async def manage(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                 tz: str, biz_id: int, what: str) -> BizResult:
    cfg = content.balance.players.business
    b = state_of(ch, biz_id)
    if b is None or what not in be.OPS:
        return BizResult(BLOCK_GONE)
    o = be.ops(b, cfg.ops_start)
    if o[what] >= 100:
        return BizResult(BLOCK_MAX)
    cost = manage_cost(content, b, what)
    if ch.state.eddies < cost:
        return BizResult(BLOCK_EDDIES)
    gain = {"stock": cfg.restock_gain, "staff": cfg.bonus_gain, "cond": cfg.repair_gain}[what]
    apply_effects(ch.state, {"eddies": -cost}, content, rng, year)
    b.update(o)
    b[what] = min(100, o[what] + gain)
    b["managed"] = ch.state.weeks_lived
    _put(ch, b, biz_id)
    await save_active(session, ch, tz)
    return BizResult(None, cost, biz_id)


TOP_UP_BELOW = 60  # owner report 24.10: «Обслужить все» tops up every resource under this


def manage_all_plan(content: Content, ch: ActiveCharacter) -> list[tuple[int, str, int]]:
    """(business id, resource, cost) of every step «Обслужить все» would buy, the emptiest first."""
    cfg = content.balance.players.business
    gain = {"stock": cfg.restock_gain, "staff": cfg.bonus_gain, "cond": cfg.repair_gain}
    plan = []
    for b in all_of(ch):
        o = be.ops(b, cfg.ops_start)
        for what in be.OPS:
            v = o[what]
            while v < TOP_UP_BELOW and gain[what] > 0:  # as many steps as it takes to reach the mark
                plan.append((v, int(b["id"]), what, manage_cost(content, b, what)))
                v += gain[what]
    plan.sort()
    return [(bid, what, cost) for _, bid, what, cost in plan]


async def manage_all(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str) -> tuple[int, int, int]:
    """Buy every step of the plan the money allows. Returns (steps done, steps left, eddies spent)."""
    cfg = content.balance.players.business
    gain = {"stock": cfg.restock_gain, "staff": cfg.bonus_gain, "cond": cfg.repair_gain}
    plan = manage_all_plan(content, ch)
    done = spent = 0
    for bid, what, cost in plan:
        b = state_of(ch, bid)
        if b is None or ch.state.eddies < cost:
            continue
        o = be.ops(b, cfg.ops_start)
        apply_effects(ch.state, {"eddies": -cost}, content, rng, year)
        b.update(o)
        b[what] = min(100, o[what] + gain[what])
        b["managed"] = ch.state.weeks_lived
        _put(ch, b, bid)
        done += 1
        spent += cost
    await save_active(session, ch, tz)
    return done, len(plan) - done, spent


async def set_scheme(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, biz_id: int, key: str | None) -> BizResult:
    """Start a scheme (pays its price once) or stop the current one (key None)."""
    b = state_of(ch, biz_id)
    if b is None:
        return BizResult(BLOCK_NONE)
    if key is None:
        b.pop("scheme", None)
        _put(ch, b, biz_id)
        await save_active(session, ch, tz)
        return BizResult(None, 0, biz_id)
    sch = next((x for x in schemes_for(content, b) if x.key == key), None)
    if sch is None:
        return BizResult(BLOCK_GONE)
    if ch.state.eddies < sch.price:
        return BizResult(BLOCK_EDDIES, sch.price)
    apply_effects(ch.state, {"eddies": -sch.price}, content, rng, year)
    b["scheme"] = key
    _put(ch, b, biz_id)
    await save_active(session, ch, tz)
    return BizResult(None, sch.price, biz_id)


def _today() -> str:
    from app.clock import utcnow

    return utcnow().date().isoformat()


@dataclass
class Visitor:
    idx: int
    key: str
    role: str
    name: str
    money: int
    criminal: bool
    rowdy: bool
    used: bool


def visitors(content: Content, ch: ActiveCharacter, b: dict) -> list[Visitor]:
    """Who sits in the business today: the same for the whole real day (a seed of the business and the date)."""
    import zlib

    cfg = content.balance.players.business
    pool = cfg.visitors.get(str(b.get("kind", ""))) or []
    if not pool:
        return []
    day = _today()
    r = random.Random(zlib.crc32(f"{ch.id}:{b.get('id')}:{day}".encode()))
    used = set((b.get("visit") or {}).get("used", [])) if (b.get("visit") or {}).get("d") == day else set()
    out: list[Visitor] = []
    for i in range(cfg.visitors_per_day):
        v = r.choice(pool)
        first = r.choice(list(content.given_names))
        g = first.gender if first.gender in ("m", "f") else r.choice(("m", "f"))
        surname = r.choice(list(content.surnames)).form(g)
        out.append(Visitor(i, v.key, v.name, f"{first.name} {surname}", r.randint(*v.money), v.criminal, v.rowdy,
                           i in used))
    return out


VISIT_ACTIONS = ("treat", "listen", "kick", "deal", "rob")


@dataclass
class VisitResult:
    blocked: str | None = None
    ok: bool = True
    check: object | None = None
    text: str = ""
    applied: object | None = None
    lines: list[str] = None  # type: ignore[assignment]


async def visit(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                biz_id: int, idx: int, action: str) -> VisitResult:
    """One deed with a visitor: once per visitor and real day, `visit_ap` AP."""
    from app.engine import checks
    from app.services import ap as ap_services

    cfg = content.balance.players.business
    b = state_of(ch, biz_id)
    if b is None:
        return VisitResult(blocked=BLOCK_NONE)
    vs = visitors(content, ch, b)
    v = next((x for x in vs if x.idx == idx), None)
    if v is None or action not in VISIT_ACTIONS:
        return VisitResult(blocked=BLOCK_GONE)
    if v.used:
        return VisitResult(blocked="used")
    if (action == "kick" and not v.rowdy) or (action == "deal" and not v.criminal):
        return VisitResult(blocked=BLOCK_GONE)
    if not ap_services.can_spend(ch, cfg.visit_ap):
        return VisitResult(blocked="ap")
    if action == "treat" and ch.state.eddies < cfg.treat_price:
        return VisitResult(blocked=BLOCK_EDDIES)
    ap_services.spend(ch, content, cfg.visit_ap)
    res = VisitResult(lines=[])
    fx: dict = {}
    nm = title(content, b)
    if action == "treat":
        fx = {"eddies": -cfg.treat_price, "mood": 2}
        b["fame"] = min(cfg.fame_max, int(b.get("fame", 0)) + 1)
        res.text = f"{v.name} поднимает стакан за хозяина. Завтра расскажет друзьям, что в {nm} наливают от души."
        res.lines.append(f"Слава заведения: {b['fame']}/{cfg.fame_max} (доход +{b['fame'] * cfg.fame_pct}%).")
    elif action == "listen":
        chk = checks.roll_check(ch.state, content, rng, "INT", "persuasion", 13, ("social",))
        res.ok, res.check = chk.ok, chk
        if chk.ok:
            fx = {"eddies": rng.randint(200, 800)}
            res.text = f"{v.name} ({v.role}) слишком громко обсуждает чужие дела. Информацию удаётся продать."
            from app.services import city_life

            res.lines.extend(await city_life.rumors(session, content, ch, ch.state.district, rng))
        else:
            res.text = f"{v.name} замечает, что ты греешь уши, и замолкает."
    elif action == "kick":
        chk = checks.roll_check(ch.state, content, rng, "BODY", "melee", 13, ("combat",))
        res.ok, res.check = chk.ok, chk
        if chk.ok:
            fx = {"mood": 3, "street_cred": 1}
            res.text = f"{v.name} вылетает на улицу вместе с дверью. Остальные гости аплодируют."
        else:
            fx = {"hp": -6}
            res.text = f"{v.name} оказывается крепче, чем выглядел. Разбитая губа, разбитая посуда."
    elif action == "deal":
        chk = checks.roll_check(ch.state, content, rng, "COOL", "persuasion", 14, ("gig_talk",))
        res.ok, res.check = chk.ok, chk
        if chk.ok:
            fx = {"eddies": rng.randint(500, 2000), "heat": 1}
            res.text = f"{v.name} ({v.role}) кивает: дело проведут через твою подсобку, доля твоя."
        else:
            fx = {"heat": 1}
            res.text = f"{v.name} смеётся тебе в лицо и уходит. Через час у входа стоит патрульная машина."
    elif action == "rob":
        chk = checks.roll_check(ch.state, content, rng, "REF", "stealth", 14, ("crime",))
        res.ok, res.check = chk.ok, chk
        if chk.ok:
            fx = {"eddies": v.money}
            res.text = f"Пока {v.name} смотрит на сцену, кошелёк меняет хозяина."
        else:
            fx = {"heat": 1}
            b["slump_until"] = max(int(b.get("slump_until", 0)), ch.state.weeks_lived) + 2
            res.text = f"{v.name} ловит твою руку и орёт на весь зал. Про {nm} пойдут нехорошие слухи."
            res.lines.append(f"{nm}: спад, 2 нед.")
    day = _today()
    vis = dict(b.get("visit") or {})
    if vis.get("d") != day:
        vis = {"d": day, "used": []}
    vis["used"] = list(vis.get("used", [])) + [idx]
    b["visit"] = vis
    _put(ch, b, biz_id)
    if fx:
        res.applied = apply_effects(ch.state, fx, content, rng, year)
    await save_active(session, ch, tz)
    return res
