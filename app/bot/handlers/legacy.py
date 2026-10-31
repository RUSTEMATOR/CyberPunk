"""Death and legacy in the window and the group (PLAN 7.2-7.4, DESIGN.md 13).

- `deathoffer`: the failed death save, a glitch may roll it again (once per life) or the player lets go;
- `legacy`: after a death, who continues the line (a child or relative, the protege, the engram, from scratch);
- `lineage`: Profile -> "Род"; `/lineage` and `/graveyard` in the group.
"""

from __future__ import annotations

import random

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot import texts, views
from app.bot.texts import SKILL_LABELS
from app.bot import texts_death as T
from app.bot.handlers.menu import menu_screen
from app.bot.handlers.week import death_screen
from app.bot.ui.callbacks import Ctx, on_screen
from app.bot.ui.kit import UiKit
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import death as death_engine
from app.engine import legacy as legacy_engine
from app.engine.state import FLAG_SECURE_SOUL
from app.services import death as death_service
from app.services import legends
from app.services import game
from app.services import legacy as legacy_service

router = Router(name="legacy")


def offer_screen(ctx: Ctx, ch: game.ActiveCharacter) -> Screen:
    rows = []
    if death_engine.glitch_offer_available(ch.state, ctx.content):
        rows.append([Button(T.BTN_OFFER_GLITCH, "glitch")])
    rows.append([Button(T.BTN_OFFER_END, "end")])
    return Screen(death_service.offer_text(ch), rows, name="deathoffer")


@on_screen("deathoffer")
async def deathoffer_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if death_service.pending_of(ch) is None:
            return await menu_screen(ctx)
        if action == "end":  # owner request 25.10: a serious button asks first
            return Screen("<b>Точно принять конец?</b>\nПерсонаж умрёт, это навсегда. Род продолжит наследник.",
                          [[Button("Да, это конец", "end_yes", style="danger")], [Button("Назад", "back")]],
                          name="deathoffer")
        if action not in ("glitch", "end_yes"):
            return offer_screen(ctx, ch)
        year = await game.current_year(s, ctx.content)
        out = await death_service.resolve_offer(s, ctx.content, ch, random.Random(), year, tz, action == "glitch")  # end_yes: accept
        name = ch.state.name
    if out.died:
        return death_screen(name, out.cause, out.lines)
    lines = [*out.lines, *out.after_lines]
    return Screen("\n".join(lines), [[Button("Дальше", "next")]], name="deathback")


@on_screen("deathback")
async def deathback_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    return await menu_screen(ctx)


# -- the legacy screen (PLAN 7.3) ---------------------------------------------------------------------------------------------


def _pct(x: float) -> int:
    return round(x * 100)


def legacy_screen(content: Content, view: legacy_service.OptionsView) -> Screen:
    cfg = content.balance.legacy
    inh = cfg.inherit
    lines = [
        T.LEGACY_TITLE.format(name=texts.esc(f"{view.dead.state.name} {view.dead.state.surname}".strip())),
        T.LEGACY_INTRO.format(family=texts.esc(view.family_name)),
        "",
    ]
    rows: list[list[Button]] = []
    if view.relatives:
        lines.append(T.LEG_RELATIVE.format(
            money=_pct(inh.money_pct), debt=_pct(inh.debt_pct), cred=_pct(inh.street_cred_pct), age=cfg.heir_min_age))
        rows.append([Button(T.BTN_LEG_RELATIVE, "rel")])
    else:
        lines.append(T.LEG_NO_RELATIVE)
    if view.protege is not None:
        npc = view.protege[1]
        skill = SKILL_LABELS.get(str(npc.data.get("skill") or ""), "")
        lines.append(T.LEG_PROTEGE.format(
            name=texts.esc(npc.name), cred=_pct(cfg.protege.street_cred_pct), skill=skill or "?",
            penalty=cfg.protege.skill_penalty))
        rows.append([Button(T.BTN_LEG_PROTEGE, "pro")])
    else:
        lines.append(T.LEG_NO_PROTEGE)
    if view.engram:
        eg = cfg.engram
        lines.append(T.LEG_ENGRAM.format(
            skills=_pct(eg.skills_pct), pen=eg.humanity_penalty, weeks=eg.conflict_event_every_weeks))
        rows.append([Button(T.BTN_LEG_ENGRAM, "eng")])
    else:
        lines.append(T.LEG_NO_ENGRAM)
    lines.append(T.LEG_SCRATCH.format(bonus=view.scratch_bonus))
    rows.append([Button(T.BTN_LEG_SCRATCH, "new")])
    return Screen("\n".join(lines), rows, name="legacy")


def pick_screen(content: Content, view: legacy_service.OptionsView) -> Screen:
    lines = [T.LEG_PICK_TITLE]
    rows: list[list[Button]] = []
    for c in view.relatives:
        lines.append(T.LEG_PICK_ROW.format(
            name=texts.esc(c.name), role=T.LEG_KIND_ROLE.get(c.role, T.LEG_KIND_ROLE["relative"]),
            age=views.years(c.age), rel=c.relation))
        rows.append([Button(T.LEG_PICK_BTN.format(name=c.name.split()[0], age=views.years(c.age)), "pick", str(c.index))])
    if not view.relatives:
        lines.append(T.LEG_PICK_EMPTY)
    rows.append([Button(T.BTN_BACK, "back")])
    return Screen("\n".join(lines), rows, name="legacy")


@on_screen("legacy")
async def legacy_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    """None = nothing is pending (the caller goes on to character creation)."""
    c = ctx.content
    async with db.session_scope() as s:
        view = await legacy_service.options(s, c, ctx.user_id)
        if view is None:
            return None
        plan = await legacy_service.load_plan(s, ctx.user_id)
        offer = await legacy_service.boon_offer(s, c, ctx.user_id) if plan is not None else []
        if action == "boon" and arg and plan is not None:
            if await legacy_service.choose_boon(s, c, ctx.user_id, arg):
                from app.bot.handlers.onboarding import preset_screen

                return await preset_screen(ctx)
            return boon_screen(c, offer)
        if action == "open" and plan is not None:
            if offer and not plan.boon:
                return boon_screen(c, offer)
            return None  # the way is chosen already: on to the starter sets ("change" reopens this screen)
        if action == "rel":
            return pick_screen(c, view)
        chosen = False
        if action == "pick" and arg and arg.isdigit():
            chosen = await legacy_service.choose(s, c, ctx.user_id, legacy_engine.OPT_RELATIVE, int(arg))
        elif action == "pro":
            chosen = await legacy_service.choose(s, c, ctx.user_id, legacy_engine.OPT_PROTEGE)
        elif action == "eng":
            chosen = await legacy_service.choose(s, c, ctx.user_id, legacy_engine.OPT_ENGRAM)
        elif action == "new":
            chosen = await legacy_service.choose(s, c, ctx.user_id, legacy_engine.OPT_SCRATCH)
    if chosen:
        async with db.session_scope() as s:
            offer = await legacy_service.boon_offer(s, c, ctx.user_id)
        if offer:
            return boon_screen(c, offer)
        from app.bot.handlers.onboarding import preset_screen

        return await preset_screen(ctx)
    return legacy_screen(c, view)


def boon_screen(content: Content, offer: list[str]) -> Screen:
    """Owner request 24.10: one gift of the dead life for the next one."""
    boons = content.balance.legacy.boons
    lines = ["🕯️ <b>Что осталось от прошлой жизни</b>",
             "Выбери одно. То, что выпало, зависит от того, чем жизнь запомнилась и как закончилась.", ""]
    rows: list[list[Button]] = []
    for k in offer:
        b = boons[k]
        bits = [f"{texts.STAT_LABELS.get(st, st)} +{n}" for st, n in b.stats.items()]
        fx = b.effects
        for key, label in (("eddies", "эдди"), ("street_cred", "репутация"), ("fame", "слава"), ("status", "статус"),
                           ("glitches", "глюки"), ("luck_pool", "удача"), ("humanity", "человечность"), ("mood", "настроение")):
            if fx.get(key):
                bits.append(f"{label} +{fx[key]}")
        if b.dead_eddies_pct:
            bits.append(f"{b.dead_eddies_pct}% денег предка")
        skills = [k2.split(":", 1)[1] for k2 in fx if k2.startswith("skill_xp:")]
        if skills:
            bits.append("опыт: " + ", ".join(SKILL_LABELS.get(x, x) for x in skills))
        if fx.get("debt"):
            bits.append("долги закрыты")
        if fx.get("scan_tolerance"):
            bits.append("знаешь свою устойчивость к хрому")
        lines += [f"<b>{texts.esc(b.name)}</b>" + (f" · {', '.join(bits)}" if bits else ""), f"<i>{texts.esc(b.text)}</i>", ""]
        rows.append([Button(b.name, "boon", k)])
    return Screen("\n".join(lines), rows, name="legacy")


def heir_block(content: Content, heir: dict, name: str) -> str:
    """The inheritance on the sheet of an heir (empty lines are left out)."""
    h = legacy_engine.HeirSummary.from_dict(heir)
    cfg = content.balance.legacy
    lines = [T.SHEET_HEIR.format(how=T.HOW.get(h.kind, T.HOW["scratch"]))]
    if h.fixed_name:
        lines.append(T.SHEET_HEIR_NAME.format(name=texts.esc(name)))
    if h.money:
        lines.append(T.SHEET_HEIR_MONEY.format(n=views.money(h.money)))
    if h.debt:
        lines.append(T.SHEET_HEIR_DEBT.format(n=views.money(h.debt)))
    if h.home:
        home = content.housing.get(h.home)
        lines.append(T.SHEET_HEIR_HOME.format(home=texts.esc(home.name if home else h.home)))
    if h.street_cred or h.status or h.fame:
        lines.append(T.SHEET_HEIR_CRED.format(street=h.street_cred, status=h.status, fame=h.fame))
    if h.skills:
        lines.append(T.SHEET_HEIR_SKILL.format(
            skills=", ".join(f"{SKILL_LABELS.get(k, k)} {v}" for k, v in h.skills.items())))
    if h.enemies:
        lines.append(T.SHEET_HEIR_ENEMIES.format(gangs=", ".join(texts.esc(content.gangs[x].name if x in content.gangs else x) for x in h.enemies)))
    if h.family:
        lines.append(T.SHEET_HEIR_FAMILY.format(n=h.family))
    if h.fixers:
        lines.append(T.SHEET_HEIR_FIXERS.format(names=", ".join(texts.esc(content.fixers[x].name if x in content.fixers else x) for x in h.fixers)))
    if h.engram_penalty:
        lines.append(T.SHEET_HEIR_ENGRAM.format(pen=h.engram_penalty, weeks=h.engram_weeks or cfg.engram.conflict_event_every_weeks))
    if h.glitch_bonus:
        lines.append(T.SHEET_HEIR_GLITCH.format(bonus=h.glitch_bonus))
    boon = content.balance.legacy.boons.get(str(heir.get("boon") or ""))
    if boon is not None:
        lines.append(f"🕯️ Дар прошлой жизни: <b>{texts.esc(boon.name)}</b>")
    return "\n".join(lines)


# -- Secure Your Soul (the property menu) ----------------------------------------------------------------------------


def engram_screen(content: Content, ch: game.ActiveCharacter, year: int, note: str | None = None) -> Screen:
    eg = content.balance.legacy.engram
    lines = [T.ENGRAM_TITLE]
    rows: list[list[Button]] = []
    if note:
        lines += [note, ""]
    if not legacy_service.engram_offered(content, year):
        lines.append(T.ENGRAM_UNAVAILABLE.format(year=eg.available_from_year))
    else:
        lines.append(T.ENGRAM_TEXT.format(
            skills=_pct(eg.skills_pct), pen=eg.humanity_penalty, weeks=eg.conflict_event_every_weeks,
            price=views.money(eg.price)))
        if ch.state.flags.get(FLAG_SECURE_SOUL):
            if note != T.ENGRAM_DONE:
                lines.append(T.ENGRAM_HAVE)
        else:
            rows.append([Button(T.ENGRAM_BUY.format(price=views.money(eg.price)), "buy")])
    rows.append([Button(T.BTN_BACK, "back")])
    return Screen("\n".join(lines), rows, name="engram")


@on_screen("engram")
async def engram_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.bot.handlers.property import property_screen

    c = ctx.content
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "back":
            return property_screen(c, ch)
        if action == "buy":
            res = await legacy_service.buy_engram(s, c, ch, random.Random(), year, tz)
            note = {
                None: T.ENGRAM_DONE,
                "have": T.ENGRAM_HAVE,
                "unavailable": T.ENGRAM_UNAVAILABLE.format(year=c.balance.legacy.engram.available_from_year),
                "jail": T.ENGRAM_JAIL,
                "eddies": T.ENGRAM_NO_EDDIES.format(price=views.money(c.balance.legacy.engram.price)),
            }.get(res.reason)
            return engram_screen(c, ch, year, note)
    return engram_screen(c, ch, year)


# -- /lineage, /graveyard, Profile -> "Род" -------------------------------------------------------------------------------


def lineage_text(content: Content, view: legacy_service.LineageView | None) -> str:
    if view is None or not view.lives:
        return T.LINEAGE_EMPTY
    lines = [T.LINEAGE_TITLE.format(family=texts.esc(view.family)), T.LINEAGE_COUNT.format(n=view.lives_ended)]
    if view.glitch_bonus:
        lines.append(T.LINEAGE_GLITCH.format(n=view.glitch_bonus))
    cap = content.balance.legacy.lineage_list_max
    skipped = max(0, len(view.lives) - cap)
    if skipped:
        lines.append(T.LINEAGE_MORE.format(n=skipped))
    for n, life in enumerate(view.lives, start=1):
        if n <= skipped:
            continue
        common = dict(n=n, name=texts.esc(life.name), role=life.role, age=views.years(life.age), how=life.how[:1].upper() + life.how[1:])
        if life.alive:
            lines.append(T.LINEAGE_ROW_ALIVE.format(**common))
        else:
            lines.append(T.LINEAGE_ROW_DEAD.format(
                **common, cause=texts.DEATH_CAUSE_NAMES.get(life.cause, life.cause), cred=life.street_cred))
    return "\n".join(lines)


@on_screen("lineage")
async def lineage_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.bot.handlers.menu import profile_handler

    if action == "back":
        return await profile_handler(ctx, "open", None)
    async with db.session_scope() as s:
        view = await legacy_service.lineage_view(s, ctx.content, ctx.user_id)
    return Screen(lineage_text(ctx.content, view), [[Button(T.BTN_BACK, "back")]], name="lineage")


@router.message(Command("lineage"))
async def cmd_lineage(message: Message, ui: UiKit) -> None:
    """The caller's family line: every life, how it began and how it ended. A temporary group message."""
    if message.from_user is None:
        return
    async with db.session_scope() as s:
        view = await legacy_service.lineage_view(s, ui.content, message.from_user.id)
    ui.reply_temp(message.chat.id, lineage_text(ui.content, view))


@router.message(Command("graveyard"))
async def cmd_graveyard(message: Message, ui: UiKit) -> None:
    """The latest dead, newest first, with the main achievement and the hunter of a MaxTac duty call. Temporary."""
    content = ui.content
    lines = [T.GRAVE_TITLE]
    async with db.session_scope() as s:
        rows = await legends.graveyard(s, content.balance.reminders.graveyard_n)
    for g in rows:
        role = content.roles.get(g.role)
        tpl = T.GRAVE_ROW_BY if g.killed_by else T.GRAVE_ROW
        lines.append(tpl.format(
            name=texts.esc(g.name), role=role.name if role else T.NO_ROLE, age=views.years(int(g.died_age)),
            cause=texts.DEATH_CAUSE_NAMES.get(g.cause, g.cause), cred=g.street_cred, hunter=texts.esc(g.killed_by or ""),
            achv=T.GRAVE_ACHV.format(achv=texts.esc(g.epitaph)) if g.epitaph else "",
        ).strip())
    if not rows:
        lines.append(T.GRAVE_EMPTY)
    ui.reply_temp(message.chat.id, "\n".join(lines))
