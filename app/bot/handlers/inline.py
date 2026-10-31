"""Inline mode (owner request 26.10, docs/PLAN_LIVING.md stage 4): `@bot` in any chat shares a card of the game.

Results: the own character, the megacorps' standing, the Hall of Legends, the districts held by players. A query word
narrows them (персонаж, корпорации, легенды, районы). Inline mode must be switched on for the bot in BotFather
(/setinline) — without it Telegram never sends the queries and nothing here runs.
"""

from __future__ import annotations

import re

from aiogram import Router
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent

from app.bot import texts, views
from app.bot.ui.kit import UiKit
from app.db import repo
from app.db import session as db
from app.services import game

router = Router(name="inline")

KEYS = {"me": ("персонаж", "я", "профиль"), "corps": ("корпорации", "корп", "стойкость"),
        "legends": ("легенды", "зал"), "turf": ("районы", "район")}


async def _me(s, content, tg: int) -> tuple[str, str] | None:  # noqa: ANN001
    from app.services import deeds

    ch = await game.get_active_character(s, tg)
    if ch is None:
        return None
    st = ch.state
    d = content.districts.get(st.district)
    lines = [f"👤 <b>{texts.esc(views.full_name(st))}</b>, {views.years(st.age_years)}",
             f"📍 {texts.esc(d.name if d else st.district)} · {st.current_year} год",
             f"Уличная репутация {st.street_cred} · слава {st.fame} · человечность {st.humanity}"]
    marks = []
    if st.flags.get("avatar_announced"):
        marks.append("Аватар Чёрного заслона")
    if st.flags.get("borg_announced"):
        marks.append("борг")
    if marks:
        lines.append(" · ".join(marks))
    known = await deeds.known_for(s, ch.id, 3)
    if known:
        lines += ["", "<b>Город помнит</b>"] + [f"• {texts.esc(x)}" for x in known]
    return f"{views.full_name(st)} — карточка", "\n".join(lines)


async def _corps(s, content) -> tuple[str, str]:  # noqa: ANN001
    from app.services import corpstand

    b = await corpstand.book(s)
    lines = ["🏢 <b>Мегакорпорации Найт-Сити</b>"]
    for k, c in content.corps.items():
        if c.kind != "megacorp":
            continue
        lines.append(f"• {texts.esc(c.name)}: {corpstand.bar(content, corpstand.rec_of(content, b, k))}")
    return "Стойкость корпораций", "\n".join(lines)


async def _legends(s) -> tuple[str, str]:  # noqa: ANN001
    from app.services import legend

    hall = (await legend.hall(s))[-10:]
    lines = ["👑 <b>Зал легенд</b>"]
    for x in reversed(hall):
        t = ", ".join(x.get("titles") or [])
        lines.append(f"• {texts.esc(str(x.get('name', '')))}" + (f" — {texts.esc(t)}" if t else ""))
    if len(lines) == 1:
        lines.append("Пока пусто. Город ждёт первую легенду.")
    return "Зал легенд", "\n".join(lines)


async def _turf(s, content) -> tuple[str, str]:  # noqa: ANN001
    from app.services import turf

    book = await repo.ws_get(s, turf.KEY, {}) or {}
    lines = ["👑 <b>Районы, которые держат игроки</b>"]
    for k, r in (book.items() if isinstance(book, dict) else []):
        d = content.districts.get(k)
        if d is not None and isinstance(r, dict):
            who = " · ".join(str(x) for x in (r.get("name"), r.get("army")) if x)
            lines.append(f"• {texts.esc(d.name)}: {texts.esc(who)}")
    if len(lines) == 1:
        lines.append("Пока ни одного: все районы под бандами и корпорациями.")
    return "Районы игроков", "\n".join(lines)


@router.inline_query()
async def inline_cards(query: InlineQuery, ui: UiKit) -> None:
    content = ui.content
    q = (query.query or "").strip().lower()
    want = [k for k, words in KEYS.items() if any(w.startswith(q) or q.startswith(w) for w in words)] if q else list(KEYS)
    out: list[tuple[str, str, str]] = []
    async with db.session_scope() as s:
        if "me" in want:
            me = await _me(s, content, query.from_user.id)
            if me:
                out.append(("me", *me))
        if "corps" in want:
            out.append(("corps", *await _corps(s, content)))
        if "legends" in want:
            out.append(("legends", *await _legends(s)))
        if "turf" in want:
            out.append(("turf", *await _turf(s, content)))
    results = [InlineQueryResultArticle(
        id=f"{key}:{query.from_user.id}", title=title, description=re.sub(r"<[^>]+>", "", (text.split("\n") + [""])[1])[:60],
        input_message_content=InputTextMessageContent(message_text=text[:4000], parse_mode="HTML"),
    ) for key, title, text in out]
    await query.answer(results, cache_time=30, is_personal=True)
