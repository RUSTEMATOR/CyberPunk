"""The city director (owner request 26.10, docs/PLAN_LIVING.md stage 2): once a day Claude reads what happened and
writes the story of the day, and the city changes.

Claude runs as a scheduled task on the owner's subscription, not through the bot's API key:
1. `dump` (the bot, every day after `hour`): data/director/in-YYYY-MM-DD.json — the deeds, the districts, the Blackwall,
   the corps, the conflicts, the crews, the living characters (skills, inventory, implants, people) with a menu of
   events each one may get, the catalogs of keys, and the director's memory (its own notes, the past stories, what the
   bot accepted yesterday).
2. The scheduled task reads the file and writes data/director/out-YYYY-MM-DD.json (format: `OUT_FORMAT`).
3. `collect` (the bot, every few minutes): the out file of today, once: posts the story, keeps the memory, adds the new
   scenes to the library, queues the hooks, applies the effects on the characters (through apply_effects, clamped into
   `balance.director.fx_*`), nudges the districts, the Blackwall, the corps and the crews, adds the rumors.
   What was accepted and rejected goes to data/director/applied-YYYY-MM-DD.json and into tomorrow's memory.
   No answer by `fallback_hour`: the bot posts a quiet-day line itself.
Claude never changes a number itself: the bot keeps only what the balance allows, clamped.
"""

from __future__ import annotations

import html
import json
import logging
import random
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.content.schemas import EVENT_CATEGORIES, KEY_RE, NPC_TAGS, NPC_TEMPLATES, SKILLS, Event, split_effect_key
from app.db import repo

log = logging.getLogger(__name__)

KEY_DONE = "director_done"
KEY_DUMP = "director_dump"
KEY_MEMORY = "director_memory"   # {"notes": <the director's own JSON>, "past": [{date, story, rumors}], "last_applied": {}}
KEY_SCENES = "director_scenes"   # {event id: {"at": date, "data": Event dict}}
PREFIX = "dir_"
FLAG_PREFIX = "dir_"             # flags the director may set and clear on a character

OUT_FORMAT = {
    "story": "2-3 абзаца для ленты группы, до 1500 знаков, по-русски, сленг Найт-Сити, без чисел, которых нет во входе",
    "memory": {"threads": [{"id": "snake_case", "title": "нить сюжета", "who": ["имена"], "state": "где сейчас",
                            "next": "что дальше"}], "notes": ["заметки себе на завтра"]},
    "scenes": ["новые сцены по scene_format, до limits.scenes"],
    "hooks": [{"tg": "tg игрока из players", "event": "id из его menu, id своей сцены из scenes/library или id события",
               "why": "одна строка: зачем это ему сегодня"}],
    "fx": [{"tg": "tg игрока", "effects": {"ключ эффекта": "значение, см. fx_format"},
            "text": "одна-две строки игроку: что с ним сделал город", "why": "одна строка для журнала"}],
    "moves": [{"district": "ключ района из districts", "gang": "целое ±limits.move_step",
               "corp": "целое ±limits.move_step", "why": "одна строка"}],
    "blackwall": [{"district": "ключ района", "delta": "целое ±limits.bw_step", "why": "одна строка"}],
    "corps": [{"corp": "ключ из corps", "delta": "целое ±limits.corp_step (стойкость)", "why": "одна строка"}],
    "crews": [{"id": "id из crews", "delta": "целое ±limits.crew_step (сила)", "why": "одна строка"}],
    "rumors": ["слух, одна строка, до 160 знаков; до limits.rumors"],
}

SCENE_FORMAT = {
    "id": "snake_case, латиница; бот добавит префикс dir_",
    "extends": "необязательно: id существующего события; тогда берётся оно, и поля ниже его дописывают",
    "title": "название, до 40 знаков",
    "category": f"одна из {list(EVENT_CATEGORIES)}",
    "text": "сцена, 2-5 предложений, на «ты», без указания пола",
    "text_add": "только с extends: абзац, который допишется к тексту события",
    "faction": "необязательно: ключ банды или корпорации из catalog, с кем встреча (тогда у драки есть выходы)",
    "choices": [
        {"label": "кнопка, до 30 знаков", "text": "что вышло", "effects": {"eddies": 2000, "mood": 3}},
        {"label": "кнопка с проверкой", "check": {"stat": "COOL", "skill": "persuasion", "dv": 13},
         "success": {"text": "удача", "effects": {"street_cred": 2}},
         "fail": {"text": "провал", "effects": {"heat": 1}}},
    ],
    "choices_add": "только с extends: выборы, которые добавятся к выборам события",
    "rules": "2-3 выбора; у проверки есть success и fail, у простого выбора text и effects; эффекты по fx_format; "
             "в сцене можно start_combat 1..3, но тогда нужен выбор без драки; stat из BODY REF TECH INT COOL EMP LUCK; "
             "skill из catalog.skills; dv 8..20",
}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.director


def folder(db_path: str) -> Path:
    return Path(db_path).resolve().parent / "director"


def _name(x: Any, k: str) -> str:
    return str(getattr(x, "name", None) or getattr(x, "title", None) or k)


# -- the library of the director's scenes ------------------------------------------------------------------------------


async def restore(content: Content) -> int:
    """At start: every scene the director wrote joins `content.events`."""
    from app.db import session as db

    async with db.session_scope() as s:
        book = await repo.ws_get(s, KEY_SCENES, {}) or {}
    n = 0
    for eid, rec in book.items():
        try:
            content.events[eid] = Event.model_validate((rec or {}).get("data"))
            n += 1
        except (ValidationError, TypeError):
            log.warning("director scene %s is broken and is skipped", eid)
    return n


# -- the input -------------------------------------------------------------------------------------------------------


def _menu(content: Content, st, rng: random.Random, n: int) -> list[dict[str, str]]:  # noqa: ANN001
    from app.engine import derived
    from app.engine import events as ev_eng

    c = cfg(content)
    flags = derived.event_flags(st, content)
    pool = [e for e in content.events.values()
            if not e.forced_only and e.category in c.categories and ev_eng.matches(e, st, content, flags)]
    rng.shuffle(pool)
    return [{"id": e.id, "title": e.title, "category": e.category} for e in pool[:n]]


def _sheet(content: Content, st) -> dict[str, Any]:  # noqa: ANN001
    """What the director may know about a character to change it: skills, things, chrome, people, standing."""
    from app.engine import blackwall as bw_eng

    def iname(kind: str, key: str) -> str:
        cat = {"weapon": content.weapons, "armor": content.armor, "quickhack": content.quickhacks}.get(kind, {})
        return _name(cat.get(key), key)

    veh = st.flags.get("vehicle")
    return {
        "stats": dict(st.stats),
        "skills": {k: v.level for k, v in st.skills.items()},
        "hp": st.hp, "mood": st.mood, "status": st.status, "fame": st.fame, "luck_pool": st.luck_pool,
        "role": st.role, "career": ({"key": st.career.key, "rank": st.career.rank} if st.career else None),
        "inventory": [{"key": i.key, "kind": i.kind, "name": iname(i.kind, i.key), "equipped": i.equipped}
                      for i in st.inventory][:20],
        "cyberware": [{"key": c.key, "name": _name(content.cyberware.get(c.key), c.key)} for c in st.cyberware][:20],
        "vehicle": (veh.get("key") if isinstance(veh, dict) else None),
        "people": [{"name": n.name, "kind": n.kind, "role": n.role, "relation": n.relation}
                   for n in st.npcs if n.alive][:8],
        "debt": st.debt_total,
        "faction_rep": {k: v for k, v in st.faction_rep.items() if v},
        "fixer_rel": {k: v for k, v in st.fixer_rel.items() if v},
        "bw_rot": bw_eng.rot(st),
        "dir_flags": sorted(k for k, v in st.flags.items() if k.startswith(FLAG_PREFIX) and v),
    }


def _catalog(content: Content, year: int) -> dict[str, Any]:
    def names(kind: str) -> dict[str, str]:
        try:
            got = content.available(kind, year)
        except (KeyError, AttributeError, TypeError):
            got = getattr(content, kind, {})
        return {k: _name(v, k) for k, v in got.items()}

    cw = {k: _name(v, k) for k, v in content.available("cyberware", year).items()
          if not getattr(v, "not_for_sale", False) and not getattr(v, "smasher_only", False)}
    return {
        "year": year,
        "skills": list(SKILLS),
        "weapons": names("weapons"), "armor": names("armor"),
        "vehicles": {k: _name(v, k) for k, v in content.vehicles.items()},
        "cyberware": cw,
        "tarot_buffs": {k: _name(v, k) for k, v in content.tarot.items()},
        "gangs": names("gangs"), "corps": names("corps"), "fixers": names("fixers"),
        "npc_tags": list(NPC_TAGS), "npc_templates": list(NPC_TEMPLATES),
    }


def _fx_format(content: Content) -> dict[str, Any]:
    c = cfg(content)
    return {
        "eddies": f"целое: не больше {int(c.fx_eddies_pct * 100)}% денег игрока (минимум {c.fx_eddies_floor})",
        "plain": {k: f"±{v}" for k, v in c.fx_caps.items()},
        "param": {f"{k}:<ключ>": f"±{v}" for k, v in c.fx_param_caps.items()},
        "grants": {
            "item_grant:<ключ из catalog.weapons|armor>": True, "item_take:<ключ вещи из inventory>": True,
            "vehicle_grant:<ключ из catalog.vehicles>": True, "cyberware_grant:<ключ из catalog.cyberware>": True,
            "buff:<ключ из catalog.tarot_buffs>": True, "queue_event:<id события>": True,
            f"flag_set:{FLAG_PREFIX}<имя>": True, f"flag_clear:{FLAG_PREFIX}<имя>": True,
            "add_npc:<шаблон из catalog.npc_templates>": True,
        },
        "later": "[{weeks: 1..8, chance: 0..1, effects: {...}, text: ..., fail_effects: {...}, fail_text: ..., "
                 "hint: ...}] — отложенное последствие через несколько месяцев игры",
        "keys_max": c.fx_keys,
        "forbidden": "смерть, тюрьма, драки (кроме start_combat в сценах), карьера, одержимость, долг",
        "skill_xp": f"skill_xp:<навык из catalog.skills>: ±{c.fx_param_caps.get('skill_xp', 0)}",
    }


async def _memory(session: AsyncSession) -> dict[str, Any]:
    got = await repo.ws_get(session, KEY_MEMORY, None)
    return dict(got) if isinstance(got, dict) else {}


async def dump(session: AsyncSession, content: Content, db_path: str, today: date) -> Path | None:
    from app.services import blackwall, control, corpstand, deeds, dyn, pmc, turf

    c = cfg(content)
    if await repo.ws_get(session, KEY_DUMP, None) == today.isoformat():
        return None
    rng = random.Random(today.toordinal())
    years: Counter[int] = Counter()
    players = []
    for row in await repo.living_characters(session):
        st = await repo.load_character(session, row.id)
        if st is None:
            continue
        years[st.current_year] += 1
        d = content.districts.get(st.district)
        players.append({
            "tg": int(row.player_tg_id), "name": f"{st.name} {st.surname}".strip(), "district": st.district,
            "district_name": d.name if d else st.district, "year": st.current_year,
            "eddies": st.eddies, "heat": st.heat, "street_cred": st.street_cred, "humanity": st.humanity,
            "known_for": await deeds.known_for(session, row.id),
            "sheet": _sheet(content, st),
            "menu": _menu(content, st, rng, c.menu_size),
        })
    year = years.most_common(1)[0][0] if years else 2077
    ctrl = await control.load(session, content)
    districts = {k: {"name": content.districts[k].name, **v} for k, v in ctrl.items() if k in content.districts}
    stand = await corpstand.book(session)
    corps = {k: {"name": content.corps[k].name, "stand": corpstand.bar(content, corpstand.rec_of(content, stand, k))}
             for k in content.corps if content.corps[k].kind == "megacorp"}
    conflict = await repo.ws_get(session, pmc.KEY, None)
    crews = [{"id": f.get("id"), "name": f["name"], "kind": f["kind"], "district": f.get("district"),
              "power": f.get("power")} for f in dyn.alive(await dyn.all_factions(session))]
    turf_book = await repo.ws_get(session, turf.KEY, {})
    from app.engine import blackwall as bw_eng
    from app.services import season

    season_st = await season.state(session, content)
    all_deeds = await deeds.all_deeds(session)
    shown = [{k: x.get(k) for k in ("at", "name", "kind", "text", "district")} for x in all_deeds[-c.deeds_shown:]]
    yesterday = date.fromordinal(today.toordinal() - 1).isoformat()
    fresh = [x for x in shown if str(x.get("at") or "") >= yesterday and x.get("kind") != "rumor"]
    mem = await _memory(session)
    scenes = await repo.ws_get(session, KEY_SCENES, {}) or {}
    data: dict[str, Any] = {
        "date": today.isoformat(), "year": year,
        "rules": c.rules,
        "out_file": f"out-{today.isoformat()}.json", "out_format": OUT_FORMAT,
        "scene_format": SCENE_FORMAT, "fx_format": _fx_format(content),
        "limits": {"hooks": c.max_hooks, "moves": c.max_moves, "move_step": c.move_step, "rumors": c.max_rumors,
                   "scenes": c.max_scenes, "fx": c.max_fx, "bw": c.max_bw, "bw_step": c.bw_step,
                   "corps": c.max_corps, "corp_step": c.corp_step, "crews": c.max_crews, "crew_step": c.crew_step,
                   "story": c.story_max},
        "quiet_day": not fresh,
        "memory": {"threads_and_notes": mem.get("notes"), "past": mem.get("past", []),
                   "yesterday_applied": mem.get("last_applied")},
        "library": [{"id": eid, "title": (rec.get("data") or {}).get("title"), "at": rec.get("at")}
                    for eid, rec in scenes.items()][-30:],
        "deeds": shown,
        "districts": districts,
        "blackwall": {"level_max": bw_eng.cfg(content).level_max, "city": await blackwall.city(session)},
        "corps": corps, "corp_conflict": conflict, "crews": crews,
        "player_turf": turf_book, "players": players,
        "season": {"n": season_st.get("n"), "left": season.left_text(season_st),
                   "top": [(r.get("name"), r.get("pts")) for _, r in season.table(season_st)[:5]]},
        "catalog": _catalog(content, year),
    }
    out = folder(db_path)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"in-{today.isoformat()}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    await repo.ws_set(session, KEY_DUMP, today.isoformat())
    return path


# -- cleaning what the director sent -----------------------------------------------------------------------------------


def _int(v: Any, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return 0


def _ref_ok(content: Content, base: str, param: str) -> bool:
    if base in ("item_grant", "item_take"):
        return param in content.weapons or param in content.armor
    if base == "vehicle_grant":
        return param in content.vehicles
    if base == "cyberware_grant":
        cw = content.cyberware.get(param)
        return cw is not None and not cw.not_for_sale and not cw.smasher_only
    if base == "buff":
        return param in content.tarot
    if base == "queue_event":
        return param in content.events and not param.startswith("uniq_")
    if base in ("flag_set", "flag_clear"):
        return param.startswith(FLAG_PREFIX) and bool(KEY_RE.match(param))
    if base == "add_npc":
        return param in NPC_TEMPLATES
    if base == "skill_xp":
        return param in SKILLS
    if base in ("faction_rep",):
        return param in content.gangs or param in content.corps
    if base == "rel_npc":
        return param in NPC_TAGS
    if base == "rel_fixer":
        return param in content.fixers
    if base == "path_xp":
        return param in {pd.key for pd in content.balance.paths.paths}
    return False


def clean_fx(content: Content, raw: Any, st=None, *, scene: bool = False,  # noqa: ANN001
             rejected: list[str] | None = None, depth: int = 0) -> dict[str, Any]:
    """The effects the balance allows, clamped. `st` = the character (money, hp, humanity guard); None = a scene."""
    c = cfg(content)
    rej = rejected if rejected is not None else []
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for key, val in list(raw.items())[: c.fx_keys]:
        key = str(key)
        base, param = split_effect_key(key)
        if base == "eddies":
            money = max(0, st.eddies) if st is not None else 0
            cap = max(c.fx_eddies_floor, int(money * c.fx_eddies_pct))
            n = _int(val, -cap, cap)
            if n:
                out[key] = n
        elif base == "start_combat" and scene:
            n = _int(val, 1, 3)
            if n:
                out[key] = n
        elif base in c.fx_caps and param is None:
            cap = int(c.fx_caps[base])
            n = _int(val, -cap, cap)
            if st is not None and base == "hp" and n < 0:
                n = max(n, -(max(0, st.hp - 1)))          # the director never kills
            if st is not None and base == "humanity" and n < 0:
                n = max(n, -(max(0, st.humanity - 10)))   # nor drives into cyberpsychosis
            if n:
                out[key] = n
        elif base in c.fx_param_caps and param:
            if not _ref_ok(content, base, param):
                rej.append(f"{key}: неизвестный ключ")
                continue
            cap = int(c.fx_param_caps[base])
            n = _int(val, -cap, cap)
            if n:
                out[key] = n
        elif base in c.fx_grants and param:
            if not _ref_ok(content, base, param):
                rej.append(f"{key}: неизвестный или запрещённый ключ")
                continue
            if base == "add_npc":
                out[key] = True
            elif val is True or val == 1 or str(val).lower() == "true":
                out[key] = True
        elif base == "later" and depth == 0 and isinstance(val, list):
            entries = []
            for e in val[:2]:
                if not isinstance(e, dict):
                    continue
                fx = clean_fx(content, e.get("effects"), st, rejected=rej, depth=1)
                ffx = clean_fx(content, e.get("fail_effects"), st, rejected=rej, depth=1)
                if not fx and not ffx:
                    continue
                rec: dict[str, Any] = {"weeks": _int(e.get("weeks"), 1, 8) or 1, "effects": fx,
                                       "text": str(e.get("text") or "Город вспомнил о тебе.")[:400]}
                try:
                    ch = float(e.get("chance", 1))
                    rec["chance"] = max(0.05, min(1.0, ch))
                except (TypeError, ValueError):
                    rec["chance"] = 1.0
                if ffx:
                    rec["fail_effects"] = ffx
                    rec["fail_text"] = str(e.get("fail_text") or "Обошлось.")[:400]
                if e.get("hint"):
                    rec["hint"] = str(e["hint"])[:200]
                entries.append(rec)
            if entries:
                out["later"] = entries
        else:
            rej.append(f"{key}: эффект не разрешён режиссёру")
    if "start_combat" in out and depth == 0 and not scene:
        out.pop("start_combat")
    try:
        from app.content.schemas import validate_effects

        validate_effects(dict(out))
    except ValueError as exc:
        rej.append(f"эффекты отброшены: {exc}")
        return {}
    return out


_SLUG = re.compile(r"[^a-z0-9_]+")


def _scene_id(raw: Any, today: date, n: int) -> str:
    s = _SLUG.sub("_", str(raw or "").lower()).strip("_")
    if s.startswith(PREFIX):
        s = s[len(PREFIX):]
    if not s or not s[0].isalpha():
        s = f"s{today:%m%d}_{n}" + (f"_{s}" if s else "")
    return (PREFIX + s)[:48]


def _clean_outcome(content: Content, raw: Any, rej: list[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw.get("text"):
        return None
    return {"text": str(raw["text"])[:900], "effects": clean_fx(content, raw.get("effects"), scene=True, rejected=rej)}


def _clean_choice(content: Content, raw: Any, rej: list[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw.get("label"):
        return None
    ch: dict[str, Any] = {"label": str(raw["label"])[:40]}
    if raw.get("violent"):
        ch["violent"] = True
    chk = raw.get("check")
    if isinstance(chk, dict):
        ch["check"] = {"stat": str(chk.get("stat", "COOL")).upper(), "dv": _int(chk.get("dv", 13), 8, 20) or 13}
        if chk.get("skill"):
            ch["check"]["skill"] = str(chk["skill"])
        ok, bad = _clean_outcome(content, raw.get("success"), rej), _clean_outcome(content, raw.get("fail"), rej)
        if ok is None or bad is None:
            return None
        ch["success"], ch["fail"] = ok, bad
    else:
        if not raw.get("text"):
            return None
        ch["text"] = str(raw["text"])[:900]
        ch["effects"] = clean_fx(content, raw.get("effects"), scene=True, rejected=rej)
    return ch


def build_scene(content: Content, raw: Any, today: date, n: int, rej: list[str]) -> Event | None:
    """A scene of the director as a real `Event` (forced_only: it comes only through a hook or queue_event)."""
    if not isinstance(raw, dict):
        return None
    base_id = str(raw.get("extends") or "")
    data: dict[str, Any]
    if base_id:
        base = content.events.get(base_id)
        if base is None:
            rej.append(f"scene {raw.get('id')}: extends неизвестного события {base_id}")
            return None
        data = base.model_dump(mode="json")
        data.update({"available_from": None, "available_until": None})
    else:
        data = {"category": "street", "choices": []}
    eid = _scene_id(raw.get("id") or (base_id and f"{base_id}_x"), today, n)
    data["id"] = eid
    for f in ("title", "text"):
        if raw.get(f):
            data[f] = str(raw[f])[: 60 if f == "title" else 1500]
    if raw.get("text_add") and data.get("text"):
        data["text"] = (str(data["text"]) + "\n\n" + str(raw["text_add"]))[:2000]
    if raw.get("category") in EVENT_CATEGORIES:
        data["category"] = raw["category"]
    if raw.get("faction"):
        f = str(raw["faction"])
        if f in content.gangs or f in content.corps:
            data["faction"] = f
    new_choices = [x for x in (_clean_choice(content, c, rej) for c in (raw.get("choices") or [])) if x]
    if new_choices:
        data["choices"] = new_choices
    add = [x for x in (_clean_choice(content, c, rej) for c in (raw.get("choices_add") or [])) if x]
    if add:
        data["choices"] = list(data.get("choices") or []) + add
    data["forced_only"] = True
    data["weight"] = 1
    data["public"] = bool(raw.get("public", data.get("public", False)))
    # every outcome (old ones of an extended event included) must still be inside the corridors
    if any(isinstance(ch, dict) and ch.get("check") and not ch.get("success") for ch in data.get("choices", [])):
        rej.append(f"scene {eid}: проверка без исходов")
        return None
    if not any(not _violent(ch) for ch in data.get("choices", [])):
        rej.append(f"scene {eid}: нужен выбор без драки")
        return None
    try:
        ev = Event.model_validate(data)
    except ValidationError as exc:
        rej.append(f"scene {eid}: {str(exc).splitlines()[0][:200]} | {exc.errors()[0].get('msg', '')[:150]}")
        return None
    return ev


def _violent(ch: dict[str, Any]) -> bool:
    if ch.get("violent"):
        return True
    outs = [ch.get("effects")] + [(ch.get(k) or {}).get("effects") for k in ("success", "fail")]
    return any("start_combat" in (fx or {}) for fx in outs)


# -- applying the answer -----------------------------------------------------------------------------------------------


async def _remember(session: AsyncSession, content: Content, today: date, story: str, rumors: list[str],
                    notes: Any, report: dict[str, Any], keep_notes: bool) -> None:
    c = cfg(content)
    mem = await _memory(session)
    past = [p for p in (mem.get("past") or []) if isinstance(p, dict) and p.get("date") != today.isoformat()]
    past.append({"date": today.isoformat(), "story": story[:1500], "rumors": rumors})
    mem["past"] = past[-c.memory_days:]
    if keep_notes and notes is not None:
        text = json.dumps(notes, ensure_ascii=False, default=str)
        if len(text) <= c.memory_max:
            mem["notes"] = notes
        else:
            report["rejected"].append(f"memory: больше {c.memory_max} знаков, оставлена вчерашняя")
    mem["last_applied"] = {k: v for k, v in report.items() if k != "date"}
    await repo.ws_set(session, KEY_MEMORY, mem)


def _write_report(db_path: str, today: date, report: dict[str, Any]) -> None:
    try:
        p = folder(db_path) / f"applied-{today.isoformat()}.json"
        p.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    except OSError:
        log.warning("director: cannot write the applied report")


async def collect(session: AsyncSession, content: Content, db_path: str, today: date, tz: str) -> list[str]:
    """Apply today's out file once. Returns the posts for the group."""
    from app.engine import events as ev_eng
    from app.engine.effects import apply_effects
    from app.services import blackwall, control, corpstand, deeds, dyn, game, notices
    from app.services.week import pending_events, set_pending

    c = cfg(content)
    if await repo.ws_get(session, KEY_DONE, None) == today.isoformat():
        return []
    path = folder(db_path) / f"out-{today.isoformat()}.json"
    src = folder(db_path) / f"in-{today.isoformat()}.json"
    report: dict[str, Any] = {"date": today.isoformat(), "story": False, "scenes": [], "hooks": [], "fx": [],
                              "moves": [], "blackwall": [], "corps": [], "crews": [], "rumors": 0, "rejected": []}
    if not path.exists() or not src.exists():
        now = datetime.now(ZoneInfo(tz))
        if src.exists() and now.hour >= c.fallback_hour and c.quiet_lines:
            await repo.ws_set(session, KEY_DONE, today.isoformat())
            line = random.Random(today.toordinal()).choice(c.quiet_lines)
            report["story"] = True
            report["rejected"].append("ответа режиссёра не было к fallback_hour: тихий день")
            await _remember(session, content, today, line, [], None, report, keep_notes=False)
            _write_report(db_path, today, report)
            return [line]
        return []
    try:
        out = json.loads(path.read_text(encoding="utf-8"))
        inp = json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(out, dict):
            raise ValueError("not an object")
    except (OSError, ValueError):
        log.warning("director: unreadable out file %s", path)
        await repo.ws_set(session, KEY_DONE, today.isoformat())
        return []
    await repo.ws_set(session, KEY_DONE, today.isoformat())
    rej: list[str] = report["rejected"]
    rng = random.Random(today.toordinal() * 7 + 1)
    posts: list[str] = []
    story = str(out.get("story") or "").strip()[: c.story_max]
    if story:
        posts.append(story)
        report["story"] = True

    # new scenes into the library
    book = dict(await repo.ws_get(session, KEY_SCENES, {}) or {})
    alias: dict[str, str] = {}
    for n, raw in enumerate(list(out.get("scenes") or [])[: c.max_scenes], start=1):
        ev = build_scene(content, raw, today, n, rej)
        if ev is None:
            continue
        content.events[ev.id] = ev
        book[ev.id] = {"at": today.isoformat(), "data": ev.model_dump(mode="json")}
        if isinstance(raw, dict) and raw.get("id"):
            alias[str(raw["id"])] = ev.id
        report["scenes"].append(ev.id)
    if len(book) > c.scenes_kept:
        for eid in sorted(book, key=lambda k: str(book[k].get("at")))[: len(book) - c.scenes_kept]:
            book.pop(eid, None)
    if report["scenes"]:
        await repo.ws_set(session, KEY_SCENES, book)

    def resolve(ev: str) -> str:
        if ev in alias:
            return alias[ev]
        if ev in content.events:
            return ev
        return PREFIX + ev if PREFIX + ev in content.events else ev

    known_tg = {int(p["tg"]) for p in inp.get("players", []) if p.get("tg") is not None}
    touched: dict[int, Any] = {}

    async def char(tg: int):  # noqa: ANN202
        if tg not in touched:
            touched[tg] = await game.get_active_character(session, tg)
        return touched[tg]

    # hooks
    used: set[int] = set()
    for h in list(out.get("hooks") or [])[: c.max_hooks]:
        try:
            tg, ev = int(h.get("tg")), resolve(str(h.get("event")))
        except (TypeError, ValueError, AttributeError):
            continue
        if tg in used or tg not in known_tg or ev not in content.events or ev.startswith("uniq_"):
            rej.append(f"hook {h.get('tg')}/{h.get('event')}: нет такого игрока или события")
            continue
        ch = await char(tg)
        if ch is None or not ev_eng.matches(content.events[ev], ch.state, content):
            rej.append(f"hook {tg}/{ev}: событие не подходит персонажу")
            continue
        q = pending_events(ch)
        if ev in q:
            continue
        set_pending(ch, [ev] + q if ev.startswith(PREFIX) else q + [ev])
        why = html.escape(str(h.get("why") or "").strip()[:200])
        notices.push(ch, "🎬 Город готовит тебе сцену: «" + html.escape(content.events[ev].title) + "»."
                     + (f" {why}" if why else ""))
        used.add(tg)
        report["hooks"].append({"tg": tg, "event": ev})

    # effects on the characters
    fx_done: set[int] = set()
    for item in list(out.get("fx") or [])[: c.max_fx]:
        try:
            tg = int((item or {}).get("tg"))
        except (TypeError, ValueError, AttributeError):
            continue
        if tg in fx_done or tg not in known_tg:
            rej.append(f"fx {tg}: нет такого игрока или второй fx")
            continue
        ch = await char(tg)
        if ch is None:
            continue
        local: list[str] = []
        fx = clean_fx(content, item.get("effects"), ch.state, rejected=local)
        rej.extend(f"fx {tg}: {x}" for x in local)
        if not fx:
            continue
        try:
            applied = apply_effects(ch.state, fx, content, rng, ch.state.current_year)
        except ValueError as exc:
            rej.append(f"fx {tg}: {exc}")
            continue
        from app.bot import views

        summary = views.effects_summary(applied, content)
        text = html.escape(str(item.get("text") or "Город вспомнил о тебе.").strip()[:400])
        notices.push(ch, "🏙 " + text + (("\n" + summary) if summary else ""))
        fx_done.add(tg)
        report["fx"].append({"tg": tg, "effects": fx, "why": str(item.get("why") or "")[:200]})

    for ch in touched.values():
        if ch is not None:
            await game.save_active(session, ch, tz)

    # the districts
    for m in list(out.get("moves") or [])[: c.max_moves]:
        dk = str((m or {}).get("district", ""))
        if dk not in content.districts:
            rej.append(f"move {dk}: нет такого района")
            continue
        g, k = _int(m.get("gang"), -c.move_step, c.move_step), _int(m.get("corp"), -c.move_step, c.move_step)
        if g or k:
            await control.adjust(session, content, dk, gang=g, corp=k)
            report["moves"].append({"district": dk, "gang": g, "corp": k})

    # the Blackwall in the city
    for b in list(out.get("blackwall") or [])[: c.max_bw]:
        dk = str((b or {}).get("district", ""))
        n = _int((b or {}).get("delta"), -c.bw_step, c.bw_step)
        if dk not in content.districts or not n:
            continue
        lvl = await blackwall._bump(session, content, dk, n)
        report["blackwall"].append({"district": dk, "delta": n, "level": lvl})

    # the corps
    stand = await corpstand.book(session)
    max_hp = corpstand.cfg(content).max_hp
    changed = False
    for x in list(out.get("corps") or [])[: c.max_corps]:
        key = str((x or {}).get("corp", ""))
        n = _int((x or {}).get("delta"), -c.corp_step, c.corp_step)
        if key not in content.corps or content.corps[key].kind != "megacorp" or not n:
            continue
        r = corpstand.rec_of(content, stand, key)
        if corpstand.down(r):
            continue
        r["hp"] = max(1, min(max_hp, int(r["hp"]) + n))
        stand[key] = r
        changed = True
        report["corps"].append({"corp": key, "delta": n, "hp": r["hp"]})
    if changed:
        await repo.ws_set(session, corpstand.KEY, stand)

    # the crews of the players
    crews = await dyn.all_factions(session)
    changed = False
    for x in list(out.get("crews") or [])[: c.max_crews]:
        try:
            fid = int((x or {}).get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        f = dyn.find(crews, fid)
        n = _int((x or {}).get("delta"), -c.crew_step, c.crew_step)
        if f is None or not f.get("alive") or not n:
            continue
        f["power"] = max(1, min(100, int(f.get("power") or 0) + n))
        changed = True
        report["crews"].append({"id": fid, "delta": n, "power": f["power"]})
    if changed:
        await dyn._save(session, crews)

    # the rumors
    kept: list[str] = []
    for r in list(out.get("rumors") or [])[: c.max_rumors]:
        t = str(r or "").strip()[:160]
        if t:
            await deeds.add(session, None, "rumor", t, w=2)
            kept.append(t)
    report["rumors"] = len(kept)

    await _remember(session, content, today, story, kept, out.get("memory"), report, keep_notes=True)
    _write_report(db_path, today, report)
    log.info("director %s: story=%s scenes=%d hooks=%d fx=%d moves=%d bw=%d corps=%d crews=%d rejected=%d",
             today, report["story"], len(report["scenes"]), len(report["hooks"]), len(report["fx"]),
             len(report["moves"]), len(report["blackwall"]), len(report["corps"]), len(report["crews"]), len(rej))
    return posts
