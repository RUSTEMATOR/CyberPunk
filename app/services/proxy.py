"""A hired player does the employer's gig in his place (owner request 29.10, docs/PLAN_PROXY.md).

The checks and the fight run on a copy of the hired character (nobody saves it). The employer gets the gig: money, cred,
fame, rank, the fixer's opinion. The hired player gets a letter of kind `proxy` with his pay for the days of work and the
consequences (applied when he opens his window, health never below 1): wounds, lost humanity, heat, a faction's anger,
mood and the experience of the skills he used. Playerdata `proxy_day` = {"d": Kyiv date, "n": sendings today}.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts_proxy as TX
from app.clock import kyiv_date
from app.content.loader import Content
from app.db import repo
from app.db.models import PlayerMail
from app.engine import combat as cb
from app.engine import derived
from app.engine import gigs as engine_gigs
from app.engine import jail as jail_eng
from app.engine import proxy as eng
from app.engine.checks import CheckResult
from app.engine.effects import AppliedEffects, apply_effects
from app.engine.state import FLAG_COMBAT
from app.services import ap as ap_services
from app.services import followups, game, playerdata
from app.services import game_events as bus
from app.services import gigs as gigs_service
from app.services import localhistory, pcorp, pguard
from app.services.game import ActiveCharacter, save_active

KIND = "proxy"
KEY_DAY = "proxy_day"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.players.proxy


@dataclass
class Hand:
    """One hired player the employer can send."""

    tg: int
    char: int
    name: str
    role: str  # guard | staff
    wage: int  # eddies a month
    why: str = ""  # '' = can be sent now
    hp: int = 0
    max_hp: int = 0


def _name(ch: ActiveCharacter) -> str:
    return f"{ch.state.name} {ch.state.surname}".strip()


def _same_city(content: Content, a: str, b: str) -> bool:
    da, db_ = content.districts.get(a), content.districts.get(b)
    return da is not None and db_ is not None and da.city == db_.city


async def _sent_today(session: AsyncSession, tg: int) -> int:
    rec = await playerdata.get(session, tg, KEY_DAY, {}) or {}
    return int(rec.get("n", 0) or 0) if rec.get("d") == kyiv_date() else 0


async def _unread(session: AsyncSession, tg: int) -> int:
    res = await session.execute(select(func.count()).select_from(PlayerMail).where(
        PlayerMail.to_tg_id == tg, PlayerMail.kind == KIND, PlayerMail.delivered_at.is_(None)))
    return int(res.scalar() or 0)


async def roster(session: AsyncSession, content: Content, ch: ActiveCharacter) -> list[Hand]:
    """The employer's bodyguards and corporation staff with the reason each cannot be sent now ('' = free)."""
    from app.services import bigmoney

    raw: list[tuple[int, int, str, str, int]] = []
    for gtg, e in (await pguard.guards_of(session, ch.player_tg_id)).items():
        raw.append((int(gtg), int(e.get("char", 0) or 0), str(e.get("name", "")), "guard", int(e.get("wage", 0) or 0)))
    corp = (await bigmoney.corps(session)).get(str(ch.player_tg_id))
    if corp is not None:
        weeks = content.balance.time.weeks_per_press
        for tg, e in pcorp.staff_of(corp).items():
            p = pcorp.post(content, int(e.get("pos", 0)))
            raw.append((int(tg), int(e.get("char", 0) or 0), str(e.get("name", "")), "staff", (p.weekly if p else 0) * weeks))
    out: list[Hand] = []
    seen: set[int] = set()
    for tg, cid, name, role, wage in raw:
        if tg in seen or tg == ch.player_tg_id:
            continue
        seen.add(tg)
        h = Hand(tg, cid, name, role, wage)
        g = await game.get_active_character(session, tg)
        if g is None or (cid and g.id != cid):
            h.why = "gone"
        else:
            h.name = _name(g)
            st = g.state
            h.hp, h.max_hp = st.hp, derived.max_hp(st, content)
            if jail_eng.in_jail(st):
                h.why = "jail"
            elif st.flags.get(FLAG_COMBAT) or st.hp <= 0:
                h.why = "fight"
            elif not _same_city(content, st.district, ch.state.district):
                h.why = "city"
            elif await _sent_today(session, tg) >= cfg(content).per_day:
                h.why = "day"
            elif await _unread(session, tg) >= cfg(content).mail_cap:
                h.why = "mail"
            h.char = g.id
        out.append(h)
    return out


async def has_hands(session: AsyncSession, ch: ActiveCharacter) -> bool:
    """The employer has somebody hired (a bodyguard or a member of the staff): the button shows."""
    from app.services import bigmoney

    if await pguard.guards_of(session, ch.player_tg_id):
        return True
    corp = (await bigmoney.corps(session)).get(str(ch.player_tg_id))
    return corp is not None and bool(pcorp.staff_of(corp))


@dataclass
class Sent:
    blocked: str | None = None
    hand: Hand | None = None
    info: gigs_service.GigInfo | None = None
    approach: str = ""
    outcome: str = ""
    pay: int = 0
    fee: int = 0
    days: int = 0
    fight: dict[str, Any] | None = None
    applied: AppliedEffects | None = None  # the employer's part
    hurt: dict[str, int] = field(default_factory=dict)  # the hired player's part (what he gets by letter)
    lines: list[str] = field(default_factory=list)
    died: bool = False
    death_cause: str | None = None
    public: bool = False
    text: str = ""


def hurt_text(content: Content, fx: dict[str, Any]) -> str:
    """The hired player's part in words: «здоровье −12, человечность −2, розыск +1»."""
    parts: list[str] = []
    for k, v in fx.items():
        if isinstance(v, bool) or not int(v):
            continue
        base, _, arg = k.partition(":")
        n = int(v)
        if base == "faction_rep":
            name = gigs_service.faction_name(content, arg)
            parts.append(f"{name} {n:+d}".replace("-", "−"))
        elif base == "skill_xp":
            continue
        elif base in TX.RU_FX:
            parts.append(f"{TX.RU_FX[base]} {n:+d}".replace("-", "−"))
    return ", ".join(parts)


async def send(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    gig_id: int, approach: str, merc_tg: int,
) -> Sent:
    """Send a hired player to the employer's gig. Everything is settled at once."""
    c = cfg(content)
    res = Sent(approach=approach)
    st = ch.state
    if not c.enabled:
        res.blocked = "off"
        return res
    if gigs_service.run_of(ch) is not None:
        res.blocked = "busy"
        return res
    hand = next((h for h in await roster(session, content, ch) if h.tg == merc_tg), None)
    if hand is None:
        res.blocked = "gone"
        return res
    res.hand = hand
    if hand.why:
        res.blocked = hand.why
        return res
    row = await repo.get_gig(session, gig_id)
    if row is not None and row.owner_tg_id not in (None, ch.player_tg_id):
        row = None
    if row is None or row.status != gigs_service.STATUS_OPEN or row.district != st.district:
        res.blocked = "taken" if row is not None and row.status == gigs_service.STATUS_TAKEN else "gone"
        return res
    if not engine_gigs.unlocked(content, st.street_cred, row.tier, engine_gigs.rank_of(st)):
        res.blocked = "locked"
        return res
    template = content.gig_templates.get(row.template_key)
    if template is None or approach not in template.approaches:
        res.blocked = "gone"
        return res
    cost = content.balance.gigs.ap_cost
    if not ap_services.can_spend(ch, cost):
        res.blocked = "no_ap"
        return res
    dep = engine_gigs.deposit(content, row.tier, row.pay)
    res.days = eng.days_of(content, row.tier)
    res.fee = eng.fee(content, hand.wage, row.tier)
    if st.eddies < dep + res.fee:
        res.blocked = "no_money"
        return res
    merc = await game.get_active_character(session, hand.tg)
    if merc is None:
        res.blocked = "gone"
        return res
    res.info = gigs_service._info(content, row)  # noqa: SLF001

    # the employer pays: action points, the deposit of a contract, the pay of the hired player
    ap_services.spend(ch, content, cost)
    apply_effects(st, {"eddies": -(dep + res.fee)}, content, rng, year)
    row.status = gigs_service.STATUS_TAKEN
    row.taken_by = merc.id

    # the checks and the fight on the hired player's copy
    plans = engine_gigs.gig_checks(content, template, approach, row.tier)
    results: list[CheckResult] = [
        engine_gigs.roll_gig_check(merc.state, content, rng, p, approach, row.type) for p in plans]
    outcome = engine_gigs.gig_outcome(results, content)
    hp_lost = 0
    hum_lost = 0
    if approach == "loud" and outcome != "clean":
        hp0 = merc.state.hp
        ctx: dict[str, Any] = {"src": "gig_proxy", "gig": row.id}
        extra = content.balance.gigs.fight_extra_by_tier.get(row.tier, 0)
        if extra:
            ctx["extra_foes"] = min(extra, 5)
        from app.engine import abilities as ab_eng
        from app.engine import ab_power, perks

        ctx["perks"] = perks.specials(merc.state)
        for k, v in ab_eng.specials(merc.state, content).items():
            ctx["perks"][k] = int(ctx["perks"].get(k, 0)) + v
        ctx["chrome_n"] = len(merc.state.cyberware)
        cs = cb.new_combat(merc.state, content, gigs_service.fight_faction(row.type, row.target or ""),
                           min(row.tier, 5), rng, year, ctx)
        ab_power.prime(cs, merc.state, content)
        res.fight = eng.auto_fight(cs, content, rng, eng.rec_of(merc.state))
        hp_lost = max(0, hp0 - max(1, cs.player.hp))
        hum_lost = int(cs.humanity_due or 0)
        mapped = content.balance.gigs.after_fight[res.fight["result"]]
        if mapped != "keep":
            outcome = engine_gigs.worse_outcome(outcome, mapped)
    res.outcome = outcome

    # the settlement: the employer's contract (his fixer, his pay modifiers), split between the two
    settlement = engine_gigs.settle(
        st, content, outcome=outcome, tier=row.tier, base_pay=row.pay, fixer=row.fixer, target=row.target or "",
        approach=approach, passed_skills=[r.skill for r in results if r.ok and r.skill], fought=res.fight is not None)
    fx: dict[str, int | bool] = dict(settlement.effects)
    if res.fight is not None:
        if hp_lost:
            fx["hp"] = -hp_lost
        if hum_lost:
            fx["humanity"] = -hum_lost
    boss_fx, hand_fx = eng.split_effects(fx)
    dep_back = dep if outcome in ("clean", "complication") else 0
    if dep_back:
        boss_fx["eddies"] = int(boss_fx.get("eddies", 0)) + dep_back
    res.pay = settlement.pay
    res.applied = apply_effects(st, boss_fx, content, rng, year)
    await playerdata.apply_deltas(session, ch.player_tg_id, res.applied.player_deltas)
    extra_lines: list[str] = []
    if dep:
        from app.bot import texts_combat as TC
        from app.bot import views

        extra_lines.append((TC.GIG_DEPOSIT_BACK if dep_back else TC.GIG_DEPOSIT_LOST).format(sum=views.money(dep)))
    gain = engine_gigs.rank_gain(content, row.tier, outcome)
    if gain:
        from app.bot import texts_combat as TC

        before = engine_gigs.rank_of(st)
        st.flags[engine_gigs.RANK_FLAG] = before + gain
        extra_lines.append(TC.GIG_RANK_GAIN.format(gain=gain, rank=before + gain))
        gb = content.balance.gigs
        for t in sorted(gb.unlock_rank_by_tier):
            if before < gb.unlock_rank_by_tier[t] <= before + gain:
                extra_lines.append(TC.GIG_RANK_OPEN.format(tier=t))
                break
    fu = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
    res.lines = extra_lines + fu.lines
    res.died, res.death_cause = fu.died, fu.cause

    # the hired player's side: the letter with the pay and the consequences
    crime = row.type in engine_gigs.CRIME_TYPES
    res.hurt = {k: int(v) for k, v in hand_fx.items() if not isinstance(v, bool)}
    sums = hurt_text(content, hand_fx)
    session.add(PlayerMail(
        to_tg_id=hand.tg, from_tg_id=ch.player_tg_id, from_char_id=ch.id, to_char_id=merc.id, from_name=_name(ch),
        from_year=year, kind=KIND,
        payload={"eddies": res.fee, "fx": res.hurt, "crime": crime, "title": res.info.title, "outcome": outcome,
                 "days": res.days, "sum": sums, "g": "m"}))
    rec = await playerdata.get(session, hand.tg, KEY_DAY, {}) or {}
    today = kyiv_date()
    await playerdata.put(session, hand.tg, KEY_DAY, {"d": today, "n": (int(rec.get("n", 0) or 0) + 1) if rec.get("d") == today else 1})

    row.status = gigs_service.STATUS_DONE
    res.public = row.tier >= content.balance.gigs.public_min_tier or outcome == "disaster"
    res.text = TX.OUTCOME_TEXT[outcome].format(name=hand.name)
    await repo.add_gig_run(
        session, gig_id=row.id, character_id=merc.id, approach=approach, checks=[r.as_dict() for r in results],
        outcome=outcome, effects={"proxy_for": ch.id, **{k: v for k, v in boss_fx.items() if not isinstance(v, bool)}},
        result_text=res.text)
    await repo.log_event(
        session, "gig_result",
        {"gig": row.id, "title": res.info.title, "tier": row.tier, "type": row.type, "approach": approach,
         "outcome": outcome, "target": row.target, "name": hand.name, "proxy_for": _name(ch), "text": res.text},
        character_id=merc.id, public=res.public)
    await localhistory.gig_legend(session, content, merc.id, merc.state, row.id, res.info.title, row.tier, outcome)
    if res.public and approach == "loud":
        await gigs_service._bump_district_heat(session, content, st.district)  # noqa: SLF001
    if not res.died:
        await save_active(session, ch, tz)
        await bus.publish("gig_done", approach=approach, type=row.type, min_tier=row.tier, outcome=outcome,
                          player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch)
    return res
