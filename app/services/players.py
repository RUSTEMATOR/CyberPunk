"""Interaction between players as a service (docs/EPIC_PLAYERS.md, phase 1).

The sender's side is applied at once to the sender's own character. The recipient's side becomes a letter in
`player_mail`; `deliver` applies it in the recipient's own session when the recipient opens the menu. A letter
goes to the PLAYER: if the character it was meant for died, the heir gets it.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import Bond, Character, Player, PlayerMail
from app.engine import players as pl
from app.engine.character_gen import FLAG_GENDER
from app.engine import activities as activities_engine
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services import game
from app.services import game_events as bus
from app.services import playerdata
from app.services.game import ActiveCharacter, save_active

KEY_SENT = "pl_sent"  # playerdata: {"date": Kyiv date, "eddies": sent today, "notes": notes today}

BUS_NAME = {pl.TOGETHER: "player_together", pl.GIFT: "player_gift", pl.DRINK: "player_drink", pl.VOUCH: "player_vouch", pl.NOTE: "player_note",
            pl.HOOKUP: "player_hookup", pl.DATE: "player_date", pl.DUEL: "player_duel", pl.CHILD: "family_time"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today(tz: str) -> str:
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def gender(ch: ActiveCharacter) -> str:
    return str(ch.state.flags.get(FLAG_GENDER, "m"))


def char_name(c: Character) -> str:
    return " ".join(x for x in (c.name, c.surname) if x)


@dataclass
class Other:
    """A living character of another player, as the list and the card show it."""

    char_id: int
    tg_id: int
    name: str
    year: int
    district: str
    role_affinity: dict[str, float]
    bond: int
    bond_label: str
    used_today: set[str] = field(default_factory=set)  # pair actions already done today
    username: str | None = None  # the Telegram @username of the player


@dataclass
class ActResult:
    action: str
    blocked: str | None = None
    no_ap: bool = False
    target: Other | None = None
    amount: int = 0
    net: int = 0
    applied: AppliedEffects | None = None
    bond: int = 0
    bond_gain: int = 0

    @property
    def done(self) -> bool:
        return self.blocked is None and not self.no_ap


# -- bonds -------------------------------------------------------------------------------------------------------------


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


async def _bond_row(session: AsyncSession, a: int, b: int, create: bool = False) -> Bond | None:
    key = _pair(a, b)
    row = await session.get(Bond, key)
    if row is None and create:
        row = Bond(a_char_id=key[0], b_char_id=key[1], value=0, data={})
        session.add(row)
    return row


async def _crew_gain(session: AsyncSession, content: Content, a_tg: int, b_tg: int, gain: int) -> int:
    """Crewmates bond `crew.bond_mult` times faster."""
    from app.services import crews

    return gain * content.balance.players.crew.bond_mult if await crews.same_crew(session, a_tg, b_tg) else gain


def _bond_value(content: Content, row: Bond | None) -> int:
    if row is None:
        return 0
    return pl.bond_now(content, row.value, row.last_at, _utcnow())


# -- the list and one card ---------------------------------------------------------------------------------------------


# Owner request 30.10 (docs/PLAN_WORLD2.md): what needs both characters in one place. Notes, money, vouching, quests and the
# crew go over any border; a drink, time together, a date, a night, a duel, a joint job, a child and an attack do not.
BLOCK_AWAY = "away"
PHYSICAL_ACTS = frozenset({pl.DRINK, pl.TOGETHER})
PHYSICAL_OFFERS = frozenset({pl.HOOKUP, pl.DATE, pl.DUEL, pl.JOB, pl.CHILD})


def away(content: Content, me: ActiveCharacter, district: str) -> str | None:
    """The other one's place when it is not mine, else None."""
    from app.engine import place

    theirs = place.of_district(content, district)
    return theirs if theirs != place.place_of(me.state, content) else None


async def _other(session: AsyncSession, content: Content, me: ActiveCharacter, c: Character, tz: str) -> Other:
    row = await _bond_row(session, me.id, c.id)
    value = _bond_value(content, row)
    today = _today(tz)
    used = {k for k, d in (row.data or {}).items() if d == today} if row is not None else set()
    player = await session.get(Player, c.player_tg_id)
    return Other(
        username=player.username if player is not None else None,
        char_id=c.id, tg_id=c.player_tg_id, name=char_name(c),
        year=await game.current_year(session, content, c.player_tg_id),
        district=c.district, role_affinity=dict(c.role_affinity or {}), bond=value,
        bond_label=pl.bond_label(content, value), used_today=used,
    )


async def others(session: AsyncSession, content: Content, me: ActiveCharacter, tz: str) -> list[Other]:
    """Living characters of the other players, the strongest bond first, then by name."""
    out = [
        await _other(session, content, me, c, tz)
        for c in await repo.living_characters(session)
        if c.player_tg_id != me.player_tg_id and not (c.flags or {}).get("ai_mode")  # an AI is not one of «Свои»
    ]
    out.sort(key=lambda o: (-o.bond, o.name))
    return out


async def other(session: AsyncSession, content: Content, me: ActiveCharacter, char_id: int, tz: str) -> Other | None:
    c = await session.get(Character, char_id)
    if c is None or not c.alive or c.player_tg_id == me.player_tg_id:
        return None
    return await _other(session, content, me, c, tz)


async def sent_today(session: AsyncSession, tg_id: int, tz: str) -> dict[str, int]:
    rec = await playerdata.get(session, tg_id, KEY_SENT, {})
    if rec.get("date") != _today(tz):
        return {"eddies": 0, "notes": 0}
    return {"eddies": int(rec.get("eddies", 0)), "notes": int(rec.get("notes", 0))}


# -- one action --------------------------------------------------------------------------------------------------------


async def act(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    char_id: int, action: str, *, amount: int = 0, text: str = "", kind: str = "", skill: str | None = None,
) -> ActResult:
    res = ActResult(action, amount=amount)
    target = await other(session, content, ch, char_id, tz)
    if target is None:
        res.blocked = pl.BLOCK_GONE
        return res
    res.target = target
    if action in PHYSICAL_ACTS and away(content, ch, target.district):
        res.blocked = BLOCK_AWAY
        return res
    sent = await sent_today(session, ch.player_tg_id, tz)
    from app.services import crews

    crew = action == pl.GIFT and await crews.same_crew(session, ch.player_tg_id, target.tg_id)
    res.blocked = pl.blocked(
        ch.state, content, action, amount=amount, sent_today=sent["eddies"],
        pair_used_today=action in target.used_today, notes_today=sent["notes"], text=text, kind=kind, skill=skill,
        crew=crew,
    )
    if res.blocked is not None:
        return res
    p = pl.plan(content, action, amount=amount, text=text, kind=kind, skill=skill, crew=crew)
    if not ap_services.can_spend(ch, p.ap):
        res.no_ap = True
        return res
    if p.ap:
        ap_services.spend(ch, content, p.ap)
    res.applied = apply_effects(ch.state, dict(p.effects), content, rng, year)
    res.net = int(p.payload.get("eddies", 0) or 0)
    await save_active(session, ch, tz)

    session.add(PlayerMail(
        to_tg_id=target.tg_id, from_tg_id=ch.player_tg_id, from_char_id=ch.id, to_char_id=target.char_id,
        from_name=f"{ch.state.name} {ch.state.surname}".strip(), from_year=year, kind=action,
        payload={**p.payload, "g": gender(ch)},
    ))
    row = await _bond_row(session, ch.id, target.char_id, create=True)
    assert row is not None
    now = _utcnow()
    gain = await _crew_gain(session, content, ch.player_tg_id, target.tg_id, p.bond)
    value = min(content.balance.players.bond_max, _bond_value(content, row) + gain)
    row.value, row.last_at = value, now
    if action in pl.DAILY_PAIR:
        row.data = {**(row.data or {}), action: _today(tz)}
    res.bond, res.bond_gain = value, gain

    if action in (pl.GIFT, pl.NOTE):
        rec = {"date": _today(tz), **sent}
        if action == pl.GIFT:
            rec["eddies"] = sent["eddies"] + (0 if crew else amount)
        else:
            rec["notes"] = sent["notes"] + 1
        await playerdata.put(session, ch.player_tg_id, KEY_SENT, rec)
    await session.flush()
    await repo.log_event(session, "player_" + action,
                         {"to": target.char_id, "amount": amount, "bond": value}, character_id=ch.id)
    await bus.publish(BUS_NAME[action], player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch)
    if action in (pl.DRINK, pl.TOGETHER):
        await bus.publish("social_time", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch)
    return res


# -- delivery ----------------------------------------------------------------------------------------------------------


@dataclass
class Delivered:
    kind: str
    from_name: str
    from_year: int
    payload: dict
    heir: bool  # meant for an earlier character of this player
    fixer: str | None = None  # vouch: the fixer who heard the word
    applied: AppliedEffects | None = None


async def pending_count(session: AsyncSession, tg_id: int) -> int:
    """Offers waiting for this player's answer."""
    res = await session.execute(select(PlayerMail.id).where(
        PlayerMail.to_tg_id == tg_id, PlayerMail.delivered_at.is_(None), PlayerMail.kind == pl.OFFER))
    return len(res.all())


async def deliver(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> list[Delivered]:
    """Apply the waiting letters to the player's living character (at most `mail_per_open`), oldest first."""
    cfg = content.balance.players
    await expire_quests(session, content, ch.player_tg_id, year)
    res = await session.execute(
        select(PlayerMail).where(PlayerMail.to_tg_id == ch.player_tg_id, PlayerMail.delivered_at.is_(None),
                                 PlayerMail.kind != pl.OFFER)
        .order_by(PlayerMail.id).limit(cfg.mail_per_open)
    )
    letters = list(res.scalars())
    out: list[Delivered] = []
    if not letters:
        return out
    now = _utcnow()
    for m in letters:
        d = Delivered(m.kind, m.from_name, m.from_year, dict(m.payload or {}),
                      heir=m.to_char_id is not None and m.to_char_id != ch.id)
        fx: dict[str, int | bool] = {}
        if m.kind == pl.GIFT:
            fx["eddies"] = int(d.payload.get("eddies", 0))
        elif m.kind == pl.DRINK:
            fx["mood"] = int(d.payload.get("mood", 0))
        elif m.kind == pl.VOUCH:
            fixer = game.district_fixer(content, ch.state.district, year)
            if fixer is not None:
                d.fixer = fixer.name
                fx[f"rel_fixer:{fixer.key}"] = int(d.payload.get("fixer_rel", 0))
        elif m.kind == pl.HIRE:
            fx["eddies"] = int(d.payload.get("eddies", 0) or 0)
            skill = d.payload.get("skill")
            if skill:
                fx[f"skill_xp:{skill}"] = int(d.payload.get("xp", 0) or 0)
            fx = {k: v for k, v in fx.items() if v}
        elif m.kind == pl.TOGETHER:
            fx["mood"] = int(d.payload.get("mood", 0) or 0)
            if d.payload.get("skill"):
                fx[f"skill_xp:{d.payload['skill']}"] = int(d.payload.get("xp", 0) or 0)
            fx = {k: v for k, v in fx.items() if v}
        elif m.kind == "raid":
            fx["eddies"] = int(d.payload.get("eddies", 0) or 0)
            fx["street_cred"] = int(d.payload.get("cred", 0) or 0)
            if d.payload.get("item"):
                fx[f"item_grant:{d.payload['item']}"] = True
            fx = {k: v for k, v in fx.items() if v}
        elif m.kind == "dialog":  # owner request 26.10: the consequences of a talk with an NPC (services/dialogs.py)
            fx.update({k: int(v) for k, v in (d.payload.get("fx") or {}).items() if int(v)})
            npc = next((n for n in ch.state.npcs if n.name == d.payload.get("npc") and n.alive), None)
            if npc is not None:
                rd = int(d.payload.get("relation", 0) or 0)
                if rd:
                    npc.relation = max(0, min(100, npc.relation + rd))
                if d.payload.get("memory"):
                    npc.data["memory"] = (list(npc.data.get("memory") or []) + [f"Итог: {d.payload['memory']}"])[-8:]
            ev = str(d.payload.get("event") or "")
            if ev and ev in content.events:
                from app.services.week import pending_events, set_pending

                q = pending_events(ch)
                if ev not in q:
                    set_pending(ch, q + [ev])
        elif m.kind == "btears":  # owner request 26.10: a consequence of a contract of «Чёрные слёзы»
            fx.update({str(k): int(v) for k, v in (d.payload.get("fx") or {}).items() if int(v)})
        elif m.kind == "ai_hit":
            lose = min(int(d.payload.get("steal", 0) or 0), max(0, ch.state.eddies))
            d.payload["lost"] = lose
            fx.update({k: v for k, v in {"eddies": -lose, "humanity": -int(d.payload.get("humanity", 0) or 0),
                                         "glitches": 1}.items() if v})
        elif m.kind == "ai_glitch":
            fx.update({"heat": int(d.payload.get("heat", 0) or 0), "mood": int(d.payload.get("mood", 0) or 0)})
        elif m.kind == pl.ATTACKED:
            fx.update(await _attacked_effects(session, content, ch, m.from_tg_id, d.payload))
        elif m.kind == "msold":
            fx["eddies"] = int(d.payload.get("eddies", 0) or 0)
        elif m.kind == "crewpay":
            fx["eddies"] = int(d.payload.get("eddies", 0) or 0)
        elif m.kind == "city":  # a reward of the city: the shared goal, the leaderboard, a bounty
            fx.update({k: int(d.payload.get(src, 0) or 0) for k, src in
                       (("eddies", "eddies"), ("street_cred", "cred"), ("fame", "fame"), ("heat", "heat"), ("mood", "mood"))})
            pct = int(d.payload.get("pct", 0) or 0)  # owner request (the purge): a share of the money on hand now
            if pct:
                fx["eddies"] = -(max(0, ch.state.eddies) * pct // 100)
            hp = int(d.payload.get("hp", 0) or 0)  # a wound by mail never kills: at least 1 hp stays
            if hp < 0:
                fx["hp"] = -min(-hp, max(0, ch.state.hp - 1))
            elif hp > 0:
                fx["hp"] = hp
            if d.payload.get("path") and d.payload.get("pxp"):  # owner request 24.10: a service paid to a player
                fx[f"path_xp:{d.payload['path']}"] = int(d.payload["pxp"])
            fx = {k: v for k, v in fx.items() if v}
            grant = d.payload.get("grant")  # owner request: an auction lot (item_grant / cyberware_grant / vehicle_grant)
            if isinstance(grant, str) and grant.split(":", 1)[0] in ("item_grant", "cyberware_grant", "vehicle_grant"):
                fx[grant] = True
            if d.payload.get("seat_lost"):  # owner request: another player took the mayor's seat
                from app.engine import paths as paths_engine

                paths_engine.set_seat(ch.state, False)
            tw = int(d.payload.get("chrono", 0) or 0)  # owner request (Aeon): a gift of time
            if tw > 0:
                from app.engine import chrono as chrono_engine

                if chrono_engine.has(ch.state):
                    chrono_engine.add(ch.state, tw)
                else:  # no clock: the time is cashed at the exchange
                    fx["eddies"] = int(fx.get("eddies", 0)) + tw * content.balance.chrono.sell_week
            if d.payload.get("empire_to") is not None:  # owner request: the megacorp was razed
                ch.state.flags["empire"] = min(int(ch.state.flags.get("empire", 0) or 0), int(d.payload["empire_to"]))
        elif m.kind == "proxy":  # owner request 29.10: sent to the employer's gig in his place (services/proxy.py)
            fx.update({k: int(v) for k, v in (d.payload.get("fx") or {}).items() if int(v)})
            if int(fx.get("hp", 0)) < 0:  # a wound by letter never kills: at least 1 health stays
                fx["hp"] = -min(-int(fx["hp"]), max(0, ch.state.hp - 1))
            fx["eddies"] = int(fx.get("eddies", 0)) + int(d.payload.get("eddies", 0) or 0)
            if d.payload.get("crime"):
                from app.engine.state import FLAG_CRIME_WEEK

                ch.state.flags[FLAG_CRIME_WEEK] = True
            fx = {k: v for k, v in fx.items() if v}
        elif m.kind == pl.Q_PAY:
            fx["eddies"] = int(d.payload.get("eddies", 0) or 0)
        elif m.kind == pl.REPLY and d.payload.get("accepted"):
            fx.update(_reply_effects(ch, content, d.payload))
            if d.payload.get("offer") == pl.CHILD and d.payload.get("child") and not d.heir:  # the same child here
                from app.engine.state import NpcState

                ch.state.npcs.append(NpcState(
                    name=str(d.payload["child"]), kind="child", role=None,
                    relation=content.balance.relations.start_rel.get("child", 80), alive=True,
                    data={"gender": str(d.payload.get("gender", "m")), "district": ch.state.district,
                          "born_week": ch.state.weeks_lived, "co_parent": m.from_name, "co_tg": m.from_tg_id}))
        elif m.kind == pl.REPLY and d.payload.get("refund"):
            fx["eddies"] = int(d.payload["refund"])
        if fx:
            d.applied = apply_effects(ch.state, fx, content, rng, year)
        m.delivered_at = now
        out.append(d)
    await save_active(session, ch, tz)
    return out


# -- offers ------------------------------------------------------------------------------------------------------------


@dataclass
class OfferResult:
    offer: str
    blocked: str | None = None
    target: Other | None = None
    stake: int = 0


async def _pending_offer(session: AsyncSession, from_tg: int, to_tg: int, offer: str) -> PlayerMail | None:
    res = await session.execute(select(PlayerMail).where(
        PlayerMail.from_tg_id == from_tg, PlayerMail.to_tg_id == to_tg, PlayerMail.kind == pl.OFFER,
        PlayerMail.delivered_at.is_(None)))
    for m in res.scalars():
        if (m.payload or {}).get("offer") == offer:
            return m
    return None


async def make_offer(
    session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, char_id: int, offer: str,
    *, stake: int = 0, goal: str = "", rng: random.Random | None = None, approach: str = "",
) -> OfferResult:
    """Send an offer. Nothing changes until the other player accepts; the stake is checked again then."""
    res = OfferResult(offer, stake=stake)
    target = await other(session, content, ch, char_id, tz)
    if target is None:
        res.blocked = pl.BLOCK_GONE
        return res
    res.target = target
    if offer in PHYSICAL_OFFERS and away(content, ch, target.district):
        res.blocked = BLOCK_AWAY
        return res
    pending = await _pending_offer(session, ch.player_tg_id, target.tg_id, offer) is not None
    res.blocked = pl.offer_blocked(ch.state, content, offer, stake=stake, pending=pending, goal=goal)
    if res.blocked is not None:
        return res
    if offer == pl.CHILD and target.bond < content.balance.players.child.bond_need:  # owner request 25.10
        res.blocked = "bond_low"
        return res
    payload: dict[str, object] = {"offer": offer, "stake": stake, "g": gender(ch)}
    if offer == pl.JOB:
        if approach not in pl.JOB_APPROACHES:
            res.blocked = pl.BLOCK_GONE
            return res
        payload.update({"job": goal, "approach": approach})
    if offer == pl.CREW:
        from app.services import crews

        crew = await crews.crew_of(session, ch.player_tg_id)
        if crew is None:
            res.blocked = pl.BLOCK_GONE
            return res
        if await crews.crew_of(session, target.tg_id) is not None:
            res.blocked = "in_crew"
            return res
        payload.update({"crew": crew.id, "crew_name": crew.name})
    if offer == pl.QUEST:  # the reward is held from the giver now; it comes back on a no, an expiry or a failure
        payload["goal"] = goal
        apply_effects(ch.state, {"eddies": -stake}, content, rng or random.Random(), year)
        await save_active(session, ch, tz)
    session.add(PlayerMail(
        to_tg_id=target.tg_id, from_tg_id=ch.player_tg_id, from_char_id=ch.id, to_char_id=target.char_id,
        from_name=f"{ch.state.name} {ch.state.surname}".strip(), from_year=year, kind=pl.OFFER, payload=payload,
    ))
    await session.flush()
    return res


async def offers_for(session: AsyncSession, content: Content, tg_id: int, year: int) -> list[PlayerMail]:
    """Offers waiting for this player's answer; expired ones are closed (the sender gets a 'no answer' reply)."""
    res = await session.execute(select(PlayerMail).where(
        PlayerMail.to_tg_id == tg_id, PlayerMail.kind == pl.OFFER, PlayerMail.delivered_at.is_(None),
    ).order_by(PlayerMail.id))
    out: list[PlayerMail] = []
    now = _utcnow()
    ttl = timedelta(hours=content.balance.players.offer_ttl_hours)
    for m in res.scalars():
        if m.created_at is not None and now - m.created_at > ttl:
            m.delivered_at = now
            p = m.payload or {}
            _reply(session, m, tg_id, "", year, {"offer": p.get("offer"), "accepted": False, "expired": True,
                                                 "goal": p.get("goal"), "refund": _refund_of(p)})
            continue
        out.append(m)
    await session.flush()
    return out


def _refund_of(p: dict) -> int:
    return int(p.get("stake", 0) or 0) if p.get("offer") == pl.QUEST else 0


def _reply(session: AsyncSession, m: PlayerMail, my_tg: int, my_name: str, year: int, payload: dict,
           my_char_id: int | None = None) -> None:
    session.add(PlayerMail(
        to_tg_id=m.from_tg_id, from_tg_id=my_tg, from_char_id=my_char_id, to_char_id=m.from_char_id,
        from_name=my_name, from_year=year, kind=pl.REPLY, payload=payload,
    ))


@dataclass
class AnswerResult:
    offer: str
    accepted: bool
    blocked: str | None = None
    from_name: str = ""
    from_tg: int = 0
    stake: int = 0
    roll: pl.DuelRoll | None = None
    applied: AppliedEffects | None = None
    bond: int = 0
    goal: str = ""  # a quest: its goal key


def _vow_breaks(st, content: Content) -> dict[str, int | bool]:  # noqa: ANN001
    return {f"vow_break:{v}": True for v in activities_engine.vows_against(st, content, "violence")}


async def answer(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    mail_id: int, accept: bool,
) -> AnswerResult | None:
    """Accept or decline an offer. The answering side changes now; the offering side gets a reply letter."""
    m = await session.get(PlayerMail, mail_id)
    if m is None or m.to_tg_id != ch.player_tg_id or m.kind != pl.OFFER or m.delivered_at is not None:
        return None
    offer = str((m.payload or {}).get("offer"))
    stake = int((m.payload or {}).get("stake", 0) or 0)
    res = AnswerResult(offer, accept, from_name=m.from_name, from_tg=m.from_tg_id, stake=stake)
    my_name = f"{ch.state.name} {ch.state.surname}".strip()
    if not accept:
        m.delivered_at = _utcnow()
        _reply(session, m, ch.player_tg_id, my_name, year,
               {"offer": offer, "accepted": False, "g": gender(ch), "goal": (m.payload or {}).get("goal"),
                "refund": _refund_of(m.payload or {})}, ch.id)
        await session.flush()
        return res
    if offer in PHYSICAL_OFFERS and m.from_char_id:  # one of the two may have moved since the offer
        sender = await session.get(Character, m.from_char_id)
        if sender is not None and away(content, ch, sender.district):
            res.blocked = BLOCK_AWAY
            return res
    cfg = content.balance.players
    fx: dict[str, int | bool] = {}
    reply: dict[str, object] = {"offer": offer, "accepted": True, "g": gender(ch)}
    if offer == pl.CREW:
        from app.services import crews

        joined = await crews.join(session, content, ch.player_tg_id, int((m.payload or {}).get("crew", 0)), my_name)
        if joined.blocked is not None:
            res.blocked = joined.blocked
            if joined.blocked == crews.BLOCK_GONE:
                m.delivered_at = _utcnow()
                await session.flush()
            return res
        m.delivered_at = _utcnow()
        _reply(session, m, ch.player_tg_id, my_name, year,
               {"offer": offer, "accepted": True, "g": gender(ch), "crew_name": joined.crew.name if joined.crew else ""},
               ch.id)
        await session.flush()
        return res
    if offer == pl.QUEST:
        active = await active_quests(session, ch.player_tg_id)
        if len(active) >= cfg.quest.max_active:
            res.blocked = pl.BLOCK_QUESTS
            return res
        goal = str((m.payload or {}).get("goal", ""))
        res.goal = goal
        until = (_utcnow() + timedelta(days=cfg.quest.days)).isoformat(timespec="minutes")
        active.append({"id": m.id, "from_tg": m.from_tg_id, "from_char": m.from_char_id, "from_name": m.from_name,
                       "g": (m.payload or {}).get("g", "m"), "goal": goal, "reward": stake, "until": until})
        await playerdata.put(session, ch.player_tg_id, KEY_QUESTS, active)
        reply.update({"goal": goal})
        bond_gain = 0
    elif offer == pl.DUEL:
        if ch.state.eddies < stake:
            res.blocked = pl.BLOCK_EDDIES
            return res
        row_them = await session.get(Character, m.from_char_id) if m.from_char_id else None
        them = await game.load_character(session, m.from_char_id) if row_them is not None and row_them.alive else None
        if them is None:
            m.delivered_at = _utcnow()
            res.blocked = pl.BLOCK_GONE
            await session.flush()
            return res
        r = pl.duel(ch.state, them.state, content, rng)
        res.roll = r
        if r.won:
            fx.update({"eddies": stake, "street_cred": cfg.duel.winner_cred})
        else:
            fx.update({"eddies": -stake, "hp": -r.loser_hp})
        fx.update(_vow_breaks(ch.state, content))
        reply.update({"stake": stake, "they_won": r.won, "hp": r.loser_hp, "cred": cfg.duel.winner_cred,
                      "mine": r.theirs, "theirs": r.mine})
        bond_gain = cfg.duel.bond
    elif offer == pl.CHILD:  # owner request 25.10: one child, two parents
        from app.engine import relations as rel

        why = rel.baby_blocked(ch.state, content, cfg.child.cost)
        if why is not None:
            res.blocked = why
            return res
        fx.update({"add_npc:child": True, "eddies": -cfg.child.cost, "mood": cfg.child.mood})
        reply["mood"] = cfg.child.mood
        bond_gain = cfg.child.bond
    else:
        o = cfg.hookup if offer == pl.HOOKUP else cfg.date
        fx["mood"] = o.mood
        reply["mood"] = o.mood
        bond_gain = o.bond
    fx = {k: v for k, v in fx.items() if v}
    res.applied = apply_effects(ch.state, fx, content, rng, year)
    if offer == pl.CHILD and res.applied.npcs_added:
        kid = res.applied.npcs_added[-1]
        kid.data.update({"co_parent": m.from_name, "co_tg": m.from_tg_id})
        reply.update({"child": kid.name, "gender": kid.data.get("gender", "m")})
        res.goal = kid.name
    m.delivered_at = _utcnow()
    _reply(session, m, ch.player_tg_id, my_name, year, reply, ch.id)
    if m.from_char_id:
        row = await _bond_row(session, ch.id, m.from_char_id, create=True)
        assert row is not None
        bond_gain = await _crew_gain(session, content, ch.player_tg_id, m.from_tg_id, bond_gain)
        row.value = min(cfg.bond_max, _bond_value(content, row) + bond_gain)
        row.last_at = _utcnow()
        res.bond = row.value
    await save_active(session, ch, tz)
    await session.flush()
    params = {"won": bool(res.roll.won)} if res.roll is not None else {}
    if offer == pl.QUEST:
        return res
    await bus.publish(BUS_NAME[offer], player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch,
                      **params)
    return res


def _reply_effects(ch: ActiveCharacter, content: Content, p: dict) -> dict[str, int | bool]:
    """What an accepted offer does to the one who offered, when the reply arrives."""
    offer = p.get("offer")
    if offer == "contract":  # owner request: the fixer's share of a contract
        from app.services import paths as paths_svc

        return paths_svc.reply_effects(p)
    if offer == pl.JOB:
        hp = min(int(p.get("hp", 0) or 0), max(0, ch.state.hp - 1))
        return pl.job_effects(content, str(p.get("job", "")), str(p.get("outcome", pl.FAIL)), hp)
    if offer in (pl.QUEST, pl.CREW, "corp"):
        return {}
    if offer == pl.CHILD:  # owner request 25.10: the other parent's half of the cost
        return {"eddies": -content.balance.players.child.cost, "mood": int(p.get("mood", 0) or 0)}
    if offer == pl.DUEL:
        stake = int(p.get("stake", 0) or 0)
        fx: dict[str, int | bool] = _vow_breaks(ch.state, content)
        if p.get("they_won"):  # the answering side won: this one lost
            fx["eddies"] = -stake
            fx["hp"] = -min(int(p.get("hp", 0) or 0), max(0, ch.state.hp - 1))
        else:
            fx["eddies"] = stake
            fx["street_cred"] = int(p.get("cred", 0) or 0)
        return {k: v for k, v in fx.items() if v}
    return {"mood": int(p.get("mood", 0) or 0)}


# -- hire: the merc exchange -------------------------------------------------------------------------------------------

KEY_MERC = "merc"  # playerdata: {"on": bool, "share": int}


async def merc_get(session: AsyncSession, content: Content, tg_id: int) -> dict:
    rec = await playerdata.get(session, tg_id, KEY_MERC, {})
    shares = content.balance.players.hire.shares
    share = int(rec.get("share", shares[1] if len(shares) > 1 else shares[0]))
    return {"on": bool(rec.get("on")), "share": share if share in shares else shares[0]}


async def merc_set(session: AsyncSession, content: Content, tg_id: int, *, on: bool | None = None,
                   share: int | None = None) -> dict:
    rec = await merc_get(session, content, tg_id)
    if on is not None:
        rec["on"] = on
    if share is not None and share in content.balance.players.hire.shares:
        rec["share"] = share
    await playerdata.put(session, tg_id, KEY_MERC, rec)
    return rec


@dataclass
class Merc:
    other: Other
    share: int
    best: list[str]  # the strongest skills, for the list


async def mercs(session: AsyncSession, content: Content, me: ActiveCharacter, tz: str) -> list[Merc]:
    """Characters of other players who stand on the exchange."""
    out: list[Merc] = []
    for o in await others(session, content, me, tz):
        rec = await merc_get(session, content, o.tg_id)
        if not rec["on"]:
            continue
        snap = await merc_snapshot(session, o.char_id)
        best: list[str] = []
        if snap is not None:
            skills = sorted(((s.level, k) for k, s in snap.state.skills.items() if s.level > 0), reverse=True)
            best = [k for _, k in skills[:2]]
        out.append(Merc(o, rec["share"], best))
    return out


async def merc_snapshot(session: AsyncSession, char_id: int) -> ActiveCharacter | None:
    """The merc as he is now (read only: nobody saves it)."""
    row = await session.get(Character, char_id)
    if row is None or not row.alive:
        return None
    return await game.load_character(session, char_id)


async def send_hire_mail(
    session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, backup: dict, *, cut: int,
    outcome: str, title: str, skill: str | None,
) -> None:
    """The merc's share and xp go out by mail; the bond grows."""
    cfg = content.balance.players.hire
    session.add(PlayerMail(
        to_tg_id=int(backup["tg"]), from_tg_id=ch.player_tg_id, from_char_id=ch.id, to_char_id=int(backup["char"]),
        from_name=f"{ch.state.name} {ch.state.surname}".strip(), from_year=year, kind=pl.HIRE,
        payload={"eddies": cut, "skill": skill, "xp": cfg.helper_xp if skill else 0, "outcome": outcome,
                 "title": title, "g": gender(ch)},
    ))
    row = await _bond_row(session, ch.id, int(backup["char"]), create=True)
    assert row is not None
    row.value = min(content.balance.players.bond_max, _bond_value(content, row) + cfg.bond)
    row.last_at = _utcnow()
    await session.flush()


@dataclass
class JobResult:
    blocked: str | None = None
    roll: pl.JobRoll | None = None
    applied: AppliedEffects | None = None
    from_name: str = ""
    from_tg: int = 0
    job: str = ""
    their_approach: str = ""


async def answer_job(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    mail_id: int, approach: str,
) -> JobResult | None:
    """Accept a joint deed with your approach: both checks are rolled now (the proposer's on his current build)."""
    from app.engine.dice import roll

    m = await session.get(PlayerMail, mail_id)
    if m is None or m.to_tg_id != ch.player_tg_id or m.kind != pl.OFFER or m.delivered_at is not None:
        return None
    p = dict(m.payload or {})
    if p.get("offer") != pl.JOB or approach not in pl.JOB_APPROACHES:
        return None
    res = JobResult(from_name=m.from_name, from_tg=m.from_tg_id, job=str(p.get("job", "")),
                    their_approach=str(p.get("approach", "loud")))
    them = await merc_snapshot(session, m.from_char_id) if m.from_char_id else None
    m.delivered_at = _utcnow()
    if them is None or pl.job_of(content, res.job) is None:
        res.blocked = pl.BLOCK_GONE
        await session.flush()
        return res
    r = pl.job_rolls(ch.state, them.state, content, rng, res.job, approach, res.their_approach)
    res.roll = r
    cfg = content.balance.players.joint
    my_hp = min(roll(cfg.fail_hp_dice, rng), max(0, ch.state.hp - 1)) if r.outcome == pl.FAIL else 0
    their_hp = roll(cfg.fail_hp_dice, rng) if r.outcome == pl.FAIL else 0
    res.applied = apply_effects(ch.state, pl.job_effects(content, res.job, r.outcome, my_hp), content, rng, year)
    await save_active(session, ch, tz)
    _reply(session, m, ch.player_tg_id, f"{ch.state.name} {ch.state.surname}".strip(), year,
           {"offer": pl.JOB, "accepted": True, "g": gender(ch), "job": res.job, "outcome": r.outcome, "hp": their_hp,
            "mine": r.theirs.total, "theirs": r.mine.total}, ch.id)  # type: ignore[attr-defined]
    if m.from_char_id:
        row = await _bond_row(session, ch.id, m.from_char_id, create=True)
        if row is not None:
            gain = await _crew_gain(session, content, ch.player_tg_id, m.from_tg_id, cfg.bond)
            row.value = min(content.balance.players.bond_max, _bond_value(content, row) + gain)
            row.last_at = _utcnow()
    await session.flush()
    await bus.publish("player_job", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch,
                      outcome=r.outcome)
    return res


# -- player quests: held by the doer in playerdata, counted by a bus listener ------------------------------------------

KEY_QUESTS = "pl_quests"  # playerdata: list of {id, from_tg, from_char, from_name, g, goal, reward, until}


async def active_quests(session: AsyncSession, tg_id: int) -> list[dict]:
    return list(await playerdata.get(session, tg_id, KEY_QUESTS, []))


def _quest_mail(session: AsyncSession, to_tg: int, kind: str, q: dict, payload: dict, from_name: str = "",
                from_tg: int = 0) -> None:
    session.add(PlayerMail(to_tg_id=to_tg, from_tg_id=from_tg, from_char_id=None, to_char_id=None,
                           from_name=from_name or str(q.get("from_name", "")), from_year=0, kind=kind,
                           payload={"goal": q.get("goal"), "g": q.get("g", "m"), **payload}))


async def expire_quests(session: AsyncSession, content: Content, tg_id: int, year: int) -> None:
    """Quests past their date fail: the doer is told, the giver gets the reward back."""
    active = await active_quests(session, tg_id)
    now = _utcnow().isoformat(timespec="minutes")
    keep = [q for q in active if str(q.get("until", "")) >= now]
    if len(keep) == len(active):
        return
    for q in active:
        if q in keep:
            continue
        _quest_mail(session, tg_id, pl.Q_FAIL, q, {})
        session.add(PlayerMail(
            to_tg_id=int(q["from_tg"]), from_tg_id=tg_id, from_char_id=None, to_char_id=q.get("from_char"),
            from_name=await repo.display_name(session, tg_id, ""), from_year=year, kind=pl.REPLY,
            payload={"offer": pl.QUEST, "accepted": False, "failed": True, "goal": q.get("goal"),
                     "refund": int(q.get("reward", 0))}))
    await playerdata.put(session, tg_id, KEY_QUESTS, keep)
    await session.flush()


class PlayerQuestListener:
    """Bus subscriber: a deed of the doer that matches one of his player quests finishes it."""

    def __init__(self, content: Content, tz: str) -> None:
        self.content = content
        self.tz = tz

    async def __call__(self, ev: bus.GameEvent) -> None:
        from app.engine import daily as engine_daily

        s = ev.session
        if s is None:
            return
        active = await active_quests(s, ev.player_tg_id)
        if not active:
            return
        now = _utcnow().isoformat(timespec="minutes")
        done = []
        for q in active:
            g = pl.quest_goal(self.content, str(q.get("goal")))
            if g is not None and str(q.get("until", "")) >= now and engine_daily.goal_matches(g.goal, ev.name, ev.params):
                done.append(q)
                break  # one deed finishes one quest
        if not done:
            return
        q = done[0]
        await playerdata.put(s, ev.player_tg_id, KEY_QUESTS, [x for x in active if x is not q])
        doer_name = await repo.display_name(s, ev.player_tg_id, "")
        if ev.ch is not None:
            doer_name = f"{ev.ch.state.name} {ev.ch.state.surname}".strip()
        _quest_mail(s, ev.player_tg_id, pl.Q_PAY, q, {"eddies": int(q.get("reward", 0))})
        _quest_mail(s, int(q["from_tg"]), pl.Q_DONE, q,
                    {"g": gender(ev.ch) if ev.ch is not None else "m"}, from_name=doer_name, from_tg=ev.player_tg_id)
        if ev.character_id and q.get("from_char"):
            row = await _bond_row(s, int(ev.character_id), int(q["from_char"]), create=True)
            if row is not None:
                row.value = min(self.content.balance.players.bond_max,
                                _bond_value(self.content, row) + self.content.balance.players.quest.bond)
                row.last_at = _utcnow()
        await s.flush()


# -- pvp: a hit on another player's character ----------------------------------------------------------------------------

KEY_PVP_LAST = "pvp_last"        # playerdata of the attacker: {target tg: iso time of the last hit}
KEY_PVP_REVENGE = "pvp_revenge"  # playerdata of the target: {attacker tg: iso time until a free hit back}
KEY_PVP_OFF = "pvp_off"          # playerdata: True = out of pvp (cannot attack, cannot be attacked)

BLOCK_PVP_OFF = "pvp_off"          # you are out of pvp
BLOCK_PVP_TARGET_OFF = "pvp_target_off"
BLOCK_PVP_NEWBIE = "pvp_newbie"
BLOCK_PVP_COOLDOWN = "pvp_cooldown"
BLOCK_PVP_RECENT = "pvp_recent"    # cannot leave pvp right after a hit


async def pvp_off(session: AsyncSession, tg_id: int) -> bool:
    return bool(await playerdata.get(session, tg_id, KEY_PVP_OFF, False))


async def set_pvp_off(session: AsyncSession, content: Content, tg_id: int, off: bool) -> str | None:
    if off:
        last = await playerdata.get(session, tg_id, KEY_PVP_LAST, {})
        border = (_utcnow() - timedelta(days=content.balance.players.pvp.cooldown_days)).isoformat(timespec="minutes")
        if any(str(t) >= border for t in last.values()):
            return BLOCK_PVP_RECENT
    await playerdata.put(session, tg_id, KEY_PVP_OFF, off)
    return None


async def revenge_until(session: AsyncSession, my_tg: int, target_tg: int) -> str | None:
    rec = await playerdata.get(session, my_tg, KEY_PVP_REVENGE, {})
    until = rec.get(str(target_tg))
    return until if until and str(until) >= _utcnow().isoformat(timespec="minutes") else None


@dataclass
class PvpResult:
    blocked: str | None = None
    no_ap: bool = False
    target: Other | None = None
    roll: pl.PvpRoll | None = None
    applied: AppliedEffects | None = None
    revenge: bool = False
    bounty: int = 0
    relic: str = ""          # owner request 25.10: a relic changed hands ("+name" won, "-name" lost)


async def attack(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    char_id: int, mode: str,
) -> PvpResult:
    cfg = content.balance.players.pvp
    res = PvpResult()
    target = await other(session, content, ch, char_id, tz)
    if target is None or mode not in pl.PVP_MODES:
        res.blocked = pl.BLOCK_GONE
        return res
    res.target = target
    if int(ch.state.flags.get("jail_weeks", 0) or 0) > 0:
        res.blocked = pl.BLOCK_JAIL
        return res
    if await pvp_off(session, ch.player_tg_id):
        res.blocked = BLOCK_PVP_OFF
        return res
    revenge = await revenge_until(session, ch.player_tg_id, target.tg_id) is not None
    res.revenge = revenge
    snap = await merc_snapshot(session, target.char_id)
    if snap is None:
        res.blocked = pl.BLOCK_GONE
        return res
    if away(content, ch, snap.state.district):  # owner request 30.10: no reaching across a border, revenge included
        res.blocked = BLOCK_AWAY
        return res
    if not revenge:
        if await pvp_off(session, target.tg_id):
            res.blocked = BLOCK_PVP_TARGET_OFF
            return res
        if snap.state.weeks_lived < cfg.newbie_weeks:
            res.blocked = BLOCK_PVP_NEWBIE
            return res
        last = (await playerdata.get(session, ch.player_tg_id, KEY_PVP_LAST, {})).get(str(target.tg_id))
        border = (_utcnow() - timedelta(days=cfg.cooldown_days)).isoformat(timespec="minutes")
        if last and str(last) >= border:
            res.blocked = BLOCK_PVP_COOLDOWN
            return res
    if not ap_services.can_spend(ch, cfg.ap):
        res.no_ap = True
        return res
    ap_services.spend(ch, content, cfg.ap)
    r = pl.pvp_fight(ch.state, snap.state, content, rng, mode)
    res.roll = r
    fx: dict[str, int | bool] = dict(_vow_breaks(ch.state, content))
    if r.won:
        fx.update({"eddies": r.steal, "street_cred": cfg.win_cred})
    else:
        fx.update({"hp": -r.hp_loss, "heat": cfg.lose_heat})
    res.applied = apply_effects(ch.state, {k: v for k, v in fx.items() if v}, content, rng, year)
    if r.won:  # owner request: a bounty on the beaten character goes to the winner
        from app.services import bounties

        res.bounty = await bounties.claim(session, content, ch, year, target.char_id)
    from app.services import relic  # owner request 25.10: the loser of a skirmish gives a relic to the winner

    me = f"{ch.state.name} {ch.state.surname}".strip()
    if r.won:
        nm = await relic.pvp(session, content, ch, ch.id, ch.player_tg_id, me, target.char_id, target.tg_id, rng)
        res.relic = f"+{nm}" if nm else ""
    else:
        nm = await relic.pvp(session, content, None, target.char_id, target.tg_id, target.name, ch.id, ch.player_tg_id, rng)
        if nm:
            res.relic = f"-{nm}"
            ch.state.relics = tuple(x for x in ch.state.relics if relic.get(content, x) is None or relic.get(content, x).name != nm)
    await save_active(session, ch, tz)
    now = _utcnow().isoformat(timespec="minutes")
    last_rec = await playerdata.get(session, ch.player_tg_id, KEY_PVP_LAST, {})
    last_rec[str(target.tg_id)] = now
    await playerdata.put(session, ch.player_tg_id, KEY_PVP_LAST, last_rec)
    if revenge:  # the free hit back is spent
        rec = await playerdata.get(session, ch.player_tg_id, KEY_PVP_REVENGE, {})
        rec.pop(str(target.tg_id), None)
        await playerdata.put(session, ch.player_tg_id, KEY_PVP_REVENGE, rec)
    session.add(PlayerMail(
        to_tg_id=target.tg_id, from_tg_id=ch.player_tg_id, from_char_id=ch.id, to_char_id=target.char_id,
        from_name=f"{ch.state.name} {ch.state.surname}".strip(), from_year=year, kind=pl.ATTACKED,
        payload={"mode": mode, "won": r.won, "steal": r.steal, "beat": r.beat, "g": gender(ch),
                 "revenge": revenge, "char": ch.id},
    ))
    await session.flush()
    await repo.log_event(session, "player_attack", {"to": target.char_id, "mode": mode, "won": r.won,
                                                    "steal": r.steal}, character_id=ch.id)
    await bus.publish("player_attack", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch,
                      won=r.won)
    return res


async def _attacked_effects(session: AsyncSession, content: Content, ch: ActiveCharacter, from_tg: int,
                            p: dict) -> dict[str, int | bool]:
    """The target's side of a hit, applied on delivery; the right to hit back is granted here."""
    cfg = content.balance.players.pvp
    rec = await playerdata.get(session, ch.player_tg_id, KEY_PVP_REVENGE, {})
    if not p.get("revenge"):
        rec[str(from_tg)] = (_utcnow() + timedelta(days=cfg.revenge_days)).isoformat(timespec="minutes")
        await playerdata.put(session, ch.player_tg_id, KEY_PVP_REVENGE, rec)
    if not p.get("won"):
        return {"street_cred": cfg.win_cred}
    if p.get("mode") == pl.ROB:
        lose = min(int(p.get("steal", 0) or 0), max(0, ch.state.eddies))
        p["lost"] = lose
        return {"eddies": -lose} if lose else {}
    dmg = min(int(p.get("beat", 0) or 0), max(0, ch.state.hp - 1))
    p["lost"] = dmg
    return {"hp": -dmg} if dmg else {}


# -- the group ping ----------------------------------------------------------------------------------------------------

_last_ping: dict[int, float] = {}


def ping_due(content: Content, tg_id: int) -> bool:
    """True (and remembered) when the recipient may be pinged in the group now."""
    now = time.monotonic()
    if now - _last_ping.get(tg_id, -1e9) < content.balance.players.ping_cooldown_s:
        return False
    _last_ping[tg_id] = now
    return True
