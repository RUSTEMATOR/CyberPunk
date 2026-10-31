"""Cyberpsychosis as a service (PLAN 6.6-6.6b): the breakdown fight, the fugitive life, the capture and its verdict.

- `start_breakdown`: humanity hit 0 (the `cyberpsychosis` followup): a fight with the NCPD, MaxTac lands on tick 2;
- `fight_over`: how a breakdown, a MaxTac hunt or a duty call ended (called from services/combat.py `_finish`);
- `begin_capture` / `resolve_capture`: a caught psycho is either offered a MaxTac contract (the recruit roll) or goes
  to the isolation ward; the verdict waits on the character in `FLAG_PSYCHO_CAPTURE`;
- `ensure_fugitive` / `week_hooks`: a fugitive loses home and job and is hunted every `hunt_weeks`; a MaxTac member
  gets a duty call every `duty_weeks` (services/duty part below).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts_chrome as T
from app.bot import texts_death as DT
from app.content.loader import Content
from app.db import repo
from app.engine import exits
from app.engine import jail as jail_engine
from app.engine import psychosis as eng
from app.engine.dice import roll
from app.engine.effects import apply_effects, apply_remove_cyberware
from app.engine.state import (
    FLAG_COMBAT,
    FLAG_EMPLOYER,
    FLAG_FUGITIVE,
    FLAG_JAIL_ISOLATION,
    FLAG_JAIL_WEEKS,
    FLAG_MAXTAC_CONTRACT,
    FLAG_MAXTAC_DUTY,
    FLAG_MAXTAC_DUTY_AT,
    FLAG_PENDING_COMBAT,
    FLAG_PENDING_EXIT,
    FLAG_PSYCHO_CAPTURE,
    FLAG_PSYCHO_CAUGHT,
    FLAG_PSYCHO_HUNT_AT,
    FLAG_WORK_HARD,
)
from app.services import death, followups
from app.services.game import ActiveCharacter, load_character, save_active


# -- the breakdown -----------------------------------------------------------------------------------------------


async def start_breakdown(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> list[str]:
    """Humanity fell to 0. In the street: the NCPD pack, MaxTac on tick 2 (the fight waits on the character, the window
    leads to it). In a cell: the ward takes him, no fight. Already in a fight: the fight goes on, the episode is not repeated."""
    st = ch.state
    await repo.log_event(
        session, "cyberpsychosis", {"name": st.name, "surname": st.surname, "district": st.district},
        character_id=ch.id, public=True,
    )
    if jail_engine.in_jail(st):
        st.flags[FLAG_JAIL_ISOLATION] = True
        st.flags[FLAG_JAIL_WEEKS] = jail_engine.weeks_left(st) + content.balance.cyberpsychosis.isolation_weeks.min
        return [T.PSY_START_WARD]
    if st.flags.get(FLAG_COMBAT):
        return []
    from app.services import combat

    cfg = content.balance.cyberpsychosis
    await combat.begin(
        session, content, ch, rng, year, tz, faction="ncpd", danger=cfg.breakdown_ncpd_danger,
        src=eng.breakdown_ctx(content, year),
    )
    return [T.PSY_START]


async def fight_over(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    src: str, result: str, ctx: dict,
) -> list[str]:
    """A breakdown, a hunt or a duty call ended with `result` (won | fled | avoided | surrendered | downed).
    Returns the lines for the closing screen."""
    if src == eng.SRC_DUTY:
        return await duty_over(session, content, ch, rng, year, tz, result, ctx)
    if result in ("downed", "surrendered"):
        return await begin_capture(session, content, ch, rng, year, tz)
    lines: list[str] = []
    if src == eng.SRC_BREAKDOWN:
        lines.append(T.PSY_ESCAPED_WON if result == "won" else T.PSY_ESCAPED_FLED)
    else:
        lines.append(T.PSY_HUNT_WON if result == "won" else T.PSY_HUNT_FLED)
    lines.extend(await ensure_fugitive(session, content, ch, rng, year, tz, quiet=True))
    return lines


# -- the fugitive ----------------------------------------------------------------------------------------------------


def is_fugitive(ch: ActiveCharacter) -> bool:
    return bool(ch.state.flags.get(FLAG_FUGITIVE))


async def ensure_fugitive(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    *, quiet: bool = False,
) -> list[str]:
    """Make the fugitive state whole (idempotent): the flag, the heat floor, no home, no job, the first hunt scheduled.
    The flag may come from the breakdown, an event or a desertion; `quiet` skips the "you lost" lines."""
    st = ch.state
    cfg = content.balance.cyberpsychosis
    lines: list[str] = []
    if not st.flags.get(FLAG_FUGITIVE):
        apply_effects(st, {"flag_set:fugitive_psycho": True}, content, rng, year)
    if st.heat < cfg.fugitive_heat:
        apply_effects(st, {"heat": cfg.fugitive_heat - st.heat}, content, rng, year)
    if st.career is not None:
        apply_effects(st, {"career_clear": True}, content, rng, year)
        for flag in (FLAG_WORK_HARD, FLAG_EMPLOYER, FLAG_MAXTAC_CONTRACT, FLAG_MAXTAC_DUTY_AT):
            st.flags.pop(flag, None)
        if not quiet:
            lines.append(T.PSY_FUGITIVE_JOB_LOST)
    if st.housing:
        st.housing = ""
        if not quiet:
            lines.append(T.PSY_FUGITIVE_HOME_LOST)
    if FLAG_PSYCHO_HUNT_AT not in st.flags:
        st.flags[FLAG_PSYCHO_HUNT_AT] = st.weeks_lived + cfg.hunt_weeks
    return lines


def _clear_fugitive(content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> None:
    """The fugitive life is over (a contract or the ward): the flag, the hunts and the file are gone."""
    st = ch.state
    st.flags.pop(FLAG_PSYCHO_HUNT_AT, None)
    st.flags.pop(FLAG_PSYCHO_CAPTURE, None)
    st.flags.pop(FLAG_PENDING_COMBAT, None)
    st.flags.pop(FLAG_PENDING_EXIT, None)
    st.flags.pop(FLAG_COMBAT, None)
    apply_effects(st, {"flag_clear:fugitive_psycho": True}, content, rng, year)
    if st.heat:
        apply_effects(st, {"heat": -st.heat}, content, rng, year)


# -- the capture -------------------------------------------------------------------------------------------------------


def capture_of(ch: ActiveCharacter) -> dict | None:
    cap = ch.state.flags.get(FLAG_PSYCHO_CAPTURE)
    return dict(cap) if cap else None


async def begin_capture(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    by: str = "",
) -> list[str]:
    """MaxTac has the character alive. The recruit roll is made here and kept, so closing the window changes nothing."""
    st = ch.state
    for flag in (FLAG_COMBAT, FLAG_PENDING_COMBAT, FLAG_PENDING_EXIT):
        st.flags.pop(flag, None)
    if st.hp <= 0:
        death.survive_at_one(ch)
    recruited = eng.roll_recruit(st, content, rng)
    st.flags[FLAG_PSYCHO_CAPTURE] = {"recruited": recruited, "by": by}
    await repo.log_event(
        session, "psycho_caught", {"name": st.name, "surname": st.surname, "recruited": recruited, "by": by},
        character_id=ch.id, public=True,
    )
    await save_active(session, ch, tz)
    return [T.PSY_CAPTURED]


@dataclass
class CaptureResult:
    kind: str = ""  # contract | isolation
    lines: list[str] = field(default_factory=list)
    died: bool = False
    cause: str | None = None
    weeks: int = 0
    stripped: list[str] = field(default_factory=list)
    verdict: str = ""  # arrested | executed (a psycho who was not recruited)


async def resolve_capture(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, choice: str,
) -> CaptureResult:
    """The verdict. `choice`: "contract" (only for the recruited) or "isolation"; a psycho who was not offered a
    contract gets the MaxTac table (arrested | executed) and goes to the ward whichever way the death rule falls."""
    cap = capture_of(ch)
    if cap is None:
        raise LookupError("no capture")
    recruited = bool(cap.get("recruited"))
    if choice == "contract" and not recruited:
        raise LookupError("no contract offered")
    if choice == "contract":
        return await _sign_contract(session, content, ch, rng, year, tz)
    res = CaptureResult("isolation")
    if not recruited:
        sr = exits.surrender(ch.state, content, rng, "maxtac")
        res.verdict = "executed" if sr.outcome == "executed" else "arrested"
        if res.verdict == "executed":
            ch.state.hp = 0
            zr = await death.resolve_zero_hp(session, content, ch, rng, year, tz, cause="cyberpsychosis", source="maxtac_verdict")
            if zr.died:
                res.died, res.cause = True, zr.cause
                return res
            res.lines.append(T.PSY_VERDICT_EXECUTED)
            res.lines.extend(zr.lines)
    return await _to_isolation(session, content, ch, rng, year, tz, res)


async def _to_isolation(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    res: CaptureResult,
) -> CaptureResult:
    st = ch.state
    res.stripped = eng.strip_picks(st, content, rng)
    for key in res.stripped:
        apply_remove_cyberware(st, content, key, rng, year)
    _clear_fugitive(content, ch, rng, year)
    res.weeks = eng.isolation_weeks(content, rng)
    applied = apply_effects(st, {"jail_weeks": res.weeks}, content, rng, year)
    fu = await followups.handle(session, content, ch, applied.followups, rng, year, tz)
    res.lines.extend(fu.lines)
    st.flags[FLAG_JAIL_ISOLATION] = True
    await repo.log_event(
        session, "psycho_ward", {"name": st.name, "surname": st.surname, "weeks": res.weeks, "stripped": len(res.stripped)},
        character_id=ch.id, public=True,
    )
    await save_active(session, ch, tz)
    return res


async def _sign_contract(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> CaptureResult:
    st = ch.state
    cfg = content.balance.cyberpsychosis
    res = CaptureResult("contract")
    _clear_fugitive(content, ch, rng, year)
    apply_effects(st, {f"cyberware_grant:{eng.INHIBITOR}": True, "career_set:maxtac": True}, content, rng, year)
    if st.career is None or st.career.key != eng.CAREER:  # the career is not on the market in this year: sign anyway
        from app.engine.state import CareerState

        st.career = CareerState(eng.CAREER, 1, 0)
    back = cfg.humanity_after_contract - st.humanity
    if back:
        apply_effects(st, {"humanity": back}, content, rng, year)
    st.flags[FLAG_MAXTAC_CONTRACT] = eng.contract_end(st, content)
    st.flags[FLAG_MAXTAC_DUTY_AT] = st.weeks_lived + cfg.duty_weeks
    res.lines.append(T.PSY_CONTRACT_SIGNED)
    await repo.log_event(
        session, "maxtac_recruit", {"name": st.name, "surname": st.surname}, character_id=ch.id, public=True,
    )
    await save_active(session, ch, tz)
    return res


async def surrender_to_maxtac(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> list[str]:
    """A fugitive walks into a MaxTac precinct on his own: the same capture as after a lost fight."""
    return await begin_capture(session, content, ch, rng, year, tz)


# -- the week and the duty ---------------------------------------------------------------------------------------------


async def week_hooks(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> list[str]:
    """After the engine week: a fugitive gets his hunt when it is due, a MaxTac member his duty call."""
    st = ch.state
    cfg = content.balance.cyberpsychosis
    lines: list[str] = []
    if jail_engine.in_jail(st):
        return lines
    if st.flags.get(FLAG_FUGITIVE):
        lines.extend(await ensure_fugitive(session, content, ch, rng, year, tz))
        due = st.flags.get(FLAG_PSYCHO_HUNT_AT)
        if due is not None and st.weeks_lived >= int(due) and not st.flags.get(FLAG_COMBAT):
            st.flags[FLAG_PSYCHO_HUNT_AT] = st.weeks_lived + cfg.hunt_weeks
            st.flags[FLAG_PENDING_COMBAT] = {
                "src": eng.SRC_HUNT, "danger": content.balance.crime.maxtac_danger, "faction": "maxtac",
            }
            lines.append(T.PSY_HUNT)
            await repo.log_event(
                session, "psycho_hunt", {"name": st.name, "surname": st.surname}, character_id=ch.id, public=True,
            )
    elif st.career is not None and st.career.key == eng.CAREER:
        due = st.flags.get(FLAG_MAXTAC_DUTY_AT)
        if due is None:  # a member who joined the career by the normal route gets his first call too
            st.flags[FLAG_MAXTAC_DUTY_AT] = st.weeks_lived + cfg.duty_weeks
        elif eng.duty_due(st, due) and not st.flags.get(FLAG_MAXTAC_DUTY) and not st.flags.get(FLAG_COMBAT):
            st.flags[FLAG_MAXTAC_DUTY_AT] = st.weeks_lived + cfg.duty_weeks
            lines.extend(await _issue_duty(session, content, ch, rng))
    else:
        st.flags.pop(FLAG_MAXTAC_DUTY_AT, None)
        st.flags.pop(FLAG_MAXTAC_DUTY, None)
    return lines


# -- the duty call --------------------------------------------------------------------------------------------------------


async def _pick_player_target(session: AsyncSession, ch: ActiveCharacter, rng: random.Random,
                              content: Content | None = None) -> ActiveCharacter | None:
    """Another player's living fugitive character, chosen at random."""
    pool: list[ActiveCharacter] = []
    for row in await repo.living_characters(session):
        if row.player_tg_id == ch.player_tg_id or row.id == ch.id:
            continue
        other = await load_character(session, row.id)
        if other is not None and eng.duty_eligible_target(other.state) and (content is None or _here(content, other, ch)):
            pool.append(other)
    return rng.choice(pool) if pool else None


def _here(content: Content, other: ActiveCharacter, ch: ActiveCharacter) -> bool:
    """Owner request 30.10: MaxTac hunts a fugitive only in the officer's own city."""
    from app.engine import place

    return place.same_place(other.state, ch.state, content)


async def _issue_duty(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random) -> list[str]:
    """Put a call on the character: a cyberpsycho of the archetypes, or (sometimes) another player's fugitive."""
    cfg = content.balance.cyberpsychosis
    call: dict = {"kind": "psycho", "danger": eng.duty_danger(content, rng)}
    if rng.random() < cfg.duty_player_target_chance:
        target = await _pick_player_target(session, ch, rng, content)
        if target is not None:
            call = {"kind": "player", "danger": cfg.duty_danger.max, "target_id": target.id,
                    "target_name": f"{target.state.name} {target.state.surname}".strip(),
                    "target_district": target.state.district}
    ch.state.flags[FLAG_MAXTAC_DUTY] = call
    await repo.log_event(session, "maxtac_duty", {"kind": call["kind"]}, character_id=ch.id)
    return [T.DUTY_CALLED]


def duty_of(ch: ActiveCharacter) -> dict | None:
    call = ch.state.flags.get(FLAG_MAXTAC_DUTY)
    return dict(call) if call else None


async def accept_duty(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
):  # noqa: ANN201 - returns a combat Step
    """Ride out: a fight straight away (no exits screen: the order is the order)."""
    from app.services import combat

    call = duty_of(ch)
    if call is None:
        raise LookupError("no duty call")
    st = ch.state
    st.flags.pop(FLAG_MAXTAC_DUTY, None)
    src = {"src": eng.SRC_DUTY, "duty": call["kind"], "target_id": call.get("target_id"),
           "target_name": call.get("target_name", "")}
    foes = None
    if call["kind"] == "player":
        target = await load_character(session, int(call["target_id"]))
        if target is not None and eng.duty_eligible_target(target.state):
            foes = [eng.player_as_foe(target.state, content)]
        else:  # he is already gone (caught, jailed, dead): the call turns into an ordinary one
            src.update(duty="psycho", target_id=None, target_name="")
    return await combat.begin(
        session, content, ch, rng, year, tz, faction="cyberpsycho", danger=int(call["danger"]), src=src, foes=foes)


async def refuse_duty(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> tuple[list[str], bool, str | None]:
    """The order is refused (or the fight was run from). With the inhibitor: a discharge. Without: standing and mood.
    Returns (lines, died, cause)."""
    st = ch.state
    cfg = content.balance.cyberpsychosis
    st.flags.pop(FLAG_MAXTAC_DUTY, None)
    if eng.has_leash(st):
        dmg = roll(cfg.refusal_shock_damage, rng)
        applied = apply_effects(st, {"hp": -dmg, "humanity": -cfg.refusal_shock_humanity}, content, rng, year)
        lines = [T.DUTY_REFUSED_SHOCK.format(dmg=dmg)]
    else:
        applied = apply_effects(
            st, {"street_cred": -cfg.refusal_plain_street_cred, "mood": -cfg.refusal_plain_mood}, content, rng, year)
        lines = [T.DUTY_REFUSED_PLAIN]
    fu = await followups.handle(session, content, ch, applied.followups, rng, year, tz)
    lines.extend(fu.lines)
    await repo.log_event(session, "maxtac_refusal", {"leash": eng.has_leash(st)}, character_id=ch.id)
    if not fu.died:
        await save_active(session, ch, tz)
    return lines, fu.died, fu.cause


async def duty_over(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    result: str, ctx: dict,
) -> list[str]:
    """A duty fight ended. Won: pay and standing (more for a player target, who is then taken). Downed: nothing, the
    surviving rule of the death stub. Left in any other way: the order was refused."""
    st = ch.state
    cfg = content.balance.cyberpsychosis
    player_call = ctx.get("duty") == "player" and ctx.get("target_id")
    if result == "won":
        pay = cfg.duty_pay_player if player_call else cfg.duty_pay
        apply_effects(st, {"eddies": pay, "street_cred": cfg.duty_cred}, content, rng, year)
        lines = [T.DUTY_WON.format(pay=f"{pay:,}".replace(",", " "), cred=cfg.duty_cred)]
        if player_call:
            lines.insert(0, await _take_player_target(
                session, content, rng, year, tz, ch, int(ctx["target_id"]), killed=int(ctx.get("kills") or 0) > 0))
        return lines
    if result == "downed":
        return [T.DUTY_LOST]
    if cfg.duty_fled_as_refusal:
        lines, _died, _cause = await refuse_duty(session, content, ch, rng, year, tz)
        return [T.DUTY_LEFT, *lines]
    return [T.DUTY_LEFT_FREE]


async def _take_player_target(
    session: AsyncSession, content: Content, rng: random.Random, year: int, tz: str, hunter: ActiveCharacter, target_id: int,
    killed: bool = False,
) -> str:
    """The hunted character is taken alive: a capture on his side, with the hunter's name. When the hunter's fight
    ended with a kill, the target is at 0 hp instead and goes through the death rules (Trauma Team, the Second Heart,
    the save, a glitch); if he lives through them he is taken alive after all, if not the obituary names the hunter."""
    target = await load_character(session, target_id)
    name = f"{hunter.state.name} {hunter.state.surname}".strip()
    if target is None or not eng.duty_eligible_target(target.state) or not _here(content, target, hunter):
        return T.DUTY_TARGET_GONE  # owner request 30.10: the fugitive who left the city is out of reach
    if killed:
        target.state.hp = 0
        zr = await death.resolve_zero_hp(
            session, content, target, rng, year, tz, cause="maxtac", source="duty_hunt", hunter=name,
            after={"kind": "capture", "by": name},
        )
        who = T.esc(f"{target.state.name} {target.state.surname}".strip())
        if zr.died:
            return DT.DUTY_TARGET_KILLED.format(name=who)
        if zr.pending:
            return DT.DUTY_TARGET_DYING.format(name=who)
    target.state.flags[FLAG_PSYCHO_CAUGHT] = name
    await begin_capture(session, content, target, rng, year, tz, by=name)
    return T.DUTY_TARGET_TAKEN.format(name=T.esc(f"{target.state.name} {target.state.surname}".strip()))
