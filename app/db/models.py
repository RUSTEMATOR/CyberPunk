from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> Any:
    return mapped_column(DateTime, server_default=func.now())


class Player(Base):
    __tablename__ = "players"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _now()
    lineage_id: Mapped[int | None] = mapped_column(ForeignKey("lineages.id"), nullable=True)
    weeks_today: Mapped[int] = mapped_column(Integer, default=0)  # RETIRED (personal time: no weeks-per-day limit); kept so old DBs insert
    weeks_today_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # RETIRED, see weeks_today
    # Daily loop (DESIGN.md 12a); lives on the player, survives character deaths.
    streak: Mapped[int] = mapped_column(Integer, default=0)
    last_daily: Mapped[str | None] = mapped_column(String(10), nullable=True)  # Kyiv date of the last claim
    tokens: Mapped[int] = mapped_column(Integer, default=0)  # Afterlife tokens
    caliber_mm: Mapped[int] = mapped_column(Integer, default=0)
    dirt: Mapped[int] = mapped_column(Integer, default=0)
    dirt_targets: Mapped[dict] = mapped_column(JSON, default=dict)  # target tg_id -> date of the last /dump
    titles: Mapped[list] = mapped_column(JSON, default=list)
    freeze_count: Mapped[int] = mapped_column(Integer, default=0)
    # Mini-games (DESIGN.md 12a, balance.yaml car / cat / coin); all of it outlives characters.
    car_hp: Mapped[int] = mapped_column(Integer, default=0)  # horsepower; 0 = no car yet, set to balance.car.start_hp on /car
    car_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    car_parts: Mapped[list] = mapped_column(JSON, default=list)  # keys of balance.car.parts that are MISSING
    cat: Mapped[dict] = mapped_column(JSON, default=dict)  # {} = no cat yet; else name, level, feedings, fed (last feed, Kyiv date), chrome (list of keys), gone (bool)
    coins: Mapped[float] = mapped_column(Float, default=0.0)  # Eddy-crypto wallet
    mini_last: Mapped[dict] = mapped_column(JSON, default=dict)  # game -> Kyiv date of the last daily play: caliber, car, coin


class PlayerData(Base):
    """Small per-player records that outlive characters: counters, the card of the day, reminder marks.

    A table of its own (not columns of `players`) so an existing database gets it from create_all.
    Keys in use: see `app/services/playerdata.py`.
    """

    __tablename__ = "player_data"

    player_tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, default=dict)


class PlayerMail(Base):
    """A letter from one player to another (docs/EPIC_PLAYERS.md): it waits until the recipient opens the window,
    and the recipient's own session applies it, so no session ever writes into another player's character."""

    __tablename__ = "player_mail"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    to_tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    from_tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    from_char_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_char_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the character it was meant for
    from_name: Mapped[str] = mapped_column(String(130))
    from_year: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16))  # gift, drink, vouch, note
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _now()
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class Raid(Base):
    """A city raid boss (balance.players.raid): one at a time, shared by the whole group, real time."""

    __tablename__ = "raids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    boss: Mapped[str] = mapped_column(String(32))
    hp_max: Mapped[int] = mapped_column(Integer)
    hp: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="active", index=True)  # active, won, escaped
    started_at: Mapped[datetime] = _now()
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # hits: {tg: {name, char, dmg, n, day}}, support: n


class Crew(Base):
    """A crew of players (docs/EPIC_PLAYERS.md, MMO part): a name, a leader, a shared bank."""

    __tablename__ = "crews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32))
    leader_tg: Mapped[int] = mapped_column(BigInteger)
    bank: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _now()
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # raids_won, log: [last lines]


class CrewMember(Base):
    """One player in one crew; a player is in at most one crew (the crew outlives his characters)."""

    __tablename__ = "crew_members"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    crew_id: Mapped[int] = mapped_column(Integer, index=True)
    joined_at: Mapped[datetime] = _now()


class Listing(Base):
    """A lot on the player market: a weapon or armor taken out of the seller's inventory until sold or withdrawn."""

    __tablename__ = "market"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seller_tg: Mapped[int] = mapped_column(BigInteger, index=True)
    seller_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seller_name: Mapped[str] = mapped_column(String(130))
    kind: Mapped[str] = mapped_column(String(12))  # weapon | armor
    item_key: Mapped[str] = mapped_column(String(32))
    price: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)  # open, sold, withdrawn
    buyer_tg: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = _now()


class Stash(Base):
    """A stash left for the future (echoes through time): found by the first character in its district whose year
    reaches open_year (any player, an heir too)."""

    __tablename__ = "stashes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_tg: Mapped[int] = mapped_column(BigInteger, index=True)
    owner_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_name: Mapped[str] = mapped_column(String(130))
    district: Mapped[str] = mapped_column(String(32), index=True)
    open_year: Mapped[int] = mapped_column(Integer)
    eddies: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="hidden", index=True)  # hidden, found
    finder_tg: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = _now()


class SagaEvent(Base):
    """A saga step written by Claude (the endless saga): kept forever and drawn by anybody whose marks fit it."""

    __tablename__ = "saga_events"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)          # the Event as a dict (conditions and marks included)
    author_tg: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _now()


class RogueAI(Base):
    """A character who stepped beyond the Blackwall and came back as a hostile AI (owner request). The player plays it
    until the city erases it (a raid) or it dissolves; then the life closes with the cause `blackwall`."""

    __tablename__ = "rogue_ais"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_tg: Mapped[int] = mapped_column(BigInteger, index=True)
    char_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(130))
    power: Mapped[int] = mapped_column(Integer, default=10)
    hp: Mapped[int] = mapped_column(Integer, default=100)
    hp_max: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[str] = mapped_column(String(12), default="active", index=True)  # active, erased, dissolved
    created_at: Mapped[datetime] = _now()
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # day, used (charges), log: [lines], hits


class Bond(Base):
    """The bond between two characters of different players, 0..balance.players.bond_max. a_char_id < b_char_id."""

    __tablename__ = "bonds"

    a_char_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    b_char_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)
    last_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # action -> Kyiv date of the last use (cooldowns)


class Lineage(Base):
    __tablename__ = "lineages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    family_name: Mapped[str] = mapped_column(String(64))
    lives_count: Mapped[int] = mapped_column(Integer, default=0)
    glitch_bonus: Mapped[int] = mapped_column(Integer, default=0)


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    lineage_id: Mapped[int | None] = mapped_column(ForeignKey("lineages.id"), nullable=True)
    alive: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    surname: Mapped[str] = mapped_column(String(64))
    preset: Mapped[str] = mapped_column(String(32), default="")  # presets.yaml key: the starting kit, never changes
    role: Mapped[str] = mapped_column(String(32), default="")  # MAIN role (roles.yaml key), earned from play; "" = none yet
    role_affinity: Mapped[dict] = mapped_column(JSON, default=dict)  # role key -> affinity (float), decays weekly
    faction_rep: Mapped[dict] = mapped_column(JSON, default=dict)  # gang / corp key -> reputation, -50..50 (balance.exits)
    origin: Mapped[str] = mapped_column(String(32))
    age_years: Mapped[int] = mapped_column(Integer, default=18)
    age_weeks: Mapped[int] = mapped_column(Integer, default=0)
    district: Mapped[str] = mapped_column(String(32))
    housing: Mapped[str] = mapped_column(String(32), default="capsule")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    skills: Mapped[dict] = mapped_column(JSON, default=dict)
    hp: Mapped[int] = mapped_column(Integer, default=0)
    mood: Mapped[int] = mapped_column(Integer, default=50)
    humanity: Mapped[int] = mapped_column(Integer, default=0)
    humanity_lost: Mapped[int] = mapped_column(Integer, default=0)
    cyber_tolerance: Mapped[str] = mapped_column(String(16), default="normal")
    tolerance_known: Mapped[bool] = mapped_column(Boolean, default=False)
    street_cred: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=0)  # reputation 0..50: career, money, connections
    fame: Mapped[int] = mapped_column(Integer, default=0)  # reputation 0..50: fans, media, stage
    chrome_free: Mapped[bool] = mapped_column(Boolean, default=False)  # no implants so far (DESIGN.md 7a)
    vows: Mapped[dict] = mapped_column(JSON, default=dict)  # vow key -> streak in weeks
    fixer_rel: Mapped[dict] = mapped_column(JSON, default=dict)  # fixer key -> 0..100
    traits: Mapped[list] = mapped_column(JSON, default=list)  # traits.yaml keys (narrative picks)
    marks: Mapped[list] = mapped_column(JSON, default=list)
    obsessions: Mapped[list] = mapped_column(JSON, default=list)
    birth_year: Mapped[int] = mapped_column(Integer, default=0)  # world year of birth; world time = birth year + age
    heat: Mapped[int] = mapped_column(Integer, default=0)
    eddies: Mapped[int] = mapped_column(Integer, default=0)
    luck_pool: Mapped[int] = mapped_column(Integer, default=0)
    glitches: Mapped[int] = mapped_column(Integer, default=0)
    death_glitch_used: Mapped[bool] = mapped_column(Boolean, default=False)
    ap: Mapped[int] = mapped_column(Integer, default=3)
    weeks_lived: Mapped[int] = mapped_column(Integer, default=0)
    career: Mapped[dict] = mapped_column(JSON, default=dict)
    flags: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _now()
    died_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    death_cause: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Debt(Base):
    __tablename__ = "debts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), index=True)
    creditor: Mapped[str] = mapped_column(String(64))
    principal: Mapped[int] = mapped_column(Integer, default=0)
    weekly_rate: Mapped[float] = mapped_column(Float, default=0.05)
    missed_weeks: Mapped[int] = mapped_column(Integer, default=0)
    creditor_kind: Mapped[str] = mapped_column(String(16), default="other")
    creditor_ref: Mapped[str | None] = mapped_column(String(48), nullable=True)  # gang / corp / ripperdoc key
    debt_key: Mapped[str | None] = mapped_column(String(48), nullable=True)  # debts.yaml key of the starting debt


class Cyberware(Base):
    __tablename__ = "cyberware"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), index=True)
    item_key: Mapped[str] = mapped_column(String(48))
    slot: Mapped[str] = mapped_column(String(32), default="")
    installed_week: Mapped[int] = mapped_column(Integer, default=0)
    complication: Mapped[bool] = mapped_column(Boolean, default=False)
    humanity_paid: Mapped[int] = mapped_column(Integer, default=0)  # actual humanity lost on install, for removal refunds
    charge: Mapped[int] = mapped_column(Integer, default=100)  # active implants, 0..100


class InventoryItem(Base):
    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), index=True)
    item_key: Mapped[str] = mapped_column(String(48))
    kind: Mapped[str] = mapped_column(String(16))  # weapon / armor / item
    equipped: Mapped[bool] = mapped_column(Boolean, default=False)


class Npc(Base):
    __tablename__ = "npcs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # family/partner/friend/contact/protege/child/fixer/ripper
    name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str | None] = mapped_column(String(48), nullable=True)
    relation: Mapped[int] = mapped_column(Integer, default=50)
    alive: Mapped[bool] = mapped_column(Boolean, default=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Gig(Base):
    __tablename__ = "gigs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    district: Mapped[str] = mapped_column(String(32), index=True)
    fixer: Mapped[str] = mapped_column(String(48))
    type: Mapped[str] = mapped_column(String(24))
    tier: Mapped[int] = mapped_column(Integer, default=1)
    pay: Mapped[int] = mapped_column(Integer, default=0)
    target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    template_key: Mapped[str] = mapped_column(String(48))
    created_day: Mapped[str] = mapped_column(String(10))  # the board stamp of the owner's own time, `w000012` (services/gigs.py)
    owner_tg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)  # whose personal board; None = an old shared board
    taken_by: Mapped[int | None] = mapped_column(ForeignKey("characters.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    brief_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class GigRun(Base):
    __tablename__ = "gig_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gig_id: Mapped[int] = mapped_column(ForeignKey("gigs.id"), index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), index=True)
    approach: Mapped[str] = mapped_column(String(16))
    checks: Mapped[list] = mapped_column(JSON, default=list)
    outcome: Mapped[str] = mapped_column(String(16))
    effects: Mapped[dict] = mapped_column(JSON, default=dict)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _now()


class EventLog(Base):
    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    public: Mapped[bool] = mapped_column(Boolean, default=False)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now()


# Keys of world_state used so far (value JSON). Kyiv dates are 'YYYY-MM-DD' strings.
WORLD_STATE_KEYS: dict[str, str] = {
    "group_chat_id": "int, the one group where the game runs",
    "world_week": "int, the RETIRED shared world clock (kept as the record the personal-time migration started from; nothing reads it)",
    "personal_time_v1": "dict, set once by the migration to personal time (services/world.py::migrate_personal_time)",
    "district_heat": "dict district key -> heat 0..10",
    "gang_wars": "dict gang key -> world days left",
    "last_tick": "world day (rolls at 03:00 Kyiv) of the last global tick",
    "last_world_day": "real-time day of the last global tick (same value as last_tick)",
    "coin_rate": "float, eddies per coin (balance.coin)",
    "coin_history": "list of {day: Kyiv date, rate: float}, at most balance.coin.history_days entries",
    "coin_shock_until": "Kyiv date until which balance.coin.shock_volatility_mult applies; absent = no shock",
    "coin_shocks": "dict world_history id -> {mult actually applied, day, until}; lets /admin_veto undo a coin_shock",
}


class WorldState(Base):
    __tablename__ = "world_state"

    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, default=dict)


class GraveyardEntry(Base):
    __tablename__ = "graveyard"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), index=True)
    lineage_id: Mapped[int | None] = mapped_column(ForeignKey("lineages.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(128))
    born_age: Mapped[int] = mapped_column(Integer, default=18)
    died_age: Mapped[int] = mapped_column(Integer, default=18)
    role: Mapped[str] = mapped_column(String(32))
    cause: Mapped[str] = mapped_column(String(32))
    street_cred: Mapped[int] = mapped_column(Integer, default=0)
    epitaph: Mapped[str | None] = mapped_column(String(200), nullable=True)
    obituary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    killed_by: Mapped[str] = mapped_column(String(128), default="")  # the MaxTac hunter who closed a duty call, else empty
    died_at: Mapped[datetime] = _now()


class LlmUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = _now()
    kind: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(48))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read: Mapped[int] = mapped_column(Integer, default=0)
    cache_write: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class Window(Base):
    __tablename__ = "windows"

    player_tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(Integer, index=True)
    screen: Mapped[str] = mapped_column(String(48), default="menu")
    screen_state: Mapped[dict] = mapped_column(JSON, default=dict)
    awaiting_text: Mapped[str | None] = mapped_column(String(48), nullable=True)
    last_active: Mapped[datetime] = _now()
    collapsed: Mapped[bool] = mapped_column(Boolean, default=False)


class CleanupItem(Base):
    __tablename__ = "cleanup_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(Integer)
    delete_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    kind: Mapped[str] = mapped_column(String(24), default="temp")


class Buff(Base):
    """Temporary modifiers: the Misty card of the day, a stim. Collected by collect_mods until expires_at."""

    __tablename__ = "buffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_tg_id: Mapped[int] = mapped_column(ForeignKey("players.tg_id"), index=True)
    character_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(64))  # tarot:<key>, shop:<key>, event:<id>
    mods: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class QuestProgress(Base):
    __tablename__ = "quest_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_tg_id: Mapped[int] = mapped_column(ForeignKey("players.tg_id"), index=True)
    quest_key: Mapped[str] = mapped_column(String(48))
    period: Mapped[str] = mapped_column(String(8))  # daily / weekly
    period_start: Mapped[str] = mapped_column(String(10))  # Kyiv date the period began
    progress: Mapped[int] = mapped_column(Integer, default=0)
    target: Mapped[int] = mapped_column(Integer, default=1)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    claimed: Mapped[bool] = mapped_column(Boolean, default=False)


class WorldHistory(Base):
    """World history: canon events from timeline.yaml, local events, Claude-written events."""

    __tablename__ = "world_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_week: Mapped[int] = mapped_column(Integer, index=True)
    year: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # canon / local / generated
    ref_id: Mapped[str | None] = mapped_column(String(48), nullable=True)  # timeline id or character id
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, default="")
    effects: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / vetoed
    reached: Mapped[bool] = mapped_column(Boolean, default=False)  # some character's date has come to it (coin shock done)
    created_at: Mapped[datetime] = _now()


class ImageCache(Base):
    __tablename__ = "image_cache"

    file: Mapped[str] = mapped_column(String(128), primary_key=True)
    tg_file_id: Mapped[str] = mapped_column(String(256))


class CityNpc(Base):
    """A person of the city (owner request: freedom of action, docs/PLAN_FREEDOM.md). Shared by all players: a dead
    bartender is dead for everyone until a new one takes the slot."""

    __tablename__ = "city_npcs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    district: Mapped[str] = mapped_column(String(48), index=True)
    place: Mapped[str] = mapped_column(String(48))       # place type key
    slot: Mapped[int] = mapped_column(Integer)
    archetype: Mapped[str] = mapped_column(String(48))
    name: Mapped[str] = mapped_column(String(80))
    gender: Mapped[str] = mapped_column(String(1), default="m")
    alive: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    money: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _now()
    died_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # bonds: {char_id: {"att": int, "notes": [str], "day": {verb: date}}}; hurt_until; killer; cause; line
    data: Mapped[dict] = mapped_column(JSON, default=dict)
