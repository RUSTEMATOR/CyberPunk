"""The window «wanted» (owner request 26.10, docs/PLAN_WANTED.md): the chase, what it costs and the ways to drop it."""

from __future__ import annotations

import random
import time

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.db import session as db
from app.engine import wanted as eng
from app.services import game
from app.services import wanted as svc
from app.services.game import ActiveCharacter


def stars(heat: int, mx: int) -> str:
    return "★" * heat + "☆" * max(0, mx - heat)


def chase_line(content, ch: ActiveCharacter) -> str | None:  # noqa: ANN001
    """One line for the menu: the next wave."""
    nxt = eng.next_in(ch.state, content, time.time())
    if nxt is None:
        return None
    secs, acts = nxt
    when = "в любой момент" if secs <= 0 or acts <= 0 else f"через ~{max(1, round(secs / 60))} мин или {acts} действ."
    faction, _, extra = eng.wave(content, ch.state.heat, int((ch.state.flags.get(eng.FLAG) or {}).get("wave", 0) or 0))
    return f"🚨 Погоня: {svc.POLICE[faction]}" + (f" +{extra}" if extra else "") + f" {when}"


def wanted_screen(ctx: Ctx, ch: ActiveCharacter, note: str | None = None) -> Screen:
    content = ctx.content
    c = eng.cfg(content)
    st = ch.state
    mx = content.balance.character.heat_max
    lines = [style.header("heat", f"Розыск {stars(st.heat, mx)} {st.heat}/{mx}")]
    if st.heat <= 0:
        lines.append("Тебя никто не ищет. Пока.")
    else:
        line = chase_line(content, ch)
        if line:
            lines.append(line)
        elif st.heat < c.chase_from:
            lines.append("Тебя пока ищут вяло: за неделю без криминала розыск 1 спадёт сам.")
        lines += ["", "<b>Что это значит</b>",
                  f"• С розыска {c.chase_from} полиция едет волнами: чем выше розыск, тем чаще и тем меньше действий "
                  f"между волнами. Каждая волна больше прошлой, на {c.maxtac_at} вместо NCPD едет МаксТак.",
                  "• Выигранная волна поднимает розыск и зовёт следующую быстрее. Проигранная — арест.",
                  f"• Розыск {c.no_decay_from}+ сам не спадает: его нужно скинуть.",
                  "• Свидетели решают: бой без выживших и без прохожих, чистая работа — розыск не растёт."]
        lines += ["", "<b>Как скинуть</b>"]
    rows: list[list[Button]] = []
    if st.heat > 0:
        hp = svc.hide_pct(content, ch)
        lines.append(f"• Залечь на дно: {c.hide_ap} ОД, {hp}% (COOL + скрытность против {eng.hide_dv(content, st.heat)}). "
                     f"Успех: розыск −{c.hide_drop}, погоня теряет след на {c.hide_pause_h:g} ч. Провал: волна сразу.")
        rows.append([Button(f"Залечь на дно · {hp}%", "hide", style="success")])
        cp = eng.change_price(content, st.heat)
        lines.append(f"• Сменить тачку и шмот: {views.money(cp)}, {c.change_ap} ОД, раз в сутки. Розыск −{c.change_drop}.")
        rows.append([Button(f"Сменить шмот · {views.money(cp)}"[:24], "change")])
        if st.heat <= c.fixer_max_heat:
            fp = eng.fixer_price(content, st.heat)
            lines.append(f"• Откупиться через фиксера: {views.money(fp)}, раз в сутки. Розыск в ноль.")
            rows.append([Button(f"Фиксер · {views.money(fp)}"[:24], "fixer")])
        else:
            lines.append("• Фиксеры за МаксТак не берутся.")
        fc = eng.face_price(content, st.heat)
        lines.append(f"• Новое лицо у риппера: {views.money(fc)}, {c.face_ap} ОД, −{c.face_humanity} человечности, "
                     f"раз в {c.face_days} дн. Розыск в ноль на любом уровне.")
        rows.append([Button(f"Новое лицо · {views.money(fc)}"[:24], "face", style="danger")])
    if note:
        lines += ["", note]
    rows.append([Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="wanted")


@on_screen("wanted")
async def wanted_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    tz = ctx.ui.settings.timezone
    rng = random.Random()
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        note = None
        if action == "hide":
            note = svc.hide(ctx.content, ch, rng, year)
        elif action == "change":
            note = svc.change(ctx.content, ch, rng, year, tz)
        elif action == "fixer":
            note = svc.fixer(ctx.content, ch, rng, year, tz)
        elif action == "face":
            note = svc.face(ctx.content, ch, rng, year)
        if note is not None:
            svc.tick(ctx.content, ch)
            await game.save_active(s, ch, tz)
    if note is not None and note.startswith("🚨") and action == "hide":
        return await goto(ctx, "evchoice", "resume")
    return wanted_screen(ctx, ch, note)
