"""The pressure (owner request 24.10): the city never lets a character just farm and live.

Pure: the threat points of a character, the tier, the chances. The service (services/pressure.py) rolls them every
«Прожить месяц» and queues the events.

Like RimWorld's raid points: the threat grows with wealth, strength and time. Like Crusader Kings' hooks: whoever
helped you holds you (`pr_hook`) and demands. Whoever you wronged (`pr_enmity`) takes revenge. Like the nemesis of
Shadow of Mordor: a named enemy who survives comes back stronger (`nem`).

flags: pr_hook, pr_enmity (0..max_level), pr_n (presses counted), pr_hist (the last event ids),
krysha = {who}, nem = {name, faction, lvl}, nem_spawn = faction key (set by an event).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from app.content.loader import Content
from app.engine.state import CharacterState

F_HOOK, F_ENMITY, F_N, F_HIST, F_KRYSHA, F_NEM, F_NEM_SPAWN = (
    "pr_hook", "pr_enmity", "pr_n", "pr_hist", "krysha", "nem", "nem_spawn")
KRYSHA_PREFIX = "pr_krysha_"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.pressure


def hook(st: CharacterState) -> int:
    return int(st.flags.get(F_HOOK, 0) or 0)


def enmity(st: CharacterState) -> int:
    return int(st.flags.get(F_ENMITY, 0) or 0)


def nemesis(st: CharacterState) -> dict | None:
    n = st.flags.get(F_NEM)
    return n if isinstance(n, dict) and n.get("name") else None


def krysha(st: CharacterState, content: Content):  # noqa: ANN201
    k = st.flags.get(F_KRYSHA)
    who = k.get("who") if isinstance(k, dict) else None
    return (who, cfg(content).krysha[who]) if who in cfg(content).krysha else (None, None)


@dataclass
class Points:
    total: int
    parts: dict[str, int]
    megacorp: bool
    legend: bool = False


def points(st: CharacterState, content: Content, *, megacorp: bool = False) -> Points:
    from app.engine import biz as biz_engine
    from app.engine import perks, upkeep

    c = cfg(content)
    parts = {
        "wealth": int(math.sqrt(max(0, upkeep.wealth(st, content)) / max(1, c.wealth_root_div))),
        "level": c.per_level * int(perks.rec(st, content).get("level", 1) or 1),
        "fame": c.per_fame * max(0, st.fame),
        "cred": max(0, st.street_cred) // max(1, c.cred_div),
        "time": min(c.month_cap, st.weeks_lived // max(1, content.balance.time.weeks_per_press)),
        "biz": c.per_business * len(biz_engine.list_of(st)),
        "corp": (c.megacorp_pts if megacorp else 0) + (c.corp_asset_pts if st.flags.get("corp_asset") else 0),
        "gang": c.gang_pts if (st.flags.get("gang") or {}).get("key") else 0,
        "dynasty": content.balance.legend.pressure_pts * int(st.flags.get("dynasty", 0) or 0),  # owner request 25.10
    }
    return Points(sum(parts.values()), parts, megacorp)


def tier_of(content: Content, pts: Points, st: CharacterState, legend: bool = False) -> str:
    c = cfg(content)
    best = c.tiers[0].key if c.tiers else "street"
    for t in sorted(c.tiers, key=lambda x: x.min):
        if pts.total >= t.min:
            best = t.key
    if pts.megacorp or st.flags.get("corp_asset"):
        best = c.corp_tier
    if legend and any(t.key == c.legend_tier for t in c.tiers):  # owner request 25.10: the shadow of a legend
        best = c.legend_tier
    return best


def tier_name(content: Content, key: str) -> str:
    return next((t.name for t in cfg(content).tiers if t.key == key), key)


def lower_tier(content: Content, key: str) -> str | None:
    order = [t.key for t in sorted(cfg(content).tiers, key=lambda x: x.min)]
    i = order.index(key) if key in order else 0
    return order[i - 1] if i > 0 else None


def base_chance(content: Content, st: CharacterState, pts: Points) -> float:
    from app.engine import derived

    c = cfg(content)
    raw = min(c.max_chance, c.base_chance + pts.total * c.chance_per_pt)
    if pts.legend:
        raw = max(raw, c.legend_chance)
    _, k = krysha(st, content)
    guard = (k.guard if k else 0) + max(0, derived.mod(st, content, "wealth_guard"))
    return raw * (100 - min(c.guard_cap, guard)) / 100


def pick(content: Content, st: CharacterState, tier: str, kind: str, rng: random.Random, taken: list[str]) -> str | None:
    """An event of the tier's pool not seen lately (falls back to the oldest seen)."""
    from app.engine import events as ev_engine

    pool = [e for e in (cfg(content).pools.get(tier) or {}).get(kind, []) if e in content.events and e not in taken
            and ev_engine.matches(content.events[e], st, content)]  # owner request 25.10: no kidnapping of a partner nobody has
    if not pool:
        return None
    hist = list(st.flags.get(F_HIST) or [])
    fresh = [e for e in pool if e not in hist]
    return rng.choice(fresh or pool)


def remember(content: Content, st: CharacterState, event_id: str) -> None:
    hist = list(st.flags.get(F_HIST) or []) + [event_id]
    st.flags[F_HIST] = hist[-cfg(content).history:]


def krysha_fee(content: Content, st: CharacterState) -> int:
    from app.engine import upkeep

    _, k = krysha(st, content)
    if k is None:
        return 0
    return max(k.fee_min, upkeep.wealth(st, content) * k.fee_pct // 100)


def nem_danger(content: Content, nem: dict) -> int:
    c = cfg(content)
    return max(1, min(c.nem_danger_max, c.nem_danger_base + int(nem.get("lvl", 0) or 0)))
