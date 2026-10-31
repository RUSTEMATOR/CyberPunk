"""Loads and validates all YAML content, then checks cross-references.

Usage:
    from app.content.loader import load_content
    content = load_content()          # raises ContentError on any problem

    python -m app.content.loader      # prints record counts, exit 0 when valid,
                                      # prints "file [record]: problem" and exits 1 otherwise;
                                      # warnings (enemy coverage, violent events without a
                                      # non-violent choice, availability of references, fill (incl. placeholder roles)
                                      # targets) are printed and counted by type (exit 0)
    python -m app.content.loader --strict   # every warning becomes an error (exit 1)
    python -m app.content.loader --dir PATH # load another content directory

cyberware*.yaml, enemies*.yaml and events*.yaml are read by glob and merged;
a key defined in two files is an error that names both files.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import BaseModel, ValidationError

from app.content.schemas import (
    Action,
    Vehicle,
    Heritage,
    Country,
    WorldPack,
    EXTRA_FACTIONS,
    Armor,
    BalanceModel,
    Career,
    Corp,
    Cyberware,
    Debt,
    District,
    Enemy,
    Era,
    Event,
    FallbackTexts,
    Fixer,
    FirstName,
    Gang,
    GigTemplate,
    Housing,
    NamesFile,
    Origin,
    Preset,
    Quest,
    Quickhack,
    Ripperdoc,
    Role,
    ShopItem,
    Surname,
    Icon,
    Tarot,
    TextEntry,
    TimelineEvent,
    Vow,
    Weapon,
    QUEST_GOAL_EVENTS,
    parse_bus_key,
    parse_mod_key,
    parse_world_effect_key,
    split_effect_key,
)

CONTENT_DIR = Path(__file__).resolve().parent.parent.parent / "content"

# Enemy coverage rule (PLAN.md 1.4): every faction needs at least one archetype
# at each danger level where it appears in gigs.
COVERAGE_GANG_DANGERS: tuple[int, ...] = (1, 2, 3)
COVERAGE_CORP_FACTIONS: tuple[str, ...] = ("arasaka", "militech", "kang_tao", "ncpd")
COVERAGE_CORP_DANGERS: tuple[int, ...] = (2, 3, 4, 5)

ENEMIES_LABEL = "enemies*.yaml"

# Content-size targets from PLAN.md 1.7c. Below the target is a warning (kind "fill").
FILL_TARGETS: dict[str, int] = {"tarot": 22, "vows": 4, "afterlife_shop": 8, "presets": 16}
FILL_TARGET_QUESTS: dict[str, int] = {"daily": 25, "weekly": 8}
# The world must not run out of eras before the 2077 setting.
ERA_MUST_REACH_YEAR = 2077

# Catalogs read through Content.available(kind, year); kind = attribute name of Content.
AVAILABLE_KINDS: tuple[str, ...] = (
    "cyberware", "quickhacks", "weapons", "armor", "districts", "gangs", "corps", "fixers",
    "ripperdocs", "presets", "roles", "origins", "enemies", "events", "gig_templates", "careers", "actions", "vehicles",
    "debts", "countries",
)
_NEG_INF, _POS_INF = -10**9, 10**9


@dataclass(frozen=True)
class Issue:
    file: str
    record: str | None
    message: str
    kind: str = ""  # warning type: coverage, violent, availability, eras, fill

    def __str__(self) -> str:
        where = f"{self.file} [{self.record}]" if self.record else self.file
        return f"{where}: {self.message}"


class ContentError(Exception):
    """Raised when content is invalid. `issues` lists every problem found."""

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__("\n".join(str(i) for i in issues))


def _window(rec: Any) -> tuple[int, int]:
    lo = rec.available_from
    hi = rec.available_until
    return (_NEG_INF if lo is None else lo, _POS_INF if hi is None else hi)


def _in_window(rec: Any, year: int) -> bool:
    lo, hi = _window(rec)
    return lo <= year <= hi


def _fmt_window(win: tuple[int, int]) -> str:
    lo, hi = win
    if lo == _NEG_INF and hi == _POS_INF:
        return "always"
    if lo == _NEG_INF:
        return f"until {hi}"
    if hi == _POS_INF:
        return f"from {lo}"
    return f"{lo}..{hi}"


@dataclass(frozen=True)
class Content:
    """All parsed content. Lookups are dicts keyed by record key / event id."""

    balance: BalanceModel
    presets: dict[str, Preset]
    roles: dict[str, Role]
    origins: dict[str, Origin]
    debts: dict[str, Debt]
    traits: dict[str, TextEntry]
    marks: dict[str, TextEntry]
    obsessions: dict[str, TextEntry]
    first_names: tuple[str, ...]  # every first name (no gender), kept for callers that only need a string
    last_names: tuple[str, ...]  # male (base) form of every surname
    given_names: tuple[FirstName, ...]  # first names with gender, in file order
    surnames: tuple[Surname, ...]  # surnames with male and female forms, in file order
    districts: dict[str, District]
    fixers: dict[str, Fixer]
    gangs: dict[str, Gang]
    corps: dict[str, Corp]
    ripperdocs: dict[str, Ripperdoc]
    cyberware: dict[str, Cyberware]
    quickhacks: dict[str, Quickhack]
    enemies: dict[str, Enemy]
    weapons: dict[str, Weapon]
    armor: dict[str, Armor]
    housing: dict[str, Housing]
    careers: dict[str, Career]
    gig_templates: dict[str, GigTemplate]
    events: dict[str, Event]
    fallback_texts: FallbackTexts
    eras: dict[str, Era]
    timeline: dict[str, TimelineEvent]
    vows: dict[str, Vow]
    tarot: dict[str, Tarot]
    quests: dict[str, Quest]
    afterlife_shop: dict[str, ShopItem]
    icons: dict[str, Icon]  # icons.yaml: the icon dictionary of app/bot/ui/style.py
    actions: dict[str, Action] = field(default_factory=dict)  # actions*.yaml: leisure, crime, gang deeds
    vehicles: dict[str, Vehicle] = field(default_factory=dict)  # vehicles.yaml
    heritages: dict[str, Heritage] = field(default_factory=dict)  # heritage.yaml: family roots (cosmetic)
    countries: dict[str, Country] = field(default_factory=dict)  # countries.yaml: the world beyond Night City
    # Merged kinds: {"cyberware": {key: file name}}, used for error messages and counts.
    sources: dict[str, dict[str, str]] = field(default_factory=dict)
    # Non-fatal problems. `--strict` turns them into errors.
    warnings: tuple[Issue, ...] = ()

    def available(self, kind: str, year: int) -> dict[str, Any]:
        """Records of one catalog that exist in `year` (bounds are inclusive; None = no limit).

        `kind` is the name of the Content attribute: cyberware, quickhacks, weapons, armor,
        districts, gangs, corps, fixers, ripperdocs, presets, roles, origins, enemies, events,
        gig_templates, careers. Game logic must use this instead of iterating the catalogs.
        """
        if kind not in AVAILABLE_KINDS:
            raise ValueError(f"unknown kind {kind!r}, allowed: {list(AVAILABLE_KINDS)}")
        catalog: dict[str, Any] = getattr(self, kind)
        return {
            key: rec for key, rec in catalog.items()
            if _in_window(rec, year)
        }

    def era_for_year(self, year: int) -> Era:
        """The era of a world year; before the first era returns the first, after the last the last."""
        ordered = sorted(self.eras.values(), key=lambda e: e.from_year)
        for era in ordered:
            if era.from_year <= year <= era.to_year:
                return era
        return ordered[0] if year < ordered[0].from_year else ordered[-1]

    def _merged_count(self, kind: str, total: int) -> str:
        per_file: dict[str, int] = {}
        for fname in self.sources.get(kind, {}).values():
            per_file[fname] = per_file.get(fname, 0) + 1
        if len(per_file) <= 1:
            return str(total)
        parts = ", ".join(f"{f} {n}" for f, n in sorted(per_file.items()))
        return f"{total} ({parts})"

    def counts(self) -> dict[str, str]:
        """Record counts per YAML file, for the loader report."""
        ft = self.fallback_texts
        variants = sum(
            sum(len(v) for v in val.values()) if isinstance(val, dict) else len(val)
            for val in (getattr(ft, f) for f in type(ft).model_fields)
        )
        return {
            "balance.yaml": f"{len(type(self.balance).model_fields)} sections",
            "presets.yaml": str(len(self.presets)),
            "roles.yaml": (
                f"{len(self.roles)} (filled {sum(not r.is_placeholder for r in self.roles.values())})"
            ),
            "origins.yaml": str(len(self.origins)),
            "debts.yaml": str(len(self.debts)),
            "traits.yaml": (
                f"{len(self.traits) + len(self.marks) + len(self.obsessions)} "
                f"(traits {len(self.traits)}, marks {len(self.marks)}, "
                f"obsessions {len(self.obsessions)})"
            ),
            "names.yaml": (
                f"{len(self.first_names) + len(self.last_names)} "
                f"(first {len(self.first_names)}, last {len(self.last_names)})"
            ),
            "districts.yaml": str(len(self.districts)),
            "fixers.yaml": str(len(self.fixers)),
            "gangs.yaml": str(len(self.gangs)),
            "corps.yaml": str(len(self.corps)),
            "ripperdocs.yaml": str(len(self.ripperdocs)),
            "cyberware*.yaml": self._merged_count("cyberware", len(self.cyberware)),
            "quickhacks.yaml": str(len(self.quickhacks)),
            "enemies*.yaml": self._merged_count("enemies", len(self.enemies)),
            "weapons.yaml": str(len(self.weapons)),
            "armor.yaml": str(len(self.armor)),
            "housing.yaml": str(len(self.housing)),
            "careers.yaml": str(len(self.careers)),
            "gig_templates.yaml": str(len(self.gig_templates)),
            "events*.yaml": self._merged_count("events", len(self.events)),
            "fallback_texts.yaml": f"{variants} variants",
            "eras.yaml": str(len(self.eras)),
            "timeline.yaml": str(len(self.timeline)),
            "vows.yaml": str(len(self.vows)),
            "tarot.yaml": str(len(self.tarot)),
            "icons.yaml": str(len(self.icons)),
            "quests.yaml": (
                f"{len(self.quests)} (daily "
                f"{sum(q.period == 'daily' for q in self.quests.values())}, weekly "
                f"{sum(q.period == 'weekly' for q in self.quests.values())})"
            ),
            "afterlife_shop.yaml": str(len(self.afterlife_shop)),
        }


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _fmt_validation(err: ValidationError) -> list[str]:
    out = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"])
        out.append(f"{loc}: {e['msg']}" if loc else e["msg"])
    return out


def _read_yaml(name: str, directory: Path, issues: list[Issue]) -> Any:
    path = directory / name
    if not path.exists():
        issues.append(Issue(name, None, "file is missing"))
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        issues.append(Issue(name, None, f"YAML syntax error: {e}"))
        return None


def _parse_list(
    name: str,
    raw: Any,
    model: type[BaseModel],
    id_field: str,
    issues: list[Issue],
) -> dict[str, Any]:
    """Validate a YAML list record by record and return {id: model}."""
    if raw is None:
        return {}
    if not isinstance(raw, list):
        issues.append(Issue(name, None, "top level must be a list of records"))
        return {}
    out: dict[str, Any] = {}
    for i, rec in enumerate(raw):
        rid = rec.get(id_field) if isinstance(rec, dict) else None
        label = str(rid) if rid is not None else f"#{i + 1}"
        try:
            obj = model.model_validate(rec)
        except ValidationError as e:
            for msg in _fmt_validation(e):
                issues.append(Issue(name, label, msg))
            continue
        oid = getattr(obj, id_field)
        if oid in out:
            issues.append(Issue(name, oid, "duplicate id"))
            continue
        out[oid] = obj
    return out


def _parse_merged(
    pattern: str,
    directory: Path,
    model: type[BaseModel],
    id_field: str,
    issues: list[Issue],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Read every file matching `pattern` (sorted by name) and merge the records.

    A key defined in two files is an error naming both files.
    Returns ({id: model}, {id: file name}).
    """
    names = sorted(p.name for p in directory.glob(pattern))
    if not names:
        issues.append(Issue(pattern, None, "no file matches this pattern"))
        return {}, {}
    out: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for name in names:
        part = _parse_list(name, _read_yaml(name, directory, issues), model, id_field, issues)
        for rid, obj in part.items():
            if rid in out:
                issues.append(Issue(
                    name, rid,
                    f"duplicate key {rid!r}: already defined in {sources[rid]} "
                    f"(keys must be unique across all {pattern})",
                ))
                continue
            out[rid] = obj
            sources[rid] = name
    return out, sources


def _parse_whole(name: str, raw: Any, model: type[BaseModel], issues: list[Issue]) -> Any:
    if raw is None:
        return None
    try:
        return model.model_validate(raw)
    except ValidationError as e:
        for msg in _fmt_validation(e):
            issues.append(Issue(name, None, msg))
        return None


def _parse_traits(raw: Any, issues: list[Issue]) -> tuple[dict, dict, dict]:
    name = "traits.yaml"
    if not isinstance(raw, dict) or set(raw) != {"traits", "marks", "obsessions"}:
        issues.append(Issue(name, None, "top level must have exactly: traits, marks, obsessions"))
        return {}, {}, {}
    parts = []
    for section in ("traits", "marks", "obsessions"):
        records = raw[section]
        if records is None:
            parts.append({})
            continue
        if not isinstance(records, list):
            issues.append(Issue(name, section, "must be a list of records"))
            parts.append({})
            continue
        sub: dict[str, TextEntry] = {}
        for i, rec in enumerate(records):
            rid = rec.get("key") if isinstance(rec, dict) else None
            label = f"{section}:{rid if rid is not None else '#' + str(i + 1)}"
            try:
                obj = TextEntry.model_validate(rec)
            except ValidationError as e:
                for msg in _fmt_validation(e):
                    issues.append(Issue(name, label, msg))
                continue
            if obj.key in sub:
                issues.append(Issue(name, label, "duplicate key"))
                continue
            sub[obj.key] = obj
        parts.append(sub)
    return parts[0], parts[1], parts[2]


# ---------------------------------------------------------------------------
# Cross-reference checks
# ---------------------------------------------------------------------------


def _effect_refs(effects: dict[str, Any] | None, c: Content, file: str, rid: str,
                 add: Callable[[str, str, str], None]) -> None:
    for key, val in (effects or {}).items():
        base, param = split_effect_key(key)
        if base == "later":
            for entry in val:
                _effect_refs(entry.get("effects"), c, file, rid, add)
                _effect_refs(entry.get("fail_effects"), c, file, rid, add)
            continue
        if base == "queue_event" and param not in c.events:
            add(file, rid, f"effect {key!r}: unknown event {param!r}")
        if base == "item_grant" and param not in c.weapons and param not in c.armor:
            add(file, rid, f"effect {key!r}: unknown item {param!r}, use a key from weapons.yaml or armor.yaml")
        if base == "vehicle_grant" and param not in c.vehicles:
            add(file, rid, f"effect {key!r}: unknown vehicle {param!r}, use a key from vehicles.yaml")
        if base == "employer" and param not in c.corps:
            add(file, rid, f"effect {key!r}: unknown corp {param!r}, corps: {sorted(c.corps)}")
        if base == "career_set" and param not in c.careers:
            add(file, rid, f"effect {key!r}: unknown career {param!r}, careers: {sorted(c.careers)}")
        if base == "cyberware_grant" and param not in c.cyberware:
            add(file, rid, f"effect {key!r}: unknown cyberware {param!r}")
        if base == "habit" and param not in {d.key for d in c.balance.economy.drugs.drugs}:
            add(file, rid, f"effect {key!r}: unknown drug {param!r}")
        if base == "path_xp" and param not in {pd.key for pd in c.balance.paths.paths}:
            add(file, rid, f"effect {key!r}: unknown path {param!r}")
        if base == "rel_fixer" and param not in c.fixers:
            add(file, rid, f"effect {key!r}: unknown fixer {param!r}")
        if base in ("faction_rep", "betray") and not (
                param in c.gangs or param in c.corps or param in ("maxtac", "cyberpsycho", "my_gang")):
            add(file, rid, f"effect {key!r}: unknown faction {param!r}, use a key from gangs.yaml or corps.yaml")
        if base == "vow_break" and param is not None and param not in c.vows:
            add(file, rid, f"effect {key!r}: unknown vow {param!r}, vows in vows.yaml: {sorted(c.vows)}")
        if base == "buff" and param not in c.tarot:
            add(file, rid,
                f"effect {key!r}: unknown tarot card {param!r}; buff takes a key from tarot.yaml "
                f"(a quest key gives nothing: quests have no mods), cards: {sorted(c.tarot)}")


def _check_cyberware_list(file: str, rid: str, keys: list[str], c: Content,
                          add: Callable[[str, str, str], None],
                          allow_not_for_sale: bool = False) -> None:
    """Gear rules shared by presets and enemies: keys exist, no repeats, one item per
    exclusive_group, `requires` satisfied, nothing smasher-only. Sale-blocked implants
    (`not_for_sale`, like maxtac_inhibitor) are allowed only where the caller says so (enemies)."""
    groups: dict[str, str] = {}
    for cw_key in keys:
        cw = c.cyberware.get(cw_key)
        if cw is None:
            add(file, rid, f"unknown cyberware {cw_key!r}")
            continue
        if keys.count(cw_key) > 1:
            add(file, rid, f"duplicate implant {cw_key!r}")
        if cw.exclusive_group:
            if cw.exclusive_group in groups and groups[cw.exclusive_group] != cw_key:
                add(file, rid,
                    f"{cw_key!r} conflicts with {groups[cw.exclusive_group]!r} ({cw.exclusive_group})")
            groups[cw.exclusive_group] = cw_key
        for req in cw.requires:
            if req not in keys:
                add(file, rid, f"{cw_key!r} requires {req!r}")
        if cw.smasher_only or cw.borg_risk:
            add(file, rid, f"{cw_key!r} is a full conversion: only a ripper installs it")
        if cw.not_for_sale and not allow_not_for_sale:
            add(file, rid,
                f"{cw_key!r} is not_for_sale: it can only be granted (career, event cyberware_grant) "
                "or worn by enemies, not given as starting gear")


def _check_mods(file: str, rid: str, mods: dict[str, int], c: Content, active_groups: set[str],
                add: Callable[[str, str, str], None]) -> None:
    """Catalog checks for mod keys; the grammar itself is validated by the schema."""
    for key in mods:
        try:
            base, params = parse_mod_key(key)
        except ValueError as e:  # already rejected by the schema, kept as a safety net
            add(file, rid, str(e))
            continue
        if base == "humanity_floor" and mods[key] > c.balance.mods_caps.humanity_floor:
            add(file, rid,
                f"mod {key!r}: {mods[key]} is above balance.mods_caps.humanity_floor "
                f"({c.balance.mods_caps.humanity_floor}), the cap on the summed floor")
        if base == "salary_pct" and params[0] != "all" and params[0] not in c.careers:
            add(file, rid,
                f"mod key {key!r}: unknown career {params[0]!r}, "
                f"careers in careers.yaml: {sorted(c.careers)} or 'all'")
        if base == "active" and params[0] != "all" and params[0] not in active_groups:
            add(file, rid,
                f"mod key {key!r}: unknown exclusive group {params[0]!r}, "
                f"groups of active implants in the cyberware catalog: {sorted(active_groups)} or 'all'")


def _bus_ref_errors(event: str, params: dict[str, Any], c: Content) -> list[str]:
    """Catalog check of `ref` parameters of a bus event (careers, districts): the key must exist."""
    out: list[str] = []
    for name, val in params.items():
        spec = QUEST_GOAL_EVENTS[event][name]
        if spec[0] == "ref":
            catalog = getattr(c, spec[1])
            if val not in catalog:
                out.append(f"{event}.{name}: unknown {spec[1]} key {val!r}, allowed: {sorted(catalog)}")
    return out


def _check_roles(c: Content, add: Callable[[str, str, str], None], active_groups: set[str]) -> None:
    """Emergent roles: affinity keys refer to real keys, thresholds and perks match balance.roles."""
    rb = c.balance.roles
    for role in c.roles.values():
        if role.is_placeholder:
            continue  # reported by the fill check
        for key in role.affinity:
            try:
                event, params = parse_bus_key(key)
            except ValueError as e:  # already rejected by the schema, kept as a safety net
                add("roles.yaml", role.key, str(e))
                continue
            for msg in _bus_ref_errors(event, params, c):
                add("roles.yaml", role.key, f"affinity {key!r}: {msg}")
        if len(role.rank_thresholds) != rb.max_rank:
            add("roles.yaml", role.key,
                f"rank_thresholds has {len(role.rank_thresholds)} values, balance.roles.max_rank is "
                f"{rb.max_rank}: write the affinity needed for rank 1..{rb.max_rank}")
        elif role.rank_thresholds[-1] > rb.affinity_max:
            add("roles.yaml", role.key,
                f"the last rank threshold {role.rank_thresholds[-1]} is above balance.roles.affinity_max "
                f"({rb.affinity_max}), the rank would never be reached")
        ranks = [p.rank for p in role.perks]
        if ranks != rb.perk_ranks:
            add("roles.yaml", role.key,
                f"perks are at ranks {ranks}, they must be exactly at {rb.perk_ranks} (balance.roles.perk_ranks)")
        for perk in role.perks:
            _check_mods("roles.yaml", f"{role.key}:{perk.rank}", perk.mods, c, active_groups, add)


def _check_disposition(c: Content, file: str, rid: str, d: Any,
                       add: Callable[[str, str, str], None]) -> None:
    for i, tb in enumerate(d.talk_bonus_if, start=1):
        for dist in tb.districts:
            if dist not in c.districts:
                add(file, rid, f"disposition.talk_bonus_if #{i}: unknown district {dist!r}")
        for org in tb.origins:
            if org not in c.origins:
                add(file, rid, f"disposition.talk_bonus_if #{i}: unknown origin {org!r}, origins: {sorted(c.origins)}")
        for role in tb.roles:
            if role not in c.roles:
                add(file, rid, f"disposition.talk_bonus_if #{i}: unknown role {role!r}, roles: {sorted(c.roles)}")
    if d.talk_gate is not None:
        for role in d.talk_gate.roles:
            if role not in c.roles:
                add(file, rid, f"disposition.talk_gate: unknown role {role!r}, roles: {sorted(c.roles)}")


def _check_dispositions(c: Content, add: Callable[[str, str, str], None]) -> None:
    for g in c.gangs.values():
        _check_disposition(c, "gangs.yaml", g.key, g.disposition, add)
    for corp in c.corps.values():
        _check_disposition(c, "corps.yaml", corp.key, corp.disposition, add)
    ex = c.balance.exits
    _check_disposition(c, "balance.yaml", "exits.maxtac", ex.maxtac, add)
    _check_disposition(c, "balance.yaml", "exits.cyberpsycho", ex.cyberpsycho, add)
    # quest goals: ref parameters (careers, districts)
    for q in c.quests.values():
        event = q.goal.get("event")
        params = {k: v for k, v in q.goal.items() if k != "event"}
        for msg in _bus_ref_errors(event, params, c):
            add("quests.yaml", q.key, f"goal {msg}")
    # shop: cat_item:<key> must be a chrome of the cat
    chrome_keys = {ch.key for ch in c.balance.cat.chrome}
    for item in c.afterlife_shop.values():
        if item.effect.startswith("cat_item:") and item.effect.partition(":")[2] not in chrome_keys:
            add("afterlife_shop.yaml", item.key,
                f"effect {item.effect!r}: unknown cat chrome, balance.cat.chrome keys: {sorted(chrome_keys)}")


def _check_refs(c: Content, issues: list[Issue]) -> None:
    def add(file: str, rid: str | None, msg: str) -> None:
        issues.append(Issue(file, rid, msg))

    cw_src = c.sources.get("cyberware", {})
    en_src = c.sources.get("enemies", {})
    ev_src = c.sources.get("events", {})
    for country in c.countries.values():  # owner request 25.10: the story of a city and its districts
        for eid in country.story:
            if eid not in c.events:
                add("world_*.yaml", country.key, f"story: unknown event {eid!r}")
        for g in (g for d in country.districts for g in c.districts[d].gangs):
            fa = c.gangs[g].fights_as if g in c.gangs else None
            if fa is not None and fa not in c.gangs and fa not in c.corps:
                add("world_*.yaml", g, f"fights_as: unknown faction {fa!r}")
    active_groups = {
        cw.exclusive_group for cw in c.cyberware.values() if cw.exclusive_group and cw.active
    }

    # fixer -> district
    for f in c.fixers.values():
        for d in f.districts:
            if d not in c.districts:
                add("fixers.yaml", f.key, f"unknown district {d!r}")

    # district -> fixer, gangs, corps
    for d in c.districts.values():
        fx = c.fixers.get(d.fixer)
        if fx is None:
            add("districts.yaml", d.key, f"unknown fixer {d.fixer!r}")
        elif d.key not in fx.districts:
            add("districts.yaml", d.key, f"fixer {d.fixer!r} does not list this district")
        for g in d.gangs:
            if g not in c.gangs:
                add("districts.yaml", d.key, f"unknown gang {g!r}")
        for corp in d.corps:
            if corp not in c.corps:
                add("districts.yaml", d.key, f"unknown corp {corp!r}")

    # origin -> start district, enemy gang
    for o in c.origins.values():
        if o.start_district not in c.districts:
            add("origins.yaml", o.key, f"unknown start_district {o.start_district!r}")
        if o.enemy_gang is not None and o.enemy_gang not in c.gangs:
            add("origins.yaml", o.key, f"unknown enemy_gang {o.enemy_gang!r}")
        if o.start_vow is not None and o.start_vow not in c.vows:
            add("origins.yaml", o.key,
                f"unknown start_vow {o.start_vow!r}, vows in vows.yaml: {sorted(c.vows)}")

    # debt creditor
    for dbt in c.debts.values():
        pools = {"gang": c.gangs, "corp": c.corps, "ripper": c.ripperdocs}
        pool = pools.get(dbt.creditor_kind)
        if pool is not None and dbt.creditor_ref not in pool:
            add("debts.yaml", dbt.key,
                f"unknown {dbt.creditor_kind} creditor_ref {dbt.creditor_ref!r}")

    # ripperdoc -> district
    for r in c.ripperdocs.values():
        if r.district is not None and r.district not in c.districts:
            add("ripperdocs.yaml", r.key, f"unknown district {r.district!r}")

    # cyberware requires, slot sanity
    for cw in c.cyberware.values():
        cw_file = cw_src.get(cw.key, "cyberware.yaml")
        for req in cw.requires:
            if req not in c.cyberware:
                add(cw_file, cw.key, f"unknown required cyberware {req!r}")
            elif req == cw.key:
                add(cw_file, cw.key, "requires itself")
        _check_mods(cw_file, cw.key, cw.mods, c, active_groups, add)

    # mods on weapons, armor and traits / marks / obsessions
    for w in c.weapons.values():
        _check_mods("weapons.yaml", w.key, w.mods, c, active_groups, add)
    for v in c.vehicles.values():
        _check_mods("vehicles.yaml", v.key, v.mods, c, active_groups, add)
    for a in c.armor.values():
        _check_mods("armor.yaml", a.key, a.mods, c, active_groups, add)
    for section, entries in (("traits", c.traits), ("marks", c.marks), ("obsessions", c.obsessions)):
        for t in entries.values():
            _check_mods("traits.yaml", f"{section}:{t.key}", t.mods, c, active_groups, add)

    # owner request 23.10: the countries of the world
    for country in c.countries.values():
        _check_mods("countries.yaml", country.key, country.mods, c, active_groups, add)
        _effect_refs(country.work.fail_effects, c, "countries.yaml", country.key, add)
        for deed in country.deeds:
            _effect_refs(deed.success.effects, c, "countries.yaml", f"{country.key}.{deed.key}", add)
            if deed.fail is not None:
                _effect_refs(deed.fail.effects, c, "countries.yaml", f"{country.key}.{deed.key}", add)
            if deed.check is not None and deed.fail is None:
                add("countries.yaml", f"{country.key}.{deed.key}", "a deed with a check needs a fail outcome")

    # mods on vow bonuses and tarot cards
    for vow in c.vows.values():
        for bonus in vow.streak_bonuses:
            _check_mods("vows.yaml", vow.key, bonus.mods, c, active_groups, add)
        _effect_refs(vow.break_effects, c, "vows.yaml", vow.key, add)
    for card in c.tarot.values():
        _check_mods("tarot.yaml", card.key, card.mods, c, active_groups, add)

    # enemies
    known_factions = set(c.gangs) | set(c.corps) | set(EXTRA_FACTIONS)
    for en in c.enemies.values():
        f = en_src.get(en.key, "enemies.yaml")
        for fac in en.factions:
            if fac not in known_factions:
                add(f, en.key,
                    f"unknown faction {fac!r}; allowed: gangs {sorted(c.gangs)}, "
                    f"corps {sorted(c.corps)}, or {list(EXTRA_FACTIONS)}")
        if en.weapon is not None and en.weapon not in c.weapons:
            add(f, en.key, f"unknown weapon {en.weapon!r}")
        if en.armor is not None and en.armor not in c.armor:
            add(f, en.key, f"unknown armor {en.armor!r}")
        _check_cyberware_list(f, en.key, en.cyberware, c, add, allow_not_for_sale=True)
        has_deck = any(
            (cw := c.cyberware.get(k)) is not None and cw.exclusive_group == "cyberdeck"
            for k in en.cyberware
        )
        for qh in en.quickhacks:
            if qh not in c.quickhacks:
                add(f, en.key, f"unknown quickhack {qh!r}")
        if len(set(en.quickhacks)) != len(en.quickhacks):
            add(f, en.key, "duplicate quickhack")
        if en.quickhacks and not has_deck:
            add(f, en.key, "has quickhacks but no cyberdeck (exclusive_group cyberdeck) in cyberware")
        if en.ai == "netrunner" and not (has_deck and en.quickhacks):
            add(f, en.key, "ai 'netrunner' needs a cyberdeck in cyberware and at least one quickhack")

    # housing -> district
    for h in c.housing.values():
        if h.district not in c.districts:
            add("housing.yaml", h.key, f"unknown district {h.district!r}")

    # careers -> corps, prerequisite career
    for car in c.careers.values():
        for corp in car.corps or []:
            if corp not in c.corps:
                add("careers.yaml", car.key, f"unknown corp {corp!r}")
        prereq = car.requirements.from_career
        if prereq is not None:
            if prereq.career not in c.careers:
                add("careers.yaml", car.key,
                    f"requirements.from_career: unknown career {prereq.career!r}, careers: {sorted(c.careers)}")
            elif prereq.career == car.key:
                add("careers.yaml", car.key, "requirements.from_career points at the career itself")

    # balance.cyberpsychosis -> content
    cp = c.balance.cyberpsychosis
    for role in cp.recruit.roles:
        if role not in c.roles:
            add("balance.yaml", "cyberpsychosis.recruit.roles",
                f"unknown role {role!r}, roles in roles.yaml: {sorted(c.roles)}")
    for cw in c.cyberware.values():
        floor = cw.mods.get("humanity_floor", 0)
        if floor > cp.humanity_after_contract:
            add("balance.yaml", "cyberpsychosis.humanity_after_contract",
                f"{cp.humanity_after_contract} is below the humanity_floor ({floor}) of {cw.key!r}: "
                "the contract would start the character under the leash")

    # preset starting gear, career and origin hint
    for r in c.presets.values():
        for w in r.gear.weapons:
            if w not in c.weapons:
                add("presets.yaml", r.key, f"unknown weapon {w!r}")
        if r.gear.armor is not None and r.gear.armor not in c.armor:
            add("presets.yaml", r.key, f"unknown armor {r.gear.armor!r}")
        if r.gear.vehicle is not None and r.gear.vehicle not in c.vehicles:
            add("presets.yaml", r.key, f"unknown vehicle {r.gear.vehicle!r}")
        _check_cyberware_list("presets.yaml", r.key, r.gear.cyberware, c, add)
        if r.starting_career is not None and r.starting_career not in c.careers:
            add("presets.yaml", r.key, f"unknown starting_career {r.starting_career!r}")
        if r.origin_hint is not None and r.origin_hint not in c.origins:
            add("presets.yaml", r.key, f"unknown origin_hint {r.origin_hint!r}, origins: {sorted(c.origins)}")
        if r.chrome_free_start and r.gear.cyberware:
            add("presets.yaml", r.key, "chrome_free_start preset cannot start with implants")

    # emergent roles
    _check_roles(c, add, active_groups)

    # faction dispositions and the exits section
    _check_dispositions(c, add)

    # balance -> content
    if c.balance.character.start_housing not in c.housing:
        add("balance.yaml", "character.start_housing",
            f"unknown housing {c.balance.character.start_housing!r}")
    ps = c.balance.character.parent_survival
    for okey in [*ps.origin_mult, *ps.orphan_origins]:
        if okey not in c.origins:
            add("balance.yaml", "character.parent_survival",
                f"unknown origin {okey!r}, origins: {sorted(c.origins)}")

    if c.balance.debt.collector_event not in c.events:
        add("balance.yaml", "debt.collector_event",
            f"unknown event {c.balance.debt.collector_event!r}")

    if c.balance.legacy.engram.conflict_event not in c.events:
        add("balance.yaml", "legacy.engram.conflict_event",
            f"unknown event {c.balance.legacy.engram.conflict_event!r}")

    # events
    known_factions = set(c.gangs) | set(c.corps) | set(EXTRA_FACTIONS)
    for ev in c.events.values():
        cond = ev.conditions
        if ev.faction is not None and ev.faction not in known_factions:
            add(ev_src.get(ev.id, "events.yaml"), ev.id,
                f"unknown faction {ev.faction!r}; allowed: gangs {sorted(c.gangs)}, "
                f"corps {sorted(c.corps)}, {list(EXTRA_FACTIONS)}")
        for d in cond.districts or []:
            if d not in c.districts:
                add(ev_src.get(ev.id, "events.yaml"), ev.id, f"conditions: unknown district {d!r}")
        for role in cond.roles or []:
            if role not in c.roles:
                add(ev_src.get(ev.id, "events.yaml"), ev.id,
                    f"conditions: unknown role {role!r}, roles in roles.yaml: {sorted(c.roles)}")
        for role, rank in (cond.min_role_rank or {}).items():
            if role not in c.roles:
                add(ev_src.get(ev.id, "events.yaml"), ev.id,
                    f"conditions: min_role_rank: unknown role {role!r}, roles in roles.yaml: {sorted(c.roles)}")
            if rank > c.balance.roles.max_rank:
                add(ev_src.get(ev.id, "events.yaml"), ev.id,
                    f"conditions: min_role_rank {role!r}: rank {rank} is above balance.roles.max_rank "
                    f"({c.balance.roles.max_rank})")
        for car in cond.careers or []:
            if car not in c.careers:
                add(ev_src.get(ev.id, "events.yaml"), ev.id, f"conditions: unknown career {car!r}")
        overlap = set(cond.requires) & set(cond.forbids)
        if overlap:
            add(ev_src.get(ev.id, "events.yaml"), ev.id, f"conditions: flags both required and forbidden: {sorted(overlap)}")
        for key in cond.employers or []:
            if key not in c.corps:
                add(ev_src.get(ev.id, "events.yaml"), ev.id, f"conditions: employers: unknown corp {key!r}")
        seasons_ok = {x.key for x in c.balance.weather.seasons}
        weather_ok = {x.key for x in c.balance.weather.kinds}
        for key in cond.seasons or []:
            if key not in seasons_ok:
                add(ev_src.get(ev.id, "events.yaml"), ev.id, f"conditions: seasons: unknown season {key!r}")
        for key in cond.weather or []:
            if key not in weather_ok:
                add(ev_src.get(ev.id, "events.yaml"), ev.id, f"conditions: weather: unknown weather {key!r}")
        for key in cond.lacks_cyberware:
            if key not in c.cyberware:
                add(ev_src.get(ev.id, "events.yaml"), ev.id, f"conditions: lacks_cyberware: unknown cyberware {key!r}")
        for key in cond.abilities:  # owner request 26.10: chrome abilities
            if key not in c.balance.chrome_abilities:
                add(ev_src.get(ev.id, "events.yaml"), ev.id, f"conditions: abilities: unknown ability {key!r}")
        for i, ch in enumerate(ev.choices, start=1):
            if ch.ability and ch.ability not in c.balance.chrome_abilities:
                add(ev_src.get(ev.id, "events.yaml"), ev.id, f"choice {i}: unknown ability {ch.ability!r}")
            _effect_refs(ch.effects, c, ev_src.get(ev.id, "events.yaml"), ev.id, add)
            for out in (ch.success, ch.fail):
                if out is not None:
                    _effect_refs(out.effects, c, ev_src.get(ev.id, "events.yaml"), ev.id, add)
            _grant_years(ev, ch, c, ev_src.get(ev.id, "events.yaml"), add)
    for cw in c.cyberware.values():  # owner request 26.10: the abilities and the build line of an implant exist
        for key in cw.abilities:
            if key not in c.balance.chrome_abilities:
                add("cyberware*.yaml", cw.key, f"abilities: unknown ability {key!r}")
        if cw.build and cw.build not in c.balance.chrome_sets:
            add("cyberware*.yaml", cw.key, f"build: unknown build line {cw.build!r}")
    # owner request 26.10 «всё должно вести к уникальным способностям»: one implant line, one ability of its own
    line_of: dict[str, str] = {}
    for cw in c.cyberware.values():
        if len(cw.abilities) != 1:
            add("cyberware*.yaml", cw.key, f"abilities: exactly one own ability, not {len(cw.abilities)}")
        line = cw.exclusive_group if cw.exclusive_group and (cw.exclusive_group.startswith("cx_")
                                                              or cw.build and cw.exclusive_group.startswith(f"{cw.build}_")) else cw.key
        for key in cw.abilities:
            if line_of.setdefault(key, line) != line:
                add("cyberware*.yaml", cw.key, f"ability {key!r} is already the own ability of {line_of[key]!r}")
    for key, cs in c.balance.chrome_sets.items():
        for need, bonus in cs.bonuses.items():
            for a in bonus.abilities:
                if line_of.setdefault(a, f"set:{key}") != f"set:{key}":
                    add("balance.yaml", f"chrome_sets.{key}.{need}", f"ability {a!r} is not unique: {line_of[a]!r}")
    for key, ab in c.balance.chrome_abilities.items():
        if ab.family and ab.family not in c.balance.chrome_abilities:
            add("balance.yaml", f"chrome_abilities.{key}", f"unknown family {ab.family!r}")
        for e in ab.events:
            if e not in c.events:
                add("balance.yaml", f"chrome_abilities.{key}", f"unknown event {e!r}")
    for key, cs in c.balance.chrome_sets.items():
        for need, bonus in cs.bonuses.items():
            for a in bonus.abilities:
                if a not in c.balance.chrome_abilities:
                    add("balance.yaml", f"chrome_sets.{key}.{need}", f"unknown ability {a!r}")
    for act in c.actions.values():  # small deeds: effects and the follow-up events must exist
        for out in list(act.success) + list(act.fail):
            _effect_refs(out.effects, c, "actions*.yaml", act.key, add)
            if out.event is not None and out.event not in c.events:
                add("actions*.yaml", act.key, f"event {out.event!r}: unknown event")
    for t in c.balance.economy.wealth.tiers:  # owner request: threats to the rich
        for eid in t.events:
            if eid not in c.events:
                add("balance.yaml economy", eid, f"wealth event {eid!r}: unknown event")
    for eid in c.balance.healing.depression_events:  # owner request: dark weeks of a low mood
        if eid not in c.events:
            add("balance.yaml healing", eid, f"depression event {eid!r}: unknown event")
    fishing = c.balance.chat_games.fishing  # owner request: the catches of the fishing
    for fc in fishing.catches:
        _effect_refs(fc.effects, c, "balance.yaml chat_games", fc.key, add)
        if fc.event is not None and fc.event not in c.events:
            add("balance.yaml chat_games", fc.key, f"event {fc.event!r}: unknown event")
    if len({r.tier for r in fishing.rods}) != len(fishing.rods):
        add("balance.yaml chat_games", "rods", "two rods share a tier")


def _grant_keys(effects: dict[str, Any] | None) -> list[str]:
    """`cyberware_grant` and `item_grant` keys of an effects dict, `later` entries included."""
    out: list[str] = []
    for key, val in (effects or {}).items():
        base, param = split_effect_key(key)
        if base in ("cyberware_grant", "item_grant", "vehicle_grant") and param:
            out.append(key)
        elif base == "later":
            for entry in val:
                out += _grant_keys(entry.get("effects")) + _grant_keys(entry.get("fail_effects"))
    return out


def _grant_years(ev: Event, ch: Any, c: Content, file: str, add: Callable[[str, str, str], None]) -> None:
    """An event may only grant an implant or an item that exists in every year the event can fire."""
    lo, hi = ev.available_from, ev.available_until
    for fx in [ch.effects] + [o.effects for o in (ch.success, ch.fail) if o is not None]:
        for key in _grant_keys(fx):
            base, param = split_effect_key(key)
            catalog = (c.cyberware if base == "cyberware_grant" else c.vehicles if base == "vehicle_grant"
                       else c.weapons if param in c.weapons else c.armor)
            rec = catalog.get(param)
            if rec is None:
                continue
            r_lo, r_hi = rec.available_from, rec.available_until
            if (r_lo is not None and (lo is None or lo < r_lo)) or (r_hi is not None and (hi is None or hi > r_hi)):
                add(file, ev.id, f"effect {key!r}: {param!r} exists only in {r_lo or '...'}..{r_hi or '...'}, "
                                 f"but the event fires in {lo or '...'}..{hi or '...'}; narrow the event's years")


def check_world_effects(effects: dict[str, Any], c: Content) -> list[str]:
    """Catalog and corridor checks of world effects (the grammar is checked by the schema).

    Returns error messages; empty = fine. Also used for Claude-written world events.
    """
    out: list[str] = []
    corr = c.balance.world_effects
    items = set(c.cyberware) | set(c.quickhacks) | set(c.weapons) | set(c.armor)
    for key, val in effects.items():
        try:
            base, param = parse_world_effect_key(key)
        except ValueError as e:  # already rejected by the schema, kept as a safety net
            out.append(str(e))
            continue
        rng = None
        if base in ("district_danger", "district_open", "district_close") and param not in c.districts:
            out.append(f"world effect {key!r}: unknown district {param!r}, districts: {sorted(c.districts)}")
        elif base == "gang_war" and param not in c.gangs:
            out.append(f"world effect {key!r}: unknown gang {param!r}, gangs: {sorted(c.gangs)}")
        elif base == "faction_power" and param not in set(c.gangs) | set(c.corps):
            out.append(
                f"world effect {key!r}: unknown faction {param!r}, "
                f"gangs: {sorted(c.gangs)}, corps: {sorted(c.corps)}"
            )
        elif base in ("unlock", "ban") and param not in items:
            out.append(
                f"world effect {key!r}: unknown item {param!r}; item keys come from "
                "cyberware*.yaml, quickhacks.yaml, weapons.yaml, armor.yaml"
            )
        elif base == "era" and param not in c.eras:
            out.append(f"world effect {key!r}: unknown era {param!r}, eras in eras.yaml: {sorted(c.eras)}")
        if base == "price_mult":
            rng = corr.price_mult
        elif base == "district_danger":
            rng = corr.district_danger
        elif base == "gang_war":
            rng = corr.gang_war_weeks
        elif base == "faction_power":
            rng = corr.faction_power
        elif base == "heat_mult":
            rng = corr.heat_mult
        elif base == "gig_pay_mult":
            rng = corr.gig_pay_mult
        elif base == "event_weight":
            rng = corr.event_weight
        elif base == "coin_shock":
            rng = corr.coin_shock
        if rng is not None and not isinstance(val, bool) and not rng.min <= val <= rng.max:
            out.append(
                f"world effect {key!r}: value {val} is outside the corridor "
                f"{rng.min:g}..{rng.max:g} (balance.yaml world_effects)"
            )
    for a, b in (("unlock", "ban"), ("district_open", "district_close")):
        both = {k.partition(".")[2] for k in effects if k.startswith(a + ".")} & {
            k.partition(".")[2] for k in effects if k.startswith(b + ".")
        }
        if both:
            out.append(f"world effects {a} and {b} on the same key: {sorted(both)}")
    return out


def _check_eras_and_timeline(c: Content, issues: list[Issue]) -> list[Issue]:
    """Eras: no gaps, no overlaps, cover the start year. Timeline: inside eras, valid effects."""
    warnings: list[Issue] = []
    ordered = sorted(c.eras.values(), key=lambda e: e.from_year)
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt.from_year > prev.to_year + 1:
            issues.append(Issue(
                "eras.yaml", nxt.key,
                f"gap in eras: {prev.key!r} ends in {prev.to_year}, {nxt.key!r} starts in "
                f"{nxt.from_year}; years {prev.to_year + 1}..{nxt.from_year - 1} have no era",
            ))
        elif nxt.from_year <= prev.to_year:
            issues.append(Issue(
                "eras.yaml", nxt.key,
                f"eras overlap: {prev.key!r} ends in {prev.to_year}, {nxt.key!r} starts in "
                f"{nxt.from_year}; the next era must start in {prev.to_year + 1}",
            ))
    start = c.balance.world.start_year
    if ordered and not ordered[0].from_year <= start <= max(e.to_year for e in ordered):
        issues.append(Issue(
            "eras.yaml", None,
            f"balance.world.start_year {start} is outside the eras ({ordered[0].from_year}.."
            f"{ordered[-1].to_year})",
        ))
    elif ordered and ordered[-1].to_year < ERA_MUST_REACH_YEAR:
        warnings.append(Issue(
            "eras.yaml", ordered[-1].key,
            f"eras end in {ordered[-1].to_year}: the world reaches {ERA_MUST_REACH_YEAR} and "
            "beyond with no era (the last era should run to 2200)",
            kind="eras",
        ))
    first, last = (ordered[0].from_year, ordered[-1].to_year) if ordered else (0, 0)
    for ev in c.timeline.values():
        if not first <= ev.year <= last:
            issues.append(Issue(
                "timeline.yaml", ev.id, f"year {ev.year} is outside the eras ({first}..{last})"
            ))
        for msg in check_world_effects(ev.effects, c):
            issues.append(Issue("timeline.yaml", ev.id, msg))
    return warnings


def _check_violent_events(c: Content) -> list[Issue]:
    """An event with a violent choice must also have a non-violent one (DESIGN.md 5a)."""
    ev_src = c.sources.get("events", {})
    out: list[Issue] = []
    for ev in c.events.values():
        violent = [ch for ch in ev.choices if ch.is_violent]
        if violent and len(violent) == len(ev.choices):
            out.append(Issue(
                ev_src.get(ev.id, "events.yaml"), ev.id,
                "violent rule: every choice is violent (start_combat or violent: true); "
                "add a choice without combat: pay, talk, run, surrender, call for help",
                kind="violent",
            ))
    return out


def _year_ranges(years: list[int]) -> str:
    """[2013, 2014, 2015, 2020] -> '2013..2015, 2020'."""
    parts: list[str] = []
    start = prev = None
    for y in sorted(years):
        if start is None:
            start = prev = y
        elif y == prev + 1:
            prev = y
        else:
            parts.append(str(start) if start == prev else f"{start}..{prev}")
            start = prev = y
    if start is not None:
        parts.append(str(start) if start == prev else f"{start}..{prev}")
    return ", ".join(parts)


def _check_district_service_coverage(c: Content) -> list[Issue]:
    """In every world year of a district there must be a fixer and a ripperdoc who work there.

    A fixer counts when it lists the district, is available that year and is not the legendary,
    reputation-gated one (Rogue); a ripperdoc counts when its district is this one or None
    (underground in every district). The world runs from the first era to the last.
    """
    out: list[Issue] = []
    if not c.eras:
        return out
    years = range(min(e.from_year for e in c.eras.values()), max(e.to_year for e in c.eras.values()) + 1)
    for d in c.districts.values():
        no_fixer: list[int] = []
        no_doc: list[int] = []
        for y in years:
            if not _in_window(d, y):
                continue
            if not any(d.key in f.districts and not f.legendary and f.min_street_cred == 0
                       and _in_window(f, y) for f in c.fixers.values()):
                no_fixer.append(y)
            if not any((r.district in (None, d.key) or (d.city != "nc" and r.district in c.districts
                        and c.districts[r.district].city == d.city)) and _in_window(r, y) for r in c.ripperdocs.values()):
                no_doc.append(y)
        if no_fixer:
            out.append(Issue("fixers.yaml", d.key,
                             f"district {d.key!r} has no available fixer in years {_year_ranges(no_fixer)}; "
                             "add a fixer for those years or widen an existing window",
                             kind="availability"))
        if no_doc:
            out.append(Issue("ripperdocs.yaml", d.key,
                             f"district {d.key!r} has no available ripperdoc in years {_year_ranges(no_doc)}; "
                             "add a ripperdoc for those years or widen an existing window",
                             kind="availability"))
    return out


def _check_availability(c: Content) -> list[Issue]:
    """References must not point at a record that is missing in the years of the referrer.

    Strong references (the referrer needs the target in all its years): main fixer of a district,
    origin district/gang, ripperdoc district, role and enemy gear, role career, implant requires.
    Weak references (a list of things that may be present; the engine filters it with
    Content.available): district gangs/corps, fixer districts, career corps, enemy factions,
    event conditions. Weak ones only warn when the two year ranges never meet.
    """
    out: list[Issue] = []
    cw_src = c.sources.get("cyberware", {})
    en_src = c.sources.get("enemies", {})
    ev_src = c.sources.get("events", {})

    def check(file: str, rid: str, rec: Any, label: str, target_kind: str, key: str | None,
              strong: bool) -> None:
        if key is None:
            return
        target = getattr(c, target_kind).get(key)
        if target is None:  # unknown keys are reported as errors elsewhere
            return
        rlo, rhi = _window(rec)
        tlo, thi = _window(target)
        ok = (tlo <= rlo and rhi <= thi) if strong else (rlo <= thi and tlo <= rhi)
        if not ok:
            need = "all years of this record" if strong else "any year of this record"
            out.append(Issue(
                file, rid,
                f"{label} {key!r} is available {_fmt_window((tlo, thi))}, this record "
                f"{_fmt_window((rlo, rhi))}; the target must exist in {need}",
                kind="availability",
            ))

    for d in c.districts.values():
        # The named main fixer may be a later-era character (Regina Jones from 2070), so it only has to
        # overlap the district; the real guarantee is the per-year coverage check below.
        check("districts.yaml", d.key, d, "fixer", "fixers", d.fixer, False)
        for g in d.gangs:
            check("districts.yaml", d.key, d, "gang", "gangs", g, False)
        for corp in d.corps:
            check("districts.yaml", d.key, d, "corp", "corps", corp, False)
    for f in c.fixers.values():
        for d in f.districts:
            check("fixers.yaml", f.key, f, "district", "districts", d, False)
    for o in c.origins.values():
        check("origins.yaml", o.key, o, "start_district", "districts", o.start_district, True)
        check("origins.yaml", o.key, o, "enemy_gang", "gangs", o.enemy_gang, True)
    for r in c.ripperdocs.values():
        check("ripperdocs.yaml", r.key, r, "district", "districts", r.district, True)
    for dbt in c.debts.values():
        creditor_pool = {"gang": "gangs", "corp": "corps", "ripper": "ripperdocs"}.get(dbt.creditor_kind)
        if creditor_pool is not None:
            check("debts.yaml", dbt.key, dbt, "creditor", creditor_pool, dbt.creditor_ref, True)
    for cw in c.cyberware.values():
        for req in cw.requires:
            check(cw_src.get(cw.key, "cyberware.yaml"), cw.key, cw, "required implant", "cyberware", req, True)
    for car in c.careers.values():
        for corp in car.corps or []:
            check("careers.yaml", car.key, car, "corp", "corps", corp, False)
    for pre in c.presets.values():
        for w in pre.gear.weapons:
            check("presets.yaml", pre.key, pre, "weapon", "weapons", w, True)
        check("presets.yaml", pre.key, pre, "armor", "armor", pre.gear.armor, True)
        check("presets.yaml", pre.key, pre, "vehicle", "vehicles", pre.gear.vehicle, True)
        for k in pre.gear.cyberware:
            check("presets.yaml", pre.key, pre, "implant", "cyberware", k, True)
        check("presets.yaml", pre.key, pre, "starting_career", "careers", pre.starting_career, True)
        check("presets.yaml", pre.key, pre, "origin_hint", "origins", pre.origin_hint, False)
    for en in c.enemies.values():
        f = en_src.get(en.key, "enemies.yaml")
        check(f, en.key, en, "weapon", "weapons", en.weapon, True)
        check(f, en.key, en, "armor", "armor", en.armor, True)
        for k in en.cyberware:
            check(f, en.key, en, "implant", "cyberware", k, True)
        for k in en.quickhacks:
            check(f, en.key, en, "quickhack", "quickhacks", k, True)
        for fac in en.factions:
            check(f, en.key, en, "faction", "gangs" if fac in c.gangs else "corps", fac, False)
    out += _check_district_service_coverage(c)
    for ev in c.events.values():
        f = ev_src.get(ev.id, "events.yaml")
        for d in ev.conditions.districts or []:
            check(f, ev.id, ev, "district", "districts", d, False)
        for k in ev.conditions.roles or []:
            check(f, ev.id, ev, "role", "roles", k, False)
        for k in ev.conditions.careers or []:
            check(f, ev.id, ev, "career", "careers", k, False)
    return out


def _check_fill_targets(c: Content) -> list[Issue]:
    """Content that is still being filled (PLAN.md 1.7): below the target size is a warning."""
    out: list[Issue] = []

    def short(file: str, what: str, have: int, want: int) -> None:
        if have < want:
            out.append(Issue(file, None, f"fill target: {have} of {want} {what}", kind="fill"))

    short("presets.yaml", "presets", len(c.presets), FILL_TARGETS["presets"])
    for role in c.roles.values():
        if role.is_placeholder:
            out.append(Issue(
                "roles.yaml", role.key,
                "fill target: role is a placeholder (no affinity, decay_pct, rank_thresholds, perks); "
                "fill it like 'solo'",
                kind="fill",
            ))
    honest = c.balance.exits.situation.honest_vow
    if honest not in c.vows:
        out.append(Issue(
            "balance.yaml", "exits.situation.honest_vow",
            f"fill target: vow {honest!r} is not in vows.yaml yet (step 1.7c adds it)",
            kind="fill",
        ))
    short("tarot.yaml", "cards", len(c.tarot), FILL_TARGETS["tarot"])
    short("vows.yaml", "vows", len(c.vows), FILL_TARGETS["vows"])
    short("afterlife_shop.yaml", "items", len(c.afterlife_shop), FILL_TARGETS["afterlife_shop"])
    for period, want in FILL_TARGET_QUESTS.items():
        have = sum(q.period == period for q in c.quests.values())
        short("quests.yaml", f"{period} quests", have, want)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _check_coverage(c: Content) -> list[Issue]:
    """Enemy coverage rule: every faction has an archetype at each required danger level.

    These are warnings while the enemy catalog is being filled; `strict` makes them errors.
    """
    required: dict[str, tuple[int, ...]] = {g: COVERAGE_GANG_DANGERS for g, gg in c.gangs.items() if not gg.fights_as}
    for corp in COVERAGE_CORP_FACTIONS:
        if corp in c.corps:
            required[corp] = COVERAGE_CORP_DANGERS
    have: dict[str, set[int]] = {}
    for en in c.enemies.values():
        for fac in en.factions:
            have.setdefault(fac, set()).add(en.danger)
    out: list[Issue] = []
    for fac, levels in required.items():
        missing = [d for d in levels if d not in have.get(fac, set())]
        if missing:
            out.append(Issue(
                ENEMIES_LABEL, fac,
                f"coverage: no archetype for danger {', '.join(map(str, missing))} "
                f"(need every level {levels[0]}-{levels[-1]})",
                kind="coverage",
            ))
    return out


def _check_icons(c: Content, issues: list[Issue]) -> None:
    """Every icon key the code uses (app/bot/ui/style.py REQUIRED_ICONS) and an arcana icon per tarot card."""
    from app.bot.ui.style import ARCANA_PREFIX, REQUIRED_ICONS  # local: style reads icons.yaml itself at import

    for key in REQUIRED_ICONS:
        if key not in c.icons:
            issues.append(Issue("icons.yaml", key, "icon used by the code is missing"))
    for card in c.tarot:
        if ARCANA_PREFIX + card not in c.icons:
            issues.append(Issue("icons.yaml", ARCANA_PREFIX + card, f"no arcana icon for tarot card {card!r}"))


def load_content(directory: Path | str | None = None, strict: bool = False) -> Content:
    """Load, validate and cross-check all content. Raises ContentError.

    Enemy coverage gaps are returned in `Content.warnings`; with `strict=True` they raise.
    """
    base = Path(directory) if directory else CONTENT_DIR
    issues: list[Issue] = []

    def rd(name: str) -> Any:
        return _read_yaml(name, base, issues)

    balance = _parse_whole("balance.yaml", rd("balance.yaml"), BalanceModel, issues)
    presets = _parse_list("presets.yaml", rd("presets.yaml"), Preset, "key", issues)
    roles = _parse_list("roles.yaml", rd("roles.yaml"), Role, "key", issues)
    origins = _parse_list("origins.yaml", rd("origins.yaml"), Origin, "key", issues)
    debts = _parse_list("debts.yaml", rd("debts.yaml"), Debt, "key", issues)
    traits, marks, obsessions = _parse_traits(rd("traits.yaml"), issues)
    names = _parse_whole("names.yaml", rd("names.yaml"), NamesFile, issues)
    districts = _parse_list("districts.yaml", rd("districts.yaml"), District, "key", issues)
    fixers = _parse_list("fixers.yaml", rd("fixers.yaml"), Fixer, "key", issues)
    gangs = _parse_list("gangs.yaml", rd("gangs.yaml"), Gang, "key", issues)
    corps = _parse_list("corps.yaml", rd("corps.yaml"), Corp, "key", issues)
    ripperdocs = _parse_list("ripperdocs.yaml", rd("ripperdocs.yaml"), Ripperdoc, "key", issues)
    cyberware, cw_sources = _parse_merged("cyberware*.yaml", base, Cyberware, "key", issues)
    if balance is not None and balance.cyber.netrun_humanity_free:  # owner request 24.10
        cyberware = {k: (cw.model_copy(update={"humanity_cost": 0}) if humanity_free(cw) else cw)
                     for k, cw in cyberware.items()}
    quickhacks = _parse_list("quickhacks.yaml", rd("quickhacks.yaml"), Quickhack, "key", issues)
    enemies, en_sources = _parse_merged(ENEMIES_LABEL, base, Enemy, "key", issues)
    weapons = _parse_list("weapons.yaml", rd("weapons.yaml"), Weapon, "key", issues)
    armor = _parse_list("armor.yaml", rd("armor.yaml"), Armor, "key", issues)
    housing = _parse_list("housing.yaml", rd("housing.yaml"), Housing, "key", issues)
    careers = _parse_list("careers.yaml", rd("careers.yaml"), Career, "key", issues)
    gig_templates = _parse_list(
        "gig_templates.yaml", rd("gig_templates.yaml"), GigTemplate, "key", issues
    )
    events, ev_sources = _parse_merged("events*.yaml", base, Event, "id", issues)
    fallback = _parse_whole("fallback_texts.yaml", rd("fallback_texts.yaml"), FallbackTexts, issues)
    eras = _parse_list("eras.yaml", rd("eras.yaml"), Era, "key", issues)
    timeline = _parse_list("timeline.yaml", rd("timeline.yaml"), TimelineEvent, "id", issues)
    vows = _parse_list("vows.yaml", rd("vows.yaml"), Vow, "key", issues)
    tarot = _parse_list("tarot.yaml", rd("tarot.yaml"), Tarot, "key", issues)
    quests = _parse_list("quests.yaml", rd("quests.yaml"), Quest, "key", issues)
    afterlife_shop = _parse_list(
        "afterlife_shop.yaml", rd("afterlife_shop.yaml"), ShopItem, "key", issues
    )
    icons = _parse_list("icons.yaml", rd("icons.yaml"), Icon, "key", issues)
    actions, _act_sources = _parse_merged("actions*.yaml", base, Action, "key", issues)
    vehicles = _parse_list("vehicles.yaml", rd("vehicles.yaml"), Vehicle, "key", issues) if (base / "vehicles.yaml").exists() else {}
    heritages = _parse_list("heritage.yaml", rd("heritage.yaml"), Heritage, "key", issues) if (base / "heritage.yaml").exists() else {}
    countries = _parse_list("countries.yaml", rd("countries.yaml"), Country, "key", issues) if (base / "countries.yaml").exists() else {}
    # owner request 25.10: the cities of the world (world_*.yaml): their districts, fixers, gangs, rippers, homes, story
    for wpath in sorted(base.glob("world_*.yaml")):
        wname = wpath.name
        raw = _read_yaml(wname, base, issues)
        for pack in _parse_list(wname, raw, WorldPack, "country", issues).values():
            if pack.country not in countries:
                issues.append(Issue(wname, pack.country, "unknown country"))
                continue
            dmap = _parse_list(wname, [dict(d, city=pack.country) for d in pack.districts], District, "key", issues)
            for target, recs, model in ((districts, None, None), (fixers, pack.fixers, Fixer), (gangs, pack.gangs, Gang),
                                        (ripperdocs, pack.ripperdocs, Ripperdoc), (housing, pack.housing, Housing)):
                got = dmap if recs is None else _parse_list(wname, recs, model, "key", issues)
                for k, v in got.items():
                    if k in target:
                        issues.append(Issue(wname, k, "duplicate id"))
                    target[k] = v
            countries[pack.country] = countries[pack.country].model_copy(update={
                "districts": list(dmap), "story_title": pack.story_title, "story": list(pack.story),
                "police": pack.police})

    # Empty files are errors: each file needs at least one record.
    for fname, coll in (
        ("presets.yaml", presets), ("roles.yaml", roles), ("origins.yaml", origins), ("debts.yaml", debts),
        ("traits.yaml", traits), ("districts.yaml", districts), ("fixers.yaml", fixers),
        ("gangs.yaml", gangs), ("corps.yaml", corps), ("ripperdocs.yaml", ripperdocs),
        ("cyberware*.yaml", cyberware), ("quickhacks.yaml", quickhacks), (ENEMIES_LABEL, enemies),
        ("weapons.yaml", weapons), ("armor.yaml", armor),
        ("housing.yaml", housing), ("careers.yaml", careers),
        ("gig_templates.yaml", gig_templates), ("events*.yaml", events),
        ("eras.yaml", eras), ("timeline.yaml", timeline), ("vows.yaml", vows),
        ("tarot.yaml", tarot), ("quests.yaml", quests), ("afterlife_shop.yaml", afterlife_shop),
        ("icons.yaml", icons),
    ):
        if not coll and not any(i.file == fname for i in issues):
            issues.append(Issue(fname, None, "no records"))

    if issues or balance is None or names is None or fallback is None:
        raise ContentError(issues)

    content = Content(
        balance=balance,
        presets=presets,
        roles=roles,
        origins=origins,
        debts=debts,
        traits=traits,
        marks=marks,
        obsessions=obsessions,
        first_names=tuple(n.name for n in names.first_names),
        last_names=tuple(s.male for s in names.last_names),
        given_names=tuple(names.first_names),
        surnames=tuple(names.last_names),
        districts=districts,
        fixers=fixers,
        gangs=gangs,
        corps=corps,
        ripperdocs=ripperdocs,
        cyberware=cyberware,
        quickhacks=quickhacks,
        enemies=enemies,
        weapons=weapons,
        armor=armor,
        housing=housing,
        careers=careers,
        gig_templates=gig_templates,
        events=events,
        fallback_texts=fallback,
        eras=eras,
        timeline=timeline,
        vows=vows,
        tarot=tarot,
        quests=quests,
        afterlife_shop=afterlife_shop,
        icons=icons,
        actions=actions,
        vehicles=vehicles,
        heritages=heritages,
        countries=countries,
        sources={"cyberware": cw_sources, "enemies": en_sources, "events": ev_sources},
    )
    _check_refs(content, issues)
    _check_icons(content, issues)
    warnings = _check_coverage(content)
    warnings += _check_eras_and_timeline(content, issues)
    warnings += _check_violent_events(content)
    warnings += _check_availability(content)
    warnings += _check_fill_targets(content)
    if strict:
        issues.extend(warnings)
    if issues:
        raise ContentError(issues)
    return replace(content, warnings=tuple(warnings))


def humanity_free(cw: Cyberware) -> bool:
    """Owner request 24.10: the neural port, the decks, the netrun chips and the Blackwall scripts are tools, not flesh:
    they take no humanity."""
    return cw.category == "netrun" or cw.deck is not None or cw.key.startswith("bw_")


def load_content_or_exit(directory: Path | str | None = None, strict: bool = False,
                         show_warnings: bool = True) -> Content:
    """For bot startup: print every problem as 'file [record]: message' and exit(1).

    Warnings are printed to stderr and do not stop the start.
    """
    try:
        content = load_content(directory, strict=strict)
    except ContentError as e:
        for issue in e.issues:
            print(f"CONTENT ERROR {issue}", file=sys.stderr)
        sys.exit(1)
    if show_warnings:
        for issue in content.warnings:
            print(f"CONTENT WARNING {issue}", file=sys.stderr)
    return content


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.content.loader")
    parser.add_argument("--strict", action="store_true",
                        help="turn all warnings into errors")
    parser.add_argument("--dir", default=None, help="content directory (default: content/)")
    args = parser.parse_args()
    content = load_content_or_exit(args.dir, strict=args.strict, show_warnings=False)
    width = max(len(k) for k in content.counts())
    for fname, count in content.counts().items():
        print(f"{fname:<{width}}  {count}")
    for issue in content.warnings:
        print(f"WARNING {issue}")
    by_kind: dict[str, int] = {}
    for issue in content.warnings:
        by_kind[issue.kind or "other"] = by_kind.get(issue.kind or "other", 0) + 1
    violent_ids = sorted(i.record for i in content.warnings if i.kind == "violent" and i.record)
    if violent_ids:
        print(f"violent events without a non-violent choice ({len(violent_ids)}): {', '.join(violent_ids)}")
    if by_kind:
        summary = ", ".join(f"{k} {n}" for k, n in sorted(by_kind.items()))
        suffix = f" ({len(content.warnings)} warnings: {summary}; use --strict to fail on them)"
    else:
        suffix = ""
    print(f"content OK{suffix}")


if __name__ == "__main__":
    main()
