"""Data of the "living city" screens (docs/PLAN_LIVING.md, «Контракт данных для Mini App»; docs/WEBAPP.md W5).

Read only. The bot owns every number: the map and the birth card come in their screen's `state`; the body scheme and
the last fight come from `app/services/citymap.py`; the breach grid from `services.net.run_of`. The fight lines are
written by the bot's own `views_combat.render_events`, so the replay says exactly what the fight window said.
"""

from __future__ import annotations

import html
import logging
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.webapp.render import to_web_html

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

log = logging.getLogger(__name__)

STATE_KEYS = {"citymap": ("map",), "birth": ("birth",)}  # what of a screen's state the app gets


def screen_state(name: str, state: dict[str, Any] | None) -> dict[str, Any] | None:
    keys = STATE_KEYS.get(name)
    if not keys or not isinstance(state, dict):
        return None
    out = {k: state[k] for k in keys if k in state}
    return out or None


def place(name: str, state: dict[str, Any] | None) -> str | None:
    """W12 themes: "clinic" for a licensed ripperdoc that keeps its patients clean, "under" for the rest.
    Read from the ripperdoc list of the content (underground flag, complication chance); None off the ripper screens."""
    if not name.startswith("rip") or not state or not state.get("doc"):
        return None
    try:
        from app.webapp import hud

        ui = hud.UI
        docs = getattr(ui.content, "ripperdocs", None) if ui is not None else None
        d = docs.get(state["doc"]) if isinstance(docs, dict) else None
        if d is not None:
            clean = not getattr(d, "underground", False) and float(getattr(d, "complication_chance", 1) or 0) < 0.15
            return "clinic" if clean else "under"
    except Exception:  # noqa: BLE001 - a theme is only looks
        log.exception("webapp: ripper place failed")
    return None


def netrun(content: Any, ch: Any) -> dict[str, Any] | None:
    """The breach grid or the ICE duel of the character's current run, as the bot keeps it."""
    try:
        from app.bot.handlers import net as net_ui
        from app.engine import net as ne
        from app.services import net as svc

        run = svc.run_of(ch)
        if not run:
            return None
        if "b" in run:
            b = run["b"]
            title, sub = net_ui.KIND_TITLES.get(run.get("kind"), ("Взлом", ""))
            return {"kind": "breach", "title": title, "sub": sub, "grid": b["g"], "used": [list(x) for x in b["used"]],
                    "active": [list(x) for x in ne.line_cells(b)], "daemons": [
                        {"name": net_ui.DAEMON_NAMES[i] if i < len(net_ui.DAEMON_NAMES) else f"Демон {i + 1}",
                         "seq": list(seq), "ok": bool(b["ok"][i])} for i, seq in enumerate(b["d"])],
                    "buffer": list(b["buf"]), "max": int(b["max"]), "mode": "row" if b["mode"] == ne.ROW else "col",
                    "line": int(b["line"])}
        d = run["d"]
        return {"kind": "duel", "ice": d["ice"], "ice_max": d["ice_max"], "brain": d["brain"],
                "brain_max": d["brain_max"], "round": d["round"], "rounds": ne.cfg(content).ice.rounds,
                "intent": net_ui.INTENT.get(d["intent"]) if d.get("seen") else None, "log": list(d.get("log", []))}
    except Exception:
        log.exception("webapp: netrun data failed")
        return None


async def body(ui: "UiKit", user_id: int) -> dict[str, Any] | None:
    from app.db import session as db
    from app.services import citymap, game

    async with db.session_scope() as s:
        ch = await game.get_active_character(s, user_id)
    if ch is None:
        return None
    out = citymap.body(ui.content, ch.state)
    hmax = None
    try:
        from app.engine import derived

        hmax = int(derived.humanity_max(ch.state, ui.content))
    except Exception:
        hmax = None
    out["humanity_max"] = hmax
    out["name"] = f"{ch.state.name} {ch.state.surname}".strip()
    return out


_TAG_RE = re.compile(r"<[^>]+>")


async def fight(ui: "UiKit", user_id: int) -> dict[str, Any] | None:
    """The last fight with the bot's own lines for every tick and everybody's health after it."""
    from app.db import session as db
    from app.services import citymap

    async with db.session_scope() as s:
        rec = await citymap.last_fight(s, user_id)
    if not rec:
        return None
    people = [rec.get("player") or {}] + list(rec.get("allies") or []) + list(rec.get("enemies") or [])
    names = {str(p.get("id")): str(p.get("name") or "") for p in people if p.get("id")}
    hp = {str(p.get("id")): int(p.get("max_hp") or 0) for p in people if p.get("id")}
    try:
        from app.bot import views_combat
    except Exception:
        views_combat = None  # type: ignore[assignment]
    shim = SimpleNamespace(tick=0, combatant=lambda cid: SimpleNamespace(name=names.get(str(cid), str(cid))))
    ticks = []
    for t in rec.get("ticks") or []:
        evs = list(t.get("ev") or [])
        lines: list[str] = []
        if views_combat is not None:
            try:
                shim.tick = int(t.get("tick") or 0)
                lines = [to_web_html(x) for x in views_combat.render_events(ui.content, shim, evs)]  # type: ignore[arg-type]
            except Exception as exc:
                log.info("webapp: fight lines fell back for tick %s: %s", t.get("tick"), exc)
                lines = []
        hits = []
        for e in evs:
            if e.get("k") == "hit" and e.get("to") is not None:
                to = str(e["to"])
                dmg = int(e.get("dmg") or 0)
                hp[to] = max(0, hp.get(to, 0) - dmg)
                hits.append({"by": str(e.get("by") or ""), "to": to, "dmg": dmg, "down": bool(e.get("down"))})
                if e.get("down"):
                    hp[to] = 0
        ticks.append({"tick": t.get("tick"), "lines": lines, "hits": hits, "dealt": t.get("dealt"),
                      "taken": t.get("taken"), "hp": dict(hp)})
    return {"faction": rec.get("faction"), "danger": rec.get("danger"), "result": rec.get("result"),
            "result_word": _result_word(rec.get("result")), "title": rec.get("title") or "", "elite": rec.get("elite"),
            "player": rec.get("player"), "allies": rec.get("allies") or [], "enemies": rec.get("enemies") or [],
            "ticks": ticks}


def _result_word(result: Any) -> str:
    try:
        from app.bot import views_combat

        return html.unescape(_TAG_RE.sub("", views_combat.fight_result_word(str(result or ""))))
    except Exception:
        return str(result or "")

