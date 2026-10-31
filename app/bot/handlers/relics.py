"""The relics of the city (owner request 25.10, docs/PLAN_ENDGAME2.md part 6): «Город» → «💎 Реликвии»."""

from __future__ import annotations

from app.bot import texts
from app.bot.effects_text import mods_line
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.services import relic as svc


def relics_screen(content: Content, book: dict[str, dict]) -> Screen:
    lines = [style.header("💎", "Реликвии"),
             "Вещи с историей, по одной на весь город. Дают сильный бонус и титул. Добываются в экспедициях; финал "
             "жизни оставляет свою реликвию легенде в Зале, а поединок с легендой забирает её. Кто проигрывает "
             "стычку другому игроку, отдаёт одну реликвию победителю. Со смертью держателя реликвия теряется в городе "
             "и снова ждёт у своего источника.", ""]
    for r in content.balance.relics.items:
        h = book.get(r.key)
        who = ("в Зале легенд, у " + texts.esc(str(h.get("name")))) if h and h.get("hall") else \
            (texts.esc(str(h.get("name"))) if h else "свободна")
        lines.append(f"💎 <b>{texts.esc(r.name)}</b> — {who}. Титул «{texts.esc(r.title)}».")
        lines.append(f"   {texts.esc(r.text)} {mods_line(r.mods)}. Источник: {texts.esc(r.source)}.")
    rows = [[Button(texts.BACK, "back"), Button(texts.MENU, "menu")]]
    return Screen("\n".join(lines), rows, name="relics")


@on_screen("relics")
async def relics_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "city", "open")
    async with db.session_scope() as s:
        book = await svc.book(s)
    return relics_screen(ctx.content, book)
