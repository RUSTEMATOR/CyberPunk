"""Life beyond Night City (owner request 23.10: the countries of the world). Pure functions.

flag `abroad` = {country, since}: the character lives in that country (content/countries.yaml); its mods work and the
weekly cost is paid every press. A boolean flag `in_<country>` mirrors it for the event conditions.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG = "abroad"


def where(state: CharacterState) -> str | None:
    r = state.flags.get(FLAG)
    return str(r.get("country")) if isinstance(r, dict) and r.get("country") else None


def country(state: CharacterState, content: Content):  # noqa: ANN201
    key = where(state)
    return content.countries.get(key) if key else None


def settle(state: CharacterState, content: Content, key: str | None) -> None:
    """Move the character: to a country, or home to Night City with None.

    Owner request 25.10: a country with a city (content/world_*.yaml) moves the character into its first district; the
    home district and the home in Night City are remembered (the home waits without rent) and given back on return."""
    for k in content.countries:
        state.flags.pop(f"in_{k}", None)
    v = state.flags.pop("visit", None)  # owner request 25.10: a trip ends at the airport
    if isinstance(v, dict) and v.get("home"):
        state.district = str(v["home"])
    prev = state.flags.get(FLAG) if isinstance(state.flags.get(FLAG), dict) else None
    home_d = (prev or {}).get("home_district") or state.district
    home_h = (prev or {}).get("home_housing") if prev and "home_housing" in prev else state.housing
    if key is None:
        state.flags.pop(FLAG, None)
        if _city(content, state.district) != "nc":
            state.district = home_d if _city(content, home_d) == "nc" else _nc_default(content)
            h = content.housing.get(home_h or "")
            state.housing = home_h if h is not None and _city(content, h.district) == "nc" else ""
        return
    c = content.countries[key]
    rec = {"country": key, "since": state.weeks_lived}
    if c.districts:
        rec.update(home_district=home_d if _city(content, home_d) == "nc" else _nc_default(content), home_housing=home_h)
        state.district = c.districts[0]
        h = content.housing.get(state.housing or "")
        if h is None or h.district not in c.districts:
            state.housing = ""  # a hotel until a home of the city is rented
    state.flags[FLAG] = rec
    state.flags[f"in_{key}"] = True


def _city(content: Content, district: str | None) -> str:
    d = content.districts.get(district or "")
    return d.city if d is not None else "nc"


def _nc_default(content: Content) -> str:
    return next((k for k, d in content.districts.items() if d.city == "nc"), "watson")


def has_local_home(state: CharacterState, content: Content) -> bool:
    c = country(state, content)
    h = content.housing.get(state.housing or "")
    return c is not None and h is not None and h.district in c.districts


def in_city(state: CharacterState, content: Content) -> bool:
    """Owner request 25.10: the character abroad lives in a district of that country's city (its systems work)."""
    c = country(state, content)
    return c is not None and _city(content, state.district) == c.key


def needs_move_in(state: CharacterState, content: Content) -> bool:
    """A character who flew before the cities existed still sits in a Night City district."""
    c = country(state, content)
    return c is not None and bool(c.districts) and _city(content, state.district) != c.key


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    c = country(state, content)
    if c is not None and c.mods:
        yield "abroad", c.mods
