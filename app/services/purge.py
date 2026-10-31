"""«Судная ночь» (owner request, after the film The Purge): once a week of real time all laws are off for a few hours.

Before the night (from `announce_hours` before the start) a character prepares: buys defenses for the home and medkits,
and chooses: hide at home or go hunting. During the night:
- a hunter has `hunts` deeds: the street (kills a real city NPC of the district and loots them, no heat), a storm of a
  hiding player's shelter (their defense against the hunter), a duel with another hunter;
- a hiding player may shoot back (more defense) and use medkits.
At dawn the gangs test every shelter; the unprepared may be caught outside; the hunter of the night and the strongest
fortress are rewarded; the group gets the summary. The losses of an absent player come by mail and never kill.

world_state `purge` = {id, start, end, status: soon | night | done, roster: {char_id: {...}}, log: [str]}
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import Character, PlayerMail
from app.engine import derived
from app.engine.effects import AppliedEffects, apply_effects
from app.services import followups, game
from app.services.game import ActiveCharacter, save_active

KEY = "purge"
SOON, NIGHT, DONE = "soon", "night", "done"
HIDE, HUNT = "hide", "hunt"
R_CLOSED, R_MODE, R_EDDIES, R_HAVE, R_NONE, R_SPENT, R_DONE, R_TARGET = (
    "closed", "mode", "eddies", "have", "none", "spent", "done", "target")
R_NOPAY, R_BUSY, R_ABROAD = "nopay", "busy", "abroad"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.purge


def window(content: Content, now: datetime) -> tuple[datetime, datetime]:
    """(start, end) of this week's night, or of next week's when this one is over. `now` is aware (Kyiv)."""
    c = cfg(content)
    day = now.date() + timedelta(days=(c.weekday - now.weekday()) % 7)
    start = datetime(day.year, day.month, day.day, c.start_hour, tzinfo=now.tzinfo)
    end = start + timedelta(hours=c.hours)
    if day == now.date() and now >= end:
        start += timedelta(days=7)
        end += timedelta(days=7)
    return start, end


def _now(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


async def state(session: AsyncSession) -> dict:
    got = await repo.ws_get(session, KEY, {})
    return dict(got) if isinstance(got, dict) else {}


async def _save(session: AsyncSession, st: dict) -> None:
    await repo.ws_set(session, KEY, st)


async def is_night(session: AsyncSession) -> bool:
    return (await state(session)).get("status") == NIGHT


def _rec(st: dict, ch: ActiveCharacter) -> dict:
    roster = st.setdefault("roster", {})
    rec = roster.setdefault(str(ch.id), {"mode": None, "defs": [], "medkits": 0, "hunts": 0, "shots": 0, "dmg": 0,
                                         "kills": 0, "loot": 0, "storms_in": 0, "broken": 0, "targets": []})
    rec["name"] = f"{ch.state.name} {ch.state.surname}".strip()
    rec["tg"] = ch.player_tg_id
    return rec


def defense(content: Content, rec: dict) -> int:
    c = cfg(content)
    by_key = {d.key: d.defense for d in c.defenses}
    total = c.base_defense + sum(by_key.get(k, 0) for k in rec.get("defs", [])) + c.shoot_back * int(rec.get("shots", 0))
    total += int(rec.get("hub_def", 0) or 0) + int(rec.get("together", 0) or 0)  # owner request 23.10: shared shelters
    return max(0, total - int(rec.get("dmg", 0)))


def ev_has_pending(ch: ActiveCharacter) -> bool:
    from app.services import events as ev_service

    return bool(ev_service.has_pending(ch))


def _mail(session: AsyncSession, tg: int, char_id: int, text: str, **payload: int) -> None:
    session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name="Судная ночь", kind="city", to_char_id=char_id,
                           payload={"text": text, **{k: v for k, v in payload.items() if v}}))


# -- the clock -----------------------------------------------------------------------------------------------------------


async def tick(session: AsyncSession, content: Content, tz: str, rng: random.Random | None = None) -> list[str]:
    """Open, start and close the night on time. Returns the posts for the group."""
    rng = rng or random.Random()
    now = _now(tz)
    start, end = window(content, now)
    st = await state(session)
    posts: list[str] = []
    night_id = start.date().isoformat()
    if st.get("id") != night_id and st.get("status") != NIGHT and start - timedelta(hours=cfg(content).announce_hours) <= now < end:
        st = {"id": night_id, "start": start.isoformat(), "end": end.isoformat(), "status": SOON, "roster": {}, "log": []}
        await _save(session, st)
        posts.append(announce_text(content, start))
    if st.get("id") == night_id and st.get("status") in (SOON, None) and start <= now < end:
        st["status"] = NIGHT
        await _save(session, st)
        posts.append(SIREN)
    if st.get("status") == NIGHT and now >= datetime.fromisoformat(str(st.get("end"))):
        posts.append(await dawn(session, content, st, rng))
    elif st.get("status") == NIGHT:
        last = st.get("wave_at")
        if not last or now - datetime.fromisoformat(str(last)) >= timedelta(minutes=cfg(content).wave_minutes):
            st["wave_at"] = now.isoformat()
            post = await _wave(session, content, st, rng)
            await _save(session, st)
            if post:
                posts.append(post)
    return posts


# -- the NPC waves (owner request 24.10: the city hunts too, not only the players) ----------------------------------------


OUT = "out"


def raider(content: Content, key: str):  # noqa: ANN201
    return next((r for r in cfg(content).raiders if r.key == key), None)


def mark(content: Content, key: str):  # noqa: ANN201
    return next((m for m in cfg(content).marks if m.key == key), None)


def _who(rec: dict) -> str:
    return str(rec.get("mode") or OUT)


def _auto(session: AsyncSession, content: Content, st: dict, cid: str, rec: dict, rng: random.Random) -> None:
    """A visit nobody answered: the shelter meets it as it is, a hunter or a passer-by takes the hit."""
    th = rec.pop("threat", None)
    r = raider(content, str((th or {}).get("key", "")))
    if r is None:
        return
    c = cfg(content)
    if _who(rec) == HIDE and r.power + rng.randint(1, 10) <= defense(content, rec) + rng.randint(1, 10):
        st.setdefault("log", []).append(f"{rec.get('name')} — дверь выстояла ({r.name})")
        return
    if _who(rec) == HIDE:
        rec["dmg"] = int(rec.get("dmg", 0)) + c.threat_def_loss
    _mail(session, rec["tg"], int(cid), f"Судная ночь: гости ({r.name}) застают тебя врасплох. Деньги и кровь.",
          eddies=-1, hp=-rng.randint(*c.threat_hp), pct=c.threat_take_pct)
    st.setdefault("log", []).append(f"{rec.get('name')} — застали врасплох ({r.name})")


async def _wave(session: AsyncSession, content: Content, st: dict, rng: random.Random) -> str:
    c = cfg(content)
    if not c.raiders:
        return ""
    roster = st.setdefault("roster", {})
    for cid, rec in roster.items():
        if rec.get("threat"):
            _auto(session, content, st, cid, rec, rng)
    res = await session.execute(select(Character).where(Character.alive.is_(True)))
    rows = list(res.scalars())
    away = {str(row.id) for row in rows if (row.flags or {}).get("abroad")}  # owner request 25.10: the Purge is Night City's
    for row in rows:
        if str(row.id) in away:
            continue
        if str(row.id) not in roster:  # nobody chose anything yet: the night comes to them too
            if rng.random() >= c.wave_chance_out:
                continue
            roster[str(row.id)] = {"mode": None, "defs": [], "medkits": 0, "hunts": 0, "shots": 0, "dmg": 0, "kills": 0,
                                   "loot": 0, "storms_in": 0, "broken": 0, "targets": [],
                                   "name": f"{row.name} {row.surname}".strip(), "tg": row.player_tg_id, "fresh": True}
    hit: list[str] = []
    for cid, rec in roster.items():
        fresh = rec.pop("fresh", False)
        if cid in away:
            continue
        who = _who(rec)
        if not fresh and rng.random() >= (c.wave_chance if who != OUT else c.wave_chance_out):
            continue
        pool = [r for r in c.raiders if who in r.targets]
        if not pool:
            continue
        r = rng.choice(pool)
        rec["threat"] = {"key": r.key}
        hit.append(f"{rec.get('name')} ({r.name})")
    if not hit:
        return ""
    return ("🚨 <b>Волна ночи.</b> Банды выходят на улицы. К дверям идут: " + ", ".join(hit) + ".\n"
            f"/purge — отбиться, откупиться или уйти. Кто не ответит за {c.wave_minutes} мин, встретит их как есть.")


THREAT_ACTS = ("t_hold", "t_pay", "t_fight", "t_run")


async def _fight_faction(content: Content, want: str, year: int) -> str:
    from app.engine import combat as cb

    for f in (want, "maelstrom", "scavengers", "tyger_claws", "valentinos", "ncpd"):
        if cb._faction_pool(content, f, year):  # noqa: SLF001
            return f
    return "ncpd"


async def _begin_fight(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                       tz: str, st: dict, r, kind: str, res: NightResult) -> None:  # noqa: ANN001
    from app.services import combat

    ch.state.flags["purge_back"] = st.get("id")
    await save_active(session, ch, tz)
    faction = await _fight_faction(content, r.faction, year)
    res.fight = await combat.begin(session, content, ch, rng, year, tz, faction=faction, danger=r.danger,
                                   src={"src": "purge", "purge": st.get("id"), "kind": kind, "raider": r.key})
    res.ok = True


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb

    st = await state(session)
    r = raider(content, str(ctx.get("raider", "")))
    if st.get("id") != ctx.get("purge") or r is None:
        return []
    rec = _rec(st, ch)
    if result != cb.WON:
        st.setdefault("log", []).append(f"{rec['name']} — бой проигран ({r.name})")
        await _save(session, st)
        return [f"Судная ночь: бой с гостями ({r.name}) проигран."]
    loot = rng.randint(*r.loot)
    rec["kills"] = int(rec.get("kills", 0)) + 1
    rec["loot"] = int(rec.get("loot", 0)) + loot
    st.setdefault("log", []).append(f"{rec['name']} — победа в бою ({r.name})")
    apply_effects(ch.state, {"eddies": loot}, content, rng, year)
    await save_active(session, ch, tz)
    await _save(session, st)
    return [f"Судная ночь: победа над гостями ({r.name}). С тел и машин: +{loot} эдди."]


async def threat_act(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, st: dict, rec: dict, what: str, res: NightResult) -> NightResult:
    th = rec.get("threat") or {}
    r = raider(content, str(th.get("key", "")))
    if r is None:
        rec.pop("threat", None)
        await _save(session, st)
        return NightResult(blocked=R_NONE)
    c = cfg(content)
    who = _who(rec)
    me = rec["name"]
    if what == "t_pay":
        if r.pay >= 99999:
            return NightResult(blocked=R_NOPAY)
        if ch.state.eddies < r.pay:
            return NightResult(blocked=R_EDDIES)
        rec.pop("threat", None)
        await _hit_self(session, content, ch, rng, year, tz, {"eddies": -r.pay}, res)
        res.ok, res.text = True, "Деньги уходят в щель под дверью. Гости пересчитывают и уходят к соседям."
    elif what == "t_fight":
        from app.services import events as ev_service

        if ev_service.has_pending(ch):
            return NightResult(blocked=R_BUSY)
        rec.pop("threat", None)
        await _save(session, st)
        await _begin_fight(session, content, ch, rng, year, tz, st, r, "threat", res)
        res.text = f"{r.name}. {r.text}"
        return res
    elif what == "t_hold" and who != HUNT:
        rec.pop("threat", None)
        mine = defense(content, rec) + derived.stat_total(ch.state, content, "BODY") + rng.randint(1, 10)
        theirs = r.power + rng.randint(1, 10)
        if mine >= theirs:
            loot = rng.randint(*r.loot) // 3
            rec["kills"] = int(rec.get("kills", 0)) + 1
            st.setdefault("log", []).append(f"{me} — дверь выстояла ({r.name})")
            await _hit_self(session, content, ch, rng, year, tz, {"eddies": loot}, res)
            res.ok = True
            res.text = f"Дверь держится ({mine} против {theirs}). Гости отступают, бросив раненых и добычу. +{loot} эдди."
        else:
            rec["dmg"] = int(rec.get("dmg", 0)) + c.threat_def_loss
            take = max(0, ch.state.eddies) * c.threat_take_pct // 100
            st.setdefault("log", []).append(f"{me} — дверь сломана ({r.name})")
            await _hit_self(session, content, ch, rng, year, tz, {"hp": -rng.randint(*c.threat_hp), "eddies": -take}, res)
            res.text = f"Дверь не выдерживает ({mine} против {theirs}). Гости выносят {take} эдди и оставляют тебя на полу."
    elif what == "t_run" and who != HIDE:
        from app.engine import checks

        rec.pop("threat", None)
        chk = checks.roll_check(ch.state, content, rng, "REF", "stealth", c.escape_dv, ())
        if chk.ok:
            res.ok, res.text = True, "Ты уходишь дворами и крышами. След потерян."
        else:
            await _hit_self(session, content, ch, rng, year, tz, {"hp": -rng.randint(*c.threat_hp)}, res)
            res.text = "В переулке тебя настигают. Удаётся вырваться, но не целиком."
    else:
        return NightResult(blocked=R_MODE)
    if ch.state.hp <= 0 and not res.died:
        ch.state.hp = 1
    await _save(session, st)
    return res


async def force_start(session: AsyncSession, content: Content, tz: str, minutes: int = 60) -> dict:
    """Admin: a night right now (tests, a special occasion)."""
    now = _now(tz)
    st = {"id": now.strftime("%Y-%m-%dT%H%M"), "start": now.isoformat(), "end": (now + timedelta(minutes=minutes)).isoformat(),
          "status": NIGHT, "roster": {}, "log": []}
    await _save(session, st)
    return st


ANNOUNCE = ("🚨 <b>Сегодня Судная ночь.</b> С {start} до {end} законов нет: NCPD не приедет, розыск не растёт, "
            "убийство — не преступление.\n"
            "Готовься: /purge — укрепить жильё и спрятаться или выйти на охоту. Кто не решит — встретит рассвет на улице.")
SIREN = ("🚨 <b>Сирена. Судная ночь началась.</b> Все законы отменены до рассвета. Двери заперты, охотники вышли. /purge")


def announce_text(content: Content, start: datetime) -> str:
    end = start + timedelta(hours=cfg(content).hours)
    return ANNOUNCE.format(start=start.strftime("%H:%M"), end=end.strftime("%H:%M"))


# -- preparation ---------------------------------------------------------------------------------------------------------


async def prepare(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                  what: str, key: str = "") -> str | None:
    """Buy a defense or a medkit, or choose the mode. None = done."""
    if ch.state.flags.get("abroad"):
        return R_ABROAD
    st = await state(session)
    if st.get("status") not in (SOON, NIGHT):
        return R_CLOSED
    rec = _rec(st, ch)
    c = cfg(content)
    if what == "def":
        d = next((x for x in c.defenses if x.key == key), None)
        if d is None:
            return R_NONE
        if key in rec["defs"]:
            return R_HAVE
        if ch.state.eddies < d.price:
            return R_EDDIES
        apply_effects(ch.state, {"eddies": -d.price}, content, rng, year)
        rec["defs"].append(key)
    elif what == "medkit":
        if ch.state.eddies < c.medkit_price:
            return R_EDDIES
        apply_effects(ch.state, {"eddies": -c.medkit_price}, content, rng, year)
        rec["medkits"] = int(rec.get("medkits", 0)) + 1
    elif what == "mode" and key in (HIDE, HUNT):
        rec["mode"] = key
    elif what == "shelter":  # owner request 23.10: the shared home, the crew headquarters, the megacorp tower
        from app.services import hubs

        if not key:
            rec["hub"], rec["hub_def"], rec["hub_name"] = "", 0, ""
        else:
            sh = next((x for x in await hubs.shelters(session, content, ch) if x.id == key), None)
            if sh is None:
                return R_NONE
            rec["hub"], rec["hub_def"], rec["hub_name"] = sh.id, sh.defense, sh.name
            rec["mode"] = HIDE
    else:
        return R_NONE
    await save_active(session, ch, tz)
    await _save(session, st)
    return None


@dataclass
class NightResult:
    blocked: str | None = None
    ok: bool = False
    text: str = ""
    applied: AppliedEffects | None = None
    lines: list[str] = field(default_factory=list)
    died: bool = False
    death_cause: str | None = None
    post: str = ""
    fight: object | None = None
    event: str = ""


async def _hit_self(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                    tz: str, fx: dict, res: NightResult) -> None:
    res.applied = apply_effects(ch.state, {k: v for k, v in fx.items() if v}, content, rng, year)
    fu = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz, cause="combat")
    res.lines, res.died, res.death_cause = fu.lines, fu.died, fu.cause
    if not fu.died:
        await save_active(session, ch, tz)


def _power(stt, content: Content) -> int:  # noqa: ANN001
    return derived.stat_total(stt, content, "REF") + derived.skill_total(stt, content, "firearms")


async def act(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              what: str, target: int = 0, key: str = "") -> NightResult:
    if ch.state.flags.get("abroad"):
        return NightResult(blocked=R_ABROAD)
    st = await state(session)
    if st.get("status") != NIGHT:
        return NightResult(blocked=R_CLOSED)
    rec = _rec(st, ch)
    c = cfg(content)
    res = NightResult()
    if what in THREAT_ACTS:
        return await threat_act(session, content, ch, rng, year, tz, st, rec, what, res)
    if what == "medkit":
        if int(rec.get("medkits", 0)) <= 0:
            return NightResult(blocked=R_NONE)
        rec["medkits"] = int(rec["medkits"]) - 1
        await _hit_self(session, content, ch, rng, year, tz, {"hp": c.medkit_hp}, res)
        res.ok, res.text = True, "Бинт, стимулятор, глоток воды. Можно жить дальше."
        await _save(session, st)
        return res
    if what == "shoot":
        if rec.get("mode") != HIDE:
            return NightResult(blocked=R_MODE)
        if int(rec.get("shots", 0)) >= c.shoot_back_times:
            return NightResult(blocked=R_SPENT)
        rec["shots"] = int(rec.get("shots", 0)) + 1
        res.ok, res.text = True, "Ты стреляешь в темноту за окном. Тени отступают от двери. Оборона крепче."
        await _save(session, st)
        return res
    if rec.get("mode") != HUNT:
        return NightResult(blocked=R_MODE)
    if int(rec.get("hunts", 0)) >= c.hunts:
        return NightResult(blocked=R_SPENT)
    rec["hunts"] = int(rec.get("hunts", 0)) + 1
    me = rec["name"]
    if what in ("street", "raid") and ev_has_pending(ch):
        rec["hunts"] = int(rec["hunts"]) - 1
        return NightResult(blocked=R_BUSY)
    if what == "street":  # owner request 24.10: the street is a table of meetings, not one resident
        w = {k: v for k, v in c.street_weights.items() if v > 0} or {"npc": 1}
        kind = rng.choices(list(w), weights=list(w.values()))[0]
        if kind == "fight" and c.raiders:
            r = rng.choice([x for x in c.raiders if "hunt" in x.targets] or c.raiders)
            await _save(session, st)
            await _begin_fight(session, content, ch, rng, year, tz, st, r, "street", res)
            res.text = f"За углом: {r.name}. {r.text}"
            return res
        if kind == "event":
            from app.services.week import pending_events, set_pending

            seen = rec.setdefault("evs", [])
            pool = [e for e in c.street_events if e in content.events and e not in seen]
            if pool:
                ev = rng.choice(pool)
                seen.append(ev)
                ch.state.flags["purge_back"] = st.get("id")
                set_pending(ch, [ev] + [e for e in pending_events(ch) if e != ev])
                await save_active(session, ch, tz)
                st.setdefault("log", []).append(f"{me}: {content.events[ev].title}")
                await _save(session, st)
                res.ok, res.event = True, ev
                return res
    if what == "mark":  # owner request 24.10: NPC shelters
        m = mark(content, key)
        tag = f"m:{key}"
        if m is None or tag in [str(x) for x in rec.get("targets", [])]:
            rec["hunts"] = int(rec["hunts"]) - 1
            return NightResult(blocked=R_TARGET)
        rec.setdefault("targets", []).append(tag)
        mine = _power(ch.state, content) + c.storm_bonus + rng.randint(1, 10)
        theirs = m.defense + rng.randint(1, 10)
        if mine > theirs:
            loot = rng.randint(*m.loot)
            rec["kills"] = int(rec.get("kills", 0)) + 1
            rec["loot"] = int(rec.get("loot", 0)) + loot
            st.setdefault("log", []).append(f"{me} — взят объект: {m.name}")
            await _hit_self(session, content, ch, rng, year, tz, {"eddies": loot}, res)
            res.ok, res.text = True, f"{m.win} Счёт {mine} против {theirs}. +{loot} эдди."
        else:
            await _hit_self(session, content, ch, rng, year, tz, {"hp": -rng.randint(*m.fail_hp)}, res)
            res.text = f"{m.lose} Счёт {mine} против {theirs}."
        await _save(session, st)
        return res
    if what == "raid":  # owner request 24.10: hunt an NPC crew of the night
        r = raider(content, key)
        tag = f"r:{key}"
        if r is None or tag in [str(x) for x in rec.get("targets", [])]:
            rec["hunts"] = int(rec["hunts"]) - 1
            return NightResult(blocked=R_TARGET)
        rec.setdefault("targets", []).append(tag)
        await _save(session, st)
        await _begin_fight(session, content, ch, rng, year, tz, st, r, "hunt", res)
        res.text = f"{r.name}. {r.text}"
        return res
    if what == "street":
        from app.engine import checks
        from app.services import city

        chk = checks.roll_check(ch.state, content, rng, "REF", "firearms", c.street_dv, ("combat",))
        if chk.ok:
            await city.ensure_district(session, content, ch.state.district, rng)
            got = await session.execute(select(city.CityNpc).where(city.CityNpc.district == ch.state.district,
                                                                   city.CityNpc.alive.is_(True)))
            victims = [n for n in got.scalars() if not city.hurt(n)]
            loot = rng.randint(*c.street_loot)
            name = "прохожий"
            if victims:
                v = rng.choice(victims)
                loot += v.money
                v.money = 0
                city._kill(v, ch, "purge")
                name = v.name
            rec["kills"] = int(rec.get("kills", 0)) + 1
            rec["loot"] = int(rec.get("loot", 0)) + loot
            st.setdefault("log", []).append(f"{me} убивает: {name}")
            await _hit_self(session, content, ch, rng, year, tz, {"eddies": loot}, res)
            res.ok, res.text = True, f"Улица пуста, но не совсем. {name} не успевает добежать до двери. +{loot} эдди."
        else:
            hp = rng.randint(*c.street_fail_hp)
            await _hit_self(session, content, ch, rng, year, tz, {"hp": -hp}, res)
            res.text = "Из окна второго этажа стреляют раньше, чем ты успеваешь прицелиться."
        await _save(session, st)
        return res
    roster = st.setdefault("roster", {})
    trec = roster.get(str(target))
    if trec is None or target == ch.id or str(target) in [str(x) for x in rec.get("targets", [])]:
        rec["hunts"] = int(rec["hunts"]) - 1
        return NightResult(blocked=R_TARGET)
    them = await game.load_character(session, target)
    if them is None:
        rec["hunts"] = int(rec["hunts"]) - 1
        return NightResult(blocked=R_TARGET)
    rec.setdefault("targets", []).append(target)
    mine = _power(ch.state, content) + rng.randint(1, 10)
    if what == "storm" and trec.get("mode") == HIDE:
        trec["storms_in"] = int(trec.get("storms_in", 0)) + 1
        if trec.get("hub"):
            from app.services import hubs

            trec["together"] = hubs.together_bonus(content, roster, str(target), str(trec["hub"]))
        theirs = defense(content, trec) + derived.stat_total(them.state, content, "BODY") + rng.randint(1, 10)
        mine += c.storm_bonus
        if mine > theirs:
            take = min(c.storm_take_cap, max(0, them.state.eddies) * c.storm_take_pct // 100)
            trec["dmg"] = int(trec.get("dmg", 0)) + c.storm_defense_loss
            trec["broken"] = int(trec.get("broken", 0)) + 1
            rec["loot"] = int(rec.get("loot", 0)) + take
            _mail(session, trec["tg"], target, f"Судная ночь: {me} врывается в твоё убежище. −{take} эдди, раны.",
                  eddies=-take, hp=-c.storm_hp)
            st.setdefault("log", []).append(f"{me} берёт убежище: {trec.get('name')}")
            await _hit_self(session, content, ch, rng, year, tz, {"eddies": take}, res)
            res.ok = True
            res.text = f"Дверь поддаётся ({mine} против {theirs}). {trec.get('name')} теряет {take} эдди."
            res.post = f"🔥 Судная ночь: {me} берёт штурмом убежище, хозяин — {trec.get('name')}."
        else:
            hp = rng.randint(*c.storm_fail_hp)
            _mail(session, trec["tg"], target, f"Судная ночь: {me} пытается взять твоё убежище и отступает.")
            await _hit_self(session, content, ch, rng, year, tz, {"hp": -hp}, res)
            res.text = f"Оборона держится ({mine} против {theirs}). Из-за двери стреляют в ответ."
        await _save(session, st)
        return res
    if what == "duel" and trec.get("mode") == HUNT:
        theirs = _power(them.state, content) + rng.randint(1, 10)
        if mine >= theirs:
            take = max(0, them.state.eddies) * c.duel_take_pct // 100
            rec["kills"] = int(rec.get("kills", 0)) + 1
            rec["loot"] = int(rec.get("loot", 0)) + take
            _mail(session, trec["tg"], target, f"Судная ночь: {me} подстерегает тебя на охоте. −{take} эдди, раны.",
                  eddies=-take, hp=-c.duel_hp)
            st.setdefault("log", []).append(f"{me} побеждает охотника: {trec.get('name')}")
            await _hit_self(session, content, ch, rng, year, tz, {"eddies": take}, res)
            res.ok, res.text = True, f"Ты стреляешь первым ({mine} против {theirs}). +{take} эдди."
            res.post = f"🔥 Судная ночь: {me} подстерегает охотника {trec.get('name')}."
        else:
            _mail(session, trec["tg"], target, f"Судная ночь: {me} нападает на тебя на охоте и проигрывает.")
            await _hit_self(session, content, ch, rng, year, tz, {"hp": -c.duel_hp}, res)
            res.text = f"{trec.get('name')} оказывается быстрее ({theirs} против {mine})."
        await _save(session, st)
        return res
    rec["targets"].remove(target)
    rec["hunts"] = int(rec["hunts"]) - 1
    return NightResult(blocked=R_TARGET)


# -- dawn ------------------------------------------------------------------------------------------------------------------


async def dawn(session: AsyncSession, content: Content, st: dict, rng: random.Random) -> str:
    """The sieges of the gangs, the unprepared on the street, the rewards; the summary post."""
    c = cfg(content)
    roster = st.setdefault("roster", {})
    lines = ["🌅 <b>Рассвет. Судная ночь окончена.</b>", ""]
    held, fell, caught = [], [], []
    from app.services import hubs  # owner request 23.10: everyone hiding in the same shelter holds the door together

    for cid, r in roster.items():
        if r.get("threat"):  # owner request 24.10: a visit nobody answered before dawn
            _auto(session, content, st, cid, r, rng)
    for cid, r in roster.items():
        if r.get("mode") == HIDE and r.get("hub"):
            r["together"] = hubs.together_bonus(content, roster, cid, str(r["hub"]))
    res = await session.execute(select(Character).where(Character.alive.is_(True)))
    for row in res.scalars():
        if (row.flags or {}).get("abroad"):  # owner request 25.10: abroad, far from the Purge
            continue
        rec = roster.get(str(row.id))
        mode = rec.get("mode") if rec else None
        if mode == HIDE and rec is not None:
            power = c.siege_power + rng.randint(1, 10)
            dfn = defense(content, rec) + rng.randint(1, 10)
            if power > dfn:
                _mail(session, row.player_tg_id, row.id, "Судная ночь: к рассвету банда ломает твою дверь. Деньги и кровь.",
                      eddies=-1, hp=-c.siege_hp, pct=c.siege_take_pct)
                fell.append(rec.get("name", ""))
            else:
                held.append(rec.get("name", ""))
        elif mode is None and rng.random() < c.street_caught_chance:
            _mail(session, row.player_tg_id, row.id, "Судная ночь застала тебя на улице без укрытия.",
                  eddies=-1, hp=-c.street_caught_hp, pct=c.street_caught_take_pct)
            caught.append(f"{row.name} {row.surname}".strip())
    hunters = [(int(r.get("kills", 0)) * 1000 + int(r.get("loot", 0)), cid, r) for cid, r in roster.items()
               if r.get("mode") == HUNT and (r.get("kills") or r.get("loot"))]
    if hunters:
        _, cid, best = max(hunters)
        _mail(session, best["tg"], int(cid), f"Ты — охотник Судной ночи. +{c.winner_eddies} эдди, слава.",
              eddies=c.winner_eddies, fame=c.winner_fame)
        lines.append(f"🎯 <b>Охотник ночи:</b> {best['name']} — убийств {best.get('kills', 0)}, добыча {best.get('loot', 0)} эдди.")
    forts = [(int(r.get("storms_in", 0)), defense(content, r), cid, r) for cid, r in roster.items()
             if r.get("mode") == HIDE and not r.get("broken") and r.get("name") in held]
    if forts:
        _, dfn, cid, best = max(forts)
        _mail(session, best["tg"], int(cid), f"Твоё убежище — крепость Судной ночи. +{c.fortress_eddies} эдди.",
              eddies=c.fortress_eddies)
        lines.append(f"🏰 <b>Крепость ночи:</b> {best['name']} — штурмов {best.get('storms_in', 0)}, оборона {dfn}.")
    if held:
        lines.append("🛡 Убежища выстояли: " + ", ".join(held) + ".")
    if fell:
        lines.append("💥 Убежища пали к рассвету: " + ", ".join(fell) + ".")
    if caught:
        lines.append("🩸 Ночь застала на улице: " + ", ".join(caught) + ".")
    log = list(st.get("log") or [])
    if log:
        lines += ["", "<b>Хроника ночи</b>"] + [f"· {x}" for x in log[-12:]]
    if len(lines) <= 2:
        lines.append("Город пережил ночь тихо. Подозрительно тихо.")
    st["status"] = DONE
    await _save(session, st)
    await session.flush()
    return "\n".join(lines)
