"""Life in a relationship (owner request 25.10 «больше жизни в отношениях: нужно периодически проводить время с
партнёром, реагировать на скандалы в недельных событиях»).

- Time together: a date, a night, a hang-out, a gift, a proposal or a child with the partner marks
  `npc.data["seen_week"]` (services/relations.perform).
- Every lived month (press_hook): time together within the last `grace_months` lifts the relation and the mood and
  may bring a warm surprise; a month without it lowers both, harder every month in a row (`npc.data["neglect"]`).
- A scandal event is queued by chance (higher when neglected); the relation at `ultimatum_rel` or below queues the
  ultimatum, where the partner may walk out (`npc_leave:partner`).
"""

from __future__ import annotations

import random

from app.content.loader import Content
from app.engine import relations as rel
from app.engine.effects import apply_effects, apply_npc_relation
from app.engine.state import CharacterState, NpcState
from app.services.game import ActiveCharacter

TOGETHER = frozenset({rel.HANG, rel.DATE, rel.NIGHT, rel.GIFT, rel.PROPOSE, rel.BABY})


def cfg(content: Content):  # noqa: ANN201
    return content.balance.romance


def mark_seen(st: CharacterState, npc: NpcState) -> None:
    npc.data["seen_week"] = st.weeks_lived
    npc.data["neglect"] = 0


def partner(st: CharacterState) -> NpcState | None:
    pool = st.npcs_of("partner")
    return pool[0] if pool else None


def weeks_apart(st: CharacterState, npc: NpcState) -> int | None:
    seen = npc.data.get("seen_week")
    return None if seen is None else max(0, st.weeks_lived - int(seen))


def neglected(st: CharacterState, npc: NpcState, content: Content) -> bool:
    w = weeks_apart(st, npc)
    return w is not None and w > content.balance.time.weeks_per_press * cfg(content).grace_months


async def press_hook(session, content: Content, ch: ActiveCharacter, rng: random.Random,  # noqa: ANN001
                     year: int) -> list[str]:
    from app.services.week import pending_events, set_pending

    st = ch.state
    npc = partner(st)
    if npc is None:
        return []
    c = cfg(content)
    if npc.data.get("seen_week") is None:  # a partner from before this feature: the count starts now
        mark_seen(st, npc)
        return []
    lines: list[str] = []
    queue = pending_events(ch)
    mine = set(c.scandals) | set(c.sweet) | {c.ultimatum}
    if neglected(st, npc, content):
        n = int(npc.data.get("neglect", 0) or 0) + 1
        npc.data["neglect"] = n
        apply_npc_relation(st, content, npc, -c.neglect_rel * min(n, 3))
        apply_effects(st, {"mood": -c.neglect_mood}, content, rng, year)
        lines.append(f"💔 {npc.name} скучает: вы давно не были вместе ({weeks_apart(st, npc)} нед.). "
                     f"Отношения {npc.relation}. Свидание или вечер вдвоём: «Связи».")
        p = min(c.scandal_cap, c.scandal_neglect + c.scandal_step * (n - 1))
    else:
        npc.data["neglect"] = 0
        apply_npc_relation(st, content, npc, c.seen_rel)
        apply_effects(st, {"mood": c.seen_mood}, content, rng, year)
        lines.append(f"💞 {npc.name} рядом, и это греет. Отношения {npc.relation}.")
        p = c.scandal_base
        if not any(e in mine for e in queue) and c.sweet and rng.random() < c.sweet_chance:
            queue.append(rng.choice(c.sweet))
    if any(e in c.scandals or e == c.ultimatum for e in queue):
        set_pending(ch, queue)
        return lines
    if npc.data.get("neglect") and npc.relation <= c.ultimatum_rel and content.events.get(c.ultimatum) is not None:
        queue.append(c.ultimatum)
        lines.append(f"⚠️ {npc.name} на грани: дальше так нельзя.")
    elif c.scandals and rng.random() < p:
        pool = [e for e in c.scandals if e != npc.data.get("last_scandal") and content.events.get(e) is not None]
        if pool:
            pick = rng.choice(pool)
            npc.data["last_scandal"] = pick
            queue.append(pick)
    set_pending(ch, queue)
    return lines
