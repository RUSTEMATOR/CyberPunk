"""Screens of the braindance studio (owner request): shoot braindances after the books of Ozon671Games, watch premieres.

path (bd_director) -> studio (books) -> studiobook (title, shoot) ; activities -> premieres (watch, royalties to the author)
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen, on_text
from app.bot.ui.screen import Button, Screen, pack
from app.content.loader import Content
from app.db import session as db
from app.engine import studio as se
from app.services import game
from app.services import studio as svc
from app.services.game import ActiveCharacter

MSG = {
    se.R_PATH: "Студия только у тех, кто идёт путём брейнданс-режиссёра («Работа» → «Свой путь: фиксер и др.»).",
    se.R_RANK: "Для этого нужен ранг повыше.",
    se.R_AP: "ОД кончились. Нажми «Прожить месяц».",
    se.R_EDDIES: "Не хватает эдди на съёмку.",
    se.R_JAIL: "В камере брейнданс не снять. Хотя сюжет был бы.",
    se.R_DONE: "Один брейнданс за месяц: актёры, свет и монтаж. Следующий после «Прожить месяц».",
    se.R_GONE: "Этого уже нет.",
    svc.R_SEEN: "Этот брейнданс ты уже смотришь второй раз только в памяти.",
    svc.R_OWN: "Свой брейнданс смотреть бесплатно и без отчислений: ты его уже прожил(а) на съёмках.",
    svc.R_EDDIES: "На билет не хватает эдди.",
}


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


def _check(b) -> str:  # noqa: ANN001
    s = texts.STAT_LABELS.get(b.stat, b.stat)
    return s + (f" + {texts.SKILL_LABELS.get(b.skill, b.skill).lower()}" if b.skill else "")


def studio_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    c = se.cfg(content)
    st = ch.state
    lines = [style.header("braindance", "Студия брейнданса"),
             f"Эдди: {views.money(st.eddies)}. ОД: {st.ap}. Съёмка: {c.ap} ОД и {views.money(se.price(st, content))} эдди "
             "(актёры, установка, площадка). Один брейнданс за месяц.",
             "Две проверки: съёмка (зависит от книги) и монтаж (ТЕХ + техника). Чем лучше, тем больше звёзд: "
             "дистрибьютор платит по звёздам, а брейнданс выходит в прокат для всего города. С каждого зрителя — "
             f"{c.royalty_pct}% билета тебе.", ""]
    if note:
        lines += [note, ""]
    why = se.blocked(st, content, jailed=int(st.flags.get("jail_weeks", 0) or 0) > 0)
    if why is not None and why not in (se.R_EDDIES, se.R_AP):
        lines += [f"<i>{MSG.get(why, why)}</i>", ""]
    lines.append("<b>Книги Озона</b>")
    for i, b in enumerate(c.books, 1):
        lines.append(f"{i}. <b>{texts.esc(b.title)}</b> ({texts.esc(b.genre)}): {_check(b)}, сложность {b.dv}.")
    rows = pack([Button(str(i), "book", b.key) for i, b in enumerate(c.books, 1)], content.balance.ui.button_chars,
                max_per_row=4)
    rows.append([Button("Премьеры", "premieres"), Button(texts.BACK, "path")])
    return Screen("\n".join(lines), rows, name="studio")


def book_screen(content: Content, ch: ActiveCharacter, key: str, title: str, note: str | None = None) -> Screen:
    b = se.book(content, key)
    assert b is not None
    lines = [style.header("braindance", f"Брейнданс по книге «{texts.esc(b.title)}»"),
             f"<i>{texts.esc(b.logline)}</i>", "",
             f"Жанр: {texts.esc(b.genre)}. Съёмка: {_check(b)}, сложность {b.dv}. Монтаж: ТЕХ + техника.",
             f"Название: <b>{texts.esc(title)}</b>"]
    if note:
        lines += ["", note]
    rows = [[Button("Снимать", "go")],
            [Button("Другое название", "regen"), Button("Своё название", "own")],
            [Button(texts.BACK, "back")]]
    return Screen("\n".join(lines), rows, name="studiobook", state={"book": key, "title": title})


@on_screen("studio")
async def studio_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if action == "path":
        return await goto(ctx, "path")
    if action == "premieres":
        return await premieres_screen(ctx, back="studio")
    if action == "book" and arg and se.book(c, arg) is not None:
        why = se.blocked(ch.state, c, jailed=int(ch.state.flags.get("jail_weeks", 0) or 0) > 0)
        if why is not None:
            return studio_screen(c, ch, MSG.get(why, why))
        return book_screen(c, ch, arg, se.auto_title(c, arg, random.Random()))
    return studio_screen(c, ch)


@on_screen("studiobook")
async def studiobook_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    key = str(ctx.state.get("book", ""))
    title = str(ctx.state.get("title", ""))
    if se.book(c, key) is None or action == "back":
        return studio_screen(c, ch)
    if action == "regen":
        return book_screen(c, ch, key, se.auto_title(c, key, random.Random()))
    if action == "own":
        scr = book_screen(c, ch, key, title, f"Напиши название в чат, до {se.cfg(c).title_max_chars} символов.")
        scr.awaiting_text = "bd_title"
        return scr
    if action == "go":
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            res = await svc.shoot(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, key, title)
        if res.blocked is not None or res.shoot is None:
            return studio_screen(c, ch, MSG.get(res.blocked or "", res.blocked))
        r = res.shoot
        rec = res.record
        lines = [views.check_line(r.shot), views.check_line(r.edit),  # type: ignore[arg-type]
                 f"<b>«{texts.esc(rec['title'])}»</b> {se.stars_text(r.stars)}",
                 f"Дистрибьютор платит {views.money(r.pay)} эдди. Брейнданс вышел в прокат: билет "
                 f"{views.money(se.ticket(c, r.stars))} эдди, тебе {views.money(se.royalty(c, r.stars))} с каждого зрителя."]
        if res.applied is not None:
            summary = views.effects_summary(res.applied, c)
            if summary:
                lines.append(summary)
        who = texts.esc(rec["author_name"])
        ctx.ui.post(f"🎬 Премьера брейнданса: <b>«{texts.esc(rec['title'])}»</b> {se.stars_text(r.stars)}\n"
                    f"Режиссёр: {who}. Смотреть: «Активности» → «Премьеры брейндансов».", 12 * 3600)
        if r.stars >= 4:
            from app.services import loud

            async with db.session_scope() as s:
                await loud.post(s, ch.id, f"{rec['author_name']} выпускает брейнданс «{rec['title']}» на {r.stars} звёзд.")
        return studio_screen(c, ch, "\n".join(lines))
    return book_screen(c, ch, key, title)


@on_text("bd_title")
async def typed_title(ctx: Ctx, text: str) -> Screen | None:
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    key = str(ctx.state.get("book", ""))
    if se.book(c, key) is None:
        return studio_screen(c, ch)
    title = " ".join((text or "").split())[: se.cfg(c).title_max_chars]
    bad = [w for w in c.balance.ui.name_blocklist if w in title.lower()]
    if not title or bad:
        return book_screen(c, ch, key, str(ctx.state.get("title", "")), "Такое название не подойдёт.")
    return book_screen(c, ch, key, title)


# -- premieres -------------------------------------------------------------------------------------------------------------


async def premieres_screen(ctx: Ctx, note: str | None = None, back: str = "activities") -> Screen:
    c = ctx.content
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    async with db.session_scope() as s:
        lst = await svc.premieres(s)
    lines = [style.header("braindance", "Премьеры брейндансов"),
             "Брейндансы игроков по книгам Озона. Билет зависит от звёзд, настроение тоже. "
             f"{c.balance.paths.studio.royalty_pct}% билета уходит режиссёру. Эдди: {views.money(ch.state.eddies)}.", ""]
    if note:
        lines += [note, ""]
    if not lst:
        lines.append("Пока ни одной премьеры. Снять первый брейнданс может брейнданс-режиссёр: «Работа» → «Свой путь: фиксер и др.».")
    rows: list[list[Button]] = []
    picks: list[Button] = []
    for i, x in enumerate(lst, 1):
        b = se.book(c, str(x.get("book", "")))
        seen = ch.id in (x.get("views") or [])
        own = int(x.get("author_char", 0)) == ch.id
        tag = " · твой" if own else " · смотрено" if seen else ""
        lines.append(f"{i}. <b>«{texts.esc(str(x.get('title', '')))}»</b> {se.stars_text(int(x.get('stars', 1)))}{tag}\n"
                     f"   по книге «{texts.esc(b.title if b else '?')}», режиссёр {texts.esc(str(x.get('author_name', '')))}, "
                     f"зрителей {len(x.get('views') or [])}, билет {views.money(se.ticket(c, int(x.get('stars', 1))))}")
        if not seen and not own:
            picks.append(Button(str(i), "w", str(x.get("id"))))
    rows += pack(picks, c.balance.ui.button_chars, max_per_row=5)
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="premieres", state={"back": back})


@on_screen("premieres")
async def premieres_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    back = str(ctx.state.get("back", "activities"))
    if action == "back":
        return await goto(ctx, back if back in ("studio", "activities") else "activities")
    if action == "w" and arg and arg.isdigit():
        c = ctx.content
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            res = await svc.watch(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, int(arg))
        if res.blocked is not None:
            return await premieres_screen(ctx, MSG.get(res.blocked, res.blocked), back)
        b = se.book(c, str(res.record.get("book", "")))
        line = (f"Ты проживаешь «{texts.esc(str(res.record.get('title', '')))}» от первого лица. "
                + (f"<i>{texts.esc(b.logline)}</i>" if b else ""))
        if res.applied is not None:
            summary = views.effects_summary(res.applied, c)
            if summary:
                line += "\n" + summary
        return await premieres_screen(ctx, line, back)
    return await premieres_screen(ctx, None, back)
