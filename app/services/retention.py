"""Bringing players back (owner request).

- Onboarding: six first steps («Первые шаги») shown in the menu; each pays, all of them pay more. Progress lives in
  the character flag `onboarding` = [done step keys]; a character created before this already counts its past deeds
  only from now on.
- The shared goal of the day: one goal from `balance.retention.goals` per Kyiv date; every matching deed of any player
  counts; at the target every contributor gets a city letter with eddies. State in world_state `group_goal`.
- Pings: a player who got a letter (an offer, a gift, an attack, ...) is mentioned in the group, outside quiet hours.
- The recap line for the night news: what each living character did in the last 24 hours.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.content.schemas import GroupGoal
from app.db import repo
from app.db.models import Character, EventLog, Player, PlayerMail
from app.engine.effects import apply_effects
from app.services import game_events as bus
from app.services import notices
from app.services.game import ActiveCharacter, save_active

log = logging.getLogger(__name__)

FLAG = "onboarding"
GOAL_KEY = "group_goal"
PING_KEY = "mail_pinged_upto"
CITY_MAIL = "city"


@dataclass(frozen=True)
class Step:
    key: str
    text: str
    events: frozenset[str]


ACTIVITY_EVENTS = frozenset({"train", "study", "rest", "bar", "braindance", "therapy", "meditate", "martial_arts",
                             "action_done"})
SOCIAL_EVENTS = frozenset({"player_gift", "player_drink", "player_vouch", "player_note", "player_hookup",
                           "player_date", "player_duel", "player_together", "social_time", "player_job"})

STEPS: tuple[Step, ...] = (
    Step("week", "Проживи месяц: кнопка «Прожить месяц»", frozenset({"week_lived"})),
    Step("work", "Отработай смену: «Работа»", frozenset({"work_hard"})),
    Step("fun", "Займись чем-нибудь: «Активности» или «Досуг и приколы»", ACTIVITY_EVENTS),
    Step("gig", "Выполни заказ фиксера: «Заказы»", frozenset({"gig_done"})),
    Step("gear", "Купи ствол или броню («Имущество») или поставь имплант («Риппер»)",
         frozenset({"item_bought", "cyberware_installed"})),
    Step("social", "Сделай что-нибудь с другим игроком: «Отношения» → «Свои»", SOCIAL_EVENTS),
)


def progress(ch: ActiveCharacter) -> list[str]:
    return list(ch.state.flags.get(FLAG) or [])


VETERAN_WEEKS = 30  # a character this old when the steps appeared does not get them


def finished(ch: ActiveCharacter) -> bool:
    flag = ch.state.flags.get(FLAG)
    return flag == "done" or (flag is None and ch.state.weeks_lived >= VETERAN_WEEKS)


def menu_block(ch: ActiveCharacter) -> str | None:
    """The «Первые шаги» lines for the menu, or None when all are done."""
    if finished(ch):
        return None
    done = set(progress(ch))
    lines = [f"<b>Первые шаги</b> ({len(done)}/{len(STEPS)}): за каждый шаг награда, за все — большая."]
    for st in STEPS:
        lines.append(("✓ " if st.key in done else "▫ ") + st.text)
    return "\n".join(lines)


def next_step_line(ch: ActiveCharacter) -> str | None:
    """One line for the menu: the next first step and the progress (the full list is in the profile)."""
    if finished(ch):
        return None
    done = set(progress(ch))
    nxt = next((st for st in STEPS if st.key not in done), None)
    if nxt is None:
        return None
    return f"🧭 Первые шаги {len(done)}/{len(STEPS)}. Дальше: {nxt.text}. Весь список — в профиле."


class OnboardingListener:
    def __init__(self, content: Content, tz: str) -> None:
        self.content, self.tz = content, tz

    async def __call__(self, ev: bus.GameEvent) -> None:
        ch, s = ev.ch, ev.session
        if ch is None or s is None or finished(ch):
            return
        done = progress(ch)
        step = next((st for st in STEPS if st.key not in done and ev.name in st.events), None)
        if step is None:
            return
        cfg = self.content.balance.retention
        done.append(step.key)
        year = ch.state.current_year
        rng = random.Random()
        apply_effects(ch.state, {"eddies": cfg.step_eddies}, self.content, rng, year)
        if len(done) >= len(STEPS):
            ch.state.flags[FLAG] = "done"
            apply_effects(ch.state, {"eddies": cfg.final_eddies, "street_cred": cfg.final_cred}, self.content, rng, year)
            notices.push(ch, f"Первые шаги пройдены! Город тебя заметил: +{cfg.final_eddies} эдди, "
                             f"уличная репутация +{cfg.final_cred}.")
            await self._drop_pack(ev, cfg.starter_pack)
        else:
            ch.state.flags[FLAG] = done
            notices.push(ch, f"Первые шаги: выполнено {len(done)}/{len(STEPS)}, +{cfg.step_eddies} эдди.")
        await save_active(s, ch, self.tz)


    async def _drop_pack(self, ev: bus.GameEvent, pack) -> None:
        """Owner request 30.10: the starter pack arrives with the last first step: eddies, the clown suit and the two
        kitchen things for everyone, and the signature pistol of the group of the starting kit."""
        ch, s = ev.ch, ev.session
        preset = self.content.presets.get(ch.state.preset)
        pistol = pack.pistols.get(preset.group) if preset is not None else None
        keys = [*pack.items, *([pistol] if pistol else [])]
        eff: dict = {"eddies": pack.eddies} if pack.eddies else {}
        for k in keys:
            eff[f"item_grant:{k}"] = True
        apply_effects(ch.state, eff, self.content, random.Random(), ch.state.current_year)
        names = [(self.content.weapons.get(k) or self.content.armor.get(k)).name for k in keys
                 if k in self.content.weapons or k in self.content.armor]
        text = f"📦 Стартовый пак от Города: +{pack.eddies} эдди, " + ", ".join(names) + ". Всё лежит в «Имуществе»."
        notices.push(ch, text)
        s.add(PlayerMail(to_tg_id=int(ch.player_tg_id), from_tg_id=0, from_name="Найт-Сити", kind=CITY_MAIL,
                         to_char_id=int(ch.id), payload={"text": text}))


# -- the shared goal of the day ------------------------------------------------------------------------------------------


def _today(tz: str) -> str:
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def goal_for(content: Content, date: str, n: int = 0) -> GroupGoal | None:
    """The n-th goal of the day: the goals are shuffled once per date, so the day's chain never repeats a goal."""
    goals = list(content.balance.retention.goals)
    if not goals:
        return None
    random.Random(f"goal:{date}").shuffle(goals)
    return goals[n % len(goals)]


async def goal_state(session: AsyncSession, content: Content, tz: str) -> dict:
    """Today's goal state; a new day starts a new chain of goals."""
    date = _today(tz)
    st = dict(await repo.ws_get(session, GOAL_KEY, {}) or {})
    if st.get("date") != date:
        g = goal_for(content, date, 0)
        st = {"date": date, "n": 0, "key": g.key if g else "", "count": 0, "by": {}, "done": False, "announced": False,
              "finished": [], "posted": 0}
        await repo.ws_set(session, GOAL_KEY, st)
    elif "n" not in st:  # owner report 24.10: a state of the old single goal a day joins the chain
        done = bool(st.get("done"))
        st["finished"] = [{"key": st.get("key"), "by": list(st.get("by") or {})}] if done else []
        st["posted"] = 1 if done and st.get("finished_posted") else 0
        st.pop("finished_posted", None)
        st["n"] = 1 if done else 0
        if done:
            g = _next_goal(content, date, 1, {str(st.get("key"))})
            st.update(key=g.key if g else "", count=0, by={}, done=g is None)
        await repo.ws_set(session, GOAL_KEY, st)
    return st


def _next_goal(content: Content, date: str, n: int, used: set[str]) -> GroupGoal | None:
    """The n-th goal of the day that is not among `used` (the chain never repeats a goal)."""
    total = len(content.balance.retention.goals)
    for i in range(n, n + total):
        g = goal_for(content, date, i)
        if g is not None and g.key not in used:
            return g
    return None


def _goal(content: Content, key: str | None) -> GroupGoal | None:
    return next((x for x in content.balance.retention.goals if x.key == key), None)


def goal_text(content: Content, st: dict) -> str:
    """The menu line: the current goal and its progress, or that the day's goals are all done."""
    per_day = content.balance.retention.goals_per_day
    n = int(st.get("n", 0) or 0)
    if st.get("done"):
        return f"все {per_day} цели на сегодня выполнены, завтра новые"
    g = _goal(content, st.get("key"))
    if g is None:
        return ""
    return (g.text.format(target=g.target) + f" — {min(int(st.get('count', 0)), g.target)}/{g.target}"
            + f" (цель {n + 1} из {per_day}, награда +{content.balance.retention.goal_reward} эдди каждому, кто помог)")


class GroupGoalListener:
    def __init__(self, content: Content, tz: str) -> None:
        self.content, self.tz = content, tz

    async def __call__(self, ev: bus.GameEvent) -> None:
        s = ev.session
        if s is None:
            return
        st = await goal_state(s, self.content, self.tz)
        g = _goal(self.content, st.get("key"))
        if g is None or st.get("done") or ev.name not in g.events:
            return
        st["count"] = int(st.get("count", 0)) + 1
        by = dict(st.get("by") or {})
        by[str(ev.player_tg_id)] = int(by.get(str(ev.player_tg_id), 0)) + 1
        st["by"] = by
        if st["count"] >= g.target:
            cfg = self.content.balance.retention
            reward = cfg.goal_reward
            for tg in by:
                s.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name="Город", kind=CITY_MAIL,
                                 payload={"eddies": reward, "text": f"Общая цель дня выполнена! Твоя доля: +{reward} эдди."}))
            st["finished"] = list(st.get("finished") or []) + [{"key": g.key, "by": list(by)}]
            n = int(st.get("n", 0) or 0) + 1
            used = {str(f.get("key")) for f in st["finished"]}
            nxt = _next_goal(self.content, str(st.get("date")), n, used) if n < cfg.goals_per_day else None
            if nxt is None:
                st["done"] = True
            else:  # owner report 24.10: the next goal opens at once, so the night owls do not close the whole day
                st.update(n=n, key=nxt.key, count=0, by={})
        await repo.ws_set(s, GOAL_KEY, st)


# -- pings and the group posts ------------------------------------------------------------------------------------------

MAIL_WORDS = {
    "gift": "подарок", "drink": "приглашение выпить", "vouch": "рекомендацию", "note": "записку",
    "together": "время вместе", "offer": "предложение, оно ждёт ответа", "reply": "ответ на предложение",
    "hire": "оплату найма", "qpay": "оплату поручения", "qdone": "отчёт по поручению", "qfail": "провал поручения",
    "raid": "итоги рейда", "crewpay": "выплату банды", "crewinfo": "новость банды", "attacked": "нападение",
    "msold": "продажу лота", "cameo": "встречу сквозь время", "stashfound": "весть о тайнике",
    "ai_hit": "атаку ИИ", "ai_glitch": "сбой ИИ", CITY_MAIL: "награду города",
}


PING_LAST_KEY = "mail_ping_last"  # owner report 28.10: {tg: unix time of the last letter ping}

# kinds the player screens already ping right away (app/bot/handlers/players.py `ping`)
ALREADY_PINGED = frozenset({"gift", "drink", "vouch", "note", "together", "offer", "reply", "attacked", "msold",
                            "crewpay"})


def _quiet(content: Content, tz: str) -> bool:
    cfg = content.balance.retention
    h = datetime.now(ZoneInfo(tz)).hour
    return h >= cfg.quiet_from or h < cfg.quiet_to if cfg.quiet_from > cfg.quiet_to else cfg.quiet_from <= h < cfg.quiet_to


async def pings(session: AsyncSession, content: Content, tz: str) -> list[str]:
    """Group posts for letters that arrived since the last ping (empty in quiet hours: they wait)."""
    from app.bot import mention

    if _quiet(content, tz):
        return []
    marker = await repo.ws_get(session, PING_KEY, None)
    if marker is None:  # first run: start from now, do not ping the whole history
        last = (await session.execute(select(func.max(PlayerMail.id)))).scalar() or 0
        await repo.ws_set(session, PING_KEY, int(last))
        return []
    upto = int(marker)
    res = await session.execute(select(PlayerMail).where(PlayerMail.id > upto, PlayerMail.delivered_at.is_(None))
                                .order_by(PlayerMail.id))
    letters = list(res.scalars())
    if not letters:
        return []
    await repo.ws_set(session, PING_KEY, max(m.id for m in letters))
    by: dict[int, list[PlayerMail]] = {}
    for m in letters:
        if m.kind in ALREADY_PINGED:
            continue
        by.setdefault(m.to_tg_id, []).append(m)
    out = []
    import time as _time  # owner report 28.10 «спамит»: one letter ping per player in a while

    last = dict(await repo.ws_get(session, PING_LAST_KEY, {}) or {})
    now = _time.time()
    cool = content.balance.retention.mail_ping_cooldown_h * 3600
    for tg in list(by):
        if now - float(last.get(str(tg), 0) or 0) < cool:
            by.pop(tg)
        else:
            last[str(tg)] = now
    await repo.ws_set(session, PING_LAST_KEY, {k: v for k, v in last.items() if now - float(v) < 2 * cool + 3600})
    for tg, ms in by.items():
        p = await session.get(Player, tg)
        name = await repo.display_name(session, tg, "")
        what: list[str] = []
        for m in ms:
            if m.kind == "dialog":  # owner report 28.10: the scenarist's letters
                what.append("последствия твоих дел" if (m.payload or {}).get("deeds")
                            else f"последствия разговора с {m.from_name}")
                continue
            w = MAIL_WORDS.get(m.kind, "письмо")
            frm = f" от {m.from_name}" if m.from_name and m.kind not in (CITY_MAIL, "raid") else ""
            what.append(w + frm)
        uniq = list(dict.fromkeys(what))[:4]
        out.append(f"{mention.ping(p.username if p else None, name or None, tg)}, в Найт-Сити тебя ждёт: "
                   f"{'; '.join(uniq)}. Открой /play")
    return out


async def goal_posts(session: AsyncSession, content: Content, tz: str) -> list[str]:
    """The announcement of the goal of the day (once, after goal_hour), and a post for every finished goal with the
    next one (owner report 24.10: a chain of goals a day)."""
    st = await goal_state(session, content, tz)
    if not st.get("key"):
        return []
    if _quiet(content, tz):
        return []
    out = []
    cfg = content.balance.retention
    hour = datetime.now(ZoneInfo(tz)).hour
    finished = list(st.get("finished") or [])
    posted = int(st.get("posted", 0) or 0)
    changed = False
    if posted < len(finished):
        for f in finished[posted:]:
            g = _goal(content, f.get("key"))
            names = [await repo.display_name(session, int(tg), "") for tg in f.get("by") or []]
            out.append(f"🎯 <b>Общая цель выполнена</b>: {g.text.format(target=g.target) if g else ''}. "
                       f"Спасибо: {', '.join(n for n in names if n)}. Награда ждёт в /play")
        st["posted"] = len(finished)
        changed = True
        if not st.get("done"):
            st["announced"] = True
            out.append(f"🎯 <b>Следующая цель дня</b>: {goal_text(content, st)}.")
        else:
            out.append(f"🎯 На сегодня все {cfg.goals_per_day} общие цели выполнены. Завтра новые.")
    elif not st.get("announced") and hour >= cfg.goal_hour and not st.get("done"):
        st["announced"] = True
        changed = True
        out.append(f"🎯 <b>Общая цель дня</b>: {goal_text(content, st)}.")
    if changed:
        await repo.ws_set(session, GOAL_KEY, st)
    return out


# -- the recap of the night news ------------------------------------------------------------------------------------------

RECAP_KINDS = {"week_lived": ("неделя", "недели", "недель"), "gig_result": ("заказ", "заказа", "заказов"),
               "action": ("дело", "дела", "дел"), "crime": ("преступление", "преступления", "преступлений"),
               "implant_install": ("имплант", "импланта", "имплантов"), "work_hard": ("смена", "смены", "смен"),
               "event_resolved": ("событие", "события", "событий")}


async def recap_lines(session: AsyncSession) -> list[str]:
    """One line per living character: what they did in the last 24 hours, or that they were quiet."""
    since = datetime.utcnow() - timedelta(hours=24)
    rows = (await session.execute(select(Character).where(Character.alive.is_(True)).order_by(Character.id))).scalars().all()
    out = []
    for ch in rows:
        res = await session.execute(select(EventLog.kind, func.count()).where(
            EventLog.character_id == ch.id, EventLog.created_at >= since).group_by(EventLog.kind))
        counts = {k: n for k, n in res.all()}
        from app.bot.effects_text import plural

        parts = [f"{counts[k]} {plural(counts[k], words)}" for k, words in RECAP_KINDS.items() if counts.get(k)]
        tag = f'<a href="tg://user?id={ch.player_tg_id}">{_esc(ch.name)}</a>'
        hl = await _highlights(session, ch.id, since)  # owner request 24.10: the specifics of the day
        line = f"{tag}: {', '.join(parts)}" if parts else f"{tag}: тишина. Город ждёт."
        if hl:
            line += "\n   " + _esc(hl)
        out.append(line)
    return out


async def _highlights(session: AsyncSession, char_id: int, since: datetime) -> str:
    """The best paid job, the money from jobs, and the loudest thing of the day."""
    res = await session.execute(select(EventLog.kind, EventLog.payload).where(
        EventLog.character_id == char_id, EventLog.created_at >= since,
        EventLog.kind.in_(("gig_result", "loud", "legend", "arrest", "canon_reached"))).order_by(EventLog.id))
    earned, best, best_pay, loud, other = 0, "", -1, "", ""
    for kind, p in res.all():
        p = p or {}
        if kind == "gig_result":
            pay = int(((p.get("effects") or {}).get("eddies", 0)) or 0)
            earned += max(0, pay)
            if pay > best_pay:
                best, best_pay = str(p.get("title", "")), pay
        elif kind in ("loud", "legend") and not loud:
            import re

            loud = re.sub(r"<[^>]+>", "", str(p.get("text", ""))).replace("📣", "").replace("🔥", "").strip()[:90]
        elif kind == "arrest":
            other = f"арест на {int(p.get('weeks', 0) or 0)} нед."
        elif kind == "canon_reached" and not other:
            other = f"застаёт «{p.get('title', '')}»"
    bits = []
    if earned:
        bits.append(f"на заказах +{earned} эдди" + (f", лучший: «{best}»" if best else ""))
    if loud:
        bits.append(loud)
    if other:
        bits.append(other)
    return " · ".join(bits)


def _esc(s: str) -> str:
    from app.bot.texts import esc

    return esc(s)



async def deaths_lines(session: AsyncSession, content=None) -> list[str]:  # noqa: ANN001
    """Owner request 24.10: who died in the last 24 hours and of what."""
    from app.bot import texts as T

    since = datetime.utcnow() - timedelta(hours=24)
    res = await session.execute(select(EventLog.payload).where(EventLog.kind == "death", EventLog.created_at >= since)
                                .order_by(EventLog.id))
    out = []
    for (p,) in res.all():
        p = p or {}
        name = " ".join(x for x in (p.get("name"), p.get("surname")) if x) or "безымянный"
        cause = T.DEATH_CAUSE_NAMES.get(str(p.get("cause", "")), str(p.get("cause", "")))
        bits = [cause]
        if p.get("age"):
            bits.append(f"{p['age']} лет")
        if p.get("by"):
            bits.append(f"охотник: {p['by']}")
        if content is not None and p.get("district") in getattr(content, "districts", {}):
            bits.append(content.districts[p["district"]].name)
        out.append(f"⚰️ {_esc(name)} — {_esc(', '.join(bits))}")
    return out
