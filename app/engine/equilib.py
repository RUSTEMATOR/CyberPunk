"""The balance of the three forces of the city (owner request 26.10, docs/PLAN_EQUILIBRIUM.md part 1): the gangs, the
Order (NCPD and the corps) and the Midnight Agency. Pure rules; the service (services/equilib.py) keeps the live
state here the way services/crisis.py does for engine/crisis.py, and the derived caches see a change through
`version`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from app.content.loader import Content
from app.engine.state import CharacterState

GANG, ORDER, AGENCY = "gang", "order", "agency"
FORCES = (GANG, ORDER, AGENCY)
NAMES = {GANG: "Банды", ORDER: "Порядок", AGENCY: "Агентство «Полночь»"}
TIER_NAMES = ("равновесие", "напряжение", "смута", "хаос")

_live: dict[str, Any] = {"mods": {}, "tier": 0, "dom": "", "weak": "", "chrome": 1.0, "version": 0}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.equilib


def shares(values: Mapping[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(values.get(f, 0))) for f in FORCES) or 1.0
    return {f: max(0.0, float(values.get(f, 0))) / total for f in FORCES}


def skew(sh: Mapping[str, float]) -> float:
    """How far the city is from a third each: 0 (even) .. 2/3 (one force holds all)."""
    return max(abs(sh[f] - 1 / 3) for f in FORCES)


def tier_of(content: Content, sh: Mapping[str, float]) -> int:
    s = skew(sh)
    return sum(1 for t in cfg(content).tier_at if s >= t)


def dominant(sh: Mapping[str, float]) -> str:
    return max(FORCES, key=lambda f: sh[f])


def weakest(sh: Mapping[str, float]) -> str:
    return min(FORCES, key=lambda f: sh[f])


def mods_for(content: Content, tier: int) -> dict[str, int]:
    """What every character feels at this tier: lower pay, slower growth."""
    c = cfg(content)
    if tier <= 0:
        return {}
    i = min(tier, len(c.salary_pct)) - 1
    return {"salary_pct.all": c.salary_pct[i], "gig_pay_pct": c.gig_pay_pct[i], "xp.all": c.xp_pct[i]}


def chrome_mult_for(content: Content, tier: int) -> float:
    c = cfg(content)
    return 1.0 if tier <= 0 else float(c.chrome_price_mult[min(tier, len(c.chrome_price_mult)) - 1])


def set_live(content: Content, tier: int, dom: str, weak: str) -> None:
    new = {"mods": mods_for(content, tier), "tier": tier, "dom": dom, "weak": weak,
           "chrome": chrome_mult_for(content, tier)}
    if any(_live.get(k) != v for k, v in new.items()):
        _live.update(new)
        _live["version"] = int(_live.get("version", 0)) + 1


def version() -> int:
    return int(_live.get("version", 0))


def live() -> dict[str, Any]:
    return dict(_live)


def mobilised() -> bool:
    """The Agency is the weakest force and the city is in unrest or worse: the rippers are taken for the war."""
    return int(_live.get("tier", 0)) >= 2 and _live.get("weak") == AGENCY


def chrome_mult() -> float:
    return float(_live.get("chrome", 1.0) or 1.0)


def mod_sources(state: CharacterState) -> Iterator[tuple[str, Mapping[str, int]]]:
    if _live.get("mods"):
        yield "equilib", dict(_live["mods"])
