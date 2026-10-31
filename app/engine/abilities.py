"""What the chrome lets you do (owner request 26.10, docs/PLAN_CHROME.md). Pure.

- An implant brings its `abilities` (balance.chrome_abilities): buttons on «⚙️ Возможности хрома» that play their own
  event, and choices in events only their owners see (EventChoice.ability, EventConditions.abilities).
- An implant belongs to a build line (`build`, balance.chrome_sets): pieces of one line give a bonus at 2 / 4 / 6
  installed — modifiers, combat specials (engine/perks.specials keys) and more abilities.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterator

from app.content.loader import Content
from app.engine.state import CharacterState


def set_counts(st: CharacterState, content: Content) -> Counter[str]:
    out: Counter[str] = Counter()
    for it in st.cyberware:
        cw = content.cyberware.get(it.key)
        if cw is not None and cw.build:
            out[cw.build] += 1
    return out


def set_bonuses(st: CharacterState, content: Content) -> list[tuple[str, int, Any]]:
    """(set key, threshold, bonus) of every bonus the installed pieces reach."""
    out = []
    for key, n in set_counts(st, content).items():
        cs = content.balance.chrome_sets.get(key)
        if cs is None:
            continue
        for need, bonus in sorted(cs.bonuses.items()):
            if n >= int(need):
                out.append((key, int(need), bonus))
    return out


def owned(st: CharacterState, content: Content) -> dict[str, list[str]]:
    """Ability key -> what gives it (implant names, set names)."""
    out: dict[str, list[str]] = {}
    for it in st.cyberware:
        cw = content.cyberware.get(it.key)
        if cw is None:
            continue
        for a in cw.abilities:
            out.setdefault(a, []).append(cw.name)
    for key, need, bonus in set_bonuses(st, content):
        for a in bonus.abilities:
            out.setdefault(a, []).append(f"{content.balance.chrome_sets[key].name} ({need})")
    return {k: v for k, v in out.items() if k in content.balance.chrome_abilities}


def family(content: Content, key: str) -> str:
    ab = content.balance.chrome_abilities.get(key)
    return (ab.family if ab is not None and ab.family else key)


def mine_for(st: CharacterState, content: Content, key: str, have: dict[str, list[str]] | None = None) -> str | None:
    """The owned ability that answers `key`: the ability itself or one of its family (owner request 26.10)."""
    have = owned(st, content) if have is None else have
    if key in have:
        return key
    return next((k for k in sorted(have) if family(content, k) == key), None)


def has(st: CharacterState, content: Content, key: str) -> bool:
    return mine_for(st, content, key) is not None


_RANKS: dict[int, dict[str, int]] = {}


def line_rank(content: Content, cw_key: str) -> int:
    """Versions of one implant line by era share their ability; the later the version, the higher the rank (1..5)."""
    table = _RANKS.get(id(content))
    if table is None:
        groups: dict[tuple[str, tuple[str, ...]], list[Any]] = {}
        for cw in content.cyberware.values():
            if cw.exclusive_group and cw.abilities:
                groups.setdefault((cw.exclusive_group, tuple(cw.abilities)), []).append(cw)
        table = {}
        for items in groups.values():
            items.sort(key=lambda c: (c.available_from or 0, c.price))
            for i, cw in enumerate(items):
                table[cw.key] = i + 1
        _RANKS.clear()
        _RANKS[id(content)] = table
    return table.get(cw_key, 1)


def ranks(st: CharacterState, content: Content) -> dict[str, int]:
    """Ability key -> its rank: the best installed version of its line; set abilities are rank 3."""
    out: dict[str, int] = {}
    for it in st.cyberware:
        cw = content.cyberware.get(it.key)
        if cw is None:
            continue
        for a in cw.abilities:
            out[a] = max(out.get(a, 0), line_rank(content, cw.key))
    for _, _, bonus in set_bonuses(st, content):
        for a in bonus.abilities:
            out[a] = max(out.get(a, 0), 3)
    return out


def mod_sources(st: CharacterState, content: Content) -> Iterator[tuple[str, dict[str, int]]]:
    for key, need, bonus in set_bonuses(st, content):
        if bonus.mods:
            yield f"set:{key}:{need}", dict(bonus.mods)


def specials(st: CharacterState, content: Content) -> dict[str, int]:
    out: dict[str, int] = {}
    for _, _, bonus in set_bonuses(st, content):
        for k, v in bonus.specials.items():
            out[k] = out.get(k, 0) + int(v)
    return out


_SCALED = ("eddies", "hp", "mood", "humanity", "xp", "street_cred", "fame")
_SCALED_PREFIX = ("skill_xp:", "rel_npc", "faction_rep:", "fixer_rel:")


def scale(content: Content, effects: dict[str, Any], rank: int) -> dict[str, Any]:
    """The chrome choice of a higher rank pays more: every positive reward (money, xp, health, mood, humanity,
    standing; owner request 27.10) grows by balance.chrome_rank.pay_step a rank."""
    if rank <= 1:
        return effects
    k = 1 + content.balance.chrome_rank.pay_step * (rank - 1)
    out = dict(effects)
    for key, v in effects.items():
        if not (key in _SCALED or key.startswith(_SCALED_PREFIX)):
            continue
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            out[key] = int(round(v * k))
    return out


def cooldown(content: Content, key: str, rank: int) -> int:
    ab = content.balance.chrome_abilities[key]
    cut = 1 if rank >= content.balance.chrome_rank.cd_cut_at else 0
    return max(1, ab.cooldown_days - cut)
