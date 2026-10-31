"""Outcomes of the talks with NPCs (owner request 26.10: «диалоги ни на что не влияют. Делай вызов Claude Code раз в
3 часа, чтобы прорабатывать диалоги … пингануть, что если вы вели с кем-то диалоги, вот есть уже результат»).

1. Every exchange of a talk (handlers/npcchat.py) is kept in world_state `dialog_queue` under a dialog id.
2. `dump` (the bot, every few minutes): data/dialogs/pending.json — the talks quiet for `idle_min` and not judged yet,
   each with the character's sheet, the NPC, the whole talk and a menu of events the character may get; the rules and
   the outcome format (`OUT_FORMAT`).
3. A scheduled task (Claude Code on the owner's subscription, every 3 hours) reads it and writes
   data/dialogs/out-<anything>.json.
4. `collect` (the bot): every out file once: checks each outcome against the queue, the menu and the limits
   (balance.dialogs), sends it as a letter (player mail kind `dialog`, applied on the next open by services/players.
   deliver) and pings the player (in the group or, outside it, in private). The file goes to data/dialogs/done/.
Claude never changes a number itself: the bot keeps only what the limits allow, clamped.

Owner request 27.10 («пускай сценарист вытягивает ещё и из действий персонажа»): `harvest` takes the character's new
event_log rows (all deeds: events, activities, gigs, fights, crimes, shops, implants, city NPCs, other players …) into
the same queue, one `deeds` record per character per sitting; `dump` gives them to the task next to the talks
(docs/PLAN_DEEDS_OUTCOMES.md).
"""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

log = logging.getLogger(__name__)

KEY = "dialog_queue"
MAIL_KIND = "dialog"
DEEDS = "deeds"                    # a queue record of the character's deeds (owner request 27.10)
DEEDS_CURSOR = "deeds_cursor"      # the last event_log id already taken into the queue

LABELS = {
    "activity": "занятие", "work_hard": "работа на износ", "relation": "время с близким", "shop": "покупка",
    "crime": "преступление", "raid": "налёт на персонажа", "promotion": "повышение", "gig_result": "заказ фиксера",
    "implant_install": "установка импланта", "implant_remove": "снятие импланта", "tolerance_scan": "скан толерантности",
    "fight_end": "бой", "action": "крупное дело", "career_join": "новая карьера", "career_quit": "уход с карьеры",
    "arrest": "арест", "jail_lawyer": "адвокат в тюрьме", "jail_escape": "побег из тюрьмы",
    "jail_escape_failed": "неудачный побег", "player_drink": "выпивка с другим игроком",
    "player_vouch": "поручительство за другого игрока", "player_together": "время с другим игроком",
    "player_gift": "подарок другому игроку", "player_note": "записка другому игроку",
    "player_attack": "нападение на другого игрока", "software": "покупка софта", "housing": "новое жильё",
    "move": "переезд в другой район", "casino": "казино", "fishing": "рыбалка", "trauma_team": "контракт Trauma Team",
    "infection_cured": "лечение заражения", "clinic_heal": "лечение в клинике", "surrender": "сдача",
    "sortie": "вылазка в район", "secure_soul": "Secure Your Soul", "elysium_citizen": "гражданство Элизиума",
    "elysium_heist": "налёт на Элизиум", "maxtac_duty": "дежурство MaxTac", "maxtac_refusal": "отказ от вызова MaxTac",
    "vehicle_buy": "покупка машины", "dyn_destroyed": "уничтожение группировки", "race": "гонка",
    "libria_rebel": "бунт в Либрии", "chrono_install": "хроно-имплант", "city_deed": "дело с человеком города",
    "city_rampage": "бойня киберпсиха", "city_free": "свой вариант с человеком города", "city_service": "услуга в городе",
    "city_hire": "найм человека города", "event_resolved": "событие",
}
_DROP = {"text", "obituary_text", "who", "name", "surname"}

OUT_FORMAT = {
    "outcomes": [{
        "id": "id записи из dialogs (разговор kind: talk или дела kind: deeds)",
        "summary": "1-3 предложения игроку по-русски: что из этого разговора вышло на деле (на «ты», без чисел, "
                   "которых нет в effects)",
        "effects": {"eddies": "целое", "mood": "целое", "street_cred": "целое", "heat": "целое", "humanity": "целое",
                    "hp": "целое", "fame": "целое", "skill_xp:<навык>": "целое", "faction_rep:<фракция>": "целое"},
        "relation": "целое: насколько потеплели или остыли отношения с собеседником (только для talk)",
        "memory": "одна строка: что собеседник запомнил из разговора (только для talk)",
        "event": "необязательно: id события из menu персонажа, если разговор или дела прямо к нему ведут",
        "nothing": "true, если разговор или дела ни к чему не привели (тогда summary коротко, без effects)",
    }],
    "notes": "необязательно: заметки владельцу, если для исхода не хватило механики (что дописать)",
}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.dialogs


def folder(db_path: str) -> Path:
    return Path(db_path).resolve().parent / "dialogs"


async def queue(session: AsyncSession) -> dict[str, dict]:
    return dict(await repo.ws_get(session, KEY, {}) or {})


async def record(session: AsyncSession, content: Content, ch, npc, history: list[list[str]], did: str) -> None:  # noqa: ANN001
    """Keep the talk so far (called after every exchange)."""
    if not cfg(content).enabled or not did or not history:
        return
    q = await queue(session)
    rec = q.get(did) or {"id": did, "tg": int(ch.player_tg_id), "cid": int(ch.id), "status": "open",
                         "started": time.time()}
    rec.update({"who": ch.state.name, "npc": npc.name, "npc_kind": npc.kind, "npc_bio": str(npc.data.get("bio") or "")[:600],
                "relation": int(npc.relation), "history": [[a[:400], b[:600]] for a, b in history][-12:],
                "updated": time.time()})
    q[did] = rec
    await repo.ws_set(session, KEY, _prune(content, q))


def _epoch(dt: Any) -> float:
    from datetime import timezone

    if dt is None:
        return time.time()
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def _local(t: float, tz: str) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    try:
        return datetime.fromtimestamp(t, ZoneInfo(tz)).strftime("%d.%m %H:%M")
    except Exception:  # noqa: BLE001
        return time.strftime("%d.%m %H:%M", time.gmtime(t))


def _compact(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if k in _DROP:
            continue
        out[k] = v[:160] if isinstance(v, str) else v
    raw = json.dumps(out, ensure_ascii=False, default=str)
    return out if len(raw) <= 500 else {"short": raw[:500]}


def describe(content: Content, kind: str, p: dict) -> str:
    """One line in Russian: what the character did (for the scheduled task)."""
    base = LABELS.get(kind, kind)
    try:
        if kind == "event_resolved":
            ev = content.events.get(str(p.get("event", "")))
            if ev is None:
                return base
            line = f"событие «{ev.title}»"
            i = p.get("choice")
            if isinstance(i, int) and 0 <= i < len(ev.choices):
                line += f", выбор «{ev.choices[i].label}»"
            if p.get("ok") is not None:
                line += ", получилось" if p.get("ok") else ", не получилось"
            return line
        if kind == "city_deed":
            from app.engine import city as ce

            v = ce.verb(content, str(p.get("verb", "")))
            line = f"{(v.name if v else p.get('verb', '')).lower()}: {p.get('npc_name', '')} ({p.get('role', '')})"
            line += ", вышло" if p.get("ok") else ", не вышло"
            if p.get("fight"):
                line += ", дошло до драки"
            if p.get("killed"):
                line += ", человек убит"
            if p.get("said"):
                line += f"; игрок написал: «{str(p['said'])[:200]}»"
            return line
        if kind == "city_free":
            return f"свой вариант с {p.get('npc_name', '')} ({p.get('role', '')}): «{str(p.get('said', ''))[:200]}»"
        if kind == "gig_result":
            return f"заказ фиксера «{p.get('title', '')}», подход {p.get('approach', '')}: {p.get('outcome', '')}"
        if kind == "fight_end":
            return f"бой против {p.get('faction', '')}: {p.get('result', '')}, убито {p.get('kills', 0)}"
        if kind == "crime":
            line = f"преступление {p.get('kind', '')}: {'вышло' if p.get('ok') else 'сорвалось'}"
            return line + (", арест" if p.get("arrested") else "")
    except Exception:  # noqa: BLE001 - a label never breaks the queue
        log.exception("dialogs: describe %s", kind)
    return base


async def harvest(session: AsyncSession, content: Content, tz: str) -> int:
    """The character's new deeds from event_log into the queue: one record per character per sitting."""
    from sqlalchemy import func, select

    from app.db.models import Character, EventLog

    c = cfg(content)
    if not c.enabled or not c.deeds:
        return 0
    cur = await repo.ws_get(session, DEEDS_CURSOR, None)
    if cur is None:  # the first run: the old history is not judged
        top = (await session.execute(select(func.max(EventLog.id)))).scalar() or 0
        await repo.ws_set(session, DEEDS_CURSOR, int(top))
        return 0
    rows = list((await session.execute(
        select(EventLog).where(EventLog.id > int(cur), EventLog.character_id.is_not(None))
        .order_by(EventLog.id).limit(3000))).scalars())
    if not rows:
        return 0
    skip = set(c.deeds_skip)
    q = await queue(session)
    last: dict[int, str] = {}
    for k, v in sorted(q.items(), key=lambda kv: float(kv[1].get("updated", 0))):
        if v.get("kind") == DEEDS and v.get("status") == "open":
            last[int(v["cid"])] = k
    chars: dict[int, Any] = {}
    n = 0
    for row in rows:
        if row.kind in skip:
            continue
        cid = int(row.character_id)
        if cid not in chars:
            chars[cid] = await session.get(Character, cid)
        who = chars[cid]
        if who is None or not who.alive:
            continue
        t = _epoch(row.created_at)
        rec = q.get(last.get(cid, ""))
        if rec is None or t - float(rec.get("updated", 0)) >= c.deeds_idle_min * 60 \
                or len(rec.get("deeds") or []) >= c.deeds_max:
            rec = {"id": f"deeds-{cid}-{row.id}", "kind": DEEDS, "tg": int(who.player_tg_id), "cid": cid,
                   "status": "open", "started": t, "who": who.name, "npc": "", "deeds": []}
            q[rec["id"]] = rec
            last[cid] = rec["id"]
        rec["deeds"].append({"t": _local(t, tz), "kind": row.kind, "what": describe(content, row.kind, row.payload or {}),
                             "data": _compact(row.payload)})
        rec["updated"] = t
        n += 1
    await repo.ws_set(session, DEEDS_CURSOR, int(rows[-1].id))
    await repo.ws_set(session, KEY, _prune(content, q))
    return n


def _prune(content: Content, q: dict[str, dict]) -> dict[str, dict]:
    cutoff = time.time() - cfg(content).keep_days * 86400
    out: dict[str, dict] = {}
    for k, v in q.items():
        if float(v.get("updated", 0)) < cutoff:
            continue
        if v.get("status") == "done" and v.get("kind") == DEEDS and v.get("deeds"):
            v = {**v, "deeds": []}  # judged: the list is not needed any more
        out[k] = v
    return out


def _ready(c, d: dict, now: float) -> bool:  # noqa: ANN001
    if d.get("status") != "open":
        return False
    if d.get("kind") == DEEDS:
        return now - float(d.get("updated", 0)) >= c.deeds_idle_min * 60 and len(d.get("deeds") or []) >= c.deeds_min
    return now - float(d.get("updated", 0)) >= c.idle_min * 60 \
        and sum(len(a) for a, _ in d.get("history") or []) >= c.min_chars


def _menu(content: Content, st, rng: random.Random, n: int) -> list[dict[str, str]]:  # noqa: ANN001
    from app.engine import derived
    from app.engine import events as ev_eng

    flags = derived.event_flags(st, content)
    pool = [e for e in content.events.values() if not e.forced_only and ev_eng.matches(e, st, content, flags)]
    rng.shuffle(pool)
    return [{"id": e.id, "title": e.title, "category": e.category} for e in pool[:n]]


async def _item(session: AsyncSession, content: Content, d: dict, rng: random.Random) -> dict[str, Any] | None:
    """What the scenarist gets for one record: the character, the talk or the deeds, the menu of events."""
    from app.db.models import Character

    row = await session.get(Character, int(d["cid"]))
    st = await repo.load_character(session, int(d["cid"])) if row is not None and row.alive else None
    if st is None:
        return None
    dist = content.districts.get(st.district)
    deeds = d.get("kind") == DEEDS
    item: dict[str, Any] = {
        "id": d["id"], "tg": d["tg"], "kind": DEEDS if deeds else "talk",
        "character": {"name": f"{st.name} {st.surname}".strip(), "age": st.age_years, "year": st.current_year,
                      "district": dist.name if dist else st.district, "role": st.role, "eddies": st.eddies,
                      "heat": st.heat, "street_cred": st.street_cred, "humanity": st.humanity, "mood": st.mood,
                      "skills": {k: v.level for k, v in st.skills.items()}},
        "menu": _menu(content, st, rng, cfg(content).menu_size),
    }
    if deeds:
        item["deeds"] = list(d.get("deeds") or [])
    else:
        item["npc"] = {"name": d.get("npc"), "kind": d.get("npc_kind"), "bio": d.get("npc_bio"),
                       "relation": d.get("relation")}
        item["talk"] = [{"player": a, "npc": b} for a, b in d.get("history") or []]
    return item


async def dump(session: AsyncSession, content: Content, db_path: str) -> Path | None:
    """data/dialogs/pending.json with the talks ready to be judged (rewritten every time something changes)."""
    c = cfg(content)
    if not c.enabled:
        return None
    q = await queue(session)
    now = time.time()
    ready = [d for d in q.values() if _ready(c, d, now)]
    path = folder(db_path) / "pending.json"
    stamp = ",".join(sorted(d["id"] for d in ready))
    if await repo.ws_get(session, "dialog_dump", "") == stamp and path.exists():
        return None
    rng = random.Random(int(now // 3600))
    dialogs = []
    for d in ready:
        item = await _item(session, content, d, rng)
        if item is not None:
            dialogs.append(item)
    folder(db_path).mkdir(parents=True, exist_ok=True)
    data = {"made": time.strftime("%Y-%m-%d %H:%M:%S"), "rules": c.rules, "limits": dict(c.limits),
            "skills": ["firearms", "melee", "stealth", "netrunning", "tech", "medicine", "persuasion", "driving"],
            "factions": sorted(list(content.gangs) + list(content.corps)),
            "out_file": "out-<ГГГГММДД-ЧЧММ>.json", "out_format": OUT_FORMAT, "dialogs": dialogs}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    await repo.ws_set(session, "dialog_dump", stamp)
    return path


def _clamp_fx(content: Content, raw: Any) -> dict[str, int]:
    lim = cfg(content).limits
    out: dict[str, int] = {}
    if not isinstance(raw, dict):
        return out
    factions = set(content.gangs) | set(content.corps)
    skills = {"firearms", "melee", "stealth", "netrunning", "tech", "medicine", "persuasion", "driving"}
    for k, v in raw.items():
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        base, _, param = str(k).partition(":")
        if base == "skill_xp" and param not in skills:
            continue
        if base == "faction_rep" and param not in factions:
            continue
        bound = lim.get(base)
        if bound is None or n == 0:
            continue
        lo, hi = bound
        n = max(int(lo), min(int(hi), n))
        if n:
            out[str(k)] = n
    return out


def _apply(session: AsyncSession, content: Content, ui: "UiKit | None", rec: dict, o: dict, menu: set[str]) -> bool:
    """One outcome of the scenarist: checked against the limits and the menu, sent as a letter with a ping. A record
    that led to nothing is closed without a letter (owner request 27.10: no noise). True when a letter went out."""
    from app.db.models import PlayerMail

    c = cfg(content)
    if rec.get("status") != "open":
        return False
    summary = str(o.get("summary") or "").strip()[: c.summary_max]
    if o.get("nothing") or not summary:
        rec["status"] = "done"
        return False
    fx = _clamp_fx(content, o.get("effects"))
    rel_lo, rel_hi = c.limits.get("relation", [-15, 15])
    try:
        rel_d = max(int(rel_lo), min(int(rel_hi), int(o.get("relation") or 0)))
    except (TypeError, ValueError):
        rel_d = 0
    ev = str(o.get("event") or "")
    if ev and (ev not in menu or ev not in content.events):
        ev = ""
    deeds = rec.get("kind") == DEEDS
    if deeds:
        rel_d = 0
    payload = {"text": summary, "npc": rec.get("npc"), "fx": fx, "relation": rel_d,
               "memory": "" if deeds else str(o.get("memory") or "")[:200], "event": ev, "deeds": deeds}
    session.add(PlayerMail(to_tg_id=int(rec["tg"]), from_tg_id=0, from_char_id=None, to_char_id=int(rec["cid"]),
                           from_name="Город" if deeds else str(rec.get("npc") or ""), from_year=0, kind=MAIL_KIND,
                           payload=payload))
    rec["status"] = "done"  # owner report 28.10 «спамит»: no ping of its own, the letter pings (services/retention.py)
    return True


# -- the scenarist inside the bot (owner request 27.10 «не scheduled Claude», docs/PLAN_SCENARIST_BOT.md) --------------


async def prepare(session: AsyncSession, content: Content) -> list[dict[str, Any]]:
    """Up to judge_per_run ready records, each with what Claude needs (the calls go outside the session)."""
    c = cfg(content)
    if not c.enabled or not c.bot_judge:
        return []
    q = await queue(session)
    now = time.time()
    ready = sorted((d for d in q.values() if _ready(c, d, now)), key=lambda d: float(d.get("updated", 0)))
    rng = random.Random(int(now // 3600))
    out: list[dict[str, Any]] = []
    for d in ready[: c.judge_per_run]:
        item = await _item(session, content, d, rng)
        if item is None:
            d["status"] = "done"
            continue
        out.append({"id": d["id"], "menu": [m["id"] for m in item["menu"]], "payload": {
            "rules": c.rules, "limits": dict(c.limits),
            "skills": ["firearms", "melee", "stealth", "netrunning", "tech", "medicine", "persuasion", "driving"],
            "factions": sorted(list(content.gangs) + list(content.corps)),
            "out_format": OUT_FORMAT["outcomes"][0], "record": item}})
    await repo.ws_set(session, KEY, q)
    return out


async def judge(items: list[dict[str, Any]], year: int | None = None) -> list[tuple[str, dict | None, list[str]]]:
    """Claude decides every prepared record (no database session open). (id, outcome or None, menu)."""
    from app.narrator import narrator

    res: list[tuple[str, dict | None, list[str]]] = []
    for it in items:
        data = None
        try:
            data = await narrator.generate_json("scenarist", it["payload"], year=year)
        except Exception:  # noqa: BLE001 - the scenarist never breaks the job
            log.exception("dialogs: scenarist failed")
        o = (data.get("outcome") if isinstance(data, dict) and isinstance(data.get("outcome"), dict) else data)
        res.append((it["id"], o if isinstance(o, dict) else None, list(it["menu"])))
    return res


async def apply_judged(session: AsyncSession, content: Content, ui: "UiKit | None", db_path: str,
                       judged: list[tuple[str, dict | None, list[str]]]) -> int:
    c = cfg(content)
    q = await queue(session)
    sent = 0
    for rid, o, menu in judged:
        rec = q.get(rid)
        if rec is None:
            continue
        if o is None:  # Claude is off, out of budget or failed: next time, then give up
            rec["tries"] = int(rec.get("tries", 0) or 0) + 1
            if rec["tries"] >= c.judge_tries:
                rec["status"] = "done"
            continue
        if _apply(session, content, ui, rec, o, set(menu)):
            sent += 1
        if o.get("notes"):
            base = folder(db_path)
            base.mkdir(parents=True, exist_ok=True)
            with (base / "notes.md").open("a", encoding="utf-8") as fh:
                fh.write(f"\n## {time.strftime('%Y-%m-%d %H:%M')} {rid}\n{str(o['notes'])[:2000]}\n")
    await session.flush()
    await repo.ws_set(session, KEY, q)
    return sent


async def collect(session: AsyncSession, content: Content, ui: "UiKit | None", db_path: str) -> int:
    """Every out file once (the old scheduled task). Returns how many outcomes were sent."""
    c = cfg(content)
    base = folder(db_path)
    if not base.exists():
        return 0
    q = await queue(session)
    pending_path = base / "pending.json"
    menus: dict[str, set[str]] = {}
    if pending_path.exists():
        try:
            for d in json.loads(pending_path.read_text(encoding="utf-8")).get("dialogs", []):
                menus[str(d.get("id"))] = {str(m.get("id")) for m in d.get("menu", [])}
        except (OSError, ValueError):
            pass
    sent = 0
    done_dir = base / "done"
    for path in sorted(base.glob("out-*.json")):
        try:
            out = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("dialogs: unreadable %s", path)
            continue
        finally:
            done_dir.mkdir(exist_ok=True)
        for o in list((out or {}).get("outcomes") or [])[: c.max_per_file]:
            if isinstance(o, dict):
                rec = q.get(str(o.get("id")))
                if rec is not None and _apply(session, content, ui, rec, o, menus.get(str(o.get("id")), set())):
                    sent += 1
        if (out or {}).get("notes"):
            notes = base / "notes.md"
            with notes.open("a", encoding="utf-8") as fh:
                fh.write(f"\n## {path.name}\n{str(out['notes'])[:4000]}\n")
        path.rename(done_dir / path.name)
    await session.flush()
    await repo.ws_set(session, KEY, q)
    return sent
