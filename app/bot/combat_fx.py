"""The player's chrome going off, for the Mini App's screen effects (owner request 30.10: «тематические эффекты при
использовании способностей имплантов, Сандевистана, Берсерка, и большая надпись, которая отдаляется, становясь
прозрачной»). Pure: reads the fight's events, writes nothing. The group window ignores it.

Each record: {"id": stable id of the event (the app plays it once), "kind": the look (sandy, berserk, kerenzikov, cloak,
smoke, blast, swarm, stim, emp, chrome), "word": the big title}."""

from __future__ import annotations

from typing import Any

from app.content.loader import Content
from app.engine import combat as cb

# The look of a fight implant: by its organ group first, then by its button.
GROUP_KIND = {"sandevistan": "sandy", "berserk": "berserk", "kerenzikov": "kerenzikov"}
BUTTON_KIND = {
    "Сандевистан": "sandy", "Горизонт": "sandy", "Берсерк": "berserk", "Штурм": "berserk",
    "Керензиков": "kerenzikov", "Уклон": "kerenzikov", "Маскировка": "cloak", "Мираж": "cloak", "Фаза": "cloak",
    "Дым": "smoke", "Пыль": "smoke", "Ракеты": "blast", "Граната": "blast", "Пушка": "blast", "Рука-пушка": "blast",
    "Дротик": "blast", "Нанорой": "swarm", "Стимы": "stim", "Инжектор": "stim",
}
# The look of a chrome ability, by its family.
FAMILY_KIND = {
    "blades": "berserk", "force": "berserk", "menace": "berserk", "brace": "berserk",
    "predict": "kerenzikov", "calc": "kerenzikov", "sprint": "sandy", "leap": "sandy", "dive": "sandy",
    "cloak": "cloak", "hide": "cloak", "night": "cloak", "spores": "smoke", "toxin": "swarm", "drone": "swarm",
    "emp": "emp", "jackin": "emp", "netsense": "emp", "radio": "emp", "medic": "stim", "mend": "stim", "chem": "stim",
}
KINDS = ("sandy", "berserk", "kerenzikov", "cloak", "smoke", "blast", "swarm", "stim", "emp", "chrome",
         "blackwall", "litany", "fire", "shock", "jam", "virus", "glitch")
# Owner request 30.10 «больше цветокода: литания розовая, чёрный для Чёрного Заслона». Quickhacks by what they do; every
# cult script (bw_*) and every Blackwall implant or weapon is black, the Litany is pink.
HACK_KIND = {
    "overheat": "fire", "vending_burst": "fire", "synapse_burnout": "fire",
    "short_circuit": "shock", "chain_lightning": "shock", "crude_overload": "shock", "neurostorm": "shock",
    "system_collapse": "shock", "sonic_shock": "jam",
    "reboot_optics": "jam", "distraction": "jam", "comm_jam": "jam", "lights_out": "jam",
    "contagion": "virus", "leech": "virus", "implant_drain": "virus",
    "weapon_glitch": "glitch", "cripple_movement": "glitch", "cyberware_malfunction": "glitch", "implant_jam": "glitch",
    "bw_litany_hymn": "litany",
}
LITANY = {"bw_litany", "litany_voice", "bw_litany_hymn"}


def implant_fx(content: Content, key: str) -> tuple[str, str]:
    """(kind, word) of a fight implant."""
    cw = content.cyberware.get(key)
    if cw is None:
        return "chrome", "ХРОМ"
    button = cw.active.button if cw.active is not None else cw.name
    kind = GROUP_KIND.get(cw.exclusive_group or "") or BUTTON_KIND.get(button) or "chrome"
    if key in LITANY:
        kind = "litany"
    elif cw.build == "blackwall":
        kind = "blackwall"
    return kind, button.upper()


def hack_fx(content: Content, key: str) -> tuple[str, str]:
    """(kind, word) of a quickhack the player ran."""
    qh = content.quickhacks.get(key)
    kind = HACK_KIND.get(key) or ("blackwall" if key.startswith("bw_") else "emp")
    return kind, (qh.name if qh else key).upper()


def ability_fx(content: Content, fam: str, key: str) -> tuple[str, str]:
    """(kind, word) of a chrome ability used in a fight: the family gives the look, the ability its name."""
    ab = content.balance.chrome_abilities.get(key)
    kind = FAMILY_KIND.get(fam, "chrome")
    if key in LITANY:
        kind = "litany"
    elif fam == "blackwall" or any(cw.build == "blackwall" and key in cw.abilities for cw in content.cyberware.values()):
        kind = "blackwall"
    return kind, (ab.name if ab else fam).upper()


def recent(content: Content, cs: cb.CombatState) -> list[dict[str, Any]]:
    """The player's implant activations, implant shots, implant dodges, quickhacks that went through and Blackwall strikes
    of the last tick and of the tick in progress."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    groups = [(int(e.get("tick", 0)), e.get("ev") or []) for e in cs.log[-1:]] + [(cs.tick, cs.cur_events)]
    for tick, evs in groups:
        for i, ev in enumerate(evs):
            if ev.get("by") != cb.PLAYER_ID:
                continue
            k = ev.get("k")
            if k == "hack" and ev.get("prog") and ev.get("ok") and f"h:{tick}:{ev['prog']}" not in seen:
                seen.add(f"h:{tick}:{ev['prog']}")  # a program that hit several targets plays once
                kind, word = hack_fx(content, str(ev["prog"]))
                out.append({"id": f"{tick}:{i}", "kind": kind, "word": word})
                continue
            if k == "blackwall" and f"b:{tick}" not in seen:  # a Blackwall weapon's strike
                seen.add(f"b:{tick}")
                out.append({"id": f"{tick}:{i}", "kind": "blackwall", "word": "ЧЁРНЫЙ ЗАСЛОН"})
                continue
            if not ev.get("imp"):
                continue
            if ev.get("k") not in ("activate", "shot", "dodge"):
                continue
            kind, word = implant_fx(content, str(ev["imp"]))
            out.append({"id": f"{tick}:{i}", "kind": kind, "word": word})
    return out[-3:]
