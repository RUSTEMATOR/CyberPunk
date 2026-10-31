"""The arcade (owner request 29.10, docs/PLAN_ARCADE.md): «Город» → «🕹 Аркада».

Five small games from the popular HTML5 list, each a move per button press in the player's one window:
Wordle («Взлом пароля»), a snake (Slither.io), the district guessing (GeoGuessr), the rhythm sequence (Friday
Night Funkin') and the town builder (Townscaper). The rules are in `engine/arcade.py`, the state and the money in
`services/arcade.py`.
"""

from __future__ import annotations

import random
from typing import Any

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen, on_text
from app.bot.ui.screen import Button, Screen
from app.clock import kyiv_date
from app.content.loader import Content
from app.db import session as db
from app.engine import arcade as eng
from app.services import arcade as svc
from app.services import game
from app.services.game import ActiveCharacter

esc = texts.esc

TILES = {"g": "🟩", "y": "🟨", "b": "⬛"}


def _money_line(content: Content, st: dict[str, Any], tz: str) -> str:
    cap = svc.cfg(content).daily_cap
    return f"Аркада на сегодня: {views.money(svc.earned_today(st, kyiv_date(tz)))} из {views.money(cap)} эдди."


def _paid_line(paid: int, record: bool) -> str:
    out = []
    if paid:
        out.append(f"Награда: +{views.money(paid)} эдди.")
    if record:
        out.append("Новый личный рекорд.")
    return " ".join(out)


def _back_row() -> list[Button]:
    return [Button(texts.BACK, "open"), Button(texts.MENU, "menu")]


# -- the menu and the records ------------------------------------------------------------------------------------------


def menu_view(content: Content, st: dict[str, Any], tz: str, note: str = "") -> Screen:
    lines = [style.header("🕹", "Аркада"),
             "Пять игр прямо в окне: ход — нажатие кнопки. В «🎮 HTML5» настоящие аркады из открытого кода, они "
             "открываются на весь экран. Деньги символические, главное — рекорды.", "", _money_line(content, st, tz)]
    if note:
        lines += ["", note]
    rows = [
        [Button("🔐 Пароль", "w"), Button("🐍 Змейка", "s")],
        [Button("🗺 Район", "g"), Button("🎤 Баттл", "b")],
        [Button("🏙 Дома", "t"), Button("🎮 HTML5", "web", style="success")],
        [Button("🏆 Рекорды", "top")],
        [Button(texts.BACK, "back"), Button(texts.MENU, "menu")],
    ]
    return Screen("\n".join(lines), rows, name="arcade")


def web_view(ctx: Ctx, st: dict[str, Any], note: str = "") -> Screen:
    """The open-source HTML5 games (docs/PLAN_ARCADE_WEB.md): a link button each, with the player's own pass."""
    c = ctx.content
    games = svc.web_games(c)
    lines = [style.header("🎮", "HTML5-аркады"),
             "Настоящие игры из открытого кода. Кнопка открывает игру, счёт после партии сам уходит в бот: рекорд и "
             "награда персонажу. Ссылка личная и живёт 12 часов.", ""]
    rows: list[list[Button]] = []
    for key, g in games.items():
        lines.append(f"<b>{esc(g.name)}</b> — {esc(g.about)} <i>({esc(g.license)})</i>")
        rows.append([Button(f"▶ {g.name}"[:24], "wplay", key,
                            url=svc.web_url(c, ctx.ui.settings.bot_token, ctx.user_id, key))])
    lines += ["", _money_line(c, st, ctx.ui.settings.timezone)]
    if note:
        lines += ["", note]
    rows.append(_back_row())
    return Screen("\n".join(lines), rows, name="arcade")


async def top_view(ctx: Ctx, tg_id: int) -> Screen:
    n = svc.cfg(ctx.content).top_size
    lines = [style.header("🏆", "Рекорды аркады"), "Тройка лучших в каждой игре и твой результат."]
    games = [(g, svc.GAME_NAMES[g], svc.GAME_UNITS[g]) for g in svc.GAMES]
    games += [("web:" + k, w.name, w.unit) for k, w in svc.web_games(ctx.content).items()]
    async with db.session_scope() as s:
        for key, name, unit in games:
            rows = await svc.top_list(s, key, n)
            mine = await svc.my_best(s, key, tg_id)
            best = " · ".join(f"{esc(who)} {val}" for who, val in rows) or "пока никого"
            lines.append(f"\n<b>{esc(name)}</b> ({unit}): {best}" + (f". Твой: {mine}" if mine else ""))
    return Screen("\n".join(lines), [_back_row()], name="arcade")


# -- Wordle --------------------------------------------------------------------------------------------------------------


def wordle_view(content: Content, w: dict[str, Any], note: str = "") -> Screen:
    answer = eng.word_of_day(svc.cfg(content).wordle.words, w["day"])
    lines = [style.header("🔐", "Взлом пароля"),
             "Пароль дня из пяти букв, один на всех. Шесть попыток. 🟩 буква на месте, 🟨 есть в пароле, но стоит "
             "в другом месте, ⬛ такой буквы нет."]
    tries = len(w["guesses"])
    lines.append("")
    for g in w["guesses"]:
        lines.append("".join(TILES[c] for c in eng.evaluate(g, answer)) + "  " + g.upper())
    for _ in range(svc.WORDLE_TRIES - tries):
        lines.append("⬜⬜⬜⬜⬜")
    if w["done"]:
        if w["win"]:
            lines += ["", f"Пароль взломан: {answer.upper()}, попыток {tries}." + (f" Награда: +{views.money(w['paid'])} эдди." if w["paid"] else "")]
        else:
            lines += ["", f"Пароль заблокирован. Это было слово {answer.upper()}. Новый придёт завтра."]
    else:
        lines += ["", f"Попытка {tries + 1} из {svc.WORDLE_TRIES}. Напиши слово в чат."]
    if note:
        lines += ["", note]
    return Screen("\n".join(lines), [_back_row()], name="arcade", awaiting_text=None if w["done"] else "arcade_word")


# -- the snake -------------------------------------------------------------------------------------------------------------


def snake_view(sn: dict[str, Any], note: str = "") -> Screen:
    size = int(sn["n"])
    grid = [["⬛"] * size for _ in range(size)]
    if sn["food"] is not None:
        fr, fc = sn["food"]
        grid[fr][fc] = "💾"
    for i, (r, c) in enumerate(sn["snake"]):
        grid[r][c] = "🟢" if i == 0 else "🟩"
    lines = [style.header("🐍", "Змейка"), "\n".join("".join(row) for row in grid),
             f"Длина: {len(sn['snake'])} · собрано чипов: {sn['score']}"]
    rows: list[list[Button]] = []
    if sn["alive"]:
        rows += [[Button("↑", "sm", "u")], [Button("←", "sm", "l"), Button("↓", "sm", "d"), Button("→", "sm", "r")],
                 [Button("Закончить", "sx")]]
    else:
        lines.append("Змейка собрала всё поле." if sn.get("won") else "Змейка разбилась.")
        rows.append([Button("Новая игра", "sn")])
    if note:
        lines.append(note)
    rows.append(_back_row())
    return Screen("\n".join(lines), rows, name="arcade")


# -- the rhythm sequence -----------------------------------------------------------------------------------------------------


def beat_view(b: dict[str, Any], note: str = "") -> Screen:
    lines = [style.header("🎤", "Рэп-баттл"),
             "Бит держит стрелки. Запомни последовательность и повтори её кнопками, с каждым раундом она длиннее."]
    rows: list[list[Button]] = []
    if not b["alive"]:
        lines += ["", f"Сбился на раунде {b['round']}. Пройдено раундов: {int(b['round']) - 1}."]
        rows.append([Button("Новый баттл", "bn")])
    elif b["phase"] == "show":
        lines += ["", f"Раунд {b['round']}:", "<b>" + "  ".join(eng.ARROWS[d] for d in b["seq"]) + "</b>"]
        rows.append([Button("Повторить", "bgo", style="success")])
    else:
        done = "●" * int(b["pos"]) + "○" * (len(b["seq"]) - int(b["pos"]))
        lines += ["", f"Раунд {b['round']}: {done}"]
        rows += [[Button("↑", "bp", "u")], [Button("←", "bp", "l"), Button("↓", "bp", "d"), Button("→", "bp", "r")]]
    if note:
        lines += ["", note]
    rows.append(_back_row())
    return Screen("\n".join(lines), rows, name="arcade")


# -- the district guessing --------------------------------------------------------------------------------------------------------


def geo_view(content: Content, year: int, g: dict[str, Any], note: str = "") -> Screen:
    lines = [style.header("🗺", "Угадай район"), f"Раунд {g['round']} из {g['total']} · очки: {g['points']}", ""]
    shown = g["text"][: int(g["clues"])]
    lines += [f"{i}. {esc(t)}" for i, t in enumerate(shown, 1)]
    rows: list[list[Button]] = []
    if g["done"]:
        lines += ["", note or f"Ответ: {content.districts[g['district']].name}."]
        last = int(g["round"]) >= int(g["total"])
        rows.append([Button("Итог" if last else "Дальше", "gn")])
    else:
        lines += ["", f"Верный ответ сейчас даёт {eng.geo_points(int(g['clues']))} оч. Какой это район?"]
        names = [Button(content.districts[k].name, "ga", k) for k in g["options"]]
        rows += [names[i:i + 2] for i in range(0, len(names), 2)]
        if int(g["clues"]) < len(g["text"]):
            rows.append([Button("Ещё подсказка", "gh")])
    rows.append(_back_row())
    return Screen("\n".join(lines), rows, name="arcade")


# -- the town builder ----------------------------------------------------------------------------------------------------------------


def town_view(grid: list[list[int]], note: str = "") -> Screen:
    lines = [style.header("🏙", "Застройка"),
             "Нажми на клетку, чтобы поставить здание, нажми ещё раз, чтобы сменить его. Зданий: "
             f"<b>{eng.town_buildings(grid)}</b>."]
    if note:
        lines.append(note)
    rows = [[Button(eng.TOWN_GLYPHS[v], "tc", f"{r}:{c}") for c, v in enumerate(row)] for r, row in enumerate(grid)]
    rows.append([Button("Очистить", "tx")] + _back_row())
    return Screen("\n".join(lines), rows, name="arcade")


# -- the handler -----------------------------------------------------------------------------------------------------------------------


async def _char(ctx: Ctx, s) -> ActiveCharacter | None:
    return await game.get_active_character(s, ctx.user_id)


@on_screen("arcade")
async def arcade_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    tz = ctx.ui.settings.timezone
    rng = random.Random()
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "city", "open")
    async with db.session_scope() as s:
        ch = await _char(ctx, s)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        st = await svc.load(s, ctx.user_id)
        day = kyiv_date(tz)

        if action == "top":
            pass  # rendered below, outside the session
        elif action == "web":
            return web_view(ctx, st)
        elif action == "wplay" and arg in svc.web_games(c):  # the Mini App presses the link button: a text link
            g = svc.web_games(c)[arg]
            url = svc.web_url(c, ctx.ui.settings.bot_token, ctx.user_id, arg)
            return web_view(ctx, st, f'<a href="{url}">▶ Открыть «{esc(g.name)}»</a>')
        elif action == "w":
            w = svc.wordle_state(c, st, day)
            await svc.store(s, ctx.user_id, st)
            return wordle_view(c, w)
        # -- the snake
        elif action in ("s", "sn"):
            sn = st.get("snake")
            if action == "sn" or not sn:
                sn = eng.new_snake(svc.cfg(c).snake.size, rng)
                st["snake"] = sn
                await svc.store(s, ctx.user_id, st)
            return snake_view(sn)
        elif action in ("sm", "sx"):
            sn = st.get("snake")
            if not sn:
                return menu_view(c, st, tz)
            note = ""
            res = "ok"
            if action == "sm" and sn["alive"]:
                res = eng.snake_step(sn, arg or sn["dir"], rng)
            elif action == "sx" and sn["alive"]:
                sn["alive"] = False
                res = "dead"
            if not sn["alive"]:
                paid, record = await svc.snake_finish(s, c, ch, st, rng, year, tz)
                note = _paid_line(paid, record)
            st["snake"] = sn
            await svc.store(s, ctx.user_id, st)
            return snake_view(sn, note)
        # -- the rhythm sequence
        elif action in ("b", "bn"):
            b = st.get("beat")
            if action == "bn" or not b:
                b = eng.new_beat(svc.cfg(c).beat.start_len, rng)
                st["beat"] = b
                await svc.store(s, ctx.user_id, st)
            return beat_view(b)
        elif action in ("bgo", "bp"):
            b = st.get("beat")
            if not b:
                return menu_view(c, st, tz)
            note = ""
            if action == "bgo" and b["alive"] and b["phase"] == "show":
                b["phase"], b["pos"] = "play", 0
            elif action == "bp" and b["alive"]:
                res = eng.beat_press(b, arg or "", rng)
                if res == "round":
                    note = f"Раунд пройден. Стрелок в следующем: {len(b['seq'])}."
            if not b["alive"]:
                paid, record = await svc.beat_finish(s, c, ch, st, rng, year, tz)
                note = _paid_line(paid, record)
            st["beat"] = b
            await svc.store(s, ctx.user_id, st)
            return beat_view(b, note)
        # -- the district guessing
        elif action in ("g", "gn"):
            g = st.get("geo")
            if not g or (action == "g" and g.get("settled")):
                g = svc.geo_new_round(c, year, rng, None)
            elif action == "gn" and g["done"]:
                if int(g["round"]) >= int(g["total"]):
                    paid, record = await svc.geo_finish(s, c, ch, st, rng, year, tz)
                    final = (f"Заход окончен. Очков: {g['points']}. " + _paid_line(paid, record)).strip()
                    st["geo"] = g
                    await svc.store(s, ctx.user_id, st)
                    return menu_view(c, st, tz, final)
                g = svc.geo_new_round(c, year, rng, g)
            st["geo"] = g
            await svc.store(s, ctx.user_id, st)
            return geo_view(c, year, g)
        elif action in ("gh", "ga"):
            g = st.get("geo")
            if not g or g["done"]:
                return menu_view(c, st, tz)
            note = ""
            if action == "gh":
                g["clues"] = min(len(g["text"]), int(g["clues"]) + 1)
            elif arg:
                right = arg == g["district"]
                pts = eng.geo_points(int(g["clues"])) if right else 0
                g["points"] = int(g["points"]) + pts
                g["done"], g["answered"] = True, arg
                name = c.districts[g["district"]].name
                note = f"Верно: {name}. +{pts} оч." if right else f"Нет, это {name}."
            st["geo"] = g
            await svc.store(s, ctx.user_id, st)
            return geo_view(c, year, g, note)
        # -- the town builder
        elif action in ("t", "tc", "tx"):
            size = svc.cfg(c).town.size
            grid = st.get("town")
            if not grid or len(grid) != size:
                grid = eng.new_town(size)
            if action == "tc" and arg and ":" in arg:
                r, _, col = arg.partition(":")
                if r.isdigit() and col.isdigit():
                    grid = await svc.town_tap(s, c, ch, int(r), int(col))
            elif action == "tx":
                grid = eng.new_town(size)
                st["town"] = grid
                await svc.store(s, ctx.user_id, st)
            elif action == "t":
                st["town"] = grid
                await svc.store(s, ctx.user_id, st)
            return town_view(grid)
        else:  # open
            return menu_view(c, st, tz)
    return await top_view(ctx, ctx.user_id)


@on_text("arcade_word")
async def arcade_word(ctx: Ctx, text: str) -> Screen | None:
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await _char(ctx, s)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        w, note = await svc.wordle_guess(s, ctx.content, ch, random.Random(), year, tz, text)
    return wordle_view(ctx.content, w, note)
