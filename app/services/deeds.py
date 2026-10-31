"""The city remembers the players (owner request 26.10 «действия юзеров отражались на мире», docs/PLAN_LIVING.md
stage 1).

world_state `deeds` = [{"id", "at" (iso date), "char", "tg", "name", "kind", "text", "district", "w"}], the last `keep`.
- Every loud post is a deed (services/loud.post); the big ones are written with their kind (corp_fall, dyn_gone,
  turf, avatar, borg, legend, pmc_win, escort, lair, ending, quest).
- What it changes: the NPCs in a talk know what the city says (npc chat card `city`, `known_for`); every lived month
  the character hears the street talk of the district and the city, and the own deeds come back as recognition with
  a small effect by kind (balance.deeds.echo); the city director reads the journal.
"""

from __future__ import annotations

import random
import re
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.content.loader import Content
from app.db import repo

KEY = "deeds"
_TAG = re.compile(r"<[^>]+>")


def cfg(content: Content):  # noqa: ANN201
    return content.balance.deeds


def plain(text: str) -> str:
    t = _TAG.sub("", text).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"^[^\w«\"(]+", "", t.strip())  # the leading icons go: the line is read inside other lines


async def all_deeds(session: AsyncSession) -> list[dict]:
    got = await repo.ws_get(session, KEY, [])
    return [dict(x) for x in got] if isinstance(got, list) else []


async def add(session: AsyncSession, character_id: int | None, kind: str, text: str, *, district: str = "",
              w: int = 1, keep: int = 80) -> None:
    """Write a deed. The character's name, player and district are read from the character when not given."""
    name, tg = "", 0
    if character_id:
        try:
            st = await repo.load_character(session, int(character_id))
            from app.db.models import Character

            row = await session.get(Character, int(character_id))
            tg = int(getattr(row, "player_tg_id", 0) or 0)
            if st is not None:
                name = f"{st.name} {st.surname}".strip()
                district = district or str(st.district or "")
        except Exception:  # noqa: BLE001 - the journal never breaks the deed itself
            pass
    items = await all_deeds(session)
    nid = max((int(x.get("id", 0)) for x in items), default=0) + 1
    items.append({"id": nid, "at": utcnow().date().isoformat(), "char": int(character_id or 0), "tg": tg,
                  "name": name, "kind": kind, "text": plain(text)[:300], "district": district, "w": int(w)})
    await repo.ws_set(session, KEY, items[-keep:])


def _fresh(items: list[dict], days: int) -> list[dict]:
    since = (utcnow().date() - timedelta(days=days)).isoformat()
    return [x for x in items if str(x.get("at", "")) >= since]


async def city_talk(session: AsyncSession, content: Content, district: str, n: int = 5) -> list[str]:
    """What the city says lately: the district first, then the biggest of the city."""
    items = _fresh(await all_deeds(session), cfg(content).talk_days)
    here = [x for x in items if x.get("district") == district]
    rest = sorted((x for x in items if x not in here), key=lambda x: (-int(x.get("w", 1)), x.get("at", "")))
    out = list(reversed(here))[:n] + rest[: max(0, n - len(here))]
    return [str(x["text"]) for x in out[:n]]


async def known_for(session: AsyncSession, char_id: int, n: int = 4) -> list[str]:
    items = [x for x in await all_deeds(session) if int(x.get("char", 0)) == int(char_id)]
    return [str(x["text"]) for x in items[-n:]]


async def press_hook(session: AsyncSession, content: Content, ch, rng: random.Random,  # noqa: ANN001
                     year: int) -> list[str]:
    """Every lived month: the street talk and the echo of the own deeds."""
    from app.engine.effects import apply_effects

    c = cfg(content)
    st = ch.state
    items = _fresh(await all_deeds(session), c.talk_days)
    lines: list[str] = []
    others = [x for x in items if int(x.get("char", 0)) != ch.id]
    here = [x for x in others if x.get("district") == st.district]
    pool = here or [x for x in others if int(x.get("w", 1)) >= 2]
    if pool and rng.random() < c.talk_chance:
        d = rng.choice(pool)
        from app.engine import grammar

        lines.append(f"🗣 {grammar.expand(content.balance.grammar, rng.choice(c.talk_heads), rng)}: {d['text']}")
    mine = [x for x in items if int(x.get("char", 0)) == ch.id and x.get("kind") in c.echo]
    seen = set(st.flags.get("deeds_echoed") or [])
    fresh = [x for x in mine if int(x["id"]) not in seen]
    if fresh:
        d = fresh[-1]
        e = c.echo[str(d["kind"])]
        if e.effects:
            apply_effects(st, dict(e.effects), content, rng, year)
        lines.append(f"👁 {rng.choice(e.lines)}")
        st.flags["deeds_echoed"] = sorted(seen | {int(x["id"]) for x in fresh})[-30:]
    return lines
