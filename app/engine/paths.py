"""Career paths (owner request): a fixer, a ripper, a politician. Pure functions.

The path lives in the flag `path` = {"key", "xp", "seat"}. One path at a time; a new path starts from zero.
The rank is the highest one whose xp, street cred, fame and seat the character has; its mods count in derived.py.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG_PATH = "path"

R_RANK, R_AP, R_EDDIES, R_JAIL, R_GONE, R_SEAT = "rank", "ap", "eddies", "jail", "gone", "seat"


def defs(content: Content) -> list:  # noqa: ANN201
    return list(content.balance.paths.paths)


def path_def(content: Content, key: str | None):  # noqa: ANN201
    return next((p for p in content.balance.paths.paths if p.key == key), None)


def record(state: CharacterState) -> dict:
    rec = state.flags.get(FLAG_PATH)
    return dict(rec) if isinstance(rec, dict) else {}


def path_of(state: CharacterState, content: Content):  # noqa: ANN201
    return path_def(content, record(state).get("key"))


def xp(state: CharacterState) -> int:
    return int(record(state).get("xp", 0) or 0)


def rank(state: CharacterState, content: Content) -> int:
    """1-based rank; 0 when the character has no path."""
    p = path_of(state, content)
    if p is None:
        return 0
    rec = record(state)
    n = 1
    for i, r in enumerate(p.ranks[1:], start=2):
        if xp(state) < r.xp or state.street_cred < r.min_cred or state.fame < r.min_fame:
            break
        if r.seat and not rec.get("seat"):
            break
        n = i
    return n


def rank_def(state: CharacterState, content: Content):  # noqa: ANN201
    p = path_of(state, content)
    n = rank(state, content)
    return p.ranks[n - 1] if p is not None and n else None


def next_rank(state: CharacterState, content: Content):  # noqa: ANN201
    p = path_of(state, content)
    n = rank(state, content)
    return p.ranks[n] if p is not None and n < len(p.ranks) else None


def choose(state: CharacterState, content: Content, key: str) -> bool:
    if path_def(content, key) is None:
        return False
    state.flags[FLAG_PATH] = {"key": key, "xp": 0, "seat": False}
    return True


def add_xp(state: CharacterState, content: Content, key: str, n: int) -> tuple[int, int, int]:
    """Xp to the path `key` if the character walks it: (rank before, rank after, xp really added)."""
    rec = record(state)
    if rec.get("key") != key or n == 0:
        r = rank(state, content)
        return r, r, 0
    before = rank(state, content)
    old = int(rec.get("xp", 0) or 0)
    rec["xp"] = max(0, old + n)
    state.flags[FLAG_PATH] = rec
    return before, rank(state, content), rec["xp"] - old


def set_seat(state: CharacterState, on: bool) -> None:
    rec = record(state)
    if rec:
        rec["seat"] = on
        state.flags[FLAG_PATH] = rec


def action(content: Content, path_key: str, key: str):  # noqa: ANN201
    p = path_def(content, path_key)
    return next((a for a in p.actions if a.key == key), None) if p is not None else None


def action_blocked(state: CharacterState, content: Content, act, *, jailed: bool = False) -> str | None:  # noqa: ANN001
    if jailed:
        return R_JAIL
    if rank(state, content) < act.min_rank:
        return R_RANK
    if getattr(act, "seat", False) and record(state).get("seat"):
        return R_SEAT
    if state.ap < act.ap:
        return R_AP
    if state.eddies < act.price:
        return R_EDDIES
    return None


def pay(content: Content, act, n: int, rng: random.Random) -> int:  # noqa: ANN001
    lo, hi = act.pay
    if hi <= 0:
        return 0
    base = rng.randint(lo, max(lo, hi))
    return base * (100 + content.balance.paths.rank_pay_pct * max(0, n - 1)) // 100


def pay_range(content: Content, act, n: int) -> tuple[int, int]:  # noqa: ANN001
    k = 100 + content.balance.paths.rank_pay_pct * max(0, n - 1)
    return act.pay[0] * k // 100, act.pay[1] * k // 100


def income(state: CharacterState, content: Content) -> int:
    r = rank_def(state, content)
    return r.income if r is not None else 0


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    p = path_of(state, content)
    r = rank_def(state, content)
    if p is not None and r is not None and r.mods:
        yield f"path:{p.key}", r.mods


# -- the fixer's contracts ------------------------------------------------------------------------------------------------


def is_fixer(state: CharacterState, content: Content) -> bool:
    return record(state).get("key") == "fixer" and rank(state, content) >= content.balance.paths.contract.min_rank


def contract_job(content: Content, key: str):  # noqa: ANN201
    return next((j for j in content.balance.paths.contract.jobs if j.key == key), None)


def contract_jobs(state: CharacterState, content: Content) -> list:
    n = rank(state, content)
    return [j for j in content.balance.paths.contract.jobs if j.min_rank <= n]


def plan_bonus(content: Content, fixer_rank: int) -> int:
    cfg = content.balance.paths.contract
    return cfg.plan_bonus + cfg.rank_bonus * max(0, fixer_rank - 1)


def split(pool: int, share: int) -> tuple[int, int]:
    """(the fixer's part, the executor's part)."""
    mine = pool * share // 100
    return mine, pool - mine


@dataclass
class ContractRoll:
    check: object   # CheckResult of the executor
    ok: bool
    bonus: int


def contract_roll(executor: CharacterState, content: Content, rng: random.Random, job, approach: str,  # noqa: ANN001
                  fixer_rank: int) -> ContractRoll:
    from app.engine import checks
    from app.engine.players import JOB_APPROACHES

    stat, skill = JOB_APPROACHES[approach]
    bonus = plan_bonus(content, fixer_rank)
    r = checks.roll_check(executor, content, rng, stat, skill, job.dv, ("crime",), extra=bonus)
    return ContractRoll(r, bool(r.ok), bonus)
