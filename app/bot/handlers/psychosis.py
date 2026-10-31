"""Cyberpsychosis in the window (PLAN 6.6a, 6.6b): the capture verdict, the fugitive's menu, the MaxTac duty call.

pcap (MaxTac has you: a contract and the ward, or the verdict) -> pcapdone
fugitive (the reduced menu of a hunted psycho) -> [give up: confirm] -> pcap
Closing the window loses nothing: the verdict and the call live on the character (`FLAG_PSYCHO_CAPTURE`, `FLAG_MAXTAC_DUTY`)
and `evchoice:resume` rebuilds the screen.
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot import texts_chrome as T
from app.bot.handlers.menu import menu_screen
from app.bot.handlers.week import death_screen
from app.bot.ui.callbacks import Ctx, goto, has_screen, on_screen, on_text
from app.bot.ui.style import header
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import derived
from app.engine import psychosis as eng
from app.engine.state import FLAG_FUGITIVE, FLAG_MAXTAC_DUTY, FLAG_PSYCHO_CAPTURE
from app.services import game
from app.services import psychosis as svc
from app.services import psylegend as legend_svc
from app.services.game import ActiveCharacter



# -- the verdict ---------------------------------------------------------------------------------------------------------


def capture_screen(content: Content, ch: ActiveCharacter) -> Screen:
    cap = svc.capture_of(ch)
    assert cap is not None
    cfg = content.balance.cyberpsychosis
    by = [T.PSY_CAP_BY.format(name=texts.esc(str(cap["by"]))), ""] if cap.get("by") else []
    if cap.get("recruited"):
        weeks = content.careers["maxtac"].service_weeks or 0
        lines = [T.PSY_CAP_TITLE, "", *by, T.PSY_CAP_OFFER, "",
                 T.PSY_CAP_CONTRACT_INFO.format(weeks=weeks),
                 T.PSY_CAP_WARD_INFO.format(lo=cfg.isolation_weeks.min, hi=cfg.isolation_weeks.max)]
        rows = [[Button(T.PSY_CAP_BTN_CONTRACT, "contract")], [Button(T.PSY_CAP_BTN_WARD, "ward")]]
    else:
        lines = [T.PSY_CAP_TITLE, "", *by, T.PSY_CAP_NOOFFER]
        rows = [[Button(T.PSY_CAP_BTN_VERDICT, "verdict")]]
    return Screen("\n".join(lines), rows, name="pcap")


def capture_done_screen(content: Content, ch: ActiveCharacter, res: svc.CaptureResult) -> Screen:
    if res.died:
        return death_screen(ch.state.name, res.cause, res.lines)
    lines: list[str] = []
    if res.kind == "contract":
        lines += res.lines
        cfg = content.balance.cyberpsychosis
        lines.append(T.PSY_DUTY_INFO.format(weeks=cfg.duty_weeks))
    else:
        lines += list(res.lines)
        lines.append(T.PSY_WARD_DONE)
        if res.stripped:
            lines.append(T.PSY_WARD_STRIPPED.format(n=len(res.stripped)))
    return Screen("\n".join(lines), [[Button(T.PSY_WARD_CONTINUE, "next")]], name="pcapdone")


@on_screen("pcap")
async def pcap_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if svc.capture_of(ch) is None:
            return await menu_screen(ctx)
        if action not in ("contract", "ward", "verdict"):
            return capture_screen(ctx.content, ch)
        year = await game.current_year(s, ctx.content)
        try:
            res = await svc.resolve_capture(
                s, ctx.content, ch, random.Random(), year, tz, "contract" if action == "contract" else "isolation")
        except LookupError:
            return capture_screen(ctx.content, ch)
    return capture_done_screen(ctx.content, ch, res)


@on_screen("pcapdone")
async def pcapdone_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    return await menu_screen(ctx)


# -- the fugitive's menu --------------------------------------------------------------------------------------------------


def fugitive_screen(content: Content, ch: ActiveCharacter, note: str | None = None, confirm: bool = False) -> Screen:
    st = ch.state
    lines = [T.FG_TITLE, T.FG_STATUS.format(
        eddies=views.money(st.eddies), ap=st.ap, hp=st.hp, hpmax=derived.max_hp(st, content), humanity=st.humanity),
        T.FG_INTRO]
    if note:
        lines += ["", note]
    if confirm:
        lines += ["", T.FG_GIVE_UP_ASK]
        rows = [[Button(T.FG_GIVE_UP_YES, "giveup_yes"), Button(T.FG_GIVE_UP_NO, "back")]]
        return Screen("\n".join(lines), rows, name="fugitive")
    n = legend_count(st)
    lines += ["", f"🔥 Легенда Найт-Сити: {n}/{len(LEGEND_DEEDS)} · {legend_title(n)}"]
    rows = [
        [Button("Легенда Найт-Сити", "legend", style="danger")],
        [Button(T.FG_BTN_PROFILE, "profile"), Button(T.FG_BTN_RIPPER, "ripper")],
        [Button(T.FG_BTN_TRAIN, "train"), Button(T.FG_BTN_CONTACTS, "contacts")],
        [Button(T.FG_BTN_CITY, "city"), Button(T.FG_BTN_WEEK, "week")],
        [Button("Рейд города", "raid")],
        [Button(T.FG_BTN_GIVE_UP, "giveup")],
    ]
    return Screen("\n".join(lines), rows, name="fugitive")


# -- the way into legend (owner request 24.10) ------------------------------------------------------------------------

LEGEND_DEEDS = legend_svc.DEEDS


def legend_count(st) -> int:  # noqa: ANN001
    return legend_svc.count(st)


def legend_title(n: int) -> str:
    return legend_svc.title(n)


def legend_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    n = legend_count(st)
    lines = [header("danger", "Легенда Найт-Сити"), f"ОД: {st.ap} · эдди: {views.money(st.eddies)}",
             f"Дел легенды: {n}/{len(LEGEND_DEEDS)} · {legend_title(n)}", ""]
    if note:
        lines += [note, ""]
    lines.append("<i>Город уже считает тебя монстром. Можно бегать от МаксТака, а можно сделать так, чтобы имя "
                 "запомнили. Каждое дело — один раз за жизнь. О каждом сделанном деле в беседе выходит отдельный "
                 "пост, и он не удаляется.</i>")
    sign = st.flags.get(legend_svc.FLAG_SIGN)
    lines.append(f"Подпись на местах дел: «{texts.esc(str(sign))}»" if sign else
                 "Подписи нет: можно оставить свою фразу на месте каждого дела.")
    lines.append("")
    rows: list[list[Button]] = []
    for key, (btn, line, ap) in LEGEND_DEEDS.items():
        mark = "✅" if legend_svc.done(st, key) else ("▶" if legend_svc.is_open(st, key) else "🔒")
        lines.append(f"{mark} <b>{texts.esc(btn)}</b> · {ap} ОД — {texts.esc(line)}")
        if legend_svc.is_open(st, key):
            label = f"{btn} · {ap} ОД"
            rows.append([Button(label if len(label) <= 24 else btn[:24], "do", key)])
    rows.append([Button("Подпись легенды", "sign")])
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="psylegend")


def _words_screen(key: str) -> Screen:
    prompt, _ = legend_svc.WORDS[key]
    btn = LEGEND_DEEDS[key][0]
    return Screen(f"{header('danger', btn)}\n{prompt}", [[Button("Без слов", "go", key)], [Button(texts.BACK, "open")]],
                  awaiting_text="psyleg_words", name="psylegend", state={"deed": key})


async def _start(ctx: Ctx, key: str, words: str | None = None) -> Screen | None:
    from app.services import ap as ap_services
    from app.services.week import pending_events, set_pending

    c = ctx.content
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if not ch.state.flags.get(FLAG_FUGITIVE):
            return await menu_screen(ctx)
        if not legend_svc.is_open(ch.state, key):
            return legend_screen(c, ch, "Это дело закрыто или ещё не открыто.")
        ap = LEGEND_DEEDS[key][2]
        if not ap_services.can_spend(ch, ap):
            return legend_screen(c, ch, "ОД кончились. Нажми «Прожить месяц».")
        ap_services.spend(ch, c, ap)
        year = await game.current_year(s, c)
        said = dict(ch.state.flags.get(legend_svc.FLAG_SAY) or {})
        if words:
            said[key] = words
        else:
            said.pop(key, None)
        if said:
            ch.state.flags[legend_svc.FLAG_SAY] = said
        else:
            ch.state.flags.pop(legend_svc.FLAG_SAY, None)
        if key == "psyleg_hunters":  # a fight with the squad that hunts you, straight away
            from app.bot.handlers.combat import step_screen
            from app.engine.effects import apply_effects
            from app.services import combat as combat_svc

            ch.state.flags[key] = True
            apply_effects(ch.state, {"fame": 6, "street_cred": 6}, c, random.Random(), year)
            await legend_svc.announce(s, ch, key, "Засада на охотников")
            step = await combat_svc.begin(s, c, ch, random.Random(), year, tz, faction="maxtac", danger=5,
                                          src={"src": "event"})
            return step_screen(c, ch, step)
        set_pending(ch, [key] + [e for e in pending_events(ch) if e != key])
        await game.save_active(s, ch, tz)
    return await goto(ctx, "evchoice", "resume")


@on_screen("psylegend")
async def psylegend_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    if action == "sign":
        return Screen(f"{header('danger', 'Подпись легенды')}\nФраза, которую ты оставляешь на месте каждого дела "
                      f"(до {legend_svc.SIGN_MAX} символов). Она будет в каждом посте о твоих делах. Напиши в чат.",
                      [[Button("Убрать подпись", "unsign")], [Button(texts.BACK, "open")]],
                      awaiting_text="psyleg_sign", name="psylegend")
    if action == "unsign":
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            ch.state.flags.pop(legend_svc.FLAG_SIGN, None)
            await game.save_active(s, ch, ctx.ui.settings.timezone)
        return legend_screen(c, ch, "Подпись убрана.")
    if action == "go" and arg in LEGEND_DEEDS:
        return await _start(ctx, arg)
    if action == "do" and arg in LEGEND_DEEDS:
        if arg in legend_svc.WORDS:
            return _words_screen(arg)
        return await _start(ctx, arg)
    if action == "open":
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
        if ch is not None and ch.state.flags.get(FLAG_FUGITIVE):
            return legend_screen(c, ch)
    return await goto(ctx, "fugitive")


@on_text("psyleg_words")
async def psyleg_words_text(ctx: Ctx, text: str) -> Screen | None:
    key = str((ctx.state or {}).get("deed", ""))
    if key not in legend_svc.WORDS:
        return await goto(ctx, "fugitive")
    words = legend_svc.clean(text, legend_svc.WORDS[key][1])
    if not words:
        return _words_screen(key)
    return await _start(ctx, key, words)


@on_text("psyleg_sign")
async def psyleg_sign_text(ctx: Ctx, text: str) -> Screen | None:
    sign = legend_svc.clean(text, legend_svc.SIGN_MAX)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if sign:
            ch.state.flags[legend_svc.FLAG_SIGN] = sign
            await game.save_active(s, ch, ctx.ui.settings.timezone)
    return legend_screen(ctx.content, ch, "Подпись сохранена." if sign else None)


@on_screen("fugitive")
async def fugitive_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.services import events as ev_service

    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if ev_service.has_pending(ch) and has_screen("evchoice"):
            return await goto(ctx, "evchoice", "resume")
        if not ch.state.flags.get(FLAG_FUGITIVE):
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        rng = random.Random()
        lines = await svc.ensure_fugitive(s, ctx.content, ch, rng, year, tz)  # the flag may come from an event
        if lines:
            await game.save_active(s, ch, tz)
        if action == "giveup":
            return fugitive_screen(ctx.content, ch, confirm=True)
        if action == "giveup_yes":
            await svc.surrender_to_maxtac(s, ctx.content, ch, rng, year, tz)
            return capture_screen(ctx.content, ch)
    if action == "profile":
        return await goto(ctx, "profile")
    if action == "ripper" and has_screen("ripper"):
        return await goto(ctx, "ripper")
    if action == "train" and has_screen("activities"):
        return await goto(ctx, "activities")
    if action == "contacts" and has_screen("relations"):
        return await goto(ctx, "relations")
    if action == "week":
        return await goto(ctx, "week", "open")
    if action == "city" and has_screen("city"):
        return await goto(ctx, "city")
    if action == "legend":
        return legend_screen(ctx.content, ch)
    if action == "raid" and has_screen("raid"):  # owner report 24.10: a psycho can go kill a raid boss too
        return await goto(ctx, "raid")
    from app.bot.handlers.players import deliver_for_menu  # owner report 24.10: a hunted psycho gets letters (money) too

    lines = list(lines or []) + [texts.esc(x) for x in await deliver_for_menu(ctx, ch)]
    return fugitive_screen(ctx.content, ch, "\n".join(lines) if lines else None)


# -- what waits on the character -------------------------------------------------------------------------------------------------


async def psy_resume_screen(ctx: Ctx, ch: ActiveCharacter) -> Screen | None:
    """The unfinished matter of M6.6: the verdict after a capture, then the duty call. None when nothing waits."""
    if ch.state.flags.get(FLAG_PSYCHO_CAPTURE):
        return capture_screen(ctx.content, ch)
    if ch.state.flags.get(FLAG_MAXTAC_DUTY):
        return duty_screen(ctx.content, ch)
    return None


def duty_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    call = svc.duty_of(ch)
    assert call is not None
    lines = [T.DUTY_TITLE, ""]
    if call["kind"] == "player":
        district = content.districts.get(call.get("target_district", ""))
        lines.append(T.DUTY_PLAYER.format(
            name=texts.esc(call.get("target_name", "")), danger=call["danger"],
            district=texts.esc(district.name if district else "где-то в городе")))
    else:
        lines.append(T.DUTY_PSYCHO.format(danger=call["danger"]))
    lines.append(T.DUTY_LEASH_WARN if eng.has_leash(ch.state) else T.DUTY_PLAIN_WARN)
    if note:
        lines += ["", note]
    rows = [[Button(T.DUTY_BTN_GO, "go"), Button(T.DUTY_BTN_REFUSE, "refuse")]]
    return Screen("\n".join(lines), rows, name="duty")


@on_screen("duty")
async def duty_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.bot.handlers.combat import step_screen

    tz = ctx.ui.settings.timezone
    rng = random.Random()
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None or svc.duty_of(ch) is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        if action == "go":
            step = await svc.accept_duty(s, ctx.content, ch, rng, year, tz)
            return step_screen(ctx.content, ch, step)
        if action == "refuse":
            lines, died, cause = await svc.refuse_duty(s, ctx.content, ch, rng, year, tz)
            if died:
                return death_screen(ch.state.name, cause, lines)
            return Screen("\n".join(lines), [[Button(T.PSY_WARD_CONTINUE, "next")]], name="dutyout")
        return duty_screen(ctx.content, ch)


@on_screen("dutyout")
async def dutyout_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    return await menu_screen(ctx)
