"""Loans (owner request 24.10): borrow from a friend, the employer, a bank, a ripper, a fixer or a gang's shark.

Pure. A loan is an ordinary debt (DebtState) with key `loan:<lender>` and the lender's weekly rate: the week takes the
interest and the minimum like any debt. Missed weeks bring the lender's own risk (`risk_effects`) once
`risk_weeks` are missed, and again every as many weeks. A friend's loan remembers the friend (creditor_ref npc:<index>).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.content.loader import Content
from app.engine.effects import AppliedEffects, apply_effects
from app.engine.state import CharacterState, DebtState

PREFIX = "loan:"

BLOCK_HAVE = "have"
BLOCK_STATUS = "status"
BLOCK_CRED = "cred"
BLOCK_JOB = "job"
BLOCK_FRIEND = "friend"
BLOCK_NONE = "none"


@dataclass
class Offer:
    key: str
    amount: int            # the most this lender gives now (0 when blocked)
    blocked: str | None
    friend: int | None = None   # index in state.npcs of the friend who lends


def loan_of(state: CharacterState, key: str) -> DebtState | None:
    return next((d for d in state.debts if d.key == PREFIX + key), None)


def _friend(state: CharacterState, need: int) -> int | None:
    best, idx = -1, None
    for i, n in enumerate(state.npcs):
        if n.alive and n.kind in ("friend", "family", "partner") and n.relation >= need and n.relation > best:
            best, idx = n.relation, i
    return idx


def offer(state: CharacterState, content: Content, key: str, salary: int) -> Offer:
    lender = content.balance.loans.lenders.get(key)
    if lender is None:
        return Offer(key, 0, BLOCK_NONE)
    if loan_of(state, key) is not None:
        return Offer(key, 0, BLOCK_HAVE)
    if state.status < lender.status_min:
        return Offer(key, 0, BLOCK_STATUS)
    if state.street_cred < lender.cred_min:
        return Offer(key, 0, BLOCK_CRED)
    if lender.needs_job and (state.career is None or salary <= 0):
        return Offer(key, 0, BLOCK_JOB)
    friend = None
    rel = 0
    if lender.friend_min:
        friend = _friend(state, lender.friend_min)
        if friend is None:
            return Offer(key, 0, BLOCK_FRIEND)
        rel = state.npcs[friend].relation
    amount = (lender.base + lender.per_status * state.status + lender.per_cred * state.street_cred
              + lender.per_salary * salary + lender.per_relation * rel)
    return Offer(key, max(0, min(lender.max, amount)), None, friend)


def take(state: CharacterState, content: Content, key: str, amount: int, salary: int, rng: random.Random,
         year: int) -> tuple[str | None, AppliedEffects | None, str]:
    """Borrow `amount` (at most the offer). Returns (blocked, applied, creditor name)."""
    o = offer(state, content, key, salary)
    if o.blocked:
        return o.blocked, None, ""
    amount = min(amount, o.amount)
    if amount <= 0:
        return BLOCK_NONE, None, ""
    lender = content.balance.loans.lenders[key]
    name = lender.name
    ref = key
    if o.friend is not None:
        name = state.npcs[o.friend].name
        ref = f"npc:{o.friend}"
    state.debts.append(DebtState(creditor=name, principal=amount, weekly_rate=lender.weekly_rate,
                                 creditor_kind=lender.kind, creditor_ref=ref, key=PREFIX + key))
    applied = apply_effects(state, {"eddies": amount}, content, rng, year)
    return None, applied, name


def risks(state: CharacterState, content: Content, rng: random.Random, year: int) -> tuple[list[str], list[AppliedEffects]]:
    """The lenders act on missed payments. Called by the week after the debts are paid."""
    lines: list[str] = []
    applied: list[AppliedEffects] = []
    for d in list(state.debts):
        if not d.key or not d.key.startswith(PREFIX):
            continue
        lender = content.balance.loans.lenders.get(d.key[len(PREFIX):])
        if lender is None or d.missed_weeks <= 0 or d.missed_weeks % lender.risk_weeks:
            continue
        who = d.creditor
        if lender.relation_hit and d.creditor_ref and d.creditor_ref.startswith("npc:"):
            i = int(d.creditor_ref[4:])
            if 0 <= i < len(state.npcs) and state.npcs[i].name == d.creditor:
                state.npcs[i].relation = max(0, state.npcs[i].relation - lender.relation_hit)
        if lender.risk_effects:
            applied.append(apply_effects(state, dict(lender.risk_effects), content, rng, year))
        lines.append(lender.risk_text.format(who=who))
    return lines, applied
