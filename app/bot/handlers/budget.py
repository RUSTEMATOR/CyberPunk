"""The budget of the month (owner request 25.10 «в минуса ухожу из-за всего»): «Профиль» → «Бюджет месяца».

What the last lived month brought and took by item (flag `budget`, written by services/week.live_week), and what the
next month will cost for sure (the forecast of fixed items)."""

from __future__ import annotations

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import army as army_eng
from app.engine import upkeep
from app.engine import week as week_eng
from app.services import game
from app.services.game import ActiveCharacter


def _sign(n: int) -> str:
    return f"+{views.money(n)}" if n > 0 else f"−{views.money(-n)}"


def forecast(content: Content, ch: ActiveCharacter) -> list[tuple[str, int]]:
    st = ch.state
    w = content.balance.time.weeks_per_press
    out: list[tuple[str, int]] = []
    lv = upkeep.lifestyle(st, content)
    if lv is not None and lv.weekly:
        out.append((f"Образ жизни «{lv.name}»", -lv.weekly * w))
    staff = upkeep.staff(st, content)
    if staff:
        out.append((f"Прислуга ({len(staff)})", -sum(m.weekly for m in staff) * w))
    rent, food = week_eng.housing_costs(st, content)
    if rent or food:
        out.append(("Жильё и еда", -(rent + food) * w))
    tr = week_eng.trauma_price(st, content)
    if tr:
        out.append(("Trauma Team", -tr * w))
    g = upkeep.needed_grade(st, content)
    taken = upkeep.grade(content, st.flags.get(upkeep.FLAG_IMMUNO))
    if g is not None and taken is not None:
        out.append((f"Иммуноподавители «{taken.name}»", -upkeep.immuno_price(st, content, taken) * w))
    steps = upkeep.empire_steps(st, content)
    if steps:
        out.append(("Империя: содержание", -sum(s.weekly for s in steps) * w))
    if army_eng.soldiers(st):
        out.append(("Свои бойцы: рэкет и жалованье", army_eng.racket(content, st) - army_eng.wage(content, st)))
    return out


def budget_screen(content: Content, ch: ActiveCharacter) -> Screen:
    st = ch.state
    lines = [style.header("eddies", "Бюджет месяца"), f"Сейчас на руках: <b>{views.money(st.eddies)}</b> эдди"
             + (f" · долг {views.money(st.debt_total)}" if st.debt_total else "")]
    b = st.flags.get("budget")
    if isinstance(b, dict) and b.get("items"):
        lines += ["", "<b>Прошлый прожитый месяц</b> (итоги месяца, без событий и дел за ОД)"]
        inc = [(k, v) for k, v in b["items"] if v > 0]
        exp = [(k, v) for k, v in b["items"] if v < 0]
        if inc:
            lines.append("Доходы:")
            lines += [f"• {texts.esc(k)}: {_sign(v)}" for k, v in sorted(inc, key=lambda x: -x[1])]
        if exp:
            lines.append("Расходы:")
            lines += [f"• {texts.esc(k)}: {_sign(v)}" for k, v in exp]
        lines.append(f"<b>Итог месяца: {_sign(int(b.get('total', 0)))} эдди</b>")
    else:
        lines += ["", "Данные появятся после следующего «Прожить месяц»."]
    fc = forecast(content, ch)
    if fc:
        total = sum(v for _, v in fc)
        lines += ["", "<b>Постоянные статьи на следующий месяц</b>"]
        lines += [f"• {texts.esc(k)}: {_sign(v)}" for k, v in fc]
        lines.append(f"Итого: <b>{_sign(total)}</b> эдди. Уровень жизни и персонал меняются кнопками ниже или в «Имущество».")
    rows = [[Button("Уровень жизни", "lifestyle"), Button("Персонал", "staff")],
            [Button(texts.BACK, "back"), Button(texts.MENU, "menu")]]
    return Screen("\n".join(lines), rows, name="budget")


@on_screen("budget")
async def budget_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "profile")
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    if ch is None:
        return await menu_screen(ctx)
    if action in ("lifestyle", "staff"):
        from app.bot.handlers import economy

        return economy.lifestyle_screen(ctx.content, ch) if action == "lifestyle" else economy.staff_screen(ctx.content, ch, year)
    return budget_screen(ctx.content, ch)
