"""The Net of the netrunners (owner request 23.10).

city -> net (the deck, the trace, the jobs) -> netrun (the breach grid / the ICE duel) -> net (the result)
                                         -> netdos (pick a player for a dossier)
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, on_screen
from app.bot.ui.screen import Button, Screen
from app.clock import kyiv_date
from app.content.loader import Content
from app.db import session as db
from app.engine import derived
from app.engine import net as ne
from app.services import game
from app.services import net as svc
from app.services.game import ActiveCharacter

BLOCKED = {
    "jail": "Из камеры в Сеть не выйти: деку забрали на входе.",
    "deck": "Без кибердеки в Сеть не войти. Деку ставит риппер, а нейропорт нужен под неё.",
    "fight": "Сначала разберись с боем.",
    "noblackwall": "Заслона ещё нет: Старая Сеть жива, и туда лучше не соваться.",
    "noheat": "Тебя и так нет в базе NCPD.",
    "ap": "ОД кончились. Нажми «Прожить месяц».",
    "gone": "Этого человека больше нет.",
    "notrace": "Следа нет, чистить нечего.",
    "eddies": "Не хватает эдди.",
    "bad": "Так нельзя.",
    "norun": "Сеанс уже закрыт.",
    "line": "Этот код не на активной линии. Подсвеченная линия — та, где стрелки.",
    "used": "Этот код уже взят.",
    "over": "Буфер полон.",
}
INTENT = {ne.STRIKE: "готовит удар", ne.PROBE: "прощупывает тебя", ne.HARDEN: "укрепляет слои"}
DAEMON_NAMES = ("Демон доступа", "Демон данных", "Демон ядра")
KIND_TITLES = {
    "gig": ("Заказ фиксера", "Камеры, замки, бухгалтерия склада: обычная работа нетраннера."),
    "ncpd": ("База NCPD", "Демон доступа −1 к розыску, данных ещё −1, ядро стирает дело целиком."),
    "blackwall": ("Нырок к Заслону", "Каждый демон стоит вчетверо дороже. Заслон заберёт человечность в любом случае."),
}


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


def _intro(year: int) -> str:
    if ne.old_net(year):
        return ("Старая Сеть: огромная, общая, дикая. Через неё можно дотянуться до любого банка мира, "
                "но в глубине живут ИИ-призраки и чужие чёрные ЛЕДы.")
    return ("После Падения Старой Сети осталась сеть подсетей: город, корпорации и банды держат свои. "
            "Дальше, за Чёрным заслоном, сидит то, что NetWatch запер навсегда.")


def net_screen(content: Content, ch: ActiveCharacter, year: int, note: str | None = None) -> Screen:
    c = ne.cfg(content)
    st = ch.state
    lines = [style.header("hack", "Сеть"), f"Эдди: {views.money(st.eddies)} · ОД: {st.ap} · розыск {st.heat}/5", ""]
    if note:
        lines += [note, ""]
    lines.append(f"<i>{_intro(year)}</i>")
    rows: list[list[Button]] = []
    deck = ne.deck_of(st, content)
    trace = ne.trace_now(st, content, kyiv_date())
    if deck is None:
        lines += ["", BLOCKED["deck"]]
        if trace > 0:
            lines.append(f"След NetWatch: {trace}/{c.trace_limit}, за прожитый месяц остывает на {c.trace_decay_per_month}.")
            rows.append([Button(f"Чистильщик · {c.cleaner_price}", "clean", "paid")])
        rows.append([Button(texts.BACK, "city"), Button(texts.MENU, "menu")])
        return Screen("\n".join(lines), rows, name="net")
    lines += ["",
              f"Дека: RAM {deck.ram_max + derived.mod(st, content, 'ram_max')}, буфер взлома {ne.buffer_size(st, content)}, "
              f"сила в Сети {ne.power(st, content)} (уровень целей {ne.tier(st, content)}).",
              f"След NetWatch: {style.bar(trace, c.trace_limit)} {trace}/{c.trace_limit}. За прожитый месяц остывает на {c.trace_decay_per_month}. "
              "Полный след — NetWatch приходит за тобой.", ""]
    ap = c.ap
    rows.append([Button(f"Заказ фиксера · {ap.get('gig', 1)} ОД", "go", "gig")])
    rows.append([Button(f"Сервер корпорации · {ap.get('corp', 2)} ОД", "go", "corp")])
    if st.heat > 0:
        rows.append([Button(f"Стереть розыск · {ap.get('ncpd', 1)} ОД", "go", "ncpd")])
    if trace > 0:  # owner report 24.10: the trace can be knocked down at once
        rows.append([Button(f"Замести следы · {c.clean_ap} ОД", "clean", "self"),
                     Button(f"Чистильщик · {c.cleaner_price}", "clean", "paid")])
    rows.append([Button(f"Досье на игрока · {ap.get('dossier', 1)} ОД", "dos")])
    if year >= c.blackwall_from:
        rows.append([Button(f"Нырок к Заслону · {ap.get('blackwall', 2)} ОД", "go", "blackwall", style="danger")])
    if can_ascend(st):  # owner request 24.10: the way out of the body is a choice, not only a random event
        if year >= c.blackwall_from:
            lines.append("⚫ Заслон знает тебя. Дверь открыта: можно оставить тело и стать ИИ за Чёрным заслоном.")
            rows.append([Button("Шагнуть за Заслон", "ascend", style="danger")])
        else:
            lines.append("⚫ Голоса из глубины Старой Сети знают тебя. Можно оставить тело и уйти к ним, стать ИИ-изгоем.")
            rows.append([Button("Уйти в Сеть навсегда", "ascend", style="danger")])
    else:
        from app.engine import blackwall as bw_engine

        lines.append(f"<i>Стать ИИ может только аватар Чёрного заслона. Цепочка аватаров: {bw_engine.stage(st)}/5, "
                     f"порча {bw_engine.rot(st)}/100. Подробности: «Город» → «Чёрный заслон».</i>")
    rows.append([Button(texts.BACK, "city"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="net")


def can_ascend(st) -> bool:  # noqa: ANN001
    """Owner request 24.10: becoming an AI is hard. Only an Avatar of the Blackwall (engine/blackwall.py) may go."""
    return bool(st.flags.get("bw_avatar"))


def ascend_screen(content: Content, year: int) -> Screen:
    cfg = content.balance.players.rogue_ai
    old = year < ne.cfg(content).blackwall_from
    head = "Уйти в Старую Сеть" if old else "Шагнуть за Чёрный заслон"
    voice = ("Голоса из глубины Старой Сети говорят, что тело тебе больше не нужно." if old else
             "Голос за Заслоном говорит, что тело тебе больше не нужно.")
    text = (f"{style.header('hack', head)}\n"
            f"{voice} Это дорога в один конец.\n\n"
            "• Человеческая жизнь заканчивается: вместо меню — окно ИИ.\n"
            f"• У ИИ {cfg.charges} действия в сутки: взлом нейропортов игроков (кража эдди), сбои в районах, рост ядра, "
            "рой как рейд города.\n"
            "• Город может собраться в рейд и стереть ядро. Тогда жизнь закрывается, а род продолжается, как после смерти.\n"
            "• Уйти из Сети можно и самому: «Раствориться».\n\n"
            "О твоём шаге узнает вся беседа.")
    return Screen(text, [[Button("Шагнуть. Пути назад нет", "ascend_yes", style="danger")], [Button(texts.BACK, "open")]],
                  name="net")


# -- the run ------------------------------------------------------------------------------------------------------------


def _breach_screen(content: Content, ch: ActiveCharacter, run: dict, note: str | None = None) -> Screen:
    b = run["b"]
    title, sub = KIND_TITLES.get(run["kind"], ("Взлом", ""))
    lines = [style.header("hack", f"Протокол взлома: {title}"), f"<i>{sub}</i>", ""]
    if note:
        lines += [note, ""]
    for i, seq in enumerate(b["d"]):
        mark = "✅" if b["ok"][i] else "▫️"
        lines.append(f"{mark} {DAEMON_NAMES[i]}: <code>{' '.join(seq)}</code>")
    slots = b["buf"] + ["··"] * (b["max"] - len(b["buf"]))
    lines += ["", f"Буфер: <code>{' '.join(slots)}</code>",
              f"Бери код в {'строке' if b['mode'] == ne.ROW else 'столбце'} {b['line'] + 1}: она отмечена стрелками. "
              "Следующий код — в столбце (строке) только что взятого."]
    active = set(ne.line_cells(b))
    used = {tuple(x) for x in b["used"]}
    rows: list[list[Button]] = []
    for r, row in enumerate(b["g"]):
        out = []
        for k, code in enumerate(row):
            if (r, k) in used:
                label = "··"
            elif (r, k) in active:
                label = f"›{code}‹"
            else:
                label = code
            out.append(Button(label, "c", f"{r}{k}"))
        rows.append(out)
    rows.append([Button("Отключиться", "out")])
    return Screen("\n".join(lines), rows, name="netrun")


def _duel_screen(content: Content, ch: ActiveCharacter, run: dict, note: str | None = None) -> Screen:
    d = run["d"]
    c = ne.cfg(content).ice
    lines = [style.header("hack", "Сервер корпорации: дуэль с ЛЕДом"),
             "<i>Атака бьёт щуп, щит держит удар, обход проходит под укреплённым слоем.</i>", ""]
    if note:
        lines += [note, ""]
    lines += [f"ЛЕД: {style.bar(d['ice'], d['ice_max'])} {d['ice']}/{d['ice_max']}",
              f"Твой мозг: {style.bar(d['brain'], d['brain_max'])} {d['brain']}/{d['brain_max']}",
              f"Ход {d['round']} из {c.rounds}.", ""]
    lines += [texts.esc(x) for x in d.get("log", [])]
    lines.append(f"<b>ЛЕД {INTENT[d['intent']]}.</b>" if d.get("seen") else "<b>ЛЕД скрывает намерение.</b>")
    rows = [[Button("Атака", "m", ne.ATTACK), Button("Щит", "m", ne.SHIELD), Button("Обход", "m", ne.BYPASS)],
            [Button("Отключиться", "out")]]
    return Screen("\n".join(lines), rows, name="netrun")


def run_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen | None:
    run = svc.run_of(ch)
    if run is None:
        return None
    return _breach_screen(content, ch, run, note) if "b" in run else _duel_screen(content, ch, run, note)


def _result_note(content: Content, res: svc.NetResult) -> str:
    parts = [views.check_line(res.check)] if res.check is not None else []  # type: ignore[arg-type]
    parts += [texts.esc(x) for x in res.lines]
    if res.applied is not None:
        sm = views.effects_summary(res.applied, content)
        if sm:
            parts.append(sm)
    return "\n".join(parts)


async def _after(ctx: Ctx, ch: ActiveCharacter, year: int, res: svc.NetResult) -> Screen:
    c = ctx.content
    if res.blocked:
        scr = run_screen(c, ch, BLOCKED.get(res.blocked, res.blocked))
        return scr or net_screen(c, ch, year, BLOCKED.get(res.blocked, res.blocked))
    if res.over:
        note = _result_note(c, res)
        if res.hunt:
            from app.bot.handlers.events import _fight_exit_screen

            return _fight_exit_screen(c, ch, note)
        return net_screen(c, ch, year, note)
    scr = run_screen(c, ch, _result_note(c, res) if res.lines else None)
    return scr or net_screen(c, ch, year)


@on_screen("net")
async def net_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if action == "city":
        from app.bot.handlers.city import city_screen

        return city_screen(c, ch)
    if svc.run_of(ch) is not None:
        return run_screen(c, ch)
    if action == "go" and arg:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            res = await svc.start(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, arg)
        return await _after(ctx, ch, year, res)
    if action == "dos":
        return await dossier_screen(ctx, ch, year)
    if action == "clean":
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            res = await svc.clean(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, arg == "paid")
        if res.blocked:
            return net_screen(c, ch, year, BLOCKED.get(res.blocked, res.blocked))
        return net_screen(c, ch, year, _result_note(c, res))
    if action in ("ascend", "ascend_yes"):
        if not can_ascend(ch.state):
            return net_screen(c, ch, year, "Заслон тебя не зовёт.")
        if int(ch.state.flags.get("jail_weeks", 0) or 0) > 0:
            return net_screen(c, ch, year, "Из камеры к Заслону не дотянуться: деку забрали на входе.")
        if action == "ascend":
            return ascend_screen(c, year)
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            ch.state.flags["ascend_ai"] = "oldnet" if year < ne.cfg(c).blackwall_from else True
            await game.save_active(s, ch, ctx.ui.settings.timezone)
        return await menu_screen(ctx)
    return net_screen(c, ch, year)


@on_screen("netrun")
async def netrun_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if svc.run_of(ch) is None:
        return net_screen(c, ch, year)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        rng, tz = random.Random(), ctx.ui.settings.timezone
        if action == "c" and arg and len(arg) == 2 and arg.isdigit():
            res = await svc.pick(s, c, ch, rng, year, tz, int(arg[0]), int(arg[1]))
        elif action == "m" and arg:
            res = await svc.move(s, c, ch, rng, year, tz, arg)
        elif action == "out":
            res = await svc.jack_out(s, c, ch, rng, year, tz)
        else:
            return run_screen(c, ch)
    return await _after(ctx, ch, year, res)


# -- the dossier ----------------------------------------------------------------------------------------------------------


async def dossier_screen(ctx: Ctx, ch: ActiveCharacter, year: int, note: str | None = None) -> Screen:
    c = ne.cfg(ctx.content)
    async with db.session_scope() as s:
        people = await svc.targets(s, ch)
    lines = [style.header("hack", "Досье на игрока"),
             f"Взлом личной сети: ИНТ + Нетраннинг против {c.dossier_dv} + нетраннинг цели. Покажет характеристики, "
             f"здоровье, счёт и хром. Провал цель заметит всегда, успех — в {c.dossier_notice_pct}% случаев.", ""]
    if note:
        lines += [note, ""]
    rows: list[list[Button]] = []
    for i, (cid, name) in enumerate(people, 1):
        lines.append(f"{i}. {texts.esc(name)}")
        rows.append([Button(f"Вскрыть {i}", "t", str(cid))])
    if not people:
        lines.append("Больше никого нет.")
    rows.append([Button(texts.BACK, "net"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="netdos")


@on_screen("netdos")
async def netdos_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if action == "t" and arg and arg.isdigit():
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            res = await svc.dossier(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, int(arg))
        if res.blocked:
            return await dossier_screen(ctx, ch, year, BLOCKED.get(res.blocked, res.blocked))
        if res.hunt:
            from app.bot.handlers.events import _fight_exit_screen

            return _fight_exit_screen(c, ch, _result_note(c, res))
        return await dossier_screen(ctx, ch, year, _result_note(c, res))
    return net_screen(c, ch, year)
