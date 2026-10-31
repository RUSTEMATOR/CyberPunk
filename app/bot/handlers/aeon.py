"""Aeon, the city of the eternal life (owner request: the «In Time» crossover).

city -> aeon (the clock, the exchange, the jobs, poker, a robbery, a gift of time)
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import chrono as ce
from app.services import chrono as svc
from app.services import game
from app.services.game import ActiveCharacter

TITLE = "Эон: город вечной жизни"
INTRO = ("Город-государство за стеной из стекла и бетона. Здесь изобрели часы под кожей: старость больше не убивает, "
         "но каждая неделя жизни списывается со счёта. Богачи в центре живут веками, в гетто у стены у людей по "
         "несколько дней на руке. Время — валюта: им платят за работу, им платят за кофе.")
BLOCKED = {
    "have": "Часы уже на тебе.",
    "none": "Без часов Эона это не сделать.",
    "eddies": "Не хватает эдди.",
    "time": "Столько времени у тебя нет. Последнюю неделю не продают.",
    "ap": "ОД кончились. Нажми «Прожить месяц».",
    "jail": "Из камеры в Эон не попасть.",
    "gone": "Этого больше нет.",
    "bad": "Так нельзя.",
}
AMOUNTS = (1, 4, 12)


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


def _clock_line(ch: ActiveCharacter) -> str:
    if not ce.has(ch.state):
        return "⏳ Часов Эона нет: живёшь, как все, и стареешь, как все."
    return f"⏳ На часах: <b>{ce.label(ch.state)}</b> Каждая неделя жизни списывает неделю."


def _status(ch: ActiveCharacter) -> str:
    return f"Эдди: {views.money(ch.state.eddies)} · ОД: {ch.state.ap}"


def _head(content: Content, ch: ActiveCharacter, note: str | None) -> list[str]:
    lines = [style.header("time", TITLE), _status(ch), _clock_line(ch), ""]
    if note:
        lines += [note, ""]
    return lines


def aeon_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    c = ce.cfg(content)
    lines = _head(content, ch, note) + [f"<i>{INTRO}</i>"]
    rows: list[list[Button]] = []
    if not ce.has(ch.state):
        lines += ["", f"Часы ставят в клинике «Хронос» за {views.money(c.implant_price)} эдди. На старте {c.implant_weeks} нед. "
                      "Снять их нельзя. Без часов работы Эона платят эдди по курсу биржи."]
        rows.append([Button(f"Поставить часы · {views.money(c.implant_price)}", "install", style="danger")])
    rows.append([Button("Работа за время", "jobs"), Button("Биржа времени", "ex")])
    rows.append([Button("Покер на время", "poker"), Button(f"Ограбить богача ({c.rob_ap} ОД)", "rob")])
    if ce.has(ch.state):
        rows.append([Button("Подарить время", "gift")])
    rows.append([Button(texts.BACK, "city"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="aeon")


def jobs_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    c = ce.cfg(content)
    lines = _head(content, ch, note) + ["<b>Работа за время</b>"]
    rows: list[list[Button]] = []
    for i, j in enumerate(c.jobs, 1):
        skill = texts.SKILL_LABELS.get(j.skill or "", "")
        stat = texts.STAT_LABELS.get(j.stat, j.stat)
        lines.append(f"{i}. <b>{texts.esc(j.name)}</b>: {j.ap} ОД, {stat}{' + ' + skill if skill else ''} против {j.dv}, "
                     f"{j.weeks[0]}–{j.weeks[1]} нед.")
        rows.append([Button(f"{i}. {j.name}", "job", j.key)])
    rows.append([Button(texts.BACK, "aeon"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="aeon")


def ex_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    c = ce.cfg(content)
    lines = _head(content, ch, note) + ["<b>Биржа времени</b>",
                                        f"Купить неделю: {views.money(c.buy_week)} эдди. Продать неделю: {views.money(c.sell_week)} эдди."]
    rows = [[Button(f"Купить {n} нед.", "buy", str(n)) for n in AMOUNTS],
            [Button(f"Продать {n} нед.", "sell", str(n)) for n in AMOUNTS],
            [Button(texts.BACK, "aeon"), Button(texts.MENU, "menu")]]
    return Screen("\n".join(lines), rows, name="aeon")


def poker_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    c = ce.cfg(content)
    unit = "нед." if ce.has(ch.state) else f"× {views.money(c.buy_week)} эдди"
    lines = _head(content, ch, note) + ["<b>Покер на время</b>",
                                        f"Стол в подвале казино. 1 ОД, ХЛАД + убеждение против {c.poker_dv}. Выигрыш удваивает ставку. "
                                        f"Ставка — недели жизни{'' if ce.has(ch.state) else ' (без часов — эдди по курсу)'}."]
    rows = [[Button(f"Ставка {n} {unit}"[:24], "bet", str(n)) for n in c.poker_stakes],
            [Button(texts.BACK, "aeon"), Button(texts.MENU, "menu")]]
    return Screen("\n".join(lines), rows, name="aeon")


async def gift_screen(ctx: Ctx, ch: ActiveCharacter, note: str | None = None, to: int | None = None) -> Screen:
    from app.services import players as pl_svc

    c = ctx.content
    lines = _head(c, ch, note) + ["<b>Подарить время</b>", "Время уходит письмом: получатель увидит его, когда откроет окно. "
                                  "Без часов у получателя время обменяют на эдди по курсу."]
    rows: list[list[Button]] = []
    if to is None:
        async with db.session_scope() as s:
            others = await pl_svc.others(s, c, ch, ctx.ui.settings.timezone)
        for o in others[:10]:
            rows.append([Button(o.name[:24], "to", str(o.char_id))])
        if not others:
            lines.append("Других живых персонажей нет.")
    else:
        rows.append([Button(f"{n} нед.", "give", f"{to}:{n}") for n in AMOUNTS])
    rows.append([Button(texts.BACK, "aeon"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="aeon")


def _result(content: Content, r: svc.ChronoResult, text: str | None = None) -> str:
    parts: list[str] = []
    if r.check is not None:
        parts.append(views.check_line(r.check))  # type: ignore[arg-type]
    if text:
        parts.append(style.quote(texts.esc(text)))
    if r.weeks:
        parts.append(f"⏳ Время: {r.weeks:+d} нед.")
    if r.applied is not None:
        sm = views.effects_summary(r.applied, content)
        if sm:
            parts.append(sm)
    parts.extend(texts.esc(x) for x in r.lines)
    return "\n".join(parts)


@on_screen("aeon")
async def aeon_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if action == "city":
        return await goto(ctx, "city")
    if action not in ("gift", "to", "give"):  # owner request 30.10: a gift of time goes over any border
        from app.bot.handlers.world import gate_screen, resident

        if not resident(c, ch, "aeon"):
            return gate_screen(c, ch, "aeon")
    if action == "jobs":
        return jobs_screen(c, ch)
    if action == "ex":
        return ex_screen(c, ch)
    if action == "poker":
        return poker_screen(c, ch)
    if action == "gift":
        return await gift_screen(ctx, ch)
    if action == "to" and arg and arg.isdigit():
        return await gift_screen(ctx, ch, to=int(arg))
    if action not in ("install", "job", "buy", "sell", "bet", "rob", "give"):
        return aeon_screen(c, ch)
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch1 = await game.get_active_character(s, ctx.user_id)
        if ch1 is None:
            return await menu_screen(ctx)
        ch = ch1
        if action == "install":
            r = await svc.install(s, c, ch, rng, year, tz)
        elif action == "job":
            r = await svc.work(s, c, ch, rng, year, tz, arg or "")
        elif action in ("buy", "sell") and arg and arg.isdigit():
            r = await svc.exchange(s, c, ch, rng, year, tz, int(arg) if action == "buy" else -int(arg))
        elif action == "bet" and arg and arg.isdigit():
            r = await svc.poker(s, c, ch, rng, year, tz, int(arg))
        elif action == "rob":
            r = await svc.rob(s, c, ch, rng, year, tz)
        elif action == "give" and arg and ":" in arg:
            to, n = arg.split(":", 1)
            r = await svc.gift(s, c, ch, tz, int(to), int(n))
        else:
            return aeon_screen(c, ch)
    if r.died:
        from app.bot.handlers.week import death_screen

        return death_screen(ch.state.name, r.death_cause, [_result(c, r)])
    if r.blocked is not None:
        note = BLOCKED.get(r.blocked, r.blocked)
    elif action == "install":
        note = _result(c, r, "Игла, щелчок, зелёные цифры проступают под кожей запястья. Ты перестал стареть. "
                             "Теперь ты просто тратишься.")
    elif action == "job":
        job = next((j for j in ce.cfg(c).jobs if j.key == arg), None)
        note = _result(c, r, (job.text if job else None) if r.ok else "Не вышло. Смену не засчитали.")
    elif action == "bet":
        note = _result(c, r, "Ты забираешь банк. Кто-то за столом только что постарел на твой выигрыш." if r.ok
                       else "Ставка уходит через стол. Цифры на руке становятся меньше.")
    elif action == "rob":
        note = _result(c, r, "Рукопожатие в толпе — и чужие годы перетекают на твою руку. Хранители времени опоздали."
                       if r.ok else "Хранители времени ловят тебя у моста. Бьют долго и со знанием дела.")
    elif action == "give":
        note = _result(c, r, "Ты прикладываешь запястье к экрану перевода. Время ушло.")
    else:
        note = _result(c, r)
    if action == "job":
        return jobs_screen(c, ch, note)
    if action in ("buy", "sell"):
        return ex_screen(c, ch, note)
    if action == "bet":
        return poker_screen(c, ch, note)
    return aeon_screen(c, ch, note)
