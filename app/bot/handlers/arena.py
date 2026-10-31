"""The fighting arena (owner request 30.10, docs/PLAN_WORLD2.md phase 4). city -> arena -> the fight -> back.

A ladder of ten fighters, bare hands and arm chrome, a bet on yourself, the table of the players, the story of the ring.
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen, pack
from app.content.loader import Content
from app.db import session as db
from app.services import arena as svc
from app.services import game
from app.services.game import ActiveCharacter

BLOCKED = {
    svc.BLOCK_OFF: "Ринг закрыт.",
    svc.BLOCK_JAIL: "Из камеры на ринг не выпускают. Хотя во дворе тюрьмы свой ринг.",
    svc.BLOCK_FIGHT: "Сначала закончи бой, который уже идёт.",
    svc.BLOCK_DAY: "На сегодня боёв хватит: врач ринга не выпустит тебя третий раз за ночь.",
    svc.BLOCK_AP: "ОД кончились. Нажми «Прожить месяц».",
    svc.BLOCK_EDDIES: "На такую ставку не хватает эдди.",
    svc.BLOCK_BUSY: "Сначала разберись с событием, которое ждёт ответа.",
    svc.BLOCK_STORY_DAY: "Следующая глава истории ринга будет завтра.",
    svc.BLOCK_STORY_WIN: "Следующая глава откроется после новой победы на ринге.",
    svc.BLOCK_STORY_DONE: "История ринга рассказана до конца. Ринг живёт дальше.",
}
RULES = ("Голые руки, кастеты и хром в руках: стволов и программ на ринге нет. До нокаута: проигравший очнётся на матах, "
         "но живым. Победа — следующая ступень, призовые, уличная слава. Можно поставить на себя.")


def arena_screen(content: Content, ch: ActiveCharacter, tz: str, note: str | None = None) -> Screen:
    c = svc.cfg(content)
    r = svc.rec(ch)
    st = ch.state
    lines = [style.header("fight", "Арена"), f"<b>{texts.esc(svc.arena_name(content, ch))}</b>",
             f"Эдди: {views.money(st.eddies)} · ОД: {st.ap} · здоровье {st.hp}", ""]
    if note:
        lines += [note, ""]
    lines.append(f"<i>{RULES}</i>")
    n = svc.rank(ch)
    lines += ["", f"Ступень: <b>{min(n, len(c.ladder))} из {len(c.ladder)}</b> · побед {int(r.get('wins', 0) or 0)}, "
                  f"поражений {int(r.get('losses', 0) or 0)} · боёв сегодня {svc.fights_today(ch, tz)} из {c.per_day}"]
    f = svc.next_fighter(content, ch)
    if f is None:
        f = c.ladder[-1]
        lines.append("🏆 Ты чемпион. Защищаешь пояс против сильнейших.")
    lines += ["", f"Соперник: <b>{texts.esc(f.name)}</b>" + (f", {texts.esc(f.title)}" if f.title else ""),
              f"<i>{texts.esc(f.style)}</i>",
              f"Опасность {f.danger} из 5 · призовые {views.money(f.purse)} эдди · слава +{f.cred}"
              + (f", известность +{f.fame}" if f.fame else "")]
    rows: list[list[Button]] = []
    bets = [Button("Драться без ставки" if b == 0 else f"Ставка {views.money(b)}", "fight", str(b),
                   style="danger" if b == 0 else None) for b in c.bets if b <= st.eddies]
    rows += pack(bets, content.balance.ui.button_chars, max_per_row=2)
    story = svc.story_ready(content, ch, tz)
    done = int(r.get("story", 0) or 0)
    if int(r.get("wins", 0) or 0) + int(r.get("losses", 0) or 0) > 0:
        label = f"📜 История ринга · {done}/{len(c.story)}"
        rows.append([Button(label, "story", style="success" if story is None else None)])
    rows.append([Button("Таблица бойцов", "table")])
    rows.append([Button(texts.BACK, "city"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="arena")


async def table_screen(ctx: Ctx, ch: ActiveCharacter) -> Screen:
    c = ctx.content
    async with db.session_scope() as s:
        rows = await svc.table(s, c, ch)
    lines = [style.header("fight", "Арена: таблица"), ""]
    if not rows:
        lines.append("На ринге ещё никто из своих не дрался.")
    for i, r in enumerate(rows[:15], 1):
        champ = " 🏆" if r.rank >= len(svc.cfg(c).ladder) else ""
        me = " · ты" if r.me else ""
        lines.append(f"{i}. <b>{texts.esc(r.name)}</b>{champ} — ступень {r.rank}, побед {r.wins} ({texts.esc(r.where)}){me}")
    return Screen("\n".join(lines), [[Button(texts.BACK, "open")]], name="arena")


@on_screen("arena")
async def arena_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "city":
        return await goto(ctx, "city")
    c, tz = ctx.content, ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "fight":
            bet = int(arg) if arg and arg.isdigit() else 0
            r = await svc.start(s, c, ch, random.Random(), year, tz, bet)
            if r.blocked:
                return arena_screen(c, ch, tz, BLOCKED.get(r.blocked, r.blocked))
            from app.bot.handlers.combat import step_screen

            return step_screen(c, ch, r.step)
        if action == "story":
            why, ev = await svc.story_next(s, c, ch, tz)
            if why:
                return arena_screen(c, ch, tz, BLOCKED.get(why, why))
    if action == "story":
        return await goto(ctx, "evchoice", "resume")
    if action == "table":
        return await table_screen(ctx, ch)
    return arena_screen(c, ch, tz)
