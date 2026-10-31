"""The oligarchs (owner request 23.10): a megacorp's asset pays a growing share every month and owes favours.

flag `corp_asset` (set by the event wealth_corp_asset), `corp_asset_pct` (the share now), `corp_asset_n` (presses).
"""

from __future__ import annotations

import random

from app.content.loader import Content
from app.engine.effects import apply_effects
from app.engine.state import FLAG_PENDING_EVENTS
from app.services.game import ActiveCharacter

START_PCT, STEP_PCT, MAX_PCT = 3, 1, 12
DEMAND_EVERY = 3
DEMAND_EVENT = "corp_asset_demand"


def press_hook(ch: ActiveCharacter, content: Content, rng: random.Random, year: int) -> list[str]:
    st = ch.state
    if not st.flags.get("corp_asset"):
        st.flags.pop("corp_asset_pct", None)
        st.flags.pop("corp_asset_n", None)
        return []
    pct = int(st.flags.get("corp_asset_pct", START_PCT) or START_PCT)
    n = int(st.flags.get("corp_asset_n", 0) or 0) + 1
    take = max(0, st.eddies) * pct // 100
    fx: dict = {"eddies": -take} if take else {}
    if st.heat > 0:
        fx["heat"] = -1  # the corp's lawyers
    if fx:
        apply_effects(st, fx, content, rng, year)
    st.flags["corp_asset_pct"] = min(MAX_PCT, pct + STEP_PCT)
    st.flags["corp_asset_n"] = n
    lines = [f"🏢 Корпорация забирает свою долю: {pct}% — {take} эдди. В следующем месяце будет {st.flags['corp_asset_pct']}%."]
    if n % DEMAND_EVERY == 0 and DEMAND_EVENT in content.events:
        queue = list(st.flags.get(FLAG_PENDING_EVENTS) or [])
        if DEMAND_EVENT not in queue:
            st.flags[FLAG_PENDING_EVENTS] = queue + [DEMAND_EVENT]
            lines.append("🏢 Куратор корпорации просит о встрече.")
    return lines
