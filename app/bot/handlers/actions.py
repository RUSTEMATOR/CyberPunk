"""Small deeds, gangs and your own business in the window (owner request).

acts (one section: leisure | crime | gang) -> actsout -> back to the list.
gangs (join / tasks / leave) -> gangleave (confirm). biz (buy / upgrade / ads / guard / sell) -> bizsell (confirm).
Entry points: «Досуг» on the activities screen, «Ещё дела» and «Банды» on the crime screen, «Свой бизнес» on property.
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot import texts_actions as T
from app.bot import texts_life as TL
from app.bot.handlers.menu import menu_screen
from app.bot.handlers.week import death_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, has_screen, on_screen, on_text
from app.bot.ui.screen import Button, Screen, pack
from app.content.loader import Content
from app.content.schemas import Action
from app.db import session as db
from app.services import actions as svc
from app.services import business as biz
from app.services import events as ev_service
from app.services import game
from app.services import gangs
from app.services.game import ActiveCharacter

SECTIONS = ("leisure", "crime", "gang")
SECTION_ICON = {"leisure": "activities", "crime": "crime", "gang": "crime"}


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


def _with_note(lines: list[str], note: str | None) -> list[str]:
    return lines + ["", note] if note else lines


# -- deed lists --------------------------------------------------------------------------------------------


def _item(content: Content, a: Action) -> str:
    check = ""
    if a.check is not None:
        skill = " + " + texts.SKILL_LABELS.get(a.check.skill, a.check.skill).lower() if a.check.skill else ""
        check = T.ITEM_CHECK.format(stat=TL.STAT_SHORT.get(a.check.stat, a.check.stat), skill=skill, dv=a.check.dv)
    return T.ITEM.format(
        name=texts.esc(a.name), ap=a.ap, cost=T.ITEM_COST.format(cost=views.money(a.cost)) if a.cost else "",
        check=check, daily=T.ITEM_DAILY if a.daily else "", desc=texts.esc(a.description),
    )


PAGE = 8  # deeds per page: a long section with descriptions does not fit one Telegram message


def list_screen(content: Content, ch: ActiveCharacter, year: int, section: str, note: str | None = None,
                page: int = 0, times: int = 1) -> Screen:
    from app.bot.handlers.activities import repeat_note, repeat_row

    st = ch.state
    acts = svc.available(content, ch, section, year)
    pages = max(1, (len(acts) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    status = T.STATUS.format(eddies=views.money(st.eddies), ap=st.ap)
    if pages > 1:
        status += f" · Стр. {page + 1}/{pages}"
    lines = [style.header(SECTION_ICON[section], T.SECTION_TITLE[section]), T.SECTION_INTRO[section], status, ""]
    if not acts:
        lines.append(T.EMPTY)
    shown = acts[page * PAGE:(page + 1) * PAGE]
    lines += [_item(content, a) for a in shown]
    if times != 1:
        lines += ["", repeat_note(times)]
    buttons = [Button(a.button, "do", a.key) for a in shown]
    rows = [repeat_row(times)] + pack(buttons, content.balance.ui.button_chars, max_per_row=2)
    if pages > 1:
        nav: list[Button] = []
        if page > 0:
            nav.append(Button(f"«« Стр. {page}", "pg", str(page - 1)))
        if page < pages - 1:
            nav.append(Button(f"Стр. {page + 2} »»", "pg", str(page + 1)))
        rows.append(nav)
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_with_note(lines, note)), rows, name="acts",
                  state={"section": section, "page": page, "n": times})


def _back_target(section: str) -> str:
    return "activities" if section == "leisure" else "gangs" if section == "gang" else "crime"


def result_screen(content: Content, ch: ActiveCharacter, res: svc.ActionResult, page: int = 0,
                  many: tuple[int, int, int] | None = None, times: int = 1) -> Screen:
    """`many` = (done, asked, checks passed) for several deeds at once; res.applied is then their sum."""
    if res.died:
        return death_screen(ch.state.name, res.death_cause, [texts.esc(res.text)] if res.text else [])
    a = res.action
    lines = [style.header(SECTION_ICON[a.section], texts.esc(a.name))]
    if many is not None:
        lines.append(texts.ACT_MANY_DONE.format(done=many[0], asked=many[1])
                     + (f" Проверок пройдено: {many[2]}." if res.check is not None else ""))
    if res.check is not None and many is None:
        lines.append(views.check_line(res.check))
    if res.text:
        lines.append(style.quote(texts.esc(res.text)))
    if res.applied is not None:
        summary = views.effects_summary(res.applied, content)
        if summary:
            lines.append(summary)
    lines.extend(res.lines)
    rows = [[Button(T.BACK_TO_LIST, "list"), Button(texts.MENU, "menu")]]
    if res.queued_event is not None and has_screen("evchoice"):
        rows = [[Button(texts.EVENT_CONTINUE, "event")]]
    elif has_screen("jail") and ch.state.flags.get("jail_weeks"):
        rows = [[Button(texts.MENU, "menu")]]
    return Screen("\n".join(x for x in lines if x), rows, name="actsout",
                  state={"section": a.section, "page": page, "n": times})


async def open_section(ctx: Ctx, section: str, note: str | None = None, page: int = 0) -> Screen:
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    return list_screen(ctx.content, ch, year, section, note, page)


@on_screen("acts")
async def acts_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    section = str(ctx.state.get("section") or "leisure")
    page = int(ctx.state.get("page") or 0)
    if action == "open" and arg in SECTIONS:
        section, page = arg, 0
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        target = _back_target(section)
        return await (goto(ctx, target) if target != "gangs" else gangs_open(ctx))
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if ev_service.has_pending(ch) and has_screen("evchoice"):
        return await goto(ctx, "evchoice", "resume")
    n_raw = ctx.state.get("n", 1)
    times = int(n_raw) if str(n_raw).isdigit() else 1
    if action == "n" and arg is not None and arg.isdigit():
        return list_screen(ctx.content, ch, year, section, None, page, int(arg))
    if action == "pg":
        return list_screen(ctx.content, ch, year, section, None, int(arg) if arg and arg.isdigit() else 0, times)
    if action != "do" or not arg:
        return list_screen(ctx.content, ch, year, section, None, page, times)
    rng = random.Random()
    done: list[svc.ActionResult] = []
    first: svc.ActionResult | None = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        act_def = ctx.content.actions.get(arg)
        want = times if times > 0 else max(1, ch.state.ap // max(1, act_def.ap if act_def else 1))
        for _ in range(want):  # owner request: put N AP into one deed; stops when it cannot go on
            r = await svc.perform(s, ctx.content, ch, rng, year, ctx.ui.settings.timezone, arg)
            if first is None:
                first = r
            if r is None or r.blocked is not None or r.no_ap:
                break
            done.append(r)
            if r.died or r.queued_event is not None:
                break
    res = done[-1] if done else first
    if res is None:
        return list_screen(ctx.content, ch, year, section, None, page, times)
    if not done:
        if res.blocked is not None:
            return list_screen(ctx.content, ch, year, section, T.BLOCKED.get(res.blocked, res.blocked), page, times)
        return list_screen(ctx.content, ch, year, section, T.NO_AP, page, times)
    if want == 1:
        return result_screen(ctx.content, ch, res, page, times=times)
    from app.engine.effects import merge_applied

    res.applied = merge_applied([r.applied for r in done if r.applied is not None])
    res.lines = [ln for r in done for ln in r.lines]
    passed = sum(1 for r in done if r.check is not None and r.check.ok)
    return result_screen(ctx.content, ch, res, page, (len(done), want, passed), times)


@on_screen("actsout")
async def actsout_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "event" and has_screen("evchoice"):
        return await goto(ctx, "evchoice", "resume")
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
    if ch is None:
        return await menu_screen(ctx)
    if ev_service.has_pending(ch) and has_screen("evchoice"):
        return await goto(ctx, "evchoice", "resume")
    n_raw = ctx.state.get("n", 1)
    ch2, year2 = await _load(ctx)
    if ch2 is None:
        return await menu_screen(ctx)
    return list_screen(ctx.content, ch2, year2, str(ctx.state.get("section") or "leisure"), None,
                       int(ctx.state.get("page") or 0), int(n_raw) if str(n_raw).isdigit() else 1)


# -- gangs -------------------------------------------------------------------------------------------------


def gangs_screen(content: Content, ch: ActiveCharacter, year: int, note: str | None = None) -> Screen:
    st = ch.state
    lines = [style.header("crime", T.GANG_TITLE)]
    rows: list[list[Button]] = []
    key = gangs.gang_of(ch)
    if key is not None:
        g = content.gangs.get(key)
        name = texts.esc(g.name) if g else key
        idx, rank = gangs.rank_of(content, ch)  # type: ignore[misc]
        ranks = content.balance.players.gangs.ranks
        rep = int((st.faction_rep or {}).get(key, 0))
        top = ranks[-1].rep or 1
        lines.append(T.GANG_MEMBER.format(name=name, rank=rank.name, rep=rep, bar=style.bar(max(0, rep), top)))
        if idx + 1 < len(ranks):
            nxt = ranks[idx + 1]
            lines.append(T.GANG_NEXT.format(rank=nxt.name, need=nxt.rep - rep, cut=views.money(rank.cut)))
        else:
            lines.append(T.GANG_TOP.format(cut=views.money(rank.cut)))
        rows.append([Button(T.GANG_TASKS_BTN, "tasks"), Button("Налёт на чужих", "raid")])
        rows.append([Button(T.GANG_LEAVE_BTN, "leave")])
    else:
        c = content.balance.players.gangs.initiation
        lines.append(T.GANG_NONE.format(stat=TL.STAT_SHORT.get(c.stat, c.stat), dv=c.dv))
        lines.append("")
        for k in gangs.joinable(content, ch, year):
            g = content.gangs[k]
            lines.append(T.GANG_ITEM.format(name=texts.esc(g.name), rep=int((st.faction_rep or {}).get(k, 0)),
                                            desc=texts.esc(g.description)))
            rows.append([Button(T.GANG_JOIN_BTN.format(name=g.name), "join", k)])
    if key is None:  # owner request 25.10: a solo raid works without a gang too
        rows.append([Button("Налёт в одиночку", "raid")])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_with_note(lines, note)), rows, name="gangs")


RAID_BLOCK = {"gone": "Этой банды здесь нет.", "jail": "Из камеры не напасть.", "busy": "Сначала разберись с тем, что уже ждёт.",
              "today": "На эту банду сегодня уже ходили. Завтра.", "ap": f"Нужно {gangs.RAID_AP} ОД."}


def raid_screen(content: Content, ch: ActiveCharacter, year: int, note: str | None = None) -> Screen:
    """Owner request 25.10: a solo raid on a rival gang, a real fight."""
    mine = gangs.gang_of(ch)
    danger = gangs.raid_danger(content, ch)
    lines = [style.header("crime", "Налёт на банду"),
             f"Один против точки чужой банды: настоящий бой, опасность {danger} (растёт с уровнем). {gangs.RAID_AP} ОД, "
             "на каждую банду раз в сутки. Победа: касса точки, уличная репутация"
             + (", уважение своей банды" if mine else "") + ". Банда запомнит: враги +1, розыск +1, иногда заклятый враг.", ""]
    targets = gangs.raid_targets(content, ch, year)
    d = content.districts.get(ch.state.district)
    local = set(d.gangs if d else [])
    rows: list[list[Button]] = []
    for k in targets:
        g = content.gangs[k]
        rep = int((ch.state.faction_rep or {}).get(k, 0))
        lines.append(f"• {texts.esc(g.name)}{' · твой район' if k in local else ''} · отношение {rep}")
    rows += pack([Button(content.gangs[k].name[:24], "go", k) for k in targets], content.balance.ui.button_chars, max_per_row=2)
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="graid")


@on_screen("graid")
async def graid_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await gangs_open(ctx)
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "go" and arg:
            r = await gangs.solo_raid(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, arg)
            if isinstance(r, str):
                return raid_screen(c, ch, year, RAID_BLOCK.get(r, r))
            from app.bot.handlers.combat import step_screen

            return step_screen(c, ch, r)  # type: ignore[arg-type]
    return raid_screen(c, ch, year)


async def gangs_open(ctx: Ctx, note: str | None = None) -> Screen:
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    return gangs_screen(ctx.content, ch, year, note)


@on_screen("gangs")
async def gangs_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "crime")
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if action == "tasks":
        return list_screen(c, ch, year, "gang")
    if action == "raid":
        return raid_screen(c, ch, year)
    if action == "leave" and gangs.gang_of(ch):
        g = c.gangs.get(gangs.gang_of(ch) or "")
        return Screen(
            T.GANG_LEAVE_CONFIRM.format(name=texts.esc(g.name) if g else "", rep=-c.balance.players.gangs.leave_rep),
            [[Button(T.GANG_LEAVE_YES, "yes"), Button(texts.CANCEL, "no")]], name="gangleave",
        )
    if action == "join" and arg:
        rng = random.Random()
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            res = await gangs.join(s, c, ch, rng, year, ctx.ui.settings.timezone, arg)
        if res.blocked:
            return gangs_screen(c, ch, year, T.GANG_BLOCKED.get(res.blocked, res.blocked))
        g = c.gangs.get(arg)
        lines = [views.check_line(res.check)] if res.check else []
        lines.append(T.GANG_JOINED.format(name=texts.esc(g.name) if g else arg) if res.ok else T.GANG_JOIN_FAIL)
        if res.applied:
            lines.append(views.effects_summary(res.applied, c))
        return gangs_screen(c, ch, year, "\n".join(x for x in lines if x))
    return gangs_screen(c, ch, year)


@on_screen("gangleave")
async def gangleave_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action != "yes":
        return await gangs_open(ctx)
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        key = gangs.gang_of(ch) or ""
        applied = await gangs.leave(s, c, ch, random.Random(), year, ctx.ui.settings.timezone)
    g = c.gangs.get(key)
    note = T.GANG_LEFT.format(name=texts.esc(g.name) if g else key)
    if applied:
        note += "\n" + views.effects_summary(applied, c)
    return gangs_screen(c, ch, year, note)


# -- business ----------------------------------------------------------------------------------------------


def biz_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    """All own businesses (owner request: several, each with a name) and the way to a new one."""
    cfg = content.balance.players.business
    st = ch.state
    lines = [style.header("property", T.BIZ_TITLE), T.STATUS.format(eddies=views.money(st.eddies), ap=st.ap), ""]
    rows: list[list[Button]] = []
    items = biz.all_of(ch)
    if items:
        lines.append(T.BIZ_OWNED.format(n=len(items), max=cfg.max_owned))
        for i, b in enumerate(items, 1):
            kind = biz.kind_of(content, b["kind"])
            if kind is None:
                continue
            lvl = int(b["level"])
            lines.append(T.BIZ_LINE.format(n=i, name=texts.esc(biz.title(content, b)), level=lvl,
                                           income=views.money(kind.income * lvl), upkeep=views.money(biz.upkeep_of(content, ch, kind, lvl))))
            rows.append([Button(f"{i}. {b.get('name') or kind.name}"[:24], "one", str(b["id"]))])
        lines.append("")
        plan = biz.manage_all_plan(content, ch)  # owner report 24.10: one button for the whole chain
        if plan:
            cost = sum(x[2] for x in plan)
            lines.append(f"Требуют внимания: {len({x[0] for x in plan})} из {len(items)}. Закупка, премии и ремонт "
                         f"всего, что ниже {biz.TOP_UP_BELOW}: {views.money(cost)} эдди.")
            rows.insert(0, [Button(f"Обслужить все · {views.money(cost)}"[:24], "mgall")])
    else:
        lines.append(T.BIZ_NONE)
    if len(items) < cfg.max_owned:
        rows.append([Button(T.BIZ_NEW_BTN, "shop")])
    else:
        lines.append(T.BIZ_FULL.format(max=cfg.max_owned))
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_with_note(lines, note)), rows, name="biz")


def biz_shop_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    cfg = content.balance.players.business
    lines = [style.header("property", T.BIZ_SHOP_TITLE), T.STATUS.format(eddies=views.money(ch.state.eddies), ap=ch.state.ap), ""]
    rows: list[list[Button]] = []
    for k in cfg.kinds:
        lines.append(T.BIZ_KIND.format(name=texts.esc(k.name), price=views.money(k.price), desc=texts.esc(k.description),
                                       income=views.money(k.income), upkeep=views.money(k.upkeep), risk=round(k.risk * 100)))
        rows.append([Button(T.BIZ_BUY_BTN.format(name=k.name), "buy", k.key)])
    rows.append([Button(texts.BACK, "list"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_with_note(lines, note)), rows, name="biz")


def biz_one_screen(content: Content, ch: ActiveCharacter, biz_id: int, note: str | None = None,
                   naming: bool = False) -> Screen:
    cfg = content.balance.players.business
    st = ch.state
    b = biz.state_of(ch, biz_id)
    kind = biz.kind_of(content, b["kind"]) if b else None
    if b is None or kind is None:
        return biz_screen(content, ch, note)
    lvl = int(b["level"])
    risk = kind.risk * (cfg.guard_risk_mult if int(b.get("guard_until", 0)) > st.weeks_lived else 1.0)
    lines = [style.header("property", texts.esc(biz.title(content, b))),
             T.STATUS.format(eddies=views.money(st.eddies), ap=st.ap), ""]
    lines.append(T.BIZ_HAVE.format(name=texts.esc(kind.name), level=lvl, max=cfg.max_level, desc=texts.esc(kind.description),
                                   income=views.money(kind.income * lvl), upkeep=views.money(biz.upkeep_of(content, ch, kind, lvl)),
                                   risk=round(risk * 100)))
    ads = int(b.get("ads_until", 0)) - st.weeks_lived
    guard = int(b.get("guard_until", 0)) - st.weeks_lived
    if ads > 0:
        lines.append(T.BIZ_ADS_ON.format(weeks=ads))
    if guard > 0:
        lines.append(T.BIZ_GUARD_ON.format(weeks=guard))
    invested = int(b.get("invested", 0))
    lines.append(T.BIZ_INVESTED.format(sum=views.money(invested), back=views.money(invested * cfg.sell_pct // 100)))
    if int(b.get("boost_until", -1)) >= st.weeks_lived:
        lines.append(f"Полоса прибыли ещё {int(b['boost_until']) - st.weeks_lived + 1} нед.")
    if int(b.get("slump_until", -1)) >= st.weeks_lived:
        lines.append(f"Спад ещё {int(b['slump_until']) - st.weeks_lived + 1} нед.")
    if int(b.get("fame", 0)):
        lines.append(f"Слава заведения: {int(b['fame'])}/{cfg.fame_max} (доход +{int(b['fame']) * cfg.fame_pct}%).")
    sch = biz.scheme_of(content, b)
    if sch is not None:
        lines.append(f"Схема: <b>{texts.esc(sch.name)}</b> (+{views.money(sch.income * lvl)}/нед, риск рейда)")
    from app.engine import biz as biz_engine  # owner request 24.10: the business must be run

    o = biz_engine.ops(b, cfg.ops_start)
    lines += ["", "<b>Хозяйство</b> <i>(всё падает каждую неделю; низкое режет доход и приносит неприятности)</i>"]
    for k, label, per in (("stock", "📦 Запасы", cfg.stock_use), ("staff", "👥 Персонал", cfg.staff_decay),
                          ("cond", "🔧 Состояние", cfg.wear)):
        warn = " ⚠️" if o[k] < cfg.low_mark else ""
        lines.append(f"{label} {style.bar(o[k], 100)} {o[k]}/100 (−{per}/нед){warn}")
    if o["stock"] <= 0:
        lines.append("⚠️ Склад пуст: доход падает втрое.")
    eff = (cfg.stock_out_mult if o["stock"] <= 0 else 1.0) * (0.6 + 0.4 * o["staff"] / 100) * (0.6 + 0.4 * o["cond"] / 100)
    if b.get("managed") is not None and st.weeks_lived - int(b["managed"]) > cfg.neglect_weeks:
        eff *= 1 - cfg.neglect_pct / 100
    est = int(kind.income * lvl * eff) - biz.upkeep_of(content, ch, kind, lvl)
    lines.append(f"Сейчас приносит примерно {'+' if est >= 0 else '−'}{views.money(abs(est))}/нед "
                 f"(хозяйство даёт {round(eff * 100)}% дохода).")
    if b.get("managed") is not None:
        idle = st.weeks_lived - int(b["managed"])
        if idle > cfg.neglect_weeks:
            lines.append(f"⚠️ Хозяина не видно {idle} нед.: управляющий подворовывает (−{cfg.neglect_pct}% дохода).")
    rows: list[list[Button]] = []
    if naming:
        lines += ["", T.BIZ_NAME_ASK]
        rows.append([Button(T.BIZ_NAME_KEEP.format(name=kind.name)[:24], "nokeep")])
        rows.append([Button(texts.BACK, "list"), Button(texts.MENU, "menu")])
        return Screen("\n".join(_with_note(lines, note)), rows, name="biz", state={"id": biz_id},
                      awaiting_text="biz_name")
    if lvl < cfg.max_level:
        rows.append([Button(T.BIZ_UPGRADE_BTN.format(cost=views.money(biz.upgrade_cost(content, kind, lvl))), "up")])
    rows.append([Button(T.BIZ_ADS_BTN.format(cost=views.money(cfg.ads_cost)), "ads"),
                 Button(T.BIZ_GUARD_BTN.format(cost=views.money(cfg.guard_cost)), "guard")])
    rows.append([Button(f"Закупка · {views.money(biz.manage_cost(content, b, 'stock'))}"[:24], "mg", "stock"),
                 Button(f"Премия · {views.money(biz.manage_cost(content, b, 'staff'))}"[:24], "mg", "staff")])
    rows.append([Button(f"Ремонт · {views.money(biz.manage_cost(content, b, 'cond'))}"[:24], "mg", "cond")])
    rows.append([Button("Зал: посетители", "hall"), Button("Схемы", "schemes")])
    rows.append([Button(T.BIZ_RENAME_BTN, "rename"), Button(T.BIZ_SELL_BTN, "sell")])
    rows.append([Button(texts.BACK, "list"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_with_note(lines, note)), rows, name="biz", state={"id": biz_id})


TERMS = (1, 3, 6, 13)  # periods of ads or guard bought at once (13 x 4 weeks = a year)


def biz_term_screen(content: Content, ch: ActiveCharacter, biz_id: int, what: str) -> Screen:
    """How long the ads or the guard should run (owner request: not only one press)."""
    cfg = content.balance.players.business
    b = biz.state_of(ch, biz_id)
    if b is None:
        return biz_screen(content, ch)
    cost, weeks = (cfg.ads_cost, cfg.ads_weeks) if what == "ads" else (cfg.guard_cost, cfg.guard_weeks)
    left = int(b.get(f"{what}_until", 0)) - ch.state.weeks_lived
    head = "Реклама" if what == "ads" else "Охрана"
    lines = [style.header("property", f"{head}: {texts.esc(biz.title(content, b))}"),
             T.STATUS.format(eddies=views.money(ch.state.eddies), ap=ch.state.ap), "",
             f"{weeks} нед. стоят {views.money(cost)}. Новые недели добавляются к уже оплаченным."]
    if left > 0:
        lines.append(f"Сейчас оплачено ещё {left} нед.")
    rows = [[Button(f"{n * weeks} нед. · {views.money(n * cost)}"[:24], what, str(n)) for n in TERMS[:2]],
            [Button(f"{n * weeks} нед. · {views.money(n * cost)}"[:24], what, str(n)) for n in TERMS[2:]],
            [Button(texts.BACK, "one", str(biz_id)), Button(texts.MENU, "menu")]]
    return Screen("\n".join(lines), rows, name="biz", state={"id": biz_id})


def biz_hall_screen(content: Content, ch: ActiveCharacter, biz_id: int, note: str | None = None,
                    pick: int | None = None) -> Screen:
    """Today's visitors (owner request): one deed with each per real day."""
    cfg = content.balance.players.business
    b = biz.state_of(ch, biz_id)
    if b is None:
        return biz_screen(content, ch, note)
    vs = biz.visitors(content, ch, b)
    lines = [style.header("property", "Зал: " + texts.esc(biz.title(content, b))),
             T.STATUS.format(eddies=views.money(ch.state.eddies), ap=ch.state.ap), "",
             f"Сегодня в зале. С каждым — одно дело в день, {cfg.visit_ap} ОД."]
    rows: list[list[Button]] = []
    for v in vs:
        tags = (" · криминал" if v.criminal else "") + (" · буйный" if v.rowdy else "") + (" · уже было" if v.used else "")
        lines.append(f"{v.idx + 1}. <b>{texts.esc(v.name)}</b>, {texts.esc(v.role)}{tags}")
        if not v.used:
            rows.append([Button(f"{v.idx + 1}. {v.name}"[:24], "v", str(v.idx))])
    if pick is not None:
        v = next((x for x in vs if x.idx == pick), None)
        if v is not None and not v.used:
            lines += ["", f"<b>{texts.esc(v.name)}</b>, {texts.esc(v.role)}. Что делаем?"]
            acts = [Button(f"Угостить · {cfg.treat_price}", "va", f"{v.idx}:treat"), Button("Подслушать", "va", f"{v.idx}:listen")]
            if v.rowdy:
                acts.append(Button("Выставить", "va", f"{v.idx}:kick"))
            if v.criminal:
                acts.append(Button("Предложить дело", "va", f"{v.idx}:deal"))
            acts.append(Button("Обчистить", "va", f"{v.idx}:rob"))
            rows = [acts[i:i + 2] for i in range(0, len(acts), 2)]
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "one", str(biz_id)), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="biz", state={"id": biz_id})


def biz_scheme_screen(content: Content, ch: ActiveCharacter, biz_id: int, note: str | None = None) -> Screen:
    b = biz.state_of(ch, biz_id)
    if b is None:
        return biz_screen(content, ch, note)
    lvl = int(b.get("level", 1))
    cur = biz.scheme_of(content, b)
    lines = [style.header("crime", "Схемы: " + texts.esc(biz.title(content, b))),
             T.STATUS.format(eddies=views.money(ch.state.eddies), ap=ch.state.ap), "",
             "Через заведение можно пустить грязные деньги. Доход сверху каждую неделю, но растёт розыск, "
             "а NCPD может прийти с обыском."]
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    if cur is not None:
        lines += ["", f"Сейчас: <b>{texts.esc(cur.name)}</b>. {texts.esc(cur.description)}"]
        rows.append([Button("Свернуть схему", "nostop")])
    else:
        for x in biz.schemes_for(content, b):
            lines += ["", f"<b>{texts.esc(x.name)}</b> · {views.money(x.price)}: +{views.money(x.income * lvl)}/нед, "
                          f"рейд {round(x.raid_chance * 100)}%/нед\n<i>{texts.esc(x.description)}</i>"]
            label = f"{x.name} · {views.money(x.price)}"
            rows.append([Button(label if len(label) <= 24 else x.name[:24], "scheme", x.key)])
    rows.append([Button(texts.BACK, "one", str(biz_id)), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="biz", state={"id": biz_id})


@on_screen("biz")
async def biz_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "property")
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    cfg = c.balance.players.business
    bid = int(arg) if action == "one" and arg and arg.isdigit() else int(ctx.state.get("id", 0) or 0)
    if action == "list":
        return biz_screen(c, ch)
    if action == "shop":
        return biz_shop_screen(c, ch)
    if action == "one":
        return biz_one_screen(c, ch, bid)
    if action == "nokeep":  # the kind's own name: the city hears of the opening now
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            await biz.announce(s, c, ch, bid)
            await game.save_active(s, ch, ctx.ui.settings.timezone)
        return biz_one_screen(c, ch, bid)
    if action == "mgall":  # owner report 24.10: serve the whole chain at once
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            done, left, spent = await biz.manage_all(s, c, ch, random.Random(), year, ctx.ui.settings.timezone)
        note = f"Сделано: {done}, потрачено {views.money(spent)} эдди." if done else "Не хватает эдди."
        if left:
            note += f" Не хватило денег ещё на {left}."
        return biz_screen(c, ch, note)
    if action == "mg" and arg:  # owner request 24.10: restock, staff bonus, repair
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            mr = await biz.manage(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, bid, arg)
        if mr.blocked:
            return biz_one_screen(c, ch, bid, {"eddies": "Не хватает эдди.", "max": "Тут и так всё в порядке.",
                                               "gone": "Этого заведения больше нет."}.get(mr.blocked, mr.blocked))
        done = {"stock": "Фуры разгружены, склад полон.", "staff": "Премия роздана. Персонал улыбается искренне.",
                "cond": "Мастера поработали: проводка новая, полы целые."}[arg]
        return biz_one_screen(c, ch, bid, f"{done} −{views.money(mr.amount)} эдди.")
    if action == "hall":
        return biz_hall_screen(c, ch, bid)
    if action == "v" and arg and arg.isdigit():
        return biz_hall_screen(c, ch, bid, pick=int(arg))
    if action == "schemes":
        return biz_scheme_screen(c, ch, bid)
    if action == "va" and not (arg and ":" in arg and arg.split(":", 1)[0].isdigit()):
        return biz_hall_screen(c, ch, bid)
    if action in ("va", "scheme", "nostop"):
        rng = random.Random()
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            if action == "va" and arg and ":" in arg:
                i, act = arg.split(":", 1)
                vr = await biz.visit(s, c, ch, rng, year, ctx.ui.settings.timezone, bid, int(i), act)
            else:
                sr = await biz.set_scheme(s, c, ch, rng, year, ctx.ui.settings.timezone, bid,
                                          arg if action == "scheme" else None)
        if action == "va":
            if vr.blocked:
                return biz_hall_screen(c, ch, bid, {"used": "С этим гостем сегодня уже было.", "ap": "ОД кончились.",
                                                    "eddies": "Не хватает эдди."}.get(vr.blocked, "Не вышло."))
            parts = [views.check_line(vr.check)] if vr.check is not None else []  # type: ignore[arg-type]
            parts.append(style.quote(texts.esc(vr.text)))
            if vr.applied is not None:
                sm = views.effects_summary(vr.applied, c)  # type: ignore[arg-type]
                if sm:
                    parts.append(sm)
            parts.extend(texts.esc(x) for x in (vr.lines or []))
            return biz_hall_screen(c, ch, bid, "\n".join(parts))
        if sr.blocked:
            return biz_scheme_screen(c, ch, bid, T.BIZ_BLOCKED.get(sr.blocked, sr.blocked))
        return biz_scheme_screen(c, ch, bid, "Схема запущена." if action == "scheme" else "Схема свёрнута. Подсобка снова просто подсобка.")
    if action == "rename":
        return biz_one_screen(c, ch, bid, naming=True)
    if action == "sell":
        b = biz.state_of(ch, bid)
        if not b:
            return biz_screen(c, ch)
        amount = int(b.get("invested", 0)) * cfg.sell_pct // 100
        return Screen(T.BIZ_SELL_CONFIRM.format(name=texts.esc(biz.title(c, b)), amount=views.money(amount)),
                      [[Button(T.BIZ_SELL_YES, "yes"), Button(texts.CANCEL, "no")]], name="bizsell", state={"id": bid})
    if action in ("ads", "guard") and not (arg and arg.isdigit()):  # owner request: pick the length
        return biz_term_screen(c, ch, bid, action)
    if action not in ("buy", "up", "ads", "guard"):
        return biz_screen(c, ch)
    rng, tz = random.Random(), ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if action == "buy":
            res = await biz.buy(s, c, ch, rng, year, tz, arg or "")
        elif action == "up":
            res = await biz.upgrade(s, c, ch, rng, year, tz, bid)
        else:
            res = await biz.boost(s, c, ch, rng, year, tz, bid, action, int(arg or 1))
    if res.blocked:
        note = T.BIZ_BLOCKED.get(res.blocked, res.blocked)
        return biz_shop_screen(c, ch, note) if action == "buy" else biz_one_screen(c, ch, bid, note)
    if action == "buy":
        kind = biz.kind_of(c, arg or "")
        return biz_one_screen(c, ch, res.biz_id, T.BIZ_BOUGHT.format(name=texts.esc(kind.name) if kind else ""), naming=True)
    if action == "up":
        note = T.BIZ_UPGRADED.format(cost=views.money(res.amount))
    elif action == "ads":
        note = T.BIZ_ADS_DONE.format(weeks=cfg.ads_weeks * int(arg or 1))
    else:
        note = T.BIZ_GUARD_DONE.format(weeks=cfg.guard_weeks * int(arg or 1))
    return biz_one_screen(c, ch, bid, note + f" Потрачено {views.money(res.amount)}.")


@on_text("biz_name")
async def biz_name_text(ctx: Ctx, text: str) -> Screen | None:
    """The name of a business typed in the chat (owner request)."""
    c = ctx.content
    bid = int(ctx.state.get("id", 0) or 0)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        res = await biz.rename(s, c, ch, ctx.ui.settings.timezone, bid, text)
    if res.blocked == biz.BLOCK_NAME:
        return biz_one_screen(c, ch, bid, T.BIZ_NAME_BAD, naming=True)
    if res.blocked:
        return biz_screen(c, ch, T.BIZ_BLOCKED.get(res.blocked, res.blocked))
    b = biz.state_of(ch, bid)
    return biz_one_screen(c, ch, bid, T.BIZ_NAMED.format(name=texts.esc(b.get("name", "") if b else "")))


@on_screen("bizsell")
async def bizsell_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    bid = int(ctx.state.get("id", 0) or 0)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if action != "yes":
            return biz_one_screen(c, ch, bid)
        year = await game.current_year(s, c)
        res = await biz.sell(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, bid)
    if res.blocked:
        return biz_screen(c, ch, T.BIZ_BLOCKED.get(res.blocked, res.blocked))
    return biz_screen(c, ch, T.BIZ_SOLD.format(amount=views.money(res.amount)))
