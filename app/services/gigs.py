"""Gigs as a service (PLAN 5.1-5.5, 5.7): the board, the brief, playing the checks, settling the outcome.

State of a gig being played lives on the character (`flags["gig_run"]`), like a week's events do:
the checks rolled so far, the one waiting for the luck / glitch / accept decision, and, for a loud
gig that needed a fight, the result of the fight. A closed window therefore loses nothing and can
never re-roll a failed check. Every change of the character goes through `apply_effects`.

Flow: `load_board` -> `brief` -> `start_run` (AP, first check) -> `take_luck` / `take_glitch` /
`take_check` ... -> [fight: services/combat.py -> `fight_finished`] -> `settle_run`.
"""

from __future__ import annotations

import logging
import random
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts_combat as TC
from app.bot import views
from app.content.loader import Content
from app.content.schemas import SKILLS
from app.db import repo
from app.engine import checks, gigs as engine_gigs
from app.engine import events as engine_events
from app.engine.checks import CheckResult
from app.engine.effects import AppliedEffects, apply_effects
from app.engine.state import FLAG_COMBAT, FLAG_CRIME_WEEK, FLAG_GIG_RUN, FLAG_PENDING_COMBAT
from app.narrator import narrator
from app.services import ap as ap_services
from app.services import followups, game, playerdata, worldfx
from app.services import world as world_service
from app.services import game_events as bus
from app.services import localhistory, narration
from app.services import players as players_service
from app.engine import players as engine_players
from app.services.game import ActiveCharacter, save_active

log = logging.getLogger(__name__)

STATUS_OPEN, STATUS_TAKEN, STATUS_DONE, STATUS_EXPIRED = "open", "taken", "done", "expired"
CYBERPSYCHO = "cyberpsycho"


class GigError(Exception):
    """A gig cannot be started or continued (an old button, a closed gig, not enough AP...)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason  # locked | taken | busy | no_ap | gone | not_today


@dataclass
class GigInfo:
    """A gig row detached from the session, with the names the screens need."""

    id: int
    district: str
    fixer: str
    fixer_name: str
    type: str
    tier: int
    pay: int
    target: str
    target_name: str
    template_key: str
    status: str
    title: str
    brief_text: str | None


@dataclass
class Board:
    fixer: str | None  # the district's main fixer (key)
    fixer_name: str | None
    open: list[GigInfo] = field(default_factory=list)
    locked: list[GigInfo] = field(default_factory=list)
    run_pending: bool = False  # a gig is already in progress


@dataclass
class RunStep:
    """One rolled check on screen."""

    index: int
    total: int
    stat: str
    skill: str
    result: CheckResult
    rev: int
    luck_points: int | None  # luck that turns a miss into a pass, if the pool has it
    can_glitch: bool


@dataclass
class GigResult:
    info: GigInfo
    approach: str
    outcome: str
    pay: int
    applied: AppliedEffects
    text: str
    checks: list[CheckResult]
    fight: dict[str, Any] | None = None
    lines: list[str] = field(default_factory=list)
    died: bool = False
    death_cause: str | None = None
    public: bool = False
    backup: dict[str, Any] | None = None  # the hired merc: name, share, cut (docs/EPIC_PLAYERS.md, phase 2)


@dataclass
class RunProgress:
    kind: str  # "check" | "fight" | "done"
    step: RunStep | None = None
    result: GigResult | None = None


# -- names -----------------------------------------------------------------------------------------------


def faction_name(content: Content, key: str) -> str:
    if key in content.gangs:
        return content.gangs[key].name
    if key in content.corps:
        return content.corps[key].name
    return key


def _info(content: Content, row: Any) -> GigInfo:
    template = content.gig_templates.get(row.template_key)
    target_name = faction_name(content, row.target or "")
    forms = engine_gigs.target_forms(content, row.target or "")
    title = engine_gigs.gig_title(template, row.id, forms) if template else row.template_key
    fixer = content.fixers.get(row.fixer)
    return GigInfo(
        row.id, row.district, row.fixer, fixer.name if fixer else row.fixer, row.type, row.tier, row.pay,
        row.target or "", target_name, row.template_key, row.status, title, row.brief_text,
    )


# -- the board ----------------------------------------------------------------------------------------------


def run_of(ch: ActiveCharacter) -> dict[str, Any] | None:
    return ch.state.flags.get(FLAG_GIG_RUN)


def board_stamp(content: Content, week: int) -> str:
    """The stamp of the board a character sees at his own date `week`: it changes every `gigs.board_refresh_weeks` weeks
    of his own time (DESIGN.md 9a: the boards are personal and refresh in personal time)."""
    return f"w{max(0, week) // content.balance.gigs.board_refresh_weeks:06d}"


async def ensure_board(
    session: AsyncSession, content: Content, ch: ActiveCharacter, district: str, year: int, rng: random.Random,
) -> list[Any]:
    """The character's own gigs in the district; the board is made on the first visit after his date moved on to a
    new refresh period (PLAN 5.1). Gigs are generated for his year, his world effects and the wars of his date."""
    tg_id = ch.player_tg_id
    stamp = board_stamp(content, world_service.char_week(ch.state, content))
    rows = await repo.board_gigs(session, tg_id, district, stamp)
    if rows:
        return rows
    await repo.expire_open_gigs(session, tg_id, district, stamp)
    fx = await worldfx.load_fx(session, content, tg_id)
    wars = await worldfx.active_wars(session, content, fx, year)
    own = {str(ch.state.flags["employer"])} if ch.state.career and ch.state.flags.get("employer") else set()
    for d in engine_gigs.generate_board(district, rng, content, year, float(ch.state.street_cred), wars, fx.gig_pay_mult,
                                        exclude=own, rank=engine_gigs.rank_of(ch.state)):
        await repo.add_gig(
            session, district=d.district, fixer=d.fixer, type=d.type, tier=d.tier, pay=d.pay,
            target=d.target, template_key=d.template_key, day=stamp, owner_tg_id=tg_id,
        )
    return await repo.board_gigs(session, tg_id, district, stamp)


async def load_board(
    session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, rng: random.Random,
) -> Board:
    st = ch.state
    rows = await ensure_board(session, content, ch, st.district, year, rng)
    own = str(st.flags.get("employer") or "") if st.career else ""
    open_rows = [r for r in rows if r.status == STATUS_OPEN and not (own and r.target == own)]
    open_idx, locked_idx = engine_gigs.visible_gigs(content, st.street_cred, [r.tier for r in open_rows],
                                                    engine_gigs.rank_of(st))
    fixer = game.district_fixer(content, st.district, year)
    return Board(
        fixer.key if fixer else None, fixer.name if fixer else None,
        [_info(content, open_rows[i]) for i in open_idx],
        [_info(content, open_rows[i]) for i in locked_idx],
        run_of(ch) is not None,
    )


async def get_info(session: AsyncSession, content: Content, gig_id: int) -> GigInfo | None:
    row = await repo.get_gig(session, gig_id)
    return _info(content, row) if row is not None else None


def approach_plans(content: Content, info: GigInfo) -> dict[str, list[engine_gigs.CheckPlan]]:
    """The checks of every approach of a gig, for the brief screen."""
    template = content.gig_templates[info.template_key]
    return {a: engine_gigs.gig_checks(content, template, a, info.tier) for a in template.approaches}


_personal_briefs: dict[tuple[int, int], str] = {}  # (gig, character) -> Claude's brief for a character with a lineage note


async def brief(
    session: AsyncSession, content: Content, ch: ActiveCharacter, gig_id: int,
) -> tuple[GigInfo, str]:
    """The gig and its brief in the fixer's voice, made once and cached in `gigs.brief_text` (PLAN 5.2).

    The cached brief is the same for everybody who opens the gig, so it does not speak to anybody by name. A character
    whose lineage has past lives (PLAN 8.7) gets a brief of their own that may mention the family; Claude's text for
    them is kept in memory only (a restart writes it again), the fallback is never kept."""
    row = await repo.get_gig(session, gig_id)
    if row is None:
        raise GigError("gone")
    info = _info(content, row)
    note = await narration.legacy_note(session, content, ch) if narrator.is_live() else ""
    if note and (row.id, ch.id) in _personal_briefs:
        return info, _personal_briefs[(row.id, ch.id)]
    if row.brief_text and not note:
        return info, row.brief_text
    district = content.districts.get(ch.state.district)
    payload: dict[str, Any] = {
        "kind": "gig_brief",
        "character": {"name": ch.state.name, "role": ch.state.role, "district": district.name if district else ch.state.district},
        "fixer": info.fixer_name,
        "gig": {"title": info.title, "type": info.type, "tier": info.tier, "pay": info.pay, "target": info.target_name},
    }
    if note:
        payload["character"]["lineage_note"] = note
        text = await narrator.generate_text("gig_brief", payload, year=ch.state.current_year)
        if text:
            if len(_personal_briefs) > 256:
                _personal_briefs.clear()
            _personal_briefs[(row.id, ch.id)] = text
            return info, text
        if row.brief_text:
            return info, row.brief_text
    text = await narrator.narrate("gig_brief", payload, year=ch.state.current_year)
    row.brief_text = text
    await session.flush()
    return info, text


# -- playing the checks -------------------------------------------------------------------------------------------


def _step(content: Content, ch: ActiveCharacter, run: dict[str, Any]) -> RunStep:
    i = int(run["i"])
    stat, skill, _dv = run["plans"][i]
    result = CheckResult(**run["cur"])
    offer = engine_events.luck_offer(result, ch.state, content)
    return RunStep(
        i + 1, len(run["plans"]), stat, skill, result, int(run["rev"]),
        offer.points if offer else None, checks.can_glitch_reroll(result, ch.state.glitches),
    )


def current_step(content: Content, ch: ActiveCharacter) -> RunStep | None:
    run = run_of(ch)
    if run is None or run.get("cur") is None or run.get("phase") != "check":
        return None
    return _step(content, ch, run)


def _roll_current(content: Content, ch: ActiveCharacter, run: dict[str, Any], rng: random.Random) -> None:
    i = int(run["i"])
    stat, skill, dv = run["plans"][i]
    plan = engine_gigs.CheckPlan(stat, skill, int(dv))
    backup = run.get("backup")
    assist = 0
    if backup and i < len(backup.get("bases", [])):
        assist = engine_players.assist_extra(ch.state, int(backup["bases"][i]), content, stat, skill)
    result = engine_gigs.roll_gig_check(ch.state, content, rng, plan, run["approach"], run["type"], assist)
    if backup:
        backup["assist"] = assist  # shown on the check screen
    run["cur"] = asdict(result)
    run["rev"] = int(run.get("rev", 0)) + 1


async def start_run(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, tz: str,
    gig_id: int, approach: str, *, skill: str | None = None, plan: str = "", backup: dict[str, Any] | None = None,
) -> RunStep:
    """Take a gig with an approach: claims the gig, spends the AP, rolls the first check.

    The free action (PLAN 8.4) passes `skill`: it replaces the skill of the FIRST check (the stat stays), and `plan`,
    the one-line summary of what the player wrote, which the narrator may refer to in the result."""
    st = ch.state
    if run_of(ch) is not None:
        raise GigError("busy")
    row = await repo.get_gig(session, gig_id)
    if row is not None and row.owner_tg_id not in (None, ch.player_tg_id):  # somebody else's board
        raise GigError("gone")
    if row is None or row.status != STATUS_OPEN or row.district != st.district:
        raise GigError("taken" if row is not None and row.status == STATUS_TAKEN else "gone")
    if not engine_gigs.unlocked(content, st.street_cred, row.tier, engine_gigs.rank_of(st)):
        raise GigError("locked")
    template = content.gig_templates.get(row.template_key)
    if template is None or approach not in template.approaches:
        raise GigError("gone")
    cost = content.balance.gigs.ap_cost
    if not ap_services.can_spend(ch, cost):
        raise GigError("no_ap")
    dep = engine_gigs.deposit(content, row.tier, row.pay)
    if dep and st.eddies < dep:  # owner request 29.10: a contract takes a deposit first
        raise GigError("no_money")
    ap_services.spend(ch, content, cost)
    if dep:
        apply_effects(st, {"eddies": -dep}, content, rng, st.current_year)
    row.status = STATUS_TAKEN
    row.taken_by = ch.id
    plans = engine_gigs.gig_checks(content, template, approach, row.tier)
    if skill in SKILLS and plans:
        plans[0] = engine_gigs.CheckPlan(plans[0].stat, skill, plans[0].dv)
    run: dict[str, Any] = {
        "gig": row.id, "approach": approach, "type": row.type, "i": 0, "rev": 0, "phase": "check",
        "plans": [[p.stat, p.skill, p.dv] for p in plans], "results": [], "cur": None,
        "outcome": None, "fight": None, "deposit": dep,
    }
    if plan:
        run["free"] = {"summary": plan, "skill": skill}
    if backup:  # a merc from the exchange: a snapshot of his stat + skill for every check of the run
        merc = await players_service.merc_snapshot(session, int(backup["char"]))
        if merc is not None:
            run["backup"] = {**backup, "bases": [
                engine_players.merc_base(merc.state, content, p.stat, p.skill) for p in plans]}
    _roll_current(content, ch, run, rng)
    st.flags[FLAG_GIG_RUN] = run
    await save_active(session, ch, tz)
    return _step(content, ch, run)


def _live_run(ch: ActiveCharacter, rev: int) -> dict[str, Any]:
    run = run_of(ch)
    if run is None or run.get("phase") != "check" or run.get("cur") is None or int(run["rev"]) != rev:
        raise GigError("gone")
    return run


async def take_luck(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, rev: int,
) -> RunStep:
    """Spend the luck points that turn the missed check into a pass."""
    run = _live_run(ch, rev)
    result = CheckResult(**run["cur"])
    offer = engine_events.luck_offer(result, ch.state, content)
    if offer is None:
        raise GigError("gone")
    spent = checks.spend_luck(result, offer.points, ch.state.luck_pool, content.balance.luck)
    apply_effects(ch.state, spent.effects, content, rng, year)
    run["cur"] = asdict(spent.result)
    run["rev"] = rev + 1
    ch.state.flags[FLAG_GIG_RUN] = run
    await save_active(session, ch, tz)
    return _step(content, ch, run)


async def take_glitch(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, rev: int,
) -> RunStep:
    """Spend a glitch on a reroll of the die (once per check)."""
    run = _live_run(ch, rev)
    result = CheckResult(**run["cur"])
    if not checks.can_glitch_reroll(result, ch.state.glitches):
        raise GigError("gone")
    re = checks.glitch_reroll(result, ch.state.glitches, rng, content.balance.checks)
    apply_effects(ch.state, re.effects, content, rng, year)
    run["cur"] = asdict(re.result)
    run["rev"] = rev + 1
    ch.state.flags[FLAG_GIG_RUN] = run
    await save_active(session, ch, tz)
    return _step(content, ch, run)


def fight_faction(info_type: str, target: str) -> str:
    """Who the player fights in a loud gig: the target, or a cyberpsycho for a hunt."""
    return CYBERPSYCHO if info_type == "cyberpsycho" else target


async def take_check(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, rev: int,
) -> RunProgress:
    """Accept the rolled check as it stands and go on: the next check, the fight, or the settlement."""
    run = _live_run(ch, rev)
    run["results"].append(run["cur"])
    run["cur"] = None
    run["i"] = int(run["i"]) + 1
    if int(run["i"]) < len(run["plans"]):
        _roll_current(content, ch, run, rng)
        ch.state.flags[FLAG_GIG_RUN] = run
        await save_active(session, ch, tz)
        return RunProgress("check", step=_step(content, ch, run))
    results = [CheckResult(**d) for d in run["results"]]
    outcome = engine_gigs.gig_outcome(results, content)
    run["outcome"] = outcome
    row = await repo.get_gig(session, int(run["gig"]))
    if run["approach"] == "loud" and outcome != "clean" and row is not None:
        run["phase"] = "fight"
        ch.state.flags[FLAG_GIG_RUN] = run
        ch.state.flags[FLAG_PENDING_COMBAT] = {
            "src": "gig", "gig": row.id, "danger": min(row.tier, 5), "faction": fight_faction(row.type, row.target or ""),
        }
        extra = content.balance.gigs.fight_extra_by_tier.get(row.tier, 0)
        if extra:  # a contract above tier 5: more guns than the tier-5 danger table gives
            ch.state.flags[FLAG_PENDING_COMBAT]["extra_foes"] = extra
        if run.get("backup"):  # the merc takes one of the enemies on himself
            ch.state.flags[FLAG_PENDING_COMBAT]["ally"] = str(run["backup"].get("name", ""))
        await save_active(session, ch, tz)
        return RunProgress("fight")
    run["phase"] = "settle"
    ch.state.flags[FLAG_GIG_RUN] = run
    return RunProgress("done", result=await settle_run(session, content, ch, rng, year, tz))


def awaiting_fight(ch: ActiveCharacter) -> bool:
    """The run needs a fight that is pending or going on (the fight screens take over)."""
    run = run_of(ch)
    return bool(run and run.get("phase") == "fight" and run.get("fight") is None)


def needs_settle(ch: ActiveCharacter) -> bool:
    run = run_of(ch)
    return bool(run and (run.get("phase") == "settle" or (run.get("phase") == "fight" and run.get("fight") is not None)))


def fight_finished(ch: ActiveCharacter, summary: dict[str, Any]) -> None:
    """The combat service reports the end of the gig's fight; `settle_run` follows."""
    run = run_of(ch)
    if run is not None:
        run["fight"] = summary
        ch.state.flags[FLAG_GIG_RUN] = run


# -- the settlement ----------------------------------------------------------------------------------------------


def _crime_kind(template_key: str, gig_type: str) -> str:
    if gig_type == "theft":
        return "car_theft" if "car" in template_key else "store_robbery"
    return "assault"


async def settle_run(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> GigResult:
    """Pay out the outcome (PLAN 5.5, 5.7): effects, the gig row, gig_runs, event_log, the bus."""
    st = ch.state
    run = run_of(ch)
    if run is None:
        raise GigError("gone")
    row = await repo.get_gig(session, int(run["gig"]))
    if row is None:
        st.flags.pop(FLAG_GIG_RUN, None)
        raise GigError("gone")
    info = _info(content, row)
    results = [CheckResult(**d) for d in run["results"]]
    approach: str = run["approach"]
    outcome: str = run["outcome"] or engine_gigs.gig_outcome(results, content)
    fight = run.get("fight")
    if fight is not None:
        mapped = content.balance.gigs.after_fight[fight["result"]]
        if mapped != "keep":
            outcome = engine_gigs.worse_outcome(outcome, mapped)
    settlement = engine_gigs.settle(
        st, content, outcome=outcome, tier=info.tier, base_pay=info.pay, fixer=info.fixer, target=info.target,
        approach=approach, passed_skills=[r.skill for r in results if r.ok and r.skill], fought=fight is not None,
    )
    backup = run.get("backup")
    effects = dict(settlement.effects)
    dep = int(run.get("deposit", 0) or 0)  # owner request 29.10: the contract's deposit comes back on a success
    dep_back = dep if outcome in ("clean", "complication") else 0
    if dep_back:
        effects["eddies"] = int(effects.get("eddies", 0)) + dep_back
    cut = 0
    if backup:
        cut = engine_players.merc_cut(settlement.pay, int(backup.get("share", 0)))
        if cut:
            effects["eddies"] = int(effects.get("eddies", 0)) - cut
    applied = apply_effects(st, effects, content, rng, year)
    await playerdata.apply_deltas(session, ch.player_tg_id, applied.player_deltas)
    if backup:
        helped = [r.skill for r in results if r.skill]
        await players_service.send_hire_mail(
            session, content, ch, year, backup, cut=cut, outcome=outcome, title=info.title,
            skill=helped[0] if helped else None)
        backup = {**backup, "cut": cut}

    extra_lines: list[str] = []
    if dep:
        extra_lines.append((TC.GIG_DEPOSIT_BACK if dep_back else TC.GIG_DEPOSIT_LOST).format(sum=views.money(dep)))
    gain = engine_gigs.rank_gain(content, info.tier, outcome)
    if gain:  # the contract rank has no cap; crossing a threshold opens the next tier
        before = engine_gigs.rank_of(st)
        st.flags[engine_gigs.RANK_FLAG] = before + gain
        extra_lines.append(TC.GIG_RANK_GAIN.format(gain=gain, rank=before + gain))
        gb = content.balance.gigs
        for t in sorted(gb.unlock_rank_by_tier):
            if before < gb.unlock_rank_by_tier[t] <= before + gain:
                extra_lines.append(TC.GIG_RANK_OPEN.format(tier=t))
                break

    crime = info.type in engine_gigs.CRIME_TYPES
    if crime:
        st.flags[FLAG_CRIME_WEEK] = True
    st.flags.pop(FLAG_GIG_RUN, None)
    st.flags.pop(FLAG_COMBAT, None)
    st.flags.pop(FLAG_PENDING_COMBAT, None)
    row.status = STATUS_DONE

    public = info.tier >= content.balance.gigs.public_min_tier or outcome == "disaster"
    district = content.districts.get(st.district)
    payload = {
        "kind": "gig_result", "outcome": outcome,
        "character": {"name": st.name, "role": st.role, "district": district.name if district else st.district},
        "fixer": info.fixer_name,
        "gig": {"title": info.title, "type": info.type, "tier": info.tier, "target": info.target_name},
        "approach": approach,
        "checks": [r.as_dict() for r in results],
        "effects": applied.narrator_effects(),
    }
    if run.get("free"):
        payload["free_plan"] = {"summary": run["free"]["summary"]}  # what the player wrote, in one line
    text = await narrator.narrate("gig_result", payload, year=st.current_year)
    fu = await followups.handle(session, content, ch, applied.followups, rng, year, tz)
    res = GigResult(info, approach, outcome, settlement.pay, applied, text, results, fight, extra_lines + fu.lines, fu.died,
                    fu.cause, public, backup)

    await repo.add_gig_run(
        session, gig_id=row.id, character_id=ch.id, approach=approach, checks=[asdict(r) for r in results],
        outcome=outcome, effects=applied.narrator_effects(), result_text=text,
    )
    if not fu.died:
        await save_active(session, ch, tz)
    await repo.log_event(
        session, "gig_result",
        {"gig": row.id, "title": info.title, "tier": info.tier, "type": info.type, "approach": approach,
         "outcome": outcome, "target": info.target, "name": st.name, "effects": applied.narrator_effects(),
         "text": text},
        character_id=ch.id, public=public,
    )
    await localhistory.gig_legend(session, content, ch.id, st, row.id, info.title, info.tier, outcome)
    if public and approach == "loud":
        await _bump_district_heat(session, content, st.district)
    if fu.died:
        return res
    kw = {"player_tg_id": ch.player_tg_id, "character_id": ch.id, "session": session, "ch": ch}
    await bus.publish("gig_done", approach=approach, type=info.type, min_tier=info.tier, outcome=outcome, **kw)
    if crime:
        await bus.publish("crime_done", kind=_crime_kind(info.template_key, info.type),
                          success=outcome in ("clean", "complication"), **kw)
    return res


async def _bump_district_heat(session: AsyncSession, content: Content, district: str) -> None:
    """A loud public gig warms the district (balance.world); the global tick (M10) cools it."""
    w = content.balance.world
    heat = dict(await repo.ws_get(session, "district_heat", {}) or {})
    heat[district] = min(w.district_heat_max, int(heat.get(district, 0)) + w.district_heat_per_public_gig)
    await repo.ws_set(session, "district_heat", heat)


# -- free action (M8) ---------------------------------------------------------------------------------------------


def top_skills(ch: ActiveCharacter, n: int = 3) -> list[str]:
    """The character's best skills (keys), for the context of the free action."""
    ranked = sorted(ch.state.skills.items(), key=lambda kv: -kv[1].level)
    return [k for k, v in ranked[:n] if v.level > 0]


def free_action_available() -> bool:
    """The free-text approach exists only when the real narrator is live (M8); until then its button is hidden."""
    return narrator.is_live()
