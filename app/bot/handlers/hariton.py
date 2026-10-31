"""The event of the week with the cat Hariton (owner request 25.10): «Город» → «🐱 Харитон недели»."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen, on_text
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.services import game
from app.services import hariton as svc
from app.services.game import ActiveCharacter

BLOCK = {"closed": "Ставки сейчас закрыты.", "other_side": "Ставка уже на другой ответ: менять сторону нельзя, можно только добавить.",
         "eddies": "Не хватает эдди."}

ABOUT = ("Харитон — рыжий экзот с от природы угрюмой мордочкой. Хозяйка научила его звуковым кнопкам, и теперь он "
         "складывает из них философские вопросы вроде «Почему я грустный?» и «Почему я котик?». Город следит за каждым "
         "его нажатием.")


def hariton_screen(content: Content, ch: ActiveCharacter, r: dict, note: str | None = None, side: str = "") -> Screen:
    c = svc.cfg(content)
    lines = [style.header("🐱", "Харитон недели"), ABOUT, ""]
    rows: list[list[Button]] = []
    q = svc.question(content, r.get("q", "")) if r else None
    if q is None:
        lines.append("Вопрос недели скоро появится.")
    else:
        yes, no = svc.totals(r)
        bank = int(r.get("bank", 0) or 0)
        n = len(r.get("bets") or {})
        lines += [f"<b>{texts.esc(q.text)}</b>", "",
                  f"На «Да»: {views.money(yes)} эдди · на «Нет»: {views.money(no)} эдди · игроков: {n}"
                  + (f"\nВ банке с прошлой недели: {views.money(bank)} эдди" if bank else ""),
                  f"Ответ: {svc.closes(content, str(r.get('week', '')))}. Победители забирают свою ставку и делят "
                  "проигравшие ставки и банк пропорционально ставкам. Если на верный ответ никто не поставил, "
                  "всё уходит в банк следующей недели.",
                  f"Ставка от {views.money(c.min_bet)} до {views.money(c.max_bet)} эдди за неделю, сторону менять нельзя."]
        mine = (r.get("bets") or {}).get(str(ch.player_tg_id))
        if mine:
            s = str(mine.get("side"))
            amt = int(mine.get("amount", 0) or 0)
            lines.append(f"\nТвоя ставка: <b>{svc.SIDES.get(s, s)}</b>, {views.money(amt)} эдди. Если выиграет — "
                         f"примерно {views.money(int(amt * svc.payout_per(r, s)))} эдди.")
            side = s
        if side:
            lines.append(f"\nСколько поставить на «{svc.SIDES[side]}»? Можно кнопкой или написать сумму в чат.")
            rows.append([Button(views.money(a), "amt", f"{side}:{a}") for a in c.presets[:2]])
            rows.append([Button(views.money(a), "amt", f"{side}:{a}") for a in c.presets[2:4]])
            if not mine:
                rows.append([Button("Другой ответ", "open")])
        else:
            rows.append([Button("Ставлю: Да", "side", "yes", style="success"), Button("Ставлю: Нет", "side", "no", style="danger")])
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="hariton", state={"side": side},
                  awaiting_text="hariton_bet" if side else None)


async def _place(ctx: Ctx, side: str, amount: int) -> Screen:
    c = ctx.content
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        why = await svc.bet(s, c, ch, random.Random(), year, tz, side, amount)
        cf = svc.cfg(c)
        note = {"min": f"Минимальная ставка {views.money(cf.min_bet)} эдди.",
                "max": f"Больше {views.money(cf.max_bet)} эдди за неделю поставить нельзя."}.get(why or "") \
            or BLOCK.get(why or "") or f"Ставка принята: {views.money(amount)} эдди на «{svc.SIDES[side]}»."
        r = await svc.load(s)
    return hariton_screen(c, ch, r, note, side if why else "")


@on_screen("hariton")
async def hariton_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "city", "open")
    if action == "amt" and arg and ":" in arg:
        side, _, a = arg.partition(":")
        if side in svc.SIDES and a.isdigit():
            return await _place(ctx, side, int(a))
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        r = await svc.load(s)
    side = arg if action == "side" and arg in svc.SIDES else ""
    return hariton_screen(ctx.content, ch, r, None, side)


@on_text("hariton_bet")
async def hariton_text(ctx: Ctx, text: str) -> Screen | None:
    side = str((ctx.window.state if ctx.window else {}).get("side") or "")
    digits = "".join(x for x in text if x.isdigit())
    if side not in svc.SIDES or not digits:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            r = await svc.load(s)
        if ch is None:
            return await menu_screen(ctx)
        return hariton_screen(ctx.content, ch, r, "Напиши сумму числом, например 3000.", side)
    return await _place(ctx, side, int(digits))
