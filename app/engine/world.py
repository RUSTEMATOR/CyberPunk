"""The world's state and its effects (PLAN 10.5, 10.6, DESIGN.md 9 and 9a). Pure functions: randomness comes from
the passed `rng`, there is no IO.

`WorldFx` is the sum of every active world effect: price multipliers, event weights, the heat and pay multipliers,
district danger and faction power offsets, unlocked and banned items, opened and closed districts. It is always
the fold of the active `world_history` effects, so cancelling a generated event is "fold again without it".
Things that are not a sum (gang wars, the Eddy-crypto shock, an era marker) are returned by `apply_world_effects`
for the service to act on.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from app.content.loader import Content
from app.content.schemas import Era, TimelineEvent, parse_world_effect_key
from app.engine.dice import clamp

# World effect values of a canon event or of a generated one are checked against `balance.world_effects`.


@dataclass
class WorldFx:
    price_mult: dict[str, float] = field(default_factory=dict)  # target (combat ... defense, weapons, housing) -> x
    event_weight: dict[str, float] = field(default_factory=dict)  # event category -> x
    district_danger: dict[str, int] = field(default_factory=dict)  # district -> offset
    faction_power: dict[str, int] = field(default_factory=dict)  # gang or corp -> offset
    heat_mult: float = 1.0
    gig_pay_mult: float = 1.0
    unlocked: list[str] = field(default_factory=list)
    banned: list[str] = field(default_factory=list)
    districts_open: list[str] = field(default_factory=list)
    districts_closed: list[str] = field(default_factory=list)
    wars: dict[str, int] = field(default_factory=dict)  # gang -> weeks of the war still left at the date of the fold

    def to_dict(self) -> dict[str, Any]:
        return {
            "price_mult": dict(self.price_mult), "event_weight": dict(self.event_weight),
            "district_danger": dict(self.district_danger), "faction_power": dict(self.faction_power),
            "heat_mult": self.heat_mult, "gig_pay_mult": self.gig_pay_mult,
            "unlocked": list(self.unlocked), "banned": list(self.banned),
            "districts_open": list(self.districts_open), "districts_closed": list(self.districts_closed),
            "wars": dict(self.wars),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any] | None) -> "WorldFx":
        d = d or {}
        return cls(
            price_mult={str(k): float(v) for k, v in (d.get("price_mult") or {}).items()},
            event_weight={str(k): float(v) for k, v in (d.get("event_weight") or {}).items()},
            district_danger={str(k): int(v) for k, v in (d.get("district_danger") or {}).items()},
            faction_power={str(k): int(v) for k, v in (d.get("faction_power") or {}).items()},
            heat_mult=float(d.get("heat_mult", 1.0) or 1.0), gig_pay_mult=float(d.get("gig_pay_mult", 1.0) or 1.0),
            unlocked=[str(x) for x in d.get("unlocked") or []], banned=[str(x) for x in d.get("banned") or []],
            districts_open=[str(x) for x in d.get("districts_open") or []],
            districts_closed=[str(x) for x in d.get("districts_closed") or []],
            wars={str(k): int(v) for k, v in (d.get("wars") or {}).items()},
        )

    def price(self, target: str) -> float:
        value = self.price_mult.get(target, 1.0)
        value = value if value > 0 else 1.0
        if target in ("combat", "noncombat", "booster", "netrun", "defense"):  # owner request 26.10: a city out of
            from app.engine import equilib  # balance has its rippers mobilised: chrome costs more

            value *= equilib.chrome_mult()
        return value


@dataclass
class WorldEffectsApplied:
    """What `apply_world_effects` did besides changing the `WorldFx`."""

    applied: dict[str, Any] = field(default_factory=dict)  # the effects as applied (corridor-clamped values)
    wars: dict[str, int] = field(default_factory=dict)  # gang -> weeks the war lasts
    coin_shock: float | None = None  # multiplier of the Eddy-crypto rate, applied once
    era: str | None = None  # era marker (the era itself follows the year table)
    rejected: list[str] = field(default_factory=list)  # keys left out: unknown catalog key, bad value


def _rng_clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _known(content: Content, key: str) -> bool:
    return any(key in cat for cat in (content.cyberware, content.quickhacks, content.weapons, content.armor))


def apply_world_effects(fx: WorldFx, effects: Mapping[str, Any], content: Content) -> WorldEffectsApplied:
    """Add `effects` (the timeline.yaml menu) to `fx`, every value inside the corridors of `balance.world_effects`.

    A multiplier is clamped on its own and again as a total, so a chain of events cannot push a price outside the
    corridor. A key that does not parse or names something that does not exist is rejected, never raised: a bad
    generated event must not break the tick.
    """
    cor = content.balance.world_effects
    out = WorldEffectsApplied()
    for key, raw in effects.items():
        try:
            base, param = parse_world_effect_key(key)
        except ValueError:
            out.rejected.append(key)
            continue
        if base == "price_mult" and param is not None:
            v = _rng_clamp(float(raw), cor.price_mult.min, cor.price_mult.max)
            fx.price_mult[param] = round(_rng_clamp(fx.price_mult.get(param, 1.0) * v, cor.price_mult.min, cor.price_mult.max), 4)
            out.applied[key] = v
        elif base == "event_weight" and param is not None:
            v = _rng_clamp(float(raw), cor.event_weight.min, cor.event_weight.max)
            fx.event_weight[param] = round(_rng_clamp(fx.event_weight.get(param, 1.0) * v, cor.event_weight.min, cor.event_weight.max), 4)
            out.applied[key] = v
        elif base == "heat_mult":
            v = _rng_clamp(float(raw), cor.heat_mult.min, cor.heat_mult.max)
            fx.heat_mult = round(_rng_clamp(fx.heat_mult * v, cor.heat_mult.min, cor.heat_mult.max), 4)
            out.applied[key] = v
        elif base == "gig_pay_mult":
            v = _rng_clamp(float(raw), cor.gig_pay_mult.min, cor.gig_pay_mult.max)
            fx.gig_pay_mult = round(_rng_clamp(fx.gig_pay_mult * v, cor.gig_pay_mult.min, cor.gig_pay_mult.max), 4)
            out.applied[key] = v
        elif base == "district_danger" and param is not None:
            if param not in content.districts:
                out.rejected.append(key)
                continue
            v = int(_rng_clamp(int(raw), cor.district_danger.min, cor.district_danger.max))
            fx.district_danger[param] = int(_rng_clamp(fx.district_danger.get(param, 0) + v, -4, 4))
            out.applied[key] = v
        elif base == "faction_power" and param is not None:
            if param not in content.gangs and param not in content.corps:
                out.rejected.append(key)
                continue
            v = int(_rng_clamp(int(raw), cor.faction_power.min, cor.faction_power.max))
            fx.faction_power[param] = int(_rng_clamp(fx.faction_power.get(param, 0) + v, -6, 6))
            out.applied[key] = v
        elif base == "gang_war" and param is not None:
            if param not in content.gangs:
                out.rejected.append(key)
                continue
            v = int(_rng_clamp(int(raw), cor.gang_war_weeks.min, cor.gang_war_weeks.max))
            out.wars[param] = max(out.wars.get(param, 0), v)
            out.applied[key] = v
        elif base in ("unlock", "ban") and param is not None:
            if not _known(content, param):
                out.rejected.append(key)
                continue
            if base == "unlock":
                if param in fx.banned:
                    fx.banned.remove(param)
                if param not in fx.unlocked:
                    fx.unlocked.append(param)
            else:
                if param in fx.unlocked:
                    fx.unlocked.remove(param)
                if param not in fx.banned:
                    fx.banned.append(param)
            out.applied[key] = True
        elif base in ("district_open", "district_close") and param is not None:
            if param not in content.districts:
                out.rejected.append(key)
                continue
            opening = base == "district_open"
            add, drop = (fx.districts_open, fx.districts_closed) if opening else (fx.districts_closed, fx.districts_open)
            if param in drop:
                drop.remove(param)
            if param not in add:
                add.append(param)
            out.applied[key] = True
        elif base == "coin_shock":
            v = _rng_clamp(float(raw), cor.coin_shock.min, cor.coin_shock.max)
            out.coin_shock = (out.coin_shock or 1.0) * v
            out.applied[key] = v
        elif base == "era":
            if param in content.eras:
                out.era = param
                out.applied[key] = True
            else:
                out.rejected.append(key)
        else:
            out.rejected.append(key)
    return out


def fold_effects(effect_sets: Iterable[Mapping[str, Any]], content: Content) -> WorldFx:
    """The `WorldFx` of a list of active effect sets, in order (the world history, vetoed rows left out)."""
    fx = WorldFx()
    for effects in effect_sets:
        apply_world_effects(fx, effects, content)
    return fx


def fold_dated(beats: Iterable[tuple[int, Mapping[str, Any]]], content: Content, at_week: int) -> WorldFx:
    """The effects a character feels at his own date `at_week` (DESIGN.md 9a): every beat dated not later than that, in
    date order. A canon war lasts its weeks from the date of the beat, so it is on only while the character's date is
    inside it (`fx.wars` is the weeks left). Random gang wars of the real-time tick are not part of this."""
    fx = WorldFx()
    for week, effects in sorted(beats, key=lambda b: b[0]):
        if week > at_week:
            break
        res = apply_world_effects(fx, effects, content)
        for gang, weeks in res.wars.items():
            left = week + weeks - at_week
            if left > 0:
                fx.wars[gang] = max(fx.wars.get(gang, 0), left)
    return fx


# -- calendar -----------------------------------------------------------------------------------------------------------


def year_week(content: Content, world_week: int) -> tuple[int, int]:
    """(world year, week of the year 1..52) of a world week."""
    per = content.balance.time.weeks_per_year
    w = max(0, world_week)
    return content.balance.world.start_year + w // per, w % per + 1


def week_of(content: Content, year: int, week: int) -> int:
    """The world week index at which year/week of the calendar starts (the inverse of `year_week`)."""
    per = content.balance.time.weeks_per_year
    return (year - content.balance.world.start_year) * per + (week - 1)


def event_week(content: Content, event: TimelineEvent) -> int:
    return week_of(content, event.year, event.week)


def eras_entered(content: Content, week0: int, week1: int) -> list[Era]:
    """The eras the world walked into between two world weeks (in order, the first one is the era at week0 and is
    not included). Empty when the era did not change."""
    y0, _ = year_week(content, week0)
    y1, _ = year_week(content, week1)
    era0 = content.era_for_year(y0)
    out: list[Era] = []
    last = era0
    for year in range(y0 + 1, y1 + 1):
        era = content.era_for_year(year)
        if era.key != last.key:
            out.append(era)
            last = era
    return out


# -- districts, heat, gang wars --------------------------------------------------------------------------------------------


def decay_district_heat(heat: Mapping[str, int], content: Content, days: int) -> dict[str, int]:
    """Every district cools by `district_heat_decay_per_day` per day; cold districts leave the dict."""
    dec = content.balance.world.district_heat_decay_per_day * max(0, days)
    out: dict[str, int] = {}
    for key, value in heat.items():
        left = clamp(int(value) - dec, 0, content.balance.world.district_heat_max)
        if left > 0:
            out[key] = left
    return out


def tick_gang_wars(wars: Mapping[str, int], days: int) -> tuple[dict[str, int], list[str]]:
    """Wars run down by the days that passed. Returns the wars still on and the gangs whose war ended."""
    left: dict[str, int] = {}
    ended: list[str] = []
    for gang, n in wars.items():
        n2 = int(n) - max(0, days)
        if n2 > 0:
            left[gang] = n2
        else:
            ended.append(gang)
    return left, ended


def roll_gang_war(
    rng: random.Random, content: Content, year: int, wars: Mapping[str, int], closed: Iterable[str] = (),
) -> tuple[str, str, int] | None:
    """A new gang war with the daily chance: a random district, one of its gangs that exists in `year` and is not
    at war yet. Returns (district, gang, days) or None."""
    w = content.balance.world
    if rng.random() >= w.gang_war_daily_chance:
        return None
    shut = set(closed)
    districts = [k for k, v in content.available("districts", year).items() if k not in shut and v.city == "nc"]
    if not districts:
        return None
    district = rng.choice(sorted(districts))
    gangs = content.available("gangs", year)
    pool = [g for g in content.districts[district].gangs if g in gangs and g not in wars]
    if not pool:
        return None
    return district, rng.choice(sorted(pool)), rng.randint(w.gang_war_min_days, w.gang_war_max_days)


def effective_danger(content: Content, fx: WorldFx, wars: Mapping[str, int], district: str) -> int:
    """District danger now: the base, the world offsets and +`gang_war_danger_bonus` while one of its gangs is at war."""
    d = content.districts[district]
    value = d.danger + fx.district_danger.get(district, 0)
    if any(g in wars for g in d.gangs):
        value += content.balance.world.gang_war_danger_bonus
    return clamp(value, 1, content.balance.world.danger_max)


def city_of(content: Content, district: str) -> str:
    """Owner request 25.10: the city of a district (nc or a country key)."""
    d = content.districts.get(district)
    return d.city if d is not None else "nc"


def districts_now(content: Content, year: int, fx: WorldFx, city: str | None = "nc") -> dict[str, Any]:
    """Districts that exist now: those of the year table, minus the closed, plus the opened by the world.
    Owner request 25.10: only the districts of `city` (Night City by default; None = every city)."""
    out = {k: v for k, v in content.available("districts", year).items() if city is None or v.city == city}
    for key in fx.districts_open:
        if key in content.districts:
            out.setdefault(key, content.districts[key])
    for key in fx.districts_closed:
        out.pop(key, None)
    return out


def available_with_fx(content: Content, kind: str, year: int, fx: WorldFx) -> dict[str, Any]:
    """`content.available(kind, year)` with the world's bans taken off the shelf and its unlocks put on it.

    An unlocked item ignores `available_from` only; the end of its window still removes it."""
    recs = dict(content.available(kind, year))
    catalog: Mapping[str, Any] = getattr(content, kind)
    for key in fx.unlocked:
        rec = catalog.get(key)
        if rec is not None and key not in recs:
            until = getattr(rec, "available_until", None)
            if until is None or year <= until:
                recs[key] = rec
    for key in fx.banned:
        recs.pop(key, None)
    return recs
