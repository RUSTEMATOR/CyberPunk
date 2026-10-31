"""What the engine leaves to the service after `apply_effects`: fights, jail, death (PLAN 4.4).

A fight (M5), jail and a raid (M9), death (M7) are real:
- `start_combat`: a pending fight on the character (exits, then services/combat.py);
- `jail`: the arrest (services/jail.py): the cell, the gear in the stash; the term runs down in advance_week;
- `vanish`: the captive weeks of a vanished surrender (services/jail.py);
- `death`: the life is closed (services/death.py::kill: graveyard, obituary, lineage);
- `death_save`: hp 0 goes through Trauma Team, the Second Heart, the save and the glitch offer (services/death.py);
- `cyberpsychosis`: the breakdown fight (services/psychosis.py, M6.6); `humanity_warning` queues a warning event.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts, texts_life
from app.content.loader import Content
from app.db import repo
from app.engine.effects import (
    FU_CYBERPSYCHOSIS,
    FU_DEATH,
    FU_DEATH_SAVE,
    FU_HUMANITY_WARNING,
    FU_JAIL,
    FU_RAID,
    FU_START_COMBAT,
    FU_VANISH,
    FU_VOW_BREAK,
    Followup,
)
from app.engine.state import FLAG_PENDING_COMBAT, FLAG_PENDING_EVENTS
from app.services import death
from app.services.game import ActiveCharacter, save_active

log = logging.getLogger(__name__)


@dataclass
class FollowupOutcome:
    lines: list[str] = field(default_factory=list)
    died: bool = False
    cause: str | None = None
    combat_danger: int | None = None  # a fight still waiting (only with defer_combat)
    pending: bool = False  # the death save failed: the glitch offer waits on the character


async def kill_character(
    session: AsyncSession, content: Content, ch: ActiveCharacter, cause: str, tz: str, *, by: str = "",
) -> None:
    """Close the life (services/death.py: the graveyard, the obituary, the lineage)."""
    await death.kill(session, content, ch, cause, tz, by=by)


WARNING_EVENTS = {"low": "humanity_warning_low", "critical": "humanity_warning_critical"}  # content/events_chrome.yaml


def queue_event(content: Content, flags: dict, event_id: str) -> None:
    """Put a forced event at the end of the week's queue (the event being played stays first)."""
    if event_id not in content.events:
        return
    pending = list(flags.get(FLAG_PENDING_EVENTS) or [])
    if event_id not in pending:
        pending.append(event_id)
        flags[FLAG_PENDING_EVENTS] = pending


async def handle(
    session: AsyncSession, content: Content, ch: ActiveCharacter, followups: list[Followup],
    rng: random.Random, year: int, tz: str, *, defer_combat: bool = False, cause: str | None = None,
) -> FollowupOutcome:
    """Act on the followups of an `apply_effects` call. Stops at a death.

    With `defer_combat` a `start_combat` is not resolved but returned in `combat_danger`
    (the event flow may offer the exits without a fight first). `cause` is the cause of death for a `death_save`
    that carries none of its own (the week names an infection, a collector event names the collector).
    """
    out = FollowupOutcome()
    st = ch.state
    for fu in followups:
        if fu.kind == FU_DEATH:
            cause = str(fu.value or "combat")
            await kill_character(session, content, ch, cause, tz)
            out.died, out.cause = True, cause
            return out
    for fu in followups:
        if fu.kind == FU_DEATH_SAVE:
            zr = await death.resolve_zero_hp(
                session, content, ch, rng, year, tz,
                cause=str(fu.data.get("cause") or cause or death.default_cause(st)), source="followup",
            )
            out.lines.extend(zr.lines)
            out.pending = out.pending or zr.pending
            if zr.died:
                out.died, out.cause = True, zr.cause
                return out
        elif fu.kind == FU_JAIL:
            from app.services import jail

            out.lines.extend(await jail.arrest(session, content, ch, int(fu.value or 0), rng, year, tz))
        elif fu.kind == FU_VANISH:
            from app.services import jail

            lines, died, cause = await jail.vanish(session, content, ch, int(fu.value or 0), rng, year, tz)
            out.lines.extend(lines)
            if died:
                out.died, out.cause = True, cause
                return out
        elif fu.kind == FU_VOW_BREAK:
            vow = content.vows.get(str(fu.value))
            out.lines.append(texts.FU_VOW_BREAK.format(vow=texts.esc(vow.name if vow else str(fu.value))))
        elif fu.kind == FU_HUMANITY_WARNING:
            critical = fu.value == "critical"
            out.lines.append(texts.FU_HUMANITY_CRITICAL if critical else texts.FU_HUMANITY_LOW)
            queue_event(content, st.flags, WARNING_EVENTS["critical" if critical else "low"])
        elif fu.kind == FU_CYBERPSYCHOSIS:
            from app.services import psychosis

            out.lines.extend(await psychosis.start_breakdown(session, content, ch, rng, year, tz))
    raided = False
    for fu in followups:
        if fu.kind == FU_RAID:  # the police (or MaxTac) come for the character: the exits screen, then the fight
            data = fu.data or {}
            faction = str(data.get("faction", "ncpd"))
            hunt = data.get("src") == "hunt"
            raided = True
            st.flags[FLAG_PENDING_COMBAT] = {"src": str(data.get("src", "raid")), "danger": int(fu.value or 1), "faction": faction}
            out.combat_danger = max(out.combat_danger or 0, int(fu.value or 1))
            out.lines.append(texts_life.RAID_HUNT if hunt else texts_life.RAID_NCPD)
            await repo.log_event(session, "raid", {"faction": faction, "danger": int(fu.value or 1), "hunt": hunt}, character_id=ch.id)
            if faction == "maxtac":  # a MaxTac hunt is a loud public event (PLAN 10.3)
                await repo.log_event(
                    session, "maxtac_hunt", {"name": st.name, "surname": st.surname}, character_id=ch.id, public=True,
                )
    for fu in followups:
        if fu.kind == FU_START_COMBAT and not raided:
            danger = int(fu.value or 1)
            out.combat_danger = max(out.combat_danger or 0, danger)
            if not defer_combat:  # no exits screen of the caller: the fight waits on the character
                from app.services import combat

                st.flags[FLAG_PENDING_COMBAT] = {
                    "src": "followup", "danger": out.combat_danger, "faction": combat.default_faction(content, ch),
                }
    return out
