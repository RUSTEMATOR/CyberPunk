"""Getting rid of enemies (owner request 25.10: «очень мало способов убирать врагов … за деньги через фиксеров и самому»).

The enemies of the pressure (engine/pressure.py): `pr_enmity` (the grudges), `pr_hook` (whoever holds you), the nemesis,
the factions that hate you (standing ≤ hostile_rep), the bounty on your head.

By money, through the fixer of the district: a contract on the nemesis, settling the grudges, a truce with a faction,
buying the hook out, taking the bounty off.
By hand: hunting the nemesis down (a check, then the fight now), a vendetta on the most hostile faction (a fight),
leaning on the patron of the hook (a check).
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import checks
from app.engine import pressure as pe
from app.engine.effects import apply_effects
from app.engine.state import FLAG_PENDING_COMBAT
from app.services.game import ActiveCharacter, save_active

SRC_VENDETTA = "vendetta"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.enemies


def hostile(content: Content, ch: ActiveCharacter, year: int) -> list[tuple[str, str, int]]:
    """(faction key, name, standing) of the gangs and corps that hate the character, the worst first."""
    out = []
    for k, v in (ch.state.faction_rep or {}).items():
        if v > cfg(content).hostile_rep:
            continue
        name = content.gangs[k].name if k in content.gangs else content.corps[k].name if k in content.corps else ""
        if name:
            out.append((k, name, int(v)))
    return sorted(out, key=lambda x: x[2])


def prices(content: Content, ch: ActiveCharacter, bounty: int) -> dict[str, int]:
    c = cfg(content)
    st = ch.state
    nem = pe.nemesis(st)
    return {
        "contract": c.contract_base * (int(nem.get("lvl", 0) or 0) + 1) if nem else 0,
        "settle": c.settle_per_level * pe.enmity(st),
        "hook": c.hook_price * pe.hook(st),
        "bounty": bounty * c.bounty_pct // 100 if bounty else 0,
    }


def truce_price(content: Content, rep: int) -> int:
    return cfg(content).truce_per_point * max(0, -rep)


def _block(ch: ActiveCharacter) -> str | None:
    from app.engine import jail as jail_eng
    from app.services import events as ev_service

    if jail_eng.in_jail(ch.state):
        return "jail"
    if ev_service.has_pending(ch):
        return "busy"
    return None


async def by_money(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                   what: str, arg: str | None = None) -> tuple[str | None, str]:
    """Through the fixer. Returns (blocked, line)."""
    from app.services import bounties, game

    c = cfg(content)
    st = ch.state
    r = _block(ch)
    if r:
        return r, ""
    fixer = game.district_fixer(content, st.district, year)
    fname = fixer.name if fixer else "Фиксер"
    bounty = await bounties.on(session, ch.id)
    pr = prices(content, ch, bounty)
    if what == "truce":
        rep = int((st.faction_rep or {}).get(arg or "", 0))
        price = truce_price(content, rep)
        if not arg or rep > c.hostile_rep:
            return "none", ""
    else:
        price = pr.get(what, 0)
        if not price:
            return "none", ""
    if st.eddies < price:
        return "eddies", ""
    fx: dict = {"eddies": -price}
    line = ""
    if what == "contract":
        nem = pe.nemesis(st)
        if rng.random() < c.contract_chance:
            st.flags.pop(pe.F_NEM, None)
            fx["enmity"] = -1
            line = f"{fname} звонит через два дня: «{nem['name']} больше не проблема. Не спрашивай как»."
        else:
            nem["lvl"] = min(content.balance.pressure.nem_danger_max, int(nem.get("lvl", 0) or 0) + 1)
            st.flags[pe.F_NEM] = nem
            line = (f"{fname} разводит руками: исполнитель не вернулся. {nem['name']} знает, кто платил, "
                    "и стал злее. Деньги не вернуть.")
    elif what == "settle":
        fx["enmity"] = -min(pe.enmity(st), c.settle_levels)
        line = f"{fname} обходит обиженных с конвертами и извинениями. Врагов меньше."
    elif what == "hook":
        st.flags[pe.F_HOOK] = 0
        line = f"{fname} выкупает твой долг у тех, кто держал тебя на крючке. Теперь ты никому не должен услуг."
    elif what == "bounty":
        b = await bounties.all_bounties(session)
        b.pop(str(ch.id), None)
        from app.db import repo

        await repo.ws_set(session, bounties.KEY, b)
        line = f"{fname} договаривается с заказчиками: награда за твою голову снята."
    elif what == "truce":
        fx[f"faction_rep:{arg}"] = c.truce_gain
        name = content.gangs[arg].name if arg in content.gangs else content.corps[arg].name
        line = f"{fname} устраивает встречу с людьми «{name}». Деньги, рукопожатие, перемирие."
    apply_effects(st, fx, content, rng, year)
    await save_active(session, ch, tz)
    return None, line + f" −{price} эдди."


async def by_hand(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                  what: str):  # noqa: ANN201
    """Yourself. Returns a block reason, (ok, check, line) or a combat Step."""
    from app.services import ap as ap_services
    from app.services import combat
    from app.services import pressure

    c = cfg(content)
    st = ch.state
    r = _block(ch)
    if r:
        return r
    if what == "hunt":
        nem = pe.nemesis(st)
        if nem is None:
            return "none"
        if not ap_services.can_spend(ch, c.hunt_ap):
            return "ap"
        ap_services.spend(ch, content, c.hunt_ap)
        lvl = int(nem.get("lvl", 0) or 0)
        chk = checks.roll_check(st, content, rng, "INT", "persuasion", c.hunt_dv + 2 * lvl, ("social",))
        await save_active(session, ch, tz)
        return await combat.begin(session, content, ch, rng, year, tz,
                                  faction=pressure._faction_ok(content, str(nem.get("faction", "")), year),  # noqa: SLF001
                                  danger=pe.nem_danger(content, nem),
                                  src={"src": pressure.SRC, "foe": str(nem["name"])},
                                  pre="" if chk.ok else "talk_fail")  # a failed hunt walks into an ambush
    if what == "vendetta":
        foes = hostile(content, ch, year)
        if not foes:
            return "none"
        if not ap_services.can_spend(ch, c.vendetta_ap):
            return "ap"
        ap_services.spend(ch, content, c.vendetta_ap)
        key = foes[0][0]
        await save_active(session, ch, tz)
        return await combat.begin(session, content, ch, rng, year, tz, faction=key, danger=c.vendetta_danger,
                                  src={"src": SRC_VENDETTA, "target": key})
    if what == "lean":
        if not pe.hook(st):
            return "none"
        if not ap_services.can_spend(ch, c.lean_ap):
            return "ap"
        ap_services.spend(ch, content, c.lean_ap)
        chk = checks.roll_check(st, content, rng, "COOL", "persuasion", c.lean_dv, ("social",))
        if chk.ok:
            apply_effects(st, {"hook": -1}, content, rng, year)
            line = "Ты приходишь к тому, кто держит крючок, и объясняешь, что долг закрыт. Он соглашается. Крючок −1."
        else:
            apply_effects(st, {"enmity": 1}, content, rng, year)
            line = "Разговор не складывается. Крючок на месте, а у тебя ещё один враг."
        await save_active(session, ch, tz)
        return chk.ok, chk, line
    return "none"


async def vendetta_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                        tz: str, result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb

    c = cfg(content)
    key = str(ctx.get("target", ""))
    name = content.gangs[key].name if key in content.gangs else content.corps[key].name if key in content.corps else key
    if result != cb.WON:
        apply_effects(ch.state, {"enmity": 1}, content, rng, year)
        await save_active(session, ch, tz)
        return [f"Вендетта против «{name}» сорвалась. Они смеются, врагов +1."]
    apply_effects(ch.state, {"enmity": -c.vendetta_enmity, f"faction_rep:{key}": c.vendetta_rep, "street_cred": 2, "heat": 1},
                  content, rng, year)
    await save_active(session, ch, tz)
    return [f"Ты вырезаешь тех из «{name}», кто держал на тебя зуб. Остальные решают, что связываться дороже: "
            f"врагов −{c.vendetta_enmity}, с «{name}» перемирие страха."]


def enemies_present(content: Content, ch: ActiveCharacter, year: int, bounty: int) -> bool:
    st = ch.state
    return bool(pe.nemesis(st) or pe.enmity(st) or pe.hook(st) or bounty or hostile(content, ch, year)
                or st.flags.get(FLAG_PENDING_COMBAT))
