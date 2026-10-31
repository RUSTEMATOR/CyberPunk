"""'Live a week' as a service (PLAN 4.2-4.4): the engine week, the week's events, the bus, the world's beats.

`live_week` does everything one press of the button does, in one unit of work, and returns a
`WeekOutcome` for the screens. It never sends anything; the handler turns the outcome into screens.

Time is personal (DESIGN.md 9a): there is no limit of weeks per day and no shared clock to wait for. The week moves
the character's own date; the canon events and the generated beats of the world that his new date reaches are
returned in the outcome (a window screen) and posted to the feed by a public record naming him.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts_life
from app.content.loader import Content
from app.content.schemas import Era
from app.db import repo
from app.engine import derived
from app.engine import events as engine_events
from app.engine import world as world_engine
from app.engine.state import FLAG_BEATS, FLAG_PENDING_EVENTS, FLAG_RECENT_EVENTS, FLAG_WEEK_LEDGER
from app.engine import jail as jail_engine
from app.engine.week import WeekReport, advance_week, merge_reports
from app.services import followups, game, notices, world, worldfx
from app.services import jail as jail_service
from app.services import roles as roles_service
from app.services import game_events as bus
from app.services.game import ActiveCharacter

log = logging.getLogger(__name__)


@dataclass
class WeekOutcome:
    report: WeekReport | None = None
    year: int = 0  # the year the week started in
    notices: list[str] = field(default_factory=list)  # lines from followups of the week (vows, jail, ...)
    died: bool = False
    death_cause: str | None = None
    pending: list[str] = field(default_factory=list)  # event ids to play now, in order
    beats: list[worldfx.Beat] = field(default_factory=list)  # canon events and generated beats the new date reached
    eras: list[Era] = field(default_factory=list)  # eras the new date walked into

    @property
    def lived(self) -> bool:
        return self.report is not None


def pending_events(ch: ActiveCharacter) -> list[str]:
    return list(ch.state.flags.get(FLAG_PENDING_EVENTS) or [])


def append_capped(ch: ActiveCharacter, event_id: str, cap: int = 5) -> None:
    """Owner request 24.10: a story step or a written event joins the queue; over the cap a drawn one makes room
    (the queue keeps forced steps first, so the last one is a random draw)."""
    queue = [e for e in pending_events(ch) if e != event_id]
    if len(queue) >= cap > 1:
        queue = queue[: cap - 1]
    set_pending(ch, queue + [event_id])


def _cool_net_trace(ch: ActiveCharacter, content: Content) -> None:
    from app.clock import kyiv_date
    from app.engine import net as net_engine

    st = ch.state
    v = net_engine.trace_now(st, content, kyiv_date())
    if v > 0:
        net_engine.trace_set(st, v - net_engine.cfg(content).trace_decay_per_month, kyiv_date())


def set_pending(ch: ActiveCharacter, ids: list[str]) -> None:
    if ids:
        ch.state.flags[FLAG_PENDING_EVENTS] = list(ids)
    else:
        ch.state.flags.pop(FLAG_PENDING_EVENTS, None)


def _beat_key(beat: worldfx.Beat) -> str:
    return beat.ref_id or f"{beat.kind}{beat.week}"


async def _deliver_beats(
    session: AsyncSession, content: Content, ch: ActiveCharacter, week0: int, week1: int,
) -> list[worldfx.Beat]:
    """The beats of the world that the character's date reached: each is made real for the group by the first one to
    arrive (`worldfx.reach`) and gets a public record naming him (the feed post).

    `flags[beats_seen]` is how far the chronicle was shown to him. While the narrator still owes a generated beat for a
    date he has passed, the mark stays put (the beats shown since are remembered by id), so a beat written a little late
    reaches him on a later week; once nothing is owed the mark jumps to his date."""
    st = ch.state
    book = dict(st.flags.get(FLAG_BEATS) or {})
    per = content.balance.time.weeks_per_year
    # a jump of the date (an admin edit) shows at most a year back; a date set back forgets what was shown ahead
    seen = min(max(int(book.get("seen", week0)), week0 - per), week0)
    done = {str(x) for x in book.get("done") or []}
    owed = 0
    if week1 > 0:
        owed = await worldfx.ensure_future_beats(session, content, st.current_year, st.age_weeks + 1)
    reached = [b for b in await worldfx.chronicle(session, content) if seen < b.week <= week1 and _beat_key(b) not in done]
    for beat in reached:
        await worldfx.reach(session, content, beat)
        await repo.log_event(
            session, "canon_reached",
            {"name": st.name, "surname": st.surname, "year": beat.year, "title": beat.title, "ref": beat.ref_id,
             "beat": beat.kind, "text": beat.text},
            character_id=ch.id, public=True,
        )
        done.add(_beat_key(beat))
    st.flags[FLAG_BEATS] = {"seen": week1} if owed == 0 else {"seen": seen, "done": sorted(done)}
    if reached:  # the heat and rent multipliers of the new date
        worldfx.refresh_state(st, await worldfx.fx_at(session, content, week1))
    return reached


async def live_week(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, tz: str,
    now: datetime | None = None,
) -> WeekOutcome:
    """Live one week (in jail: one press, several weeks). There is no limit and no clock to wait for.

    Order: engine week -> followups of the week (death ends it) -> the beats of the world that the new date reached ->
    events of the week (saved as pending on the character) -> save -> log -> bus.
    """
    st = ch.state
    e_start = st.eddies  # owner request 25.10: the budget of the month (flag `budget`)
    ledger_in: dict[str, int] = {}
    from app.engine import property as prop_engine

    trip_back = prop_engine.trip_home(st)  # owner request 25.10: a month is lived at home, the trip ends
    week0 = world.char_week(st, content)
    weeks0_maniac = st.weeks_lived  # a kill since the last press leaves a clue
    year = st.current_year
    heat_mult = 1.0  # the world's heat multiplier is on the state (`world_heat_mult`), apply_effects takes it
    district_heat = int(((await repo.ws_get(session, "district_heat", {}) or {}).get(st.district, 0)) or 0)
    jailed = jail_engine.in_jail(st)  # a press in jail lives several weeks and plays no ordinary events
    reports: list[WeekReport] = []
    humanity_before = st.humanity
    out = WeekOutcome(year=year)
    if trip_back:
        out.notices.append("🚕 Поездка окончена: месяц ты живёшь у себя дома.")
    ledger = dict(st.flags.pop(FLAG_WEEK_LEDGER, None) or {})  # what the events since the last report moved
    weeks = jail_engine.press_weeks(st, content) if jailed else content.balance.time.weeks_per_press
    from app.engine.state import FLAG_PENDING_COMBAT
    from app.services import business, gangs  # owner request: your gang's cut, your own business, every week

    gang_lines: list[str] = []
    biz_lines: list[str] = []
    _cool_net_trace(ch, content)  # owner request 24.10: the NetWatch trace cools once a lived month
    for _ in range(weeks):
        was_jailed = jail_engine.in_jail(st)
        was_isolated = jail_engine.isolated(st)
        report = advance_week(
            st, rng, content, st.current_year, now=now, heat_mult=heat_mult, district_heat=district_heat,
        )
        if not reports:
            report.events_eddies = int(ledger.get("eddies", 0))
            report.events_debt = int(ledger.get("debt", 0))
        reports.append(report)
        out.report = merge_reports(reports)
        if was_jailed:
            out.notices.extend(await jail_service.after_week(content, ch, rng, st.current_year, was_isolated))
        if report.new_main_role:
            notices.push(ch, roles_service.main_notice(content, report.new_main_role))
        fu = await followups.handle(session, content, ch, report.followups, rng, st.current_year, tz)
        out.notices.extend(fu.lines)
        if fu.died:
            out.died, out.death_cause = True, fu.cause
            return out
        if not was_jailed and not jail_engine.in_jail(st):
            e0 = st.eddies
            cut = gangs.week_cut(content, ch, rng, st.current_year)
            if cut:
                gang_lines.append(cut)
            _book(ledger_in, "Банда (доля)", st.eddies - e0)
            e0 = st.eddies
            biz_lines.extend(business.week_hook(content, ch, rng, st.current_year))
            _book(ledger_in, "Бизнес", st.eddies - e0)
        # a raid or an arrest stops the press: the fight or the cell is played now, the rest of the month waits
        if st.flags.get(FLAG_PENDING_COMBAT) or (not was_jailed and jail_engine.in_jail(st)):
            break
    report = out.report
    assert report is not None
    from app.engine import upkeep

    upkeep.end_of_press(st)  # the doses of this press wore off
    from app.services import bigmoney

    lost = await bigmoney.dissolve_lost(session, ch)  # the megacorp could not be paid for
    if lost:
        out.notices.append(f"Корпорация «{lost}» обанкротилась: содержать её было нечем.")
    if not jailed:  # owner request: the income of a career path (a clinic, a salary, a cut of the deals)
        from app.engine import paths as paths_engine
        from app.engine.effects import apply_effects

        inc = paths_engine.income(st, content)
        rk, pd = paths_engine.rank_def(st, content), paths_engine.path_of(st, content)
        if inc and rk is not None and pd is not None:
            apply_effects(st, {"eddies": inc}, content, rng, st.current_year)
            report.eddies_delta += inc
            _book(ledger_in, pd.name, inc)
            out.notices.append(f"{pd.name} ({rk.name}): +{inc} эдди за месяц.")
    out.notices.extend(_press_lines(gang_lines, biz_lines, st))
    week1 = world.char_week(st, content)
    out.beats = await _deliver_beats(session, content, ch, week0, week1)
    out.eras = world_engine.eras_entered(content, week0, week1)
    year = st.current_year
    if jailed and st.humanity > humanity_before:  # the therapy of the isolation ward, summed over the press
        out.notices.append(texts_life.JAIL_THERAPY.format(n=f"+{st.humanity - humanity_before}"))

    if not jailed:  # M6.6a: a fugitive's hunt, a MaxTac member's duty call
        from app.services import psychosis

        out.notices.extend(await psychosis.week_hooks(session, content, ch, rng, year, tz))
        from app.services import echoes  # docs/EPIC_PLAYERS.md phase 4: cameos and stashes

        out.notices.extend(await echoes.week_hooks(session, content, ch, rng, year, tz))
        from app.services import maniac  # owner request: the maniac's thirst and the clues

        out.notices.extend(await maniac.press_hook(session, content, ch, rng, year, weeks0_maniac))
        from app.services import city_life  # owner request: avengers, grudges, gratitude, the companion

        out.notices.extend(await city_life.press_hook(session, content, ch, rng, year))
        from app.services import city as city_svc  # owner request 27.10: the tribute of the city's people

        out.notices.extend(await city_svc.tribute_hook(session, content, ch, rng, year))
        out.notices.extend(dict.fromkeys(st.flags.pop("_living_lines", None) or []))  # owner request 23.10: living
        from app.services import oligarch  # owner request 23.10: a megacorp's asset pays and owes

        e0 = st.eddies
        out.notices.extend(oligarch.press_hook(ch, content, rng, year))
        _book(ledger_in, "Корпоративный актив", st.eddies - e0)
        from app.services import dyn  # owner request 23.10: the non-canon crews ambush in their district

        out.notices.extend(await dyn.press_hook(session, content, ch, rng, year))
        from app.services import army  # owner request 25.10: the own fighters: racket and wages

        e0 = st.eddies
        out.notices.extend(army.pay(content, ch, rng, year))
        _book(ledger_in, "Свои бойцы: рэкет и жалованье", st.eddies - e0)
        from app.services import pguard  # owner request 25.10: the wages of the players in the guard

        e0 = st.eddies
        out.notices.extend(await pguard.pay(session, content, ch, rng, year))
        _book(ledger_in, "Охрана из игроков", st.eddies - e0)
        from app.services import control  # owner request 25.10: the district held by gangs bites, the regime raids

        out.notices.extend(await control.press_hook(session, content, ch, rng, year))
        from app.services import turf  # owner request 25.10: the districts held by players: tax and law

        e0 = st.eddies
        from app.services import escort  # owner request 25.10: the bodyguard contract

        out.notices.extend(await escort.press_hook(session, content, ch, rng, year))
        _book(ledger_in, "Охрана корпоратов", st.eddies - e0)
        e0 = st.eddies
        from app.services import pmc  # owner request 25.10: the PMC's retainer

        out.notices.extend(await pmc.press_hook(session, content, ch, rng, year))
        _book(ledger_in, "ЧВК: содержание", st.eddies - e0)
        e0 = st.eddies
        out.notices.extend(await turf.press_hook(session, content, ch, rng, year))
        _book(ledger_in, "Районы: налог", st.eddies - e0)
        from app.services import tearz  # owner request 26.10: Raven Tearz and Life in Tears in «Связи» once met

        tearz.ensure_contacts(st)
        from app.services import possession  # owner request 26.10: the AI in the head

        out.notices.extend(await possession.press_hook(session, content, ch, rng, year))
        from app.services import deeds  # owner request 26.10: the street talk, the own deeds come back

        out.notices.extend(await deeds.press_hook(session, content, ch, rng, year))
        from app.services import romance  # owner request 25.10: life in a relationship, scandals

        out.notices.extend(await romance.press_hook(session, content, ch, rng, year))
        from app.services import wear  # owner request 25.10: the price of power

        out.notices.extend(await wear.press_hook(session, content, ch))
        from app.services import pressure  # owner request 24.10: the threat director, a constant challenge

        out.notices.extend(await pressure.press_hook(session, content, ch, rng, year))
        from app.services import equilib as equilib_svc  # owner request 26.10: the balance of the three forces

        out.notices.extend(await equilib_svc.press_hook(session, content, ch, rng, year))
        from app.services import elysium as elysium_svc  # owner request: the «Elysium» crossover

        out.notices.extend(elysium_svc.press_lines(ch, content, rng))
        from app.services import libria as libria_svc  # owner request 23.10: the «Equilibrium» crossover

        out.notices.extend(libria_svc.press_lines(ch, content, rng, year))
        from app.services import abroad as abroad_svc  # owner request 23.10: life in another country

        out.notices.extend(abroad_svc.press_lines(ch, content, rng, year))
        from app.services import hubs as hubs_svc  # owner request 23.10: the shared home, the crew headquarters

        out.notices.extend(await hubs_svc.press_lines(session, content, ch, rng, year))
        from app.engine import property as prop_engine  # owner request 24.10: a talking weapon has its say

        for wkey in prop_engine.equipped_weapons(st):
            wpn = content.weapons.get(wkey)
            if wpn is not None and wpn.voice is not None and wpn.voice.idle:
                out.notices.append(f"🗣 {wpn.name}: «{rng.choice(wpn.voice.idle)}»")
                break
        from app.services import pcorp as pcorp_svc  # owner request 24.10: players hired by a player's megacorp

        out.notices.extend(await pcorp_svc.press_lines(session, content, ch, rng, year))
        from app.services import crisis as crisis_svc  # owner request 24.10: the city crisis weighs on everyone

        out.notices.extend(await crisis_svc.press(session, content, ch, rng, year))
        from app.engine import chrono as chrono_engine  # owner request: the «In Time» crossover

        if chrono_engine.has(st):
            left = chrono_engine.weeks(st)
            out.notices.append(f"⏳ Часы Эона: осталось {chrono_engine.label(st)}"
                               + (" Время на исходе: работа, биржа или покер в «Город» → «Эон»."
                                  if left < content.balance.chrono.warn_weeks else ""))
        st.ap = derived.ap_per_press(st, content)  # owner request: the AP of every week of the press at once
        report.ap = st.ap
    queued = pending_events(ch)  # events the followups of this week put there (humanity warnings)
    drawn = await _draw_events(session, content, ch, report, rng, year, jailed=jailed)
    fresh = [e for e in drawn if e not in queued]
    room = max(0, content.balance.events.max_per_press - len(queued))
    keep = [e for e in fresh if e in report.forced_events] + [e for e in fresh if e not in report.forced_events]
    out.pending = queued + keep[:max(room, sum(1 for e in fresh if e in report.forced_events))]
    set_pending(ch, out.pending)
    _save_budget(content, st, report, ledger_in, st.eddies - e_start)
    await game.save_active(session, ch, tz)

    await repo.log_event(
        session, "week_lived",
        {"week": report.weeks_lived, "salary": report.salary, "rent": report.rent + report.food,
         "interest": report.interest, "paid": report.debt_paid, "date": week1, "events": list(out.pending),
         "eddies": report.eddies_delta, "events_eddies": report.events_eddies,
         "later": [{"ok": r.ok, "eddies": r.applied.deltas.get("eddies", 0) if r.applied else 0} for r in report.later]},
        character_id=ch.id,
    )
    kw = {"player_tg_id": ch.player_tg_id, "character_id": ch.id, "session": session, "ch": ch}
    for _ in range(report.span):  # quests count lived weeks, and a press lives several
        await bus.publish("week_lived", heat=ch.state.heat, mood=ch.state.mood, **kw)
    for vow_key in report.vow_weeks:
        await bus.publish("vow_week", vow=vow_key, **kw)
    if report.debt_paid:
        await bus.publish("debt_paid", **kw)
    return out


def _book(ledger: dict[str, int], label: str, n: int) -> None:
    if n:
        ledger[label] = ledger.get(label, 0) + n


def _save_budget(content: Content, st, report, ledger: dict[str, int], total: int) -> None:  # noqa: ANN001
    """Owner request 25.10 «в минуса ухожу из-за всего»: where the money of the lived month went (flag `budget`)."""
    items = dict(ledger)
    up = report.upkeep
    for label, n in (("Зарплата", report.salary), ("Жильё и еда", -(report.rent + report.food)),
                     ("Trauma Team", -report.trauma),
                     ("Выплата долгов", -report.debt_paid),
                     ("Образ жизни", -(up.lifestyle_paid if up else 0)), ("Прислуга", -(up.staff_paid if up else 0)),
                     ("Иммуноподавители", -(up.immuno_paid if up else 0)),
                     ("Империя", (up.empire_income - up.empire_paid) if up else 0)):
        _book(items, label, n)
    rest = total - sum(items.values())
    _book(items, "Прочее", rest)
    st.flags["budget"] = {"week": st.weeks_lived, "total": total,
                          "items": sorted(([k, v] for k, v in items.items()), key=lambda x: x[1])}


def _press_lines(gang_lines: list[str], biz_lines: list[str], st) -> list[str]:  # noqa: ANN001
    """The gang cut and the business of every week of a press, folded into one line each (incidents kept)."""
    out: list[str] = []
    if gang_lines:
        import re

        total = sum(int(m.group(1)) for x in gang_lines if (m := re.search(r"\+(\d+)", x)))
        head = gang_lines[0].rsplit(":", 1)[0]
        out.append(f"{head}: +{total} эдди" + (f" за {len(gang_lines)} нед." if len(gang_lines) > 1 else "."))
    net = int(st.flags.pop("_biz_net", 0) or 0)
    summ = st.flags.pop("_biz_sum", None) or {}
    if summ.get("weeks"):  # owner report 24.10: one summary for all businesses and all weeks of the press
        w = int(summ["weeks"])
        out.append(f"🏪 Бизнес ({summ.get('n', 0)}): {'+' if net >= 0 else '−'}{views_money(abs(net))} эдди"
                   + (f" за {w} нед." if w > 1 else " за неделю."))
        from collections import Counter

        for note, by_id in (summ.get("notes") or {}).items():
            cnt = Counter((by_id or {}).values()).most_common()
            parts = [f"{t} ×{k}" if k > 1 else t for t, k in cnt]
            shown = ", ".join(parts[:3]) + (f" и ещё {len(parts) - 3}" if len(parts) > 3 else "")
            out.append(f"⚠️ {note[0].upper() + note[1:]}: {shown}.")
        if summ.get("inc"):
            out.append(f"💥 Происшествий: {summ['inc']}, потери {views_money(int(summ.get('inc_loss', 0)))} эдди."
                       + (f" Последнее: {biz_lines[-1]}" if biz_lines else ""))
        if summ.get("notes"):
            out.append("Бизнес → «Обслужить все», чтобы закупить, выдать премии и починить разом.")
    return out


def views_money(n: int) -> str:
    from app.bot.views import money

    return money(n)


async def _draw_events(
    session: AsyncSession, content: Content, ch: ActiveCharacter, report: WeekReport, rng: random.Random, year: int,
    *, jailed: bool = False,
) -> list[str]:
    """The events of the week: forced ones (the collector) first, then weighted draws.

    The count is 1-3 by `events.extra_chances`. The category weights are the `event_weight` of the world effects at
    the character's own date."""
    if jailed:  # one press in jail: a prison event now and then, never the street ones
        count = len(report.forced_events) + (1 if rng.random() < content.balance.jail.event_chance else 0)
        return engine_events.pick_events(
            ch.state, content, year, rng, count, forced=report.forced_events,
            recent=list(ch.state.flags.get(FLAG_RECENT_EVENTS) or []), only_requires="in_jail",
            extra_flags={"in_jail"},  # the press that releases still plays a prison event
        )
    rolled = sum(engine_events.roll_event_count(rng, content) for _ in range(max(1, report.span)))
    cap = content.balance.events.max_per_press  # owner request 24.10: five at most
    count = max(min(rolled, cap), len(report.forced_events))  # owner request: the events of every week of the press
    fx = await worldfx.load_fx(session, content, ch.player_tg_id)
    return engine_events.pick_events(
        ch.state, content, year, rng, count, forced=report.forced_events,
        recent=list(ch.state.flags.get(FLAG_RECENT_EVENTS) or []), category_mult=dict(fx.event_weight),
    )
