"""The character editor (owner request 23.10, BitLife-style): roots, birth country, age, background, looks.

Roots and looks are cosmetic: they pick names and fill the profile. The background (origin) is the real one of the
game: changing it recomputes what the origin gives (skills, district, street cred, the enemy gang, the starting vow);
the stats stay as rolled, so it is no free reroll. Everything is stored in the draft's flags.
"""

from __future__ import annotations

import random

from app.content.loader import Content
from app.engine.character_gen import FLAG_GENDER, CharacterDraft, _roll_skills
from app.engine.state import CharacterState

LOOKS: dict[str, tuple[str, ...]] = {
    "build": ("худощавое", "обычное", "жилистое", "крепкое", "массивное", "полное"),
    "hair": ("бритая голова", "короткий ёжик", "длинные чёрные", "рыжие кудри", "неоновый ирокез",
             "седые до плеч", "выбритые виски", "косички", "белые как снег"),
    "mark": ("без особых примет", "шрам через бровь", "татуировка на шее", "веснушки", "золотой зуб",
             "разные глаза", "ожог на руке", "пирсинг в губе", "родинка над губой"),
}
LOOK_TITLES = {"build": "Телосложение", "hair": "Волосы", "mark": "Примета"}
HOME = "night_city"


def heritage_of(state: CharacterState) -> str | None:
    v = state.flags.get("heritage")
    return str(v) if v else None


def look_of(state: CharacterState) -> dict[str, str]:
    v = state.flags.get("look")
    return {k: str(x) for k, x in v.items()} if isinstance(v, dict) else {}


def set_heritage(rng: random.Random, content: Content, draft: CharacterDraft, key: str) -> None:
    h = content.heritages.get(key)
    if h is None:
        return
    st = draft.state
    st.flags["heritage"] = key
    gender = str(st.flags.get(FLAG_GENDER) or "m")
    st.name = rng.choice(h.male if gender == "m" else h.female)
    st.surname = rng.choice(h.surnames).form(gender)


def heritage_name(rng: random.Random, content: Content, draft: CharacterDraft) -> bool:
    """A new first name from the draft's roots; False when the draft has no roots."""
    h = content.heritages.get(heritage_of(draft.state) or "")
    if h is None:
        return False
    st = draft.state
    pool = h.male if str(st.flags.get(FLAG_GENDER) or "m") == "m" else h.female
    others = [n for n in pool if n != st.name] or pool
    st.name = rng.choice(others)
    return True


def heritage_surname(rng: random.Random, content: Content, draft: CharacterDraft) -> bool:
    h = content.heritages.get(heritage_of(draft.state) or "")
    if h is None:
        return False
    st = draft.state
    gender = str(st.flags.get(FLAG_GENDER) or "m")
    st.surname = rng.choice(h.surnames).form(gender)
    return True


def set_birth(draft: CharacterDraft, content: Content, key: str) -> None:
    if key == HOME:
        draft.state.flags.pop("birth_country", None)
    elif key in content.countries:
        draft.state.flags["birth_country"] = key


def set_age(draft: CharacterDraft, content: Content, age: int) -> None:
    ch = content.balance.character
    st = draft.state
    year = st.birth_year + st.age_years
    st.age_years = max(ch.age_min, min(ch.age_max, age))
    st.birth_year = year - st.age_years


def cycle_look(draft: CharacterDraft, part: str) -> None:
    opts = LOOKS.get(part)
    if not opts:
        return
    look = look_of(draft.state)
    cur = look.get(part)
    i = (opts.index(cur) + 1) % len(opts) if cur in opts else 0
    look[part] = opts[i]
    draft.state.flags["look"] = look


def random_look(rng: random.Random, draft: CharacterDraft) -> None:
    draft.state.flags["look"] = {k: rng.choice(v) for k, v in LOOKS.items()}


def origins_for(content: Content, draft: CharacterDraft, year: int) -> list[str]:
    """Backgrounds the preset allows: a chrome-free preset keeps its own, a chromed one has no chrome-free origins."""
    preset = content.presets[draft.preset_key]
    available = content.available("origins", year)
    if preset.chrome_free_start and preset.origin_hint in available:
        return [preset.origin_hint]
    chromed = bool(preset.gear.cyberware)
    return [k for k, o in available.items() if not (o.chrome_free_start and chromed)]


def set_origin(content: Content, draft: CharacterDraft, key: str, year: int) -> bool:
    """Swap the background: undo what the old one gave, put what the new one gives. False when not allowed."""
    if draft.heir or key not in origins_for(content, draft, year):
        return False
    st = draft.state
    old = content.origins.get(draft.origin_key)
    new = content.origins[key]
    preset = content.presets[draft.preset_key]
    if old is not None:
        if old.enemy_gang:
            st.faction_rep.pop(old.enemy_gang, None)
        if old.start_vow:
            st.vows.pop(old.start_vow, None)
    st.skills = _roll_skills(content, preset, new)
    if new.start_district in content.districts:
        st.district = new.start_district
    st.street_cred = new.start_street_cred
    if new.enemy_gang:
        st.faction_rep[new.enemy_gang] = content.balance.character.enemy_start_rep
    if new.start_vow:
        vow = content.vows.get(new.start_vow)
        if vow is not None and not ("cyberware" in vow.forbids and st.cyberware):
            st.vows[new.start_vow] = 0
    st.origin = key
    draft.origin_key = key
    return True


def randomize(rng: random.Random, content: Content, draft: CharacterDraft) -> None:
    """Everything cosmetic at random: roots with a name, gender, birth country, age, looks."""
    ch = content.balance.character
    st = draft.state
    st.flags[FLAG_GENDER] = rng.choice(("m", "f"))
    if content.heritages:
        set_heritage(rng, content, draft, rng.choice(list(content.heritages)))
    keys = [HOME] * 3 + list(content.countries)
    set_birth(draft, content, rng.choice(keys))
    set_age(draft, content, rng.randint(ch.age_min, ch.age_max))
    random_look(rng, draft)


def profile_line(content: Content, state: CharacterState) -> str:
    """«Японские корни · родом из Мексики · телосложение крепкое, неоновый ирокез, шрам через бровь»."""
    parts: list[str] = []
    h = content.heritages.get(heritage_of(state) or "")
    if h is not None:
        parts.append(h.name)
    b = content.countries.get(str(state.flags.get("birth_country") or ""))
    if b is not None:
        parts.append(f"родом: {b.name}")
    look = look_of(state)
    if look:
        bits = [f"телосложение {look['build']}" if look.get("build") else "", look.get("hair", ""), look.get("mark", "")]
        parts.append(", ".join(x for x in bits if x and x != "без особых примет") or "без особых примет")
    return " · ".join(parts)
