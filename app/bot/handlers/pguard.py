"""The window of the bodyguards (owner request 25.10): «Свои» → «Охрана». Hire a player, fire one, leave a post."""

from __future__ import annotations

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen, on_text
from app.bot.ui.screen import Button, Screen
from app.db import session as db
from app.services import game
from app.services import pguard as svc


async def guard_screen(ctx: Ctx, note: str | None = None) -> Screen:
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        mine = await svc.guards_of(s, ch.player_tg_id)
        theirs = await svc.guarding(s, ch.player_tg_id)
    cfg = svc.cfg(c)
    lines = [style.header("war", "Охрана"),
             "Найми другого игрока в охрану: его персонаж со своим оружием, хромом и навыками встаёт рядом в каждом "
             "твоём бою (кроме личных вызовов), если он в том же городе, на свободе и не в своём бою. Здоровье и патроны "
             f"охранника не тратятся. Зарплата уходит письмом каждый прожитый месяц. Не больше {cfg.max_guards}. "
             "Охранника можно отправить на заказ вместо себя: кнопка «🪖 Поручить своему» на брифинге заказа.", ""]
    rows: list[list[Button]] = []
    if mine:
        lines.append("<b>Твоя охрана</b>")
        for tg, e in mine.items():
            lines.append(f"• 🛡 {texts.esc(str(e.get('name')))} · {views.money(int(e.get('wage', 0) or 0))} эдди в месяц")
            rows.append([Button(f"Уволить: {e.get('name')}"[:24], "fire", tg)])
    else:
        lines.append("Охраны нет.")
    if theirs:
        lines += ["", "<b>Ты охраняешь</b>"]
        for emp, e in theirs:
            boss = str(e.get("boss") or "нанимателя")
            lines.append(f"• {texts.esc(boss)} · {views.money(int(e.get('wage', 0) or 0))} эдди в месяц")
            rows.append([Button(f"Уйти от: {boss}"[:24], "quit", emp)])
        rows.append([Button("⚔️ Как я бьюсь в охране", "pally")])
    if note:
        lines += ["", note]
    if len(mine) < cfg.max_guards:
        rows.append([Button("Нанять игрока", "hire", style="success")])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="pguard")


async def pick_screen(ctx: Ctx) -> Screen:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        people = await svc.candidates(s, ch)
    lines = [style.header("war", "Кого нанять"), "Живые персонажи других игроков."]
    rows = [[Button(name[:24], "who", str(cid))] for cid, name in people[:20]]
    if not people:
        lines.append("Некого.")
    rows.append([Button(texts.BACK, "open"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="pguard")


def wage_screen(ctx: Ctx, char_id: int, name: str) -> Screen:
    lines = [style.header("war", f"Охрана: {texts.esc(name)}"), "Сколько платить в месяц?"]
    rows = [[Button(f"{views.money(w)} эдди", "wage", f"{char_id}:{w}") for w in svc.cfg(ctx.content).wages[:2]],
            [Button(f"{views.money(w)} эдди", "wage", f"{char_id}:{w}") for w in svc.cfg(ctx.content).wages[2:]],
            [Button("Своя сумма", "custom", str(char_id))],
            [Button(texts.BACK, "hire"), Button(texts.MENU, "menu")]]
    return Screen("\n".join(lines), [r for r in rows if r], name="pguard")


@on_screen("pguard")
async def pguard_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "plist", "open")
    if action == "hire":
        return await pick_screen(ctx)
    if action == "pally":  # owner request 26.10
        return await goto(ctx, "pally", "open")
    c = ctx.content
    if action == "who" and arg and arg.isdigit():
        from app.db.models import Character

        async with db.session_scope() as s:
            row = await s.get(Character, int(arg))
            name = f"{row.name} {row.surname or ''}".strip() if row else ""
        return wage_screen(ctx, int(arg), name) if row else await pick_screen(ctx)
    if action == "custom" and arg and arg.isdigit():  # owner request 25.10: any wage typed in
        cfg = svc.cfg(c)
        return Screen(f"{style.header('war', 'Охрана: своя сумма')}\nНапиши в чат зарплату в эдди за месяц: "
                      f"от {views.money(cfg.wage_min)} до {views.money(cfg.wage_max)}.",
                      [[Button(texts.BACK, "hire"), Button(texts.MENU, "menu")]], awaiting_text="guard_wage",
                      name="pguard", state={"char": int(arg)})
    if action == "wage" and arg and ":" in arg:
        cid, _, w = arg.partition(":")
        return await _send_offer(ctx, int(cid), int(w))
    note = None
    if action in ("fire", "quit") and arg:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            if action == "fire":
                note = await svc.fire(s, ch.player_tg_id, int(arg), by_guard=False)
            else:
                note = await svc.fire(s, int(arg), ch.player_tg_id, by_guard=True)
        note = texts.esc(note)
    return await guard_screen(ctx, note)


async def _send_offer(ctx: Ctx, char_id: int, wage: int) -> Screen:
    from app.db.models import Character

    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        note = await svc.offer(s, c, ch, year, char_id, wage)
        row = await s.get(Character, char_id)
        target_tg = row.player_tg_id if row else 0
    if note.startswith("Предложение ушло") and target_tg:
        from app.bot.handlers.players import _ping_target

        await _ping_target(ctx, target_tg, "offer")
    return await guard_screen(ctx, texts.esc(note))


@on_text("guard_wage")
async def guard_wage_text(ctx: Ctx, text: str) -> Screen | None:
    import re

    cid = int((ctx.window.state or {}).get("char", 0) or 0) if ctx.window is not None else 0
    m = re.search(r"\d[\d\s]*", text or "")
    if not cid:
        return await guard_screen(ctx)
    if not m:
        return await guard_screen(ctx, "Нужна сумма числом.")
    return await _send_offer(ctx, cid, int(m.group(0).replace(" ", "")))
