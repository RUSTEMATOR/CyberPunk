"""The Hall of Legends in the city (owner request 25.10, docs/PLAN_ENDGAME2.md part 3): «Город» → «👑 Зал легенд»:
duels with the legends, the blessing of the own line."""

from __future__ import annotations

import random

from app.bot import texts
from app.bot.effects_text import mods_line
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.services import game, legend, lgcity
from app.services.game import ActiveCharacter

BLOCK = {"gone": "Такой легенды нет.", "jail": "Из камеры не выйти.", "busy": "Сначала разберись с тем, что уже ждёт.",
         "today": "Один поединок с легендой в день.", "ap": "Не хватает ОД.",
         "nobuild": "От этой легенды остались только истории: драться не с кем.",
         "noline": "В твоём роду ещё нет легенд.", "week": "Благословение уже получено на этой неделе."}
SHOW = 10


def hall_screen(content: Content, ch: ActiveCharacter, hall: list[dict], line: int, note: str | None = None) -> Screen:
    c = lgcity.cfg(content)
    lines = [style.header("legends", "Зал легенд"),
             "Те, кто ушёл, остаются в городе. Легенде можно бросить вызов: поединок один на один против того, кем она "
             f"была в день ухода ({c.duel_ap} ОД, раз в день). Проигрыш не убивает. Первая победа над легендой: "
             f"+{c.duel_eddies} эдди, слава и репутация, очки сезона."]
    rows: list[list[Button]] = []
    beaten = set(ch.state.flags.get(lgcity.FLAG_BEATEN) or [])
    start = max(0, len(hall) - SHOW)
    if not hall:
        lines += ["", "Зал пуст. Первая легенда ещё впереди."]
    for i in range(len(hall) - 1, start - 1, -1):
        h = hall[i]
        t = ", ".join(h.get("titles") or [])
        mark = " · ✅ побеждена" if lgcity.hall_key(h) in beaten else ""
        lines.append(f"👑 <b>{texts.esc(str(h.get('name')))}</b>" + (f" — {texts.esc(t)}" if t else "")
                     + f" · {h.get('date', '')}{mark}")
        if int(h.get("tg", 0) or 0) != ch.player_tg_id or str(h.get("name")) != f"{ch.state.name} {ch.state.surname}".strip():
            rows.append([Button(f"⚔️ {h.get('name')}"[:24], "duel", str(i), style="danger")])
    lines += ["", "<b>Благословение рода</b>"]
    if not line:
        lines.append("Когда в твоём роду появится легенда, она раз в неделю даст благословение.")
    else:
        cur = lgcity.blessing_now(ch.state)
        k = min(c.bless_cap, line)
        if cur is not None:
            lines.append("Сейчас действует: " + mods_line(cur.mods) + ".")
        lines.append(f"Легенд в роду: {line}. Раз в неделю одно благословение на {c.bless_days} дн., сила ×{k}.")
        for b in c.blessings:
            lines.append(f"• {texts.esc(b.name)}: {texts.esc(b.text)}.")
            rows.append([Button(b.name[:24], "bless", b.key)])
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="lghall")


@on_screen("lghall")
async def lghall_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "city", "open")
    c = ctx.content
    tz = ctx.ui.settings.timezone
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "duel" and arg and arg.isdigit():
            r = await lgcity.challenge(s, c, ch, random.Random(), year, tz, int(arg))
            if isinstance(r, str):
                note = BLOCK.get(r, r)
            else:
                from app.bot.handlers.combat import step_screen

                return step_screen(c, ch, r)  # type: ignore[arg-type]
        elif action == "bless" and arg:
            why = await lgcity.bless(s, c, ch, tz, arg)
            note = BLOCK.get(why, why) if why else "Легенда рода рядом. Благословение на неделю."
        hall = await legend.hall(s)
        line = await legend.legends_of(s, ctx.user_id)
    return hall_screen(c, ch, hall, line, note)
