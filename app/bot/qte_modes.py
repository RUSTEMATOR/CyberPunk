"""Which quick-reaction game the Mini App plays for an attack (owner request 30.10: «для каждого вида оружия своя
мини-игра: холодное — фрут-ниндзя, дробовик — один выстрел, автомат — быстро по нескольким точкам, кулаки — натапать,
пока не разобьётся витрина»). Pure; docs/PLAN_QTE.md.

Modes: quick (pistol: one target, fast), spray (SMG, assault rifle, minigun: many targets), scope (sniper, bow: catch the
swaying target in the crosshair), blast (shotgun, launcher: shoot when the target fills the ring), lock (smart guns: lock
three marks), slice (blades: swipe the thrown targets), swing (blunt: stop the mark in the zone), smash (fists: tap the
glass until it breaks)."""

from __future__ import annotations

from app.content.loader import Content
from app.engine import combat as cb

MODES = ("quick", "spray", "scope", "blast", "lock", "slice", "swing", "smash")

_SLICE = ("katana", "knife", "machete", "cleaver", "blade", "mantis", "axe", "wakizashi", "kusarigama", "sword", "seraph",
          "fang", "stiletto", "claw", "razor", "monowire", "spur", "nail", "plasma_cutter", "lotus")
_SMASH = ("knuckle", "gorilla", "fist", "hydraulic")
_SCOPE = ("sniper", "marksman", "dmr", "anti_materiel", "railgun", "lever", "hunting", "widowmaker", "bow", "crossbow",
          "harpoon", "cantor", "tech_rifle")
_SPRAY = ("minigun", "auto_cannon", "flamethrower", "sonic", "nail_gun", "auto_shotgun")
_BLAST = ("launcher", "rocket", "grenade", "flare", "last_song")


def _has(key: str, words: tuple[str, ...]) -> bool:
    return any(w in key for w in words)


def mode_of(content: Content, source: str, kind: str) -> str:
    """The game for an attack with this weapon (a weapon key, an implant key, or "" for bare fists)."""
    key = source or ""
    if kind != cb.RANGED:
        if not key or _has(key, _SMASH):
            return "smash"
        return "slice" if _has(key, _SLICE) else "swing"
    if key.startswith("smart_"):
        return "lock"
    if _has(key, _BLAST):
        return "blast"
    if _has(key, _SCOPE):
        return "scope"
    if _has(key, _SPRAY):
        return "spray"
    w = content.weapons.get(key)
    t = (w.type if w else None) or "pistol"
    return {"smg": "spray", "rifle": "spray", "shotgun": "blast", "heavy": "spray"}.get(t, "quick")


def modes(content: Content, cs: cb.CombatState) -> dict[str, str]:
    """For the fight screen: the game of every weapon at hand ("w:<key>") and of the plain shoot / melee buttons."""
    p = cs.player
    out: dict[str, str] = {}
    for key, atk in p.arsenal.items():
        out[f"w:{key}"] = mode_of(content, key, atk.kind)
    ranged = p.attacks.get(cb.RANGED)
    melee = p.attacks.get(cb.MELEE_NL if cs.nonlethal else cb.MELEE) or p.attacks.get(cb.MELEE)
    if ranged is not None:
        out["shoot"] = mode_of(content, ranged.source, cb.RANGED)
    if melee is not None:
        out["melee"] = mode_of(content, melee.source, cb.MELEE)
    return out
