"""«Судная ночь» in the window (owner request): /purge or «Город» → «Судная ночь».

purge (the night: preparation, the choice, the deeds of the night) -> purgetgt (a target: a shelter or a hunter)
"""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, make_ctx, on_screen
from app.bot.ui.kit import UiKit
from app.bot.ui.screen import Button, Screen, pack
from app.db import session as db
from app.services import game
from app.services import purge as svc
from app.services.game import ActiveCharacter

router = Router(name="purge")

MSG = {
    svc.R_CLOSED: "Сейчас не Судная ночь и не день перед ней.",
    svc.R_MODE: "Это не для твоего выбора на эту ночь.",
    svc.R_EDDIES: "Не хватает эдди.",
    svc.R_HAVE: "Это уже стоит.",
    svc.R_NONE: "Нечего.",
    svc.R_SPENT: "На эту ночь хватит: силы и патроны кончились.",
    svc.R_TARGET: "Эта цель недоступна.",
    svc.R_NOPAY: "С этим не договориться.",
    svc.R_BUSY: "Сначала разберись с тем, что уже ждёт.",
    svc.R_ABROAD: "Ты за границей. Судная ночь бывает только в Найт-Сити: здесь ты в безопасности.",
}
WEEKDAYS = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


async def purge_screen(ctx: Ctx, note: str | None = None) -> Screen:
    c = ctx.content
    cfg = svc.cfg(c)
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    async with db.session_scope() as s:
        st = await svc.state(s)
    status = st.get("status")
    lines = [style.header("danger", "Судная ночь")]
    rows: list[list[Button]] = []
    if status not in (svc.SOON, svc.NIGHT):
        start, end = svc.window(c, datetime.now(ZoneInfo(ctx.ui.settings.timezone)))
        lines += ["Раз в неделю на несколько часов в Найт-Сити отменяются все законы: NCPD не приезжает, розыск не растёт, "
                  "убийство — не преступление.",
                  f"Следующая ночь: в {WEEKDAYS[start.weekday()]}, {start.strftime('%d.%m')}, с {start.strftime('%H:%M')} "
                  f"до {end.strftime('%H:%M')}. Подготовка откроется за {cfg.announce_hours} ч.",
                  "", "Можно укрепить жильё и спрятаться или выйти на охоту: на улицах, на чужие убежища, на других охотников. "
                  "Кто не решит, может встретить рассвет на улице."]
        if note:
            lines += ["", note]
        rows.append([Button(texts.MENU, "menu")])
        return Screen("\n".join(lines), rows, name="purge")
    rec = (st.get("roster") or {}).get(str(ch.id), {})
    mode = rec.get("mode")
    mode_word = {svc.HIDE: "прятаться дома", svc.HUNT: "охотиться"}.get(mode, "не выбрано")
    lines.append(("<b>Ночь идёт.</b> Законов нет до " if status == svc.NIGHT else "<b>Подготовка.</b> Сирена в ")
                 + datetime.fromisoformat(str(st.get("end" if status == svc.NIGHT else "start"))).strftime("%H:%M") + ".")
    lines.append(f"Эдди: {views.money(ch.state.eddies)} · здоровье {ch.state.hp} · твой выбор: <b>{mode_word}</b>")
    where = rec.get("hub_name") or "своё жильё"
    lines.append(f"Убежище: <b>{texts.esc(str(where))}</b> · оборона <b>{svc.defense(c, rec)}</b> · аптечек: {rec.get('medkits', 0)}")
    async with db.session_scope() as s:
        from app.services import hubs

        options = await hubs.shelters(s, c, ch)
    if options:
        lines.append("<i>Можно прятаться вместе: общий дом, штаб банды или башня корпорации дают свою оборону, "
                     "и каждый, кто прячется там же, добавляет ещё.</i>")
    if note:
        lines += ["", note]
    th = rec.get("threat") if status == svc.NIGHT else None
    r = svc.raider(c, str(th.get("key", ""))) if th else None
    if r is not None:  # owner request 24.10: an NPC crew at the door
        who = rec.get("mode") or svc.OUT
        lines += ["", f"🚨 <b>К тебе идут: {texts.esc(r.name)}</b>", f"<i>{texts.esc(r.text)}</i>",
                  "Если не ответить до следующей волны, они придут сами."]
        trow = []
        if who != svc.HUNT:
            trow.append(Button("Держать дверь", "t_hold"))
        if who != svc.HIDE:
            trow.append(Button("Уйти дворами", "t_run"))
        trow.append(Button("Принять бой", "t_fight"))
        rows.append(trow)
        if r.pay < 99999:
            rows.append([Button(f"Откупиться · {views.money(r.pay)}"[:24], "t_pay")])
    owned = set(rec.get("defs") or [])
    lines += ["", "<b>Укрепить жильё</b>"]
    buy: list[Button] = []
    for d in cfg.defenses:
        mark = "✓ " if d.key in owned else ""
        lines.append(f"{mark}{texts.esc(d.name)} — +{d.defense} к обороне, {views.money(d.price)} эдди. <i>{texts.esc(d.text)}</i>")
        if d.key not in owned:
            buy.append(Button(f"{d.name}"[:22], "def", d.key))
    rows += pack(buy, c.balance.ui.button_chars, max_per_row=2)
    rows.append([Button(f"Аптечка · {cfg.medkit_price}", "medkit_buy")])
    if options:
        cur = rec.get("hub") or ""
        opts = [Button(("• " if not cur else "") + "Своё жильё", "shelter", "")]
        short = {"home": "Общий дом", "crew": "Штаб банды", "corp": "Башня"}
        opts += [Button(("• " if cur == o.id else "") + f"{short.get(o.id.split(':')[0], o.name)} (+{o.defense})",
                        "shelter", o.id) for o in options]
        rows += pack(opts, c.balance.ui.button_chars, max_per_row=1)
    rows.append([Button(("• " if mode == svc.HIDE else "") + "Спрятаться дома", "mode", svc.HIDE),
                 Button(("• " if mode == svc.HUNT else "") + "Выйти на охоту", "mode", svc.HUNT)])
    if status == svc.NIGHT:
        lines.append("")
        if mode == svc.HUNT:
            left = cfg.hunts - int(rec.get("hunts", 0))
            lines.append(f"<b>Охота</b>: осталось {left} из {cfg.hunts}. Улица — кто попадётся: житель района, банда, "
                         "своя история ночи. Штурм — убежище игрока или NPC против его обороны. "
                         "Охотники — другой охотник или банда ночи. "
                         f"Добыча: {cfg.storm_take_pct}% денег жертвы, {cfg.duel_take_pct}% у охотника.")
            rows.append([Button("Улица", "street"), Button("Штурм убежища", "targets", svc.HIDE),
                         Button("Охотники", "targets", svc.HUNT)])
        elif mode == svc.HIDE:
            lines.append(f"<b>Оборона</b>: отстреливаться можно ещё {cfg.shoot_back_times - int(rec.get('shots', 0))} раз "
                         f"(+{cfg.shoot_back} к обороне каждый). На рассвете убежища проверят банды.")
            rows.append([Button("Отстреливаться", "shoot")])
        if int(rec.get("medkits", 0)) > 0:
            rows.append([Button(f"Аптечка (+{cfg.medkit_hp} здоровья)", "medkit")])
    rows.append([Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="purge")


async def targets_screen(ctx: Ctx, kind: str) -> Screen:
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    async with db.session_scope() as s:
        st = await svc.state(s)
    roster = st.get("roster") or {}
    mine = roster.get(str(ch.id), {})
    done = {str(x) for x in mine.get("targets", [])}
    lines = [style.header("danger", "Штурм убежища" if kind == svc.HIDE else "Охота на охотников"), ""]
    cfg = svc.cfg(ctx.content)
    buttons: list[Button] = []
    i = 0
    for cid, r in roster.items():
        if cid == str(ch.id) or r.get("mode") != kind or cid in done:
            continue
        i += 1
        extra = f", оборона ~{svc.defense(ctx.content, r)}" if kind == svc.HIDE else ""
        lines.append(f"{i}. {texts.esc(str(r.get('name', '')))}{extra}")
        buttons.append(Button(str(i), "t", cid))
    npc: list[Button] = []  # owner request 24.10: NPC targets, not only the players
    if kind == svc.HIDE:
        for m in cfg.marks:
            if f"m:{m.key}" in done:
                continue
            lines.append(f"🏚 {texts.esc(m.name)}, оборона ~{m.defense}. <i>{texts.esc(m.text)}</i>")
            npc.append(Button(m.name[:24], "m", m.key))
    else:
        for r in cfg.raiders:
            if f"r:{r.key}" in done or "hunt" not in r.targets:
                continue
            lines.append(f"🔪 {texts.esc(r.name)}, опасность {r.danger}. <i>{texts.esc(r.text)}</i>")
            npc.append(Button(r.name[:24], "r", r.key))
    if not buttons and not npc:
        lines.append("Целей нет: к каждому визит уже был.")
    rows = pack(buttons, ctx.content.balance.ui.button_chars, max_per_row=5)
    rows += pack(npc, ctx.content.balance.ui.button_chars, max_per_row=2)
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="purgetgt", state={"kind": kind})


@on_screen("purge")
async def purge_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "targets" and arg in (svc.HIDE, svc.HUNT):
        return await targets_screen(ctx, arg)
    c = ctx.content
    if action in ("def", "medkit_buy", "mode", "shelter"):
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            what = {"def": "def", "medkit_buy": "medkit", "mode": "mode", "shelter": "shelter"}[action]
            why = await svc.prepare(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, what, arg or "")
        return await purge_screen(ctx, MSG.get(why or "", why) if why else "Готово.")
    if action in ("street", "shoot", "medkit") + svc.THREAT_ACTS:
        return await _act(ctx, action, 0)
    return await purge_screen(ctx)


async def _act(ctx: Ctx, what: str, target: int, key: str = "") -> Screen:
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        res = await svc.act(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, what, target, key)
    if res.blocked:
        return await purge_screen(ctx, MSG.get(res.blocked, res.blocked))
    if res.fight is not None:
        from app.bot.handlers.combat import step_screen

        return step_screen(c, ch, res.fight)  # type: ignore[arg-type]
    if res.event:
        return await goto(ctx, "evchoice", "resume")
    parts = [texts.esc(res.text)]
    if res.applied is not None:
        summary = views.effects_summary(res.applied, c)
        if summary:
            parts.append(summary)
    parts.extend(res.lines)
    if res.died:
        from app.bot.handlers.week import death_screen

        return death_screen(ch.state.name, res.death_cause, parts)
    if res.post:
        ctx.ui.post(texts.esc(res.post), 6 * 3600)
    return await purge_screen(ctx, "\n".join(parts))


@on_screen("purgetgt")
async def purgetgt_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    kind = str(ctx.state.get("kind", svc.HIDE))
    if action == "t" and arg and arg.isdigit():
        return await _act(ctx, "storm" if kind == svc.HIDE else "duel", int(arg))
    if action in ("m", "r") and arg:
        return await _act(ctx, "mark" if action == "m" else "raid", 0, arg)
    return await purge_screen(ctx)


@router.message(Command("purge"))
async def cmd_purge(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    ctx = await make_ctx(ui, user, message.chat.id)
    await ui.windows.open(message.chat.id, user.id, await purge_screen(ctx))


@router.message(Command("admin_purge"))
async def cmd_admin_purge(message: Message, command: CommandObject, ui: UiKit) -> None:
    """Admin: start a night now for N minutes (default 60)."""
    user = message.from_user
    if user is None or not ui.is_admin(user.id):
        return
    minutes = int(command.args) if command.args and command.args.strip().isdigit() else 60
    async with db.session_scope() as s:
        await svc.force_start(s, ui.content, ui.settings.timezone, minutes)
    ui.post(svc.SIREN, 3 * 3600)
