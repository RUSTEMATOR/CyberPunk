"""Help called into a fight (owner request 24.10). Once per fight.

NPCs: paid at the call, they arrive after their wait in turns and fight as allies.
A player: the caller sets the price, it is held at the call; the other player gets an offer (PlayerMail `offer`,
payload offer "reinf"). Accepted before the fight ends, the helper's character joins as an ally when the wait is over and
the price goes to the helper by mail. Not accepted by the end of the fight: the money comes back.
The call lives in the fight context: ctx["reinf"] = {kind, key | mail, at, price, name, done}.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db.models import Character, PlayerMail
from app.engine import combat as cb
from app.engine import jail as jail_eng
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter

OFFER = "reinf"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.combat.reinforce


def npc_price(content: Content, cs: cb.CombatState, key: str) -> int:
    n = cfg(content).npc.get(key)
    return n.price_per_danger * max(1, cs.danger) if n else 0


@dataclass
class CallResult:
    blocked: str | None = None
    text: str = ""


def _open(cs: cb.CombatState) -> str | None:
    if cs.over:
        return "over"
    if cs.ctx.get("reinf"):
        return "used"
    if cs.ctx.get("solo"):
        return "solo"
    return None


def call_npc(content: Content, ch: ActiveCharacter, cs: cb.CombatState, key: str, rng: random.Random,
             year: int) -> CallResult:
    b = _open(cs)
    if b:
        return CallResult(b)
    n = cfg(content).npc.get(key)
    if n is None or (n.requires_flag and not ch.state.flags.get(n.requires_flag)):
        return CallResult("none")
    price = npc_price(content, cs, key)
    if ch.state.eddies < price:
        return CallResult("eddies")
    apply_effects(ch.state, {"eddies": -price}, content, rng, year)
    cs.ctx["reinf"] = {"kind": "npc", "key": key, "at": cs.tick + n.wait, "price": price, "name": n.name, "done": False}
    return CallResult(text=f"{n.name} в пути: будут через {n.wait} х. Оплачено {price} эдди.")


async def helpers(session: AsyncSession, ch: ActiveCharacter) -> list[tuple[int, str]]:
    """Other players whose living character can come (not in a cell)."""
    from app.services import game

    rows = (await session.execute(select(Character).where(Character.alive.is_(True),
                                                          Character.player_tg_id != ch.player_tg_id))).scalars()
    out: list[tuple[int, str]] = []
    for r in rows:
        other = await game.get_active_character(session, r.player_tg_id)
        if other is None or other.id != r.id or jail_eng.in_jail(other.state):
            continue
        out.append((r.player_tg_id, f"{r.name} {r.surname}".strip()))
    return out


async def call_player(session: AsyncSession, content: Content, ch: ActiveCharacter, cs: cb.CombatState, tg: int,
                      price: int, rng: random.Random, year: int) -> CallResult:
    b = _open(cs)
    if b:
        return CallResult(b)
    if price not in cfg(content).player_prices:
        return CallResult("none")
    if ch.state.eddies < price:
        return CallResult("eddies")
    who = dict(await helpers(session, ch)).get(int(tg))
    if who is None:
        return CallResult("gone")
    apply_effects(ch.state, {"eddies": -price}, content, rng, year)
    m = PlayerMail(to_tg_id=int(tg), from_tg_id=ch.player_tg_id, from_char_id=ch.id, from_name=f"{ch.state.name} {ch.state.surname}".strip(),
                   from_year=year, kind="offer", payload={"offer": OFFER, "price": price})
    session.add(m)
    await session.flush()
    wait = cfg(content).player_wait
    cs.ctx["reinf"] = {"kind": "player", "mail": m.id, "to": int(tg), "at": cs.tick + wait, "price": price, "name": who,
                       "done": False}
    return CallResult(text=f"Звонок ушёл: {who}. Если ответит, будет на месте через {wait} х. {price} эдди отложено "
                           "и вернётся, если помощь не придёт.")


async def answer(session: AsyncSession, ch: ActiveCharacter, mail_id: int, accept: bool) -> str:
    """The helper's answer. Returns a line for the helper's screen."""
    m = await session.get(PlayerMail, mail_id)
    if m is None or m.to_tg_id != ch.player_tg_id or (m.payload or {}).get("offer") != OFFER or m.delivered_at is not None:
        return "Этот звонок уже неактуален."
    from app.services.players import _utcnow  # noqa: PLC0415

    m.delivered_at = _utcnow()
    if not accept:
        m.payload = {**(m.payload or {}), "declined": True}
        await session.flush()
        return "Ты сбрасываешь звонок."
    m.payload = {**(m.payload or {}), "accepted": True, "helper_char": ch.id}
    await session.flush()
    return (f"Ты едешь на помощь к {m.from_name}. Если бой ещё идёт, появишься в нём, когда доедешь. "
            f"Оплата {int((m.payload or {}).get('price', 0))} эдди придёт письмом.")


async def tick(session: AsyncSession, content: Content, ch: ActiveCharacter, cs: cb.CombatState, rng: random.Random,
               year: int) -> None:
    """Called after every turn of the caller: the help arrives when its turn comes."""
    r = cs.ctx.get("reinf")
    if not r or r.get("done") or cs.over or cs.tick < int(r.get("at", 0)):
        return
    if r["kind"] == "npc":
        n = cfg(content).npc.get(str(r.get("key")))
        if n is None:
            r["done"] = True
            return
        for _ in range(n.count):
            idx = len(cs.allies)
            cs.allies.append(cb.build_ally(content, year, min(5, cs.danger + n.danger_add), idx, n.name, rng))
        r["done"] = True
        cb._emit(cs, k="reinf", name=n.name, n=n.count)  # noqa: SLF001
        return
    m = await session.get(PlayerMail, int(r.get("mail", 0)))
    p = (m.payload or {}) if m is not None else {}
    if not p.get("accepted"):
        return
    from app.services import game

    helper = await game.load_character(session, int(p.get("helper_char", 0)))
    if helper is None:
        r["done"] = True
        return
    c = cb.build_player(helper.state, content)
    idx = len(cs.allies)
    c.id, c.side, c.ai, c.name = f"a{idx}", "ally", "", f"{helper.state.name} {helper.state.surname}".strip()
    cs.allies.append(c)
    session.add(PlayerMail(to_tg_id=helper.player_tg_id, from_tg_id=ch.player_tg_id, from_name=m.from_name, kind="city",
                           payload={"text": f"Оплата за помощь в бою от {m.from_name}.", "eddies": int(r.get("price", 0))}))
    r["done"] = True
    cb._emit(cs, k="reinf", name=c.name, n=1)  # noqa: SLF001


def refund_if_missed(content: Content, ch: ActiveCharacter, cs: cb.CombatState, rng: random.Random, year: int) -> str:
    """At the end of the fight: a player who never came costs nothing."""
    r = cs.ctx.get("reinf")
    if not r or r.get("done") or r.get("kind") != "player":
        return ""
    r["done"] = True
    price = int(r.get("price", 0))
    if price:
        apply_effects(ch.state, {"eddies": price}, content, rng, year)
    return f"{r.get('name', 'Помощь')}: помощь так и не пришла. {price} эдди вернулись."
