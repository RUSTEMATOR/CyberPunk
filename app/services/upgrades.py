"""Weapon attachments and things for your home (owner request). State: the character flags
`weapon_mods` = {weapon key: [mod keys]} (work while that weapon is equipped) and `home` = [home item keys]
(they move with you). Their modifiers work through engine/derived.py."""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.content.schemas import HomeItem, WeaponMod
from app.engine.effects import apply_effects
from app.services import game_events as bus
from app.services.game import ActiveCharacter, save_active

BLOCK_NO_WEAPON = "no_weapon"
BLOCK_FIT = "fit"
BLOCK_FULL = "full"
BLOCK_HAVE = "have"
BLOCK_EDDIES = "eddies"
BLOCK_GONE = "gone"


def weapon_type(content: Content, key: str) -> str | None:
    w = content.weapons.get(key)
    if w is None:
        return None
    return w.type or ("melee" if w.skill == "melee" else "pistol")


def _in_year(rec, year: int) -> bool:  # noqa: ANN001
    return (rec.available_from is None or rec.available_from <= year) and (
        rec.available_until is None or year <= rec.available_until)


def mods_for(content: Content, ch: ActiveCharacter, year: int) -> list[WeaponMod]:
    """Attachments that fit the equipped weapon in this year."""
    t = weapon_type(content, ch.state.weapon_key or "")
    if t is None:
        return []
    return [m for m in content.balance.upgrades.weapon_mods if t in m.types and _in_year(m, year)]


def installed(ch: ActiveCharacter) -> list[str]:
    return list((ch.state.flags.get("weapon_mods") or {}).get(ch.state.weapon_key or "", []))


def home_items(content: Content, year: int) -> list[HomeItem]:
    return [h for h in content.balance.upgrades.home if _in_year(h, year)]


def owned_home(ch: ActiveCharacter) -> list[str]:
    return list(ch.state.flags.get("home") or [])


async def install(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, key: str) -> str | None:
    w = ch.state.weapon_key
    if not w:
        return BLOCK_NO_WEAPON
    m = next((x for x in mods_for(content, ch, year) if x.key == key), None)
    if m is None:
        return BLOCK_FIT
    have = installed(ch)
    if key in have:
        return BLOCK_HAVE
    if len(have) >= content.balance.upgrades.max_weapon_mods:
        return BLOCK_FULL
    if ch.state.eddies < m.price:
        return BLOCK_EDDIES
    apply_effects(ch.state, {"eddies": -m.price, "skill_xp:tech": 1}, content, random.Random(), year)
    allm = dict(ch.state.flags.get("weapon_mods") or {})
    allm[w] = have + [key]
    ch.state.flags["weapon_mods"] = allm
    await save_active(session, ch, tz)
    await bus.publish("upgrade_done", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch,
                      kind="weapon_mod")
    return None


async def remove(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, key: str) -> str | None:
    w = ch.state.weapon_key
    have = installed(ch)
    if not w or key not in have:
        return BLOCK_GONE
    allm = dict(ch.state.flags.get("weapon_mods") or {})
    allm[w] = [k for k in have if k != key]
    ch.state.flags["weapon_mods"] = allm
    await save_active(session, ch, tz)
    return None


async def buy_home(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, key: str) -> str | None:
    h = next((x for x in home_items(content, year) if x.key == key), None)
    if h is None:
        return BLOCK_GONE
    if key in owned_home(ch):
        return BLOCK_HAVE
    if ch.state.eddies < h.price:
        return BLOCK_EDDIES
    apply_effects(ch.state, {"eddies": -h.price, "mood": 2}, content, random.Random(), year)
    ch.state.flags["home"] = owned_home(ch) + [key]
    await save_active(session, ch, tz)
    await bus.publish("upgrade_done", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch,
                      kind="home_item")
    return None
