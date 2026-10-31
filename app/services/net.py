"""The Net of the netrunners (owner request 23.10): runs, the trace, the payouts.

A run lives in the character flag `net_run` = {"kind", "t" (tier), "b" (breach) | "d" (ICE duel)} until it is over.
Kinds: gig (a fixer's job: eddies), ncpd (wipe the wanted level), corp (an ICE duel for a corp server), blackwall
(a dive to the Blackwall: big money, humanity, a chance of the contact that opens the Blackwall market).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.db import repo
from app.db.models import PlayerMail
from app.engine import checks, derived, roles
from app.engine import combat as cb
from app.engine import net as ne
from app.engine.effects import AppliedEffects, apply_effects
from app.engine.state import FLAG_COMBAT, FLAG_PENDING_COMBAT
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

FLAG_RUN = "net_run"
KINDS = ("gig", "ncpd", "corp", "blackwall")
HUNT_SRC = "net_hunt"


@dataclass
class NetResult:
    blocked: str | None = None
    lines: list[str] = field(default_factory=list)
    applied: AppliedEffects | None = None
    check: object | None = None
    hunt: bool = False
    over: bool = False


def run_of(ch: ActiveCharacter) -> dict | None:
    r = ch.state.flags.get(FLAG_RUN)
    return r if isinstance(r, dict) else None


def blocked(ch: ActiveCharacter, content: Content, year: int, kind: str) -> str | None:
    st = ch.state
    c = ne.cfg(content)
    if int(st.flags.get("jail_weeks", 0) or 0) > 0:
        return "jail"
    if not ne.has_deck(st, content):
        return "deck"
    if st.flags.get(FLAG_PENDING_COMBAT) or st.flags.get(FLAG_COMBAT):
        return "fight"
    if kind == "blackwall" and year < c.blackwall_from:
        return "noblackwall"
    if kind == "ncpd" and st.heat <= 0:
        return "noheat"
    if not ap_services.can_spend(ch, int(c.ap.get(kind, 1))):
        return "ap"
    return None


def _add_trace(ch: ActiveCharacter, content: Content, n: int) -> int:
    today = kyiv_date()
    v = ne.trace_now(ch.state, content, today) + ne.trace_cost(ch.state, content, n)
    ne.trace_set(ch.state, v, today)
    return v


async def clean(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                paid: bool) -> NetResult:
    """Owner report 24.10: the trace can be knocked down at once: by own hands (INT + netrunning, needs a deck)
    or by a fixer's cleaner for eddies (anyone)."""
    c = ne.cfg(content)
    st = ch.state
    today = kyiv_date()
    v = ne.trace_now(st, content, today)
    if v <= 0:
        return NetResult(blocked="notrace")
    if int(st.flags.get("jail_weeks", 0) or 0) > 0:
        return NetResult(blocked="jail")
    res = NetResult(over=True)
    if paid:
        if st.eddies < c.cleaner_price:
            return NetResult(blocked="eddies")
        cut = min(v, c.cleaner_cut)
        res.applied = apply_effects(st, {"eddies": -c.cleaner_price}, content, rng, year)
        res.lines = [f"Чистильщик фиксера переписывает логи провайдеров и камер. След −{cut}."]
    else:
        if not ne.has_deck(st, content):
            return NetResult(blocked="deck")
        if not ap_services.can_spend(ch, c.clean_ap):
            return NetResult(blocked="ap")
        ap_services.spend(ch, content, c.clean_ap)
        chk = checks.roll_check(st, content, rng, "INT", "netrunning", c.clean_dv)
        res.check = chk
        cut = min(v, c.clean_cut if chk.ok else c.clean_fail_cut)
        res.lines = [f"Ты подчищаешь логи, гоняешь трафик через чужие узлы и подкидываешь ложные следы. След −{cut}."
                     if chk.ok else f"Половина логов уже ушла в NetWatch. Удаётся стереть немного: след −{cut}."]
        if chk.ok:
            res.applied = apply_effects(st, {"skill_xp:netrunning": 1}, content, rng, year)
    ne.trace_set(st, v - cut, today)
    await save_active(session, ch, tz)
    return res


def _hunt_if_due(ch: ActiveCharacter, content: Content, year: int) -> bool:
    c = ne.cfg(content)
    st = ch.state
    today = kyiv_date()
    if ne.trace_now(st, content, today) < c.trace_limit or st.flags.get(FLAG_PENDING_COMBAT):
        return False
    faction = "netwatch" if cb._faction_pool(content, "netwatch", year) else "ncpd"
    st.flags[FLAG_PENDING_COMBAT] = {"src": HUNT_SRC, "faction": faction, "danger": c.hunt_danger}
    ne.trace_set(st, c.trace_after_hunt, today)
    return True


async def start(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                kind: str) -> NetResult:
    if kind not in KINDS:
        return NetResult(blocked="bad")
    if run_of(ch) is not None:
        return NetResult()  # the run in progress is shown
    why = blocked(ch, content, year, kind)
    if why:
        return NetResult(blocked=why)
    c = ne.cfg(content)
    st = ch.state
    ap_services.spend(ch, content, int(c.ap.get(kind, 1)))
    _add_trace(ch, content, int(c.trace.get(kind, 10)))
    t = ne.tier(st, content)
    if kind == "corp":
        st.flags[FLAG_RUN] = {"kind": kind, "t": t, "d": ne.new_duel(st, content, rng, t)}
    else:
        buf = ne.buffer_size(st, content) - (1 if kind == "blackwall" else 0)
        st.flags[FLAG_RUN] = {"kind": kind, "t": t, "b": ne.new_breach(rng, content, max(3, buf), 3)}
    await save_active(session, ch, tz)
    return NetResult()


async def pick(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               r: int, k: int) -> NetResult:
    run = run_of(ch)
    if run is None or "b" not in run:
        return NetResult(blocked="norun")
    b = run["b"]
    why = ne.pick(b, r, k)
    if why:
        return NetResult(blocked=why)
    ch.state.flags[FLAG_RUN] = run
    res = NetResult()
    if b["over"]:
        res = _finish(content, ch, rng, year, run)
    await save_active(session, ch, tz)
    return res


async def move(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               what: str) -> NetResult:
    run = run_of(ch)
    if run is None or "d" not in run or what not in (ne.ATTACK, ne.SHIELD, ne.BYPASS):
        return NetResult(blocked="norun")
    d = run["d"]
    lines = ne.duel_move(d, what, ch.state, content, rng)
    ch.state.flags[FLAG_RUN] = run
    res = NetResult(lines=list(lines))
    if d["over"]:
        fin = _finish(content, ch, rng, year, run)
        fin.lines = lines + fin.lines
        res = fin
    await save_active(session, ch, tz)
    return res


async def jack_out(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                   tz: str) -> NetResult:
    run = run_of(ch)
    if run is None:
        return NetResult(blocked="norun")
    if "b" in run:
        run["b"]["over"] = True
    else:
        run["d"]["over"] = run["d"]["over"] or "out"
    res = _finish(content, ch, rng, year, run)
    await save_active(session, ch, tz)
    return res


def _hurt(ch: ActiveCharacter, n: int) -> int:
    """Brain damage never kills on its own: at most down to 1 hp."""
    return max(0, min(n, ch.state.hp - 1))


def _finish(content: Content, ch: ActiveCharacter, rng: random.Random, year: int, run: dict) -> NetResult:
    c = ne.cfg(content)
    st = ch.state
    st.flags.pop(FLAG_RUN, None)
    kind, t = run["kind"], int(run["t"])
    top = content.balance.gigs.pay_by_tier[t][1]
    fx: dict = {}
    lines: list[str] = []
    good = False
    if "b" in run:
        done = ne.solved(run["b"])
        good = bool(done)
        if kind == "gig":
            pay = sum(c.gig_pay_pct[i] for i in done) * top // 100
            if pay:
                fx["eddies"] = pay
                lines.append(f"Заказ фиксера выполнен: демонов загружено {len(done)} из 3. Оплата {pay} эдди.")
        elif kind == "ncpd":
            if 2 in done:
                fx["heat"] = -st.heat
                lines.append("Твоё дело в базе NCPD стёрто целиком. Розыска больше нет.")
            elif done:
                fx["heat"] = -min(st.heat, len(done))
                lines.append(f"Часть записей NCPD стёрта: розыск −{min(st.heat, len(done))}.")
        elif kind == "blackwall":
            fx["humanity"] = -c.blackwall_humanity
            bwc = content.balance.blackwall  # owner request 24.10: every dive deepens the taint
            fx["bw_rot"] = bwc.dive_rot if done else bwc.dive_fail_rot
            if done:
                pay = int(sum(c.gig_pay_pct[i] for i in done) * top * c.blackwall_pay_mult // 100)
                fx["eddies"] = pay
                lines.append(f"Из-за Заслона удаётся вытащить обрывки чужого кода. Брокеры платят {pay} эдди не торгуясь.")
                if 2 in done and not st.flags.get("blackwall_contact") and rng.randint(1, 100) <= c.blackwall_contact_pct:
                    fx["flag_set:blackwall_contact"] = True
                    lines.append("Что-то по ту сторону запомнило тебя. Теперь с тобой будут торговать скриптами Заслона.")
            else:
                hurt = _hurt(ch, 8)
                if hurt:
                    fx["hp"] = -hurt
                lines.append("Заслон отвечает. Из носа идёт кровь, ты падаешь из Сети на пол.")
            if rng.randint(1, 100) <= c.blackwall_glitch_pct:
                fx["glitches"] = 1
                lines.append("В голове остался чужой шёпот. Глюк +1.")
            lines.append(f"Человечность −{c.blackwall_humanity}: Заслон всегда берёт свою долю.")
        if done:
            fx["skill_xp:netrunning"] = len(done)
        else:
            _add_trace(ch, content, c.trace_fail)
            if kind != "blackwall":
                lines.append(f"Ни один демон не загружен. Система подняла тревогу: след +{ne.trace_cost(st, content, c.trace_fail)}.")
    else:
        d = run["d"]
        if d["over"] == "won":
            good = True
            loot = int(top * c.corp_loot_mult)
            fx.update({"eddies": loot, "skill_xp:netrunning": 3, "street_cred": c.corp_cred})
            lines.append(f"ЛЕД рассыпается. Сервер корпорации открыт: счета, чертежи, компромат. Продано за {loot} эдди.")
        elif d["over"] == "flat":
            _add_trace(ch, content, c.trace_fail)
            lines.append("ЛЕД выжег тебе мозги до плоской линии. Деку пришлось выдёргивать руками.")
        else:
            _add_trace(ch, content, c.trace_fail // 2)
            lines.append("Ты отключился, не дожав ЛЕД. Сервер закрылся и запомнил твой почерк.")
        if d.get("trace"):
            _add_trace(ch, content, int(d["trace"]))
        hurt = _hurt(ch, int(d.get("taken", 0)) // max(1, c.ice.hp_loss_div))
        if hurt:
            fx["hp"] = -hurt
    res = NetResult(over=True)
    if fx:
        res.applied = apply_effects(st, fx, content, rng, year)
    if good:
        roles.grow(st, content, "hack", {})
    res.hunt = _hunt_if_due(ch, content, year)
    if res.hunt:
        lines.append("След стал полным. NetWatch сдал твой адрес, отряд уже у дверей.")
    res.lines = lines
    return res


# -- the dossier on another player -------------------------------------------------------------------------------------


async def targets(session: AsyncSession, ch: ActiveCharacter) -> list[tuple[int, str]]:
    rows = await repo.living_characters(session)
    return [(r.id, f"{r.name} {r.surname or ''}".strip()) for r in rows if r.id != ch.id]


async def dossier(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                  target_id: int) -> NetResult:
    from app.services import game

    why = blocked(ch, content, year, "dossier")
    if why:
        return NetResult(blocked=why)
    other = await game.load_character(session, target_id)
    if other is None or other.id == ch.id:
        return NetResult(blocked="gone")
    c = ne.cfg(content)
    ap_services.spend(ch, content, int(c.ap.get("dossier", 1)))
    _add_trace(ch, content, int(c.trace.get("dossier", 10)))
    o = other.state
    dv = c.dossier_dv + derived.skill_total(o, content, "netrunning") + derived.mod(o, content, "ice")
    chk = checks.roll_check(ch.state, content, rng, "INT", "netrunning", dv, ("gig_hack",))
    res = NetResult(check=chk, over=True)
    who = f"{o.name} {o.surname}".strip()
    if chk.ok:
        res.applied = apply_effects(ch.state, {"skill_xp:netrunning": 1}, content, rng, year)
        roles.grow(ch.state, content, "hack", {})
        from app.bot.texts import STAT_LABELS

        stats = ", ".join(f"{STAT_LABELS.get(k, k)} {derived.stat_total(o, content, k)}"
                          for k in ("BODY", "REF", "INT", "COOL", "TECH", "EMP"))
        chrome = [content.cyberware[i.key].name for i in o.cyberware if i.key in content.cyberware]
        res.lines = [
            f"Досье: {who}, {o.age_years} лет. Район: "
            f"{content.districts[o.district].name if o.district in content.districts else o.district}.",
            f"Характеристики: {stats}.",
            f"Здоровье {o.hp}/{derived.max_hp(o, content)}, человечность {o.humanity}, розыск {o.heat}/5.",
            f"Счёт: {o.eddies} эдди.",
            "Хром: " + (", ".join(chrome[:12]) if chrome else "чистый, без имплантов") + ".",
        ]
        noticed = rng.randint(1, 100) <= c.dossier_notice_pct
    else:
        _add_trace(ch, content, c.trace_fail)
        res.lines = [f"Защита {who} сработала: тебя выкинуло, след +{ne.trace_cost(ch.state, content, c.trace_fail)}."]
        noticed = True
    if noticed:
        session.add(PlayerMail(
            to_tg_id=other.player_tg_id, from_tg_id=0, from_name="Сеть", kind="city", to_char_id=other.id,
            payload={"text": f"Кто-то копался в твоих данных через Сеть. В логах остался след: {ch.state.name}."}))
        res.lines.append(f"{o.name} это заметит: в логах осталось твоё имя.")
    res.hunt = _hunt_if_due(ch, content, year)
    if res.hunt:
        res.lines.append("След стал полным. NetWatch сдал твой адрес, отряд уже у дверей.")
    await save_active(session, ch, tz)
    return res
