"""A raid on a rival megacorp (owner request 25.10: «если я член корпорации … нападать на другие корпорации. Я хочу
сражаться, дай мне сражаться»).

Who may: a character with a megacorp employer (career), an employee of a player's megacorp, a member of a board or the
head of one's own megacorp. Three kinds of target, each a real fight against the corp's security:
  depot — a warehouse, danger by level;
  lab   — a lab, danger 5, scaled to the challenge ladder one step below the character's own;
  tower — the tower, scaled to the character's challenge level (at least 2), with a counter against the build.
Three times a day per corp (any site, balance corp_raid.per_day). The fight: services/combat.begin with src "craid"; raid_over pays out.
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import challenge as chal
from app.engine.effects import apply_effects
from app.engine.state import FLAG_EMPLOYER, CharacterState
from app.services.game import ActiveCharacter, save_active

SRC = "craid"
FLAG_DAY = "craid_day"  # {corp key: {"d": kyiv date, "n": raids on it that day}} (a bare date is the old form, one raid)
SITES = ("depot", "lab", "tower")
SITE_RU = {"depot": "Склад", "lab": "Лаборатория", "tower": "Башня"}
SITE_DESC = {"depot": "охрана склада, опасность по уровню",
             "lab": "охрана лаборатории: опасность 5, усиленная под сильных",
             "tower": "штурм башни: отряд вызова твоего уровня, с контрмерой против билда"}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.corp_raid


def member_of(st: CharacterState, content: Content) -> tuple[str, str] | None:
    """(the canon megacorp the character serves or "", how the screen names the membership), or None."""
    emp = str(st.flags.get(FLAG_EMPLOYER) or "")
    corp = content.corps.get(emp)
    if corp is not None and corp.kind == "megacorp":
        return emp, f"сотрудник {corp.name}"
    pc = st.flags.get("pcorp")
    if isinstance(pc, dict) and pc.get("corp"):
        return "", f"сотрудник «{pc['corp']}»"
    steps = [s.key for s in content.balance.economy.empire]
    n = int(st.flags.get("empire", 0) or 0)
    if "megacorp" in steps and n > steps.index("megacorp"):
        return "", "глава своей мегакорпорации"
    if "board" in steps and n > steps.index("board"):
        return "", "член совета директоров"
    return None


async def allowed(session: AsyncSession, content: Content, ch: ActiveCharacter) -> tuple[str, str] | None:
    """A member of a corp, or (owner request 25.10 «выносить корпорации в городе») a character the city has outgrown:
    a merc who answers to nobody."""
    mem = member_of(ch.state, content)
    if mem is not None:
        return mem
    from app.services import escort

    if await escort.eligible(session, content, ch):
        return "", "сам по себе: наёмник, которого не купить"
    return None


def targets(content: Content, st: CharacterState, year: int) -> list[str]:
    from app.engine import combat as cb

    mine = (member_of(st, content) or ("", ""))[0]
    out = []
    for k, c in content.available("corps", year).items():
        if c.kind == "megacorp" and k != mine and cb._faction_pool(content, k, year):  # noqa: SLF001
            out.append(k)
    return out


def raids_today(st: CharacterState, key: str, today: str) -> int:
    rec = (st.flags.get(FLAG_DAY) or {}).get(key)
    if isinstance(rec, dict):
        return int(rec.get("n", 0) or 0) if rec.get("d") == today else 0
    return 1 if rec == today else 0


def depot_danger(content: Content, st: CharacterState) -> int:
    from app.engine import perks

    lv = int(perks.rec(st, content).get("level", 1) or 1)
    return max(2, min(5, 2 + lv // 6))


async def ladder(session: AsyncSession, ch: ActiveCharacter) -> int:
    from app.services import challenge

    return int((await challenge.record(session, ch)).get("level", 1) or 1)


def site_level(site: str, ladder_lv: int) -> int:
    """The challenge level the site is scaled to (0 = a plain fight)."""
    if site == "tower":
        return max(2, ladder_lv)
    if site == "lab":
        return ladder_lv - 1 if ladder_lv >= 3 else 0
    return 0


async def raid(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               target: str, site: str):  # noqa: ANN201
    """Start the fight, or return a block reason (str)."""
    from app.clock import kyiv_date
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services import combat
    from app.services import events as ev_service
    from app.services.combat import _store

    c = cfg(content)
    st = ch.state
    if await allowed(session, content, ch) is None:
        return "member"
    if site not in SITES or target not in targets(content, st, year):
        return "gone"
    from app.services import corpstand

    if corpstand.down(corpstand.rec_of(content, await corpstand.book(session), target)):
        return "down"
    if jail_eng.in_jail(st):
        return "jail"
    if ev_service.has_pending(ch):
        return "busy"
    today = kyiv_date(tz)
    done = raids_today(st, target, today)
    if done >= c.per_day:  # owner request 25.10: three raids a day on one corp
        return "today"
    ap = int(c.ap.get(site, 2))
    if not ap_services.can_spend(ch, ap):
        return "ap"
    ladder_lv = await ladder(session, ch)
    if site == "tower" and ladder_lv < 2:
        return "tower"
    ap_services.spend(ch, content, ap)
    days = dict(st.flags.get(FLAG_DAY) or {})
    days[target] = {"d": today, "n": done + 1}
    st.flags[FLAG_DAY] = days
    lv = site_level(site, ladder_lv)
    danger = depot_danger(content, st) if site == "depot" else 5
    step = await combat.begin(session, content, ch, rng, year, tz, faction=target, danger=danger,
                              src={"src": SRC, "target": target, "site": site, "lv": lv})
    if lv >= 2:
        counter = rng.choice(chal.COUNTERS) if site == "tower" else ""
        boss = f"Начальник охраны {content.corps[target].name}"
        chal.scale(step.cs, content, lv, counter, rng, year, boss)
        if site == "tower":  # owner request 25.10: the tower is an elite fight
            from app.engine import elite

            elite.setup(step.cs, content, rng, year, boss=boss)
        _store(ch, step.cs)
    await save_active(session, ch, tz)
    return step


async def raid_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                    tz: str, result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb
    from app.services import loud

    c = cfg(content)
    target = str(ctx.get("target", ""))
    site = str(ctx.get("site", "depot"))
    corp = content.corps.get(target)
    if corp is None or site not in SITES:
        return []
    mine = (member_of(ch.state, content) or ("", ""))[0]
    lv = int(ctx.get("lv", 0) or 0)
    if result != cb.WON:
        apply_effects(ch.state, {f"faction_rep:{target}": -5, "heat": 1, "enmity": 1}, content, rng, year)
        await save_active(session, ch, tz)
        return [f"Налёт на {corp.name} сорван. Охрана знает твоё лицо, а служба безопасности уже пишет отчёт. "
                f"Корпорация не пострадала."]
    lo, hi = c.loot[site]
    loot = rng.randint(lo, hi) * max(1, chal.mult(lv) // 2 if lv >= 2 else 1)
    loot = min(loot, c.loot_cap)
    fx: dict = {"eddies": loot, "street_cred": int(c.cred.get(site, 2)), f"faction_rep:{target}": int(c.rep_target.get(site, -10)),
                "heat": c.heat, "enmity": 1}
    if mine:
        fx[f"faction_rep:{mine}"] = c.rep_own
    if site == "tower":
        fx["fame"] = 2
    if rng.random() < c.nemesis_chance:
        fx[f"flag_set:nem_spawn_{target}"] = True
    from app.services import ambition  # owner request 25.10: the ambition «erase a corp» counts the raids

    ambition.count(ch.state, "raids", target)
    from app.services import season  # owner request 25.10

    await season.add_ch(session, content, ch, "craid", content.balance.season.pts.get(f"craid_{site}", 0))
    if site == "tower":
        ambition.count(ch.state, "tower", target)
    apply_effects(ch.state, fx, content, rng, year)
    await save_active(session, ch, tz)
    from app.services import control  # owner request 25.10: a raid on a corp loosens its hold on the district

    where = control.corp_district(content, target, ch.state.district)
    cc = content.balance.control
    if where:
        await control.adjust(session, content, where, gang=cc.corpraid_gang, corp=cc.corpraid_corp)
    from app.services import corpstand  # owner request 25.10: the damage to the corp is seen

    hurt = await corpstand.hit(session, content, ch, target, site, lv, rng, year)
    from app.engine import grammar  # owner request 26.10

    what = grammar.say(content, rng, f"raid_{site}")
    if site == "tower":
        await loud.post(session, ch.id, "🏢 " + grammar.say(content, rng, "tower_post", corp=corp.name,
                                                             who=loud.who(ch.state.name, ch.state.surname)),
                        kind="tower", w=2)
    out = [f"{what}: +{loot} эдди." + (" Своя корпорация довольна." if mine else "")]
    out += hurt
    if where:
        out.append(f"Хватка {corp.name} в районе {content.districts[where].name} слабеет "
                   f"({f'{cc.corpraid_corp:+d}'.replace('-', '−')}), банды района поднимают голову.")
    out.append(f"Отношение {corp.name} " + f"{fx[f'faction_rep:{target}']:+d}".replace("-", "−") + f", розыск +{c.heat}, враги +1."
               + (" Служба безопасности назначила за тобой охотника." if fx.get(f"flag_set:nem_spawn_{target}") else ""))
    return out


# -- the corps that come and go (services/dyn.py): a strike on the office ---------------------------------------------

SRC_DYN = "dyn_strike"


async def dyn_corps(session: AsyncSession) -> list[dict]:
    from app.services import dyn

    return [f for f in dyn.alive(await dyn.all_factions(session)) if f.get("kind") == "corp"]


async def strike_dyn(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, fid: int):  # noqa: ANN201
    from app.clock import kyiv_date
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services import combat, dyn
    from app.services import events as ev_service

    c = cfg(content)
    st = ch.state
    if await allowed(session, content, ch) is None:
        return "member"
    f = dyn.find(await dyn.all_factions(session), fid)
    if f is None or not f.get("alive") or f.get("kind") != "corp":
        return "gone"
    if jail_eng.in_jail(st):
        return "jail"
    if ev_service.has_pending(ch):
        return "busy"
    today = kyiv_date(tz)
    key = f"dyn{fid}"
    done = raids_today(st, key, today)
    if done >= c.per_day:
        return "today"
    if not ap_services.can_spend(ch, c.dyn_ap):
        return "ap"
    ap_services.spend(ch, content, c.dyn_ap)
    days = dict(st.flags.get(FLAG_DAY) or {})
    days[key] = {"d": today, "n": done + 1}
    st.flags[FLAG_DAY] = days
    danger = 5 if int(f["power"]) > 50 else 4
    step = await combat.begin(session, content, ch, rng, year, tz, faction=dyn.base_faction(content, f, year),
                              danger=danger, src={"src": SRC_DYN, "dyn": fid, "dyn_name": f["name"], "dyn_kind": "corp"})
    await save_active(session, ch, tz)
    return step


async def strike_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                      tz: str, result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb
    from app.services import control, dyn, loud, season

    c = cfg(content)
    items = await dyn.all_factions(session)
    f = dyn.find(items, int(ctx.get("dyn", 0) or 0))
    if f is None or not f.get("alive"):
        return []
    if result != cb.WON:
        f["power"] = min(100, int(f["power"]) + 5)
        await dyn._save(session, items)  # noqa: SLF001
        return [f"Удар по «{f['name']}» сорван. Корпорация нанимает ещё охраны: сила {f['power']}/100."]
    lo, hi = c.dyn_loot
    loot = rng.randint(lo, hi)
    apply_effects(ch.state, {"eddies": loot, "street_cred": 2, "heat": c.heat}, content, rng, year)
    f["power"] = max(0, int(f["power"]) - c.dyn_power)
    lines = [f"Офис «{f['name']}» разнесён: +{loot} эдди. Урон корпорации: −{c.dyn_power}, сила {f['power']}/100."]
    await season.add_ch(session, content, ch, "craid", content.balance.season.pts.get("craid_lab", 0))
    if f["power"] <= 0:
        from app.clock import utcnow

        f.update(alive=False, died=utcnow().date().isoformat(), how="player")
        cc = content.balance.control
        await control.adjust(session, content, str(f.get("district", "")), gang=cc.office_gang, corp=cc.office_corp)
        apply_effects(ch.state, {"fame": 2, "street_cred": 3}, content, rng, year)
        await season.add_ch(session, content, ch, "lair", content.balance.season.pts.get("lair", 0))
        await loud.post(session, ch.id, f"🏢 {loud.who(ch.state.name, ch.state.surname)} стирает корпорацию "
                                        f"«{f['name']}» с карты города.", kind="dyn_gone", w=2)
        lines.append(f"🏢 «{f['name']}» больше нет: офисы закрыты, счета заморожены, сотрудники ищут работу. Слава +2.")
    await dyn._save(session, items)  # noqa: SLF001
    await save_active(session, ch, tz)
    return lines
