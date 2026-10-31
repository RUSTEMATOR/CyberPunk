"""The window of the expeditions (owner request 25.10, docs/PLAN_ENDGAME2.md part 4): «Город» → «🚀 Экспедиции»."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.services import expedition as svc
from app.services import game
from app.services.game import ActiveCharacter

BLOCK = {"none": "Экспедиции нет.", "ap": "Не хватает ОД.", "not_yet": "Ещё не готово.", "gone": "Такой экспедиции нет.",
         "busy_exp": "Сначала закончи начатую экспедицию.", "again": "Туда ещё рано: мир не забыл прошлый визит.",
         "jail": "Из камеры не выйти.", "busy": "Сначала разберись с тем, что уже ждёт."}


def exped_screen(content: Content, ch: ActiveCharacter, note: str | None = None, holders: dict | None = None) -> Screen:
    st = ch.state
    lines = [style.header("🚀", "Экспедиции"),
             "Другие миры для тех, кому мал город. Каждый мир отнимает свою часть силы: где-то не стреляет оружие, "
             "где-то не работает хром или программы. Проигранный бой срывает экспедицию. В конце — большая награда и "
             "реликвия мира, если её никто не держит."]
    rows: list[list[Button]] = []
    r = svc.current(st)
    holders = holders or {}
    if r is not None:
        e = svc.get(content, r["key"])
        i = int(r.get("stage", 0))
        ok, txt = svc.status(content, st, r)
        lines += ["", f"<b>{texts.esc(e.name)}</b>", f"<i>{texts.esc(e.text)}</i>", f"Правила мира: {texts.esc(e.rules_text)}."]
        for j, s in enumerate(e.stages):
            mark = "✅" if j < i else ("▶️" if j == i else "▫️")
            lines.append(f"{mark} {texts.esc(s.name)}" + (f" — {texts.esc(s.text)}" if j == i and s.text else ""))
        lines.append(f"Сейчас: {texts.esc(txt)}.")
        s = e.stages[i]
        label = {"pay": f"Заплатить {views.money(s.value)}", "check": "Попробовать",
                 "fight": f"В бой · {svc.cfg(content).fight_ap} ОД"}.get(s.kind, "Проверить")
        rows.append([Button(label[:24], "go", style="danger" if s.kind == "fight" else ("success" if ok else None))])
        rows.append([Button("Вернуться домой", "drop")])
    else:
        for e in svc.cfg(content).items:
            wait = svc.again_in(content, st, e.key)
            holder = holders.get(e.relic) if e.relic else None
            lines += ["", f"<b>{texts.esc(e.name)}</b>. {texts.esc(e.text)}", f"Правила: {texts.esc(e.rules_text)}.",
                      f"Этапов: {len(e.stages)}. Награда: {views.money(int(e.reward.get('eddies', 0) or 0))}"
                      + (f", реликвия у {texts.esc(holder)}" if holder else (", реликвия свободна" if e.relic else ""))
                      + (f". Снова через {wait} нед." if wait else ".")]
            if not wait:
                rows.append([Button(e.name[:24], "pick", e.key)])
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="exped")


@on_screen("exped")
async def exped_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
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
        if action == "pick" and arg:
            why = await svc.start(s, c, ch, tz, arg)
            note = BLOCK.get(why, why) if why else "Экспедиция начата."
        elif action == "go":
            res = await svc.advance(s, c, ch, random.Random(), year, tz)
            if not isinstance(res, tuple):
                from app.bot.handlers.combat import step_screen

                return step_screen(c, ch, res)  # type: ignore[arg-type]
            why, out, chk = res
            note = BLOCK.get(why, why) if why and not out else "\n".join(
                ([views.check_line(chk)] if chk else []) + [texts.esc(x) for x in out])
        elif action == "drop":
            await svc.drop(s, ch, tz)
            note = "Экспедиция брошена. Ты дома."
        from app.services import relic

        holders = await relic.holder_names(s)
    return exped_screen(c, ch, note, holders)
