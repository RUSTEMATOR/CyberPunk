"""Relics (owner request 25.10, docs/PLAN_ENDGAME2.md part 6): things with a history, one of each in the whole city.
A relic gives strong modifiers and a title (engine/derived reads state.relics, loaded in db/repo).

world_state `relics` = {key: {"char", "tg", "name", "since", "how"} | {"hall": hall key, "name"}}; a free relic is absent.
- Expeditions give their relic when it is free; an ending leaves its relic with the legend in the Hall.
- A legend that leaves takes the relics it holds into the Hall; a win in a duel with it takes them back into the city.
- A holder who dies otherwise loses the relic: it is free again (checked lazily).
- A holder who loses a skirmish with another player (services/players.attack) gives one relic to the winner.
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.content.loader import Content
from app.db import repo
from app.db.models import Character, PlayerMail
from app.services.game import ActiveCharacter

KEY = "relics"


def get(content: Content, key: str):  # noqa: ANN201
    return next((r for r in content.balance.relics.items if r.key == key), None)


async def book(session: AsyncSession) -> dict[str, dict]:
    """The holders; the relics of the dead (not in the Hall) are free again."""
    b = {str(k): dict(v) for k, v in (await repo.ws_get(session, KEY, {}) or {}).items()}
    dead = []
    for k, r in b.items():
        if r.get("char") and not r.get("hall"):
            row = await session.get(Character, int(r["char"]))
            if row is None or not row.alive:
                dead.append(k)
    if dead:
        for k in dead:
            b.pop(k, None)
        await repo.ws_set(session, KEY, b)
    return b


async def _save(session: AsyncSession, b: dict) -> None:
    await repo.ws_set(session, KEY, b)


def held_by(b: dict, char_id: int) -> list[str]:
    return [k for k, r in b.items() if int(r.get("char", 0) or 0) == int(char_id) and not r.get("hall")]


def _rec(ch: ActiveCharacter, how: str) -> dict:
    return {"char": ch.id, "tg": ch.player_tg_id, "name": f"{ch.state.name} {ch.state.surname}".strip(),
            "since": utcnow().date().isoformat(), "how": how}


def _refresh(ch: ActiveCharacter, b: dict) -> None:
    ch.state.relics = tuple(held_by(b, ch.id))


async def grant(session: AsyncSession, content: Content, ch: ActiveCharacter, key: str, how: str) -> list[str]:
    from app.services import loud

    r = get(content, key)
    if r is None:
        return []
    b = await book(session)
    if key in b:
        who = b[key].get("name", "")
        return [f"💎 {r.name} уже в чужих руках ({who}). Отнять можно в стычке или в поединке с легендой."]
    b[key] = _rec(ch, how)
    await _save(session, b)
    _refresh(ch, b)
    await loud.post(session, ch.id, f"💎 У {loud.who(ch.state.name, ch.state.surname)} теперь реликвия «{r.name}» ({how}).")
    return [f"💎 Реликвия «{r.name}» твоя. Титул: {r.title}."]


async def to_hall(session: AsyncSession, content: Content, ch: ActiveCharacter, hall_key: str, ending_relic: str = "") -> None:
    """A legend leaves: its relics go with it into the Hall; an ending leaves its own relic there too."""
    b = await book(session)
    name = f"{ch.state.name} {ch.state.surname}".strip()
    for k in held_by(b, ch.id):
        b[k] = {"hall": hall_key, "name": name, "since": utcnow().date().isoformat()}
    if ending_relic and get(content, ending_relic) is not None and ending_relic not in b:
        b[ending_relic] = {"hall": hall_key, "name": name, "since": utcnow().date().isoformat()}
    await _save(session, b)


async def from_legend(session: AsyncSession, content: Content, ch: ActiveCharacter, hall_key: str) -> list[str]:
    b = await book(session)
    won = [k for k, r in b.items() if r.get("hall") == hall_key]
    if not won:
        return []
    for k in won:
        b[k] = _rec(ch, "победа над легендой")
    await _save(session, b)
    _refresh(ch, b)
    return [f"💎 Реликвия «{get(content, k).name if get(content, k) else k}» переходит к тебе." for k in won]


async def pvp(session: AsyncSession, content: Content, winner: ActiveCharacter | None, winner_char: int, winner_tg: int,
              winner_name: str, loser_char: int, loser_tg: int, rng: random.Random) -> str | None:
    """The loser of a skirmish gives one relic to the winner. Returns the relic name or None."""
    b = await book(session)
    mine = held_by(b, loser_char)
    if not mine:
        return None
    k = rng.choice(mine)
    b[k] = {"char": winner_char, "tg": winner_tg, "name": winner_name, "since": utcnow().date().isoformat(),
            "how": "стычка"}
    await _save(session, b)
    if winner is not None:
        _refresh(winner, b)
    r = get(content, k)
    nm = r.name if r else k
    session.add(PlayerMail(to_tg_id=int(loser_tg), from_tg_id=0, from_name="Город", kind="city",
                           payload={"text": f"Реликвия «{nm}» ушла к {winner_name} после стычки."}))
    from app.services import loud

    await loud.post(session, winner_char, f"💎 Реликвия «{nm}» меняет хозяина: теперь она у {winner_name}.")
    return nm


async def holder_names(session: AsyncSession) -> dict[str, str]:
    return {k: (f"легенды {r.get('name')}" if r.get("hall") else str(r.get("name", ""))) for k, r in (await book(session)).items()}


def titles(content: Content, st) -> list[str]:  # noqa: ANN001
    return [r.title for k in st.relics if (r := get(content, k)) is not None]
