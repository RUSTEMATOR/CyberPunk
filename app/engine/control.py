"""Who holds the streets (owner request 25.10, «как в Prototype»; 25.10 (2): «NCPD пускай не будет … либо банды
заполняют, либо мегакорпорации»). Every district of Night City has two meters, 0–100:

  gang — how much of the district the gangs hold. It grows by itself every day, faster with every new gang in the
         district and in the city; above `ambush_from` the gangs attack the people who live there, more often and in
         bigger packs.
  corp — how much of it the megacorps own: offices, security, bought-out blocks. It grows with every office there
         (canon corps of the district and new corps); above `tight_from` their surveillance makes wanted level grow
         faster, above `regime_from` corporate security sweeps the residents and attacks those in the way.

The two push each other out: where one is stronger the other loses ground. The players hold both back: a cleansing
cuts the gangs (and the corps move in), a strike at a corp cuts the corps (and the gangs move in).
Pure: the state is a dict, the randomness comes in.
"""

from __future__ import annotations

import random
from typing import Any

from app.content.loader import Content

GANG = "gang"
CORP = "corp"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.control


def clamp(n: float) -> int:
    return max(0, min(100, int(round(n))))


def nc_districts(content: Content) -> list[str]:
    return [k for k, d in content.districts.items() if d.city == "nc"]


def offices(content: Content, district: str) -> int:
    return len(megacorps_of(content, district))


def megacorps_of(content: Content, district: str) -> list[str]:
    """The canon megacorps with an office in the district."""
    d = content.districts.get(district)
    return [k for k in (d.corps if d is not None else []) if k in content.corps and content.corps[k].kind == "megacorp"]


def start(content: Content, district: str) -> dict[str, int]:
    c = cfg(content)
    d = content.districts[district]
    return {GANG: clamp(c.start_gang + c.start_gang_per_danger * d.danger),
            CORP: clamp(c.start_corp + c.start_corp_per_office * offices(content, district))}


def normalize(content: Content, raw: dict[str, Any] | None) -> dict[str, dict[str, int]]:
    """Every Night City district with both meters; a new district (or one from the NCPD version) starts fresh."""
    raw = dict(raw or {})
    out: dict[str, dict[str, int]] = {}
    for k in nc_districts(content):
        v = raw.get(k)
        if isinstance(v, dict) and CORP in v:
            out[k] = {GANG: clamp(v.get(GANG, 0)), CORP: clamp(v.get(CORP, 0))}
        else:
            s = start(content, k)
            if isinstance(v, dict) and GANG in v:
                s[GANG] = clamp(v[GANG])
            out[k] = s
    return out


def day(content: Content, state: dict[str, dict[str, int]], gangs: dict[str, int], corps: dict[str, int],
        rng: random.Random) -> list[tuple[str, str]]:
    """One real day. `gangs` / `corps` = alive new gangs / corps per district. Changes `state` in place; returns
    (district, what crossed)."""
    c = cfg(content)
    g_total, c_total = sum(gangs.values()), sum(corps.values())
    crossed: list[tuple[str, str]] = []
    for k, m in state.items():
        g0, k0 = m[GANG], m[CORP]
        g = g0 + c.gang_growth + c.gang_per_crew * gangs.get(k, 0) + c.gang_per_crew_city * g_total + rng.randint(-1, 2)
        p = (k0 + c.corp_growth + c.corp_per_crew * corps.get(k, 0) + c.corp_per_crew_city * c_total
             + c.corp_per_office * offices(content, k) + rng.randint(-1, 2))
        if k0 > g0:  # the stronger side pushes the other out
            g -= (k0 - g0) * c.suppress
        elif g0 > k0:
            p -= (g0 - k0) * c.suppress
        m[GANG], m[CORP] = clamp(g), clamp(p)
        for key, marks in ((GANG, c.gang_marks), (CORP, c.corp_marks)):
            before = g0 if key == GANG else k0
            for mark in marks:
                if before < mark <= m[key]:
                    crossed.append((k, f"{key}{mark}"))
            if before >= marks[0] > m[key]:
                crossed.append((k, f"{key}_down"))
    return crossed


def shift(state: dict[str, dict[str, int]], district: str, gang: int = 0, corp: int = 0) -> None:
    m = state.get(district)
    if m is None:
        return
    m[GANG], m[CORP] = clamp(m[GANG] + gang), clamp(m[CORP] + corp)


def city_avg(state: dict[str, dict[str, int]], key: str = GANG) -> int:
    return int(sum(m[key] for m in state.values()) / max(1, len(state)))


def ambush_chance(content: Content, value: int) -> float:
    c = cfg(content)
    return max(0.0, (value - c.ambush_from) / 100 * c.ambush_k)


def ambush_danger(content: Content, value: int) -> int:
    c = cfg(content)
    return max(2, min(5, 2 + (value - c.ambush_from) // c.danger_step))


def ambush_extra(content: Content, value: int) -> int:
    """More fighters than a plain pack: one per `extra_step` above the line."""
    c = cfg(content)
    return max(0, (value - c.extra_from) // c.extra_step + 1) if value >= c.extra_from else 0


def sweep_chance(content: Content, corp: int) -> float:
    """Corporate security sweeps the residents: a scene or a fight."""
    c = cfg(content)
    return max(0.0, (corp - c.regime_from) / 100 * c.raid_k) if corp >= c.regime_from else 0.0


def heat_mult(content: Content, corp: int) -> float:
    c = cfg(content)
    return 1.0 + max(0, corp - c.tight_from) / 100 * c.heat_k


def fight_danger(content: Content, value: int) -> int:
    return max(2, min(5, 2 + value // 25))


def spawn_bonus(content: Content, state: dict[str, dict[str, int]], key: str = GANG) -> tuple[float, int]:
    """New gangs (or corps) come oftener and more of them live at once when the city is held by their kind."""
    c = cfg(content)
    avg = city_avg(state, key)
    return avg * c.spawn_per_point, avg // c.cap_step


def spawn_district(state: dict[str, dict[str, int]], rng: random.Random, key: str = GANG) -> str | None:
    keys = list(state)
    if not keys:
        return None
    return rng.choices(keys, weights=[5 + state[k][key] for k in keys])[0]


def word(gang: int, corp: int) -> str:
    if gang >= 75 and gang >= corp:
        return "улицы за бандами"
    if corp >= 75:
        return "район принадлежит корпорациям"
    if gang >= 50 and corp >= 50:
        return "война банд и корпораций"
    if gang >= 50:
        return "банды сильнее корпораций"
    if corp >= 50:
        return "корпорации скупают район"
    return "ничья земля"


# -- the map (owner request 25.10: «карту, чтобы мы понимали, насколько город захвачен») ----------------------------------

MAP_ROWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Север", ("northside", "watson", "north_oak")),
    ("Побережье и восток", ("waterfront", "little_europe", "hot_zone", "westbrook")),
    ("Центр", ("city_center", "university")),
    ("Юг", ("the_glen", "heywood", "santo_domingo")),
    ("Пасифика", ("pacifica", "dogtown", "combat_zone")),
    ("За городом", ("badlands",)),
)
SIDE_ICON = {"gang": "🟥", "gang_up": "🔴", "corp": "🟦", "corp_up": "🔵", "war": "🟪", "free": "⬜"}
SIDE_WORD = {"gang": "банды", "gang_up": "банды наступают", "corp": "корпорации", "corp_up": "корпорации наступают",
             "war": "война", "free": "ничья"}


def side(gang: int, corp: int) -> str:
    if gang >= 50 and corp >= 50:
        return "war"
    if gang >= 50:
        return "gang"
    if corp >= 50:
        return "corp"
    if max(gang, corp) < 30:
        return "free"
    return "gang_up" if gang >= corp else "corp_up"


def map_rows(content: Content, state: dict[str, dict[str, int]]) -> list[tuple[str, list[tuple[str, str, int, int]]]]:
    """[(area, [(district key, side, gang, corp)])] in map order; districts outside the layout go last."""
    placed: set[str] = set()
    out: list[tuple[str, list[tuple[str, str, int, int]]]] = []
    for area, keys in MAP_ROWS + (("Прочее", tuple(k for k in state if k not in {x for _, ks in MAP_ROWS for x in ks})),):
        row = [(k, side(state[k][GANG], state[k][CORP]), state[k][GANG], state[k][CORP]) for k in keys if k in state]
        placed |= {k for k, *_ in row}
        if row:
            out.append((area, row))
    return out
