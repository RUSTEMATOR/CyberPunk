"""A player's character as the raid boss with a bounty on the head (owner request 27.10, docs/PLAN_PBOSS.md).

State: world_state `pboss` = {cid, tg, name, bounty, since, hits: {tg: {name, dmg, n}}, last: {tg: ts}}.
Every hunter may attack the boss once in a while, and the boss cannot refuse (no «out of pvp», no newbie guard, no
pair pause). Every attack is a real turn-based fight with the boss's own fighter (owner request 27.10 «пошаговое пвп
как полноценный бой»). Owner request 27.10, later: «я же могу умереть, почему мой нападающий не может … я от них бегаю
по городу, они меня должны вынести за один бой; если дадут уйти, я могу вылечиться» — the boss fights on the real
health of the character (what a fight takes stays, any healing between the fights counts), the hunter can die like in
any fight (the zero-hp saves, Trauma Team), and the boss falls only when a hunter takes the fighter down. The fallen
boss leaves as a legend (owner request 27.10): the Hall of legends and a scene written by Claude, posted to the group.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import Character, PlayerMail
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

log = logging.getLogger(__name__)

KEY = "pboss"
SCENES = "pboss_scenes"           # the legends of fallen bosses waiting for their scene (the job writes them)
SEED_FLAG = "pboss_seed_2710"     # the first boss, asked by the owner on 27.10
SEED_TG = 296599159

_CACHE: dict[str, Any] | None = None   # the active boss for the city screen (one process)
POSTS: list[str] = []                  # group posts made inside a fight's end, sent by the job

BLOCK_AWAY = "away"  # owner request 30.10: the boss lives in another city
BLOCK_NONE, BLOCK_SELF, BLOCK_JAIL, BLOCK_CD, BLOCK_AP, BLOCK_GONE, BLOCK_BUSY = (
    "none", "self", "jail", "cd", "ap", "gone", "busy")
SRC = "pboss"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.pboss


def cached() -> dict[str, Any] | None:
    return _CACHE


async def load(session: AsyncSession) -> dict[str, Any] | None:
    global _CACHE
    rec = await repo.ws_get(session, KEY, None)
    _CACHE = dict(rec) if isinstance(rec, dict) and rec.get("cid") else None
    return _CACHE


async def _store(session: AsyncSession, rec: dict[str, Any] | None) -> None:
    global _CACHE
    await repo.ws_set(session, KEY, rec or {})
    _CACHE = dict(rec) if rec else None


def tag(tg: int, name: str) -> str:
    from app.bot import texts

    return f'<a href="tg://user?id={int(tg)}">{texts.esc(name)}</a>'


def money(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


async def boss_of(session: AsyncSession, rec: dict[str, Any] | None) -> ActiveCharacter | None:
    from app.services import game

    if rec is None:
        return None
    row = await session.get(Character, int(rec["cid"]))
    return await game.load_character(session, int(rec["cid"])) if row is not None and row.alive else None


def health(content: Content, boss: ActiveCharacter) -> tuple[int, int]:
    from app.engine import derived

    return max(0, int(boss.state.hp)), max(1, derived.max_hp(boss.state, content))


async def declare(session: AsyncSession, content: Content, tg: int, bounty: int | None = None) -> dict[str, Any] | None:
    """Make the living character of the player the boss (the old boss, if any, is dropped without a payout)."""
    from app.services import game

    ch = await game.get_active_character(session, tg)
    if ch is None:
        return None
    rec = {"cid": int(ch.id), "tg": int(tg), "name": f"{ch.state.name} {ch.state.surname}".strip(),
           "bounty": int(bounty if bounty is not None else cfg(content).bounty), "since": time.time(),
           "hits": {}, "last": {}}
    await _store(session, rec)
    await repo.log_event(session, "pboss", {"name": rec["name"], "bounty": rec["bounty"]}, character_id=ch.id)
    return rec


async def clear(session: AsyncSession) -> dict[str, Any] | None:
    rec = await load(session)
    await _store(session, None)
    return rec


def announce_text(rec: dict[str, Any]) -> str:
    return (f"🎯 <b>Рейд-босс Найт-Сити: {tag(rec['tg'], rec['name'])}</b>\n"
            f"Город назначил цену за эту голову: <b>{money(rec['bounty'])} эдди</b> тому, кто добьёт.\n"
            f"Охота открыта всем: «Город» → «🎯 Рейд-босс». Это настоящий пошаговый бой насмерть с бойцом босса: его "
            f"оружие, броня, хром и его здоровье. Отказаться босс не может, но между боями может уйти и залечиться. "
            f"Награда — только за окончательную смерть: Trauma Team, глюк и клон босса тоже работают. Каждые сутки, "
            f"пока голова на плечах, цена растёт на миллион. Охотник тоже может не вернуться.")


def cooldown_left(content: Content, rec: dict[str, Any], tg: int) -> float:
    last = float((rec.get("last") or {}).get(str(tg), 0) or 0)
    return max(0.0, last + cfg(content).hit_cooldown_h * 3600 - time.time())


@dataclass
class Start:
    blocked: str | None = None
    wait_s: float = 0
    step: object | None = None          # the combat Step of the fight that began
    rec: dict[str, Any] | None = None


async def start(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str) -> Start:
    """A real fight with the boss's own fighter (weapon, armour, chrome, programs) on the boss's own health now."""
    from app.engine import psychosis
    from app.services import ap as ap_services
    from app.services import combat
    from app.services import events as ev_service

    c = cfg(content)
    rec = await load(session)
    if not c.enabled or rec is None:
        return Start(blocked=BLOCK_NONE)
    if int(rec["cid"]) == int(ch.id):
        return Start(blocked=BLOCK_SELF, rec=rec)
    if int(ch.state.flags.get("jail_weeks", 0) or 0) > 0:
        return Start(blocked=BLOCK_JAIL, rec=rec)
    if ev_service.has_pending(ch):
        return Start(blocked=BLOCK_BUSY, rec=rec)
    wait = cooldown_left(content, rec, ch.player_tg_id)
    if wait > 0:
        return Start(blocked=BLOCK_CD, wait_s=wait, rec=rec)
    boss = await boss_of(session, rec)
    if boss is None:
        await _store(session, None)
        return Start(blocked=BLOCK_GONE, rec=rec)
    from app.engine import place

    if not place.same_place(boss.state, ch.state, content):
        return Start(blocked=BLOCK_AWAY, rec=rec)
    if not ap_services.can_spend(ch, c.ap):
        return Start(blocked=BLOCK_AP, rec=rec)
    ap_services.spend(ch, content, c.ap)
    hp, mx = health(content, boss)
    foe = psychosis.player_as_foe(boss.state, content)
    foe.danger = c.foe_danger
    foe.max_hp, foe.hp = mx, max(1, hp)
    foe.name = f"{rec['name']} · рейд-босс"
    last = dict(rec.get("last") or {})
    last[str(ch.player_tg_id)] = time.time()
    rec["last"] = last
    await _store(session, rec)
    await save_active(session, ch, tz)
    step = await combat.begin(session, content, ch, rng, year, tz, faction="cyberpsycho", danger=c.foe_danger,
                              foes=[foe], src={"src": SRC, "solo": True, "no_rep": True, "foe": rec["name"],
                                               "pb_cid": int(rec["cid"]), "pb_start": foe.hp,
                                               "boss_ctl": {"e0": int(rec["tg"])}})
    cs = getattr(step, "cs", None)
    if cs is not None and not cs.outcome:  # owner request 27.10 «меня не пригласило в бой со мной»
        await go_live(session, content, ch, cs, rec)
    return Start(step=step, rec=rec)


async def on_end(session: AsyncSession, content: Content, ch: ActiveCharacter, cs, result: str,  # noqa: ANN001
                 rng: random.Random, year: int, tz: str) -> None:
    """Called by the fight's end (services/combat.py) before the hunter's own fate: the wounds stay on the boss;
    a fighter taken down is the boss's death, a legend, and the bounty for the hunter. Lines go to ctx `pb_lines`."""
    from app.engine import combat as cb

    foe = next((e for e in cs.enemies if e.id == "e0"), None)
    rec = await load(session)
    me_name = f"{ch.state.name} {ch.state.surname}".strip()
    lines: list[str] = []
    cs.ctx["pb_lines"] = lines
    await end_live(session, ch, cs, result)
    if foe is None or rec is None or int(rec.get("cid", 0)) != int(cs.ctx.get("pb_cid", -1)):
        lines.append("Пока ты дрался, охоту закрыли: эта голова уже не стоит денег.")
        return
    boss = await boss_of(session, rec)
    if boss is None:
        await _store(session, None)
        lines.append("Голова ушла раньше тебя: награду платить некому.")
        return
    dmg = max(0, int(cs.ctx.get("pb_start", 0) or 0) - max(0, int(foe.hp)))
    down = foe.hp <= 0 or foe.out in (cb.OUT_KILLED, cb.OUT_TAKEN)
    hits = dict(rec.get("hits") or {})
    h = dict(hits.get(str(ch.player_tg_id)) or {"name": me_name, "dmg": 0, "n": 0})
    h.update({"name": me_name, "dmg": int(h.get("dmg", 0)) + dmg, "n": int(h.get("n", 0)) + 1})
    hits[str(ch.player_tg_id)] = h
    rec["hits"] = hits
    if down:  # owner request 27.10: «20 миллионов за мою окончательную смерть: у меня и Trauma Team, и клон»
        lines.extend(await _fall(session, content, ch, boss, rec, rng, year, tz))
        return
    if dmg:
        boss.state.hp = max(1, int(boss.state.hp) - dmg)
        await save_active(session, boss, tz)
        apply_effects(ch.state, {"street_cred": cfg(content).hit_cred}, content, rng, year)
    hp, mx = health(content, boss)
    await _store(session, rec)
    if result == cb.DOWNED:
        text = f"{me_name} пришёл за твоей головой и остался лежать" + (f", но успел снять {dmg} здоровья." if dmg else ".")
    elif dmg:
        text = f"{me_name} бился с тобой и ушёл живым: −{dmg} здоровья (осталось {hp} из {mx}). Есть время залечиться."
    else:
        text = f"{me_name} пришёл за твоей головой и не смог даже поцарапать."
    session.add(PlayerMail(to_tg_id=int(rec["tg"]), from_tg_id=ch.player_tg_id, from_char_id=ch.id,
                           to_char_id=int(rec["cid"]), from_name="Охота", from_year=year, kind="city",
                           payload={"text": "🎯 " + text}))
    await repo.log_event(session, "pboss_hit", {"boss": rec["name"], "dmg": dmg, "boss_hp": hp, "result": result},
                         character_id=ch.id)
    fled = foe.out == cb.OUT_FLED
    if fled:
        lines.append(f"🎯 Босс вырывается и уходит в город: −{dmg}, у него {hp} из {mx}. Залечится, если дать время.")
    else:
        lines.append(f"🎯 Босс уходит раненым: −{dmg}, у него {hp} из {mx}. Залечится, если дать время." if dmg
                     else "🎯 Босс уходит целым.")


def _act(rec: dict[str, Any], killer: str) -> str:
    hunters = sorted((rec.get("hits") or {}).values(), key=lambda x: -int(x.get("dmg", 0)))
    return (f"Рейд-босс Найт-Сити. Город назначил за голову {money(rec['bounty'])} эдди, и весь город вышел на охоту. "
            f"Отказаться от боя было нельзя, бегать от охотников и зализывать раны между боями — можно; Trauma Team и "
            f"клон держали эту голову на плечах, сколько могли. Охотились: "
            + ", ".join(f"{x.get('name')} (боёв {x.get('n')}, урона {x.get('dmg')})" for x in hunters[:6])
            + f". Последний бой: {killer} находит босса и в пошаговом бою насмерть добивает окончательно. Это гибель в "
              f"бою, ставшая легендой: персонаж умирает, а город запоминает, как его брали.")


async def _fall(session: AsyncSession, content: Content, ch: ActiveCharacter, boss: ActiveCharacter,
                rec: dict[str, Any], rng: random.Random, year: int, tz: str) -> list[str]:
    """The boss's fighter is down: the boss falls to zero like in any fight — Trauma Team, a second heart, the save,
    the glitch offer, the clone. The bounty only for the final death."""
    from app.services import death, legend

    me_name = f"{ch.state.name} {ch.state.surname}".strip()
    try:  # what Claude will need if this is the end (before the death drops the flags)
        payload = await legend.scene_payload(session, content, boss, _act(rec, me_name))
    except Exception:  # noqa: BLE001 - the legend leaves anyway
        log.exception("pboss: scene payload failed")
        payload = {"kind": "legend_scene", "act": _act(rec, me_name)}
    boss.state.hp = 0
    zr = await death.resolve_zero_hp(session, content, boss, rng, year, tz, cause="bounty_hunt", source=SRC,
                                     hunter=me_name, after={"kind": "pboss"})
    if zr.died:
        await _finish(session, content, boss, rec, ch.player_tg_id, me_name, ch.id, payload, year, ch, rng, tz)
        return [f"🩸 Последний удар твой. {rec['name']} падает и больше не встаёт: ни Trauma Team, ни клон не "
                f"успевают. За голову: +{money(rec['bounty'])} эдди. Город запишет это в легенды."]
    saved = " ".join(_plain(x) for x in zr.lines)
    if zr.pending:  # the glitch offer waits for the boss: the end is decided when the boss answers
        rec["fallen"] = {"tg": ch.player_tg_id, "name": me_name, "cid": ch.id, "year": year, "payload": payload,
                         "at": time.time()}
        await _store(session, rec)
        _mail(session, rec, ch, year, f"{me_name} вынес тебя. Ты на асфальте, сердце стоит: решает глюк. "
                                      f"Откажешься или не выйдет — голова уйдёт за {money(rec['bounty'])}.")
        await repo.log_event(session, "pboss_hit", {"boss": rec["name"], "down": True, "pending": True},
                             character_id=ch.id)
        return [f"🩸 {rec['name']} падает. Сердце стоит, но глюк ещё может вытащить. Если не вытащит — "
                f"{money(rec['bounty'])} эдди твои, письмом."]
    await save_active(session, boss, tz)  # Trauma Team, the second heart, the save or the clone
    await _store(session, rec)
    _mail(session, rec, ch, year, f"{me_name} вынес тебя, но ты выкарабкиваешься. {saved}".strip())
    await repo.log_event(session, "pboss_hit", {"boss": rec["name"], "down": True, "saved": True}, character_id=ch.id)
    POSTS.append(f"🎯 {tag(ch.player_tg_id, me_name)} выносит рейд-босса {tag(rec['tg'], rec['name'])}, но голова "
                 f"остаётся на плечах. {saved} Охота продолжается.")
    return [f"🩸 {rec['name']} падает… и выкарабкивается. {saved}".strip(),
            "Награды нет: голова ещё на плечах. Охота продолжается."]


def _plain(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", str(text)).strip()


def _mail(session: AsyncSession, rec: dict[str, Any], ch: ActiveCharacter, year: int, text: str) -> None:
    session.add(PlayerMail(to_tg_id=int(rec["tg"]), from_tg_id=ch.player_tg_id, from_char_id=ch.id,
                           to_char_id=int(rec["cid"]), from_name="Охота", from_year=year, kind="city",
                           payload={"text": "🎯 " + text}))


async def _finish(session: AsyncSession, content: Content, boss: ActiveCharacter, rec: dict[str, Any], killer_tg: int,
                  killer: str, killer_cid: int, payload: dict, year: int, ch: ActiveCharacter | None = None,
                  rng: random.Random | None = None, tz: str = "") -> None:
    """The final death: the bounty (now to the hunter in the fight, or by mail), the Hall, the scene, the posts."""
    from app.services import legend, loud

    if ch is not None and rng is not None:
        apply_effects(ch.state, {"eddies": int(rec["bounty"])}, content, rng, year)
        await save_active(session, ch, tz)
    else:
        session.add(PlayerMail(to_tg_id=int(killer_tg), from_tg_id=0, from_name="Город", kind="city",
                               payload={"eddies": int(rec["bounty"]),
                                        "text": f"🎯 {rec['name']} так и не поднимается после твоего удара. "
                                                f"Награда за голову: {money(rec['bounty'])} эдди."}))
    av = boss.state.flags.get("avatar")
    queue = list(await repo.ws_get(session, SCENES, []) or [])
    queue.append({"cid": boss.id, "tg": boss.player_tg_id, "name": rec["name"], "year": year, "payload": payload,
                  "avatar": str(av.get("file_id")) if isinstance(av, dict) and av.get("file_id") else ""})
    await repo.ws_set(session, SCENES, queue[-10:])
    await legend.retire(session, content, boss, ending_title="Рейд-босс Найт-Сити", fallen=True)
    await loud.post(session, killer_cid, f"🎯 {loud.who(killer)} добивает рейд-босса {loud.who(rec['name'])} и "
                                         f"забирает {money(rec['bounty'])} эдди за голову.", kind="pvp", w=3)
    POSTS.append(f"🎯 <b>Рейд-босс повержен.</b> {tag(killer_tg, killer)} добивает {tag(rec['tg'], rec['name'])} "
                 f"и забирает {money(rec['bounty'])} эдди за голову. Имя уходит в Зал легенд, сцена следом.")
    await repo.log_event(session, "pboss_kill", {"boss": rec["name"], "bounty": rec["bounty"]}, character_id=killer_cid)
    await _store(session, None)


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    return list(ctx.get("pb_lines") or [])


async def write_scenes(session: AsyncSession) -> int:
    """The job: the scene of every fallen boss, into the Hall entry and the group (permanent, under the avatar)."""
    from app.bot import texts
    from app.services import legend

    queue = list(await repo.ws_get(session, SCENES, []) or [])
    if not queue:
        return 0
    item = queue.pop(0)
    await repo.ws_set(session, SCENES, queue)
    await session.commit()  # the scene takes up to a minute: never write it twice
    name = str(item.get("name") or "")
    first, _, last = name.partition(" ")
    scene, written = await legend.write_scene(dict(item.get("payload") or {}), int(item.get("year") or 2077),
                                              SimpleNamespace(name=first, surname=last))
    if not written:  # the ready-made text of a fallen boss (the narrator is off or failed)
        scene = (f"За голову {name} город назначил цену, и город пришёл за ней. Охотники шли по одному, и каждый "
                 f"оставлял на этом теле след. Голова уходила в дым, раны затягивались, и охота начиналась снова.\n\n"
                 f"Последний бой был коротким. Когда всё стихло, на асфальте осталось тело, которое город боялся больше "
                 f"всего на свете, а в барах ещё долго спорят, сколько охотников оно забрало с собой.")
    hall = list(await repo.ws_get(session, legend.HALL, []) or [])
    for h in reversed(hall):
        if int(h.get("char", 0) or 0) == int(item.get("cid", 0) or 0):
            h["scene"] = scene[:4000]
            break
    await repo.ws_set(session, legend.HALL, hall)
    payload = {"text": f"👑 <b>Легенда Найт-Сити: {texts.esc(name)}</b>\n<i>рейд-босс</i>\n\n{texts.esc(scene)}",
               "ref": "legend_scene"}
    if item.get("avatar"):
        payload["avatar"] = str(item["avatar"])
    await repo.log_event(session, "legend", payload, character_id=int(item.get("cid") or 0) or None, public=True)
    return 1


async def tick(session: AsyncSession, content: Content, tz: str = "Europe/Kyiv") -> list[str]:  # noqa: ARG001
    """The job: the first boss once (owner request 27.10); a boss who died otherwise or left drops the hunt.
    Returns posts for the group."""
    posts: list[str] = list(POSTS)
    POSTS.clear()
    if not await repo.ws_get(session, "refund_bribe_2710", False):  # owner report 27.10: a demand read as a bribe
        await repo.ws_set(session, "refund_bribe_2710", True)
        session.add(PlayerMail(to_tg_id=SEED_TG, from_tg_id=0, from_name="Город", kind="city",
                               payload={"eddies": 300, "text": "Город признаёт ошибку: требование дани прочитали как "
                                                               "взятку. 300 эдди возвращаются. Теперь есть «Обложить "
                                                               "данью»."}))
    rec = await load(session)
    if not await repo.ws_get(session, SEED_FLAG, False):
        await repo.ws_set(session, SEED_FLAG, True)
        if rec is None and cfg(content).enabled:
            rec = await declare(session, content, SEED_TG)
            if rec is not None:
                posts.append(announce_text(rec))
    if rec is not None and cfg(content).daily_add and not rec.get("fallen"):
        # owner request 27.10: «по миллиону за каждый день, что я жив» → «реальные сутки»: every full 24 hours since
        # the declaration
        days = int(max(0.0, time.time() - float(rec.get("since") or time.time())) // 86400)
        paid = int(rec.get("days", 0) or 0)
        if days > paid:
            rec["bounty"] = int(rec["bounty"]) + (days - paid) * cfg(content).daily_add
            rec["days"] = days
            await _store(session, rec)
            posts.append(f"🎯 Голова {tag(rec['tg'], rec['name'])} держится уже {days} сут. Цена растёт: "
                         f"<b>{money(rec['bounty'])} эдди</b>. «Город» → «🎯 Рейд-босс».")
    fallen = (rec or {}).get("fallen")
    if rec is not None and isinstance(fallen, dict):  # the boss answered the glitch offer (or still has to)
        from app.services import death, game

        row = await session.get(Character, int(rec["cid"]))
        if row is not None and not row.alive:
            boss = await game.load_character(session, int(rec["cid"]))
            if boss is not None:
                await _finish(session, content, boss, rec, int(fallen["tg"]), str(fallen["name"]), int(fallen["cid"]),
                              dict(fallen.get("payload") or {}), int(fallen.get("year") or 2077))
                posts += POSTS
                POSTS.clear()
                return posts
        boss = await boss_of(session, rec)
        if boss is not None and death.pending_of(boss) is None and boss.state.hp > 0:
            rec.pop("fallen", None)
            await _store(session, rec)
            posts.append(f"🎯 {tag(rec['tg'], rec['name'])} выкарабкивается после удара {fallen.get('name')}: глюк "
                         f"вытащил. Охота продолжается.")
        return posts
    if rec is not None and await boss_of(session, rec) is None:
        await _store(session, None)
        posts.append(f"🎯 Охота на {rec['name']} закрыта: эта голова ушла из игры без охотников. "
                     f"Награда никому не досталась.")
    return posts


# -- the boss in person (owner request 27.10 «меня не пригласило в бой со мной») -----------------------------------------
# Like the hired players in an employer's fight (services/pally.py): the boss gets an invitation with a button, joins
# in person and picks the fighter's move for every tick of the hunter's fight; the hunter's tick waits for it (the
# timers of balance.pally). Not joined or too slow — the fighter acts on its own (engine/enemy_ai).
# world_state `pboss_live` = {boss tg: {hunter, hunter_name, tick, snap, skills, joined, invite_until, turn_at, at,
# last, chat}}, `pboss_orders` = {hunter tg: skill}, `pboss_over` = {boss tg: {text, at}}.

LIVE, ORDERS, OVER = "pboss_live", "pboss_orders", "pboss_over"
JOIN_PREFIX = "pbj"


def _snap(cs) -> str:  # noqa: ANN001
    foe = next((e for e in cs.enemies if e.id == "e0"), None)
    p = cs.player
    me = f"{max(0, foe.hp)}/{foe.max_hp}" if foe is not None else "?"
    return f"Ход {cs.tick}. Ты {me} · охотник {max(0, p.hp)}/{p.max_hp}"


async def go_live(session: AsyncSession, content: Content, ch: ActiveCharacter, cs, rec: dict[str, Any]) -> None:  # noqa: ANN001
    from app.engine import combat as cb

    foe = next((e for e in cs.enemies if e.id == "e0"), None)
    if foe is None:
        return
    pc = content.balance.pally
    now = time.time()
    hunter = f"{ch.state.name} {ch.state.surname}".strip()
    live = dict(await repo.ws_get(session, LIVE, {}) or {})
    live[str(rec["tg"])] = {"hunter": int(ch.player_tg_id), "hunter_name": hunter, "tick": cs.tick, "snap": _snap(cs),
                            "skills": cb.boss_skills(cs, foe, content), "joined": False, "at": now,
                            "invite_until": now + pc.invite_s, "turn_at": now, "last": ""}
    await repo.ws_set(session, LIVE, live)
    from app.services import pally

    ui = pally.UI
    if ui is not None:
        text = (f"🎯 {tag(rec['tg'], rec['name'])}, за твоей головой пришёл {tag(ch.player_tg_id, hunter)}. "
                f"{pc.invite_s} секунд, чтобы встать в бой самому и выбирать ходы. Не успеешь — твой боец дерётся сам.")
        ui.spawn(_invite(ui, int(rec["tg"]), int(ch.player_tg_id), text))


async def _invite(ui, tg: int, hunter: int, text: str) -> None:  # noqa: ANN001
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from app.services import dm_relay

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="🎯 Защищаться самому", callback_data=f"{JOIN_PREFIX}:{hunter}:{tg}")]])
    try:
        member = ui.group_id is not None and (not dm_relay.cfg(ui).enabled or await dm_relay.in_group(ui, tg))
    except Exception:  # noqa: BLE001
        member = False
    await ui._send_temp(ui.group_id if member else tg, text, 10 * 60, "news", kb)  # noqa: SLF001


async def live_for(session: AsyncSession, content: Content, tg: int) -> dict[str, Any] | None:
    e = (await repo.ws_get(session, LIVE, {}) or {}).get(str(tg))
    if not e or time.time() - float(e.get("at", 0)) > content.balance.pally.live_min * 60:
        return None
    return dict(e)


async def join_live(session: AsyncSession, tg: int, hunter: int, chat_id: int) -> str | None:
    live = dict(await repo.ws_get(session, LIVE, {}) or {})
    e = live.get(str(tg))
    if not e or int(e.get("hunter", 0)) != int(hunter):
        return "Этот бой уже кончился."
    e.update({"joined": True, "chat": int(chat_id), "turn_at": time.time()})
    live[str(tg)] = e
    await repo.ws_set(session, LIVE, live)
    return None


async def choose(session: AsyncSession, content: Content, tg: int, skill: str) -> str:
    e = await live_for(session, content, tg)
    if e is None:
        return "Бой уже кончился."
    if skill not in (e.get("skills") or []):
        return "Сейчас так не выйдет."
    orders = dict(await repo.ws_get(session, ORDERS, {}) or {})
    orders[str(e["hunter"])] = skill
    await repo.ws_set(session, ORDERS, orders)
    return f"Ход принят: {skill_label(content, skill)}. Ждём охотника."


def skill_label(content: Content, skill: str) -> str:
    from app.engine import pally as pally_eng

    return "Уйти от охотника" if skill == "flee" else pally_eng.label(content, skill)


async def waiting(session: AsyncSession, content: Content, hunter_tg: int) -> tuple[str, float] | None:
    """Whether the hunter's tick waits for the boss now: (boss name, seconds left)."""
    rec = await load(session)
    if rec is None:
        return None
    e = (await repo.ws_get(session, LIVE, {}) or {}).get(str(rec["tg"]))
    if not e or int(e.get("hunter", 0)) != int(hunter_tg):
        return None
    pc = content.balance.pally
    now = time.time()
    if not e.get("joined"):
        left = float(e.get("invite_until", 0) or 0) - now
    elif str(hunter_tg) not in (await repo.ws_get(session, ORDERS, {}) or {}):
        left = float(e.get("turn_at", 0) or 0) + pc.turn_s - now
    else:
        return None
    return (str(rec["name"]), left) if left > 0 else None


async def pull_order(session: AsyncSession, ch: ActiveCharacter, cs) -> None:  # noqa: ANN001
    """Before the hunter's tick: the boss's own move goes into the fight."""
    orders = dict(await repo.ws_get(session, ORDERS, {}) or {})
    skill = orders.pop(str(ch.player_tg_id), None)
    if skill:
        bo = dict(cs.ctx.get("boss_orders") or {})
        bo["e0"] = skill
        cs.ctx["boss_orders"] = bo
        await repo.ws_set(session, ORDERS, orders)
        live = dict(await repo.ws_get(session, LIVE, {}) or {})
        boss_tg = str((cs.ctx.get("boss_ctl") or {}).get("e0", ""))
        if boss_tg in live:
            live[boss_tg]["last"] = skill
            await repo.ws_set(session, LIVE, live)


async def after_tick(session: AsyncSession, content: Content, ch: ActiveCharacter, cs) -> None:  # noqa: ANN001
    from app.engine import combat as cb

    boss_tg = str((cs.ctx.get("boss_ctl") or {}).get("e0", ""))
    live = dict(await repo.ws_get(session, LIVE, {}) or {})
    e = live.get(boss_tg)
    foe = next((x for x in cs.enemies if x.id == "e0"), None)
    if not e or int(e.get("hunter", 0)) != ch.player_tg_id or foe is None:
        return
    e.update({"tick": cs.tick, "snap": _snap(cs), "at": time.time(), "turn_at": time.time(),
              "skills": cb.boss_skills(cs, foe, content) if foe.active else []})
    live[boss_tg] = e
    await repo.ws_set(session, LIVE, live)
    if e.get("joined"):
        refresh(int(boss_tg))


async def end_live(session: AsyncSession, ch: ActiveCharacter, cs, result: str) -> None:  # noqa: ANN001
    boss_tg = str((cs.ctx.get("boss_ctl") or {}).get("e0", ""))
    live = dict(await repo.ws_get(session, LIVE, {}) or {})
    e = live.pop(boss_tg, None)
    orders = dict(await repo.ws_get(session, ORDERS, {}) or {})
    if orders.pop(str(ch.player_tg_id), None) is not None:
        await repo.ws_set(session, ORDERS, orders)
    if e is None:
        return
    await repo.ws_set(session, LIVE, live)
    foe = next((x for x in cs.enemies if x.id == "e0"), None)
    from app.engine import combat as cb

    if foe is not None and foe.out == cb.OUT_FLED:
        word = "ты уходишь от охотника"
    elif foe is not None and (foe.hp <= 0 or foe.out in (cb.OUT_KILLED, cb.OUT_TAKEN)):
        word = "ты падаешь"
    elif result == cb.DOWNED:
        word = "охотник лежит"
    else:
        word = "охотник уходит"
    over = dict(await repo.ws_get(session, OVER, {}) or {})
    over[boss_tg] = {"text": f"Бой с охотником {e.get('hunter_name')}: {word}. {_snap(cs)}.", "at": time.time()}
    await repo.ws_set(session, OVER, over)
    if e.get("joined"):
        refresh(int(boss_tg))


def refresh(tg: int) -> None:
    from app.services import pally

    ui = pally.UI
    if ui is None:
        return

    async def _go() -> None:
        import asyncio

        from app.bot.handlers.pboss import push_fight

        await asyncio.sleep(1.0)  # the hunter's tick is committed first, then the boss's window reads it
        await push_fight(ui, tg)

    ui.spawn(_go())
