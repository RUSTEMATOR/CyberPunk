"""The look of a character and of an enemy (owner request 26.10: «редактор персонажа… нагенерить противников и
показывать модельки персонажей во время боя»; docs/PLAN_MODELS.md). Pure functions, randomness only through `rng`.

A look is a dict of part -> option key (`PARTS`), kept in the character's flag `look`. What the body carries comes
from the gear (`Gear`): the weapon in hand, chrome arms, glowing optics. Enemies get a look from their faction's style
(`balance.look.factions`, else one made from the faction key and the words of its description) and from the gear of
their archetype.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass
from typing import Any

from app.engine.pix import RGB, hexc

FLAG = "figure"  # owner report 27.10: the flag `look` is the onboarding look (engine/creator.py: build, hair, mark)
FLAG_FIGHT_OFF = "look_fight_off"

COLORS: dict[str, tuple[str, str]] = {
    "black": ("#1c1b22", "чёрный"), "graphite": ("#3d4250", "графит"), "grey": ("#7d828f", "серый"),
    "white": ("#e6e8ee", "белый"), "red": ("#c8283a", "красный"), "crimson": ("#7e1626", "бордовый"),
    "orange": ("#e3742a", "оранжевый"), "yellow": ("#f2c230", "жёлтый"), "olive": ("#5d6b34", "оливковый"),
    "green": ("#2fae5b", "зелёный"), "teal": ("#1f8f8c", "бирюзовый"), "blue": ("#2f6fd6", "синий"),
    "navy": ("#1d2b5c", "тёмно-синий"), "purple": ("#7a3cc2", "фиолетовый"), "pink": ("#e2479a", "розовый"),
    "tan": ("#b8946a", "песочный"), "brown": ("#6b4529", "коричневый"), "gold": ("#c9a23a", "золотой"),
}
HAIR_COLORS: dict[str, tuple[str, str]] = {
    "black": ("#17151a", "чёрные"), "brown": ("#5a3a22", "каштановые"), "blonde": ("#e0c070", "светлые"),
    "white": ("#e8e8f0", "белые"), "red": ("#b83222", "рыжие"), "pink": ("#ff5ab4", "розовые"),
    "cyan": ("#30e0f0", "голубые"), "purple": ("#9a50f0", "фиолетовые"), "green": ("#50e070", "зелёные"),
    "blue": ("#3a6cf0", "синие"),
}
NEON: dict[str, tuple[str, str]] = {
    "cyan": ("#2ef2ff", "голубой"), "magenta": ("#ff2bd6", "пурпурный"), "yellow": ("#ffe23b", "жёлтый"),
    "green": ("#4dff7a", "зелёный"), "red": ("#ff3046", "красный"), "orange": ("#ff9a2e", "оранжевый"),
    "blue": ("#4c7dff", "синий"), "white": ("#f4f6ff", "белый"),
}
SKINS: dict[str, tuple[str, str]] = {
    "s1": ("#f3d6c2", "очень светлая"), "s2": ("#e5b897", "светлая"), "s3": ("#c99470", "смуглая"),
    "s4": ("#a26d4a", "оливковая"), "s5": ("#7a4c31", "тёмная"), "s6": ("#523221", "очень тёмная"),
    "synth": ("#a9b6c8", "синтетика"), "chrome": ("#c4cad6", "сплошной хром"),
}

PARTS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("build", "Телосложение", [("slim", "худое"), ("avg", "среднее"), ("heavy", "крепкое")]),
    ("skin", "Кожа", [(k, v[1]) for k, v in SKINS.items()]),
    ("hair", "Причёска", [("shaved", "бритая голова"), ("buzz", "ёжик"), ("short", "короткая"),
                          ("mohawk", "ирокез"), ("spikes", "шипы"), ("bob", "каре"), ("long", "длинные"),
                          ("ponytail", "хвост"), ("bun", "пучок"), ("afro", "афро"), ("dreads", "дреды"),
                          ("hood", "капюшон")]),
    ("hair_c", "Цвет волос", [(k, v[1]) for k, v in HAIR_COLORS.items()]),
    ("eyes", "Глаза", [("plain", "обычные"), ("glow", "светятся неоном"), ("kiroshi", "оптика Kiroshi"),
                       ("visor", "визор"), ("optic", "один красный глаз"), ("shades", "очки")]),
    ("face", "Лицо", [("none", "чистое"), ("tattoo", "тату"), ("lines", "неоновые линии"),
                      ("chrome_jaw", "хромированная челюсть"), ("mask", "маска"), ("scar", "шрам")]),
    ("top", "Одежда", [("jacket", "куртка"), ("coat", "плащ"), ("hoodie", "худи"), ("vest", "жилет"),
                       ("tank", "майка"), ("suit", "костюм"), ("armor", "броня"), ("jumpsuit", "комбинезон"),
                       ("robe", "кимоно")]),
    ("top_c", "Цвет одежды", [(k, v[1]) for k, v in COLORS.items()]),
    ("legs_c", "Цвет штанов", [(k, v[1]) for k, v in COLORS.items()]),
    ("accent", "Неон", [(k, v[1]) for k, v in NEON.items()]),
]
PART_KEYS = [p[0] for p in PARTS]
_OPTS = {key: [o[0] for o in opts] for key, _, opts in PARTS}
_LABELS = {key: dict(opts) for key, _, opts in PARTS}
PART_NAMES = {key: name for key, name, _ in PARTS}

WEAPONS = ("none", "pistol", "smg", "rifle", "shotgun", "heavy", "blade", "blunt", "mantis", "exotic")


@dataclass
class Gear:
    weapon: str = "none"
    chrome_arms: int = 0      # 0 flesh, 1 the front arm, 2 both
    glow: bool = False        # optics glow (implants in the eyes)
    leader: bool = False
    borg: bool = False        # owner request 27.10: a borg is drawn in chrome


def label(part: str, value: str) -> str:
    return _LABELS.get(part, {}).get(value, value)


def normalize(d: Any) -> dict[str, str]:
    """A stored look made whole: unknown parts dropped, a missing or bad one gets the first option."""
    src = d if isinstance(d, dict) else {}
    return {k: (str(src.get(k)) if str(src.get(k)) in _OPTS[k] else _OPTS[k][0]) for k in PART_KEYS}


def random_look(rng: random.Random) -> dict[str, str]:
    look = {k: rng.choice(_OPTS[k]) for k in PART_KEYS}
    if rng.random() < 0.85 and look["skin"] in ("synth", "chrome"):
        look["skin"] = rng.choice(["s1", "s2", "s3", "s4", "s5", "s6"])
    if look["face"] == "mask" and rng.random() < 0.6:
        look["face"] = "none"
    return look


def default_look(seed: int, creator: dict[str, str] | None = None) -> dict[str, str]:
    """The look of a character who never opened the editor: random but the same every time, and true to the look
    picked when the character was made (engine/creator.py LOOKS: build, hair, mark)."""
    look = random_look(random.Random(seed * 7919 + 17))
    cr = creator or {}
    build = {"худощавое": "slim", "обычное": "avg", "жилистое": "slim", "крепкое": "heavy", "массивное": "heavy",
             "полное": "heavy"}.get(str(cr.get("build", "")))
    if build:
        look["build"] = build
    hair = {"бритая голова": ("shaved", None), "короткий ёжик": ("buzz", None), "длинные чёрные": ("long", "black"),
            "рыжие кудри": ("afro", "red"), "неоновый ирокез": ("mohawk", "cyan"), "седые до плеч": ("long", "white"),
            "выбритые виски": ("spikes", None), "косички": ("dreads", None), "белые как снег": ("bob", "white")
            }.get(str(cr.get("hair", "")))
    if hair:
        look["hair"] = hair[0]
        if hair[1]:
            look["hair_c"] = hair[1]
    mark = {"шрам через бровь": ("face", "scar"), "татуировка на шее": ("face", "tattoo"),
            "разные глаза": ("eyes", "kiroshi"), "пирсинг в губе": ("face", "lines")}.get(str(cr.get("mark", "")))
    if mark:
        look[mark[0]] = mark[1]
    return look


def cycle(look: dict[str, str], part: str, step: int = 1) -> dict[str, str]:
    out = normalize(look)
    if part in _OPTS:
        opts = _OPTS[part]
        out[part] = opts[(opts.index(out[part]) + step) % len(opts)]
    return out


def colour(part: str, value: str) -> RGB:
    table = {"hair_c": HAIR_COLORS, "accent": NEON, "skin": SKINS}.get(part, COLORS)
    return hexc((table.get(value) or next(iter(table.values())))[0])


# -- gear --------------------------------------------------------------------------------------------------------


_BLADE_WORDS = ("katana", "knife", "blade", "machete", "sword", "wakizashi", "kusarigama", "cleaver", "fang", "lotus",
                "cutter")
_BLUNT_WORDS = ("bat", "crowbar", "hammer", "baton", "pan", "axe", "tonfa", "knuckles", "guitar", "pipe", "club")


def weapon_kind(content: Any, key: str | None) -> str:
    w = content.weapons.get(key or "") if key else None
    if w is None:
        return "none"
    k = w.key.lower()
    if w.type in ("pistol", "smg", "rifle", "shotgun", "heavy") and w.skill == "firearms":
        return w.type
    if w.skill == "firearms":
        return "exotic"
    if "mantis" in k:
        return "mantis"
    if any(x in k for x in _BLUNT_WORDS) or w.type == "heavy":
        return "blunt"
    if any(x in k for x in _BLADE_WORDS):
        return "blade"
    return "blunt" if w.type == "exotic" else "blade"


def gear_from_cyber(content: Any, keys: list[str], weapon: str) -> Gear:
    g = Gear(weapon=weapon)
    for key in keys:
        cw = content.cyberware.get(key)
        if cw is None:
            continue
        if cw.slot in ("forearms", "hands"):
            g.chrome_arms = 2
            if "blades" in (cw.abilities or []) and weapon in ("none", "blade", "blunt"):
                g.weapon = "mantis"
        elif cw.slot == "eyes":
            g.glow = True
    return g


def player_gear(content: Any, state: Any) -> Gear:
    """What the character carries: the first equipped weapon, the chrome of the arms and eyes."""
    from app.engine import derived

    held = [it.key for it in state.inventory if it.kind == "weapon" and it.equipped]
    weapon = weapon_kind(content, held[0]) if held else "none"
    g = gear_from_cyber(content, list(state.cyber_keys), weapon)
    if derived.is_borg(state, content):
        g.borg, g.chrome_arms, g.glow = True, 2, True
    return g


# -- enemies -----------------------------------------------------------------------------------------------------


_TOP_BY_ARMOR = (
    (("suit", "kimono", "jumpsuit", "overalls", "racer", "hazmat", "netrunner"), None),
    (("armorjack", "riot", "metalgear", "powered", "exo", "plate", "armored", "firefighter", "security", "police"),
     "armor"),
    (("trench", "duster", "raincoat", "cloak", "poncho", "coat"), "coat"),
    (("hoodie",), "hoodie"),
    (("jacket", "leathers", "bomber", "padded", "colors"), "jacket"),
    (("vest",), "vest"),
    (("robe",), "robe"),
)


def _top_from_armor(key: str) -> str | None:
    k = key.lower()
    if "kimono" in k or "robe" in k:
        return "robe"
    if "suit" in k and "armored" not in k:
        return "jumpsuit" if any(x in k for x in ("netrunner", "stealth", "racer", "hazmat", "nanoweave")) else "suit"
    if any(x in k for x in ("overalls", "jumpsuit")):
        return "jumpsuit"
    for words, top in _TOP_BY_ARMOR[1:]:
        if any(w in k for w in words):
            return top
    return None


_KEYWORDS = (
    (("хром", "механ", "киборг", "имплант"), {"chrome": 0.7}),
    (("кож", "байк"), {"tops": ["jacket"], "top_c": ["black", "brown"]}),
    (("костюм", "корпор", "деловой"), {"tops": ["suit"]}),
    (("брон", "солдат", "военн", "наёмн", "камуфл"), {"tops": ["armor", "vest"], "top_c": ["olive", "graphite", "tan"]}),
    (("маск",), {"face": ["mask"]}),
    (("тату", "татуир"), {"face": ["tattoo"]}),
    (("катан", "нож", "клинк", "мачете"), {"melee": 0.5}),
    (("пустын", "кочев"), {"top_c": ["tan", "brown", "orange"], "tops": ["coat", "jacket"]}),
)


def faction_style(content: Any, faction: str) -> dict[str, Any]:
    """The style a faction's fighters are drawn in: the hand-made one, else one grown from the key and the words."""
    styles = content.balance.look.factions
    if faction in styles:
        return dict(styles[faction])
    seed = zlib.crc32(faction.encode())
    r = random.Random(seed)
    cols = list(COLORS)
    st: dict[str, Any] = {
        "top_c": r.sample(cols, 3), "legs_c": r.sample(["black", "graphite", "navy", "brown", "olive", "tan"], 2),
        "accent": r.sample(list(NEON), 2), "hair": r.sample(_OPTS["hair"][:-1], 4),
        "hair_c": r.sample(list(HAIR_COLORS), 3), "tops": r.sample(_OPTS["top"], 3),
    }
    fac = content.gangs.get(faction) or content.corps.get(faction)
    words = f"{getattr(fac, 'style', '') or ''} {getattr(fac, 'description', '') or ''}".lower()
    for keys, extra in _KEYWORDS:
        if any(k in words for k in keys):
            for k, v in extra.items():
                st[k] = v
    return st


def _pick(rng: random.Random, opts: Any, fallback: list[str]) -> str:
    pool = [o for o in (opts or []) if o]
    return rng.choice(pool) if pool else rng.choice(fallback)


def enemy_look(content: Any, faction: str, archetype: str, seed: int, leader: bool = False,
               ai: str = "") -> tuple[dict[str, str], Gear]:
    """The look and gear of one enemy; the same seed gives the same fighter."""
    rng = random.Random(seed)
    st = faction_style(content, faction)
    look = random_look(rng)
    look["top_c"] = _pick(rng, st.get("top_c"), _OPTS["top_c"])
    look["legs_c"] = _pick(rng, st.get("legs_c"), ["black", "graphite", "navy"])
    look["accent"] = _pick(rng, st.get("accent"), _OPTS["accent"])
    look["hair"] = _pick(rng, st.get("hair"), _OPTS["hair"])
    look["hair_c"] = _pick(rng, st.get("hair_c"), _OPTS["hair_c"])
    look["top"] = _pick(rng, st.get("tops"), _OPTS["top"])
    if st.get("face") and rng.random() < 0.6:
        look["face"] = _pick(rng, st.get("face"), ["none"])
    elif rng.random() < 0.6:
        look["face"] = "none"
    if st.get("eyes") and rng.random() < 0.7:
        look["eyes"] = _pick(rng, st.get("eyes"), ["plain"])
    elif rng.random() < 0.5:
        look["eyes"] = "plain"
    if st.get("build"):
        look["build"] = _pick(rng, st.get("build"), _OPTS["build"])
    if st.get("skin") and rng.random() < 0.5:
        look["skin"] = _pick(rng, st.get("skin"), ["s3"])
    elif look["skin"] in ("synth", "chrome"):
        look["skin"] = rng.choice(["s1", "s2", "s3", "s4", "s5", "s6"])
    en = content.enemies.get(archetype)
    weapon = weapon_kind(content, en.weapon if en else None)
    if weapon == "none" and float(st.get("melee", 0) or 0) > rng.random():
        weapon = "blade"
    g = gear_from_cyber(content, list(en.cyberware) if en else [], weapon)
    if en is not None and en.armor:
        top = _top_from_armor(en.armor)
        if top:
            look["top"] = top
    if g.chrome_arms == 0 and float(st.get("chrome", 0) or 0) > rng.random():
        g.chrome_arms = rng.choice([1, 2])
    if ai == "netrunner":
        look["eyes"] = "visor"
        if look["top"] not in ("armor", "suit"):
            look["hair"] = "hood"
    if leader:
        g.leader = True
        if look["build"] == "slim":
            look["build"] = "avg"
    if faction == "cyberpsycho":
        look.update(build="heavy", eyes="optic", face=rng.choice(["chrome_jaw", "mask"]))
        g.chrome_arms = 2
        if rng.random() < 0.5:
            look["skin"] = "chrome"
    return look, g
