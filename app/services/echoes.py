"""Echoes through time (docs/EPIC_PLAYERS.md, phase 4).

Every player lives in his own year. A week of your life may meet a character of another player whose life covers
your year (a cameo), and may find a stash somebody hid in your district for a year you have reached.
Lines go to the week report; the other player learns about it by mail.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db.models import Character, PlayerMail, Stash
from app.engine.character_gen import FLAG_GENDER
from app.engine.effects import apply_effects
from app.services import game
from app.services.game import ActiveCharacter, save_active

MAIL_CAMEO = "cameo"
MAIL_STASH = "stashfound"

# cameo scenes: (text, effects); {name} is the other character, {a} the past-tense ending of the other
CAMEOS: list[tuple[str, dict[str, int]]] = [
    ("В баре за стойкой сидит {name}. Пара слов, пара шуток, и {name} молча оплачивает твой стакан.", {"mood": 4}),
    ("Мимо на бешеной скорости проносится {name}, за машиной гонится патруль. На асфальте остаётся выпавший кредчип.",
     {"eddies": 100}),
    ("На стене в переулке свежее граффити: портрет {name} и подпись «Легенда района». Кто-то оставил рядом свечу.",
     {"mood": 2}),
    ("Фиксер вспоминает, что {name} когда-то брал{a} у него заказы. «Хорошая школа», — говорит он и смотрит на тебя "
     "внимательнее.", {"street_cred": 1}),
    ("В новостях мелькает {name}: репортаж с места очередной заварухи. Ты узнаёшь это лицо.", {"mood": 1}),
    ("На рынке {name} торгуется за ствол и уступает тебе очередь. Продавец по старой дружбе делает скидку и тебе.",
     {"eddies": 80}),
]
CAMEO_MAIL_TEXT = "Где-то в городе {name} встретил{a} тебя."


def _life_years(content: Content, row: Character, alive_year: int | None) -> tuple[int, int]:
    """The years a character was an adult in the city: from 16 years of age to now (alive) or to the death."""
    start = row.birth_year + 16
    end = alive_year if row.alive and alive_year is not None else row.birth_year + row.age_years
    return start, end


def _a(g: str) -> str:
    return "а" if g == "f" else ""


async def _cameo(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
) -> list[str]:
    cfg = content.balance.players.echo
    if rng.random() >= cfg.cameo_chance:
        return []
    res = await session.execute(select(Character).where(Character.player_tg_id != ch.player_tg_id))
    cands: list[Character] = []
    for row in res.scalars():
        their_year = await game.current_year(session, content, row.player_tg_id) if row.alive else None
        lo, hi = _life_years(content, row, their_year)
        if lo <= year <= hi:
            cands.append(row)
    if not cands:
        return []
    other = rng.choice(cands)
    name = " ".join(x for x in (other.name, other.surname) if x)
    g = str((other.flags or {}).get(FLAG_GENDER, "m"))
    text, fx = rng.choice(CAMEOS)
    apply_effects(ch.state, dict(fx), content, rng, year)
    if other.alive:
        session.add(PlayerMail(
            to_tg_id=other.player_tg_id, from_tg_id=ch.player_tg_id, from_char_id=ch.id, to_char_id=other.id,
            from_name=f"{ch.state.name} {ch.state.surname}".strip(), from_year=year, kind=MAIL_CAMEO,
            payload={"g": str(ch.state.flags.get(FLAG_GENDER, "m"))},
        ))
    from app.services.players import _bond_row, _bond_value, _utcnow  # the bond lives with the players service

    row = await _bond_row(session, ch.id, other.id, create=True)
    if row is not None:
        row.value = min(content.balance.players.bond_max, _bond_value(content, row) + cfg.cameo_bond)
        row.last_at = _utcnow()
    return [text.format(name=name, a=_a(g))]


@dataclass
class StashResult:
    blocked: str | None = None
    stash: Stash | None = None


async def hide(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    amount: int, years: int,
) -> StashResult:
    cfg = content.balance.players.echo
    if amount <= 0 or years not in cfg.stash_years:  # owner request 24.10: any sum
        return StashResult("gone")
    mine = await session.execute(select(Stash).where(Stash.owner_tg == ch.player_tg_id, Stash.status == "hidden"))
    if len(list(mine.scalars())) >= cfg.stash_max:
        return StashResult("limit")
    if ch.state.eddies < amount:
        return StashResult("eddies")
    apply_effects(ch.state, {"eddies": -amount}, content, rng, year)
    await save_active(session, ch, tz)
    stash = Stash(owner_tg=ch.player_tg_id, owner_char=ch.id, owner_name=f"{ch.state.name} {ch.state.surname}".strip(),
                  district=ch.state.district, open_year=year + years, eddies=amount, status="hidden")
    session.add(stash)
    await session.flush()
    return StashResult(None, stash)


async def my_stashes(session: AsyncSession, tg_id: int) -> list[Stash]:
    res = await session.execute(select(Stash).where(Stash.owner_tg == tg_id).order_by(Stash.id.desc()).limit(10))
    return list(res.scalars())


async def _find_stash(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
) -> list[str]:
    from app.bot import texts_players as T  # local: services do not import texts at module level

    res = await session.execute(select(Stash).where(
        Stash.status == "hidden", Stash.district == ch.state.district, Stash.open_year <= year,
    ).order_by(Stash.id))
    out: list[str] = []
    for st in res.scalars():
        if st.owner_char == ch.id:
            continue  # your own stash waits for somebody else, or for your heir
        st.status, st.finder_tg = "found", ch.player_tg_id
        apply_effects(ch.state, {"eddies": st.eddies}, content, rng, year)
        out.append(T.STASH_FOUND.format(name=st.owner_name, eddies=f"{st.eddies:,}".replace(",", " ")))
        session.add(PlayerMail(
            to_tg_id=st.owner_tg, from_tg_id=ch.player_tg_id, from_char_id=ch.id, to_char_id=st.owner_char,
            from_name=f"{ch.state.name} {ch.state.surname}".strip(), from_year=year, kind=MAIL_STASH,
            payload={"eddies": st.eddies, "g": str(ch.state.flags.get(FLAG_GENDER, "m")),
                     "own": st.owner_tg == ch.player_tg_id},
        ))
    return out


async def week_hooks(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> list[str]:
    """Cameo and stash lines of the week; the character is saved when something happened."""
    lines = await _find_stash(session, content, ch, rng, year)
    lines += await _cameo(session, content, ch, rng, year)
    if lines:
        await save_active(session, ch, tz)
        await session.flush()
    return lines
