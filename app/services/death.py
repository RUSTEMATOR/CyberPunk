"""Zero hit points and death as a service (PLAN 7.1, 7.2, DESIGN.md 6, 13).

- `resolve_zero_hp`: the one place that decides what happens at 0 hp (combat, followups, gigs, MaxTac verdicts all go
  through it): Trauma Team, Second Heart, the death save, the glitch offer (the decision waits on the character in
  `FLAG_DEATH_PENDING`, the window leads to it), then `kill`;
- `resolve_offer`: the player's answer to the glitch offer;
- `kill`: the life is closed for good: the graveyard, the lineage count, the public record with the obituary (the city
  feed posts it at once, with a picture when there is one). The legacy screen follows in the player's window.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts, texts_death as T, views
from app.content.loader import Content
from app.db import repo
from app.db.models import Character, EventLog, Gig, GigRun, GraveyardEntry
from app.engine import death as eng
from app.engine import derived
from app.engine import legacy as legacy_engine
from app.engine import psychosis as psy_engine
from app.engine.state import (
    FLAG_COMBAT,
    FLAG_DEATH_PENDING,
    FLAG_GIG_RUN,
    FLAG_LEGACY_PENDING,
    FLAG_MAXTAC_DUTY,
    FLAG_PENDING_CHECK,
    FLAG_PENDING_COMBAT,
    FLAG_PENDING_EVENTS,
    FLAG_PENDING_EXIT,
    FLAG_PSYCHO_CAPTURE,
    FLAG_PSYCHO_CAUGHT,
    CharacterState,
)
from app.narrator import narrator
from app.services import legends, localhistory, narration
from app.services.game import ActiveCharacter, save_active

log = logging.getLogger(__name__)

# what a dead character no longer carries: fights, queues, calls
_DROP_FLAGS = (
    FLAG_COMBAT, FLAG_PENDING_COMBAT, FLAG_PENDING_EXIT, FLAG_PENDING_EVENTS, FLAG_PENDING_CHECK, FLAG_GIG_RUN,
    FLAG_MAXTAC_DUTY, FLAG_PSYCHO_CAPTURE, FLAG_DEATH_PENDING,
)


@dataclass
class ZeroHpResult:
    survived: bool = True  # up and breathing (nothing happened, evacuated, the heart, the save)
    died: bool = False
    pending: bool = False  # the save failed, the glitch offer waits on the character
    cause: str | None = None
    lines: list[str] = field(default_factory=list)


@dataclass
class OfferOutcome:
    survived: bool = False
    died: bool = False
    cause: str | None = None
    lines: list[str] = field(default_factory=list)
    after_lines: list[str] = field(default_factory=list)  # what the interrupted flow (a fight's end) adds now


def survive_at_one(ch: ActiveCharacter) -> bool:
    """A character at or below 0 hp stays at the survive hp. Kept for flows that already hold a decided capture."""
    if ch.state.hp <= 0:
        ch.state.hp = 1
        return True
    return False


# -- causes ---------------------------------------------------------------------------------------------------------------


def cause_for_fight(src: str, faction: str) -> str:
    """The cause of death for a fight that put the character at 0 hp: the yard is prison, a breakdown or a hunt of a
    fugitive is cyberpsychosis, any other MaxTac fight is MaxTac, the rest is combat."""
    if src == "jail":
        return "prison"
    if src == "dgame":
        return "dgame"
    if src in (psy_engine.SRC_BREAKDOWN, psy_engine.SRC_HUNT):
        return "cyberpsychosis"
    if faction == "maxtac" and src != psy_engine.SRC_DUTY:
        return "maxtac"
    return "combat"


def default_cause(st: CharacterState) -> str:
    """Cause for a zero-hp moment outside a fight: a cell is prison, otherwise combat (a beating, an accident)."""
    from app.engine.state import FLAG_JAIL_WEEKS

    return "prison" if int(st.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0 else "wounds"  # owner report 29.10: not a fight


# -- the pending decision --------------------------------------------------------------------------------------------------


def pending_of(ch: ActiveCharacter) -> dict[str, Any] | None:
    raw = ch.state.flags.get(FLAG_DEATH_PENDING)
    return dict(raw) if raw else None


async def has_pending(session: AsyncSession, tg_id: int) -> bool:
    """The player's living character waits for the answer to the glitch offer."""
    cid = await repo.active_character_id(session, tg_id)
    if cid is None:
        return False
    row = await repo.get_character_row(session, cid)
    return bool(row is not None and (row.flags or {}).get(FLAG_DEATH_PENDING))


def _penalty_text(penalty: int) -> str:
    return T.ZH_PENALTY.format(n=penalty) if penalty else ""


def offer_text(ch: ActiveCharacter) -> str:
    p = pending_of(ch) or {}
    return "\n".join([
        T.OFFER_TITLE,
        T.OFFER_TEXT.format(
            roll=p.get("roll", "?"), need=p.get("need", "?"), penalty=_penalty_text(int(p.get("penalty", 0) or 0)),
            glitches=ch.state.glitches,
        ),
    ])


# -- zero hp ----------------------------------------------------------------------------------------------------------------


async def resolve_zero_hp(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    *, cause: str = "combat", source: str = "", hunter: str = "", after: dict[str, Any] | None = None,
    allow_evac: bool = True,
) -> ZeroHpResult:
    """The character fell to 0 hp from `cause` (`source`: where, for the log; `hunter`: who did it, for the obituary).
    `after`: what the interrupted flow must still do when a glitch offer is answered (see `resolve_offer`)."""
    res = ZeroHpResult()
    st = ch.state
    if st.hp > 0:
        return res
    if pending_of(ch) is not None:
        res.survived, res.pending = False, True
        return res
    out = eng.resolve_zero_hp(st, rng, content, year=year, allow_evac=allow_evac)
    from app.bot.ui.callbacks import LAST_ACT  # owner report 29.10: say what brought the health to zero

    act = LAST_ACT.get()
    if act:
        res.lines.append(T.ZH_WHY.format(what=T.act_word(act)))
    saves = st.flags.get(eng.FLAG_DEATH_SAVES) or []
    if len(saves) == 2 and int(saves[1] or 0) >= 2:
        res.lines.append(T.ZH_AGAIN.format(n=int(saves[1])))
    if out.tier_failed and out.tier:
        res.lines.append(T.ZH_EVAC_LATE.format(tier=content.balance.trauma_team[out.tier].name))
    penalty = _penalty_text(out.save.penalty) if out.save else ""
    if out.outcome == eng.EVACUATED:
        assert out.tier is not None
        res.lines.append(T.ZH_EVAC.format(
            tier=content.balance.trauma_team[out.tier].name, price=views.money(out.evac_price), hp=st.hp))
    elif out.outcome == eng.SECOND_HEART:
        res.lines.append(T.ZH_HEART.format(hp=st.hp))
    elif out.outcome == eng.SAVED:
        assert out.save is not None
        res.lines.append(T.ZH_SAVE_OK.format(roll=out.save.roll, need=out.save.need, penalty=penalty, hp=st.hp))
    elif out.save is not None:
        res.lines.append(T.ZH_SAVE_FAIL.format(roll=out.save.roll, need=out.save.need, penalty=penalty))
    await repo.log_event(
        session, "zero_hp",
        {"outcome": out.outcome, "cause": cause, "source": source, "act": act, "tier": out.tier, "evac_price": out.evac_price,
         "heart": out.heart, "roll": out.save.roll if out.save else None, "need": out.save.need if out.save else None},
        character_id=ch.id,
    )
    if out.outcome == eng.OFFER:
        assert out.save is not None
        st.flags[FLAG_DEATH_PENDING] = {
            "cause": cause, "source": source, "hunter": hunter, "repeats": out.repeats, "roll": out.save.roll,
            "need": out.save.need, "penalty": out.save.penalty, "after": after,
        }
        res.survived, res.pending = False, True
        res.lines.append(T.ZH_PENDING)
        await save_active(session, ch, tz)
        return res
    if out.outcome == eng.DEAD:
        saved = await _clone_save(session, content, ch, rng, year, tz, cause)  # owner request 26.10
        if saved is not None:
            res.lines.extend(saved)
            if ch.state.hp > 0:
                return res
        await kill(session, content, ch, cause, tz, year=year, by=hunter)
        res.survived, res.died, res.cause = False, True, cause
        return res
    return res


async def resolve_offer(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, accept: bool,
) -> OfferOutcome:
    """The answer to the glitch offer: spend the glitch and roll the save again, or let go. Raises LookupError
    when no offer waits (a stale button)."""
    p = pending_of(ch)
    if p is None:
        raise LookupError("no death offer")
    st = ch.state
    st.flags.pop(FLAG_DEATH_PENDING, None)
    cause, hunter = str(p.get("cause") or "combat"), str(p.get("hunter") or "")
    out = OfferOutcome(cause=cause)
    if accept and eng.glitch_offer_available(st, content):
        res = eng.accept_glitch(st, rng, content, int(p.get("repeats", 0) or 0), year=year)
        assert res.save is not None
        await repo.log_event(
            session, "death_glitch", {"ok": res.survived, "roll": res.save.roll, "need": res.save.need}, character_id=ch.id,
        )
        if res.survived:
            out.survived = True
            out.lines.append(T.ZH_GLITCH_OK.format(roll=res.save.roll, need=res.save.need, hp=st.hp))
            await save_active(session, ch, tz)
            after = p.get("after")
            if after:
                out.after_lines = await _run_after(session, content, ch, rng, year, tz, after)
            return out
        out.lines.append(T.ZH_GLITCH_FAIL.format(roll=res.save.roll, need=res.save.need))
    saved = await _clone_save(session, content, ch, rng, year, tz, cause)  # owner request 26.10
    if saved is not None:
        out.lines.extend(saved)
        if st.hp > 0:
            out.survived = True
            await save_active(session, ch, tz)
            return out
    await kill(session, content, ch, cause, tz, year=year, by=hunter)
    out.died = True
    return out


async def _clone_save(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                      tz: str, cause: str) -> list[str] | None:
    """Owner request 26.10: the brain moves into a clone of the clone corp (services/clones.py)."""
    from app.services import clones

    if clones.FLAG_READY not in ch.state.flags:
        return None
    lines = clones.clone_save(content, ch, rng, year, cause, await clones.corp(session))
    if ch.state.hp > 0:
        await save_active(session, ch, tz)
    return lines


async def _run_after(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    after: dict[str, Any],
) -> list[str]:
    if after.get("kind") == "fight":
        from app.services import combat  # local: combat imports this module

        lines = await combat.after_fight(
            session, content, ch, rng, year, tz, str(after["src"]), str(after["faction"]), str(after["result"]),
            dict(after.get("ctx") or {}),
        )
        await save_active(session, ch, tz)
        return lines
    if after.get("kind") == "capture":  # a MaxTac duty hunter's target lived through the death rules: he is taken alive
        from app.services import psychosis  # local: psychosis imports this module

        ch.state.flags[FLAG_PSYCHO_CAUGHT] = str(after.get("by") or "")
        return await psychosis.begin_capture(session, content, ch, rng, year, tz, by=str(after.get("by") or ""))
    return []


# -- death ---------------------------------------------------------------------------------------------------------------------


async def _facts(session: AsyncSession, content: Content, ch: ActiveCharacter) -> dict[str, int]:
    """The numbers the main achievement is picked from."""
    st = ch.state
    gigs = (await session.execute(select(func.count()).select_from(GigRun).where(GigRun.character_id == ch.id))).scalar_one()
    fights = 0
    res = await session.execute(select(EventLog.payload).where(EventLog.character_id == ch.id, EventLog.kind == "fight_end"))
    for payload in res.scalars():
        if (payload or {}).get("result") == "won":
            fights += 1
    return {
        "gigs": int(gigs), "fights": fights, "street_cred": st.street_cred, "fame": st.fame, "status": st.status,
        "career_rank": st.career.rank if st.career else 0, "children": len(st.npcs_of("child")),
        "years": st.weeks_lived // content.balance.time.weeks_per_year,
        "wealth": max(0, st.eddies - st.debt_total),
    }


def achievement_text(content: Content, facts: dict[str, int]) -> str:
    key, value = legacy_engine.pick_achievement(content, facts)
    if key == legacy_engine.ACHIEVEMENT_NONE:
        return T.ACHV_NONE
    return T.ACHV[key].format(n=views.money(value), years=views.years(value))


async def kill(
    session: AsyncSession, content: Content, ch: ActiveCharacter, cause: str, tz: str,
    *, year: int | None = None, by: str = "",
) -> None:
    """Close the life: dead for good, the graveyard row, the lineage count, the public record with the obituary.

    `cause` is one of schemas.DEATH_CAUSES; `by` names the hunter of a MaxTac duty call. Safe to call twice.
    The state is saved first so the last values (reputation, wallet of the life) survive in the row."""
    row = await repo.get_character_row(session, ch.id)
    if row is None or not row.alive:
        return
    try:  # owner request 26.10: a dead host lets Capri go, a dead holder drops the chip
        from app.services import capri

        await capri.on_death(session, content, ch)
    except Exception:  # noqa: BLE001 - a death must never fail on this
        log.exception("capri: on_death failed")
    st = ch.state
    st.hp = 0
    for flag in _DROP_FLAGS:
        st.flags.pop(flag, None)
    st.flags[FLAG_LEGACY_PENDING] = True
    facts = await _facts(session, content, ch)
    achv = achievement_text(content, facts)
    role_key = derived.main_role(st, content)
    role_label = legends.roles_label(content, st.role_affinity)
    cause_name = texts.DEATH_CAUSE_NAMES.get(cause, cause)
    payload = {
        "name": f"{st.name} {st.surname}".strip(), "role": role_label, "age": st.age_years, "cause": cause_name,
        "street_cred": st.street_cred, "achievement": achv, "district": st.district, "hunter": by,
    }
    if narrator.is_live():  # the past lives of the lineage, for the obituary (PLAN 8.7)
        payload["lineage_note"] = await narration.legacy_note(session, content, ch)
    obituary = await narrator.narrate("obituary", payload, year=st.current_year)
    if not obituary:
        obituary = payload["name"]
    if by:
        obituary = f"{obituary} {T.OBIT_HUNTER.format(hunter=by)}"
    await save_active(session, ch, tz)
    await repo.mark_dead(session, ch.id, cause)
    start_age = max(0, st.age_years - st.weeks_lived // content.balance.time.weeks_per_year)
    session.add(GraveyardEntry(
        character_id=ch.id, lineage_id=row.lineage_id, name=payload["name"], born_age=start_age, died_age=st.age_years,
        role=role_key, cause=cause, street_cred=st.street_cred, epitaph=achv[:200], obituary_text=obituary, killed_by=by,
    ))
    await session.flush()
    if row.lineage_id is not None:
        lineage = await repo.get_lineage(session, row.lineage_id)
        if lineage is not None:  # lives_count = lives that ended (this one included), counted from the rows
            dead = (await session.execute(
                select(func.count()).select_from(Character).where(Character.lineage_id == row.lineage_id, Character.alive.is_(False))
            )).scalar_one()
            lineage.lives_count = int(dead)
            await localhistory.dynasty_legend(session, content, st, lineage.id, lineage.family_name, int(dead))
    gigs = await session.execute(select(Gig).where(Gig.taken_by == ch.id, Gig.status == "taken"))
    for g in gigs.scalars():
        g.status = "expired"  # nobody is left to finish it
    await repo.log_event(
        session, "death",
        {"name": st.name, "surname": st.surname, "cause": cause, "age": st.age_years, "street_cred": st.street_cred,
         "weeks_lived": st.weeks_lived, "district": st.district, "by": by, "obituary_text": obituary,
         "avatar": (st.flags.get("avatar") or {}).get("file_id") if isinstance(st.flags.get("avatar"), dict) else None},
        character_id=ch.id, public=True,
    )
    await localhistory.death_legend(session, content, ch.id, st, role_label, cause)  # a famous death goes into the history
    log.info("death: character %s (%s %s), cause %s", ch.id, st.name, st.surname, cause)


async def latest_dead_pending(session: AsyncSession, tg_id: int) -> Character | None:
    """The player's newest dead character whose heir is not chosen yet (flag `legacy_pending`), else None.
    Only the newest life counts: a player with a living character has nothing pending."""
    if await repo.active_character_id(session, tg_id) is not None:
        return None
    res = await session.execute(
        select(Character).where(Character.player_tg_id == tg_id).order_by(Character.id.desc()).limit(1)
    )
    row = res.scalar_one_or_none()
    if row is not None and not row.alive and (row.flags or {}).get(FLAG_LEGACY_PENDING):
        return row
    return None


async def revive(session: AsyncSession, content: Content, tg_id: int, tz: str) -> ActiveCharacter | None:
    """Owner request 24.10: bring the player's last dead character back (an admin decision: the Trauma Team after all).
    Refused (None) when the player already lives as someone else. The graveyard row goes, the lineage count is redone,
    the leftovers of the death (the heir choice, the game of the rich) are dropped; hp comes back as after an evacuation."""
    from app.services import game

    if await game.get_active_character(session, tg_id) is not None:
        return None
    row = (await session.execute(
        select(Character).where(Character.player_tg_id == tg_id, Character.alive.is_(False))
        .order_by(Character.died_at.desc(), Character.id.desc()).limit(1)
    )).scalar_one_or_none()
    if row is None:
        return None
    row.alive = True
    row.died_at = None
    row.death_cause = None
    await session.flush()
    ch = await game.load_character(session, row.id)
    if ch is None:
        return None
    st = ch.state
    for flag in (*_DROP_FLAGS, FLAG_LEGACY_PENDING, "dgame", "dg_enter", "sortie", "tstorm"):
        st.flags.pop(flag, None)
    st.hp = eng.revive_hp(st, content)
    await save_active(session, ch, tz)
    for g in (await session.execute(select(GraveyardEntry).where(GraveyardEntry.character_id == row.id))).scalars():
        await session.delete(g)
    await session.flush()
    if row.lineage_id is not None:
        lineage = await repo.get_lineage(session, row.lineage_id)
        if lineage is not None:
            dead = (await session.execute(
                select(func.count()).select_from(Character).where(Character.lineage_id == row.lineage_id, Character.alive.is_(False))
            )).scalar_one()
            lineage.lives_count = int(dead)
    log.info("revive: character %s (%s %s)", ch.id, st.name, st.surname)
    return ch
