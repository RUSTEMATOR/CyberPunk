"""The cell in the window (PLAN 9.5): the reduced menu of a jailed character.

jail -> [profile | training (activities) | yard fight (combat) | contacts (relations, lawyer: jailcontacts) |
escape (lifechk -> jailout) | live weeks (week)]. While the sentence runs, the main menu leads here.
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot import texts_life as T
from app.bot.handlers import lifecheck as lc
from app.bot.handlers.menu import menu_screen
from app.bot.ui.callbacks import Ctx, goto, has_screen, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import derived
from app.engine import jail as eng
from app.engine.state import CharacterState
from app.services import events as ev_service
from app.services import game
from app.services import jail as svc
from app.services.game import ActiveCharacter
from app.services.lifecheck import LifeResult


def jail_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st: CharacterState = ch.state
    cfg = content.balance.jail
    lines = [T.JAIL_TITLE, T.JAIL_STATUS.format(
        weeks=eng.weeks_left(st), eddies=views.money(st.eddies), ap=st.ap, hp=st.hp, hpmax=derived.max_hp(st, content))]
    lines.append(T.JAIL_INTRO.format(n=cfg.weeks_per_press))
    lines.append(T.JAIL_COSTS.format(fight=cfg.fight_ap, escape=cfg.escape.ap))
    if eng.isolated(st):
        lines.append(T.JAIL_ISOLATION)
    if note:
        lines += ["", note]
    rows = [
        [Button(T.JAIL_BTN_PROFILE, "profile"), Button(T.JAIL_BTN_TRAIN, "train")],
        [Button(T.JAIL_BTN_FIGHT, "fight"), Button(T.JAIL_BTN_CONTACTS, "contacts")],
        [Button("Работа", "work"), Button("Занятия", "pastime")],
    ]
    if not eng.isolated(st):
        rows.append([Button(T.JAIL_BTN_ESCAPE, "escape")])
    rows.append([Button(T.JAIL_BTN_WEEK.format(n=eng.press_weeks(st, content)), "week")])
    return Screen("\n".join(lines), rows, name="jail")


def lawyer_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    lines = [T.LAW_TITLE]
    block = eng.lawyer_blocked(st, content)
    rows: list[list[Button]] = []
    if block is None or block == eng.BLOCK_NO_MONEY:
        offer = eng.lawyer_offer(st, content)
        if offer is not None:
            lines.append(T.LAW_INTRO.format(name=texts.esc(svc.lawyer_contact_name(ch, content))))
            lines.append(T.LAW_OFFER.format(cut=offer[0], cost=views.money(offer[1])))
            if block is None:
                rows.append([Button(T.LAW_BUTTON.format(cost=views.money(offer[1])), "hire")])
    if block is not None and not (note and block == eng.BLOCK_LAWYER_DONE):
        lines.append(T.LAW_BLOCKED[block].format(n=content.balance.jail.lawyer.min_relation))
    if note:
        lines += ["", note]
    rows.append([Button(T.LAW_BACK, "back")])
    return Screen("\n".join(lines), rows, name="jailcontacts")


def render_escape(content: Content, ch: ActiveCharacter, res: LifeResult) -> Screen:
    lines = [views.check_line(res.check)] if res.check else []
    if res.ok:
        lines += [T.ESC_OK, T.ESC_RESULT_OK.format(heat=res.data["heat"])]
    else:
        lines += [T.ESC_FAIL.format(weeks=res.data["weeks"])]
    if res.applied:
        lines.append(views.effects_summary(res.applied, content))
    lines.extend(res.lines)
    rows = [[Button(T.JAIL_BTN_BACK if not res.ok else texts.MENU, "back" if not res.ok else "menu")]]
    return Screen("\n".join(x for x in lines if x), rows, name="jailout")


lc.register_renderer(svc.ESCAPE, render_escape)


async def _load(ctx: Ctx) -> ActiveCharacter | None:
    async with db.session_scope() as s:
        return await game.get_active_character(s, ctx.user_id)


def _blocked_note(res: LifeResult) -> str:
    if res.no_ap:
        return T.JAIL_NO_AP
    return T.ESC_ISOLATION if res.blocked == eng.BLOCK_ISOLATION else T.JAIL_FREE


@on_screen("jail")
async def jail_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if ev_service.has_pending(ch) and has_screen("evchoice"):
        return await goto(ctx, "evchoice", "resume")
    if not eng.in_jail(ch.state):
        return await menu_screen(ctx)
    if action == "profile":
        return await goto(ctx, "profile")
    if action == "train" and has_screen("activities"):
        return await goto(ctx, "activities")
    if action == "contacts" and has_screen("relations"):
        return await goto(ctx, "relations")
    if action == "week":
        return await goto(ctx, "week", "open")
    if action in ("work", "pastime"):
        return cell_screen(c, ch, action)
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    if action == "fight":
        from app.bot.handlers.combat import step_screen

        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            got = await svc.fight(s, c, ch, rng, year, tz)
            if isinstance(got, str):
                note = {"no_ap": T.JAIL_NO_AP, "weak": T.JAIL_FIGHT_BLOCKED, "free": T.JAIL_FREE}[got]
                return jail_screen(c, ch, note)
            return step_screen(c, ch, got)
    if action == "escape":
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            got = await svc.escape(s, c, ch, rng, year, tz)
            if not isinstance(got, LifeResult):
                return lc.check_screen(c, ch)
            if not got.done:
                return jail_screen(c, ch, _blocked_note(got))
        return lc.result_screen(c, ch, got)
    from app.bot.handlers.players import deliver_for_menu  # owner report 24.10: letters (money) reach the cell too

    mail = await deliver_for_menu(ctx, ch)
    return jail_screen(c, ch, "\n".join(texts.esc(x) for x in mail) if mail else None)


CELL_BLOCKED = {"free": "Ты уже на воле.", "ap": "ОД кончились. Нажми «Прожить месяц».", "none": "Такого здесь нет."}


def cell_screen(content: Content, ch: ActiveCharacter, kind: str, note: str | None = None) -> Screen:
    """Owner request 24.10: paid work in the cell (the debt keeps growing) and pastimes."""
    st = ch.state
    title = "Работа в камере" if kind == "work" else "Занятия"
    lines = [f"<b>{title}</b>", f"Эдди: {views.money(st.eddies)} · долг: {views.money(st.debt_total)} · ОД: {st.ap}"]
    if kind == "work":
        lines.append("<i>Платят мало, но платят: проценты по долгам на воле никто не отменял.</i>")
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    for key, job in content.balance.jail.jobs.items():
        if (job.kind == "job") != (kind == "work"):
            continue
        bits = [f"{job.ap} ОД"]
        if job.eddies:
            bits.append(f"{views.money(job.eddies)} эдди")
        if job.stat:
            bits.append("проверка")
        lines += ["", f"<b>{texts.esc(job.name)}</b> · {' · '.join(bits)}"]
        rows.append([Button(job.name, "do", f"{kind}:{key}")])
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="jailcell")


@on_screen("jailcell")
async def jailcell_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    if action == "back" or not arg or ":" not in arg:
        ch = await _load(ctx)
        return jail_screen(c, ch) if ch is not None and eng.in_jail(ch.state) else await menu_screen(ctx)
    kind, _, key = arg.partition(":")
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        r = await svc.cell_work(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, key)
    if r.blocked:
        if r.blocked == "free":
            return await menu_screen(ctx)
        return cell_screen(c, ch, kind, CELL_BLOCKED.get(r.blocked, r.blocked))
    note = texts.esc(r.text)
    if r.check is not None:
        note = views.check_line(r.check) + "\n" + note
    if r.applied is not None:
        extra = "\n".join(views.effects_lines(r.applied, c))
        if extra:
            note += "\n" + extra
    return cell_screen(c, ch, kind, note)


@on_screen("jailout")
async def jailout_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    return await menu_screen(ctx)  # the menu leads to the cell while the term runs, else to the menu


@on_screen("jailcontacts")
async def jailcontacts_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action == "back" and has_screen("relations"):
        return await goto(ctx, "relations")
    if action == "hire":
        rng = random.Random()
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, ctx.content)
            res = await svc.hire_lawyer(s, ctx.content, ch, rng, year, ctx.ui.settings.timezone)
        if res.blocked is None:
            note = T.LAW_DONE.format(name=texts.esc(res.name), cut=res.cut, left=res.left, cost=views.money(res.cost))
            return lawyer_screen(ctx.content, ch, note)
    return lawyer_screen(ctx.content, ch)
