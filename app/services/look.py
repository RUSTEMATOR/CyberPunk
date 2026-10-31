"""Looks and pictures of characters (owner request 26.10; docs/PLAN_MODELS.md).

`look_of` reads the character's flag `look` (or the stable random look of a character who never opened the editor),
`portrait_png` draws the editor's picture, `fight_png` the fight scene of the window, `gallery_png` a group of
fighters of one faction. Everything is drawn by engine/sprite.py and blown up by engine/pix.py.
"""

from __future__ import annotations

import zlib
from dataclasses import asdict
from typing import Any

from app.content.loader import Content
from app.engine import combat as cb
from app.engine import look as lk
from app.engine import sprite as spr


def cfg(content: Content):  # noqa: ANN201
    return content.balance.look


def _seed(ch: Any) -> int:
    cid = getattr(ch, "id", None)
    if cid is not None:
        return int(cid)
    st = getattr(ch, "state", ch)
    return zlib.crc32(f"{st.name}|{st.surname}|{st.birth_year}".encode())


def look_of(ch: Any) -> dict[str, str]:
    st = getattr(ch, "state", ch)
    rec = st.flags.get(lk.FLAG)
    if isinstance(rec, dict) and rec:
        return lk.normalize(rec)
    creator = st.flags.get("look")  # the look picked when the character was made
    return lk.default_look(_seed(ch), creator if isinstance(creator, dict) else None)


def snapshot(content: Content, ch: Any) -> dict[str, Any]:
    """The look and gear of a character, JSON-safe (kept in a fight's ctx for hired players)."""
    return {"look": look_of(ch), "gear": asdict(lk.player_gear(content, ch.state))}


def _gear(d: Any) -> lk.Gear:
    if isinstance(d, lk.Gear):
        return d
    src = d if isinstance(d, dict) else {}
    return lk.Gear(weapon=str(src.get("weapon") or "none"), chrome_arms=int(src.get("chrome_arms", 0) or 0),
                   glow=bool(src.get("glow")), leader=bool(src.get("leader")), borg=bool(src.get("borg")))


def fight_on(content: Content, ch: Any) -> bool:
    c = cfg(content)
    return bool(c.enabled and c.fight_picture and not ch.state.flags.get(lk.FLAG_FIGHT_OFF))


def figure_of(content: Content, ch: Any, look: dict[str, str] | None = None) -> Any:
    """The bare figure of the character (no street behind), for the 3D district (services/city3d.py)."""
    return spr.figure(look or look_of(ch), lk.player_gear(content, ch.state))


def portrait_png(content: Content, ch: Any, look: dict[str, str] | None = None) -> bytes:
    lo = look or look_of(ch)
    cv = spr.portrait(lo, lk.player_gear(content, ch.state), spr.place_seed(str(ch.state.district)))
    return cv.png(cfg(content).preview_scale)


def gallery_png(content: Content, faction: str, year: int, seed: int, count: int = 5) -> bytes | None:
    pool = cb._faction_pool(content, faction, year)  # noqa: SLF001
    if not pool:
        return None
    pool = sorted(pool, key=lambda e: (e.danger, e.key))
    figs: list[tuple[dict[str, str], lk.Gear]] = []
    for i in range(count):
        en = pool[(seed + i * 3) % len(pool)] if i < count - 1 else pool[-1]
        figs.append(lk.enemy_look(content, faction, en.key, seed * 101 + i, leader=en.leader, ai=en.ai))
    return spr.lineup(figs, spr.place_seed(faction)).png(cfg(content).gallery_scale)


def _ally_look(c: cb.Combatant, i: int) -> tuple[dict[str, str], lk.Gear]:
    look = lk.default_look(zlib.crc32(c.name.encode()) + i)
    weapon = "rifle" if cb.RANGED in c.attacks else "blade"
    return look, lk.Gear(weapon=weapon, chrome_arms=1 if c.has_chrome and i % 2 else 0)


def fight_png(content: Content, ch: Any, cs: cb.CombatState, target: int = -1) -> bytes | None:
    """The fight as a picture, or None when pictures are off for this character or for everybody."""
    if not fight_on(content, ch):
        return None
    try:
        return _fight_png(content, ch, cs, target)
    except Exception:  # noqa: BLE001 - a picture never breaks the fight
        import logging

        logging.getLogger(__name__).exception("look: fight picture failed")
        return None


def _fight_png(content: Content, ch: Any, cs: cb.CombatState, target: int) -> bytes:
    looks = cs.ctx.get("looks") or {}
    p = cs.player
    fighters: list[dict[str, Any]] = [{
        "id": p.id, "side": "player", "look": look_of(ch), "gear": lk.player_gear(content, ch.state),
        "hp": p.hp, "max_hp": p.max_hp, "out": p.out,
    }]
    for i, a in enumerate(cs.allies):
        rec = looks.get(a.id)
        if isinstance(rec, dict) and rec.get("look"):
            lo, g = lk.normalize(rec["look"]), _gear(rec.get("gear"))
        else:
            lo, g = _ally_look(a, i)
        fighters.append({"id": a.id, "side": "ally", "look": lo, "gear": g, "hp": a.hp, "max_hp": a.max_hp,
                         "out": a.out})
    for i, e in enumerate(cs.enemies):
        seed = zlib.crc32(f"{e.name}|{e.archetype}|{i}".encode())
        en = content.enemies.get(e.archetype)
        faction = cs.faction
        if en is not None and en.factions and cb.SPECIAL_FACTIONS.get(faction, faction) not in en.factions:
            faction = en.factions[0]
        lo, g = lk.enemy_look(content, faction, e.archetype, seed, leader=e.leader, ai=e.ai)
        fighters.append({"id": e.id, "side": "enemy", "look": lo, "gear": g, "hp": e.hp, "max_hp": e.max_hp,
                         "out": e.out, "n": i + 1, "target": i == target and e.active,
                         "barrier": bool(getattr(e, "barrier", 0))})
    events = list((cs.log[-1].get("ev") or []) if cs.log else [])
    district = str(cs.ctx.get("district") or ch.state.district)
    cv = spr.fight_scene(fighters, events, spr.place_seed(district), cover=bool(p.cover))
    return cv.png(cfg(content).scale)
