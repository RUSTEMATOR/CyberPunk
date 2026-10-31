"""«В голове» (owner request 26.10): the AI that lives in the character. «Профиль» → «🧠 В голове»."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import possession as eng
from app.services import game
from app.services import possession as svc
from app.services.game import ActiveCharacter

MOD_RU = {"stat.INT": "ИНТ", "stat.REF": "РЕФЛ", "check.gig_hack": "взломы", "check.combat": "бой", "ram_max": "ОЗУ"}


def possess_screen(content: Content, ch: ActiveCharacter, note: str | None = None, confirm: bool = False) -> Screen:
    st = ch.state
    r = eng.rec(st)
    c = eng.cfg(content)
    if r is None:
        return Screen("🧠 В голове тихо. Никого, кроме тебя." + (f"\n\n{note}" if note else ""),
                      [[Button(texts.BACK, "back")]], name="possess")
    t = eng.tier(content, r)
    sync = int(r.get("sync", 0))
    bar = "▰" * (sync // 10) + "▱" * (10 - sync // 10)
    nxt = next((x for x in c.tiers if x.at > sync), None)
    src = c.sources.get(str(r.get("src")))
    lines = [f"🧠 <b>В голове: {texts.esc(r['name'])}</b>",
             f"<i>{texts.esc(src.bio)}</i>" if src else "", "",
             f"Синхронизация {bar} {sync}%" + (f" · «{t.name}»" if t else ""),
             f"Доверие: {int(r.get('trust', 50))}" + (" · блокаторы: она молчит до конца месяца" if r.get("quiet") else ""),
             "Что даёт: " + (", ".join(f"{MOD_RU.get(k, k)} {v:+d}" for k, v in (t.mods if t else {}).items()) or "ничего"),
             (f"Дальше: «{nxt.name}» с {nxt.at}%." if nxt else "Дальше только одно: решить, кто вы друг другу."), "",
             f"Каждый прожитый месяц синхронизация растёт на {c.sync_month} (быстрее, если она доверяет), и она "
             f"просит о чём-то. На {c.final_at}% придётся решать: стать одним, отдать ей тело или отпустить."]
    if note:
        lines += ["", note]
    rows = [[Button("Поговорить с ней", "talk")]]
    if confirm:
        rows.append([Button(f"Да, вырезать · {views.money(c.remove_cost)}", "cut", "yes", style="danger")])
    else:
        rows.append([Button(f"Блокаторы · {views.money(c.suppress_cost)}", "quiet"),
                     Button("Вырезать у риппера", "cut")])
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(x for x in lines if x is not None), rows, name="possess")


@on_screen("possess")
async def possess_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            from app.bot.handlers.menu import menu_screen

            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "talk":
            n = eng.npc_of(ch.state)
            if n is not None:
                from app.bot.handlers.npcchat import open_chat

                return await open_chat(ctx, ch.state.npcs.index(n))
        if action == "quiet":
            why = await svc.suppress(s, c, ch, random.Random(), year, tz)
            return possess_screen(c, ch, "Не хватает эдди." if why == "eddies" else
                                  "Блокаторы приняты: синхронизация ниже, до конца месяца в голове тихо.")
        if action == "cut":
            if arg != "yes":
                return possess_screen(c, ch, "Риппер предупреждает: назад её не вернуть.", confirm=True)
            why = await svc.cut_out(s, c, ch, random.Random(), year, tz)
            if why == "eddies":
                return possess_screen(c, ch, "Не хватает эдди.")
            return possess_screen(c, ch, "Шесть часов на столе. В голове тишина. Человечность +5.")
    if action == "back":  # owner request 27.10: the screen opens from «🧬 Мой ИИ»
        return await goto(ctx, "aidash", "open")
    return possess_screen(c, ch)
