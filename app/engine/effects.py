"""The only place where a character changes (PLAN 2.5, architecture rule 3).

`apply_effects(state, effects, content, rng, year)` applies an `effects` dict (the keys of
schemas.validate_effects) to a CharacterState, clamps every scale and returns an AppliedEffects:
the real changes for the UI and the narrator, and the followups the caller must act on
(a fight, jail, death, a breakdown). The engine never starts those itself.

State is mutated in place; the caller saves it afterwards. Randomness only through `rng`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from app.content.loader import Content
from app.content.schemas import EFFECT_LATER, split_effect_key, validate_effects
from app.engine import derived
from app.engine.character_gen import roll_first_name, surname_for
from app.engine.dice import choice, clamp, half_up, stochastic_round
from app.engine.state import (
    FLAG_STORY_QUEUE,
    FLAG_BETRAYED_PREFIX,
    FLAG_EMPLOYER,
    FLAG_FUGITIVE,
    FLAG_INFECTION,
    FLAG_LATER,
    FLAG_TOLERANCE_SHOWN,
    KIND_ARMOR,
    KIND_WEAPON,
    Buff,
    CareerState,
    CharacterState,
    CyberItem,
    DebtState,
    InventoryItem,
    NpcState,
)

# Followup kinds.
FU_START_COMBAT = "start_combat"  # value: danger 1..5
FU_JAIL = "jail"  # value: weeks
FU_DEATH = "death"  # value: cause key
FU_DEATH_SAVE = "death_save"  # hp fell to 0: roll the death save
FU_CYBERPSYCHOSIS = "cyberpsychosis"  # humanity fell to 0: the breakdown episode
FU_HUMANITY_WARNING = "humanity_warning"  # value: "low" | "critical" (balance.cyber.warn_humanity_*)
FU_VOW_BREAK = "vow_break"  # value: vow key; the break effects are already applied
FU_VANISH = "vanish"  # value: weeks. Taken away and held (surrender outcome vanished); the weeks pass without events
FU_RAID = "raid"  # value: danger; data: faction (ncpd | maxtac), src (raid | hunt). A police raid or a MaxTac hunt came

# Numeric fields whose real change is reported in AppliedEffects.deltas.
_TRACKED = (
    "eddies", "hp", "mood", "humanity", "humanity_lost", "street_cred", "status", "fame", "heat",
    "luck_pool", "glitches",
)

_MAX_DEPTH = 3  # break effects of a vow can break another vow, but not forever


@dataclass
class Followup:
    kind: str
    value: Any = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppliedEffects:
    """What an effects dict really did. Numbers are the actual change after clamps."""

    requested: dict[str, int | bool] = field(default_factory=dict)
    deltas: dict[str, int] = field(default_factory=dict)  # tracked fields + "debt" (total principal)
    clamped: dict[str, int] = field(default_factory=dict)  # field -> part of the request that did not fit
    overflow_to_debt: int = 0  # eddies below zero that became debt
    player_deltas: dict[str, int] = field(default_factory=dict)  # dirt, tokens: live on the player
    skill_xp: dict[str, int] = field(default_factory=dict)
    level_ups: list[tuple[str, int]] = field(default_factory=list)  # (skill, new level)
    cyberware_added: list[str] = field(default_factory=list)
    cyberware_removed: list[str] = field(default_factory=list)
    debts_created: list[str] = field(default_factory=list)  # creditors of debts opened by this call
    flags_set: list[str] = field(default_factory=list)
    flags_cleared: list[str] = field(default_factory=list)
    vows_broken: list[str] = field(default_factory=list)
    npcs_added: list[NpcState] = field(default_factory=list)
    rel_changes: list[tuple[str, int]] = field(default_factory=list)  # (npc name, change)
    fixer_rel: dict[str, int] = field(default_factory=dict)
    faction_rep: dict[str, int] = field(default_factory=dict)  # faction key -> real change of the standing
    betrayed: list[str] = field(default_factory=list)  # factions marked as cheated by this call
    buffs_added: list[Buff] = field(default_factory=list)
    career_change: tuple[str | None, str | None] | None = None  # (old key, new key)
    employer: str | None = None  # corp key named by `employer:<corp>`
    items_added: list[tuple[str, str]] = field(default_factory=list)  # (kind, key) put into the inventory
    items_removed: list[tuple[str, str]] = field(default_factory=list)  # (kind, key) taken away (item_take)
    biz_lines: list[str] = field(default_factory=list)  # owner request: what a story did to a business
    vehicle_sold: tuple[str, int] | None = None  # `vehicle_grant` replaced this ride: (old key, eddies paid)
    later_added: list[dict[str, Any]] = field(default_factory=list)  # delayed outcomes scheduled (with `due`)
    tolerance_revealed: str | None = None  # `scan_tolerance`: the true type, now known
    debt_grace: bool = False  # `debt_grace`: the missed weeks of the debts were forgiven
    char_levels: int = 0  # owner request 24.10: character levels gained (engine/perks.py)
    infection: bool = False
    path_xp: int = 0  # owner request: the xp of the career path (fixer, ripper, politician)
    path_rank_up: int | None = None  # the new rank of the path, when it grew
    followups: list[Followup] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # ignored or adjusted parts, for logs and debugging

    def narrator_effects(self) -> dict[str, Any]:
        """Compact `effects` object for the narrator payload (DESIGN.md section 14)."""
        out: dict[str, Any] = {k: v for k, v in self.deltas.items() if v}
        out.pop("humanity_lost", None)
        fixer_total = sum(self.fixer_rel.values())
        if fixer_total:
            out["fixer_rel"] = fixer_total
        if self.skill_xp:
            out["skill_xp"] = dict(self.skill_xp)
        if self.level_ups:
            out["level_ups"] = [f"{s}:{n}" for s, n in self.level_ups]
        if self.cyberware_added:
            out["cyberware_added"] = list(self.cyberware_added)
        if self.cyberware_removed:
            out["cyberware_removed"] = list(self.cyberware_removed)
        if self.vows_broken:
            out["vows_broken"] = list(self.vows_broken)
        if self.career_change:
            out["career"] = self.career_change[1]
        if self.player_deltas:
            out.update({k: v for k, v in self.player_deltas.items() if v})
        if self.infection:
            out["infection"] = True
        if self.items_added:
            out["items_added"] = [k for _, k in self.items_added]
        if self.later_added:
            out["later_weeks"] = [int(x["weeks"]) for x in self.later_added]
        if self.followups:
            out["followups"] = [f.kind for f in self.followups]
        return out

    @property
    def is_empty(self) -> bool:
        return not (
            any(self.deltas.values()) or self.skill_xp or self.cyberware_added
            or self.cyberware_removed or self.flags_set or self.flags_cleared or self.vows_broken
            or self.npcs_added or self.rel_changes or self.fixer_rel or self.faction_rep or self.betrayed
            or self.buffs_added or self.items_added or self.items_removed or self.later_added or self.tolerance_revealed
            or self.debt_grace or self.employer or self.path_xp
            or self.career_change or self.followups or self.player_deltas or self.infection
        )

    def followup(self, kind: str) -> Followup | None:
        for f in self.followups:
            if f.kind == kind:
                return f
        return None


class _Applier:
    def __init__(
        self, state: CharacterState, content: Content, rng: random.Random, year: int,
        heat_mult: float,
    ) -> None:
        self.s = state
        self.c = content
        self.rng = rng
        self.year = year
        self.heat_mult = heat_mult
        self.out = AppliedEffects()

    # -- scales ---------------------------------------------------------

    def _set(self, name: str, new: int, lo: int, hi: int, requested_delta: int) -> int:
        old = getattr(self.s, name)
        value = clamp(new, lo, hi)
        setattr(self.s, name, value)
        lost = requested_delta - (value - old)
        if lost:
            self.out.clamped[name] = self.out.clamped.get(name, 0) + lost
        return value

    def _bump(self, name: str, delta: int, lo: int, hi: int) -> int:
        return self._set(name, getattr(self.s, name) + delta, lo, hi, delta)

    def _hp(self, delta: int) -> None:
        before = self.s.hp
        after = self._bump("hp", delta, 0, derived.max_hp(self.s, self.c))
        if delta < 0 and before > 0 and after <= 0:
            self.out.followups.append(Followup(FU_DEATH_SAVE))

    def _humanity(self, delta: int) -> None:
        s, c = self.s, self.c
        if s.chrome_free:
            self.out.notes.append("humanity ignored: a clean body keeps full humanity")
            return
        immune = derived.psychosis_immune(s, c)
        lo = derived.humanity_floor(s, c)
        if immune:
            lo = max(lo, 1)
        before = s.humanity
        after = self._bump("humanity", delta, lo, derived.humanity_max(s, c))
        if delta >= 0 or immune:
            return
        if after <= 0 < before:
            self.out.followups.append(Followup(FU_CYBERPSYCHOSIS))
            return
        cy = c.balance.cyber
        if after <= cy.warn_humanity_critical < before:
            self.out.followups.append(Followup(FU_HUMANITY_WARNING, "critical"))
        elif after <= cy.warn_humanity_low < before:
            self.out.followups.append(Followup(FU_HUMANITY_WARNING, "low"))

    def _heat(self, delta: int) -> None:
        s, c = self.s, self.c
        if delta > 0:
            district = c.districts.get(s.district)
            mult = (district.heat_mult if district else 1.0) * self.heat_mult * s.world_heat_mult
            if s.district_control and district is not None and district.city == "nc":  # owner request 25.10: NCPD's grip
                from app.engine import control as ctl_eng

                mult *= ctl_eng.heat_mult(c, s.district_control[1])
            if s.district_law:  # owner request 25.10: the law of a player-held district
                lw = next((x for x in c.balance.turf.laws if x.key == s.district_law), None)
                if lw is not None:
                    mult *= lw.heat_mult
            if mult != 1.0:
                # owner report 24.10: rounding every +1 up made ×1.5 districts (North Oak) a ×2. The fraction now
                # carries over (flag heat_frac), so ×1.5 really is +1, +2, +1, +2 ...
                raw = delta * mult + float(s.flags.get("heat_frac", 0.0) or 0.0)
                delta = int(raw)
                frac = round(raw - delta, 3)
                if frac > 0:
                    s.flags["heat_frac"] = frac
                else:
                    s.flags.pop("heat_frac", None)
                if delta <= 0:
                    return
        lo = c.balance.cyberpsychosis.fugitive_heat if s.flags.get(FLAG_FUGITIVE) else 0
        self._bump("heat", delta, lo, c.balance.character.heat_max)

    def _final_clamps(self) -> None:
        """Caps can drop when an implant leaves (hp_max, LUCK, humanity ceiling): pull values back."""
        s, c = self.s, self.c
        s.hp = clamp(s.hp, 0, derived.max_hp(s, c))
        s.luck_pool = clamp(s.luck_pool, 0, derived.luck_max(s, c))
        s.glitches = clamp(s.glitches, 0, derived.glitch_cap(s, c))
        if not s.chrome_free:
            s.humanity = clamp(s.humanity, derived.humanity_floor(s, c), derived.humanity_max(s, c))

    # -- money ----------------------------------------------------------

    def _new_debt(self, amount: int) -> DebtState:
        d = DebtState(
            creditor=self.c.balance.debt.overdraft_creditor, principal=amount,
            weekly_rate=self.c.balance.debt.weekly_rate, creditor_kind="other",
        )
        self.s.debts.append(d)
        self.out.debts_created.append(d.creditor)
        return d

    def _debt_up(self, amount: int) -> None:
        if amount <= 0:
            return
        if self.s.debts:
            max(self.s.debts, key=lambda d: d.principal).principal += amount
        else:
            self._new_debt(amount)

    def _debt_down(self, amount: int) -> None:
        left = amount
        for d in list(self.s.debts):
            if left <= 0:
                break
            paid = min(d.principal, left)
            d.principal -= paid
            left -= paid
            if d.principal <= 0:
                self.s.debts.remove(d)
        if left:
            self.out.clamped["debt"] = self.out.clamped.get("debt", 0) + left

    def _settle_eddies(self) -> None:
        """Eddies below zero become debt (rule 3)."""
        if self.s.eddies < 0:
            short = -self.s.eddies
            self.s.eddies = 0
            self.out.overflow_to_debt += short
            self._debt_up(short)

    # -- implants ---------------------------------------------------------

    def _return_share(self, item: CyberItem) -> int:
        cy = self.c.balance.cyber
        paid = item.humanity_paid
        if paid <= 0:
            base = self.c.cyberware[item.key].humanity_cost if item.key in self.c.cyberware else 0
            paid = derived.humanity_loss(base, self.s, self.c)
        return half_up(paid * cy.removal_return_pct)

    def remove_cyberware(self, item: CyberItem, refund: bool = True) -> None:
        """Take an implant out, and anything that cannot work without it (the deck needs the port)."""
        s, c = self.s, self.c
        if item not in s.cyberware:
            return
        share = self._return_share(item) if refund else 0
        s.cyberware.remove(item)
        self.out.cyberware_removed.append(item.key)
        if share:
            back = min(share, s.humanity_lost)
            s.humanity_lost -= back
            if not s.chrome_free:
                self._bump(
                    "humanity", back, derived.humanity_floor(s, c), derived.humanity_max(s, c)
                )
        for other in list(s.cyberware):
            cw = c.cyberware.get(other.key)
            if cw is not None and item.key in cw.requires:
                self.remove_cyberware(other, refund)

    def install_cyberware(
        self, key: str, *, humanity_paid: int = 0, complication: bool = False, free: bool = True,
    ) -> bool:
        """Put an implant in. Handles replacement, requirements, the clean-body flag and vows.

        `free` implants (grants) bring their missing requirements with them; paid ones need them
        installed already. The humanity price is charged by the caller of the paid path.
        """
        s, c = self.s, self.c
        cw = c.cyberware.get(key)
        if cw is None:
            self.out.notes.append(f"cyberware {key!r} does not exist, ignored")
            return False
        if s.has_cyberware(key):
            self.out.notes.append(f"cyberware {key!r} is already installed, ignored")
            return False
        for req in cw.requires:
            if not s.has_cyberware(req):
                if not free:
                    self.out.notes.append(f"cyberware {key!r} needs {req!r}, ignored")
                    return False
                self.install_cyberware(req, free=True)
        if cw.exclusive_group:
            for other in list(s.cyberware):
                ocw = c.cyberware.get(other.key)
                if ocw is not None and ocw.exclusive_group == cw.exclusive_group:
                    self.remove_cyberware(other)
        s.cyberware.append(CyberItem(
            key=key, charge=100, complication=complication, installed_week=s.weeks_lived,
            humanity_paid=humanity_paid,
        ))
        self.out.cyberware_added.append(key)
        from app.engine import injury  # owner request 26.10: a new limb in the slot ends the injury there

        injury.on_install(s, c, key)
        s.chrome_free = False  # for good: taking the chrome out later does not bring it back
        if humanity_paid:
            s.humanity_lost += humanity_paid
            self._humanity(-humanity_paid)
        for vkey in list(s.vows):
            vow = c.vows.get(vkey)
            if vow is not None and "cyberware" in vow.forbids:
                self.break_vow(vkey)
        return True

    # -- vows -------------------------------------------------------------

    def break_vow(self, key: str, depth: int = 0) -> None:
        s = self.s
        if key not in s.vows:
            self.out.notes.append(f"vow {key!r} is not held, nothing to break")
            return
        del s.vows[key]  # the streak burns; the vow can be taken again
        self.out.vows_broken.append(key)
        self.out.followups.append(Followup(FU_VOW_BREAK, key))
        vow = self.c.vows.get(key)
        if vow is not None and vow.break_effects and depth < _MAX_DEPTH:
            self.run(vow.break_effects, depth + 1)

    # -- npcs -------------------------------------------------------------

    def _rel_range(self) -> tuple[int, int]:
        r = self.c.balance.relations
        return r.rel_min, r.rel_max

    def _gain(self, n: int) -> int:
        return n + derived.mod(self.s, self.c, "relation_gain") if n > 0 else n

    def rel_npc(self, tag: str, n: int) -> None:
        if tag == "random":
            pool = [x for x in self.s.npcs if x.alive]
        else:
            pool = self.s.npcs_of(tag)
        if not pool:
            self.out.notes.append(f"rel_npc:{tag}: no such npc, ignored")
            return
        npc = choice(self.rng, pool)
        lo, hi = self._rel_range()
        before = npc.relation
        npc.relation = clamp(before + self._gain(n), lo, hi)
        self.out.rel_changes.append((npc.name, npc.relation - before))

    def rel_fixer(self, key: str, n: int) -> None:
        lo, hi = self._rel_range()
        before = self.s.fixer_rel.get(key, self.c.balance.gigs.fixer_rel_start)
        after = clamp(before + self._gain(n), lo, hi)
        self.s.fixer_rel[key] = after
        self.out.fixer_rel[key] = self.out.fixer_rel.get(key, 0) + (after - before)

    def faction_rep(self, key: str, n: int) -> None:
        sit = self.c.balance.exits.situation.faction_rep
        before = self.s.faction_rep.get(key, 0)
        after = clamp(before + n, sit.min, sit.max)
        self.s.faction_rep[key] = after
        self.out.faction_rep[key] = self.out.faction_rep.get(key, 0) + (after - before)

    def betray(self, key: str) -> None:
        self.s.flags[FLAG_BETRAYED_PREFIX + key] = True
        self.out.betrayed.append(key)

    def add_npc(self, template: str, count: int) -> None:
        """A new NPC with a gendered name (M2.7 rules): a unique first name, a surname that agrees
        with the gender (a child takes the character's own surname). The gender goes to `data`."""
        taken = {n.name.split()[0].casefold() for n in self.s.npcs if n.name}
        taken.add(self.s.name.casefold())
        lo, hi = self._rel_range()
        start = self.c.balance.relations.start_rel.get(template, (lo + hi) // 2)
        for _ in range(count):
            first, gender = roll_first_name(self.rng, self.c, None, taken)
            if template == "child":
                surname = surname_for(self.c, self.s.surname, gender)
            else:
                surname = choice(self.rng, self.c.surnames).form(gender)
            data: dict[str, Any] = {"gender": gender, "district": self.s.district}
            if template == "child":
                data["born_week"] = self.s.weeks_lived
            npc = NpcState(
                name=f"{first} {surname}", kind=template, role=None,
                relation=clamp(start, lo, hi), alive=True, data=data,
            )
            self.s.npcs.append(npc)
            self.out.npcs_added.append(npc)

    # -- skills -----------------------------------------------------------

    def skill_xp(self, skill: str, n: int) -> None:
        s, c = self.s, self.c
        cap = c.balance.character.skill_cap
        st = s.skills[skill]
        if n < 0:
            st.xp = max(0, st.xp + n)
            self.out.skill_xp[skill] = self.out.skill_xp.get(skill, 0) + n
            return
        gain = stochastic_round(n * derived.xp_multiplier(s, c, skill), self.rng)
        if st.level >= cap:
            return
        st.xp += gain
        self.out.skill_xp[skill] = self.out.skill_xp.get(skill, 0) + gain
        if gain > 0:  # owner request 24.10: every skill experience is character experience too
            from app.engine import perks

            self.out.char_levels += perks.add_xp(s, c, gain)
        while st.level < cap and st.xp >= derived.skill_xp_needed(st.level, c):
            st.xp -= derived.skill_xp_needed(st.level, c)
            st.level += 1
            self.out.level_ups.append((skill, st.level))
            from app.engine import perks as _perks  # owner request 24.10: a skill level is worth a share of a talent

            self.out.char_levels += _perks.on_skill_level(s, c)
        if st.level >= cap:
            st.xp = 0

    # -- things and delayed outcomes ----------------------------------------

    def grant_item(self, key: str) -> None:
        """A weapon or armor for free: into the inventory, equipped when that slot is empty. A second copy is not kept."""
        s, c = self.s, self.c
        kind = KIND_WEAPON if key in c.weapons else KIND_ARMOR if key in c.armor else None
        if kind is None:
            self.out.notes.append(f"item {key!r} does not exist, ignored")
            return
        if any(i.kind == kind and i.key == key for i in s.inventory):
            self.out.notes.append(f"item {key!r} is already owned, ignored")
            return
        s.inventory.append(InventoryItem(key, kind, equipped=s.equipped_key(kind) is None))
        self.out.items_added.append((kind, key))

    def take_item(self, key: str) -> None:
        """Owner request 26.10: the city director takes a weapon or armor away. Not owned: nothing happens."""
        s = self.s
        for i, it in enumerate(s.inventory):
            if it.key == key:
                s.inventory.pop(i)
                self.out.items_removed.append((it.kind, key))
                return
        self.out.notes.append(f"item {key!r} is not owned, ignored")

    def grant_vehicle(self, key: str) -> None:
        """A ride for free (character flag `vehicle`). The old ride parks in the garage (owner request 24.10); only with
        a full garage is it sold to a dealer at the usual sell price."""
        s, c = self.s, self.c
        v = c.vehicles.get(key)
        if v is None:
            self.out.notes.append(f"vehicle {key!r} does not exist, ignored")
            return
        old = s.flags.get("vehicle")
        if isinstance(old, dict) and old.get("key") == key:
            self.out.notes.append(f"vehicle {key!r} is already owned, ignored")
            return
        if isinstance(old, dict) and old.get("key") in c.vehicles and len(
                [r for r in (s.flags.get("garage") or []) if isinstance(r, dict)]) < c.balance.vehicles.garage_max:
            s.flags["garage"] = [r for r in (s.flags.get("garage") or []) if isinstance(r, dict)] + [dict(old)]
            self.out.notes.append(f"vehicle {old.get('key')!r} parked in the garage")  # owner request 24.10: a fleet
        elif isinstance(old, dict) and old.get("key") in c.vehicles:
            cfg = c.balance.vehicles
            pct = cfg.stolen_sell_pct if old.get("stolen") else cfg.sell_pct
            price = c.vehicles[old["key"]].price
            paid = max(0, price * pct // 100 * (100 - int(old.get("dmg", 0) or 0)) // 100)
            s.eddies += paid
            self.out.vehicle_sold = (str(old["key"]), paid)
        s.flags["vehicle"] = {"key": key, "tune": 0, "dmg": 0, "stolen": False}
        self.out.items_added.append(("vehicle", key))

    def schedule_later(self, entries: list[dict[str, Any]]) -> None:
        """Park delayed outcomes on the character; engine/week.py rolls each when `due` comes."""
        pending = list(self.s.flags.get(FLAG_LATER) or [])
        for e in entries:
            rec = dict(e)
            rec["due"] = self.s.weeks_lived + int(rec["weeks"])
            pending.append(rec)
            self.out.later_added.append(rec)
        self.s.flags[FLAG_LATER] = pending

    # -- the dispatcher -----------------------------------------------------

    def run(self, effects: dict[str, Any], depth: int = 0) -> None:
        s, c = self.s, self.c
        for key, val in effects.items():
            base, param = split_effect_key(key)
            if base == "eddies":
                s.eddies += int(val)  # may go below zero until _settle_eddies
            elif base in ("biz_level", "biz_boost", "biz_slump", "biz_lost", "biz_chain",
                          "biz_stock", "biz_staff", "biz_cond"):  # owner request: a business story
                from app.engine import biz as biz_engine

                bc = c.balance.players.business
                line = biz_engine.apply_effect(s, base, val, bc.max_level, bc.max_owned)  # type: ignore[arg-type]
                if line:
                    self.out.biz_lines.append("🧠 " + line)
            elif base == "vamp_blood":  # owner request 24.10: the night-taxi vampires
                from app.engine import vampire

                vampire.add_blood(s, c, int(val))
            elif base == "crisis_pts":  # owner request 24.10: the city crisis (services/crisis.py takes it from here)
                if int(val):
                    s.flags["crisis_pts"] = int(s.flags.get("crisis_pts", 0) or 0) + int(val)
                    self.out.biz_lines.append(f"🔥 Вклад в кризис города {int(val):+d}")
            elif base in ("hook", "enmity"):  # owner request 24.10: the pressure (services/pressure.py)
                if int(val):
                    fl = "pr_" + base
                    cap = c.balance.pressure.max_level
                    s.flags[fl] = max(0, min(cap, int(s.flags.get(fl, 0) or 0) + int(val)))
                    word = "🪝 Крючок" if base == "hook" else "🗡 Враги"
                    self.out.biz_lines.append(f"{word} {int(val):+d} (теперь {s.flags[fl]})")
            elif base in ("bw_rot", "bw_favor"):  # owner request 24.10: the Blackwall taint and the cult's favor
                from app.engine import blackwall

                if base == "bw_rot":
                    before = blackwall.rot(s)
                    now = blackwall.add_rot(s, c, int(val))
                    if now != before:
                        names = c.balance.blackwall.tier_names
                        t = blackwall.tier(s, c)
                        self.out.biz_lines.append(f"⚫ Порча Заслона {now - before:+d}: {now}/{c.balance.blackwall.rot_max}"
                                                  + (f" · {names[t]}" if t < len(names) else ""))
                else:
                    blackwall.add_favor(s, int(val))
                    if int(val):
                        self.out.biz_lines.append(f"⚫ Милость культа {int(val):+d}")
            elif base == "chrono":  # owner request (Aeon): weeks of time; without the clock, eddies at the exchange
                from app.engine import chrono

                if chrono.has(s):
                    if chrono.add(s, int(val)) <= 0:
                        self.out.followups.append(Followup(FU_DEATH, chrono.DEATH))
                else:
                    s.eddies += int(val) * c.balance.chrono.sell_week if int(val) > 0 else 0
            elif base in ("possess_sync", "possess_trust"):  # owner request 26.10: the AI in the head
                from app.engine import possession

                if base == "possess_sync":
                    possession.shift(s, c, sync=int(val))
                else:
                    possession.shift(s, c, trust=int(val))
            elif base == "eddies_pct":
                pct = int(val)
                if pct < 0:
                    pct = pct * max(0, 100 + derived.mod(s, c, "robbery_loss_pct")) // 100
                s.eddies += max(0, s.eddies) * pct // 100
            elif base == "hp":
                self._hp(int(val))
            elif base == "mood":
                ch = c.balance.character
                self._bump("mood", int(val), ch.mood_min, ch.mood_max)
            elif base == "humanity":
                self._humanity(int(val))
            elif base == "street_cred":
                self._bump("street_cred", int(val), 0, c.balance.reputation.street_cred_max)
            elif base == "status":
                self._bump("status", int(val), 0, c.balance.reputation.status_max)
            elif base == "fame":
                self._bump("fame", int(val), 0, c.balance.reputation.fame_max)
            elif base == "heat":
                self._heat(int(val))
            elif base == "debt":
                if int(val) > 0:
                    self._debt_up(int(val))
                elif int(val) < 0:
                    self._debt_down(-int(val))
            elif base == "luck_pool":
                self._bump("luck_pool", int(val), 0, derived.luck_max(s, c))
            elif base == "glitches":
                self._bump("glitches", int(val), 0, derived.glitch_cap(s, c))
            elif base in ("dirt", "tokens"):
                self.out.player_deltas[base] = self.out.player_deltas.get(base, 0) + int(val)
            elif base == "infection":
                s.flags[FLAG_INFECTION] = True
                self.out.infection = True
            elif base == "remove_random_cyberware":
                count = 1 if val is True else int(val)
                for _ in range(count):
                    if not s.cyberware:
                        self.out.notes.append("remove_random_cyberware: no implants left")
                        break
                    self.remove_cyberware(choice(self.rng, s.cyberware))
            elif base == "cyberware_grant":
                assert param is not None
                self.install_cyberware(param, free=True)
            elif base == "cyberware_lose":  # owner request 26.10: torn off, nothing comes back
                assert param is not None
                item = s.cyber_item(param) if hasattr(s, "cyber_item") else None
                if item is not None:
                    self.remove_cyberware(item, refund=False)
            elif base == "item_grant":
                assert param is not None
                self.grant_item(param)
            elif base == "vehicle_grant":
                assert param is not None
                self.grant_vehicle(param)
            elif base == "item_take":
                assert param is not None
                self.take_item(param)
            elif base == EFFECT_LATER:
                self.schedule_later(val)  # type: ignore[arg-type]
            elif base == "scan_tolerance":
                s.tolerance_known = True
                s.flags.pop(FLAG_TOLERANCE_SHOWN, None)
                self.out.tolerance_revealed = s.cyber_tolerance
            elif base == "debt_grace":
                for d in s.debts:
                    d.missed_weeks = 0
                self.out.debt_grace = bool(s.debts)
            elif base == "employer":
                pass  # after the loop: it names the employer of a career set in the same effects
            elif base == "career_clear":
                old = s.career.key if s.career else None
                s.career = None
                if old:
                    self.out.career_change = (old, None)
            elif base == "career_set":
                assert param is not None
                if param not in c.available("careers", self.year):
                    self.out.notes.append(f"career {param!r} does not exist in {self.year}, ignored")
                else:
                    old = s.career.key if s.career else None
                    s.career = CareerState(param, 1, 0)
                    s.flags.pop(FLAG_EMPLOYER, None)  # the old employer does not come with the new career
                    self.out.career_change = (old, param)
            elif base == "start_combat":
                self.out.followups.append(Followup(FU_START_COMBAT, int(val)))
            elif base == "jail_weeks":
                self.out.followups.append(Followup(FU_JAIL, int(val)))
            elif base == "vanish_weeks":
                self.out.followups.append(Followup(FU_VANISH, int(val)))
            elif base == "death":
                if not self.out.followup(FU_DEATH):
                    self.out.followups.append(Followup(FU_DEATH, param))
            elif base == "skill_xp":
                assert param is not None
                self.skill_xp(param, int(val))
            elif base == "habit":  # owner request 24.10: hooked on a drug by someone else
                assert param is not None
                hb = dict(s.flags.get("habit") or {})
                hb[param] = max(0, int(hb.get(param, 0) or 0) + int(val))
                s.flags["habit"] = hb
                self.out.biz_lines.append(f"💉 Тяга {int(val):+d}")
            elif base == "path_xp":
                from app.engine import paths

                assert param is not None
                before, after, gained = paths.add_xp(s, c, param, int(val))
                self.out.path_xp += gained
                if after > before:
                    self.out.path_rank_up = after
            elif base == "rel_npc":
                assert param is not None
                self.rel_npc(param, int(val))
            elif base == "npc_lost":  # owner request 25.10: a close person gone for good (the shadow of a legend)
                assert param is not None
                pool = [x for x in s.npcs if x.alive] if param == "random" else s.npcs_of(param)
                if pool:
                    gone = choice(self.rng, pool)
                    gone.alive = False
                    self.out.biz_lines.append(f"✝️ {gone.name} больше нет.")
            elif base == "possess":  # owner request 26.10: an AI moves into the head
                from app.engine import possession

                assert param is not None
                if possession.rec(s) is None:
                    self.out.biz_lines.append("🧠 " + possession.start(s, self.c, param))
            elif base == "possess_end":
                from app.engine import possession

                assert param is not None
                line = possession.end(s, self.c, param)
                if line:
                    self.out.biz_lines.append(line)
            elif base == "npc_leave":  # owner request 25.10: a person walks out of the character's life (alive)
                assert param is not None
                pool = s.npcs_of(param)
                if pool:
                    gone = choice(self.rng, pool)
                    s.npcs.remove(gone)
                    if param == "partner":
                        s.flags.pop("partner_moved_in", None)
                        s.flags.pop("married", None)
                    self.out.biz_lines.append(f"💔 {gone.name} уходит из твоей жизни.")
            elif base == "rel_fixer":
                assert param is not None
                self.rel_fixer(param, int(val))
            elif base == "faction_rep":
                assert param is not None
                self.faction_rep(param, int(val))
            elif base == "betray":
                assert param is not None
                self.betray(param)
            elif base == "add_npc":
                assert param is not None
                self.add_npc(param, 1 if val is True else int(val))
            elif base == "flag_set":
                assert param is not None
                s.flags[param] = True
                if param.startswith("s_"):  # the endless saga: the newest mark leads the next step
                    s.flags["saga_last_mark"] = param
                self.out.flags_set.append(param)
            elif base == "queue_event":
                assert param is not None
                if param in c.events:
                    queue = list(s.flags.get(FLAG_STORY_QUEUE) or [])
                    if param not in queue:
                        queue.append(param)
                    s.flags[FLAG_STORY_QUEUE] = queue
                else:
                    self.out.notes.append(f"queue_event {param!r}: no such event, ignored")
            elif base == "flag_clear":
                assert param is not None
                s.flags.pop(param, None)
                self.out.flags_cleared.append(param)
            elif base == "buff":
                assert param is not None
                card = c.tarot.get(param)
                if card is None or not card.mods:
                    self.out.notes.append(f"buff {param!r}: no such card or it has no mods, ignored")
                else:
                    source = f"tarot:{param}"
                    s.buffs = [b for b in s.buffs if b.source != source]  # the same card does not stack
                    buff = Buff(source=source, mods=dict(card.mods))
                    s.buffs.append(buff)
                    self.out.buffs_added.append(buff)
            elif base == "vow_break":
                if param is not None:
                    self.break_vow(param, depth)
                else:
                    for vkey in list(s.vows):
                        self.break_vow(vkey, depth)
            else:  # validate_effects rejects unknown keys before we get here
                raise ValueError(f"unhandled effect key {key!r}")
        for key in effects:
            base, param = split_effect_key(key)
            if base == "employer" and param:
                if param not in c.corps:
                    self.out.notes.append(f"employer {param!r}: no such corp, ignored")
                elif s.career is None:
                    self.out.notes.append(f"employer {param!r}: no career, ignored")
                else:
                    s.flags[FLAG_EMPLOYER] = param
                    self.out.employer = param

    def finish(self, before: dict[str, int], debt_before: int) -> AppliedEffects:
        self._settle_eddies()
        self._final_clamps()
        s = self.s
        after = {name: getattr(s, name) for name in _TRACKED}
        self.out.deltas = {name: after[name] - before[name] for name in _TRACKED}
        self.out.deltas["debt"] = s.debt_total - debt_before
        return self.out


def _snapshot(state: CharacterState) -> tuple[dict[str, int], int]:
    return {name: getattr(state, name) for name in _TRACKED}, state.debt_total


def apply_effects(
    state: CharacterState,
    effects: dict[str, Any],
    content: Content,
    rng: random.Random,
    year: int,
    *,
    heat_mult: float = 1.0,
) -> AppliedEffects:
    """Apply an effects dict to the character. Raises ValueError on an unknown or malformed key.

    `year` is the world year. `heat_mult` is the world's heat multiplier (the world effect);
    the district's own multiplier is applied here too, to every increase of heat.
    """
    effects = validate_effects(dict(effects))
    ap = _Applier(state, content, rng, year, heat_mult)
    before, debt_before = _snapshot(state)
    ap.out.requested = dict(effects)
    ap.run(effects)
    return ap.finish(before, debt_before)


def apply_install_cyberware(
    state: CharacterState,
    content: Content,
    key: str,
    rng: random.Random,
    year: int,
    *,
    humanity_paid: int = 0,
    complication: bool = False,
) -> AppliedEffects:
    """A paid installation (M6): the implant goes in and `humanity_paid` (already multiplied by
    tolerance, era and complication, see derived.humanity_loss) is charged. Money is the caller's."""
    ap = _Applier(state, content, rng, year, 1.0)
    before, debt_before = _snapshot(state)
    ap.install_cyberware(key, humanity_paid=humanity_paid, complication=complication, free=False)
    return ap.finish(before, debt_before)


def apply_npc_relation(state: CharacterState, content: Content, npc: NpcState, delta: int) -> int:
    """Change the relation of ONE chosen NPC (the player picked them), clamped, with the
    `relation_gain` modifier on gains. Returns the real change. `rel_npc:<tag>` in effects picks at random."""
    ap = _Applier(state, content, random.Random(0), 0, 1.0)
    lo, hi = ap._rel_range()
    before = npc.relation
    npc.relation = clamp(before + ap._gain(delta), lo, hi)
    return npc.relation - before


def apply_remove_cyberware(
    state: CharacterState, content: Content, key: str, rng: random.Random, year: int, *, refund: bool = True,
) -> AppliedEffects:
    """A removal at a ripper (M6): half of the humanity price comes back (balance.cyber.removal_return_pct).
    `refund=False`: an implant that burns out (the Second Heart) gives nothing back."""
    ap = _Applier(state, content, rng, year, 1.0)
    before, debt_before = _snapshot(state)
    item = state.cyber_item(key)
    if item is None:
        ap.out.notes.append(f"cyberware {key!r} is not installed")
    else:
        ap.remove_cyberware(item, refund)
    return ap.finish(before, debt_before)


def merge_applied(items: "list[AppliedEffects]") -> AppliedEffects:
    """Several results as one (owner request: many sessions of one deed at once): numbers add up, lists join."""
    out = AppliedEffects()
    for a in items:
        for k, v in a.requested.items():
            if isinstance(v, bool) or not isinstance(v, int):
                out.requested[k] = v
            else:
                out.requested[k] = int(out.requested.get(k, 0) or 0) + v
        for src, dst in ((a.deltas, out.deltas), (a.clamped, out.clamped), (a.player_deltas, out.player_deltas),
                         (a.skill_xp, out.skill_xp), (a.fixer_rel, out.fixer_rel), (a.faction_rep, out.faction_rep)):
            for k, v in src.items():
                dst[k] = dst.get(k, 0) + v
        out.overflow_to_debt += a.overflow_to_debt
        out.path_xp += a.path_xp
        out.path_rank_up = a.path_rank_up or out.path_rank_up
        for name in ("level_ups", "cyberware_added", "cyberware_removed", "debts_created", "flags_set", "flags_cleared",
                     "vows_broken", "npcs_added", "rel_changes", "betrayed", "buffs_added", "items_added", "items_removed",
                     "later_added",
                     "followups", "notes", "biz_lines"):
            getattr(out, name).extend(getattr(a, name))
        out.career_change = a.career_change or out.career_change
        out.employer = a.employer or out.employer
        out.vehicle_sold = a.vehicle_sold or out.vehicle_sold
        out.tolerance_revealed = a.tolerance_revealed or out.tolerance_revealed
        out.debt_grace = out.debt_grace or a.debt_grace
        out.infection = out.infection or a.infection
    top: dict[str, int] = {}
    for skill, lvl in out.level_ups:  # one line per skill: the level it ended at
        top[skill] = max(lvl, top.get(skill, 0))
    out.level_ups = list(top.items())
    return out
