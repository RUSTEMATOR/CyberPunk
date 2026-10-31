"""The work section in the window (PLAN 9.3).

work -> [join a career | work harder | ask for a promotion (lifechk) | transfer | workquit (confirm)]
-> workout (what happened) -> back to work. Firing comes from events (content/events_life.yaml).
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot import texts_life as T
from app.bot.handlers import lifecheck as lc
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, has_screen, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.content.schemas import Career
from app.db import session as db
from app.engine import careers as eng
from app.engine.dice import clamp
from app.engine.state import FLAG_EMPLOYER, FLAG_MAXTAC_CONTRACT, CharacterState
from app.services import careers as svc
from app.services import events as ev_service
from app.services import game
from app.services.game import ActiveCharacter
from app.services.lifecheck import LifeResult

esc = texts.esc


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


def _employer(content: Content, st: CharacterState) -> str:
    key = st.flags.get(FLAG_EMPLOYER)
    corp = content.corps.get(str(key)) if key else None
    return T.WORK_EMPLOYER.format(employer=esc(corp.name)) if corp else ""


def _req_text(content: Content, st: CharacterState, career: Career) -> str:
    info = eng.join_info(st, content, career)
    gaps = {(g.kind, g.key): g for g in info.gaps}
    parts: list[str] = []
    req = career.requirements
    for stat, need in req.stats.items():
        g = gaps.get(("stat", stat))
        label = T.STAT_SHORT[stat]
        parts.append(T.WORK_REQ_GAP.format(label=label, need=need, have=g.have) if g else T.WORK_REQ_OK.format(label=label, need=need))
    for skill, need in req.skills.items():
        g = gaps.get(("skill", skill))
        label = texts.SKILL_LABELS.get(skill, skill).lower()
        parts.append(T.WORK_REQ_GAP.format(label=label, need=need, have=g.have) if g else T.WORK_REQ_OK.format(label=label, need=need))
    if req.from_career is not None:
        g = gaps.get(("career", req.from_career.career))
        name = content.careers[req.from_career.career].name
        txt = T.REQ_CAREER.format(name=name, rank=req.from_career.min_rank)
        parts.append(f"{txt} (нет)" if g else f"{txt} (есть)")
    if req.min_humanity_lost:
        g = gaps.get(("humanity", "pch"))
        txt = T.REQ_PCH.format(n=req.min_humanity_lost)
        parts.append(f"{txt} (у тебя {g.have})" if g else f"{txt} (есть)")
    g = gaps.get(("heat", "heat"))
    if g:
        parts.append(T.WORK_REQ_HEAT.format(n=g.need) + f" (у тебя {g.have})")
    return T.WORK_REQ_LINE.format(reqs="; ".join(parts)) if parts else T.WORK_REQ_NONE


PATH_BUTTON = "Свой путь: фиксер и др."


def _pcorp(st: CharacterState, lines: list[str], rows: list[list[Button]]) -> None:
    """Owner request 24.10: the job in a player's megacorp goes alongside the career."""
    job = st.flags.get("pcorp")
    if isinstance(job, dict) and job.get("corp"):
        lines += ["", f"🏢 Ещё ты в штате корпорации «{esc(str(job['corp']))}» (корпорация игрока)."]
        rows.append([Button(f"Корп.: {job['corp']}"[:24], "pcorp")])


def work_screen(content: Content, ch: ActiveCharacter, year: int, note: str | None = None) -> Screen:
    st = ch.state
    cfg = content.balance.careers
    lines = [T.WORK_TITLE, T.WORK_STATUS.format(eddies=views.money(st.eddies), ap=st.ap)]
    if eng.in_jail(st):
        lines.append(T.WORK_JAIL)
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    cur = st.career
    if cur is None:
        lines += ["", T.WORK_NO_JOB]
        for career in eng.joinable(content, year, st):
            if career.key == "maxtac" and career.requirements.from_career is not None:
                continue  # MaxTac is entered by transfer from NCPD (or recruited), not hired off the street
            lines += ["", T.WORK_CAREER_LINE.format(name=esc(career.name), salary=views.money(career.ranks[0].salary)),
                      T.WORK_CAREER_DESC.format(desc=esc(career.description)), _req_text(content, st, career)]
            rows.append([Button(T.WORK_JOIN_BUTTON.format(name=career.name, ap=cfg.join_ap), "join", career.key)])
        _pcorp(st, lines, rows)
        rows.append([Button("🕴 Контракты элиты", "elite")])  # owner request 25.10
        rows.append([Button(PATH_BUTTON, "path")])
        rows.append([Button(texts.BACK, "back")])
        return Screen("\n".join(lines), rows, name="work")

    career = content.careers[cur.key]
    rank = career.ranks[clamp(cur.rank, 1, len(career.ranks)) - 1]
    lines += ["", T.WORK_POST.format(rank=esc(rank.name), career=esc(career.name)) + _employer(content, st),
              T.WORK_PAY.format(salary=views.money(rank.salary), weeks=cur.weeks)]
    if cur.rank < len(career.ranks):
        nxt = career.ranks[cur.rank]
        ck = career.promotion_check
        check = T.STAT_SHORT[ck.stat] + (" + " + texts.SKILL_LABELS.get(ck.skill, ck.skill) if ck.skill else "")
        lines.append(T.WORK_NEXT.format(rank=esc(nxt.name), salary=views.money(nxt.salary), min=rank.min_weeks,
                                        check=check, dv=rank.promotion_dv))
    else:
        lines.append(T.WORK_TOP)
    if eng.bonus(st):
        lines.append(T.WORK_HARD_NOTE.format(n=eng.bonus(st), cap=cfg.work_hard_cap))
    until = st.flags.get(FLAG_MAXTAC_CONTRACT)
    if eng.under_contract(st):
        lines.append(T.WORK_CONTRACT.format(n=int(until) - st.weeks_lived))
    rows.append([Button(T.WORK_BUTTON_HARD.format(ap=cfg.work_hard_ap), "hard")])
    if cur.key == "night_taxi":  # owner request 24.10: the vampires of the night taxi feed
        rows.append([Button("Кормёжка", "vamp", style="danger")])
    if cfg.ops.get(cur.key):  # owner request 23.10: the inner tasks of the career
        rows.append([Button(cfg.ops_title.get(cur.key, "Особые поручения"), "ops")])
    if cur.rank < len(career.ranks):
        rows.append([Button(T.WORK_BUTTON_PROMO.format(ap=cfg.promo_ap), "promo")])
    for other in eng.joinable(content, year, ch.state):  # transfer: MaxTac from NCPD
        fc = other.requirements.from_career
        if fc is not None and fc.career == cur.key:
            lines += ["", T.WORK_CAREER_LINE.format(name=esc(other.name), salary=views.money(other.ranks[0].salary)),
                      _req_text(content, st, other)]
            rows.append([Button(T.WORK_TRANSFER_BUTTON.format(name=other.name, ap=cfg.join_ap), "join", other.key)])
    rows.append([Button(T.WORK_BUTTON_QUIT, "quit")])
    _pcorp(st, lines, rows)
    rows.append([Button("🕴 Контракты элиты", "elite")])  # owner request 25.10
    rows.append([Button(PATH_BUTTON, "path")])
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="work")


def quit_screen(content: Content, ch: ActiveCharacter) -> Screen:
    lines = [T.QUIT_TITLE, T.QUIT_TEXT]
    if eng.under_contract(ch.state):
        lines.append(T.QUIT_DESERT.format(heat=content.balance.careers.desertion_heat))
    return Screen("\n".join(lines), [[Button(T.QUIT_YES, "yes")], [Button(texts.CANCEL, "no")]], name="workquit")


# -- results --------------------------------------------------------------------------------------------------------


def _blocked_note(res: LifeResult) -> str:
    if res.no_ap:
        return T.WORK_NO_AP
    return T.WORK_BLOCKED.get(res.blocked or "", res.blocked or "").format(n=res.data.get("n", 0))


def _out(lines: list[str]) -> Screen:
    return Screen("\n".join(x for x in lines if x), [[Button(T.WORK_BACK, "back"), Button(texts.MENU, "menu")]], name="workout")


def render_join(content: Content, ch: ActiveCharacter, res: LifeResult) -> Screen:
    career = content.careers[res.data["career"]]
    text = T.JOINED.format(career=esc(career.name), rank=esc(career.ranks[0].name), salary=views.money(career.ranks[0].salary))
    corp = content.corps.get(str(res.data.get("employer", "")))
    if corp:
        text += T.JOINED_EMPLOYER.format(employer=esc(corp.name))
    return _out([text, views.effects_summary(res.applied, content) if res.applied else ""])


def render_hard(content: Content, ch: ActiveCharacter, res: LifeResult) -> Screen:
    return _out([random.choice(T.HARD_TEXT), T.HARD_RESULT.format(n=res.data["bonus"]),
                 views.effects_summary(res.applied, content) if res.applied else "", *res.lines])


def render_promo(content: Content, ch: ActiveCharacter, res: LifeResult) -> Screen:
    lines = [views.check_line(res.check)] if res.check else []
    if res.ok:
        lines.append(T.PROMO_OK.format(rank=esc(res.data["rank_name"]), salary=views.money(res.data["salary"])))
    else:
        lines.append(T.PROMO_FAIL.format(n=res.data["retry"]))
    return _out(lines)


def render_quit(content: Content, ch: ActiveCharacter, res: LifeResult) -> Screen:
    return _out([T.QUIT_DESERT_DONE if res.data.get("desertion") else T.QUIT_DONE,
                 views.effects_summary(res.applied, content) if res.applied else "", *res.lines])


lc.register_renderer(svc.JOIN, render_join)
lc.register_renderer(svc.HARD, render_hard)
lc.register_renderer(svc.PROMO, render_promo)
lc.register_renderer(svc.QUIT, render_quit)


# -- handlers ---------------------------------------------------------------------------------------------------------


@on_screen("work")
async def work_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await menu_screen(ctx)
    if action == "elite":  # owner request 25.10: the contracts of the elite
        return await goto(ctx, "elitejob", "open")
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if ev_service.has_pending(ch) and has_screen("evchoice"):
        return await goto(ctx, "evchoice", "resume")
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    if action == "vamp":
        from app.bot.handlers.vampire import vamp_screen

        return vamp_screen(c, ch)
    if action == "path":
        return await goto(ctx, "path")
    if action == "pcorp":
        return await goto(ctx, "pcjob")
    if action == "ops" and ch.state.career is not None:
        return ops_screen(c, ch)
    if action == "quit" and ch.state.career is not None:
        return quit_screen(c, ch)
    if action not in ("join", "hard", "promo"):
        return work_screen(c, ch, year)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "join" and arg:
            res = await svc.join(s, c, ch, rng, year, tz, arg)
        elif action == "hard":
            res = await svc.work_hard(s, c, ch, rng, year, tz)
        else:
            got = await svc.ask_promotion(s, c, ch, rng, year, tz)
            if not isinstance(got, LifeResult):
                return lc.check_screen(c, ch)
            res = got
    if not res.done:
        return work_screen(c, ch, year, _blocked_note(res))
    return lc.result_screen(c, ch, res)


@on_screen("workquit")
async def workquit_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action != "yes":
        return work_screen(ctx.content, ch, year)
    rng = random.Random()
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        res = await svc.quit_job(s, ctx.content, ch, rng, year, ctx.ui.settings.timezone)
    if not res.done:
        return work_screen(ctx.content, ch, year, _blocked_note(res))
    return lc.result_screen(ctx.content, ch, res)


@on_screen("workout")
async def workout_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    return work_screen(ctx.content, ch, year)


# -- inner tasks of the career (owner request 23.10) -----------------------------------------------------------------


def ops_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    from app.services import career_ops as ops

    st = ch.state
    cfg = content.balance.careers
    cur = st.career
    title = cfg.ops_title.get(cur.key, "Особые поручения") if cur else "Особые поручения"
    lines = [f"<b>{esc(title)}</b>", T.WORK_STATUS.format(eddies=views.money(st.eddies), ap=st.ap), "",
             "Одно поручение в месяц. Удача двигает по службе, провал стоит дорого: работы, здоровья, а иногда и жизни."]
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    if ops.done_this_press(ch):
        lines += ["", "<i>В этом месяце ты уже брался за поручение.</i>"]
    for o in ops.ops_for(content, ch):
        skill = texts.SKILL_LABELS.get(o.skill or "", "")
        risk = []
        if o.fail.fired:
            risk.append("увольнение")
        if o.fail.death_chance:
            risk.append(f"смерть {round(o.fail.death_chance * 100)}%")
        if o.fail.hp:
            risk.append(f"раны до {o.fail.hp}")
        lock = f" (с ранга {o.min_rank})" if cur is not None and cur.rank < o.min_rank else ""
        lines += ["", f"<b>{esc(o.name)}</b>{lock}: {o.ap} ОД, {T.STAT_SHORT[o.stat]}{' + ' + skill if skill else ''} против {o.dv}"
                      + (f", {views.money(o.pay[0])}–{views.money(o.pay[1])}" if o.pay[1] else "")
                      + (", повышение" if o.promo else "") + (f". Риск: {', '.join(risk)}" if risk else ""),
                  f"<i>{esc(o.text)}</i>"]
        if not lock and not ops.done_this_press(ch):
            rows.append([Button(o.name, "op", o.key)])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="workops")


@on_screen("workops")
async def workops_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    import random as _random

    from app.services import career_ops as ops

    c = ctx.content
    if action == "menu":
        return await menu_screen(ctx)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "back":
            return work_screen(c, ch, year)
        if action != "op" or not arg:
            return ops_screen(c, ch)
        r = await ops.run(s, c, ch, _random.Random(), year, ctx.ui.settings.timezone, arg)
    if r.died:
        from app.bot.handlers.week import death_screen

        return death_screen(ch.state.name, r.death_cause, [esc(r.text)])
    if r.blocked is not None:
        return ops_screen(c, ch, {"done": "В этом месяце уже было.", "ap": "ОД кончились.", "rank": "Рано для такого поручения.",
                                  "jail": "Из камеры не выйти.", "none": "Работы нет.", "gone": "Такого поручения нет."}.get(r.blocked, r.blocked))
    parts = [views.check_line(r.check)] if r.check is not None else []  # type: ignore[arg-type]
    parts.append(style.quote(esc(r.text)))
    if r.applied is not None:
        sm = views.effects_summary(r.applied, c)
        if sm:
            parts.append(sm)
    parts.extend(esc(x) for x in r.lines)
    if ch.state.career is None:
        return work_screen(c, ch, year, "\n".join(parts))
    return ops_screen(c, ch, "\n".join(parts))
