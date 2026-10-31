"""The wanted level as a chase (owner request 26.10, docs/PLAN_WANTED.md): pure functions, numbers in
`balance.wanted`.

The chase lives in the flag `chase` = {"wave": waves met so far, "next": unix time of the next wave, "acts": AP spent
since the last wave, "pause": unix time until which the police lost the trail}. It starts by itself once the heat
reaches `chase_from` and ends when the heat drops below it.
"""

from __future__ import annotations

import random

from app.content.loader import Content
from app.engine.dice import clamp
from app.engine.state import FLAG_FUGITIVE, FLAG_JAIL_WEEKS, CharacterState

FLAG = "chase"
FLAG_CHANGE_DAY = "wanted_change_day"
FLAG_FIXER_DAY = "wanted_fixer_day"
FLAG_FACE_AT = "wanted_face_at"


def cfg(content: Content):  # noqa: ANN201 - a WantedBalance
    return content.balance.wanted


def _hk(table: dict[int, int], heat: int) -> int:
    keys = sorted(table)
    if not keys:
        return 0
    return int(table[max([k for k in keys if k <= heat] or [keys[0]])])


def in_city(st: CharacterState, content: Content) -> bool:
    d = content.districts.get(st.district)
    return d is not None and d.city == "nc" and not st.flags.get("abroad")


def can_chase(st: CharacterState, content: Content) -> bool:
    """The police can come for this character now (not the reasons of the moment: a fight, a dungeon)."""
    c = cfg(content)
    return (c.enabled and st.heat >= c.chase_from and in_city(st, content)
            and not st.flags.get(FLAG_FUGITIVE) and int(st.flags.get(FLAG_JAIL_WEEKS, 0) or 0) <= 0)


def interval_s(content: Content, heat: int) -> int:
    return max(60, _hk(cfg(content).interval_min, heat) * 60)


def acts_needed(content: Content, heat: int) -> int:
    return max(1, _hk(cfg(content).acts_per_wave, heat))


def ensure(st: CharacterState, content: Content, now: float) -> dict | None:
    """The chase record: started when the heat is high enough, dropped when it is not."""
    if not can_chase(st, content):
        if FLAG in st.flags and st.heat < cfg(content).chase_from:
            st.flags.pop(FLAG, None)
        return None
    rec = st.flags.get(FLAG)
    if not isinstance(rec, dict):
        rec = {"wave": 0, "acts": 0, "pause": 0.0,
               "next": now + interval_s(content, st.heat) * cfg(content).first_wave_part}
        st.flags[FLAG] = rec
    return rec


def due(st: CharacterState, content: Content, now: float) -> bool:
    rec = st.flags.get(FLAG)
    if not isinstance(rec, dict) or not can_chase(st, content):
        return False
    if now < float(rec.get("pause", 0) or 0):
        return False
    return now >= float(rec.get("next", 0) or 0) or int(rec.get("acts", 0) or 0) >= acts_needed(content, st.heat)


def wave(content: Content, heat: int, n: int) -> tuple[str, int, int]:
    """(faction, danger, extra fighters) of the wave number `n` (0 is the first) at this heat."""
    c = cfg(content)
    maxtac = heat >= c.maxtac_at
    base = _hk(c.danger, heat)
    danger = base + n // max(1, c.danger_every)
    danger = int(clamp(danger, 1, c.maxtac_danger_max if maxtac else c.ncpd_danger_max))
    extra = int(clamp(_hk(c.extra_base, heat) + c.extra_per_wave * n, 0, c.extra_cap))
    return ("maxtac" if maxtac else "ncpd"), danger, extra


def next_in(st: CharacterState, content: Content, now: float) -> tuple[int, int] | None:
    """(seconds until the next wave, AP actions left before it) or None without a chase."""
    rec = st.flags.get(FLAG)
    if not isinstance(rec, dict) or not can_chase(st, content):
        return None
    pause = float(rec.get("pause", 0) or 0)
    t = max(float(rec.get("next", 0) or 0), pause) - now
    acts = max(0, acts_needed(content, st.heat) - int(rec.get("acts", 0) or 0))
    return max(0, int(t)), acts


def witness_chance(st: CharacterState, content: Content, stealth: int) -> float:
    c = cfg(content)
    d = content.districts.get(st.district)
    mult = d.heat_mult if d is not None else 1.0
    return float(clamp(c.witness_base * mult - c.witness_per_stealth * stealth, c.witness_min, c.witness_max))


def witnessed(st: CharacterState, content: Content, rng: random.Random, stealth: int, left_alive: int) -> bool:
    """Somebody saw it: an enemy left alive always did, bystanders by chance."""
    return left_alive > 0 or rng.random() < witness_chance(st, content, stealth)


def hide_dv(content: Content, heat: int) -> int:
    c = cfg(content)
    return c.hide_dv_base + c.hide_dv_per_heat * heat


def change_price(content: Content, heat: int) -> int:
    return cfg(content).change_price_per_heat * max(1, heat)


def fixer_price(content: Content, heat: int) -> int:
    return cfg(content).fixer_price_per_heat2 * max(1, heat) ** 2


def face_price(content: Content, heat: int) -> int:
    return cfg(content).face_price_per_heat * max(1, heat)
