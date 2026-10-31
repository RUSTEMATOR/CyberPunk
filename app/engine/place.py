"""Where a character physically is (owner request 30.10, docs/PLAN_WORLD2.md): Night City, a country's city, or one of the
crossover cities. Players in different places cannot touch each other: no attacks, dates, duels, joint jobs, hired
guards at the side or hunts. Talk, money, notes, the Net and the crew go over any border. Pure functions.

The place is the city of the character's district (`district.city`, "nc" for Night City), so a flight that moves the
character into a city's district (engine/abroad.py) moves the place too."""

from __future__ import annotations

from app.content.loader import Content
from app.engine.state import CharacterState

NC = "nc"


def of_district(content: Content, district: str) -> str:
    d = content.districts.get(district)
    return str(getattr(d, "city", None) or NC) if d is not None else NC


def place_of(state: CharacterState, content: Content) -> str:
    return of_district(content, state.district)


def same_place(a: CharacterState, b: CharacterState, content: Content) -> bool:
    return place_of(a, content) == place_of(b, content)


def name_of(content: Content, place: str) -> str:
    """The city's name for the player: «Найт-Сити», «Токио»."""
    if place == NC:
        return "Найт-Сити"
    c = content.countries.get(place)  # a city of the world is keyed by its country (district.city)
    return c.city if c is not None else place


def name_in(content: Content, place: str) -> str:
    """«в Найт-Сити», «в городе Токио» (a safe form for any city name)."""
    return "в Найт-Сити" if place == NC else f"в городе {name_of(content, place)}"


def away_line(content: Content, who: str, place: str) -> str:
    """The refusal when the other one is in another place."""
    return f"{who} сейчас {name_in(content, place)}. Это делается только на месте: сначала переезжай туда же («Мир»)."
