"""The window of the ambitions (owner request 25.10): «Профиль» → «Амбиции»."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.services import ambition as svc
from app.services import game
from app.services.game import ActiveCharacter

BLOCK = {"none": "Амбиции нет.", "ap": "Не хватает ОД.", "not_yet": "Ещё не готово.", "busy_amb": "Сначала закончи текущую амбицию.",
         "done": "Эта амбиция уже исполнена.", "target": "Нужно выбрать цель.", "gone": "Такой амбиции нет."}


def amb_screen(content: Content, ch: ActiveCharacter, r: dict | None, ok: bool, txt: str, note: str | None = None,
               hall: list[dict] | None = None) -> Screen:
    st = ch.state
    lines = [style.header("legends", "Амбиции"),
             "Большая цель, ради которой стоит жить дальше. Одна за раз, этапы на недели. Исполненная даёт титул "
             "легенды и открывает «Уйти легендой»."]
    t = svc.titles(st)
    if t:
        lines.append("Твои титулы: " + ", ".join(f"<b>{texts.esc(x)}</b>" for x in t) + ".")
    rows: list[list[Button]] = []
    if r is not None:
        a = svc.get(content, r["key"])
        i = int(r.get("stage", 0))
        lines += ["", f"<b>{texts.esc(a.name)}</b>" + (f" · цель: {texts.esc(content.corps[r['target']].name)}"
                                                         if r.get("target") in content.corps else ""),
                  f"<i>{texts.esc(a.text)}</i>"]
        for j, s in enumerate(a.stages):
            mark = "✅" if j < i else ("▶️" if j == i else "▫️")
            lines.append(f"{mark} {texts.esc(s.name)}" + (f" — {texts.esc(s.text)}" if j == i and s.text else ""))
        lines.append(f"Сейчас: {texts.esc(txt)}.")
        s = a.stages[i]
        label = {"pay": f"Заплатить {views.money(s.value)}", "check": "Попробовать"}.get(s.kind, "Проверить")
        rows.append([Button(label[:24], "go", style="success" if ok else None)])
        rows.append([Button("Бросить амбицию", "drop")])
    else:
        lines.append("")
        done = {x.get("key") for x in svc.done(st)}
        for a in svc.cfg(content).items:
            if a.key in done:
                continue
            lines.append(f"<b>{texts.esc(a.name)}</b> → {texts.esc(a.title)}. {texts.esc(a.text)} "
                         f"Этапов: {len(a.stages)}.")
            rows.append([Button(a.name[:24], "pick", a.key)])
    if hall:
        lines += ["", "<b>Зал легенд</b>"]
        for h in hall[-8:][::-1]:
            t = ", ".join(h.get("titles") or [])
            lines.append(f"👑 {texts.esc(str(h.get('name')))}" + (f" — {texts.esc(t)}" if t else "") + f" · {h.get('date', '')}")
    if note:
        lines += ["", note]
    rows.append([Button("🌅 Финалы жизни", "finale")])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="amb")


def target_screen(content: Content, year: int) -> Screen:
    corps = [k for k, c in content.available("corps", year).items() if c.kind == "megacorp"]
    rows = [[Button(content.corps[k].name[:24], "target", k)] for k in corps]
    rows.append([Button(texts.BACK, "open")])
    return Screen(f"{style.header('legends', 'Стереть корпорацию')}\nКакая корпорация должна исчезнуть из Найт-Сити?",
                  rows, name="amb")


@on_screen("amb")
async def amb_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "profile")
    if action == "finale":  # owner request 25.10
        return await goto(ctx, "endg", "open")
    c = ctx.content
    tz = ctx.ui.settings.timezone
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "pick" and arg:
            a = svc.get(c, arg)
            if a is not None and a.target:
                return target_screen(c, year)
            why = await svc.start(s, c, ch, tz, arg)
            note = BLOCK.get(why, why) if why else "Амбиция выбрана. Путь долгий."
        elif action == "target" and arg:
            why = await svc.start(s, c, ch, tz, "erase_corp", arg)
            note = BLOCK.get(why, why) if why else f"Цель выбрана: {texts.esc(c.corps[arg].name)}."
        elif action == "go":
            why, out, chk = await svc.advance(s, c, ch, random.Random(), year, tz)
            note = BLOCK.get(why, why) if why and not out else "\n".join(
                ([views.check_line(chk)] if chk else []) + [texts.esc(x) for x in out])
        elif action == "drop":
            await svc.drop(s, ch, tz)
            note = "Амбиция брошена."
        r, ok, txt = await svc.status(s, c, ch)
        from app.services import legend

        hall = await legend.hall(s)
    return amb_screen(c, ch, r, ok, txt, note, hall)
