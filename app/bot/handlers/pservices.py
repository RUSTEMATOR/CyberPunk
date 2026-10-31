"""Services of other players (owner request 24.10). city -> psvc."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen, pack
from app.db import session as db
from app.services import game
from app.services import pservices as svc
from app.services.game import ActiveCharacter

BLOCKED = {"jail": "Из камеры к людям не выйти.", "gone": "Этой услуги уже нет.", "eddies": "Не хватает эдди.",
           "ap": "ОД кончились. Нажми «Прожить месяц».", "novehicle": "Машины нет."}


async def psvc_screen(ctx: Ctx, ch: ActiveCharacter, note: str | None = None) -> Screen:
    c = ctx.content
    async with db.session_scope() as s:
        offers = await svc.offers(s, c, ch)
    lines = [style.header("who", "Услуги игроков"), f"Эдди: {views.money(ch.state.eddies)} · ОД: {ch.state.ap}", ""]
    if note:
        lines += [note, ""]
    lines.append("<i>Кто из игроков риппер, фиксер, политик, медик, коп, журналист, рокер, механик или нетраннер — "
                 "тот открыт для дел. Деньги уходят ему письмом.</i>")
    lines.append("")
    buttons: list[Button] = []
    if not offers:
        lines.append("Пока ни у кого из живых игроков нет ремесла, которым можно воспользоваться. Пути «Риппер», "
                     "«Фиксер», «Политик», карьеры Trauma Team, NCPD, медиа, рокера, механика или роль нетраннера.")
    for i, o in enumerate(offers, 1):
        price = f"{views.money(o.price)} эдди" if o.price else "без оплаты вперёд"
        lines.append(f"{i}. <b>{texts.esc(o.provider)}</b> — {texts.esc(o.name)}: {texts.esc(o.text)}, {price}.")
        buttons.append(Button(str(i), "u", f"{o.provider_id}.{o.key}"))
    rows = pack(buttons, c.balance.ui.button_chars, max_per_row=5)
    rows.append([Button(texts.BACK, "city"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="psvc")


@on_screen("psvc")
async def psvc_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "city":
        return await goto(ctx, "city")
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if action != "u" or not arg or "." not in arg:
            return await psvc_screen(ctx, ch)
        pid, key = arg.split(".", 1)
        if not pid.isdigit():
            return await psvc_screen(ctx, ch)
        year = await game.current_year(s, c)
        r = await svc.use(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, int(pid), key)
    if r.blocked:
        return await psvc_screen(ctx, ch, BLOCKED.get(r.blocked, r.blocked))
    parts = [views.check_line(r.check)] if r.check is not None else []  # type: ignore[arg-type]
    parts.append(style.quote(texts.esc(r.text)))
    if r.applied is not None:
        sm = views.effects_summary(r.applied, c)
        if sm:
            parts.append(sm)
    return await psvc_screen(ctx, ch, "\n".join(parts))
