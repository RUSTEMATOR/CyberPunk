"""The character card of the Mini App (docs/WEBAPP.md, W1): the numbers of the main menu as data.

Read only. Every value comes from the same function the menu header uses (`app/bot/views.py`, `engine/derived.py`),
so the app can never show a number the group window does not. `menu_screen` itself is never called: it takes
notices and runs one-time fixes, which must happen once, in the player's window.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import TYPE_CHECKING, Any

from app.webapp.render import emoji_to_plain, to_web_html

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

log = logging.getLogger(__name__)

WAIT_S = 2.0
_TAG_RE = re.compile(r"<[^>]+>")
_LEAD_ICON_RE = re.compile(r"^[^\w«\"(]{1,4}\s+")

UI: "UiKit | None" = None
_cache: dict[int, dict[str, Any] | None] = {}
_header: dict[int, str] = {}


def _city(content, st) -> str:  # noqa: ANN001
    from app.engine import place

    return place.place_of(st, content)


def _city_name(content, st) -> str:  # noqa: ANN001
    from app.engine import place

    return place.name_of(content, place.place_of(st, content))


def _plain(s: str) -> str:
    return html.unescape(_TAG_RE.sub("", emoji_to_plain(s or ""))).strip()


def _strip_icon(s: str) -> str:
    return _LEAD_ICON_RE.sub("", s, count=1)


async def build(ui: "UiKit", user_id: int) -> tuple[dict[str, Any] | None, str | None]:
    """(hud, header html) for the player's living character; (None, None) when there is none."""
    from app.bot import views
    from app.db import session as db
    from app.engine import derived
    from app.services import game
    from app.services import world as world_service

    content = ui.content
    actor = world_service.set_actor(user_id)
    try:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, user_id)
            if ch is None:
                return None, None
            year = await game.current_year(s, content)
        st = ch.state
        mood_text = _strip_icon(_plain(views.mood_line(content, st)))
        weather = _plain(views.weather_line(content, st, detail=False))
        hud = {
            "name": f"{st.name} {st.surname}".strip(),
            "age": int(st.age_years),
            "district": _plain(views.district_name(content, st.district)),
            "city": _city(content, st),  # owner request 30.10: the app paints itself in the colours of the city
            "city_name": _city_name(content, st),
            "year": int(year),
            "hp": int(st.hp),
            "hp_max": int(derived.max_hp(st, content)),
            "eddies": int(st.eddies),
            "debt": int(st.debt_total),
            "ap": int(st.ap),
            "mood": int(st.mood),
            "mood_text": mood_text,
            "heat": int(st.heat or 0),
            "heat_max": int(content.balance.character.heat_max),
            "cred": int(st.street_cred),
            "cred_max": int(content.balance.reputation.street_cred_max),
            "cred_title": views.cred_title(content, st.street_cred),
            "weather": weather,
        }
        hud.update(_sheet(content, st))  # W12: the character tab (radar, skills) and the district hero
        from app.webapp import living

        hud["netrun"] = living.netrun(content, ch)  # W8: the breach grid of a run in progress, None outside a run
        header = to_web_html(views.menu_header(content, st, year))
        return hud, header
    finally:
        world_service.reset_actor(actor)


def _sheet(content, st) -> dict[str, Any]:  # noqa: ANN001
    """W12: stats, skills and the district of the character tab, from the same functions the profile uses."""
    from app.bot.texts import SKILL_LABELS, STAT_LABELS
    from app.content.schemas import SKILLS, STATS
    from app.engine import derived

    out: dict[str, Any] = {"district_key": str(st.district or "")}
    try:
        out["humanity"] = int(st.humanity)
        out["humanity_max"] = int(derived.humanity_max(st, content))
    except Exception:  # noqa: BLE001 - the card works without it
        log.exception("webapp: humanity failed")
    try:
        out["stats"] = [{"k": k, "label": STAT_LABELS.get(k, k), "v": int(derived.stat_total(st, content, k)),
                         "base": int(st.stats.get(k, 0))} for k in STATS]
        out["stat_cap"] = int(content.balance.character.stat_cap)
        cap = int(content.balance.character.skill_cap)
        skills = []
        for k in SKILLS:
            lvl = int(st.skill_level(k))
            sk = st.skills.get(k)
            skills.append({"k": k, "label": SKILL_LABELS.get(k, k), "lvl": lvl,
                           "bonus": int(derived.skill_total(st, content, k)) - lvl,
                           "xp": int(sk.xp if sk else 0),
                           "need": int(derived.skill_xp_needed(lvl, content)) if lvl < cap else 0, "cap": cap})
        out["skills"] = skills
    except Exception:  # noqa: BLE001
        log.exception("webapp: stats failed")
    ctl = getattr(st, "district_control", None)
    if ctl:
        out["control"] = [int(ctl[0]), int(ctl[1])]
    return out


def get(user_id: int) -> dict[str, Any] | None:
    return _cache.get(user_id)


def menu_rest(user_id: int, screen_html: str) -> str | None:
    """The menu text without the lines the cards already show, or None when the header is not found as is."""
    header = _header.get(user_id)
    if not header or header not in screen_html:
        return None
    rest = screen_html.replace(header, "", 1)
    rest = re.sub(r"</blockquote>\n+", "</blockquote>", rest)  # a quote is a block of its own: no blank line after it
    return re.sub(r"\n{3,}", "\n\n", rest).strip("\n")


async def refresh(user_id: int) -> bool:
    """Recompute the card; True when it changed. Never raises."""
    ui = UI
    if ui is None:
        return False
    try:
        hud, header = await asyncio.wait_for(build(ui, user_id), timeout=10)
    except Exception:
        log.exception("webapp: hud failed for %s", user_id)
        hud, header = None, None
    changed = _cache.get(user_id) != hud or _header.get(user_id) != header
    _cache[user_id] = hud
    if header:
        _header[user_id] = header
    else:
        _header.pop(user_id, None)
    return changed


async def refresh_bounded(user_id: int) -> None:
    try:
        await asyncio.wait_for(refresh(user_id), timeout=WAIT_S)
    except Exception:
        pass
