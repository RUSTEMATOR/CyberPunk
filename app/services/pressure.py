"""The pressure (owner request 24.10): the threat director of every «Прожить месяц». See engine/pressure.py.

The month: the hook and the enmity cool down slowly; the protection is paid; a new nemesis is named; the base threats
of the tier are rolled (fewer under a protection); the holders of the hook demand; the enemies take revenge; the
nemesis comes back as a real fight (src "nemesis", fight_over below).
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import pressure as eng
from app.engine.effects import apply_effects
from app.engine.state import FLAG_PENDING_COMBAT
from app.services.game import ActiveCharacter
from app.services.week import pending_events, set_pending

SRC = "nemesis"
DEFAULT_FACTIONS = ("maelstrom", "tyger_claws", "valentinos", "sixth_street", "animals", "scavengers")


async def _megacorp(session: AsyncSession, ch: ActiveCharacter) -> bool:
    from app.services import bigmoney

    return str(ch.player_tg_id) in await bigmoney.corps(session)


async def snapshot(session: AsyncSession, content: Content, ch: ActiveCharacter) -> tuple[eng.Points, str]:
    pts = eng.points(ch.state, content, megacorp=await _megacorp(session, ch))
    pts.legend = await _legend(session, content, ch)
    return pts, eng.tier_of(content, pts, ch.state, legend=pts.legend)


async def _legend(session: AsyncSession, content: Content, ch: ActiveCharacter) -> bool:
    """Owner request 25.10: the city's measure of the build (services/challenge.py) says the character outgrew it."""
    from app.engine import challenge as chal_eng
    from app.services import challenge

    rec = (await challenge.book(session)).get(str(ch.id)) or {}
    return chal_eng.overpowered(rec.get("report") or {}, content)


def _faction_ok(content: Content, faction: str, year: int) -> str:
    from app.engine import combat as cb

    for f in (faction, *DEFAULT_FACTIONS, "ncpd"):
        if f and cb._faction_pool(content, f, year):  # noqa: SLF001
            return f
    return "ncpd"


def _spawn_nemesis(content: Content, ch: ActiveCharacter, rng: random.Random, faction: str) -> str:
    c = eng.cfg(content)
    name = rng.choice(c.nem_names) if c.nem_names else "Безымянный"
    ch.state.flags[eng.F_NEM] = {"name": name, "faction": faction, "lvl": 0}
    who = c.nem_titles.get(faction, "человек, которого ты задел")
    return f"🩸 У тебя появился заклятый враг: {name}, {who}. Такое не забывают, встреча будет скоро."


async def press_hook(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> list[str]:
    st = ch.state
    c = eng.cfg(content)
    if not c.pools:
        return []
    lines: list[str] = []
    n = int(st.flags.get(eng.F_N, 0) or 0) + 1
    st.flags[eng.F_N] = n
    if c.decay_months and n % c.decay_months == 0:
        for fl in (eng.F_HOOK, eng.F_ENMITY):
            if int(st.flags.get(fl, 0) or 0) > 0:
                st.flags[fl] = int(st.flags[fl]) - 1
    for who, k in c.krysha.items():  # an event agreed to a protection
        if st.flags.pop(eng.KRYSHA_PREFIX + who, None):
            st.flags[eng.F_KRYSHA] = {"who": who}
            lines.append(f"🛡 Теперь у тебя крыша: {k.name}. Платить каждый месяц. Рэкет к тебе больше не ходит.")
    who, k = eng.krysha(st, content)
    if k is not None:
        fee = eng.krysha_fee(content, st)
        if st.eddies >= fee:
            apply_effects(st, {"eddies": -fee}, content, rng, year)
            lines.append(f"🛡 Крыша ({k.name}): −{fee} эдди за месяц защиты.")
        else:
            st.flags.pop(eng.F_KRYSHA, None)
            apply_effects(st, {"enmity": 1}, content, rng, year)
            lines.append(f"🛡 Платить крыше ({k.name}) нечем. Защита ушла и обиделась: врагов +1.")
    spawn = st.flags.pop(eng.F_NEM_SPAWN, None)
    for key in [k for k in st.flags if k.startswith(eng.F_NEM_SPAWN + "_")]:  # flag_set:nem_spawn_<faction>
        st.flags.pop(key, None)
        spawn = key[len(eng.F_NEM_SPAWN) + 1:]
    if spawn and eng.nemesis(st) is None:
        lines.append(_spawn_nemesis(content, ch, rng, str(spawn) if isinstance(spawn, str) else rng.choice(DEFAULT_FACTIONS)))
    pts, tier = await snapshot(session, content, ch)
    queue: list[str] = []

    def add(t: str, kind: str) -> None:
        e = eng.pick(content, st, t, kind, rng, queue)
        if e:
            queue.append(e)
            eng.remember(content, st, e)

    def base() -> None:
        low = eng.lower_tier(content, tier)
        add(low if low and rng.random() < c.lower_tier_chance else tier, "base")

    if rng.random() < eng.base_chance(content, st, pts):
        base()
    if pts.total >= c.second_from and rng.random() < c.second_chance:
        base()
    if eng.hook(st) and rng.random() < eng.hook(st) * c.hook_chance_per:
        add(tier, "demand")
    nem = eng.nemesis(st)
    revenge = eng.enmity(st) and rng.random() < eng.enmity(st) * c.enmity_chance_per
    if revenge and not (nem and rng.random() < 0.5):
        add(tier, "revenge")
        revenge = False
    if nem and (revenge or rng.random() < c.nem_return_chance) and not st.flags.get(FLAG_PENDING_COMBAT):
        st.flags[FLAG_PENDING_COMBAT] = {"src": SRC, "faction": _faction_ok(content, str(nem.get("faction", "")), year),
                                         "danger": eng.nem_danger(content, nem), "foe": str(nem["name"])}
        lines.append(f"🩸 {nem['name']} выходит на охоту за тобой."
                     + (" Злее и сильнее, чем в прошлый раз." if int(nem.get("lvl", 0) or 0) else ""))
    if queue:
        set_pending(ch, pending_events(ch) + [e for e in queue if e not in pending_events(ch)])
    return lines


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb

    st = ch.state
    nem = eng.nemesis(st)
    if nem is None:
        return []
    c = eng.cfg(content)
    if result == cb.WON:
        st.flags.pop(eng.F_NEM, None)
        apply_effects(st, {"street_cred": c.nem_cred_win, "enmity": -1}, content, rng, year)
        return [f"🩸 {nem['name']} больше не вернётся. Улица это запомнит: репутация +{c.nem_cred_win}."]
    nem["lvl"] = min(c.nem_danger_max, int(nem.get("lvl", 0) or 0) + 1)
    st.flags[eng.F_NEM] = nem
    return [f"🩸 {nem['name']} уходит с поля боя и теперь ещё злее. В следующий раз будет тяжелее."]


def profile_line(content: Content, st, tier: str) -> str:  # noqa: ANN001
    """Owner request 24.10: the pressure in the profile."""
    bits = [f"Давление: <b>{eng.tier_name(content, tier)}</b>"]
    if eng.hook(st):
        bits.append(f"крючок {eng.hook(st)}")
    if eng.enmity(st):
        bits.append(f"враги {eng.enmity(st)}")
    _, k = eng.krysha(st, content)
    if k is not None:
        bits.append(f"крыша: {k.name} ({eng.krysha_fee(content, st)}/мес, рэкет не ходит)")
    nem = eng.nemesis(st)
    if nem:
        bits.append(f"заклятый враг: {nem['name']} (ур. {int(nem.get('lvl', 0) or 0) + 1})")
    return "⚖️ " + " · ".join(bits)
