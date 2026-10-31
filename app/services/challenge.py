"""Challenges (owner request 25.10): once a day every living character is measured. A fighter who outgrew the city and a
character who got rich get a personal threat that Claude designs against the build or the fortune; every next one is
twice as strong (engine/challenge.py).

Owner request 25.10 (2): «вызов не должен быть одним боем, который можно пережить». A challenge is three stages, and
while it is open the threat bites every day by mail:
  fighter — a fight with the squad → track the leader down (a check) → the fight with the leader (stronger);
            every day: wounds and money;
  wealth  — a blow to the assets (pay or a check) → an attempt on the life (a fight) → the board or the court (pay or a
            check); at the start a share of the money is frozen and comes back only with the win; every day: money.
Not done in time: the street calls it cowardice, the frozen money is gone.

world_state `challenges` = {char_id: {level, wlevel, last, open: {...} | None, history, report, day}}
The fights: services/combat.begin with src "challenge"; fight_over moves the stage.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date, utcnow
from app.content.loader import Content
from app.db import repo
from app.db.models import Character, PlayerMail
from app.engine import challenge as eng
from app.engine import checks
from app.engine.effects import apply_effects
from app.services import game
from app.services.game import ActiveCharacter, save_active

log = logging.getLogger(__name__)
KEY = "challenges"
SRC = "challenge"
FIGHTER, WEALTH = "fight", "wealth"
FALLBACK = [
    ("Охота «Чёрного списка»", "Ликвидатор Штерн", "militech", "pierce",
     "Корпорации надоело, что один человек ломает их отряды. Они наняли команду, которая охотится только на таких, как ты, "
     "и знает, куда бить сквозь броню.", "Броня — это просто отсрочка."),
    ("Рой", "Королева Улья", "maelstrom", "swarm",
     "Все банды города скинулись и наняли толпу: если один не справляется, придут десять.", "Ты силён. Нас больше."),
    ("Глухая ночь", "Тень из NetWatch", "netwatch", "jammer",
     "Твою оптику и прицелы глушат со спутника. Кто-то очень хочет посмотреть, чего ты стоишь вслепую.",
     "Посмотрим, как ты стреляешь без глаз."),
    ("Разряд", "Доктор Вольт", "maxtac", "stun",
     "МаксТак выпустил экспериментальный отряд с шокерами против киборгов. Ты в их списке первым.",
     "Танки падают громче всех."),
    ("Тяжёлая артиллерия", "Сержант Молот", "arasaka", "heavy",
     "Арасака прислала штурмовую группу с оружием, которое не предназначено для улиц.", "Тебе понравится наш калибр."),
]
FALLBACK_W = [
    ("Рейдерский захват", "Синдикат «Сорок»", "militech", "heavy",
     "Синдикат скупает долги твоих бизнесов и присылает юристов с охраной. Сначала бумаги, потом стволы.",
     "Налоговая находит у твоих бизнесов долги, о которых ты не знаешь. Счета замораживают один за другим.",
     "Совет кредиторов решает, кому достанется твоё имя на вывеске.", "Всё, что твоё, станет нашим. По закону."),
    ("Тихое поглощение", "Арасака", "arasaka", "pierce",
     "Арасака решила, что твой капитал выглядит лучше в их отчётности. Предложение, от которого нельзя отказаться, "
     "уже в пути.", "Поставщики разрывают контракты, партнёры перестают отвечать.",
     "Суд по делу о недобросовестной конкуренции. Судья ужинает с их юристами.", "Мы не угрожаем. Мы покупаем."),
    ("Кандидат", "Сенатор Мэйхью", "ncpd", "swarm",
     "Кандидату в мэры нужны деньги на кампанию, и он решил, что твои подойдут. Проверки, обыски, улыбки в камеру.",
     "NCPD опечатывает склады «до выяснения».", "Комиссия по этике решает, оставить ли тебе лицензии.",
     "Город любит щедрых. Будь щедрым."),
]


async def book(session: AsyncSession) -> dict[str, dict]:
    return {str(k): dict(v) for k, v in (await repo.ws_get(session, KEY, {}) or {}).items()}


async def _save(session: AsyncSession, b: dict[str, dict]) -> None:
    await repo.ws_set(session, KEY, b)


def _mail(session: AsyncSession, tg: int, char_id: int, text: str, **payload: int) -> None:
    session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name="Вызов", kind="city", to_char_id=char_id,
                           payload={"text": text, **{k: v for k, v in payload.items() if v}}))


def track_of(op: dict) -> str:
    return str(op.get("track") or FIGHTER)


def stages_of(op: dict) -> tuple[str, ...]:
    return eng.WEALTH_STAGES if track_of(op) == WEALTH else eng.FIGHT_STAGES


def stage_of(op: dict) -> str:
    st = stages_of(op)
    return st[min(int(op.get("stage", 0) or 0), len(st) - 1)]


async def _design(content: Content, ch: ActiveCharacter, track: str, level: int, history: list[str],
                  rng: random.Random) -> dict:
    """Claude designs the threat; a ready one when the narrator is off or answers badly."""
    from app.narrator import narrator

    from app.engine import combat as cb

    c = content.balance.challenge
    wealth = track == WEALTH
    year = ch.state.current_year
    # owner report 26.10 «дерусь с Kang Tao, а против меня Militech»: only those who have fighters in this year
    facs = [f for f in c.factions if cb._faction_pool(content, f, year)] or ["militech"]  # noqa: SLF001
    payload = {"level": level, "history": history[-6:], "factions": facs, "counters": list(eng.COUNTERS)}
    payload["wealth" if wealth else "build"] = (eng.wealth_summary(ch.state, content) if wealth
                                                else eng.build_summary(ch.state, content))
    data = None
    try:
        data = await narrator.generate_json("challenge_w" if wealth else "challenge", payload, year=ch.state.current_year,
                                            text_max=450)
    except Exception:  # noqa: BLE001 - the narrator never breaks the game
        log.exception("challenge: narrator failed")
    ok = (isinstance(data, dict) and data.get("faction") in facs and data.get("counter") in eng.COUNTERS
          and data.get("title") and data.get("intro") and (not wealth or (data.get("assets") and data.get("board"))))
    if not ok:
        if wealth:
            t, foe, fac, counter, intro, assets, board, taunt = rng.choice(
                [x for x in FALLBACK_W if x[2] in facs] or FALLBACK_W)
            return {"title": t, "foe": foe, "faction": fac, "counter": counter, "intro": intro, "assets": assets,
                    "board": board, "taunt": taunt, "ai": False}
        t, foe, fac, counter, intro, taunt = rng.choice([x for x in FALLBACK if x[2] in facs] or FALLBACK)
        return {"title": t, "foe": foe, "faction": fac, "counter": counter, "intro": intro, "taunt": taunt, "ai": False}
    out = {"title": str(data["title"])[:40], "foe": str(data.get("foe") or "Главарь")[:30], "faction": data["faction"],
           "counter": data["counter"], "intro": str(data["intro"])[:600], "taunt": str(data.get("taunt") or "")[:140],
           "ai": True}
    if wealth:
        out["assets"], out["board"] = str(data["assets"])[:220], str(data["board"])[:220]
    return out


def _pick_track(rec: dict, fighter: bool, rich: bool) -> str | None:
    if fighter and rich:
        return WEALTH if rec.get("last") == FIGHTER else FIGHTER
    return FIGHTER if fighter else WEALTH if rich else None


async def daily(session: AsyncSession, content: Content, tz: str, rng: random.Random | None = None,
                force: bool = False) -> list[str]:
    """Measure every living character; the open challenges bite; open new ones. Returns posts for the group."""
    rng = rng or random.Random()
    c = content.balance.challenge
    today = kyiv_date(tz)
    b = await book(session)
    posts: list[str] = []
    now = utcnow()
    res = await session.execute(select(Character).where(Character.alive.is_(True)))
    for row in list(res.scalars()):
        rec = b.setdefault(str(row.id), {"level": 1, "wlevel": 1, "open": None, "history": []})
        op = rec.get("open")
        if op and datetime.fromisoformat(op["expires"]) < now:  # ignored or not finished: the street calls it cowardice
            esc = int(op.get("escrow", 0) or 0)
            _mail(session, row.player_tg_id, row.id, f"Вызов «{op['title']}» не пройден до срока. Улица решила, что ты отступаешь."
                  + (f" Замороженные {esc} эдди сгорают." if esc else ""), cred=-c.coward_cred, fame=-c.coward_fame)
            rec["open"] = None
            op = None
        if op and op.get("hit_day") != today and op.get("opened_day") != today:  # the threat bites every day it is open
            op["hit_day"] = today
            lv = int(op["level"])
            hit = eng.daily_hit(content, track_of(op), lv)
            what = ("люди врага давят на твои деньги: счета, контракты, проверки" if track_of(op) == WEALTH
                    else "люди врага достают тебя на улице")
            _mail(session, row.player_tg_id, row.id, f"⚔️ «{op['title']}»: {what}.", **hit)
        if rec.get("day") == today and not force:
            continue
        ch = await game.load_character(session, row.id)
        if ch is None:
            continue
        try:
            report = eng.power(ch.state, content, ch.state.current_year, seed=row.id)
        except Exception:  # noqa: BLE001 - one broken build must not stop the others
            log.exception("challenge: power failed for %s", row.id)
            continue
        rec["report"], rec["day"] = report, today
        if op:
            continue
        fighter = eng.overpowered(report, content) or int(rec.get("level", 1)) > 1
        rich = eng.wealthy(ch.state, content) or int(rec.get("wlevel", 1) or 1) > 1
        track = _pick_track(rec, fighter, rich)
        if track is None:
            continue
        level = int(rec.get("wlevel" if track == WEALTH else "level", 1) or 1)
        d = await _design(content, ch, track, level, list(rec.get("history") or []), rng)
        d.update(level=level, track=track, stage=0, opened_day=today,
                 expires=(now + timedelta(hours=c.expire_hours)).isoformat())
        rec["open"], rec["last"] = d, track
        icon = "💼" if track == WEALTH else "⚔️"
        _mail(session, row.player_tg_id, row.id, f"{icon} Вызов {level}: «{d['title']}». Открой его в меню: кнопка «Вызов».")
        posts.append(f"{icon} <b>{'Город заметил деньги' if track == WEALTH else 'Город заметил силу'}.</b> "
                     f"{row.name} {row.surname}: вызов {level}, «{d['title']}». Три этапа, каждый день промедления стоит дорого.")
    await _save(session, b)
    return posts


async def current(session: AsyncSession, ch: ActiveCharacter) -> dict | None:
    return (await book(session)).get(str(ch.id), {}).get("open")


async def record(session: AsyncSession, ch: ActiveCharacter) -> dict:
    return (await book(session)).get(str(ch.id), {"level": 1, "wlevel": 1, "history": []})


def _faction(content: Content, op: dict, year: int) -> str:
    from app.engine import combat as cb

    for f in (str(op.get("faction") or ""), "militech", "ncpd"):
        if cb._faction_pool(content, f, year):  # noqa: SLF001
            return f
    return "ncpd"


def _blocked(ch: ActiveCharacter) -> str | None:
    from app.engine import jail as jail_eng
    from app.services import events as ev_service

    if jail_eng.in_jail(ch.state):
        return "jail"
    if ev_service.has_pending(ch):
        return "busy"
    return None


async def _freeze(session: AsyncSession, content: Content, ch: ActiveCharacter, b: dict, op: dict, rng: random.Random,
                  year: int) -> None:
    """The first step of a wealth challenge freezes a share of the money until the win."""
    if track_of(op) != WEALTH or op.get("frozen"):
        return
    amount = eng.escrow(content, int(op["level"]), ch.state.eddies)
    if amount:
        apply_effects(ch.state, {"eddies": -amount}, content, rng, year)
    op["escrow"], op["frozen"] = amount, True


async def accept(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str):  # noqa: ANN201
    """Start the fight of the current stage (fight, boss, hit), or a block reason (str)."""
    from app.services import combat
    from app.services.combat import _store

    b = await book(session)
    rec = b.get(str(ch.id)) or {}
    op = rec.get("open")
    if not op:
        return "none"
    stage = stage_of(op)
    if stage not in ("fight", "boss", "hit"):
        return "stage"
    r = _blocked(ch)
    if r:
        return r
    c = content.balance.challenge
    await _freeze(session, content, ch, b, op, rng, year)
    await _save(session, b)
    level = int(op["level"])
    step = await combat.begin(session, content, ch, rng, year, tz, faction=_faction(content, op, year), danger=c.danger,
                              src={"src": SRC, "level": level, "stage": stage, "solo": True})
    eng.scale(step.cs, content, level, str(op["counter"]), rng, year,
              "" if stage == "fight" else str(op.get("foe") or ""))
    if stage == "boss" and step.cs.enemies:
        lead = max(step.cs.enemies, key=lambda e: e.max_hp)
        lead.max_hp = lead.hp = int(lead.max_hp * c.boss_hp_mult)
    _store(ch, step.cs)
    await save_active(session, ch, tz)
    return step


async def act(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              how: str) -> tuple[str | None, list[str], object | None]:
    """A stage without a fight: track (how = net | street), assets / board (how = pay | check).
    Returns (block reason, lines, check)."""
    from app.services import ap as ap_services

    b = await book(session)
    rec = b.get(str(ch.id)) or {}
    op = rec.get("open")
    if not op:
        return "none", [], None
    stage = stage_of(op)
    if stage not in ("track", "assets", "board"):
        return "stage", [], None
    r = _blocked(ch)
    if r:
        return r, [], None
    c = content.balance.challenge
    level = int(op["level"])
    st = ch.state
    await _freeze(session, content, ch, b, op, rng, year)
    lines: list[str] = []
    chk = None
    if how == "pay" and stage in ("assets", "board"):
        price = eng.pay_price(content, level, stage)
        if st.eddies < price:
            await _save(session, b)
            await save_active(session, ch, tz)
            return "eddies", [], None
        apply_effects(st, {"eddies": -price}, content, rng, year)
        lines.append(f"Ты платишь {price} эдди. " + ("Активы отбиты: юристы, взятки, новые счета."
                                                    if stage == "assets" else "Голоса куплены. Решение в твою пользу."))
        passed = True
    else:
        if not ap_services.can_spend(ch, c.check_ap):
            await _save(session, b)
            await save_active(session, ch, tz)
            return "ap", [], None
        ap_services.spend(ch, content, c.check_ap)
        stat, skill, ctxs = {"net": ("INT", "netrunning", ("gig_hack",)), "street": ("COOL", "persuasion", ("social",)),
                             "check": ("INT", "persuasion", ("career", "social"))}.get(how, ("COOL", "persuasion", ("social",)))
        if stage == "board":
            stat = "COOL"
        chk = checks.roll_check(st, content, rng, stat, skill, eng.check_dv(content, level), ctxs)
        passed = chk.ok
        if passed:
            lines.append({"track": f"След найден: {op.get('foe') or 'главарь'} прячется там, где ты не стал бы искать. Пора.",
                          "assets": "Юристы и связи держат удар: активы целы.",
                          "board": "Ты переговариваешь совет. Решение в твою пользу."}[stage])
        elif stage == "track":
            dmg = min(c.check_fail_hp * level, max(0, st.hp - 1))
            if dmg:
                apply_effects(st, {"hp": -dmg}, content, rng, year)
            lines.append(f"След ведёт в засаду. Ты уходишь, но с ранами (−{dmg} здоровья). Можно попробовать снова.")
        else:
            pct = eng.fail_pct(content, level)
            loss = st.eddies * pct // 100
            if loss:
                apply_effects(st, {"eddies": -loss}, content, rng, year)
            lines.append(f"Не вышло: враг забирает {pct}% денег на руках (−{loss} эдди). Можно попробовать снова.")
    if passed:
        op["stage"] = int(op.get("stage", 0) or 0) + 1
        if op["stage"] >= len(stages_of(op)):
            lines += await _win(session, content, ch, rng, year, tz, b, rec, op)
            return None, lines, chk
        lines.append(f"Дальше: {eng.STAGE_RU[stage_of(op)].lower()}.")
    await _save(session, b)
    await save_active(session, ch, tz)
    return None, lines, chk


async def _win(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               b: dict, rec: dict, op: dict) -> list[str]:
    from app.services import loud

    c = content.balance.challenge
    level = int(op["level"])
    wealth = track_of(op) == WEALTH
    if wealth:
        reward = min(c.w_reward_cap, c.w_reward * eng.mult(level))
        back = int(op.get("escrow", 0) or 0)
        apply_effects(ch.state, {"eddies": reward + back, "fame": c.fame_per_level * level}, content, rng, year)
        rec["wlevel"] = level + 1
        text = f"💼 Вызов {level} пройден: замороженные {back} эдди вернулись, сверху +{reward}."
    else:
        reward = min(c.reward_cap, c.reward_base * eng.mult(level))
        apply_effects(ch.state, {"eddies": reward, "fame": c.fame_per_level * level, "street_cred": c.cred_per_level * level},
                      content, rng, year)
        rec["level"] = level + 1
        text = f"⚔️ Вызов {level} пройден: +{reward} эдди, слава и репутация."
    rec["history"] = (list(rec.get("history") or []) + [op["title"]])[-12:]
    rec["open"] = None
    await _save(session, b)
    await save_active(session, ch, tz)
    from app.services import season  # owner request 25.10

    await season.add_ch(session, content, ch, "challenge", content.balance.season.pts.get("challenge", 0) * level)
    await loud.post(session, ch.id, f"{'💼' if wealth else '⚔️'} {loud.who(ch.state.name, ch.state.surname)} проходит вызов "
                                    f"{level}: «{op['title']}». Следующий будет вдвое сильнее.", kind="challenge", w=2)
    return [text + f" Следующий вызов — ×{eng.mult(level + 1)}."]


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb

    b = await book(session)
    rec = b.get(str(ch.id))
    if rec is None or not rec.get("open"):
        return []
    op = rec["open"]
    stage = stage_of(op)
    if stage not in ("fight", "boss", "hit"):
        return []
    if result != cb.WON:
        await _save(session, b)
        return [f"⚔️ «{op['title']}»: этап «{eng.STAGE_RU[stage]}» не взят. Вызов ждёт до срока, можно пробовать снова."]
    op["stage"] = int(op.get("stage", 0) or 0) + 1
    if op["stage"] >= len(stages_of(op)):
        return await _win(session, content, ch, rng, year, tz, b, rec, op)
    await _save(session, b)
    return [f"⚔️ «{op['title']}»: этап «{eng.STAGE_RU[stage]}» пройден. Дальше: {eng.STAGE_RU[stage_of(op)].lower()}."]
