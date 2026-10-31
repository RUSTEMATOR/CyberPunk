"""Screens of the career paths (owner request): fixer, ripper, politician; the fixer's contract to a player.

work -> path (choose a path | the rank and the deeds) -> pathcon (a contract: player, job, plan, split)
"""

from __future__ import annotations

import random

from app.bot import effects_text, texts, views
from app.bot import texts_players as TP
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.rich import inline_button
from app.bot.ui.screen import Button, Screen, pack
from app.bot.ui.style import SEP
from app.content.loader import Content
from app.db import session as db
from app.engine import paths as pe
from app.engine import players as pl
from app.services import game
from app.services import paths as svc
from app.services.game import ActiveCharacter

ICON = {"fixer": "fixer", "ripper": "ripper", "politician": "news", "bd_director": "braindance"}

MSG = {
    pe.R_RANK: "Для этого нужен ранг повыше.",
    pe.R_AP: "ОД кончились. Нажми «Прожить месяц».",
    pe.R_EDDIES: "Не хватает эдди.",
    pe.R_JAIL: "Из камеры этого не сделать.",
    pe.R_GONE: "Этого уже нет.",
    pe.R_SEAT: "Кресло мэра и так твоё.",
    svc.R_SWITCH: "Сменить путь стоит {price} эдди. Столько нет.",
    svc.R_SAME: "Ты уже идёшь этим путём.",
    pl.BLOCK_PENDING: "Этот заказ уже ждёт ответа.",
}


def _msg(content: Content, reason: str | None) -> str:
    return MSG.get(reason or "", reason or "").format(price=views.money(content.balance.paths.switch_price))


def _mods(mods: dict) -> str:
    return effects_text.cap(", ".join(effects_text.mods_phrases(mods))) if mods else ""


def _check(a) -> str:  # noqa: ANN001
    s = texts.STAT_LABELS.get(a.stat, a.stat)
    return s + (f" + {texts.SKILL_LABELS.get(a.skill, a.skill).lower()}" if a.skill else "")


def _req(r, st=None) -> str:  # noqa: ANN001
    """The needs of a rank; with the character, each one says what is there (owner report 24.10: «опыт 112, а ранга
    нет» — the missing fame was not visible)."""
    def one(label: str, need: int, have: int | None) -> str:
        if have is None:
            return f"{label} {need}"
        return f"{label} {need} (есть)" if have >= need else f"{label} {need} (у тебя {have}, не хватает)"

    xp_have = pe.xp(st) if st is not None else None
    parts = [one("опыт", r.xp, xp_have)]
    if r.min_cred:
        parts.append(one("уличная репутация", r.min_cred, st.street_cred if st is not None else None))
    if r.min_fame:
        parts.append(one("слава", r.min_fame, st.fame if st is not None else None))
    if r.seat:
        parts.append("победа на выборах")
    return ", ".join(parts)


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


def choose_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    cur = pe.record(ch.state).get("key")
    lines = [style.header("work", "Свой путь"),
             "Работа платит зарплату. Путь — это то, кем тебя знает улица. Один путь за раз: новый начинается с нуля."]
    if cur:
        lines.append(f"Смена пути стоит {views.money(content.balance.paths.switch_price)} эдди, опыт старого пропадёт.")
    for p in pe.defs(content):
        top = p.ranks[-1]
        lines += ["", style.header(ICON.get(p.key, "work"), texts.esc(p.name)), texts.esc(p.text),
                  f"<i>Ранги: {' → '.join(texts.esc(r.name) for r in p.ranks)}. На вершине: {views.money(top.income)} эдди в месяц.</i>"]
    if note:
        lines += ["", note]
    rows = [[Button(style.label(ICON.get(p.key, "work"), p.name), "pick", p.key)] for p in pe.defs(content) if p.key != cur]
    rows.append([Button(texts.BACK, "back" if cur else "work")])
    return Screen("\n".join(lines), rows, name="path")


async def path_screen(ctx: Ctx, ch: ActiveCharacter, note: str | None = None, times: int | None = None) -> Screen:
    if times is None:
        n_raw = ctx.state.get("n", 1) if isinstance(ctx.state, dict) else 1
        times = int(n_raw) if str(n_raw).isdigit() else 1
    c = ctx.content
    st = ch.state
    p = pe.path_of(st, c)
    if p is None:
        return choose_screen(c, ch, note)
    n = pe.rank(st, c)
    r = p.ranks[n - 1]
    nxt = pe.next_rank(st, c)
    lines = [style.header(ICON.get(p.key, "work"), f"{texts.esc(p.name)}: {texts.esc(r.name)}"),
             f"Ранг {n} из {len(p.ranks)}. Опыт пути: {pe.xp(st)}. Эдди: {views.money(st.eddies)}. ОД: {st.ap}."]
    if r.text:
        lines.append(f"<i>{texts.esc(r.text)}</i>")
    if r.income:
        lines.append(f"Доход: {views.money(r.income)} эдди в месяц.")
    if r.mods:
        lines.append(f"Ранг даёт: {_mods(r.mods)}.")
    if nxt is not None:
        lines.append(f"Следующий ранг «{texts.esc(nxt.name)}»: {_req(nxt, st)}. Ранг растёт сам, когда выполнено всё.")
    if p.key == "politician":
        async with db.session_scope() as s:
            seat = await svc.mayor(s)
        lines.append("Кресло мэра: " + (f"<b>{texts.esc(str(seat.get('name', '')))}</b>." if seat else "свободно."))
    if note:
        lines += ["", SEP, note, SEP]
    if p.key == c.balance.paths.studio.path:
        lines += ["", "<b>Студия</b>", "Брейндансы по книгам Озона: снять, выпустить в прокат, получать отчисления с каждого зрителя. "
                  + inline_button("path", "Студия", "studio", None, "primary")]
    lines += ["", "<b>Дела</b>"]
    rows: list[list[Button]] = []
    for i, a in enumerate(p.actions, 1):
        if a.min_rank > n:
            lines += ["", f"🔒 {texts.esc(a.name)} — с ранга «{texts.esc(p.ranks[a.min_rank - 1].name)}»."]
            continue
        cost = [f"{a.ap} ОД"] + ([f"{views.money(a.price)} эдди"] if a.price else [])
        pay = f", доход {views.money(pe.pay_range(c, a, n)[0])}–{views.money(pe.pay_range(c, a, n)[1])}" if a.pay[1] else ""
        lines += ["", f"<b>{texts.esc(a.name)}</b> ({', '.join(cost)}): {_check(a)}, сложность {a.dv}{pay}.",
                  f"<i>{texts.esc(a.text)}</i> " + inline_button("path", a.name, "do", a.key, "primary")]
    if p.key == "fixer":
        cfg = c.balance.paths.contract
        if n >= cfg.min_rank:
            lines += ["", "<b>Заказ игроку</b>",
                      "Выбираешь человека, дело, план и долю. Если согласится — бросок за ним, "
                      "деньги делятся по договору, опыт фиксера твой. "
                      + inline_button("path", "Заказ игроку", "contract", None, "primary")]
        else:
            lines += ["", f"Заказы игрокам — с ранга «{texts.esc(p.ranks[cfg.min_rank - 1].name)}»."]
    from app.bot.handlers.activities import repeat_note, repeat_row

    if times != 1:
        lines += ["", repeat_note(times)]
    rows.insert(0, repeat_row(times))
    rows.append([Button("Сменить путь", "switch"), Button(texts.BACK, "work")])
    return Screen("\n".join(lines), rows, name="path", state={"n": times})


def result_text(c: Content, p, a, res: svc.ActResult) -> str:  # noqa: ANN001
    lines = [views.check_line(res.check)] if res.check is not None else []  # type: ignore[arg-type]
    lines.append(f"<b>{'Удалось.' if res.ok else 'Не вышло.'}</b> " + texts.esc(a.win_text if res.ok else a.fail_text))
    if res.seat_won:
        lines.append("Кресло мэра твоё." + (f" {texts.esc(res.seat_from)} его теряет." if res.seat_from else ""))
    if res.applied is not None:
        summary = views.effects_summary(res.applied, c)
        if summary:
            lines.append(summary)
    return "\n".join(lines)


@on_screen("path")
async def path_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    if action == "work":
        return await goto(ctx, "work")
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action == "switch":
        return choose_screen(c, ch)
    if action == "pick" and arg:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            why = await svc.choose(s, c, ch, year, ctx.ui.settings.timezone, arg)
        if why is not None:
            return choose_screen(c, ch, _msg(c, why))
        p = pe.path_def(c, arg)
        return await path_screen(ctx, ch, f"Путь выбран: {texts.esc(p.name if p else arg)}. Всё с начала.")
    if action == "n" and arg is not None and arg.isdigit():
        return await path_screen(ctx, ch, None, int(arg))
    if action == "do" and arg:
        n_raw = ctx.state.get("n", 1)
        times = int(n_raw) if str(n_raw).isdigit() else 1
        results: list = []
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            p0 = pe.path_of(ch.state, c)
            a0 = pe.action(c, p0.key, arg) if p0 else None
            want = times if times > 0 else max(1, ch.state.ap // max(1, a0.ap if a0 else 1))
            for _ in range(want):  # owner request: put N AP into one deed
                r = await svc.act(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, arg)
                results.append(r)
                if r.blocked is not None or r.seat_won:
                    break
        res = results[-1]
        ok_runs = [r for r in results if r.blocked is None]
        if not ok_runs:
            return await path_screen(ctx, ch, _msg(c, res.blocked))
        p = pe.path_of(ch.state, c)
        a = pe.action(c, p.key, arg) if p else None
        if len(ok_runs) > 1 and a is not None:
            from app.engine.effects import merge_applied

            merged = merge_applied([r.applied for r in ok_runs if r.applied is not None])
            wins = sum(1 for r in ok_runs if r.ok)
            text = [texts.ACT_MANY_DONE.format(done=len(ok_runs), asked=want) + f" Удачно: {wins}."]
            if res.blocked is not None:
                text.append(f"<i>Остановилось раньше: {_msg(c, res.blocked)}</i>")
            summary = views.effects_summary(merged, c)
            if summary:
                text.append(summary)
            return await path_screen(ctx, ch, "\n".join(text))
        res = ok_runs[-1]
        if res.seat_won:
            from app.services import loud

            who = f"{ch.state.name} {ch.state.surname}".strip()
            ctx.ui.post(f"🏛 В Найт-Сити новый мэр: <b>{texts.esc(who)}</b>." +
                        (f" {texts.esc(res.seat_from)} теряет кресло." if res.seat_from else ""), 12 * 3600)
            async with db.session_scope() as s:
                await loud.post(s, ch.id, f"{who} — новый мэр Найт-Сити.", kind="mayor", w=3)
        return await path_screen(ctx, ch, result_text(c, p, a, res) if a else None)
    if action == "contract":
        return await contract_screen(ctx, {})
    if action == "studio":
        from app.bot.handlers.studio import studio_screen

        return studio_screen(c, ch)
    return await path_screen(ctx, ch)


# -- the fixer's contract to a player -------------------------------------------------------------------------------------


async def contract_screen(ctx: Ctx, state: dict, note: str | None = None) -> Screen:
    c = ctx.content
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    cfg = c.balance.paths.contract
    budgets = c.balance.ui.button_chars
    lines = [style.header("fixer", "Заказ игроку")]
    if note:
        lines += [note, ""]
    if "char" not in state:
        async with db.session_scope() as s:
            people = await svc.candidates(s, c, ch, ctx.ui.settings.timezone)
        if not people:
            lines.append("В городе сейчас нет других живых.")
            return Screen("\n".join(lines), [[Button(texts.BACK, "back")]], name="pathcon", state=state)
        lines.append("Кому отдать заказ?")
        lines += [f"{i}. {texts.esc(o.name)}" for i, o in enumerate(people, 1)]
        rows = pack([Button(str(i), "who", str(o.char_id)) for i, o in enumerate(people, 1)], budgets,
                    max_per_row=5)
        rows.append([Button(texts.BACK, "back")])
        return Screen("\n".join(lines), rows, name="pathcon", state=state)
    if "name" not in state:
        async with db.session_scope() as s:
            o = await svc.psvc.other(s, c, ch, int(state["char"]), ctx.ui.settings.timezone)
        if o is None:
            return await contract_screen(ctx, {}, TP.BLOCKED["gone"])
        state["name"] = o.name
    lines.append(f"Исполнитель: <b>{texts.esc(str(state.get('name', '')))}</b>.")
    if "job" not in state:
        jobs = pe.contract_jobs(ch.state, c)
        lines.append("Какое дело?")
        lines += [f"{i}. <b>{texts.esc(j.name)}</b>: куш {views.money(j.pool)}, сложность {j.dv}.\n<i>{texts.esc(j.text)}</i>"
                  for i, j in enumerate(jobs, 1)]
        rows = pack([Button(str(i), "job", j.key) for i, j in enumerate(jobs, 1)], budgets, max_per_row=5)
        rows.append([Button(texts.BACK, "back")])
        return Screen("\n".join(lines), rows, name="pathcon", state=state)
    job = pe.contract_job(c, str(state["job"]))
    if job is None:
        return await contract_screen(ctx, {k: v for k, v in state.items() if k != "job"})
    lines.append(f"Дело: <b>{texts.esc(job.name)}</b>, куш {views.money(job.pool)}, сложность {job.dv}.")
    if "plan" not in state:
        bonus = pe.plan_bonus(c, pe.rank(ch.state, c))
        lines += [f"Какой план? Исполнитель идёт по нему и получает +{bonus} к броску.", TP.JOB_APPROACH_HELP]
        rows = pack([Button(TP.JOB_APPROACHES[a], "plan", a) for a in pl.JOB_APPROACHES], budgets, max_per_row=2)
        rows.append([Button(texts.BACK, "back")])
        return Screen("\n".join(lines), rows, name="pathcon", state=state)
    lines.append(f"План: <b>{TP.JOB_APPROACHES.get(str(state['plan']), '?')}</b>.")
    lines.append("Какую долю берёшь себе? Чем жаднее, тем вероятнее отказ.")
    for share in cfg.shares:
        mine, theirs = pe.split(job.pool, share)
        lines.append(f"{share}%: тебе {views.money(mine)}, исполнителю {views.money(theirs)}.")
    rows = pack([Button(f"{share}%", "share", str(share)) for share in cfg.shares], budgets, max_per_row=3)
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="pathcon", state=state)


@on_screen("pathcon")
async def pathcon_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    state = dict(ctx.state or {})
    if action == "back":
        ch, _ = await _load(ctx)
        return await path_screen(ctx, ch) if ch is not None else await menu_screen(ctx)
    if action == "who" and arg and arg.isdigit():
        return await contract_screen(ctx, {"char": int(arg)})
    if action == "job" and arg:
        state["job"] = arg
        return await contract_screen(ctx, state)
    if action == "plan" and arg in pl.JOB_APPROACHES:
        state["plan"] = arg
        return await contract_screen(ctx, state)
    if action == "share" and arg and arg.isdigit() and {"char", "job", "plan"} <= set(state):
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            why, target = await svc.send_contract(s, c, ch, year, ctx.ui.settings.timezone, int(state["char"]),
                                                  str(state["job"]), str(state["plan"]), int(arg))
        if why is not None:
            return await path_screen(ctx, ch, _msg(c, why))
        if target is not None:
            from app.bot.handlers.players import _ping_target

            await _ping_target(ctx, target.tg_id, "offer")
        return await path_screen(ctx, ch, f"Заказ ушёл: {texts.esc(target.name if target else '')} увидит его, "
                                          "когда откроет окно. Ответ придёт письмом.")
    return await contract_screen(ctx, state)
