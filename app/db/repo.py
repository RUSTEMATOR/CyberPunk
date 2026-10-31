"""Persistence layer: CharacterState <-> ORM rows, players, world_state, event_log, windows, cleanup.

Every function takes an open `AsyncSession` and never commits: the caller owns the unit of work
(`async with db.session_scope() as s`). No game rules live here, only mapping.

Character mapping:
- scalar and JSON columns of `characters` carry the identity, scales, stats, skills, vows, flags,
  career, faction and fixer standing, and the narrative picks;
- `debts`, `cyberware` (with charge and humanity_paid), `inventory`, `npcs` and `buffs` are child
  tables, saved by position (rows are updated in place, extras are deleted, missing ones inserted);
- a buff row has a real `expires_at`. In the state `Buff.expires` is either the marker
  "kyiv_midnight" (new buff, the row gets the next Kyiv midnight) or the ISO string of the stored
  expiry. Expired rows are not loaded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import DEFAULT_TZ, next_kyiv_midnight_utc, utcnow
from app.db.session import INSTANT_FEED_INFO_KEY
from app.engine import feed as feed_engine
from app.db.models import (
    Buff as BuffRow,
    Character,
    CleanupItem,
    Cyberware,
    Debt,
    EventLog,
    Gig,
    GigRun,
    InventoryItem as InventoryRow,
    Lineage,
    Npc,
    Player,
    Window,
    WorldHistory,
    WorldState,
)
from app.engine.state import (
    FLAG_WORLD_MULT,
    Buff,
    CareerState,
    CharacterState,
    CyberItem,
    DebtState,
    InventoryItem,
    NpcState,
    SkillState,
)

# ---------------------------------------------------------------------------
# Players and lineages
# ---------------------------------------------------------------------------


async def get_player(session: AsyncSession, tg_id: int) -> Player | None:
    return await session.get(Player, tg_id)


async def ensure_player(session: AsyncSession, tg_id: int, username: str | None = None) -> Player:
    """The player row, created on first contact; the username is refreshed when it changed."""
    player = await session.get(Player, tg_id)
    if player is None:
        player = Player(tg_id=tg_id, username=username)
        session.add(player)
        await session.flush()
    elif username and player.username != username:
        player.username = username
    return player


async def get_lineage(session: AsyncSession, lineage_id: int | None) -> Lineage | None:
    return await session.get(Lineage, lineage_id) if lineage_id else None


async def ensure_lineage(session: AsyncSession, player: Player, family_name: str) -> Lineage:
    """The player's lineage; the first character founds it under their surname."""
    lineage = await get_lineage(session, player.lineage_id)
    if lineage is None:
        lineage = Lineage(player_tg_id=player.tg_id, family_name=family_name, lives_count=0, glitch_bonus=0)
        session.add(lineage)
        await session.flush()
        player.lineage_id = lineage.id
    return lineage


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------

_SCALAR_FIELDS: tuple[str, ...] = (
    "name", "surname", "preset", "role", "origin", "birth_year", "age_years", "age_weeks",
    "weeks_lived", "ap", "district", "housing", "hp", "mood", "humanity", "humanity_lost",
    "cyber_tolerance", "tolerance_known", "chrome_free", "street_cred", "status", "fame", "heat",
    "eddies", "luck_pool", "glitches", "death_glitch_used",
)


def _apply_state_to_row(row: Character, st: CharacterState) -> None:
    for f in _SCALAR_FIELDS:
        setattr(row, f, getattr(st, f))
    row.role_affinity = dict(st.role_affinity)
    row.faction_rep = dict(st.faction_rep)
    row.fixer_rel = dict(st.fixer_rel)
    row.stats = dict(st.stats)
    row.skills = {k: {"level": v.level, "xp": v.xp} for k, v in st.skills.items()}
    row.vows = dict(st.vows)
    row.traits = list(st.traits)
    row.marks = list(st.marks)
    row.obsessions = list(st.obsessions)
    row.career = (
        {"key": st.career.key, "rank": st.career.rank, "weeks": st.career.weeks} if st.career else {}
    )
    row.flags = dict(st.flags)


def _state_from_row(row: Character) -> CharacterState:
    st = CharacterState()
    for f in _SCALAR_FIELDS:
        setattr(st, f, getattr(row, f))
    st.role_affinity = {k: float(v) for k, v in (row.role_affinity or {}).items()}
    st.faction_rep = {k: int(v) for k, v in (row.faction_rep or {}).items()}
    st.fixer_rel = {k: int(v) for k, v in (row.fixer_rel or {}).items()}
    stats = dict(st.stats)
    stats.update({k: int(v) for k, v in (row.stats or {}).items()})
    st.stats = stats
    skills = dict(st.skills)
    for k, v in (row.skills or {}).items():
        skills[k] = SkillState(int(v.get("level", 0)), int(v.get("xp", 0)))
    st.skills = skills
    st.vows = {k: int(v) for k, v in (row.vows or {}).items()}
    st.traits = list(row.traits or [])
    st.marks = list(row.marks or [])
    st.obsessions = list(row.obsessions or [])
    career = row.career or {}
    st.career = (
        CareerState(career["key"], int(career.get("rank", 1)), int(career.get("weeks", 0)))
        if career.get("key") else None
    )
    st.flags = dict(row.flags or {})
    return st


async def _rows(session: AsyncSession, model: Any, character_id: int) -> list[Any]:
    res = await session.execute(
        select(model).where(model.character_id == character_id).order_by(model.id)
    )
    return list(res.scalars())


async def _sync(
    session: AsyncSession, model: Any, character_id: int, existing: Sequence[Any],
    wanted: Sequence[dict[str, Any]],
) -> None:
    """Make the child rows equal `wanted`: update by position, insert the rest, delete the extras."""
    for i, vals in enumerate(wanted):
        if i < len(existing):
            for k, v in vals.items():
                setattr(existing[i], k, v)
        else:
            session.add(model(character_id=character_id, **vals))
    for row in existing[len(wanted):]:
        await session.delete(row)


def _buff_expiry(marker: str, tz: str) -> datetime:
    if marker and marker != "kyiv_midnight":
        try:
            return datetime.fromisoformat(marker)
        except ValueError:
            pass
    return next_kyiv_midnight_utc(tz)


async def load_character(session: AsyncSession, character_id: int) -> CharacterState | None:
    """The full state of a character (alive or dead), or None when there is no such id."""
    row = await session.get(Character, character_id)
    if row is None:
        return None
    st = _state_from_row(row)
    st.debts = [
        DebtState(
            creditor=d.creditor, principal=d.principal, weekly_rate=d.weekly_rate,
            missed_weeks=d.missed_weeks, creditor_kind=d.creditor_kind or "other",
            creditor_ref=d.creditor_ref, key=d.debt_key,
        )
        for d in await _rows(session, Debt, character_id)
    ]
    st.cyberware = [
        CyberItem(
            key=c.item_key, charge=c.charge, complication=c.complication,
            installed_week=c.installed_week, humanity_paid=c.humanity_paid,
        )
        for c in await _rows(session, Cyberware, character_id)
    ]
    st.inventory = [
        InventoryItem(i.item_key, i.kind, i.equipped) for i in await _rows(session, InventoryRow, character_id)
    ]
    st.npcs = [
        NpcState(
            name=n.name, kind=n.kind, role=n.role, relation=n.relation, alive=n.alive,
            data=dict(n.data or {}),
        )
        for n in await _rows(session, Npc, character_id)
    ]
    now = utcnow()
    st.buffs = [
        Buff(source=b.source, mods={k: int(v) for k, v in (b.mods or {}).items()},
             expires=b.expires_at.isoformat())
        for b in await _rows(session, BuffRow, character_id)
        if b.expires_at > now
    ]
    mult = st.flags.get(FLAG_WORLD_MULT) or {}  # the world's multipliers at the character's own date (DESIGN.md 9a)
    st.world_heat_mult = float(mult.get("heat", 1.0) or 1.0)
    st.world_housing_mult = float(mult.get("housing", 1.0) or 1.0)
    ctl = (await ws_get(session, "control", {}) or {}).get(st.district)  # owner request 25.10: gangs against corps
    if isinstance(ctl, dict) and "corp" in ctl:
        st.district_control = (int(ctl.get("gang", 0) or 0), int(ctl.get("corp", 0) or 0))
    turf = (await ws_get(session, "turf", {}) or {}).get(st.district)  # owner request 25.10: a player-held district
    if isinstance(turf, dict):
        st.district_law = str(turf.get("law", "") or "")
    rel = await ws_get(session, "relics", {}) or {}  # owner request 25.10: relics, one of each in the city
    st.relics = tuple(k for k, r in rel.items() if isinstance(r, dict) and not r.get("hall")
                      and int(r.get("char", 0) or 0) == character_id)
    return st


async def save_character(
    session: AsyncSession, character_id: int, st: CharacterState, tz: str = DEFAULT_TZ
) -> None:
    """Write the whole state back. Raises LookupError for an unknown id."""
    row = await session.get(Character, character_id)
    if row is None:
        raise LookupError(f"character {character_id} does not exist")
    _apply_state_to_row(row, st)
    await _sync(session, Debt, character_id, await _rows(session, Debt, character_id), [
        {"creditor": d.creditor, "principal": d.principal, "weekly_rate": d.weekly_rate,
         "missed_weeks": d.missed_weeks, "creditor_kind": d.creditor_kind,
         "creditor_ref": d.creditor_ref, "debt_key": d.key}
        for d in st.debts
    ])
    await _sync(session, Cyberware, character_id, await _rows(session, Cyberware, character_id), [
        {"item_key": c.key, "installed_week": c.installed_week, "complication": c.complication,
         "humanity_paid": c.humanity_paid, "charge": c.charge}
        for c in st.cyberware
    ])
    await _sync(session, InventoryRow, character_id, await _rows(session, InventoryRow, character_id), [
        {"item_key": i.key, "kind": i.kind, "equipped": i.equipped} for i in st.inventory
    ])
    await _sync(session, Npc, character_id, await _rows(session, Npc, character_id), [
        {"kind": n.kind, "name": n.name, "role": n.role, "relation": n.relation,
         "alive": n.alive, "data": dict(n.data)}
        for n in st.npcs
    ])
    await _sync(session, BuffRow, character_id, await _rows(session, BuffRow, character_id), [
        {"player_tg_id": row.player_tg_id, "source": b.source, "mods": dict(b.mods),
         "expires_at": _buff_expiry(b.expires, tz)}
        for b in st.buffs
    ])
    await session.flush()


async def create_character(
    session: AsyncSession, player_tg_id: int, st: CharacterState, lineage_id: int | None = None,
    tz: str = DEFAULT_TZ,
) -> int:
    """Insert a new alive character with all its child rows and return its id."""
    row = Character(player_tg_id=player_tg_id, lineage_id=lineage_id, alive=True)
    _apply_state_to_row(row, st)
    session.add(row)
    await session.flush()
    await save_character(session, row.id, st, tz)
    return row.id


async def get_character_row(session: AsyncSession, character_id: int) -> Character | None:
    return await session.get(Character, character_id)


async def active_character_id(session: AsyncSession, tg_id: int) -> int | None:
    """The id of the player's living character (a player has at most one), or None."""
    res = await session.execute(
        select(Character.id)
        .where(Character.player_tg_id == tg_id, Character.alive.is_(True))
        .order_by(Character.id.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def living_characters(session: AsyncSession) -> list[Character]:
    res = await session.execute(select(Character).where(Character.alive.is_(True)).order_by(Character.id))
    return list(res.scalars())


async def find_player_by_username(session: AsyncSession, username: str) -> Player | None:
    """Look a player up by @username (case-insensitive) for admin commands."""
    uname = username.lstrip("@").lower()
    res = await session.execute(select(Player))
    for p in res.scalars():
        if p.username and p.username.lower() == uname:
            return p
    return None


async def display_name(session: AsyncSession, tg_id: int, fallback: str = "Игрок") -> str:
    """Name for window captions: the living character's first name, else the @username."""
    cid = await active_character_id(session, tg_id)
    if cid is not None:
        row = await session.get(Character, cid)
        if row is not None and row.name:
            return row.name
    player = await session.get(Player, tg_id)
    if player is not None and player.username:
        return player.username
    return fallback


# ---------------------------------------------------------------------------
# world_state
# ---------------------------------------------------------------------------


async def ws_get(session: AsyncSession, key: str, default: Any = None) -> Any:
    row = await session.get(WorldState, key)
    return default if row is None else row.value


async def ws_set(session: AsyncSession, key: str, value: Any) -> None:
    row = await session.get(WorldState, key)
    if row is None:
        session.add(WorldState(key=key, value=value))
    else:
        row.value = value
    await session.flush()


async def ws_delete(session: AsyncSession, key: str) -> None:
    await session.execute(delete(WorldState).where(WorldState.key == key))


# ---------------------------------------------------------------------------
# event_log
# ---------------------------------------------------------------------------


async def log_event(
    session: AsyncSession, kind: str, payload: dict[str, Any] | None = None,
    character_id: int | None = None, public: bool = False,
) -> EventLog:
    """Append to event_log. `public` rows are picked up by the city feed (published stays False)."""
    row = EventLog(kind=kind, payload=payload or {}, character_id=character_id, public=public)
    session.add(row)
    await session.flush()
    if public and feed_engine.is_instant(kind):  # the city feed posts it right after the commit (PLAN 10.3)
        session.info[INSTANT_FEED_INFO_KEY] = True
    return row


async def unpublished_public_events(session: AsyncSession, limit: int = 100) -> list[EventLog]:
    res = await session.execute(
        select(EventLog)
        .where(EventLog.public.is_(True), EventLog.published.is_(False))
        .order_by(EventLog.id)
        .limit(limit)
    )
    return list(res.scalars())


async def mark_published(session: AsyncSession, event_ids: Sequence[int]) -> None:
    for eid in event_ids:
        row = await session.get(EventLog, eid)
        if row is not None:
            row.published = True


async def recent_events(session: AsyncSession, character_id: int, limit: int = 20) -> list[EventLog]:
    res = await session.execute(
        select(EventLog).where(EventLog.character_id == character_id).order_by(EventLog.id.desc()).limit(limit)
    )
    return list(res.scalars())


# ---------------------------------------------------------------------------
# windows and cleanup_queue (the UI layer, M3.0)
# ---------------------------------------------------------------------------


async def get_window(session: AsyncSession, tg_id: int) -> Window | None:
    return await session.get(Window, tg_id)


async def get_window_by_message(session: AsyncSession, chat_id: int, message_id: int) -> Window | None:
    res = await session.execute(
        select(Window).where(Window.chat_id == chat_id, Window.message_id == message_id)
    )
    return res.scalars().first()


async def save_window(
    session: AsyncSession, tg_id: int, chat_id: int, message_id: int, screen: str,
    screen_state: dict[str, Any], awaiting_text: str | None, collapsed: bool = False,
) -> Window:
    """Create or overwrite the player's window row and stamp last_active."""
    row = await session.get(Window, tg_id)
    if row is None:
        row = Window(player_tg_id=tg_id, chat_id=chat_id, message_id=message_id)
        session.add(row)
    row.chat_id = chat_id
    row.message_id = message_id
    row.screen = screen
    row.screen_state = dict(screen_state)
    row.awaiting_text = awaiting_text
    row.collapsed = collapsed
    row.last_active = utcnow()
    await session.flush()
    return row


async def touch_window(session: AsyncSession, tg_id: int) -> None:
    row = await session.get(Window, tg_id)
    if row is not None:
        row.last_active = utcnow()


async def idle_windows(session: AsyncSession, before: datetime) -> list[Window]:
    """Open windows nobody touched since `before`."""
    res = await session.execute(
        select(Window).where(Window.collapsed.is_(False), Window.last_active < before)
    )
    return list(res.scalars())


async def stale_windows(session: AsyncSession, before: datetime) -> list[Window]:
    """Collapsed windows nobody touched since `before` (owner request 23.10: deleted from the chat)."""
    res = await session.execute(select(Window).where(Window.collapsed.is_(True), Window.last_active < before))
    return list(res.scalars())


async def add_cleanup(session: AsyncSession, chat_id: int, message_id: int, delete_at: datetime, kind: str) -> None:
    session.add(CleanupItem(chat_id=chat_id, message_id=message_id, delete_at=delete_at, kind=kind))
    await session.flush()


async def due_cleanup(session: AsyncSession, now: datetime, limit: int) -> list[CleanupItem]:
    res = await session.execute(
        select(CleanupItem).where(CleanupItem.delete_at <= now).order_by(CleanupItem.delete_at).limit(limit)
    )
    return list(res.scalars())


async def delete_cleanup(session: AsyncSession, ids: Sequence[int]) -> None:
    if ids:
        await session.execute(delete(CleanupItem).where(CleanupItem.id.in_(list(ids))))


async def cleanup_count(session: AsyncSession) -> int:
    res = await session.execute(select(CleanupItem.id))
    return len(res.all())


# ---------------------------------------------------------------------------
# Death (M4)
# ---------------------------------------------------------------------------


async def mark_dead(session: AsyncSession, character_id: int, cause: str) -> None:
    """Close a life: alive = False, the time and the cause (M7 adds the graveyard and the obituary)."""
    row = await session.get(Character, character_id)
    if row is None:
        raise LookupError(f"character {character_id} does not exist")
    row.alive = False
    row.died_at = utcnow()
    row.death_cause = cause
    await session.flush()


# ---------------------------------------------------------------------------
# gigs and gig_runs (M5)
# ---------------------------------------------------------------------------


async def district_street_creds(session: AsyncSession, district: str) -> list[int]:
    """Street cred of every living character who lives in the district (the board weights its tiers by it)."""
    res = await session.execute(
        select(Character.street_cred).where(Character.alive.is_(True), Character.district == district)
    )
    return [int(x) for x in res.scalars()]


async def board_gigs(session: AsyncSession, owner_tg_id: int, district: str, stamp: str) -> list[Gig]:
    """The gigs of a player's personal board in a district with the board stamp `stamp` (any status), oldest first."""
    res = await session.execute(
        select(Gig).where(Gig.owner_tg_id == owner_tg_id, Gig.district == district, Gig.created_day == stamp).order_by(Gig.id)
    )
    return list(res.scalars())


async def expire_open_gigs(session: AsyncSession, owner_tg_id: int, district: str, keep_stamp: str) -> None:
    """Open gigs of the player's earlier boards leave the board."""
    res = await session.execute(
        select(Gig).where(
            Gig.owner_tg_id == owner_tg_id, Gig.district == district, Gig.status == "open", Gig.created_day != keep_stamp,
        )
    )
    for g in res.scalars():
        g.status = "expired"


async def add_gig(
    session: AsyncSession, *, district: str, fixer: str, type: str, tier: int, pay: int, target: str,
    template_key: str, day: str, owner_tg_id: int | None = None,
) -> Gig:
    row = Gig(
        owner_tg_id=owner_tg_id, district=district, fixer=fixer, type=type, tier=tier, pay=pay, target=target,
        template_key=template_key, created_day=day, status="open",
    )
    session.add(row)
    await session.flush()
    return row


async def get_gig(session: AsyncSession, gig_id: int) -> Gig | None:
    return await session.get(Gig, gig_id)


async def add_gig_run(
    session: AsyncSession, *, gig_id: int, character_id: int, approach: str, checks: list,
    outcome: str, effects: dict, result_text: str | None,
) -> GigRun:
    row = GigRun(
        gig_id=gig_id, character_id=character_id, approach=approach, checks=checks, outcome=outcome,
        effects=effects, result_text=result_text,
    )
    session.add(row)
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# world_history (M10)
# ---------------------------------------------------------------------------


async def add_history(
    session: AsyncSession, *, world_week: int, year: int, kind: str, ref_id: str | None, title: str, text: str,
    effects: dict[str, Any],
) -> WorldHistory:
    """`world_week` is the date of the entry on the calendar (weeks since the start), not a shared clock."""
    row = WorldHistory(world_week=world_week, year=year, kind=kind, ref_id=ref_id, title=title, text=text, effects=effects)
    session.add(row)
    await session.flush()
    return row


async def history_rows(session: AsyncSession, limit: int = 10, *, active_only: bool = False) -> list[WorldHistory]:
    """The latest rows, newest first."""
    q = select(WorldHistory)
    if active_only:
        q = q.where(WorldHistory.status == "active")
    res = await session.execute(q.order_by(WorldHistory.id.desc()).limit(limit))
    return list(res.scalars())


async def active_history(session: AsyncSession) -> list[WorldHistory]:
    """Every active row in the order the effects were applied."""
    res = await session.execute(select(WorldHistory).where(WorldHistory.status == "active").order_by(WorldHistory.id))
    return list(res.scalars())


async def fired_canon_ids(session: AsyncSession) -> set[str]:
    """Timeline ids already in the history (vetoed rows count: a canon event never fires twice)."""
    res = await session.execute(select(WorldHistory.ref_id).where(WorldHistory.kind == "canon"))
    return {str(x) for x in res.scalars() if x}


async def get_history(session: AsyncSession, row_id: int) -> WorldHistory | None:
    return await session.get(WorldHistory, row_id)
