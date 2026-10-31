"""Interaction between the characters of different players (docs/EPIC_PLAYERS.md, phase 1). Pure functions.

What one action costs the sender and what the letter carries to the recipient. The sender's side is applied at
once; the recipient's side travels as a letter (`player_mail`) and is applied by the recipient's own session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import random
from datetime import datetime

from app.content.loader import Content
from app.engine.state import FLAG_JAIL_WEEKS, CharacterState

GIFT, DRINK, VOUCH, NOTE, TOGETHER = "gift", "drink", "vouch", "note", "together"
ACTIONS: tuple[str, ...] = (GIFT, DRINK, VOUCH, NOTE, TOGETHER)
T_TRAIN, T_WALK, T_RIDE = "train", "walk", "ride"  # kinds of time together
TOGETHER_KINDS: tuple[str, ...] = (T_TRAIN, T_WALK, T_RIDE)
# Offers: a letter that waits for the recipient's yes or no; then both sides change.
HOOKUP, DATE, DUEL, QUEST, CREW, JOB = "hookup", "date", "duel", "quest", "crew", "job"
CHILD = "child"  # owner request 25.10: a child of two players' characters
OFFERS: tuple[str, ...] = (HOOKUP, DATE, DUEL, QUEST, CREW, JOB, CHILD)
# mail kinds of a player quest: the reward to the doer, the news to the giver, the failure to the doer
Q_PAY, Q_DONE, Q_FAIL = "qpay", "qdone", "qfail"
OFFER = "offer"  # mail kind of an offer; payload["offer"] names it
REPLY = "reply"  # mail kind of the answer to an offer, back to the one who offered
HIRE = "hire"    # mail kind of a merc's share after a gig
DAILY_PAIR: frozenset[str] = frozenset({DRINK, VOUCH, TOGETHER})  # once per real day for one pair

BLOCK_EDDIES = "eddies"
BLOCK_CAP = "cap"
BLOCK_TODAY = "today"
BLOCK_NOTES = "notes"
BLOCK_JAIL = "jail"
BLOCK_GONE = "gone"
BLOCK_AMOUNT = "amount"
BLOCK_EMPTY = "empty"
BLOCK_PENDING = "pending"  # the same offer already waits for an answer
BLOCK_STAKE = "stake"
BLOCK_QUESTS = "quests"  # the doer already holds max_active quests


@dataclass
class Plan:
    """One action: the AP it takes, the sender's effects now, the payload of the letter, the bond gain."""

    action: str
    ap: int = 0
    effects: dict[str, int | bool] = field(default_factory=dict)
    payload: dict[str, object] = field(default_factory=dict)
    bond: int = 0


def ap_cost(content: Content, action: str) -> int:
    cfg = content.balance.players
    return {DRINK: cfg.drink.ap, VOUCH: cfg.vouch.ap, TOGETHER: cfg.together.ap}.get(action, 0)


def fee(content: Content, amount: int) -> int:
    return amount * content.balance.players.transfer_fee_pct // 100


def blocked(
    state: CharacterState, content: Content, action: str, *, amount: int = 0, sent_today: int = 0,
    pair_used_today: bool = False, notes_today: int = 0, text: str = "", kind: str = "", skill: str | None = None,
    crew: bool = False,
) -> str | None:
    """Why the action cannot be done now, or None. AP is checked by the service."""
    cfg = content.balance.players
    if action == TOGETHER:
        if kind not in TOGETHER_KINDS or (kind == T_TRAIN and not skill):
            return BLOCK_GONE
        if state.eddies < together_kind(content, kind).cost:
            return BLOCK_EDDIES
    if action in (DRINK, VOUCH, TOGETHER) and int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0:
        return BLOCK_JAIL
    if action == GIFT:
        if amount < 1:  # owner request 24.10: any amount, typed or from the buttons
            return BLOCK_AMOUNT
        if state.eddies < amount:
            return BLOCK_EDDIES
        if not crew and sent_today + amount > cfg.transfer_daily_cap:  # crew members send without a cap
            return BLOCK_CAP
    if action in DAILY_PAIR and pair_used_today:
        return BLOCK_TODAY
    if action == NOTE:
        if notes_today >= cfg.note.per_day:
            return BLOCK_NOTES
        if not text.strip():
            return BLOCK_EMPTY
    return None


def together_kind(content: Content, kind: str):  # noqa: ANN201
    t = content.balance.players.together
    return {T_TRAIN: t.train, T_WALK: t.walk, T_RIDE: t.ride}[kind]


def plan(content: Content, action: str, *, amount: int = 0, text: str = "", kind: str = "",
         skill: str | None = None, crew: bool = False) -> Plan:
    cfg = content.balance.players
    if action == TOGETHER:
        k = together_kind(content, kind)
        trained = skill if kind == T_TRAIN else ("driving" if kind == T_RIDE else None)
        fx: dict[str, int | bool] = {}
        if k.cost:
            fx["eddies"] = -k.cost
        if k.mood:
            fx["mood"] = k.mood
        if trained and k.xp:
            fx[f"skill_xp:{trained}"] = k.xp
        return Plan(TOGETHER, cfg.together.ap, fx,
                    {"kind": kind, "skill": trained, "xp": k.xp if trained else 0, "mood": k.mood}, cfg.together.bond)
    if action == GIFT:
        net = amount if crew else amount - fee(content, amount)  # owner request 24.10: no fee inside a crew
        return Plan(GIFT, 0, {"eddies": -amount}, {"eddies": net, "sent": amount}, cfg.transfer_bond)
    if action == DRINK:
        return Plan(DRINK, cfg.drink.ap, {"mood": cfg.drink.mood}, {"mood": cfg.drink.mood}, cfg.drink.bond)
    if action == VOUCH:
        return Plan(VOUCH, cfg.vouch.ap, {}, {"fixer_rel": cfg.vouch.fixer_rel}, cfg.vouch.bond)
    if action == NOTE:
        return Plan(NOTE, 0, {}, {"text": text.strip()[: cfg.note.max_len]}, cfg.note.bond)
    raise ValueError(f"unknown player action {action!r}")


def bond_now(content: Content, value: int, last_at: datetime | None, now: datetime) -> int:
    """The bond after cooling: past `bond_decay_after_days` without contact it drops by 1 per week."""
    cfg = content.balance.players
    if last_at is None:
        return value
    idle = (now - last_at).days - cfg.bond_decay_after_days
    return max(0, value - max(0, idle // 7 + (1 if idle >= 0 else 0)))


def bond_label(content: Content, value: int) -> str:
    label = content.balance.players.bond_labels[0][1]
    for threshold, name in content.balance.players.bond_labels:
        if value >= threshold:
            label = name
    return label


# -- offers ------------------------------------------------------------------------------------------------------------


def quest_goal(content: Content, key: str):  # noqa: ANN201
    return next((g for g in content.balance.players.quest.goals if g.key == key), None)


def offer_blocked(state: CharacterState, content: Content, offer: str, *, stake: int = 0, pending: bool = False,
                  goal: str = "") -> str | None:
    if offer not in OFFERS:
        return BLOCK_GONE
    if offer == CREW:
        return BLOCK_PENDING if pending else None
    if offer == JOB:
        if job_of(content, goal) is None:
            return BLOCK_GONE
        return BLOCK_PENDING if pending else None
    if offer == QUEST:
        if quest_goal(content, goal) is None:
            return BLOCK_GONE
        if stake not in content.balance.players.quest.rewards:
            return BLOCK_STAKE
        if state.eddies < stake:
            return BLOCK_EDDIES
        return None  # several quests to one player may wait at once
    if pending:
        return BLOCK_PENDING
    if int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0:
        return BLOCK_JAIL
    if offer == CHILD:
        from app.engine import relations as rel

        return rel.baby_blocked(state, content, content.balance.players.child.cost)
    if offer == DUEL:
        if stake not in content.balance.players.duel.stakes:
            return BLOCK_STAKE
        if state.eddies < stake:
            return BLOCK_EDDIES
    return None


@dataclass
class DuelRoll:
    mine: int       # stat + skill + d10 of the one who accepted
    theirs: int     # the same for the one who offered
    won: bool       # the one who accepted won
    loser_hp: int   # damage to the loser (already limited to keep 1 hp)


def duel(me: CharacterState, them: CharacterState, content: Content, rng: random.Random) -> DuelRoll:
    """A duel to the first blood: stat + skill + d10 each, ties rerolled. Chrome and perks count (derived totals)."""
    from app.engine import derived
    from app.engine.dice import roll

    cfg = content.balance.players.duel

    def score(st: CharacterState) -> int:
        return derived.stat_total(st, content, cfg.stat) + derived.skill_total(st, content, cfg.skill)

    while True:
        a, b = score(me) + rng.randint(1, 10), score(them) + rng.randint(1, 10)
        if a != b:
            break
    won = a > b
    loser = them if won else me
    dmg = min(roll(cfg.loser_hp_dice, rng), max(0, loser.hp - 1))
    return DuelRoll(a, b, won, dmg)


# -- hire --------------------------------------------------------------------------------------------------------------


def assist_extra(me: CharacterState, merc_base: int, content: Content, stat: str, skill: str | None) -> int:
    """The bonus a merc adds to one gig check: at least `assist_bonus`, or the gap when the merc is better."""
    from app.engine import derived

    mine = derived.stat_total(me, content, stat) + derived.skill_total(me, content, skill)
    return max(content.balance.players.hire.assist_bonus, merc_base - mine)


def merc_base(merc: CharacterState, content: Content, stat: str, skill: str | None) -> int:
    from app.engine import derived

    return derived.stat_total(merc, content, stat) + derived.skill_total(merc, content, skill)


def merc_cut(pay: int, share: int) -> int:
    return max(0, pay * share // 100)


# -- pvp: a hit on another player's character ----------------------------------------------------------------------------

ROB, BEAT = "rob", "beat"
PVP_MODES: tuple[str, ...] = (ROB, BEAT)
ATTACKED = "attacked"  # mail kind to the target


def pvp_score(st: CharacterState, content: Content) -> int:
    """The better of shooting and fists: stat + skill, chrome and perks included."""
    from app.engine import derived

    return max(
        derived.stat_total(st, content, "REF") + derived.skill_total(st, content, "firearms"),
        derived.stat_total(st, content, "BODY") + derived.skill_total(st, content, "melee"),
    )


@dataclass
class PvpRoll:
    mine: int
    theirs: int
    won: bool        # the attacker won
    steal: int       # robbery: eddies taken from the target (the target pays on delivery, up to what he has then)
    beat: int        # a beating: hp of the target (limited on delivery to keep 1)
    hp_loss: int     # the attacker lost: his hp (limited to keep 1)


def pvp_fight(me: CharacterState, target: CharacterState, content: Content, rng: random.Random, mode: str) -> PvpRoll:
    from app.engine.dice import roll

    cfg = content.balance.players.pvp
    a = pvp_score(me, content) + rng.randint(1, 10)
    d = pvp_score(target, content) + cfg.defense_bonus + rng.randint(1, 10)
    won = a > d
    steal = min(cfg.rob_cap, target.eddies * cfg.rob_pct // 100) if won and mode == ROB else 0
    beat = roll(cfg.beat_dice, rng) if won and mode == BEAT else 0
    hp_loss = 0 if won else min(roll(cfg.lose_hp_dice, rng), max(0, me.hp - 1))
    return PvpRoll(a, d, won, max(0, steal), beat, hp_loss)


# -- joint deeds ---------------------------------------------------------------------------------------------------------

JOB_APPROACHES: dict[str, tuple[str, str]] = {
    "loud": ("REF", "firearms"), "quiet": ("REF", "stealth"), "hack": ("INT", "netrunning"), "talk": ("COOL", "persuasion"),
}
FULL, HALF, FAIL = "full", "half", "fail"


def job_of(content: Content, key: str):  # noqa: ANN201
    return next((j for j in content.balance.players.joint.jobs if j.key == key), None)


@dataclass
class JobRoll:
    mine: object     # CheckResult of the one who accepted
    theirs: object   # CheckResult of the one who proposed (his build as it is now)
    outcome: str     # full, half, fail
    synergy: bool


def job_rolls(me: CharacterState, them: CharacterState, content: Content, rng: random.Random, key: str,
              my_approach: str, their_approach: str) -> JobRoll:
    from app.engine import checks

    job = job_of(content, key)
    assert job is not None
    syn = my_approach != their_approach
    bonus = content.balance.players.joint.synergy if syn else 0
    s1, k1 = JOB_APPROACHES[my_approach]
    s2, k2 = JOB_APPROACHES[their_approach]
    a = checks.roll_check(me, content, rng, s1, k1, job.dv, ("crime",), extra=bonus)
    b = checks.roll_check(them, content, rng, s2, k2, job.dv, ("crime",), extra=bonus)
    ok = int(a.ok) + int(b.ok)
    return JobRoll(a, b, FULL if ok == 2 else HALF if ok == 1 else FAIL, syn)


def job_effects(content: Content, key: str, outcome: str, hp_loss: int) -> dict[str, int | bool]:
    cfg = content.balance.players.joint
    job = job_of(content, key)
    pay = job.pay if job else 0
    if outcome == FULL:
        return {"eddies": pay, "street_cred": cfg.cred}
    if outcome == HALF:
        return {"eddies": pay // 2}
    return {k: v for k, v in {"hp": -hp_loss, "heat": cfg.fail_heat}.items() if v}
