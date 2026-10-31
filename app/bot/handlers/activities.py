"""The activities section in the window (PLAN 4.5).

activities (the list) -> [actskill: pick a skill for training / study | actstake: pick a stake]
-> actout (what happened) or gamblecheck (a missed roll: luck / glitch / accept) -> back to the list.
Each phase has its own screen name, so an old button of a replaced screen is ignored.
"""

from __future__ import annotations

import random

from app.bot import texts, texts_actions, texts_life, views
from app.bot.handlers.menu import menu_screen
from app.bot.handlers.week import death_screen
from aiogram.enums import DiceEmoji

from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, has_screen, on_screen
from app.bot.ui.screen import Button, Screen, pack
from app.content.loader import Content
from app.content.schemas import SKILLS
from app.db import session as db
from app.engine import activities as act
from app.engine import checks
from app.engine.state import FLAG_JAIL_WEEKS
from app.engine import events as engine_events
from app.engine.checks import CheckResult
from app.engine.dice import D10, TG_SLOTS_FACES, d10_from_face
from app.services import activities as act_service
from app.services import events as ev_service
from app.services import game
from app.services.game import ActiveCharacter


ACT_ICONS: dict[str, str] = {  # activity -> icon key (app/bot/ui/style.py)
    act.TRAIN: "train", act.STUDY: "study", act.BAR: "afterlife", act.BRAINDANCE: "braindance",
    act.THERAPY: "therapy", act.REST: "rest", act.GAMBLE: "slots", act.MEDITATION: "meditation",
    act.MARTIAL_ARTS: "martial_arts", act.SERVICE: "service",
}


def _price(content: Content, key: str, state=None) -> str:  # noqa: ANN001
    eddies = act.eddy_cost(content, key, state)
    return texts.ACT_PRICE.format(eddies=views.money(eddies)) if eddies else ""


def _desc(content: Content, key: str) -> str:
    """The 'what it gives' line under an activity, numbers taken from the balance."""
    a = content.balance.activities
    cfg = {
        act.TRAIN: a.training, act.STUDY: a.study, act.BAR: a.bar, act.BRAINDANCE: a.braindance,
        act.THERAPY: a.therapy, act.REST: a.rest, act.GAMBLE: a.gamble, act.MEDITATION: a.meditation,
        act.MARTIAL_ARTS: a.martial_arts, act.SERVICE: a.service,
    }[key]
    fields = {k: getattr(cfg, k) for k in ("xp", "hp", "fame", "eddies") if hasattr(cfg, k)}
    if hasattr(cfg, "mood"):
        fields["mood"] = style.signed(cfg.mood) if key in (act.TRAIN, act.STUDY) else cfg.mood
    if hasattr(cfg, "humanity_dice"):
        fields["dice"] = str(cfg.humanity_dice)
    return texts.ACT_DESC[key].format(**fields)


REPEATS: tuple[int, ...] = (1, 3, 6, 0)  # owner request: put N AP into one activity at once; 0 = all the AP there is
NO_REPEAT: frozenset[str] = frozenset({act.GAMBLE})


def repeat_row(times: int, action: str = "n") -> list[Button]:
    return [Button(("• " if t == times else "") + (f"×{t}" if t else "Все ОД"), action, str(t)) for t in REPEATS]


def repeat_note(times: int) -> str:
    return texts.ACT_REPEAT_NOTE.format(n=f"{times} раз" if times else "на все ОД") if times != 1 else ""


def times_of(st, content: Content, activity: str, times: int) -> int:  # noqa: ANN001
    """How many sessions to try: `times`, or all the AP buys (0)."""
    if times > 0:
        return times
    return max(1, st.ap // max(1, act.ap_cost(content, activity)))


def activities_screen(content: Content, ch: ActiveCharacter, note: str | None = None, times: int = 1) -> Screen:
    st = ch.state
    head = [
        texts.ACT_TITLE,
        texts.ACT_STATUS.format(eddies=views.money(st.eddies), ap=st.ap, mood=st.mood),
    ]
    if int(st.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0:
        head.append(texts.ACT_JAIL_NOTE)
    if note:
        head += ["", note]
    chars = content.balance.ui.button_chars

    def line(key: str) -> str:
        head = style.line(ACT_ICONS.get(key, "activities"), texts.ACT_LINE.format(
            name=f"<b>{texts.ACT_LABELS[key]}</b>", ap=act.ap_cost(content, key), price=_price(content, key, st)))
        return f"{head}\n    <i>{_desc(content, key)}</i>"

    head.append("")
    main: list[Button] = []
    for key in act.ORDER:
        if key in act.PATH_ACTIVITIES:
            continue
        entry = line(key)
        if key == act.BRAINDANCE and act.vows_against(st, content, "braindance"):
            entry += f" ({texts.ACT_VOW_WARN})"
        head.append(entry)
        main.append(Button(texts.ACT_SHORT.get(key, texts.ACT_LABELS[key]), "a", key))
    rows = [repeat_row(times)] + pack(main, chars, max_per_row=2)
    if times != 1:
        head.append(repeat_note(times))
    head.append("")
    head.append(texts.ACT_PATH_HEAD)
    path: list[Button] = []
    for key in act.PATH_ACTIVITIES:
        head.append(line(key))
        path.append(Button(texts.ACT_SHORT.get(key, texts.ACT_LABELS[key]), "a", key))
    rows += pack(path, chars, max_per_row=2)
    if has_screen("acts"):
        rows.append([Button(texts_actions.BTN_LEISURE, "fun"), Button("Рыбалка", "fish")])
    rows.append([Button("Спортзал", "gym")])
    if has_screen("premieres"):
        rows.append([Button("Премьеры брейндансов", "premieres")])
    if has_screen("protege"):
        rows.append([Button(texts_life.PRO_BUTTON, "protege")])
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(head), rows, name="activities", state={"n": times})


def skill_screen(content: Content, ch: ActiveCharacter, study: bool, times: int = 1) -> Screen:
    cfg = content.balance.activities.study if study else content.balance.activities.training
    kind = "study" if study else "train"
    skills = act.trainable(content, study)
    text = texts.ACT_SKILL_TITLE.format(
        title=texts.ACT_LABELS[kind], what=texts.ACT_SKILL_WHAT[kind],
        eddies=views.money(act.eddy_cost(content, act.STUDY if study else act.TRAIN, ch.state)),
        ap=cfg.ap_cost, xp=cfg.xp, mood=style.signed(cfg.mood),
    )
    text += "\n\n" + "\n".join(
        views.skill_progress(ch.state, content, s, act.sessions_to_level(ch.state, content, s, cfg.xp))
        for s in skills
    )
    if len(skills) < len(SKILLS):
        text += f"\n\n<i>{texts.ACT_SKILL_OTHER[kind]}</i>"
    if times != 1:
        text += "\n\n" + repeat_note(times)
    buttons = [Button(texts.SKILL_LABELS[s], "s", s) for s in skills]
    rows = [repeat_row(times)] + pack(buttons, content.balance.ui.button_chars, max_per_row=2)
    rows.append([Button(texts.BACK, "back")])
    return Screen(text, rows, name="actskill", state={"study": study, "n": times})


def stake_screen(content: Content) -> Screen:
    cfg = content.balance.activities.gamble
    text = texts.ACT_GAMBLE_TITLE.format(
        stat=texts.STAT_LABELS.get(cfg.check_stat, cfg.check_stat), dv=cfg.check_dv, mult=cfg.win_mult,
    ).replace("x2.0", "x2")
    rows = [[Button(texts.ACT_GAMBLE_STAKE.format(stake=views.money(k)), "k", str(k))] for k in cfg.stakes]
    rows.append([Button(texts.BACK, "back")])
    return Screen(text, rows, name="actstake")


def slots_line(face: int | None, check: CheckResult | None) -> list[str]:
    """'🎰 Автомат выдал 37 из 64.' when the roll came from the slot machine (the check line shows the die)."""
    if face is None or check is None:
        return []
    return [texts.ACT_GAMBLE_SLOTS.format(face=face, faces=TG_SLOTS_FACES)]


def gamble_check_screen(content: Content, ch: ActiveCharacter, face: int | None = None) -> Screen:
    """A rolled gamble waiting for the decision (also the resume of a closed window)."""
    p = act_service.pending_gamble(ch)
    assert p is not None
    check = act_service.check_of(p)
    rev = int(p["rev"])
    st = ch.state
    lines = [texts.ACT_GAMBLE_CHECK_HEAD.format(stake=views.money(int(p["stake"]))), "",
             *slots_line(face, check), views.check_line(check)]
    rows: list[list[Button]] = []
    if check.ok:
        lines.append(texts.CHECK_PASS_LUCK)
        rows.append([Button(texts.EVENT_CONTINUE, "take", str(rev))])
    else:
        offer = engine_events.luck_offer(check, st, content)
        can_glitch = checks.can_glitch_reroll(check, st.glitches)
        lines.append(texts.CHECK_FAIL if (offer or can_glitch) else texts.CHECK_FAIL_NOTHING)
        if offer:
            rows.append([Button(texts.EVENT_LUCK.format(points=offer.points, pool=offer.pool), "luck", str(rev))])
        if can_glitch:
            rows.append([Button(texts.EVENT_GLITCH.format(glitches=st.glitches), "glitch", str(rev))])
        rows.append([Button(texts.EVENT_ACCEPT_FAIL, "take", str(rev))])
    return Screen("\n".join(lines), rows, name="gamblecheck", state={"rev": rev})


def result_screen(
    content: Content, ch: ActiveCharacter, res: act_service.ActivityResult, face: int | None = None,
) -> Screen:
    """What an activity did: the line, the check, the real changes, the followups."""
    if res.died:
        lines = [texts.ACT_OVERDOSE] if res.plan is not None and res.plan.overdose else []
        return death_screen(ch.state.name, res.death_cause, lines)
    lines = [style.header(ACT_ICONS.get(res.activity, "activities"), texts.ACT_LABELS[res.activity])]
    if res.text:
        lines.append(style.quote(texts.esc(res.text)))
    lines += slots_line(face, res.check)
    if res.check is not None:
        lines.append(views.check_line(res.check))
    if res.activity == act.GAMBLE:
        lines.append(
            texts.ACT_GAMBLE_WIN.format(payout=views.money(res.payout)) if res.won
            else texts.ACT_GAMBLE_LOSE.format(stake=views.money(res.stake))
        )
    if res.applied is not None:
        if res.activity == act.THERAPY:
            lines.append(texts.ACT_THERAPY_LINE.format(n=res.applied.deltas.get("humanity", 0)))
        summary = views.effects_summary(res.applied, content)
        if summary:
            lines.append(summary)
        skill = res.plan.skill if res.plan is not None else None
        if skill:  # where the trained skill stands now, and how many more sessions the next level takes
            gain = _xp_of(content, res.activity)
            lines.append(texts.ACT_SKILL_AFTER.format(line=views.skill_progress(
                ch.state, content, skill, act.sessions_to_level(ch.state, content, skill, gain))))
        if res.applied.npcs_added:
            lines.append(texts.ACT_PARTNER)
    if res.plan is not None and res.plan.addiction is not None:
        lines.append(texts.ACT_ADDICTION.format(n=res.plan.addiction))
    lines.extend(res.lines)
    rows: list[list[Button]] = []
    if res.queued_event is not None:
        lines.append(texts.ACT_QUEUED_ADDICTION if res.queued_event == act_service.ADDICTION_EVENT else texts.ACT_QUEUED_BAR)
        rows.append([Button(texts.EVENT_CONTINUE, "event")])
    else:
        rows.append([Button(texts.ACT_BACK_TO_LIST, "list"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="actout")


def _xp_of(content: Content, activity: str) -> int:
    a = content.balance.activities
    return {act.TRAIN: a.training.xp, act.STUDY: a.study.xp, act.MARTIAL_ARTS: a.martial_arts.xp}.get(activity, 0)


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


def _blocked_note(reason: str) -> str:
    return texts.ACT_BLOCKED.get(reason, reason)


async def _list(ctx: Ctx, note: str | None = None) -> Screen:
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    n = ctx.state.get("n", 1) if isinstance(ctx.state, dict) else 1
    return activities_screen(ctx.content, ch, note, times=int(n) if str(n).isdigit() and int(n) in REPEATS else 1)


@on_screen("activities")
async def activities_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await menu_screen(ctx)
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if ev_service.has_pending(ch) and has_screen("evchoice"):
        return await goto(ctx, "evchoice", "resume")
    content = ctx.content
    if action == "protege" and has_screen("protege"):
        return await goto(ctx, "protege")
    if action == "fun" and has_screen("acts"):
        return await goto(ctx, "acts", "open", "leisure")
    if action == "premieres" and has_screen("premieres"):
        from app.bot.handlers.studio import premieres_screen

        return await premieres_screen(ctx, back="activities")
    if action == "gym":
        return gym_screen(content, ch)
    if action == "fish" and has_screen("fishing"):
        return await goto(ctx, "fishing", "open")
    times = int(ctx.state.get("n", 1) or 0) if str(ctx.state.get("n", 1)).isdigit() else 1
    if action == "n" and arg is not None and arg.isdigit() and int(arg) in REPEATS:
        return activities_screen(content, ch, times=int(arg))
    if action != "a" or arg not in act.ORDER:
        return activities_screen(content, ch, times=times)
    if arg in (act.TRAIN, act.STUDY):
        reason = act.blocked(ch.state, content, arg)
        if reason is not None:
            return activities_screen(content, ch, _blocked_note(reason), times=times)
        return skill_screen(content, ch, arg == act.STUDY, times)
    if times != 1 and arg not in NO_REPEAT:
        return await _perform_many(ctx, arg, None, times)
    if arg == act.GAMBLE:
        reason = act.blocked(ch.state, content, arg)
        if reason is not None:
            return activities_screen(content, ch, _blocked_note(reason))
        return stake_screen(content)
    return await _perform(ctx, arg, None)


async def _perform(ctx: Ctx, activity: str, skill: str | None) -> Screen:
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        res = await act_service.perform(s, ctx.content, ch, rng, year, tz, activity, skill=skill)
    if res.blocked is not None:
        return activities_screen(ctx.content, ch, _blocked_note(res.blocked))
    if res.no_ap:
        return activities_screen(ctx.content, ch, texts.ACT_NO_AP)
    scr = result_screen(ctx.content, ch, res)
    if not res.late or res.died:
        return scr

    async def job() -> Screen | None:  # owner request: Claude's line replaces the authored one when it comes
        text = await act_service.late_text(res)
        if not text:
            return None
        res.text = text
        return result_screen(ctx.content, ch, res)

    from app.bot.ui import late

    return late.attach(ctx, scr, job)


@on_screen("actskill")
async def actskill_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    study = bool(ctx.state.get("study"))
    times = int(ctx.state.get("n", 1)) if str(ctx.state.get("n", 1)).isdigit() else 1
    if action == "n" and arg is not None and arg.isdigit() and int(arg) in REPEATS:
        ch, _ = await _load(ctx)
        if ch is None:
            return await menu_screen(ctx)
        return skill_screen(ctx.content, ch, study, int(arg))
    if action == "s" and arg in SKILLS:
        if times != 1:
            return await _perform_many(ctx, act.STUDY if study else act.TRAIN, arg, times)
        return await _perform(ctx, act.STUDY if study else act.TRAIN, arg)
    return await _list(ctx)


async def _perform_many(ctx: Ctx, activity: str, skill: str | None, times: int) -> Screen:
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        n = times_of(ch.state, ctx.content, activity, times)
        res = await act_service.perform_many(s, ctx.content, ch, rng, year, tz, activity, n, skill=skill)
    if res.done == 0:
        if res.last.blocked is not None:
            return activities_screen(ctx.content, ch, _blocked_note(res.last.blocked), times=times)
        return activities_screen(ctx.content, ch, texts.ACT_NO_AP, times=times)
    return many_screen(ctx.content, ch, res, times)


def many_screen(content: Content, ch: ActiveCharacter, res: act_service.ManyResult, times: int) -> Screen:
    """The sum of many sessions: how many were done, the real changes, where the skill stands now."""
    last = res.last
    if last.died:
        lines = [texts.ACT_OVERDOSE] if last.plan is not None and last.plan.overdose else []
        return death_screen(ch.state.name, last.death_cause, lines)
    lines = [style.header(ACT_ICONS.get(last.activity, "activities"), texts.ACT_LABELS[last.activity]),
             texts.ACT_MANY_DONE.format(done=res.done, asked=res.asked)]
    if last.text:
        lines.append(style.quote(texts.esc(last.text)))
    if res.stopped and res.stopped not in ("event",):
        lines.append(texts.ACT_MANY_STOPPED.format(why=texts.ACT_NO_AP if res.stopped == "no_ap" else _blocked_note(res.stopped)))
    if res.applied is not None:
        summary = views.effects_summary(res.applied, content)
        if summary:
            lines.append(summary)
    skill = last.plan.skill if last.plan is not None else None
    if skill:
        gain = _xp_of(content, last.activity)
        lines.append(texts.ACT_SKILL_AFTER.format(line=views.skill_progress(
            ch.state, content, skill, act.sessions_to_level(ch.state, content, skill, gain))))
    lines.extend(last.lines)
    rows: list[list[Button]] = []
    if last.queued_event is not None:
        lines.append(texts.ACT_QUEUED_ADDICTION if last.queued_event == act_service.ADDICTION_EVENT else texts.ACT_QUEUED_BAR)
        rows.append([Button(texts.EVENT_CONTINUE, "event")])
    else:
        rows.append([Button(texts.ACT_BACK_TO_LIST, "list"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="actout", state={"n": times})


async def spin(ctx: Ctx) -> tuple[int | None, D10 | None]:
    """Spin the slot machine in the chat: (face, the check die it stands for). (None, None) when the dice could
    not be sent; the service then rolls with its rng. Waits for the animation before the result is shown."""
    face = await ctx.ui.roll_dice(ctx.chat_id, DiceEmoji.SLOT_MACHINE.value)
    if face is None:
        return None, None
    await ctx.ui.reveal()
    return face, d10_from_face(face, TG_SLOTS_FACES, ctx.content.balance.checks)


@on_screen("actstake")
async def actstake_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action != "k" or arg is None or not arg.isdigit():
        return await _list(ctx)
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    stake = int(arg)
    ch, _ = await _load(ctx)
    face, die = await spin(ctx) if ch is not None and act_service.gamble_can_start(ctx.content, ch, stake) else (None, None)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        got = await act_service.start_gamble(s, ctx.content, ch, rng, year, tz, stake, die)
    if isinstance(got, CheckResult):
        return gamble_check_screen(ctx.content, ch, face)
    if got.blocked is not None:
        return activities_screen(ctx.content, ch, _blocked_note(got.blocked))
    if got.no_ap:
        return activities_screen(ctx.content, ch, texts.ACT_NO_AP)
    return result_screen(ctx.content, ch, got, face)


@on_screen("gamblecheck")
async def gamblecheck_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    if arg is None or not arg.lstrip("-").isdigit():
        return await _list(ctx)
    rev = int(arg)
    face, die = None, None
    if action == "glitch":  # the reroll spins the slot machine again
        ch, _ = await _load(ctx)
        if ch is not None and act_service.gamble_can_reroll(ch, rev):
            face, die = await spin(ctx)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        try:
            if action == "luck":
                await act_service.gamble_luck(s, ctx.content, ch, rng, year, tz, rev)
                return gamble_check_screen(ctx.content, ch)
            if action == "glitch":
                await act_service.gamble_glitch(s, ctx.content, ch, rng, year, tz, rev, die)
                return gamble_check_screen(ctx.content, ch, face)
            if action == "take":
                res = await act_service.gamble_accept(s, ctx.content, ch, rng, year, tz, rev)
                return result_screen(ctx.content, ch, res)
        except LookupError:
            pass
    return await _list(ctx)


@on_screen("actout")
async def actout_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "event" and has_screen("evchoice"):
        return await goto(ctx, "evchoice", "resume")
    return await _list(ctx)


# -- the gym (owner request 23.10: health grows with sport) ----------------------------------------------------------


def gym_screen(content, ch, note: str | None = None) -> Screen:  # noqa: ANN001
    from app.services import gym

    g = content.balance.gym
    st = ch.state
    pts = gym.points(ch)
    hp = pts // g.hp_per_points
    body = sum(1 for x in g.body_at if pts >= x)
    nxt = next((x for x in g.body_at if pts < x), None)
    lines = [style.header("activities", "Спортзал"), f"Эдди: {views.money(st.eddies)} · ОД: {st.ap}", ""]
    if note:
        lines += [note, ""]
    lines += [f"Форма: <b>{pts}/{g.cap}</b>. Сейчас: здоровье +{hp}" + (f", ТЕЛО +{body}" if body else "") + ".",
              f"Каждые {g.hp_per_points} очка формы — +1 к здоровью. ТЕЛО +1 на {', '.join(map(str, g.body_at))} очках: "
              "больше здоровья, и хром ложится легче."
              + (f" До следующего ТЕЛА: {nxt - pts}." if nxt else ""),
              "",
              f"Тренировка: {views.money(g.price)}, {g.ap} ОД, +1 очко, ещё +1 при проверке ТЕЛА против {g.dv}.",
              f"С тренером: {views.money(g.coach_price)}, {g.ap} ОД, сразу +{g.coach_points}."]
    rows = [[Button(f"Тренировка · {views.money(g.price)}", "go")], [Button(f"С тренером · {views.money(g.coach_price)}", "coach")],
            [Button(texts.BACK, "back"), Button(texts.MENU, "menu")]]
    return Screen("\n".join(lines), rows, name="gym")


GYM_BLOCKED = {"jail": "В камере только отжимания. Спортзал — после срока.", "cap": "Форма на пределе.",
               "eddies": "Не хватает эдди.", "ap": "ОД кончились. Нажми «Прожить месяц»."}


@on_screen("gym")
async def gym_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.services import gym

    if action == "menu":
        return await menu_screen(ctx)
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action == "back":
        return activities_screen(ctx.content, ch)
    if action not in ("go", "coach"):
        return gym_screen(ctx.content, ch)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        r = await gym.train(s, ctx.content, ch, random.Random(), year, ctx.ui.settings.timezone, coach=action == "coach")
    if r.blocked:
        return gym_screen(ctx.content, ch, GYM_BLOCKED.get(r.blocked, r.blocked))
    parts = [views.check_line(r.check)] if r.check is not None else []  # type: ignore[arg-type]
    parts.append(f"Пот, железо, музыка из колонки. Форма +{r.gained}.")
    if r.applied is not None:
        sm = views.effects_summary(r.applied, ctx.content)
        if sm:
            parts.append(sm)
    return gym_screen(ctx.content, ch, "\n".join(parts))
