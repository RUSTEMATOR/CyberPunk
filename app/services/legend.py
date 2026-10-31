"""Leaving as a legend (owner request 25.10, docs/PLAN_ENDGAME.md part 3).

Who may: a character with a legend title of an ambition, or one the city measures as outgrown (the challenge power),
or one past the third challenge. The character goes alive into the Hall of Legends (world_state `legends_hall`) and the
life closes like any other (the heir). The dynasty (world_state `dynasty` = {tg: {"legends": n}}) gives every next
heir of the player money and talent points per legend of the line, and makes the city harder for them (pressure).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.content.loader import Content
from app.db import repo
from app.services.game import ActiveCharacter

HALL, DYN = "legends_hall", "dynasty"
RULE = "для тех, кто исполнил амбицию, перерос город по оценке вызовов или прошёл третий вызов."


def cfg(content: Content):  # noqa: ANN201
    return content.balance.legend


async def eligible(session: AsyncSession, content: Content, ch: ActiveCharacter) -> bool:
    from app.engine import challenge as chal_eng
    from app.services import ambition, challenge

    if ambition.titles(ch.state):
        return True
    rec = (await challenge.book(session)).get(str(ch.id)) or {}
    return chal_eng.overpowered(rec.get("report") or {}, content) or int(rec.get("level", 1) or 1) > cfg(content).challenge_level


async def legends_of(session: AsyncSession, tg: int) -> int:
    return int(((await repo.ws_get(session, DYN, {}) or {}).get(str(tg)) or {}).get("legends", 0) or 0)


def heir_bonus_text(content: Content, n: int) -> str:
    c = cfg(content)
    return (f"легенд в роду станет {n}: каждый следующий наследник получает {c.heir_eddies * n} эдди на старт и "
            f"{c.heir_points * n} очк. атрибутов и талантов, а город с первого дня давит на него сильнее.")


def build_of(content: Content, st) -> dict:  # noqa: ANN001
    """The legend's fighter as it was on the day it left (docs/PLAN_ENDGAME2.md part 3: a challenge to a legend)."""
    from dataclasses import asdict

    from app.engine import psychosis

    c = psychosis.player_as_foe(st, content)
    c.hp = c.max_hp
    return asdict(c)


ACT_MAX = 2000  # owner request 27.10: was 500, «очень мало»


async def scene_payload(session: AsyncSession, content: Content, ch: ActiveCharacter, act: str) -> dict:
    """Owner request 27.10: what Claude knows for the last deed of a legend (docs/PLAN_LEGEND_SCENE.md)."""
    from app.engine import derived
    from app.services import ambition, chronicle, deeds

    st = ch.state
    base = await chronicle.payload(session, content, ch)
    notable = sorted((content.cyberware[c.key] for c in st.cyberware if c.key in content.cyberware),
                     key=lambda cw: -cw.price)[:8]
    mine = [deeds.plain(str(d.get("text") or "")) for d in await deeds.all_deeds(session)
            if int(d.get("char", 0) or 0) == ch.id][-10:]
    return {"kind": "legend_scene", "character": base["character"], "gender": base["gender"], "life": base["life"],
            "titles": ambition.titles(st), "deeds": mine, "recent": base.get("recent", [])[-6:],
            "chrome": {"implants": len(st.cyberware), "borg": derived.is_borg(st, content),
                       "notable": [cw.name for cw in notable]},
            "act": act.strip()[:ACT_MAX]}


def fallback_scene(st, act: str) -> str:  # noqa: ANN001
    name = f"{st.name} {st.surname}".strip()
    act = act.strip()[:ACT_MAX]
    if act:
        return (f"Последнее дело {name} город пересказывает по-разному, но в главном сходятся все: «{act}».\n\n"
                "Наутро у бара остаются только пустой стакан и следы на мокром асфальте. Имя уходит в Зал легенд, "
                "а улицы ещё долго спорят, как всё было на самом деле.")
    return (f"{name} уходит так же, как жил: без прощаний и без свидетелей. Наутро у бара остаются только пустой "
            "стакан и следы на мокром асфальте. Имя уходит в Зал легенд, а улицы ещё долго спорят, как всё было.")


async def write_scene(payload: dict, year: int, st) -> tuple[str, bool]:  # noqa: ANN001
    """(the scene, written by Claude?) — the ready-made text when the narrator is off or failed."""
    from app.narrator import narrator

    try:
        text = await narrator.generate_text("legend_scene", payload, year=year)
    except Exception:  # noqa: BLE001 - the legend leaves anyway
        text = None
    return (text, True) if text else (fallback_scene(st, str(payload.get("act") or "")), False)


async def post_scene(session: AsyncSession, ch: ActiveCharacter, scene: str) -> None:
    """The scene goes to the group as a permanent post, under the character's own avatar when there is one."""
    from app.bot import texts

    st = ch.state
    who = texts.esc(f"{st.name} {st.surname}".strip())
    payload = {"text": f"👑 <b>Легенда Найт-Сити: {who}</b>\n\n{texts.esc(scene)}", "ref": "legend_scene"}
    av = st.flags.get("avatar")
    if isinstance(av, dict) and av.get("file_id"):
        payload["avatar"] = str(av["file_id"])
    await repo.log_event(session, "legend", payload, character_id=ch.id, public=True)


async def retire(session: AsyncSession, content: Content, ch: ActiveCharacter, ending: str = "", ending_title: str = "",
                 scene: str = "", fallen: bool = False) -> None:
    from app.services import ambition, loud

    st = ch.state
    hall = list(await repo.ws_get(session, HALL, []) or [])
    titles = ambition.titles(st) + ([ending_title] if ending_title else [])
    try:
        build = build_of(content, st)
    except Exception:  # noqa: BLE001 — a broken build must not stop the life from closing
        build = None
    hall.append({"name": f"{st.name} {st.surname}".strip(), "tg": ch.player_tg_id, "char": ch.id, "titles": titles,
                 "age": st.age_years, "weeks": st.weeks_lived, "fame": st.fame, "cred": st.street_cred,
                 "date": utcnow().date().isoformat(), "ending": ending, "build": build,
                 **({"scene": scene[:4000]} if scene else {})})
    await repo.ws_set(session, HALL, hall[-200:])
    from app.services import stickers  # owner request 26.10: a sticker of the legend for the group's set

    e0 = next((x for x in content.balance.endings.items if x.key == ending), None) if ending else None
    await stickers.queue(session, {"name": f"{st.name} {st.surname}".strip(), "titles": titles, "ending": ending,
                                   "ending_name": e0.name if e0 else "",
                                   "years": f"{st.current_year - st.age_years}–{st.current_year}"})
    from app.services import lgcity, relic  # owner request 25.10: the relics go with the legend into the Hall

    e = next((x for x in content.balance.endings.items if x.key == ending), None) if ending else None
    await relic.to_hall(session, content, ch, lgcity.hall_key(hall[-1]), e.relic if e else "")
    dyn = dict(await repo.ws_get(session, DYN, {}) or {})
    rec = dict(dyn.get(str(ch.player_tg_id)) or {})
    rec["legends"] = int(rec.get("legends", 0) or 0) + 1
    rec["names"] = (list(rec.get("names") or []) + [f"{st.name} {st.surname}".strip()])[-20:]
    if ending:
        rec["gifts"] = (list(rec.get("gifts") or []) + [ending])[-10:]
    dyn[str(ch.player_tg_id)] = rec
    await repo.ws_set(session, DYN, dyn)
    if ending:  # the ending posts its own words and points (services/finale.py)
        return
    from app.services import season  # owner request 25.10

    await season.add_ch(session, content, ch, "legend", content.balance.season.pts.get("legend", 0))
    t = (" — " + ", ".join(titles)) if titles else ""
    if fallen:  # owner request 27.10: the raid boss taken by the hunters (services/pboss.py)
        await loud.post(session, ch.id, f"👑 {loud.who(st.name, st.surname)}{t} уходит легендой: город брал эту голову "
                                        "всем миром. Имя остаётся в Зале легенд Найт-Сити.", kind="legend", w=3)
        return
    await loud.post(session, ch.id, f"👑 {loud.who(st.name, st.surname)}{t} уходит легендой. Живым. Имя остаётся в Зале "
                                    "легенд Найт-Сити.", kind="legend", w=3)


async def hall(session: AsyncSession) -> list[dict]:
    return [dict(x) for x in (await repo.ws_get(session, HALL, []) or []) if isinstance(x, dict)]


async def apply_heir(session: AsyncSession, content: Content, tg: int, state) -> int:  # noqa: ANN001
    """The dynasty's gift to a new heir. Returns the number of legends of the line."""
    from app.engine import perks

    n = await legends_of(session, tg)
    if not n:
        return 0
    c = cfg(content)
    state.eddies += c.heir_eddies * n
    r = perks.rec(state, content)
    r["attr"] = int(r.get("attr", 0)) + c.heir_points * n
    r["perk"] = int(r.get("perk", 0)) + c.heir_points * n
    for key in ((await repo.ws_get(session, DYN, {}) or {}).get(str(tg)) or {}).get("gifts") or []:  # the endings of the line
        e = next((x for x in content.balance.endings.items if x.key == key), None)
        if e is None:
            continue
        g = e.gift
        state.eddies += g.eddies
        r["attr"] = int(r.get("attr", 0)) + g.attr
        r["perk"] = int(r.get("perk", 0)) + g.perk
        state.street_cred = min(content.balance.character.street_cred_max, state.street_cred + g.street_cred)
        state.fame += g.fame
    state.flags[perks.FLAG] = r
    state.flags["dynasty"] = n
    return n
