"""Gigs in the window (PLAN 5.2-5.5, 5.7): the board, the brief, the approach, the checks, the result.

Screens: `gigs` (the fixer's board) -> `gigbrief` (brief and four approaches) -> `gigcheck` (one
check per screen with luck / glitch / accept after a miss) -> `gigres` (the outcome), or the fight
screens of handlers/combat.py when a loud gig needs one. Closing the window loses nothing: the run
lives on the character and `handlers.events.resume_screen` rebuilds the right screen.
"""

from __future__ import annotations

import random

from app.bot import texts_combat as T
from app.bot import views, views_combat
from app.bot.handlers.menu import menu_screen
from app.bot.handlers.week import death_screen
from app.bot.texts import SKILL_LABELS, STAT_LABELS
from app.bot import texts_narrator as T_N
from app.bot import texts_players as TP
from app.bot import texts_proxy as TXP
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, on_screen, on_text
from app.bot.ui.screen import Button, Screen, list_rows, pack, pick_label
from app.content.loader import Content
from app.db import session as db
from app.engine import gigs as engine_gigs
from app.engine import proxy as proxy_eng
from app.engine.checks import CheckResult
from app.narrator import narrator
from app.services import events as ev_service
from app.services import game
from app.services import gigs as gigs_service
from app.services import players as players_service
from app.services import proxy as proxy_service
from app.engine.state import FLAG_GIG_BACKUP
from app.services.game import ActiveCharacter
from app.services.gigs import GigError, GigInfo, GigResult, RunStep

esc = T.esc


def _err(reason: str) -> str:
    return f"<i>{T.GIG_ERRORS.get(reason, T.GIG_ERRORS['gone'])}</i>\n\n"


# -- builders -------------------------------------------------------------------------------------------------


def board_screen(content: Content, ch: ActiveCharacter, board: gigs_service.Board, prefix: str = "") -> Screen:
    district = content.districts.get(ch.state.district)
    lines = [T.GIGS_TITLE.format(district=esc(district.name if district else ch.state.district))]
    if board.fixer_name is None:
        lines.append(T.GIGS_NO_FIXER)
        return Screen(prefix + "\n".join(lines), [[Button(T.MENU, "back")]], name="gigs")
    lines.append(T.GIGS_FIXER.format(fixer=esc(board.fixer_name)))
    rows: list[list[Button]] = []
    if board.run_pending:
        lines.append(T.GIGS_BUSY)
        rows.append([Button(T.GIGS_CONTINUE, "resume")])
    elif board.open:
        lines.append(T.GIGS_PICK)
        chars = content.balance.ui.button_chars
        picks: list[Button] = []
        for n, g in enumerate(board.open, 1):
            lines.append(T.GIG_LINE.format(
                n=n, title=esc(g.title), tier=g.tier, pay=views.money(g.pay), kind=T.GIG_TYPE_WORDS.get(g.type, g.type)))
            picks.append(Button(pick_label(n, T.GIG_TYPE_SHORT.get(g.type), chars), "pick", str(g.id)))
        rows.extend(pack(picks, chars, max_per_row=2))
    else:
        lines.append(T.GIGS_EMPTY)
    st = ch.state
    rank = engine_gigs.rank_of(st)
    for g in board.locked:
        need_cred, need_rank = engine_gigs.lock_reason(content, st.street_cred, g.tier, rank)
        gb = content.balance.gigs
        if need_rank and need_cred:
            msg = T.GIG_LOCKED_BOTH.format(tier=g.tier, cred=gb.unlock_cred_by_tier[g.tier], rank=gb.unlock_rank_by_tier[g.tier])
        elif need_rank:
            msg = T.GIG_LOCKED_RANK.format(tier=g.tier, rank=gb.unlock_rank_by_tier[g.tier])
        else:
            msg = T.GIG_LOCKED.format(tier=g.tier, cred=gb.unlock_cred_by_tier[g.tier])
        lines.append(f"<i>{msg}</i>")
    if st.street_cred >= content.balance.gigs.unlock_cred_by_tier[5] or rank:  # the contract ladder is shown at the top
        goal = engine_gigs.next_rank_goal(content, st.street_cred, rank)
        lines.append(T.GIG_RANK_LINE.format(rank=rank, tier=goal[0], need=goal[1] - rank) if goal
                     else T.GIG_RANK_TOP.format(rank=rank))
    rows.append([Button(T.MENU, "back")])
    return Screen(prefix + "\n".join(lines), rows, name="gigs")


def brief_screen(content: Content, info: GigInfo, brief_text: str, prefix: str = "",
                 backup: dict | None = None, proxy: bool = False) -> Screen:
    template = content.gig_templates[info.template_key]
    lines = [style.header("gigs", esc(info.title)), style.quote(esc(brief_text)),
             T.BRIEF_FACTS.format(tier=info.tier, pay=views.money(info.pay), target=esc(info.target_name)),
             T.BRIEF_AP.format(ap=content.balance.gigs.ap_cost)]
    dep = engine_gigs.deposit(content, info.tier, info.pay)
    if dep:
        lines.append(T.BRIEF_DEPOSIT.format(sum=views.money(dep)))
    lines += ["", T.BRIEF_APPROACHES]
    plans = gigs_service.approach_plans(content, info)
    for approach in template.approaches:
        checks = ", ".join(
            T.BRIEF_CHECK.format(stat=STAT_LABELS.get(p.stat, p.stat), skill=SKILL_LABELS.get(p.skill, p.skill))
            for p in plans[approach]
        )
        dv = plans[approach][0].dv
        lines.append(style.line(T.APPROACH_ICONS.get(approach, "gigs"), T.BRIEF_APPROACH_LINE.format(
            name=f"<b>{T.APPROACH_BUTTONS[approach]}</b>", checks=f"{checks} ({T.BRIEF_DV.format(dv=dv)})")))
    lines.append(T.BRIEF_LOUD_WARN)
    names = list(template.approaches)
    rows = [[Button(T.APPROACH_BUTTONS[a], "go", a) for a in names[i:i + 2]] for i in range(0, len(names), 2)]
    if gigs_service.free_action_available():
        rows.append([Button(T.BRIEF_FREE_BUTTON, "free")])
    if backup and backup.get("gig") == info.id:
        lines.append(TP.HIRE_PICKED.format(name=esc(str(backup.get("name", ""))), share=backup.get("share", 0)))
        rows.append([Button(TP.BTN_BACKUP_SET.format(name=str(backup.get("name", "")).split()[0]), "hire")])
    else:
        rows.append([Button(TP.BTN_BACKUP, "hire")])
    if proxy:  # owner request 29.10: the hired players do the gig in the employer's place
        rows.append([Button(TXP.BTN, "proxy")])
    rows.append([Button(T.BACK, "back")])
    return Screen(prefix + "\n".join(lines), rows, name="gigbrief", state={"gig": info.id})


def check_screen(content: Content, info: GigInfo, ch: ActiveCharacter, step: RunStep) -> Screen:
    run = gigs_service.run_of(ch) or {}
    lines = [T.CHECK_HEAD.format(title=esc(info.title), approach=T.APPROACH_BUTTONS.get(run.get("approach", ""), ""),
                                 i=step.index, n=step.total)]
    for d in run.get("results", []):
        prev = CheckResult(**d)
        lines.append(T.CHECK_PASSED_BEFORE.format(icon=style.icon("ok" if prev.ok else "fail"),
                                                  line=f"{STAT_LABELS.get(prev.stat or '', '')} + "
                                                      f"{SKILL_LABELS.get(prev.skill or '', '')}: "
                                                      f"{T.CHECK_PREV_OK if prev.ok else T.CHECK_PREV_FAIL}"))
    lines += ["", views.check_line(step.result)]
    backup = run.get("backup")
    if backup and backup.get("assist"):
        lines.append(TP.HIRE_ASSIST.format(name=esc(str(backup.get("name", ""))), n=backup["assist"]))
    rows: list[list[Button]] = []
    rev = str(step.rev)
    if step.result.ok:
        if step.result.luck:
            lines.append(T.CHECK_PASS_LUCK)
        else:
            lines.append(T.CHECK_PASS)
        rows.append([Button(T.NEXT, "take", rev)])
    else:
        lines.append(T.CHECK_FAIL if (step.luck_points or step.can_glitch) else T.CHECK_FAIL_NOTHING)
        if step.luck_points:
            rows.append([Button(T.BTN_LUCK.format(points=step.luck_points, pool=ch.state.luck_pool), "luck", rev)])
        if step.can_glitch:
            rows.append([Button(T.BTN_GLITCH.format(glitches=ch.state.glitches), "glitch", rev)])
        rows.append([Button(T.BTN_ACCEPT_FAIL, "take", rev)])
    return Screen("\n".join(lines), rows, name="gigcheck", state={"rev": step.rev})


def result_screen(content: Content, ch: ActiveCharacter, res: GigResult) -> Screen:
    if res.died:
        return death_screen(ch.state.name, res.death_cause, res.lines)
    lines = [T.GIG_RESULT_HEAD.format(title=esc(res.info.title), outcome=T.OUTCOME_TITLES[res.outcome],
                                      mark=T.OUTCOME_MARKS.get(res.outcome, "")), style.quote(esc(res.text))]
    if res.fight is not None:

        lines.append(T.GIG_FIGHT_NOTE.format(result=views_combat.fight_result_word(res.fight["result"])))
    lines.append(T.GIG_PAY.format(pay=views.money(res.pay)) if res.pay else T.GIG_NO_PAY)
    summary = views.effects_summary(res.applied, content)
    if summary:
        lines.append(summary)
    for key, delta in res.applied.fixer_rel.items():
        if delta:
            fixer = content.fixers.get(key)
            lines.append(T.GIG_FIXER_REL.format(fixer=esc(fixer.name if fixer else key), delta=views.signed(delta)))
    for key, delta in res.applied.faction_rep.items():
        if delta:
            lines.append(T.GIG_FACTION_REP.format(faction=esc(views_combat.faction_dat(content, key)), delta=views.signed(delta)))
    if res.backup:
        b = res.backup
        cut = int(b.get("cut", 0) or 0)
        lines.append(TP.HIRE_RESULT.format(name=esc(str(b.get("name", ""))), cut=views.money(cut)) if cut
                     else TP.HIRE_RESULT_NONE.format(name=esc(str(b.get("name", ""))), a=""))
    lines.extend(res.lines)
    if res.public:
        lines.append(T.GIG_PUBLIC)
    return Screen("\n".join(lines), [[Button(T.NEXT, "next")]], name="gigres")


# -- helpers ----------------------------------------------------------------------------------------------------


async def _board(ctx: Ctx, prefix: str = "") -> Screen:
    rng = random.Random()
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        board = await gigs_service.load_board(s, ctx.content, ch, year, rng)
    return board_screen(ctx.content, ch, board, prefix)


async def step_screen(ctx: Ctx, ch: ActiveCharacter, step: RunStep) -> Screen:
    run = gigs_service.run_of(ch)
    assert run is not None
    async with db.session_scope() as s:
        info = await gigs_service.get_info(s, ctx.content, int(run["gig"]))
    assert info is not None
    return check_screen(ctx.content, info, ch, step)


async def resume_gig_screen(ctx: Ctx, ch: ActiveCharacter) -> Screen | None:
    """The screen of a gig in progress, or None when there is none. Settles a gig whose fight is over."""
    run = gigs_service.run_of(ch)
    if run is None:
        return None
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    if gigs_service.needs_settle(ch):
        async with db.session_scope() as s:
            ch2 = await game.get_active_character(s, ctx.user_id)
            assert ch2 is not None
            year = await game.current_year(s, ctx.content)
            try:
                res = await gigs_service.settle_run(s, ctx.content, ch2, rng, year, tz)
            except GigError:
                return None
        return result_screen(ctx.content, ch2, res)
    step = gigs_service.current_step(ctx.content, ch)
    if step is not None:
        return await step_screen(ctx, ch, step)
    return None


# -- handlers ---------------------------------------------------------------------------------------------------


@on_screen("gigs")
async def gigs_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await menu_screen(ctx)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
    if ch is None:
        return await menu_screen(ctx)
    if ev_service.has_pending(ch):  # an unfinished event, check or fight comes first
        from app.bot.handlers.events import resume_screen

        return await resume_screen(ctx, ch)
    from app.engine import abroad

    away = abroad.country(ch.state, ctx.content)
    if away is not None and not abroad.in_city(ch.state, ctx.content):  # a country without its own city: no fixers
        from app.bot.ui.screen import Button, Screen

        return Screen(f"✈️ Ты живёшь в стране «{away.name}». Фиксеры Найт-Сити дают заказы только тем, кто в городе. "
                      "Местная работа — в «Город» → «Мир».",
                      [[Button("Мир", "world"), Button("Меню", "back")]], name="gigsaway")
    if action == "pick" and arg is not None and arg.isdigit():
        async with db.session_scope() as s:
            try:
                info, text = await gigs_service.brief(s, ctx.content, ch, int(arg))
            except GigError as e:
                pass_reason = e.reason
            else:
                if info.status != gigs_service.STATUS_OPEN or info.district != ch.state.district:
                    pass_reason = "taken" if info.status == gigs_service.STATUS_TAKEN else "gone"
                else:
                    return brief_screen(ctx.content, info, text, backup=ch.state.flags.get(FLAG_GIG_BACKUP),
                                        proxy=await proxy_service.has_hands(s, ch))
        return await _board(ctx, _err(pass_reason))
    return await _board(ctx)


@on_screen("gigbrief")
async def gigbrief_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    gig_id = ctx.state.get("gig")
    if action == "back" or not isinstance(gig_id, int):
        return await _board(ctx)
    if action == "free" and gigs_service.free_action_available():
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            info, text = await gigs_service.brief(s, ctx.content, ch, gig_id)
        scr = brief_screen(ctx.content, info, text)
        return Screen(T.FREE_PROMPT, [[Button(T.BACK, "back")]], awaiting_text="gig_free", name="gigbrief", state=scr.state)
    if action == "go" and arg is not None:
        return await _start_gig(ctx, gig_id, arg)
    if action == "hire":
        return await hire_screen(ctx, gig_id)
    if action == "proxy":
        return await proxy_pick_screen(ctx, gig_id)
    return await _board(ctx)


async def _brief_again(ctx: Ctx, gig_id: int, note: str = "") -> Screen:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        try:
            info, text = await gigs_service.brief(s, ctx.content, ch, gig_id)
        except GigError as e:
            return await _board(ctx, _err(e.reason))
        has = await proxy_service.has_hands(s, ch)
    return brief_screen(ctx.content, info, text, prefix=(note + "\n\n") if note else "",
                        backup=ch.state.flags.get(FLAG_GIG_BACKUP), proxy=has)


# -- a hired player does the gig (owner request 29.10, docs/PLAN_PROXY.md) -------------------------------------------


async def proxy_pick_screen(ctx: Ctx, gig_id: int, note: str = "") -> Screen:
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        info = await gigs_service.get_info(s, c, gig_id)
        hands = await proxy_service.roster(s, c, ch)
    if info is None:
        return await _board(ctx, _err("gone"))
    lines = [style.header("war", TXP.TITLE), TXP.INTRO, ""]
    if note:
        lines = [note, ""] + lines
    if not hands:
        lines.append(TXP.NONE)
    rows: list[list[Button]] = []
    for h in hands:
        fee = proxy_eng.fee(c, h.wage, info.tier)
        days = proxy_eng.days_of(c, info.tier)
        lines.append(TXP.LINE.format(
            name=esc(h.name), role=TXP.ROLE.get(h.role, h.role), hp=h.hp, max_hp=h.max_hp, wage=views.money(h.wage), days=days, fee=views.money(fee),
            state=TXP.STATE_BAD.format(why=TXP.WHY.get(h.why, h.why)) if h.why else ""))
        if not h.why:
            rows.append([Button(h.name[:24], "m", str(h.tg))])
    rows.append([Button(T.BACK, "back")])
    return Screen("\n".join(lines), rows, name="gigproxy", state={"gig": gig_id})


@on_screen("gigproxy")
async def gigproxy_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    gig_id = ctx.state.get("gig")
    if not isinstance(gig_id, int):
        return await _board(ctx)
    if action == "m" and arg is not None and arg.isdigit():
        return await _proxy_approach_screen(ctx, gig_id, int(arg))
    return await _brief_again(ctx, gig_id)


async def _proxy_approach_screen(ctx: Ctx, gig_id: int, tg: int) -> Screen:
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        info = await gigs_service.get_info(s, c, gig_id)
        hand = next((h for h in await proxy_service.roster(s, c, ch) if h.tg == tg and not h.why), None)
    if info is None or hand is None:
        return await proxy_pick_screen(ctx, gig_id, f"<i>{TXP.WHY['gone']}</i>")
    template = c.gig_templates[info.template_key]
    plans = gigs_service.approach_plans(c, info)
    lines = [style.header("war", TXP.TITLE),
             TXP.PICK_APPROACH.format(name=esc(hand.name), fee=views.money(proxy_eng.fee(c, hand.wage, info.tier)),
                                      days=proxy_eng.days_of(c, info.tier)), ""]
    for approach in template.approaches:
        checks = ", ".join(
            T.BRIEF_CHECK.format(stat=STAT_LABELS.get(p.stat, p.stat), skill=SKILL_LABELS.get(p.skill, p.skill))
            for p in plans[approach])
        lines.append(style.line(T.APPROACH_ICONS.get(approach, "gigs"), T.BRIEF_APPROACH_LINE.format(
            name=f"<b>{T.APPROACH_BUTTONS[approach]}</b>", checks=f"{checks} ({T.BRIEF_DV.format(dv=plans[approach][0].dv)})")))
    lines.append(T.BRIEF_LOUD_WARN)
    names = list(template.approaches)
    rows = [[Button(T.APPROACH_BUTTONS[a], "go", a) for a in names[i:i + 2]] for i in range(0, len(names), 2)]
    rows.append([Button(T.BACK, "back")])
    return Screen("\n".join(lines), rows, name="gigproxy2", state={"gig": gig_id, "tg": tg})


@on_screen("gigproxy2")
async def gigproxy2_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    gig_id, tg = ctx.state.get("gig"), ctx.state.get("tg")
    if not isinstance(gig_id, int) or not isinstance(tg, int):
        return await _board(ctx)
    if action != "go" or arg is None:
        return await proxy_pick_screen(ctx, gig_id)
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        res = await proxy_service.send(s, ctx.content, ch, rng, year, tz, gig_id, arg, tg)
    if res.blocked:
        return await proxy_pick_screen(ctx, gig_id, f"<i>{TXP.WHY.get(res.blocked, res.blocked)}</i>")
    return proxy_result_screen(ctx.content, ch, res)


def proxy_result_screen(content: Content, ch: ActiveCharacter, res: "proxy_service.Sent") -> Screen:
    if res.died:
        return death_screen(ch.state.name, res.death_cause, res.lines)
    assert res.info is not None and res.hand is not None and res.applied is not None
    lines = [TXP.RESULT_HEAD.format(title=esc(res.info.title), outcome=T.OUTCOME_TITLES[res.outcome]),
             style.quote(esc(res.text)), TXP.RESULT_WHO.format(name=esc(res.hand.name), days=res.days)]
    if res.fight is not None:
        lines.append(TXP.RESULT_FIGHT.format(result=TXP.FIGHT_WORDS.get(res.fight["result"], res.fight["result"])))
    lines.append(TXP.RESULT_FEE.format(fee=views.money(res.fee)))
    lines += ["", TXP.RESULT_REWARD, TXP.RESULT_PAY.format(pay=views.money(res.pay)) if res.pay else TXP.RESULT_NO_PAY]
    summary = views.effects_summary(res.applied, content)
    if summary:
        lines.append(summary)
    for key, delta in res.applied.fixer_rel.items():
        if delta:
            fixer = content.fixers.get(key)
            lines.append(T.GIG_FIXER_REL.format(fixer=esc(fixer.name if fixer else key), delta=views.signed(delta)))
    lines.extend(res.lines)
    hurt = proxy_service.hurt_text(content, res.hurt)
    lines += ["", TXP.RESULT_HURT.format(hurt=esc(hurt)) if hurt else TXP.RESULT_CLEAN]
    if res.public:
        lines.append(T.GIG_PUBLIC)
    return Screen("\n".join(lines), [[Button(T.NEXT, "next")]], name="gigres")


async def hire_screen(ctx: Ctx, gig_id: int) -> Screen:
    """Pick a merc from the exchange for this gig (docs/EPIC_PLAYERS.md, phase 2)."""
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        found = await players_service.mercs(s, c, ch, ctx.ui.settings.timezone)
    lines = [TP.HIRE_TITLE, TP.HIRE_TEXT.format(bonus=c.balance.players.hire.assist_bonus), ""]
    if not found:
        lines.append(TP.HIRE_NONE)
    for m in found:
        skills = ", ".join(SKILL_LABELS.get(k, k).lower() for k in m.best) or "без навыков"
        lines.append(TP.HIRE_LINE.format(name=esc(m.other.name), share=m.share, skills=skills))
    numbered, rows = list_rows([m.other.name for m in found], "m", [str(m.other.char_id) for m in found],
                               c.balance.ui.button_chars)
    if numbered:
        lines = lines[:3] + [f"{i}. {x}" for i, x in enumerate(lines[3:], 1)]
    rows.append([Button(TP.BTN_NO_BACKUP, "none")])
    rows.append([Button(T.BACK, "back")])
    return Screen("\n".join(lines), rows, name="gighire", state={"gig": gig_id})


@on_screen("gighire")
async def gighire_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    gig_id = ctx.state.get("gig")
    if not isinstance(gig_id, int):
        return await _board(ctx)
    c = ctx.content
    note = ""
    if action in ("m", "none"):
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            if action == "none":
                ch.state.flags.pop(FLAG_GIG_BACKUP, None)
                note = TP.HIRE_CLEARED
            elif arg and arg.isdigit():
                pick = next((m for m in await players_service.mercs(s, c, ch, ctx.ui.settings.timezone)
                             if m.other.char_id == int(arg)), None)
                if pick is not None:
                    ch.state.flags[FLAG_GIG_BACKUP] = {
                        "gig": gig_id, "char": pick.other.char_id, "tg": pick.other.tg_id, "name": pick.other.name,
                        "share": pick.share,
                    }
            await game.save_active(s, ch, ctx.ui.settings.timezone)
    return await _brief_again(ctx, gig_id, note)


async def _start_gig(ctx: Ctx, gig_id: int, approach: str, skill: str | None = None, summary: str = "") -> Screen:
    """Take the gig with an approach. The free action passes the skill of the first check and the plan's summary."""
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        try:
            backup = ch.state.flags.pop(FLAG_GIG_BACKUP, None)
            if not isinstance(backup, dict) or backup.get("gig") != gig_id:
                backup = None
            step = await gigs_service.start_run(s, ctx.content, ch, rng, tz, gig_id, approach, skill=skill, plan=summary,
                                                backup=backup)
        except GigError as e:
            reason = e.reason
        else:
            reason = ""
    if reason:
        return await _board(ctx, _err(reason))
    scr = await step_screen(ctx, ch, step)
    if summary:
        scr.text = T_N.FREE_PLAN.format(plan=esc(summary)) + "\n" + scr.text
    return scr


@on_text("gig_free")
async def gig_free_text(ctx: Ctx, text: str) -> Screen | None:
    """The free action (M8): the narrator turns the text into an approach. Without it, back to the buttons."""
    gig_id = ctx.state.get("gig")
    if not isinstance(gig_id, int):
        return await _board(ctx)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        info, brief_text = await gigs_service.brief(s, ctx.content, ch, gig_id)
    template = ctx.content.gig_templates[info.template_key]
    context = {
        "available_approaches": list(template.approaches),
        "gig": {"title": info.title, "type": info.type, "tier": info.tier},
        "character": {"role": ch.state.role, "skills": gigs_service.top_skills(ch)},
    }
    parsed = await narrator.classify_action(text, context, year=ch.state.current_year)
    approach = parsed.get("approach") if parsed else None
    if approach not in template.approaches:
        return brief_screen(ctx.content, info, brief_text, f"<i>{T.FREE_NOT_UNDERSTOOD}</i>\n\n")
    return await _start_gig(ctx, gig_id, approach, parsed.get("skill"), parsed.get("summary") or "")


@on_screen("gigcheck")
async def gigcheck_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.bot.handlers.events import resume_screen

    rng = random.Random()
    tz = ctx.ui.settings.timezone
    if arg is None or not arg.lstrip("-").isdigit():
        return await _resume(ctx)
    rev = int(arg)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        try:
            if action in ("luck", "glitch"):
                fn = gigs_service.take_luck if action == "luck" else gigs_service.take_glitch
                step = await fn(s, ctx.content, ch, rng, year, tz, rev)
                kind, res = "check", None
            elif action == "take":
                prog = await gigs_service.take_check(s, ctx.content, ch, rng, year, tz, rev)
                kind, step, res = prog.kind, prog.step, prog.result
            else:
                return await _resume(ctx, ch)
        except GigError:
            return await _resume(ctx, ch)
    if kind == "check":
        assert step is not None
        return await step_screen(ctx, ch, step)
    if kind == "fight":
        return await resume_screen(ctx, ch)  # the exits screen of the pending fight
    assert res is not None
    return result_screen(ctx.content, ch, res)


@on_screen("gigres")
async def gigres_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    return await menu_screen(ctx)


async def _resume(ctx: Ctx, ch: ActiveCharacter | None = None) -> Screen:
    from app.bot.handlers.events import resume_screen

    return await resume_screen(ctx, ch)
