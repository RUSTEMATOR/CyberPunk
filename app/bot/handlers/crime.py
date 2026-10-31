"""The crime section in the window (PLAN 9.4).

crime -> [one of four actions -> lifechk (luck / glitch / accept)] -> crimeout -> back to crime.
The raid and the MaxTac hunt come at "Прожить месяц" (engine/week.py) and lead to the exits screen.
"""

from __future__ import annotations

import random

from app.bot import texts, texts_actions, views
from app.bot import texts_life as T
from app.bot.handlers import lifecheck as lc
from app.bot.handlers.menu import menu_screen
from app.bot.ui.callbacks import Ctx, goto, has_screen, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import crime as eng
from app.services import crime as svc
from app.services import events as ev_service
from app.services import game
from app.services.game import ActiveCharacter
from app.services.lifecheck import LifeResult

KINDS = ("pickpocket", "car_theft", "store_robbery", "assault")


def _check_text(content: Content, kind: str) -> str:
    ck = eng.action_cfg(content, kind).check
    return T.STAT_SHORT[ck.stat] + (" + " + texts.SKILL_LABELS.get(ck.skill, ck.skill).lower() if ck.skill else "")


def crime_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    c = content.balance.crime
    lines = [T.CRIME_TITLE, T.CRIME_STATUS.format(
        eddies=views.money(st.eddies), ap=st.ap, heat=st.heat, max=content.balance.character.heat_max)]
    lines.append(T.CRIME_INTRO.format(decay=c.heat_decay_per_week))
    chance = eng.raid_chance(content, st.heat)
    if chance > 0 and not eng.blocked(st, content):
        lines.append(T.CRIME_RAID_HUNT if st.heat >= c.maxtac_heat else T.CRIME_RAID_RISK.format(pct=round(chance * 100)))
    if eng.blocked(st, content) == eng.BLOCK_JAIL:
        lines.append(T.CRIME_JAIL)
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    for kind in KINDS:
        cfg = eng.action_cfg(content, kind)
        lines += ["", T.CRIME_HEAD.format(name=T.CRIME_NAMES[kind], ap=c.ap), T.CRIME_DESC[kind], T.CRIME_LINE.format(
            check=_check_text(content, kind), dv=cfg.check.dv, lo=cfg.reward_min, hi=cfg.reward_max,
            heat_ok=cfg.heat_success, heat_fail=cfg.heat_fail, pct=round(cfg.arrest_chance_on_fail * 100),
            wlo=cfg.jail_weeks_min, whi=cfg.jail_weeks_max)]
        rows.append([Button(T.CRIME_NAMES[kind], "do", kind)])
    if has_screen("acts"):
        rows.append([Button(texts_actions.BTN_MORE_CRIME, "more"), Button(texts_actions.BTN_GANGS, "gangs")])
    rows.append([Button("Барыга", "dealer"), Button("Чёрный рынок", "blackmarket")])  # owner request
    rows.append([Button("Влияние", "influence"), Button("Корпорации", "corps")])
    rows.append([Button("Свои игры богачей", "dghost"), Button("Мои бойцы", "army")])  # owner request 24.10, 25.10
    rows.append([Button("Враги", "foes", style="danger")])  # owner request 25.10: ways to get rid of enemies
    rows.append([Button("Налёт на корпорацию", "craid", style="danger")])  # owner request 25.10: the screen says who may
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="crime")


def render_crime(content: Content, ch: ActiveCharacter, res: LifeResult) -> Screen:
    kind = str(res.data["kind"])
    lines = [views.check_line(res.check)] if res.check else []
    if res.ok:
        lines.append(random.choice(T.CRIME_OK[kind]))
    else:
        lines.append(random.choice(T.CRIME_FAIL[kind]))
        if res.data.get("crit") == "fail":
            lines.append(T.CRIME_CRIT)
        if res.data.get("arrested"):
            lines.append(T.CRIME_ARRESTED)
    if res.applied:
        lines.append(views.effects_summary(res.applied, content))
    lines.extend(res.lines)
    rows = [[Button(T.CRIME_BACK, "back"), Button(texts.MENU, "menu")]]
    if res.data.get("arrested") and has_screen("jail"):
        rows = [[Button(texts.MENU, "menu")]]
    return Screen("\n".join(x for x in lines if x), rows, name="crimeout")


lc.register_renderer(svc.CRIME, render_crime, lambda content, args: T.CHK_HEAD["crime"].format(kind=T.CRIME_NAMES[args["kind"]].lower()))


def _blocked_note(res: LifeResult) -> str:
    return T.CRIME_NO_AP if res.no_ap else T.CRIME_BLOCKED.get(res.blocked or "", res.blocked or "")


@on_screen("crime")
async def crime_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await menu_screen(ctx)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if ev_service.has_pending(ch) and has_screen("evchoice"):
        return await goto(ctx, "evchoice", "resume")
    if action == "more" and has_screen("acts"):
        return await goto(ctx, "acts", "open", "crime")
    if action == "foes":
        return await goto(ctx, "foes", "open")
    if action == "army":  # owner request 25.10: the own fighters
        return await goto(ctx, "army", "open")
    if action == "craid":
        return await goto(ctx, "craid", "open")
    if action == "gangs" and has_screen("gangs"):
        return await goto(ctx, "gangs", "open")
    if action == "dghost":
        from app.bot.handlers.dgame import host_screen

        return await host_screen(ctx)
    if action in ("influence", "corps"):
        from app.bot.handlers import bigmoney

        if action == "corps":
            return await bigmoney.corps_screen(ctx)
        async with db.session_scope() as s:
            year = await game.current_year(s, c)
        return bigmoney.influence_screen(c, ch, year)
    if action in ("dealer", "blackmarket"):
        from app.bot.handlers import economy

        async with db.session_scope() as s:
            year = await game.current_year(s, c)
        return economy.dealer_screen(c, ch, year) if action == "dealer" else economy.blackmarket_screen(c, ch, year)
    if action != "do" or arg not in KINDS:
        return crime_screen(c, ch)
    rng = random.Random()
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        got = await svc.commit(s, c, ch, rng, year, ctx.ui.settings.timezone, arg)
        if not isinstance(got, LifeResult):
            return lc.check_screen(c, ch)
    if not got.done:
        return crime_screen(c, ch, _blocked_note(got))
    return lc.result_screen(c, ch, got)


@on_screen("crimeout")
async def crimeout_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
    if ch is None:
        return await menu_screen(ctx)
    return crime_screen(ctx.content, ch)
