"""The district in 3D (owner request 30.10: «давай попробуем 3Д», docs/PLAN_CITY3D.md).

The page webapp/city3d/v1/ is a three.js walk through the character's district. This module gives it the data: the doors
(the places of the district plus a few sections of the game), the people standing at the places (city_life.present, the
same crowd the place screen shows), the character's ride and the colours of the city. A door or a person pressed in 3D
comes back to the bot as the press `city:d3:<door>` on the player's window (handlers/city.py), so every rule of the
place screens holds: jail, AP, abroad.
"""

from __future__ import annotations

import random
import zlib
from typing import Any

from app.content.loader import Content
from app.engine import city as ce
from app.engine import place as place_engine

GAME = "city3d"
VERSION = "v4"

# the colours of the 3D city: (accent, second accent, sky, fog); the same families as the app themes (webapp/app.css)
_THEMES: dict[str, tuple[str, str, str, str]] = {
    "neon": ("#FF3E8A", "#FFB547", "#0B0A12", "#16101F"),
    "tokyo": ("#FF7EB6", "#6EC8FF", "#0E0A14", "#1A1222"),
    "asia": ("#E8433A", "#FFC14A", "#120A0A", "#1E1212"),
    "euro": ("#5AA9FF", "#F5D76E", "#0D1116", "#161C24"),
    "sov": ("#E33B2E", "#D9B44A", "#120D0D", "#1D1414"),
    "desert": ("#E7B54A", "#3FB6A8", "#1A140A", "#2A2216"),
    "africa": ("#FF8C2A", "#6BD3FF", "#120D08", "#1F160D"),
    "frontier": ("#E04848", "#5B8DEF", "#0B0E14", "#141A24"),
    "latam": ("#FF5F7E", "#2EC4B6", "#120B10", "#1E1219"),
    "oceania": ("#2EC4D6", "#FFC857", "#08121A", "#0F1E29"),
    "synod": ("#7FFFE5", "#FFFFFF", "#0B1012", "#18242A"),
    "libria": ("#F2F2F2", "#7A7F85", "#161718", "#232628"),
    "elysium": ("#E9C46A", "#9AD9CF", "#1C1A14", "#2E2A20"),
    "aeon": ("#39FF7A", "#E5FF6E", "#050806", "#0C130E"),
}
_CITY_THEME = {
    "japan": "tokyo", "korea": "tokyo", "china": "asia", "hongkong": "asia", "singapore": "asia", "thailand": "asia",
    "india": "asia", "europe": "euro", "uk": "euro", "germany": "euro", "italy": "euro", "spain": "euro",
    "netherlands": "euro", "sweden": "euro", "poland": "euro", "ukraine": "euro", "russia": "sov", "kazakhstan": "sov",
    "turkey": "desert", "uae": "desert", "egypt": "desert", "morocco": "desert", "nigeria": "africa", "kenya": "africa",
    "south_africa": "africa", "nusa": "frontier", "texas": "frontier", "canada": "frontier", "mexico": "latam",
    "brazil": "latam", "argentina": "latam", "colombia": "latam", "cuba": "latam", "australia": "oceania",
    "new_zealand": "oceania", "synod": "synod", "libria": "libria", "elysium": "elysium", "aeon": "aeon",
}
# the sections of the game that stand in the street as doors: (door id, sign, kind); the id is the menu section or a
# city action (handlers/city.py::city_handler, action "d3")
_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("gigs", "Фиксер", "Заказы"),
    ("ripper", "Риппердок", "Импланты"),
    ("work", "Биржа труда", "Работа"),
    ("property", "Твоё жильё", "Имущество"),
    ("arena", "Арена", "Бои"),
    ("arcade", "Аркада", "Игры"),
)
MAX_PEOPLE = 40


def url(content: Content, bot_token: str, tg_id: int) -> str:
    """The page on the Mini App's own origin (taken from the arcade's base url), so the app opens it in its frame."""
    from app.services import arcade

    origin = arcade.cfg(content).web.base_url.split("/static/")[0].rstrip("/")
    return f"{origin}/static/city3d/{VERSION}/index.html#t={arcade.web_token(bot_token, tg_id, GAME)}"


def theme(place: str) -> dict[str, str]:
    a, b, bg, fog = _THEMES[_CITY_THEME.get(place, "neon")]
    return {"acc": a, "acc2": b, "bg": bg, "fog": fog}


# owner request 30.10 (docs/PLAN_CITY3D_2.md, phase 5): how the district is built
_DISTRICT_STYLE = {
    "watson": "dense", "westbrook": "japantown", "city_center": "corpo", "heywood": "suburb", "santo_domingo": "industrial",
    "pacifica": "ruins", "dogtown": "dogtown", "badlands": "desert", "northside": "industrial", "waterfront": "docks",
    "little_europe": "old", "north_oak": "rich", "the_glen": "suburb", "combat_zone": "ruins", "university": "suburb",
    "hot_zone": "ruins", "ely_station": "station", "ely_la": "ruins", "lib_center": "libria", "lib_nether": "ruins",
    "aeon_dayton": "aeon", "aeon_greenwich": "aeon",
}
_THEME_STYLE = {"tokyo": "japantown", "asia": "japantown", "euro": "old", "sov": "sov", "desert": "dense", "africa": "dense",
                "frontier": "suburb", "latam": "dense", "oceania": "suburb", "synod": "corpo", "libria": "libria",
                "elysium": "station", "aeon": "aeon"}
# gang colours on the street: the canon gangs by their look in the game, any other by its key
_GANG_COLOR = {"maelstrom": "#E0262B", "tyger_claws": "#19C6A6", "valentinos": "#E8B923", "sixth_street": "#3A66E0",
               "animals": "#B5652B", "voodoo_boys": "#9B45D6", "mox": "#FF5FB4", "scavengers": "#8A8A8A",
               "wraiths": "#C9903F", "barghest": "#5C6E78", "aldecaldos": "#D07A2A", "raffen_shiv": "#7A2E2E"}
_CHROME_GROUPS = ("sandevistan", "kerenzikov", "berserk")


def style_of(content: Content, district: str) -> str:
    if district in _DISTRICT_STYLE:
        return _DISTRICT_STYLE[district]
    place = place_engine.of_district(content, district)
    return _THEME_STYLE.get(_CITY_THEME.get(place, ""), "dense")


def gang_color(key: str) -> str:
    if key in _GANG_COLOR:
        return _GANG_COLOR[key]
    h = zlib.crc32(key.encode()) % 360
    return f"hsl({h}, 70%, 55%)"


def _chrome(content: Content, ch: Any) -> list[str]:
    out: list[str] = []
    for key in ch.state.cyber_keys:
        cw = content.cyberware.get(key)
        g = getattr(cw, "exclusive_group", None) if cw is not None else None
        if g in _CHROME_GROUPS and g not in out:
            out.append(g)
    return out


def _unity_link(ui: Any, tg: int) -> str | None:
    """Owner report 31.10: an admin reaches the Unity client from inside the district too (docs/PLAN_UNITY.md)."""
    from app.services import unity3d

    if not ui.is_admin(tg):
        return None
    return unity3d.url(ui.content, ui.settings.bot_token, tg)


async def map_for(ui: Any, token: str) -> dict[str, Any]:
    """The district of the token's player, for the page: doors, people, the ride, and the state of the street
    (heat, gangs, gigs, the Purge, the Blackwall, the way out to other districts)."""
    from app.db import session as db
    from app.engine import property as prop_engine, wanted as wanted_eng
    from app.services import arcade, blackwall, city as city_svc, city_life, game, gangs as gangs_svc, gigs, purge
    from app.services import vehicles, worldfx

    content: Content = ui.content
    data = arcade.read_web_token(ui.settings.bot_token, token, arcade.cfg(content).web.token_ttl_h)
    if data is None or data.get("g") != GAME:
        return {"ok": False, "status": "auth"}
    tg = int(data["u"])
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, tg)
        if ch is None:
            return {"ok": False, "status": "nochar"}
        st = ch.state
        district = st.district
        year = st.current_year
        rng = random.Random(zlib.crc32(f"{district}:{tg}".encode()))
        doors: list[dict[str, Any]] = []
        npcs: list[dict[str, Any]] = []
        for p in ce.cfg(content).places:
            doors.append({"id": f"p.{p.key}", "label": ce.venue_name(district, p, content, year),
                          "kind": ce.kind_name(content, district, p), "room": p.key})
            if len(npcs) >= MAX_PEOPLE:
                continue
            for n, _home in await city_life.present(s, content, district, p.key, rng):
                if n.alive and not city_svc.hurt(n) and len(npcs) < MAX_PEOPLE:  # a hurt resident is away
                    npcs.append({"id": n.id, "name": n.name, "arch": n.archetype, "gender": n.gender,
                                 "door": f"p.{p.key}"})
        night = await purge.is_night(s)
        bw = int((await blackwall.city(s)).get(district, 0))
        gig_list: list[dict[str, Any]] = []
        gig_run = False
        try:
            board = await gigs.load_board(s, content, ch, year, random.Random())
            gig_list = [{"id": g.id, "title": g.title, "pay": g.pay} for g in board.open[:6]]
            gig_run = bool(board.run_pending)
        except Exception:  # a board that cannot be made (a country without fixers) shows no markers
            gig_list = []
        fx = await worldfx.load_fx(s, content, tg)
        exits_keys = prop_engine.districts_to_move(st, content, year, fx)
    for key, label, kind in _SECTIONS:
        doors.append({"id": key, "label": label, "kind": kind, "room": key})
    place = place_engine.of_district(content, district)
    d = content.districts.get(district)
    v = vehicles.model(content, ch)
    r = vehicles.ride(ch) or {}
    gangs_out: list[dict[str, Any]] = []
    if d is not None and d.city == "nc" and not night:
        mine = gangs_svc.gang_of(ch)
        g_pct = int((st.district_control or [50, 50])[0])
        for gk in d.gangs:
            gg = content.gangs.get(gk)
            if gg is not None:
                gangs_out.append({"key": gk, "name": gg.name, "color": gang_color(gk), "mine": gk == mine})
        gangs_out = gangs_out[:3]
        for g in gangs_out:
            g["pct"] = g_pct
    law = None
    if st.heat > 0 and not night:
        law = wanted_eng.wave(content, st.heat, 0)[0]
    home = prop_engine.visiting(st)
    import time

    _WHO[tg] = (ch.id, f"{st.name} {st.surname}".strip(), district, time.time())  # a trip moved the player: fresh at once
    exits = [{"key": k, "name": content.districts[k].name, "home": k == home} for k in exits_keys
             if k in content.districts][:4]
    return {
        "ok": True, "city": place, "city_name": place_engine.name_of(content, place), "district": district,
        "district_name": d.name if d is not None else district, "seed": zlib.crc32(district.encode()) % 100000,
        "theme": theme(place), "style": style_of(content, district), "doors": doors, "npcs": npcs,
        "me": {"c": ch.id, "name": f"{st.name} {st.surname}".strip()},
        "heat": st.heat, "law": law, "purge": night, "bw": bw, "bw_max": 5,
        "gangs": gangs_out, "gigs": gig_list, "gig_run": gig_run, "chrome": _chrome(content, ch), "exits": exits,
        "unity": _unity_link(ui, tg),
        "vehicle": ({"name": v.name, "speed": vehicles.speed(content, ch), "kind": v.kind, "dmg": int(r.get("dmg", 0) or 0)}
                    if v is not None else None),
    }


async def hit(ui: Any, token: str, npc_id: int, speed: float) -> dict[str, Any]:
    """Owner request 30.10: the player's ride hits a resident of the district in 3D. A slow bump is a push the resident
    remembers; from `hurt_speed` the resident is hurt (away from the place for `hurt_days`, like after a beating) and the
    street adds heat. Under the player's lock, like a button press."""
    from datetime import timedelta

    from app.clock import utcnow
    from app.db import repo, session as db
    from app.db.models import CityNpc
    from app.engine.effects import apply_effects
    from app.services import arcade, city as city_svc, game, purge, world as world_service

    content: Content = ui.content
    data = arcade.read_web_token(ui.settings.bot_token, token, arcade.cfg(content).web.token_ttl_h)
    if data is None or data.get("g") != GAME:
        return {"ok": False, "status": "auth"}
    tg = int(data["u"])
    b = content.balance.city3d
    async with ui.lock_for(tg):
        actor = world_service.set_actor(tg)
        try:
            async with db.session_scope() as s:
                ch = await game.get_active_character(s, tg)
                if ch is None:
                    return {"ok": False, "status": "nochar"}
                npc = await s.get(CityNpc, int(npc_id))
                if npc is None or not npc.alive or npc.district != ch.state.district or city_svc.hurt(npc):
                    return {"ok": True, "status": "gone"}
                arch = ce.archetype(content, npc.archetype)
                role = arch.name if arch else ""
                me = f"{ch.state.name} {ch.state.surname}".strip()
                day = utcnow().date().isoformat()
                rec = dict(ch.state.flags.get("c3d_hits") or {})
                if rec.get("d") != day:
                    rec = {"d": day, "n": 0}
                hard = float(speed) >= b.hurt_speed and int(rec.get("n", 0)) < b.per_day
                if not hard:
                    city_svc.remember(content, npc, ch.id, f"{me}: задели машиной", b.att_push)
                    await s.flush()
                    return {"ok": True, "status": "push", "line": f"{npc.name} отлетает в сторону и орёт тебе вслед."}
                rec["n"] = int(rec.get("n", 0)) + 1
                ch.state.flags["c3d_hits"] = rec
                nd = dict(npc.data or {})
                nd["hurt_until"] = (utcnow() + timedelta(days=ce.cfg(content).hurt_days)).isoformat()
                npc.data = nd
                city_svc.remember(content, npc, ch.id, f"{me}: сбили машиной", b.att_hurt)
                heat = 0 if await purge.is_night(s) else (b.heat_law if npc.archetype in b.law else b.heat)
                if heat:
                    year = await game.current_year(s, content)
                    apply_effects(ch.state, {"heat": heat}, content, random.Random(), year)
                await game.save_active(s, ch, ui.settings.timezone)
                await repo.log_event(s, "city_deed", {"npc": npc.id, "verb": "runover", "ok": True, "fight": False,
                                                      "killed": False, "district": npc.district, "place": npc.place,
                                                      "who": me, "npc_name": npc.name, "role": role}, character_id=ch.id)
                line = f"{npc.name} летит через капот. Пару дней этого человека на улице не будет."
                if heat:
                    line += f" Розыск +{heat}." if npc.archetype not in b.law else f" Под колёсами {role}: розыск +{heat}."
                await s.flush()
        finally:
            world_service.reset_actor(actor)
    return {"ok": True, "status": "hurt", "line": line, "heat": heat}


# -- other players on the street (docs/PLAN_CITY3D_2.md, phase 1) ---------------------------------------------------------

POS_TTL = 8.0  # a spot older than this is somebody who left the 3D district
_POS: dict[int, dict[str, Any]] = {}  # character id -> the last spot; memory only, a restart forgets it
_WHO: dict[int, tuple[int, str, str, float]] = {}  # tg -> (character id, name, district, when read)
_LOOKS: dict[tuple[int, str], bytes] = {}


def _token_tg(ui: Any, token: str) -> int | None:
    from app.services import arcade

    data = arcade.read_web_token(ui.settings.bot_token, token, arcade.cfg(ui.content).web.token_ttl_h)
    return int(data["u"]) if data is not None and data.get("g") == GAME else None


async def _who(ui: Any, tg: int, fresh: bool = False) -> tuple[int, str, str] | None:
    import time

    from app.db import session as db
    from app.services import game

    w = _WHO.get(tg)
    if w is not None and not fresh and time.time() - w[3] < 20:
        return w[0], w[1], w[2]
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, tg)
    if ch is None:
        _WHO.pop(tg, None)
        return None
    _WHO[tg] = (ch.id, f"{ch.state.name} {ch.state.surname}".strip(), ch.state.district, time.time())
    return _WHO[tg][:3]


async def pos(ui: Any, token: str, spot: dict[str, Any]) -> dict[str, Any]:
    """The player's spot in 3D, every 1.5 s; the answer is everybody else in the same district right now."""
    import time

    tg = _token_tg(ui, token)
    if tg is None:
        return {"ok": False, "status": "auth"}
    who = await _who(ui, tg)
    if who is None:
        return {"ok": False, "status": "nochar"}
    cid, name, district = who
    now = time.time()

    def num(k: str, lim: float = 2000.0) -> float:
        try:
            return max(-lim, min(lim, float(spot.get(k) or 0)))
        except (TypeError, ValueError):
            return 0.0

    _POS[cid] = {"c": cid, "tg": tg, "name": name, "d": district, "x": round(num("x"), 2), "z": round(num("z"), 2),
                 "yaw": round(num("yaw", 100.0), 3), "drv": str(spot.get("drv") or "")[:8],
                 "room": str(spot.get("room") or "")[:24], "t": now}
    for k in [k for k, v in _POS.items() if now - v["t"] > 120]:
        _POS.pop(k, None)
    others = [{k: v[k] for k in ("c", "name", "x", "z", "yaw", "drv", "room")} for v in _POS.values()
              if v["d"] == district and v["c"] != cid and now - v["t"] < POS_TTL]
    return {"ok": True, "players": others}


async def look_png(ui: Any, token: str, char_id: int) -> bytes | None:
    """The figure of a character from «Внешность», for the sprite in 3D: oneself, or a player on the same street."""
    import json
    import time

    from app.db import session as db
    from app.services import game, look

    tg = _token_tg(ui, token)
    if tg is None:
        return None
    who = await _who(ui, tg)
    if who is None:
        return None
    target_tg = tg if char_id == who[0] else None
    if target_tg is None:
        v = _POS.get(char_id)
        if v is None or v["d"] != who[2] or time.time() - v["t"] > 60:
            return None
        target_tg = int(v["tg"])
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, target_tg)
    if ch is None:
        return None
    lo = look.look_of(ch)
    key = (ch.id, json.dumps(lo, sort_keys=True) + str(sorted(ch.state.cyber_keys)))
    if key not in _LOOKS:
        if len(_LOOKS) > 200:
            _LOOKS.clear()
        _LOOKS[key] = figure_png(look.figure_of(ui.content, ch, lo))
    return _LOOKS[key]


def figure_png(sp: Any) -> bytes:
    """A pixel figure (engine/pix.Sprite) as an RGBA PNG with a transparent background, for a billboard in 3D."""
    import struct

    raw = bytearray()
    for y in range(sp.h):
        raw += b"\x00"
        for x in range(sp.w):
            c = sp.get(x, y)
            raw += bytes((c[0], c[1], c[2], 255)) if c is not None else b"\x00\x00\x00\x00"

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", sp.w, sp.h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


# -- the chase, the crash (phases 2 and 9) ----------------------------------------------------------------------------------

_CRASH_T: dict[int, float] = {}


async def escape(ui: Any, token: str) -> dict[str, Any]:
    """The player got away from a patrol in 3D: the heat drops by one, now and then."""
    import time

    from app.clock import kyiv_date
    from app.db import session as db
    from app.engine.effects import apply_effects
    from app.services import game, world as world_service

    tg = _token_tg(ui, token)
    if tg is None:
        return {"ok": False, "status": "auth"}
    b = ui.content.balance.city3d
    async with ui.lock_for(tg):
        actor = world_service.set_actor(tg)
        try:
            async with db.session_scope() as s:
                ch = await game.get_active_character(s, tg)
                if ch is None or ch.state.heat <= 0:
                    return {"ok": True, "status": "none"}
                day = kyiv_date(ui.settings.timezone)
                rec = dict(ch.state.flags.get("c3d_escape") or {})
                if rec.get("d") != day:
                    rec = {"d": day, "n": 0, "t": 0}
                now = time.time()
                if int(rec["n"]) >= b.escape_per_day or now - float(rec.get("t", 0)) < b.escape_cd_min * 60:
                    return {"ok": True, "status": "cool", "line": "Патруль отстал, но твоё лицо уже в базе: розыск тот же."}
                rec.update({"n": int(rec["n"]) + 1, "t": now})
                ch.state.flags["c3d_escape"] = rec
                year = await game.current_year(s, ui.content)
                apply_effects(ch.state, {"heat": -1}, ui.content, random.Random(), year)
                heat = ch.state.heat
                await game.save_active(s, ch, ui.settings.timezone)
        finally:
            world_service.reset_actor(actor)
    return {"ok": True, "status": "down", "heat": heat, "line": f"Ушёл от погони. Розыск −1, теперь {heat}."}


async def crash(ui: Any, token: str, speed: float) -> dict[str, Any]:
    """The ride hit a wall in 3D at speed: its damage grows (repaired as usual in the garage)."""
    import time

    from app.db import session as db
    from app.services import game, vehicles, world as world_service

    tg = _token_tg(ui, token)
    if tg is None:
        return {"ok": False, "status": "auth"}
    b = ui.content.balance.city3d
    now = time.time()
    if float(speed) < b.crash_speed or now - _CRASH_T.get(tg, 0) < 4:
        return {"ok": True, "status": "none"}
    _CRASH_T[tg] = now
    async with ui.lock_for(tg):
        actor = world_service.set_actor(tg)
        try:
            async with db.session_scope() as s:
                ch = await game.get_active_character(s, tg)
                r = vehicles.ride(ch) if ch is not None else None
                if ch is None or not r:
                    return {"ok": True, "status": "none"}
                dmg = int(r.get("dmg", 0) or 0)
                if dmg >= b.crash_cap:
                    return {"ok": True, "status": "cap", "dmg": dmg}
                dmg = min(b.crash_cap, dmg + min(15, 3 + int((float(speed) - b.crash_speed) * 1.5)))
                r["dmg"] = dmg
                ch.state.flags[vehicles.FLAG] = r
                await game.save_active(s, ch, ui.settings.timezone)
        finally:
            world_service.reset_actor(actor)
    return {"ok": True, "status": "dmg", "dmg": dmg, "line": f"Удар о стену. Повреждения машины: {dmg}%."}
