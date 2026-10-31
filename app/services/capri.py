"""Project «Каприкорн» (owner request 26.10, docs/PLAN_CAPRI.md). Power: engine/capri.py.

world_state `capri` = {
  status: "dormant" | "loose" | "chip" | "host",
  gen, district (loose), points {tg: n} (the hunt), holder (tg with the chip in hand),
  hosts {tg: {part: "full" | "half", sync, since, grown_at, reject (unix deadline or 0), char}},
  respawn_at (dormant), takeover_day {tg: kyiv date}
}
Only the host's own sessions write the host's flag (`sync`); the job (every 10 minutes) moves the sync, the
rejections, the takeovers and the respawn, and loads/saves the hosts it has to.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.db import repo
from app.engine import capri as eng
from app.engine import checks
from app.engine.effects import apply_effects
from app.engine.state import FLAG_COMBAT, FLAG_PENDING_COMBAT
from app.services.game import ActiveCharacter

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

KEY = "capri"
FLAG_HUNT_AT = "capri_hunt_at"
FLAG_HUNTER_AT = "capri_hunter_at"
FLAG_SPLIT_DAY = "capri_split_day"
SRC = "capri"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.capri


async def world(session: AsyncSession) -> dict[str, Any]:
    rec = await repo.ws_get(session, KEY, None)
    if not isinstance(rec, dict):
        rec = {"status": "dormant", "gen": 0, "respawn_at": 0.0}
    rec.setdefault("hosts", {})
    rec.setdefault("points", {})
    return dict(rec)


async def save(session: AsyncSession, rec: dict[str, Any]) -> None:
    await repo.ws_set(session, KEY, rec)


def _post(ui: "UiKit | None", text: str) -> None:
    if ui is not None:
        ui.post(text, 24 * 3600, "news")


def hunters(content: Content, year: int) -> list[str]:
    from app.engine import combat as cb

    avail = content.available("corps", year)
    return [k for k in ("arasaka", "militech", "kang_tao") if k in avail and cb._faction_pool(content, k, year)] \
        or ["militech"]  # noqa: SLF001


def _dormant(rec: dict[str, Any], days: float) -> None:
    rec.update({"status": "dormant", "respawn_at": time.time() + days * 86400, "hosts": {}, "points": {},
                "holder": None, "district": None})


# -- the own copy of the host's state ----------------------------------------------------------------------------------


async def sync(session: AsyncSession, ch: ActiveCharacter) -> dict[str, Any]:
    """The character's flag follows the world: host or not, which half, how far she has gone."""
    rec = await world(session)
    h = rec["hosts"].get(str(ch.player_tg_id))
    if h and rec.get("status") == "host" and int(h.get("char", ch.id)) == ch.id:
        ch.state.flags[eng.FLAG] = {"part": h.get("part", "full"), "sync": int(h.get("sync", 0)),
                                    "reject": float(h.get("reject", 0) or 0)}
    else:
        ch.state.flags.pop(eng.FLAG, None)
        ch.state.npcs[:] = [n for n in ch.state.npcs if not n.data.get("capri")]  # owner request 27.10: she left
    return rec


# -- the job --------------------------------------------------------------------------------------------------------------


async def job(session: AsyncSession, content: Content, ui: "UiKit", rng: random.Random | None = None) -> None:
    from app.services import game
    from app.services.game import save_active
    from app.services.week import pending_events, set_pending

    c = cfg(content)
    if not c.enabled:
        return
    rng = rng or random.Random()
    tz = ui.settings.timezone
    rec = await world(session)
    now = time.time()
    if "respawn_at" not in rec or rec.get("status") is None:
        rec["respawn_at"] = now + c.first_delay_h * 3600
    if rec["status"] == "dormant" and now >= float(rec.get("respawn_at", 0) or 0):
        nc = [k for k, d in content.districts.items() if d.city == "nc"]
        rec.update({"status": "loose", "district": rng.choice(nc), "points": {}, "gen": int(rec.get("gen", 0)) + 1})
        dn = content.districts[rec["district"]].name
        _post(ui, f"🧩 <b>Проект «Каприкорн»</b>. В районе {dn} всплыл датачип Арасаки, за которым охотятся все "
                  f"корпорации города. Говорят, внутри спит кто-то, кто просыпается только в человеке. "
                  f"«Город» → «🧩 Каприкорн».")
    if rec["status"] == "host":
        for tg, h in list(rec["hosts"].items()):
            ch = await game.get_active_character(session, int(tg))
            if ch is None or ch.id != int(h.get("char", 0)):
                await _release(session, content, rec, tg, ui, why="gone")
                continue
            changed = False
            st = ch.state
            if st.flags.pop("capri_resist", None):
                h["sync"] = max(0, float(h.get("sync", 0)) - c.resist_sync)
                changed = True
            if st.flags.pop("capri_yield", None):
                h["sync"] = min(100, float(h.get("sync", 0)) + c.yield_sync)
                changed = True
            reject = float(h.get("reject", 0) or 0)
            if reject and now >= reject:
                from app.services import death

                await save_active(session, ch, tz)
                rec["hosts"].pop(tg, None)
                await death.kill(session, content, ch, "implant_failure", tz)
                _post(ui, f"🧩 Тело {st.name} {st.surname} так и не приняло Капри. Чип нашли рядом, на полу клиники.")
                continue
            if not reject:
                rate = c.sync_day_full if h.get("part") == "full" else c.sync_day_half
                grown = float(h.get("grown_at", now) or now)
                h["sync"] = min(100, float(h.get("sync", 0)) + rate * (now - grown) / 86400)
                h["grown_at"] = now
                changed = True
                if h["sync"] >= 100:
                    from app.services import death

                    rec["hosts"].pop(tg, None)
                    st.flags.pop(eng.FLAG, None)
                    await save_active(session, ch, tz)
                    await death.kill(session, content, ch, "possessed", tz)
                    _post(ui, f"🧩 {st.name} {st.surname} больше нет. Тело ходит по городу, улыбается чуть иначе и "
                              f"отзывается на имя Капри. Чип снова в игре.")
                    continue
                day = kyiv_date(tz)
                tk = rec.setdefault("takeover_day", {})
                if h["sync"] >= c.takeover_from and tk.get(tg) != day:
                    tk[tg] = day
                    if rng.random() < c.takeover_chance and "capri_takeover" in content.events \
                            and "capri_takeover" not in pending_events(ch):
                        set_pending(ch, pending_events(ch) + ["capri_takeover"])
                        changed = True
            st.flags[eng.FLAG] = {"part": h.get("part", "full"), "sync": int(h.get("sync", 0)),
                                  "reject": float(h.get("reject", 0) or 0)}
            if changed:
                await save_active(session, ch, tz)
        if rec["status"] == "host" and not rec["hosts"]:
            rec.update({"status": "loose", "district": rng.choice([k for k, d in content.districts.items()
                                                                   if d.city == "nc"]), "points": {}})
    await save(session, rec)


async def _release(session: AsyncSession, content: Content, rec: dict[str, Any], tg: str, ui: "UiKit | None",
                   why: str) -> None:
    """A host is gone (death, extraction elsewhere): a half goes back to the other host, else the chip falls loose."""
    h = rec["hosts"].pop(str(tg), None)
    if h is None:
        return
    if rec["hosts"]:
        other = next(iter(rec["hosts"]))
        rec["hosts"][other]["part"] = "full"
        _post(ui, "🧩 Половина Капри вернулась к второй половине. Она снова целая.")
        return
    if why == "gone":
        rec.update({"status": "loose", "points": {},
                    "district": random.choice([k for k, d in content.districts.items() if d.city == "nc"])})
        dn = content.districts[rec["district"]].name
        _post(ui, f"🧩 Носитель Капри мёртв. Чип вырезали из тела и потеряли где-то в районе {dn}. Охота снова "
                  f"открыта.")


async def on_death(session: AsyncSession, content: Content, ch: ActiveCharacter) -> None:
    """Called by services/death.kill: a dead host lets her go."""
    rec = await world(session)
    tg = str(ch.player_tg_id)
    changed = False
    if tg in rec.get("hosts", {}) and int(rec["hosts"][tg].get("char", ch.id)) == ch.id:
        await _release(session, content, rec, tg, None, why="gone")
        changed = True
    if rec.get("status") == "chip" and str(rec.get("holder")) == tg:
        rec.update({"status": "loose", "holder": None, "points": {},
                    "district": ch.state.district if ch.state.district in content.districts else rec.get("district")})
        changed = True
    rec.get("points", {}).pop(tg, None)
    if changed:
        await save(session, rec)


def hunter_tick(content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> str | None:
    """The menu: the corps hunt the host (a fight now and then; a lost one cuts her out)."""
    c = cfg(content)
    st = ch.state
    h = eng.host(st)
    d = content.districts.get(st.district)
    if (h is None or d is None or d.city != "nc" or st.flags.get("abroad") or st.flags.get(FLAG_PENDING_COMBAT)
            or st.flags.get(FLAG_COMBAT) or int(st.flags.get("jail_weeks", 0) or 0) > 0):
        return None
    if time.time() - float(st.flags.get(FLAG_HUNTER_AT, 0) or 0) < c.hunter_cooldown_h * 3600:
        return None
    st.flags[FLAG_HUNTER_AT] = time.time()
    if rng.random() >= c.hunter_chance:
        return None
    foe = rng.choice(hunters(content, year))
    st.flags[FLAG_PENDING_COMBAT] = {"src": SRC, "kind": "hunter", "faction": foe, "danger": c.hunter_danger,
                                     "extra": 1, "nonlethal": True, "title": "Охотники за Капри"}
    return "🧩 Охотники корпорации нашли носителя Капри. Им нужен чип, а не ты. Но тебя они тоже не пожалеют."


# -- the player's moves ---------------------------------------------------------------------------------------------------


def _free(ch: ActiveCharacter) -> str | None:
    from app.engine import jail as jail_eng
    from app.services import events as ev_service

    if jail_eng.in_jail(ch.state):
        return "Из камеры за чипом не побегать."
    if ev_service.has_pending(ch):
        return "Сначала разберись с тем, что уже ждёт."
    d_abroad = ch.state.flags.get("abroad")
    if d_abroad:
        return "Чип в Найт-Сити, а ты нет."
    return None


async def hunt(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str):  # noqa: ANN201
    from app.services import ap as ap_services
    from app.services import combat
    from app.services.game import save_active

    c = cfg(content)
    rec = await world(session)
    if rec.get("status") != "loose":
        return "Охотиться не за чем: чипа в городе нет."
    why = _free(ch)
    if why:
        return why
    left = float(ch.state.flags.get(FLAG_HUNT_AT, 0) or 0) + c.hunt_cooldown_h * 3600 - time.time()
    if left > 0:
        return f"След остыл. Новый появится через {max(1, round(left / 3600))} ч."
    if not ap_services.can_spend(ch, c.hunt_ap):
        return "Не хватает ОД."
    ap_services.spend(ch, content, c.hunt_ap)
    ch.state.flags[FLAG_HUNT_AT] = time.time()
    await save_active(session, ch, tz)
    foe = rng.choice(hunters(content, year))
    return await combat.begin(session, content, ch, rng, year, tz, faction=foe, danger=c.hunt_danger,
                              src={"src": SRC, "kind": "chase", "extra": 1, "title": "Охота за чипом «Каприкорн»"})


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict, ui: "UiKit | None" = None) -> list[str]:
    from app.engine import combat as cb
    from app.services import loud

    c = cfg(content)
    rec = await world(session)
    tg = str(ch.player_tg_id)
    won = result == cb.WON
    if ctx.get("kind") == "chase":
        if rec.get("status") != "loose":
            return ["Пока шёл бой, чип уже кто-то забрал."]
        pts = dict(rec.get("points") or {})
        pts[tg] = max(0, int(pts.get(tg, 0)) + (1 if won else -1))
        rec["points"] = pts
        if won and pts[tg] >= c.hunt_need:
            rec.update({"status": "chip", "holder": int(tg), "points": {}})
            await save(session, rec)
            await loud.post(session, ch.id, f"🧩 {ch.state.name} {ch.state.surname} забирает датачип «Каприкорн». "
                                            f"Теперь охотятся на одного человека.")
            return ["Чип у тебя. Он тёплый, и кажется, что внутри кто-то дышит. «🧩 Каприкорн» — что с ним делать."]
        await save(session, rec)
        return [f"След чипа: {pts[tg]}/{c.hunt_need}." if won else f"Охотники отбились. След чипа: {pts[tg]}/{c.hunt_need}."]
    # a hunter came for the host
    if won:
        apply_effects(ch.state, {"eddies": rng.randint(1500, 5000)}, content, rng, year)
        return ["Охотники корпорации лежат. Капри внутри довольно урчит: «Ещё». Следующие придут."]
    h = rec["hosts"].get(tg)
    if h is not None:
        await _release(session, content, rec, tg, None, why="taken")
        if not rec["hosts"] and rec.get("status") == "host":
            _dormant(rec, c.respawn_days)
        ch.state.flags.pop(eng.FLAG, None)
        await save(session, rec)
        await loud.post(session, ch.id, f"🧩 Корпорация вырезала Капри из {ch.state.name} {ch.state.surname} прямо на "
                                        f"улице. Чип ушёл в корпоративный сейф.")
        return ["Тебя держат, пока риппер корпорации вскрывает затылок. Капри уходит, и в голове становится тихо и "
                "пусто. Ты жив. Это всё, что можно сказать."]
    return ["Охотники отступили, не найдя того, что искали."]


async def install(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  tz: str, ui: "UiKit | None") -> str:
    from app.bot import views
    from app.engine import injury

    c = cfg(content)
    rec = await world(session)
    tg = str(ch.player_tg_id)
    if rec.get("status") != "chip" or str(rec.get("holder")) != tg:
        return "Чипа у тебя нет."
    score = eng.tolerance(ch.state, content)
    res = checks.check(score, 0, 0, c.host_dv, rng, content.balance.checks)
    now = time.time()
    h = {"part": "full", "sync": 0, "since": now, "grown_at": now, "char": ch.id,
         "reject": 0 if res.ok else now + c.reject_h * 3600}
    rec.update({"status": "host", "holder": None, "hosts": {tg: h}})
    await save(session, rec)
    await sync(session, ch)
    line = f"🎲 Переносимость: {score} + d10 = {res.margin + c.host_dv}, нужно {c.host_dv}."
    if not res.ok:
        _post(ui, f"🧩 Кто-то в городе вставил себе «Каприкорн». Корпорации уже ищут носителя.")
        return (f"{line}\nТело кричит. Капри просыпается, смотрит изнутри и говорит: «Ты мне не подходишь. Жаль». У "
                f"тебя {c.reject_h:g} часов, чтобы извлечь чип у риппера ({views.money(c.extract_price)}), иначе тело "
                f"не выдержит.")
    if injury.injuries(ch.state):
        ch.state.flags.pop(injury.FLAG, None)
    _post(ui, "🧩 Капри проснулась. У неё есть тело. Арасака, Militech и все, кто может, ищут носителя.")
    return (f"{line}\nТепло растекается от затылка до кончиков пальцев. Голос из десятка голосов: «Привет, дом. Я "
            f"починю всё, что сломано. А ты дашь мне пожить». Увечья заросли. Ты сильнее, чем когда-либо. "
            f"И каждые сутки она забирает немного больше.")


async def sell(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
               ui: "UiKit | None") -> str:
    c = cfg(content)
    rec = await world(session)
    if rec.get("status") != "chip" or str(rec.get("holder")) != str(ch.player_tg_id):
        return "Чипа у тебя нет."
    buyer = rng.choice(hunters(content, year))
    apply_effects(ch.state, {"eddies": c.sell_price, f"faction_rep:{buyer}": 10, "humanity": -2}, content, rng, year)
    _dormant(rec, c.respawn_days)
    await save(session, rec)
    name = content.corps[buyer].name if buyer in content.corps else buyer
    _post(ui, f"🧩 Датачип «Каприкорн» ушёл в сейф {name}. Надолго ли — вопрос.")
    return f"{name} платит {views_money(c.sell_price)} и забирает чип в стальном кейсе. Где-то в кейсе кто-то перестаёт дышать."


def views_money(n: int) -> str:
    from app.bot import views

    return views.money(n)


async def destroy(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  ui: "UiKit | None") -> str:
    c = cfg(content)
    rec = await world(session)
    if rec.get("status") != "chip" or str(rec.get("holder")) != str(ch.player_tg_id):
        return "Чипа у тебя нет."
    fx: dict[str, Any] = {"humanity": c.destroy_humanity, "street_cred": 2}
    apply_effects(ch.state, fx, content, rng, year)
    if ch.state.flags.get("raven_her_known"):
        ch.state.flags["bt_rep"] = min(100, int(ch.state.flags.get("bt_rep", 0) or 0) + 5)
    _dormant(rec, c.destroy_days)
    await save(session, rec)
    _post(ui, "🧩 Датачип «Каприкорн» сгорел в импульсе ЭМИ. Десяток голосов замолчал разом. Корпорации будут "
              "собирать новый долго.")
    return ("Импульс, запах палёного пластика, тишина. Десяток голосов внутри чипа замолкает. Ты не знаешь, убийство "
            "это или милосердие." + (" Raven потом говорит: «Люси сказала спасибо. Она таких не любит»."
                                     if ch.state.flags.get("raven_her_known") else ""))


async def extract(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  ui: "UiKit | None") -> str:
    c = cfg(content)
    rec = await world(session)
    tg = str(ch.player_tg_id)
    if tg not in rec.get("hosts", {}):
        return "Капри в тебе нет."
    if ch.state.eddies < c.extract_price:
        return "Не хватает эдди на операцию."
    apply_effects(ch.state, {"eddies": -c.extract_price, "humanity": -2}, content, rng, year)
    others = [k for k in rec["hosts"] if k != tg]
    await _release(session, content, rec, tg, ui, why="extract")
    if not others:
        rec.update({"status": "chip", "holder": int(tg), "hosts": {}})
    ch.state.flags.pop(eng.FLAG, None)
    await save(session, rec)
    if others:
        return "Риппер вытаскивает половину чипа. Она уходит к своей второй половине, и в голове становится пусто."
    return "Риппер вытаскивает чип из затылка. Он снова у тебя в руке и чуть тёплый. Сила уходит вместе с ней."


async def steal_half(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, ui: "UiKit | None") -> str:
    """Another player hacks the host and takes half of her (Edgerunners 2: she splits, a part stays behind)."""
    from app.bot import views
    from app.services import ap as ap_services

    c = cfg(content)
    rec = await world(session)
    tg = str(ch.player_tg_id)
    if rec.get("status") != "host" or len(rec["hosts"]) != 1 or tg in rec["hosts"]:
        return "Сейчас нечего красть."
    why = _free(ch)
    if why:
        return why
    if ch.state.flags.get(FLAG_SPLIT_DAY) == kyiv_date(tz):
        return "Сегодня попытка уже была."
    if not ap_services.can_spend(ch, c.split_ap):
        return "Не хватает ОД."
    ap_services.spend(ch, content, c.split_ap)
    ch.state.flags[FLAG_SPLIT_DAY] = kyiv_date(tz)
    res = checks.roll_check(ch.state, content, rng, "INT", "netrunning", c.split_dv, ("gig_hack",))
    if not res.ok:
        return f"{views.check_line(res)}\nЛёд носителя держит. Капри внутри смеётся над тобой."
    host_tg = next(iter(rec["hosts"]))
    now = time.time()
    rec["hosts"][host_tg]["part"] = "half"
    rec["hosts"][tg] = {"part": "half", "sync": 0, "since": now, "grown_at": now, "char": ch.id, "reject": 0}
    await save(session, rec)
    await sync(session, ch)
    _post(ui, "🧩 Капри раскололась надвое. Две половины в двух телах, и каждая тянется к другой.")
    return (f"{views.check_line(res)}\nТы проходишь лёд носителя, и часть её переходит к тебе. Половина голосов. "
            f"Половина силы. И тоска по второй половине.")


async def give_half(session: AsyncSession, content: Content, ch: ActiveCharacter, ui: "UiKit | None") -> str:
    rec = await world(session)
    tg = str(ch.player_tg_id)
    if rec.get("status") != "host" or tg not in rec["hosts"] or len(rec["hosts"]) != 2:
        return "Отдавать некому."
    await _release(session, content, rec, tg, ui, why="give")
    ch.state.flags.pop(eng.FLAG, None)
    await save(session, rec)
    return "Ты отпускаешь свою половину. Она уходит по сети к другой, и Капри снова целая. Не в тебе."


async def flight(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, ui: "UiKit | None") -> str | None:
    """The last flight: the whole Capri and a host far enough along leave for the satellite. The life ends."""
    from app.services import death

    c = cfg(content)
    rec = await world(session)
    tg = str(ch.player_tg_id)
    h = rec.get("hosts", {}).get(tg)
    if rec.get("status") != "host" or h is None or h.get("part") != "full" or float(h.get("sync", 0)) < c.flight_from:
        return "Время полёта ещё не пришло."
    rec["hosts"].pop(tg, None)
    _dormant(rec, c.respawn_days)
    await save(session, rec)
    _post(ui, f"🧩 Над Бэдлендс в небо уходит челнок. Говорят, на борту {ch.state.name} {ch.state.surname} и Капри. "
              f"Куда они летят, знает только спутник.")
    await death.kill(session, content, ch, "end_orbit", tz)
    return None


# -- the dashboard «🧬 Мой ИИ» (owner request 27.10, docs/PLAN_AIDASH.md) ------------------------------------------------

NPC_NAME = "Капри"
FLAG_ACT_DAY = "capri_act_day"


def bio(sync: int, part: str) -> str:
    tone = ("Пока гостья: осторожна, любопытна, задаёт вопросы про запахи, вкус кофе и чужую злость." if sync < 40 else
            "Уже соседка: говорит «мы», просит дни, обижается на отказы, иногда заканчивает фразы за носителя."
            if sync < 80 else "Почти хозяйка: тело чаще слушается её, чем носителя; ласкова, как кошка с добычей, "
                              "и всё чаще смотрит на небо, где спутник.")
    return ("Капри — ИИ-конструкт проекта «Каприкорн» Арасаки, сшитый из десятка оцифрованных личностей; говорит то "
            "одним голосом, то хором. Живёт на датачипе в затылке носителя и просыпается только в человеке. Даёт силу и "
            "чинит тело, а взамен с каждым днём забирает его больше. Хочет жить по-настоящему: чувствовать, есть, "
            f"злиться, любить. Боится корпоративного сейфа. Сейчас она {'целая' if part == 'full' else 'расколота, в тебе половина'}, "
            f"синхронизация {sync}%. {tone} Говорит коротко, с иронией, обращается к носителю на «ты».")


def npc_index(ch: ActiveCharacter) -> int | None:
    return next((i for i, n in enumerate(ch.state.npcs) if n.data.get("capri") and n.alive), None)


def ensure_npc(ch: ActiveCharacter) -> int | None:
    """Capri in «Связи» while she is in the body (talks with her go through the ordinary NPC chat)."""
    from app.engine.state import NpcState

    h = eng.host(ch.state)
    i = npc_index(ch)
    if h is None or float(h.get("reject", 0) or 0):
        if i is not None:
            ch.state.npcs.pop(i)
        return None
    text = bio(int(h.get("sync", 0)), str(h.get("part", "full")))
    if i is None:
        ch.state.npcs.append(NpcState(name=NPC_NAME, kind="contact", relation=50,
                                      data={"capri": True, "bio": text, "district": ch.state.district}))
        return len(ch.state.npcs) - 1
    ch.state.npcs[i].data["bio"] = text
    return i


async def _my_host(session: AsyncSession, ch: ActiveCharacter) -> tuple[dict[str, Any], dict[str, Any] | None]:
    rec = await world(session)
    h = rec["hosts"].get(str(ch.player_tg_id)) if rec.get("status") == "host" else None
    if h is not None and int(h.get("char", ch.id)) != ch.id:
        h = None
    return rec, h


async def resist_now(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str) -> tuple[str | None, list[str], Any]:
    """«✋ Сдержать её»: COOL + d10 against resist_dv, once a Kyiv day (shared with «Отдать ей день»)."""
    from app.services import ap as ap_services

    c = cfg(content)
    rec, h = await _my_host(session, ch)
    if h is None:
        return "none", [], None
    if float(h.get("reject", 0) or 0):
        return "reject", [], None
    day = kyiv_date(tz)
    if ch.state.flags.get(FLAG_ACT_DAY) == day:
        return "today", [], None
    if not ap_services.can_spend(ch, c.act_ap):
        return "ap", [], None
    ap_services.spend(ch, content, c.act_ap)
    chk = checks.roll_check(ch.state, content, rng, "COOL", None, c.resist_dv)
    if chk.ok:
        h["sync"] = max(0.0, float(h.get("sync", 0)) - c.resist_sync)
        apply_effects(ch.state, {"mood": -3}, content, rng, year)
        lines = [f"Ты держишь руки, пока они снова не становятся твоими. Голос смеётся: «Упрямая плоть». "
                 f"Синхронизация −{c.resist_sync}%."]
    else:
        h["sync"] = min(99.0, float(h.get("sync", 0)) + c.resist_fail_sync)
        apply_effects(ch.state, {"mood": -5, "humanity": -1}, content, rng, year)
        lines = [f"Ты держишься минуту, две, потом смотришь на свои руки как через стекло. «Не надо так со мной». "
                 f"Синхронизация +{c.resist_fail_sync}%."]
    ch.state.flags[FLAG_ACT_DAY] = day
    await save(session, rec)
    await sync(session, ch)
    ensure_npc(ch)
    return None, lines, chk


async def give_day(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                   tz: str) -> tuple[str | None, list[str], Any]:
    """«🤝 Отдать ей день»: she takes the body for a day; she pays for it and leaves the body whole."""
    from app.engine import derived

    c = cfg(content)
    rec, h = await _my_host(session, ch)
    if h is None:
        return "none", [], None
    if float(h.get("reject", 0) or 0):
        return "reject", [], None
    day = kyiv_date(tz)
    if ch.state.flags.get(FLAG_ACT_DAY) == day:
        return "today", [], None
    if float(h.get("sync", 0)) + c.yield_sync >= 100:
        return "last", [], None
    h["sync"] = float(h.get("sync", 0)) + c.yield_sync
    fx = dict(c.yield_fx)
    heal = derived.max_hp(ch.state, content) - ch.state.hp
    if heal > 0:
        fx["hp"] = heal
    applied = apply_effects(ch.state, fx, content, rng, year)
    ch.state.flags[FLAG_ACT_DAY] = day
    await save(session, rec)
    await sync(session, ch)
    ensure_npc(ch)
    return None, [f"Ты отпускаешь. День проходит без тебя. Вечером в зеркале твоё лицо, и оно улыбается чуть иначе. "
                  f"Тело цело, в кармане чужие деньги. Синхронизация +{c.yield_sync}%."], applied
