"""Plain dataclasses for everything the engine needs to know about a character (PLAN 2).

No logic of the game lives here, only data and conversion to and from plain dicts. The DB layer
maps `characters`, `debts`, `cyberware`, `inventory`, `npcs` and `buffs` rows to and from these
(`CharacterState.to_dict` / `from_dict` use the same shapes as the JSON columns).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.content.schemas import SKILLS, STATS

# Reserved keys inside CharacterState.flags (the dict also holds anything `flag_set:<name>` sets).
FLAG_INFECTION = "infection"  # True while the infection runs
FLAG_ADDICTION = "addiction"  # int, braindance addiction counter
FLAG_JAIL_WEEKS = "jail_weeks"  # int, weeks of the sentence left
FLAG_FUGITIVE = "fugitive_psycho"  # True for a fugitive cyberpsycho
FLAG_FULL_CONVERSION = "full_conversion"  # True after a full body conversion
FLAG_TRAUMA_TEAM = "trauma_team"  # tier key: silver | gold | platinum
FLAG_CIVILIAN_KILLS = "civilian_kills"  # int, counter for the MaxTac recruitment roll
FLAG_PARTNER_MOVED_IN = "partner_moved_in"
FLAG_CRIME_WEEK = "crime_week"  # True when a crime happened this week: heat does not decay; advance_week clears it
FLAG_PENDING_EVENTS = "pending_events"  # list of event ids drawn for the week and not yet played
FLAG_PENDING_CHECK = "pending_check"  # a rolled check waiting for the luck / glitch decision (no re-roll by closing the window)
FLAG_PENDING_COMBAT = "pending_combat"  # a fight offered by an event, waiting for the exits or the fight
FLAG_RECENT_EVENTS = "recent_events"  # list of the last shown event ids (balance.events.recent_memory)
FLAG_NOTICES = "notices"  # lines parked by bus listeners for the next screen (services/notices.py)
FLAG_PENDING_EXIT = "pending_exit"  # a failed exit check waiting for the luck / glitch / accept decision
FLAG_COMBAT = "combat"  # the running fight: a CombatState dict (engine/combat.py), saved after every tick
FLAG_STORY_QUEUE = "story_queue"  # event ids queued by the effect queue_event: they come with the next week (story arcs)
FLAG_GIG_BACKUP = "gig_backup"  # the merc picked on a gig brief before the approach: {gig, char, tg, name, share, g}
FLAG_GIG_RUN = "gig_run"  # the gig being played: approach, checks rolled so far, the decision waiting (services/gigs.py)
FLAG_BETRAYED_PREFIX = "betrayed:"  # flags["betrayed:<faction>"] = True after cheating a faction (effect `betray:<faction>`)
FLAG_TRUCE_PREFIX = "truce:"  # flags["truce:<faction>"] = weeks_lived of the last bribe or talk with that faction
FLAG_WORK_HARD = "work_hard"  # int, stacked bonus to the next promotion check; gone with the promotion
FLAG_PROMO_RETRY = "promo_retry"  # weeks_lived before which no new promotion is asked
FLAG_EMPLOYER = "employer"  # corps.yaml key of the employer (careers with several corps)
FLAG_MAXTAC_CONTRACT = "maxtac_contract"  # weeks_lived when the recruited MaxTac contract ends (set by M6.6a)
FLAG_JAIL_STASH = "jail_stash"  # {kind: item key} of the weapon and armor taken at the arrest, put back at the release
FLAG_JAIL_ISOLATION = "jail_isolation"  # True while the term is served in the isolation ward (set by M6.6a): therapy every week, no escape
FLAG_JAIL_LAWYER = "jail_lawyer"  # True once a lawyer has cut this sentence
FLAG_TOLERANCE_SHOWN = "tolerance_shown"  # what a lying scan told (the tolerance type shown to the player); gone after a true scan
FLAG_PSYCHO_CAPTURE = "psycho_capture"  # a captured psycho waits for the verdict: {recruited: bool, ...} (services/psychosis.py)
FLAG_PSYCHO_HUNT_AT = "psycho_hunt_at"  # weeks_lived of the next MaxTac hunt of a fugitive psycho
FLAG_MAXTAC_DUTY_AT = "maxtac_duty_at"  # weeks_lived of the next mandatory call of a MaxTac member
FLAG_MAXTAC_DUTY = "maxtac_duty"  # the call waiting for the answer: {target: archetype | player character id, ...}
FLAG_PSYCHO_CAUGHT = "psycho_caught"  # name of the MaxTac member who took this fugitive (another player's duty call)
FLAG_DEATH_PENDING = "death_pending"  # the death save failed and a glitch may roll it again: {cause, source, hunter, repeats, roll, need, penalty, after} (services/death.py)
FLAG_LEGACY_PENDING = "legacy_pending"  # on a DEAD character: the player has not chosen the heir yet
FLAG_LEGACY_PLAN = "legacy_plan"  # on a dead character: the chosen way on, {option, npc} (services/legacy.py)
FLAG_SECURE_SOUL = "secure_your_soul"  # Arasaka engram paid for (DESIGN.md 13)
FLAG_ENGRAM = "engram"  # this life is an engram: True; the conflict event is forced every legacy.engram.conflict_event_every_weeks
FLAG_HEIR_OF = "heir_of"  # how this life began: {kind: child|relative|protege|engram|scratch, from: name of the dead}
FLAG_STIM = "stim"  # int, bonus of the Afterlife stim for the next check; gone with the week
FLAG_WORLD_MULT = "world_mult"  # {heat, housing}: the world multipliers at the character's own date (services/worldfx.py::refresh_state)
FLAG_LATER = "later_outcomes"  # delayed outcomes (effect `later`): [{due, src, ...LaterEntry}], rolled by engine/week.py
FLAG_WEEK_LEDGER = "week_ledger"  # {eddies, debt} moved by events since the last week report (services/events.py)
FLAG_BEATS = "beats_seen"  # {seen: week, done: [ref ids]}: how far the world's beats were shown to this character (services/week.py)

# Inventory kinds.
KIND_WEAPON = "weapon"
KIND_ARMOR = "armor"
KIND_ITEM = "item"
KIND_QUICKHACK = "quickhack"


@dataclass
class SkillState:
    level: int = 0
    xp: int = 0


@dataclass
class CyberItem:
    """An installed implant. `charge` is 0..100 and only matters for active implants."""

    key: str
    charge: int = 100
    complication: bool = False
    installed_week: int = 0
    # Humanity actually lost for this implant (after tolerance, era and complication). Removal
    # returns a share of it. 0 = unknown or free (granted by an event or career).
    humanity_paid: int = 0


@dataclass
class DebtState:
    creditor: str
    principal: int
    weekly_rate: float
    missed_weeks: int = 0
    creditor_kind: str = "other"
    creditor_ref: str | None = None
    key: str | None = None  # debts.yaml key for the starting debt


@dataclass
class InventoryItem:
    key: str
    kind: str  # weapon | armor | item | quickhack
    equipped: bool = False


@dataclass
class NpcState:
    """A family member, partner, friend, contact, protege or child (fixers live in fixer_rel)."""

    name: str
    kind: str  # family | partner | friend | contact | protege | child
    role: str | None = None  # parent | sibling | ... free key, the UI translates it
    relation: int = 50
    alive: bool = True
    data: dict[str, Any] = field(default_factory=dict)  # district, flags (owes money, enemy, in trouble)


@dataclass
class CareerState:
    key: str
    rank: int = 1  # 1..5
    weeks: int = 0  # weeks served at the current rank


@dataclass
class Buff:
    """Temporary modifiers (Misty card, stim). The service drops them when they expire."""

    source: str  # tarot:<key>, shop:<key>, event:<id>
    mods: dict[str, int] = field(default_factory=dict)
    expires: str = "kyiv_midnight"  # marker for the DB layer, the engine never reads the clock


def _default_stats() -> dict[str, int]:
    return {s: 0 for s in STATS}


def _default_skills() -> dict[str, SkillState]:
    return {s: SkillState() for s in SKILLS}


@dataclass
class CharacterState:
    # identity
    name: str = ""
    surname: str = ""
    preset: str = ""
    role: str = ""  # MAIN role, earned from play; "" = none yet
    role_affinity: dict[str, float] = field(default_factory=dict)
    origin: str = ""
    # the world's effects as the character feels them: set when the state is loaded, never saved
    world_heat_mult: float = field(default=1.0, repr=False, compare=False)
    world_housing_mult: float = field(default=1.0, repr=False, compare=False)
    district_control: tuple[int, int] | None = field(default=None, repr=False, compare=False)  # (gang, order) 25.10
    district_law: str = field(default="", repr=False, compare=False)  # the law of a player-held district 25.10
    relics: tuple[str, ...] = field(default=(), repr=False, compare=False)  # relics held (world_state relics) 25.10
    # time
    birth_year: int = 0  # world year of birth; the character's year = birth_year + age_years
    age_years: int = 18
    age_weeks: int = 0
    weeks_lived: int = 0
    ap: int = 0
    # place
    district: str = ""
    housing: str = ""
    # body and mind
    stats: dict[str, int] = field(default_factory=_default_stats)
    skills: dict[str, SkillState] = field(default_factory=_default_skills)
    hp: int = 0
    mood: int = 0
    humanity: int = 0
    humanity_lost: int = 0  # permanent loss from implants; every 10 = EMP -1
    cyber_tolerance: str = "normal"  # hidden until scanned
    tolerance_known: bool = False
    chrome_free: bool = False
    # standing
    street_cred: int = 0
    status: int = 0
    fame: int = 0
    heat: int = 0
    faction_rep: dict[str, int] = field(default_factory=dict)  # gang / corp key -> -50..50
    fixer_rel: dict[str, int] = field(default_factory=dict)  # fixer key -> 0..100
    # money
    eddies: int = 0
    debts: list[DebtState] = field(default_factory=list)
    # luck
    luck_pool: int = 0
    glitches: int = 0
    death_glitch_used: bool = False
    # things
    cyberware: list[CyberItem] = field(default_factory=list)
    inventory: list[InventoryItem] = field(default_factory=list)
    # narrative picks (keys of traits.yaml lists)
    traits: list[str] = field(default_factory=list)
    marks: list[str] = field(default_factory=list)
    obsessions: list[str] = field(default_factory=list)
    # path and life
    vows: dict[str, int] = field(default_factory=dict)  # vow key -> streak in weeks
    career: CareerState | None = None
    flags: dict[str, Any] = field(default_factory=dict)
    npcs: list[NpcState] = field(default_factory=list)
    buffs: list[Buff] = field(default_factory=list)
    # cache for derived.collect_mods; never saved, validated against a signature on every read
    _mods_cache: Any = field(default=None, repr=False, compare=False)

    # -- cheap read helpers (no game rules) -------------------------------

    @property
    def current_year(self) -> int:
        return self.birth_year + self.age_years

    @property
    def cyber_keys(self) -> list[str]:
        return [c.key for c in self.cyberware]

    def has_cyberware(self, key: str) -> bool:
        return any(c.key == key for c in self.cyberware)

    def cyber_item(self, key: str) -> CyberItem | None:
        for c in self.cyberware:
            if c.key == key:
                return c
        return None

    @property
    def debt_total(self) -> int:
        return sum(d.principal for d in self.debts)

    def skill_level(self, skill: str) -> int:
        s = self.skills.get(skill)
        return s.level if s else 0

    def equipped_key(self, kind: str) -> str | None:
        for it in self.inventory:
            if it.kind == kind and it.equipped:
                return it.key
        return None

    @property
    def weapon_key(self) -> str | None:
        return self.equipped_key(KIND_WEAPON)

    @property
    def armor_key(self) -> str | None:
        return self.equipped_key(KIND_ARMOR)

    def npcs_of(self, kind: str, alive_only: bool = True) -> list[NpcState]:
        return [n for n in self.npcs if n.kind == kind and (n.alive or not alive_only)]

    # -- conversion to and from plain dicts (JSON-safe) ---------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "surname": self.surname, "preset": self.preset, "role": self.role,
            "role_affinity": dict(self.role_affinity), "origin": self.origin,
            "birth_year": self.birth_year, "age_years": self.age_years, "age_weeks": self.age_weeks,
            "weeks_lived": self.weeks_lived, "ap": self.ap,
            "district": self.district, "housing": self.housing,
            "stats": dict(self.stats),
            "skills": {k: {"level": v.level, "xp": v.xp} for k, v in self.skills.items()},
            "hp": self.hp, "mood": self.mood, "humanity": self.humanity,
            "humanity_lost": self.humanity_lost, "cyber_tolerance": self.cyber_tolerance,
            "tolerance_known": self.tolerance_known, "chrome_free": self.chrome_free,
            "street_cred": self.street_cred, "status": self.status, "fame": self.fame,
            "heat": self.heat, "faction_rep": dict(self.faction_rep),
            "fixer_rel": dict(self.fixer_rel),
            "eddies": self.eddies,
            "debts": [
                {"creditor": d.creditor, "principal": d.principal, "weekly_rate": d.weekly_rate,
                 "missed_weeks": d.missed_weeks, "creditor_kind": d.creditor_kind,
                 "creditor_ref": d.creditor_ref, "key": d.key}
                for d in self.debts
            ],
            "luck_pool": self.luck_pool, "glitches": self.glitches,
            "death_glitch_used": self.death_glitch_used,
            "cyberware": [
                {"key": c.key, "charge": c.charge, "complication": c.complication,
                 "installed_week": c.installed_week, "humanity_paid": c.humanity_paid}
                for c in self.cyberware
            ],
            "inventory": [
                {"key": i.key, "kind": i.kind, "equipped": i.equipped} for i in self.inventory
            ],
            "traits": list(self.traits), "marks": list(self.marks),
            "obsessions": list(self.obsessions),
            "vows": dict(self.vows),
            "career": (
                {"key": self.career.key, "rank": self.career.rank, "weeks": self.career.weeks}
                if self.career else {}
            ),
            "flags": dict(self.flags),
            "npcs": [
                {"name": n.name, "kind": n.kind, "role": n.role, "relation": n.relation,
                 "alive": n.alive, "data": dict(n.data)}
                for n in self.npcs
            ],
            "buffs": [
                {"source": b.source, "mods": dict(b.mods), "expires": b.expires} for b in self.buffs
            ],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CharacterState":
        """Inverse of to_dict. Missing keys fall back to the defaults, so old rows still load."""
        st = cls()
        for key in (
            "name", "surname", "preset", "role", "origin", "birth_year", "age_years", "age_weeks",
            "weeks_lived", "ap", "district", "housing", "hp", "mood", "humanity", "humanity_lost",
            "cyber_tolerance", "tolerance_known", "chrome_free", "street_cred", "status", "fame",
            "heat", "eddies", "luck_pool", "glitches", "death_glitch_used",
        ):
            if key in d and d[key] is not None:
                setattr(st, key, d[key])
        st.role_affinity = {k: float(v) for k, v in (d.get("role_affinity") or {}).items()}
        st.faction_rep = {k: int(v) for k, v in (d.get("faction_rep") or {}).items()}
        st.fixer_rel = {k: int(v) for k, v in (d.get("fixer_rel") or {}).items()}
        stats = _default_stats()
        stats.update({k: int(v) for k, v in (d.get("stats") or {}).items()})
        st.stats = stats
        skills = _default_skills()
        for k, v in (d.get("skills") or {}).items():
            skills[k] = SkillState(int(v.get("level", 0)), int(v.get("xp", 0)))
        st.skills = skills
        st.debts = [
            DebtState(
                creditor=x["creditor"], principal=int(x["principal"]),
                weekly_rate=float(x["weekly_rate"]), missed_weeks=int(x.get("missed_weeks", 0)),
                creditor_kind=x.get("creditor_kind", "other"),
                creditor_ref=x.get("creditor_ref"), key=x.get("key"),
            )
            for x in d.get("debts") or []
        ]
        st.cyberware = [
            CyberItem(
                key=x["key"], charge=int(x.get("charge", 100)),
                complication=bool(x.get("complication", False)),
                installed_week=int(x.get("installed_week", 0)),
                humanity_paid=int(x.get("humanity_paid", 0)),
            )
            for x in d.get("cyberware") or []
        ]
        st.inventory = [
            InventoryItem(x["key"], x["kind"], bool(x.get("equipped", False)))
            for x in d.get("inventory") or []
        ]
        st.traits = list(d.get("traits") or [])
        st.marks = list(d.get("marks") or [])
        st.obsessions = list(d.get("obsessions") or [])
        st.vows = {k: int(v) for k, v in (d.get("vows") or {}).items()}
        career = d.get("career") or {}
        st.career = (
            CareerState(career["key"], int(career.get("rank", 1)), int(career.get("weeks", 0)))
            if career.get("key") else None
        )
        st.flags = dict(d.get("flags") or {})
        st.npcs = [
            NpcState(
                name=x["name"], kind=x["kind"], role=x.get("role"),
                relation=int(x.get("relation", 50)), alive=bool(x.get("alive", True)),
                data=dict(x.get("data") or {}),
            )
            for x in d.get("npcs") or []
        ]
        st.buffs = [
            Buff(x["source"], {k: int(v) for k, v in (x.get("mods") or {}).items()},
                 x.get("expires", "kyiv_midnight"))
            for x in d.get("buffs") or []
        ]
        return st
