"""Loans in the window (owner request 24.10): property -> loans -> a lender -> how much."""

from __future__ import annotations

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import loans as eng
from app.services import game
from app.services import loans as svc
from app.services.game import ActiveCharacter

BLOCKED = {
    eng.BLOCK_HAVE: "Этот займ уже взят: сначала верни.",
    eng.BLOCK_STATUS: "Нужен статус повыше.",
    eng.BLOCK_CRED: "Нужна уличная репутация повыше.",
    eng.BLOCK_JOB: "Нужна работа с зарплатой.",
    eng.BLOCK_FRIEND: "Нет друга или родни, который настолько тебе доверяет.",
    eng.BLOCK_NONE: "Здесь не дадут.",
}


def loans_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    lines = [style.header("debt", "Займы"), f"Эдди: {views.money(st.eddies)} · долг всего: {views.money(st.debt_total)}", "",
             "Каждый займ — обычный долг: раз в неделю проценты и минимальный платёж списываются сами. Пропустишь "
             "платежи — каждый кредитор напоминает по-своему."]
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    for o in svc.offers(content, ch):
        ln = content.balance.loans.lenders[o.key]
        rate = f"{views._pct(ln.weekly_rate)} в неделю" if ln.weekly_rate else "без процентов"
        head = f"<b>{texts.esc(ln.name)}</b> · {rate}"
        if o.blocked:
            head += f" · <i>{BLOCKED.get(o.blocked, o.blocked)}</i>"
        else:
            head += f" · до {views.money(o.amount)}"
            rows.append([Button(f"{ln.name.split()[0]} · до {views.money(o.amount)}", "pick", o.key)])
        lines += ["", head, f"<i>{texts.esc(ln.text)}</i>"]
    rows.append([Button("Мои долги", "debts"), Button(texts.BACK, "prop")])
    return Screen("\n".join(lines), rows, name="loans")


def lender_screen(content: Content, ch: ActiveCharacter, key: str) -> Screen:
    o = next((x for x in svc.offers(content, ch) if x.key == key), None)
    if o is None or o.blocked:
        return loans_screen(content, ch, BLOCKED.get(o.blocked if o else eng.BLOCK_NONE, ""))
    ln = content.balance.loans.lenders[key]
    who = ch.state.npcs[o.friend].name if o.friend is not None else ln.name
    lines = [style.header("debt", texts.esc(who)), texts.esc(ln.text), "",
             f"Дадут до <b>{views.money(o.amount)}</b> эдди. Процент: "
             f"{views._pct(ln.weekly_rate) + ' в неделю' if ln.weekly_rate else 'нет'}.",
             f"Если пропустить платежи {ln.risk_weeks} нед. подряд: <i>{texts.esc(ln.risk_text.format(who=who))}</i>"]
    rows = [[Button(f"{p}% · {views.money(max(100, o.amount * p // 100 // 100 * 100))}", "take", f"{key}:{p}")
             for p in content.balance.loans.fractions]]
    rows.append([Button(texts.BACK, "list")])
    return Screen("\n".join(lines), rows, name="loans")


@on_screen("loans")
async def loans_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    if action == "prop":
        return await goto(ctx, "property")
    if action == "debts":
        return await goto(ctx, "debts", "open")
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if action == "pick" and arg:
            return lender_screen(c, ch, arg)
        if action == "take" and arg and ":" in arg:
            key, _, pct = arg.partition(":")
            year = await game.current_year(s, c)
            why, applied, who, amount = await svc.take(s, c, ch, year, ctx.ui.settings.timezone, key,
                                                       int(pct) if pct.isdigit() else 100)
            if why:
                return loans_screen(c, ch, BLOCKED.get(why, why))
            return loans_screen(c, ch, f"Займ получен: +{views.money(amount)} эдди от «{texts.esc(who)}». Платежи "
                                       "пойдут каждую неделю сами.")
    return loans_screen(c, ch)
