"""Players hired by a player's megacorp (owner request 24.10).

work -> pcjob (the employee: jobs for the corp, quit)
empire -> pcstaff (the owner: the staff) -> pcman (a post, firing) | pchire (whom to hire) -> pcpost (which post)
players card -> pcpost (hire this one)
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen, pack
from app.db import session as db
from app.services import game
from app.services import pcorp as svc

BLOCKED = {
    "gone": "Этого уже нет.",
    "jail": "Из камеры на работу не ходят.",
    "nojob": "Ты не в штате корпорации: уволили или корпорации больше нет.",
    "tasks": "Поручения на этот месяц закрыты. Новые — после «Прожить месяц».",
    "ap": "ОД кончились. Нажми «Прожить месяц».",
    "nocorp": "Корпорации больше нет.",
    "employed": "Этот человек уже работает на другую корпорацию игрока.",
    "full": "Штат заполнен.",
    "pending": "Предложение уже ждёт ответа.",
    "stale": "Предложение уже неактуально.",
}


def _note(r: svc.Result) -> str:
    return BLOCKED.get(r.blocked, r.blocked) if r.blocked else texts.esc(r.text)


# -- the employee -----------------------------------------------------------------------------------------------------------


async def job_screen(ctx: Ctx, note: str | None = None) -> Screen:
    c = ctx.content
    cfg = svc.cfg(c)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        got = await svc.sync(s, ch)
        await game.save_active(s, ch, ctx.ui.settings.timezone)
        owner_name = ""
        if got is not None:
            owner_name = str(got[1].get("owner_name", ""))
    st = ch.state
    lines = [style.header("work", "Корпорация игрока"), f"Эдди: {views.money(st.eddies)} · ОД: {st.ap}", ""]
    if note:
        lines += [note, ""]
    rows: list[list[Button]] = []
    if got is None:
        lines.append("Ты не в штате ни одной корпорации игроков. Нанять тебя может владелец мегакорпорации.")
    else:
        _, corp, me = got
        p = svc.post(c, int(me.get("pos", 0)))
        left = svc.tasks_left(c, ch)
        lines += [
            f"🏢 <b>{texts.esc(corp['name'])}</b>, владелец — {texts.esc(owner_name)}.",
            f"Должность: {texts.esc(p.name if p else '?')}, {views.money(p.weekly if p else 0)} в неделю. "
            "Зарплату платит владелец раз в месяц, когда живёт свой месяц; она приходит письмом.",
            "",
            f"Поручения корпорации: {cfg.task_ap} ОД, осталось в этом месяце: {left}. Успех — премия "
            f"{cfg.task_bonus_pct}% недельной зарплаты и статус, корпорации — выручка. Чем выше должность, тем сложнее.",
            "Переговоры: ХЛАД + убеждение. Шпионаж: ИНТ + нетраннинг. Силовая работа: РЕФЛ + стрельба (провал ранит).",
            "В Судную ночь можно прятаться в башне корпорации.",
        ]
        if left > 0:
            rows.append([Button(title, "t", key) for key, (_, _, _, title) in list(svc.TASKS.items())[:2]])
            rows.append([Button(svc.TASKS["guard"][3], "t", "guard")])
        rows.append([Button("⚔️ Как я бьюсь за корпорацию", "pally")])
        rows.append([Button("Уволиться", "quit", style="danger")])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="pcjob")


@on_screen("pcjob")
async def pcjob_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "work", "open")
    if action == "pally":  # owner request 26.10
        return await goto(ctx, "pally", "open")
    if action == "quit":
        return Screen("Уволиться из корпорации? Зарплата перестанет приходить.",
                      [[Button("Да, уволиться", "quit_yes", style="danger")], [Button(texts.CANCEL, "open")]],
                      name="pcjob")
    if action not in ("t", "quit_yes"):
        return await job_screen(ctx)
    c = ctx.content
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "quit_yes":
            r = await svc.quit_job(s, ch, tz)
        else:
            r = await svc.task(s, c, ch, random.Random(), year, tz, str(arg))
    note = _note(r)
    if not r.blocked:
        head = views.check_line(r.check) if r.check is not None else ""
        note = "\n".join(x for x in (head, note) if x)
        if r.applied is not None:
            note += "\n" + "\n".join(views.effects_lines(r.applied, c))
    return await job_screen(ctx, note)


# -- the owner ---------------------------------------------------------------------------------------------------------------


async def staff_screen(ctx: Ctx, note: str | None = None) -> Screen:
    c = ctx.content
    cfg = svc.cfg(c)
    async with db.session_scope() as s:
        from app.services import bigmoney

        corp = (await bigmoney.corps(s)).get(str(ctx.user_id))
    lines = [style.header("status", "Сотрудники корпорации"), ""]
    if note:
        lines += [note, ""]
    rows: list[list[Button]] = []
    if corp is None:
        lines.append("У тебя нет своей мегакорпорации.")
        rows.append([Button(texts.BACK, "back")])
        return Screen("\n".join(lines), rows, name="pcstaff")
    staff = svc.staff_of(corp)
    weeks = c.balance.time.weeks_per_press
    total = sum((svc.post(c, int(r.get("pos", 0))).weekly if svc.post(c, int(r.get("pos", 0))) else 0)
                for r in staff.values())
    lines += [f"🏢 <b>{texts.esc(corp['name'])}</b>. Штат {len(staff)}/{svc.staff_cap(c, corp)}.",
              f"Зарплаты: {views.money(total)} в неделю, {views.money(total * weeks)} за месяц. Списываются с тебя, "
              "когда живёшь месяц; не хватит денег — штат сидит без зарплаты.",
              f"Каждый сотрудник: +{cfg.staff_dv_per} к сложности атаки на башню (до +{cfg.staff_dv_cap}), "
              "поручения приносят выручку письмом, в Судную ночь штат прячется в башне.", ""]
    buttons = []
    for i, (tg, r) in enumerate(staff.items(), 1):
        p = svc.post(c, int(r.get("pos", 0)))
        lines.append(f"{i}. {texts.esc(str(r.get('name', '?')))} — {texts.esc(p.name if p else '?')}, "
                     f"{views.money(p.weekly if p else 0)}/нед.")
        buttons.append(Button(str(i), "m", tg))
    if not staff:
        lines.append("Пока никого.")
    rows = pack(buttons, c.balance.ui.button_chars, max_per_row=5)
    if len(staff) < svc.staff_cap(c, corp):
        rows.append([Button("Нанять игрока", "hire")])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="pcstaff")


@on_screen("pcstaff")
async def pcstaff_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "empire", "open")
    if action == "hire":
        return await hire_screen(ctx)
    if action == "m" and arg:
        return await man_screen(ctx, arg)
    return await staff_screen(ctx)


async def hire_screen(ctx: Ctx) -> Screen:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        people = await svc.candidates(s, ch)
    lines = [style.header("status", "Нанять игрока"), "Кого позвать в штат? Работающих на другую корпорацию игроков "
             "сначала надо переманить: пусть уволятся.", ""]
    buttons = []
    for i, (cid, name, where) in enumerate(people, 1):
        lines.append(f"{i}. {texts.esc(name)}" + (f" — в штате «{texts.esc(where)}»" if where else ""))
        buttons.append(Button(str(i), "p", str(cid)))
    if not people:
        lines.append("Некого.")
    rows = pack(buttons, ctx.content.balance.ui.button_chars, max_per_row=5)
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="pchire")


@on_screen("pchire")
async def pchire_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "p" and arg and arg.isdigit():
        return await post_screen(ctx, cid=int(arg))
    return await staff_screen(ctx)


async def post_screen(ctx: Ctx, cid: int | None = None, tg: str | None = None, back: str = "staff") -> Screen:
    """Pick a post: for a new hire (cid) or for an employee (tg)."""
    c = ctx.content
    async with db.session_scope() as s:
        from app.db.models import Character

        row = await s.get(Character, cid) if cid else None
    who = f"{row.name} {row.surname or ''}".strip() if row is not None else ""
    title = f"Нанять: {who}" if cid else "Сменить должность"
    lines = [style.header("status", title), "Должность задаёт зарплату (в неделю) и сложность поручений.", ""]
    rows: list[list[Button]] = []
    for i, p in enumerate(svc.posts(c)):
        lines.append(f"• {texts.esc(p.name)}: {views.money(p.weekly)}/нед.")
        label = f"{p.name} · {views.money(p.weekly)}"
        if len(label) > 24:
            label = f"{p.name.split()[0]} · {views.money(p.weekly)}"
        rows.append([Button(label[:24], "pos", str(i))])
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="pcpost",
                  state={"cid": cid or 0, "tg": tg or "", "back": back})


@on_screen("pcpost")
async def pcpost_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    st = ctx.state or {}
    back = str(st.get("back", "staff"))
    cid, tg = int(st.get("cid", 0) or 0), str(st.get("tg", "") or "")
    if action != "pos" or not arg or not arg.isdigit():
        if back == "card" and cid:
            from app.bot.handlers.players import card_screen

            return await card_screen(ctx, cid)
        return await staff_screen(ctx)
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if cid:
            r = await svc.offer(s, c, ch, year, cid, int(arg))
        else:
            r = await svc.set_post(s, c, ch, tg, int(arg))
    if cid and not r.blocked:
        from app.bot.handlers.players import _ping_target

        async with db.session_scope() as s:
            from app.db.models import Character

            row = await s.get(Character, cid)
        if row is not None:
            await _ping_target(ctx, row.player_tg_id, "offer")
    if back == "card" and cid:
        from app.bot.handlers.players import card_screen

        return await card_screen(ctx, cid, _note(r))
    return await staff_screen(ctx, _note(r))


async def man_screen(ctx: Ctx, tg: str, note: str | None = None) -> Screen:
    c = ctx.content
    async with db.session_scope() as s:
        from app.services import bigmoney

        corp = (await bigmoney.corps(s)).get(str(ctx.user_id))
    rec = svc.staff_of(corp).get(tg) if corp else None
    if rec is None:
        return await staff_screen(ctx, BLOCKED["gone"])
    p = svc.post(c, int(rec.get("pos", 0)))
    lines = [style.header("status", texts.esc(str(rec.get("name", "?")))),
             f"Должность: {texts.esc(p.name if p else '?')}, {views.money(p.weekly if p else 0)}/нед."]
    if note:
        lines += ["", note]
    rows = [[Button("Сменить должность", "post")], [Button("Уволить", "fire", style="danger")],
            [Button(texts.BACK, "back")]]
    return Screen("\n".join(lines), rows, name="pcman", state={"tg": tg})


@on_screen("pcman")
async def pcman_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    tg = str((ctx.state or {}).get("tg", ""))
    if action == "post" and tg:
        return await post_screen(ctx, tg=tg)
    if action == "fire" and tg:
        return Screen("Уволить сотрудника? Он узнает об этом письмом.",
                      [[Button("Да, уволить", "fire_yes", style="danger")], [Button(texts.CANCEL, "back")]],
                      name="pcman", state={"tg": tg})
    if action == "fire_yes" and tg:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            r = await svc.fire(s, ch, tg)
        return await staff_screen(ctx, _note(r))
    return await staff_screen(ctx)
