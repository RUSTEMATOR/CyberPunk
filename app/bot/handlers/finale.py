"""The window of the endings (owner request 25.10, docs/PLAN_ENDGAME2.md part 2): «Профиль» → «Конец пути» →
«Финалы жизни», and «Амбиции» → «Финалы»."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.services import finale as svc
from app.services import game, legend
from app.services.game import ActiveCharacter

BLOCK = {"none": "Финала нет.", "ap": "Не хватает ОД.", "not_yet": "Ещё не готово.", "busy_end": "Сначала закончи начатый финал.",
         "gone": "Такого финала нет.", "small": "Финалы открыты " + legend.RULE,
         "jail": "Из камеры не выйти.", "busy": "Сначала разберись с тем, что уже ждёт."}


def endg_screen(content: Content, ch: ActiveCharacter, r: dict | None, ok: bool, txt: str, eligible: bool,
                note: str | None = None) -> Screen:
    lines = [style.header("legends", "Финалы жизни"),
             "Долгая дорога к концу, о котором будут рассказывать. Этапы на недели, бои с элитой. Последний шаг "
             "закрывает жизнь персонажа своей причиной, записывает финал в Зал легенд и оставляет дар всем следующим "
             "наследникам рода."]
    rows: list[list[Button]] = []
    if r is not None:
        e = svc.get(content, r["key"])
        i = int(r.get("stage", 0))
        lines += ["", f"<b>{texts.esc(e.name)}</b> → {texts.esc(e.title)}", f"<i>{texts.esc(e.text)}</i>"]
        for j, s in enumerate(e.stages):
            mark = "✅" if j < i or r.get("ready") else ("▶️" if j == i else "▫️")
            lines.append(f"{mark} {texts.esc(s.name)}" + (f" — {texts.esc(s.text)}" if j == i and s.text and not r.get("ready") else ""))
        lines.append(f"Сейчас: {texts.esc(txt)}.")
        lines.append(f"Дар роду: {texts.esc(e.gift.text)}.")
        if r.get("ready"):
            rows.append([Button("🌅 Финал", "final", style="success")])
        else:
            s = e.stages[i]
            label = {"pay": f"Заплатить {views.money(s.value)}", "check": "Попробовать",
                     "fight": f"В бой · {svc.cfg(content).fight_ap} ОД"}.get(s.kind, "Проверить")
            rows.append([Button(label[:24], "go", style="danger" if s.kind == "fight" else ("success" if ok else None))])
        rows.append([Button("Бросить финал", "drop")])
    else:
        lines.append("")
        if not eligible:
            lines.append("Финалы открыты " + legend.RULE)
        for e in svc.cfg(content).items:
            lines.append(f"<b>{texts.esc(e.name)}</b> → {texts.esc(e.title)}. {texts.esc(e.text)} Этапов: {len(e.stages)}. "
                         f"Дар роду: {texts.esc(e.gift.text)}.")
            if eligible:
                rows.append([Button(e.name[:24], "pick", e.key)])
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="endg")


def final_confirm(content: Content, r: dict) -> Screen:
    e = svc.get(content, r["key"])
    return Screen(f"<b>{texts.esc(e.name)}</b>\nФинал закроет жизнь персонажа навсегда. Имя и титул «{texts.esc(e.title)}» "
                  f"уйдут в Зал легенд, род получит дар: {texts.esc(e.gift.text)}. Дальше — наследник.\nОтменить нельзя.",
                  [[Button("Да, это мой финал", "final_yes", style="success")], [Button("Нет, ещё поживу", "open")]],
                  name="endg")


@on_screen("endg")
async def endg_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "profile")
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
            note = BLOCK.get(why, why) if why else "Финал выбран. Дорога долгая."
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
            note = "Финал брошен."
        elif action == "final":
            r = svc.current(ch.state)
            if r is not None and r.get("ready"):
                return final_confirm(c, r)
        elif action == "final_yes":
            name = ch.state.name
            res = await svc.close(s, c, ch, tz, year)
            if res is not None:
                from app.bot.handlers.week import death_screen

                return death_screen(name, res[0], [])
        r, ok, txt = await svc.status(s, c, ch)
        eligible = r is not None or await legend.eligible(s, c, ch)
    return endg_screen(c, ch, r, ok, txt, eligible, note)
