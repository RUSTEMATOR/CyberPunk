"""The wanted level as a chase (owner request 26.10, docs/PLAN_WANTED.md). Rules: engine/wanted.py.

- `tick` (the menu): when a wave is due, it waits as a police raid (pending combat src "raid" with `chase`), so the
  ordinary exits and fight follow; every wave is bigger, at the top heat MaxTac comes.
- `count_ap` (services/ap.spend): AP spent while wanted brings the next wave sooner.
- `after_wave` / `after_exit`: how the wave ended moves the chase.
- `witness`: a won fight in the city raises the heat only when somebody saw it.
- `hide`, `change`, `fixer`, `face`: the ways to drop it (the window «wanted»).
"""

from __future__ import annotations

import random
import time

from app.clock import kyiv_date
from app.content.loader import Content
from app.engine import checks, derived
from app.engine import wanted as eng
from app.engine.effects import apply_effects
from app.engine.state import FLAG_COMBAT, FLAG_PENDING_COMBAT
from app.services.game import ActiveCharacter

POLICE = {"ncpd": "NCPD", "maxtac": "МаксТак"}


def _set(ch: ActiveCharacter, rec: dict) -> None:
    ch.state.flags[eng.FLAG] = dict(rec)


def tick(content: Content, ch: ActiveCharacter, now: float | None = None, force: bool = False) -> str | None:
    """Called with the menu: starts or ends the chase; a due wave becomes a pending police raid. Returns a line.
    `force`: a patrol caught the player in the 3D district (docs/PLAN_CITY3D_2.md, phase 2): the wave comes now."""
    now = time.time() if now is None else now
    st = ch.state
    rec = eng.ensure(st, content, now)
    if rec is None:
        return None
    _set(ch, rec)
    if st.flags.get(FLAG_PENDING_COMBAT) or st.flags.get(FLAG_COMBAT):
        return None
    for busy in ("tower_run", "dgame", "sortie", "gig_run", "purge_back"):
        if st.flags.get(busy):
            return None
    if not force and not eng.due(st, content, now):
        return None
    n = int(rec.get("wave", 0) or 0)
    faction, danger, extra = eng.wave(content, st.heat, n)
    st.flags[FLAG_PENDING_COMBAT] = {"src": "raid", "faction": faction, "danger": danger, "extra": extra,
                                     "chase": n + 1, "title": f"Погоня, волна {n + 1}"}
    if faction == "ncpd":  # NCPD takes you alive; MaxTac does not ask
        st.flags[FLAG_PENDING_COMBAT]["nonlethal"] = True
    rec.update({"wave": n + 1, "acts": 0, "next": now + eng.interval_s(content, st.heat)})
    _set(ch, rec)
    who = POLICE[faction]
    more = f", с ними ещё {extra}" if extra else ""
    return (f"🚨 Розыск {st.heat}: {who} настигает тебя (волна {n + 1}{more}). Каждая следующая будет больше, "
            f"пока не скинешь розыск.")


def count_ap(ch: ActiveCharacter, cost: int) -> None:
    rec = ch.state.flags.get(eng.FLAG)
    if isinstance(rec, dict) and cost > 0:
        rec = dict(rec)
        rec["acts"] = int(rec.get("acts", 0) or 0) + int(cost)
        ch.state.flags[eng.FLAG] = rec


def after_wave(content: Content, ch: ActiveCharacter, result: str, now: float | None = None) -> list[str]:
    """A chase wave was fought (services/crime.fight_over already gave the heat or the arrest)."""
    now = time.time() if now is None else now
    st = ch.state
    rec = st.flags.get(eng.FLAG)
    if result != "won" or not isinstance(rec, dict):
        st.flags.pop(eng.FLAG, None)
        return []
    if not eng.can_chase(st, content):
        return []
    rec = dict(rec)
    rec["next"] = now + eng.interval_s(content, st.heat) * eng.cfg(content).won_speedup
    rec["acts"] = 0
    _set(ch, rec)
    faction, _, extra = eng.wave(content, st.heat, int(rec.get("wave", 0) or 0))
    return [f"🚨 Розыск {st.heat}. Сирены уже ближе: следующей волной едет {POLICE[faction]}"
            + (f" и ещё {extra} бойцов" if extra else "") + ". Скрыться можно в «🚨 Розыск» в меню."]


def after_exit(content: Content, ch: ActiveCharacter, result: str, now: float | None = None) -> None:
    """A chase wave ended before the fight: fled, avoided (bribe, talk) or surrendered."""
    now = time.time() if now is None else now
    rec = ch.state.flags.get(eng.FLAG)
    if not isinstance(rec, dict):
        return
    c = eng.cfg(content)
    rec = dict(rec)
    if result == "fled":
        rec["pause"] = now + c.fled_pause_min * 60
        rec["acts"] = 0
    elif result == "avoided":
        rec.update({"pause": now + c.avoided_pause_min * 60, "acts": 0, "wave": 0})
    else:
        ch.state.flags.pop(eng.FLAG, None)
        return
    _set(ch, rec)


def witness(content: Content, ch: ActiveCharacter, rng: random.Random, year: int, src: str, left_alive: int) -> list[str]:
    """A won fight in the city: heat only with witnesses (owner request 26.10)."""
    c = eng.cfg(content)
    st = ch.state
    if not c.enabled or src not in c.witness_srcs or not eng.in_city(st, content):
        return []
    stealth = derived.skill_total(st, content, "stealth")
    if not eng.witnessed(st, content, rng, stealth, left_alive):
        return ["🕶 Свидетелей не осталось: розыск не вырос."]
    before = st.heat
    apply_effects(st, {"heat": c.witness_heat}, content, rng, year)
    if st.heat <= before:
        return []
    who = "Кто-то из них остался жив и всё видел" if left_alive else "Прохожие всё видели"
    return [f"👁 {who}: розыск {st.heat}."]


# -- dropping it ------------------------------------------------------------------------------------------------------


def _ap(ch: ActiveCharacter, content: Content, cost: int) -> bool:
    from app.services import ap as ap_services

    if not ap_services.can_spend(ch, cost):
        return False
    ap_services.spend(ch, content, cost)
    return True


def hide_pct(content: Content, ch: ActiveCharacter) -> int:
    c = eng.cfg(content)
    st = ch.state
    base = (derived.stat_total(st, content, c.hide_stat) + derived.skill_total(st, content, "stealth")
            + derived.check_bonus(st, content, ("escape",)))
    return round(checks.pass_chance(base, eng.hide_dv(content, st.heat)) * 100)


def hide(content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> str:
    c = eng.cfg(content)
    st = ch.state
    if st.heat <= 0:
        return "Тебя никто не ищет."
    if not _ap(ch, content, c.hide_ap):
        return "Не хватает ОД."
    res = checks.roll_check(st, content, rng, c.hide_stat, "stealth", eng.hide_dv(content, st.heat), ("escape",))
    if res.ok:
        apply_effects(st, {"heat": -c.hide_drop}, content, rng, year)
        rec = st.flags.get(eng.FLAG)
        if isinstance(rec, dict):
            rec = dict(rec)
            rec.update({"pause": time.time() + c.hide_pause_h * 3600, "wave": 0, "acts": 0})
            st.flags[eng.FLAG] = rec
        eng.ensure(st, content, time.time())
        return (f"🕶 Ты залегаешь на дно: чужая квартира, выключенный телефон, никаких лишних движений. "
                f"Розыск {st.heat}." + (" Погоня потеряла след на несколько часов." if st.heat >= c.chase_from else ""))
    rec = st.flags.get(eng.FLAG)
    if isinstance(rec, dict):
        rec = dict(rec)
        rec.update({"next": 0, "pause": 0})
        st.flags[eng.FLAG] = rec
    return "🚨 Не вышло: тебя срисовал дрон. Полиция уже едет."


def _today(tz: str) -> str:
    return kyiv_date(tz)


def change(content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str) -> str:
    c = eng.cfg(content)
    st = ch.state
    if st.heat <= 0:
        return "Тебя никто не ищет."
    if st.flags.get(eng.FLAG_CHANGE_DAY) == _today(tz):
        return "Тачку и шмот сегодня уже меняли."
    price = eng.change_price(content, st.heat)
    if st.eddies < price:
        return "Не хватает эдди."
    if not _ap(ch, content, c.change_ap):
        return "Не хватает ОД."
    apply_effects(st, {"eddies": -price, "heat": -c.change_drop}, content, rng, year)
    st.flags[eng.FLAG_CHANGE_DAY] = _today(tz)
    return f"🧥 Новая куртка, чужие номера, другая походка. Розыск {st.heat}."


def fixer(content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str) -> str:
    c = eng.cfg(content)
    st = ch.state
    if st.heat <= 0:
        return "Тебя никто не ищет."
    if st.heat > c.fixer_max_heat:
        return "За МаксТак фиксеры не берутся: такие дела не закрывают за деньги."
    if st.flags.get(eng.FLAG_FIXER_DAY) == _today(tz):
        return "Фиксер уже закрыл одно дело сегодня. Второе за день не возьмёт."
    price = eng.fixer_price(content, st.heat)
    if st.eddies < price:
        return "Не хватает эдди."
    apply_effects(st, {"eddies": -price, "heat": -st.heat}, content, rng, year)
    st.flags[eng.FLAG_FIXER_DAY] = _today(tz)
    eng.ensure(st, content, time.time())
    return f"📁 Фиксер делает пару звонков. Дело теряется в архиве NCPD. Розыск {st.heat}."


def face(content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> str:
    c = eng.cfg(content)
    st = ch.state
    if st.heat <= 0:
        return "Тебя никто не ищет."
    left = float(st.flags.get(eng.FLAG_FACE_AT, 0) or 0) + c.face_days * 86400 - time.time()
    if left > 0:
        return f"Лицо меняли совсем недавно. Риппер возьмётся снова через {max(1, round(left / 86400))} дн."
    price = eng.face_price(content, st.heat)
    if st.eddies < price:
        return "Не хватает эдди."
    if not _ap(ch, content, c.face_ap):
        return "Не хватает ОД."
    apply_effects(st, {"eddies": -price, "heat": -st.heat, "humanity": -c.face_humanity}, content, rng, year)
    st.flags[eng.FLAG_FACE_AT] = time.time()
    eng.ensure(st, content, time.time())
    return f"🔪 Скулы, глаза, отпечатки, голос. В зеркале чужое лицо. Розыск {st.heat}."
