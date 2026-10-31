"""A sortie through a piece of the district (owner request 24.10). city -> sortie (the map, the point under you).

While a sortie runs the menu opens it; a fight goes through the combat window and comes back here
(handlers/events.resume_screen).
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import derived
from app.engine import sortie as eng
from app.services import game
from app.services import sortie as svc
from app.services.game import ActiveCharacter

BLOCKED = {
    "jail": "Из камеры на вылазку не ходят.",
    "abroad": "Ты не в Найт-Сити.",
    "busy": "Сначала разберись с тем, что уже происходит.",
    "ap": "ОД кончились. Нажми «Прожить месяц».",
    "none": "Вылазки нет.",
    "blocked": "Путь перекрыт: разберись с тем, что здесь, или отойди назад.",
    "edge": "Дальше этот кусок района кончается.",
    "eddies": "Не хватает эдди.",
}


def intro_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    c = svc.cfg(content)
    d = content.districts.get(ch.state.district)
    title, *_ = svc.GOALS[svc.goal_kind(content, ch)]
    lines = [style.header("crime", "Вылазка по району"), f"ОД: {ch.state.ap} · здоровье {ch.state.hp}", ""]
    if note:
        lines += [note, ""]
    lines += [
        f"Кусок района «{texts.esc(d.name if d else ch.state.district)}» {c.size}×{c.size}, каждый раз новый. Видно только "
        f"клетки рядом. На вылазку {c.steps} шагов, потом темнеет.",
        "На улицах: бандиты на углах, кого-то грабят, тайники, растяжки, торговцы из-под полы. Бои настоящие, "
        "твой отряд дерётся рядом.",
        f"Твоя цель: <b>{texts.esc(title)}</b> (🎯 на карте, когда найдёшь).",
        f"Деньги копятся в кармане вылазки и достаются, когда выходишь через 🚪. Ушёл не через выход или стемнело — "
        f"половина потеряна. Разобрался со всеми бандитами и грабителями — бонус. Цена: {c.ap} ОД.",
    ]
    rows = [[Button(f"Выйти на улицы · {c.ap} ОД", "start", style="danger")], [Button(texts.BACK, "city")]]
    return Screen("\n".join(lines), rows, name="sortie")


def sortie_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    run = svc.run_of(ch)
    if run is None:
        return intro_screen(content, ch, note)
    st = ch.state
    d = content.districts.get(run["district"])
    title, gtext, *_ = svc.GOALS[run["goal"]["k"]]
    x, y = run["pos"]
    lines = [style.header("crime", f"Вылазка: {texts.esc(d.name if d else run['district'])}"),
             f"❤️ {st.hp}/{derived.max_hp(st, content)} · шагов {run['steps']} · "
             f"в кармане {run['eddies']} эдди · угроз осталось {eng.hostile_left(run)}", ""]
    lines += eng.grid_lines(run)
    lines += ["", f"<i>Ты здесь: {eng.FLAVORS[run['flavor'][eng.key(x, y)]]}.</i>"]
    if note:
        lines += ["", note]
    p = eng.here(run)
    rows: list[list[Button]] = []
    if p is not None:
        k = p["k"]
        lines.append("")
        if k == "goal":
            lines += [f"🎯 <b>{texts.esc(title)}</b>", texts.esc(gtext)]
        else:
            lines += [f"<b>{svc.TITLES.get(k, k)}</b>", svc.TEXTS.get(k, "")]
        c = svc.cfg(content)
        opts = svc.OPTIONS.get(k, [])
        for key, label in opts:
            if k == "trader" and key == "stim":
                label = f"Стим · {c.stim_price}"
            elif k == "trader" and key == "map":
                label = f"Карта района · {c.map_price}"
            rows.append([Button(label, "do", key, style="danger" if key == "fight" else None)])
    if p is None or p["k"] not in eng.HOSTILE:
        rows += [[Button("⬆️", "go", "n")], [Button("⬅️", "go", "w"), Button("⬇️", "go", "s"), Button("➡️", "go", "e")]]
    if eng.at_exit(run):
        rows.append([Button("🚪 Уйти с добычей", "leave")])
    else:
        rows.append([Button("Уйти отсюда (−половина)", "bail")])
    lines += ["", "<i>🟢 ты · 🚪 выход · 🎯 цель · ☠️ бандиты · ❗ грабят · 📦 тайник · ⚠️ ловушка · 🛒 торговец · "
                  "💰 сумка · ✖️ разобрано · ⬛ не видно</i>"]
    return Screen("\n".join(x for x in lines if x is not None), rows, name="sortie")


@on_screen("sortie")
async def sortie_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.bot.handlers.combat import step_screen

    c = ctx.content
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if action == "city" and svc.run_of(ch) is None:
            from app.bot.handlers.city import city_screen

            return city_screen(c, ch)
        year = await game.current_year(s, c)
        rng = random.Random()
        if action == "start":
            r = await svc.start(s, c, ch, rng, year, tz)
        elif action == "go" and arg:
            r = await svc.go(s, c, ch, rng, year, tz, arg)
        elif action == "do" and arg:
            r = await svc.act(s, c, ch, rng, year, tz, arg)
        elif action in ("leave", "bail"):
            r = await svc.leave(s, c, ch, rng, year, tz)
        else:
            return sortie_screen(c, ch)
    if r.step is not None:
        scr = step_screen(c, ch, r.step)
        if r.lines:
            scr = Screen("\n".join(r.lines) + "\n\n" + scr.text, scr.keyboard, name=scr.name, state=scr.state,
                         awaiting_text=scr.awaiting_text)
        return scr
    if r.blocked:
        return sortie_screen(c, ch, BLOCKED.get(r.blocked, r.blocked))
    if r.ended:
        return intro_screen(c, ch, "🏁 " + texts.esc(r.ended))
    note = "\n".join(texts.esc(x) for x in r.lines)
    if r.check is not None:
        note = views.check_line(r.check) + ("\n" + note if note else "")
    if r.applied is not None:
        extra = "\n".join(views.effects_lines(r.applied, c))
        note = note + ("\n" + extra if extra else "")
    return sortie_screen(c, ch, note or None)
