"""The window of a challenge (owner request 25.10): menu → «⚔️ Вызов N»."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui.callbacks import Ctx, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import challenge as eng
from app.services import challenge as svc
from app.services import game
from app.services.game import ActiveCharacter

BLOCKED = {"none": "Сейчас вызова нет.", "jail": "Из камеры на вызов не выйти.", "busy": "Сначала разберись с тем, что уже ждёт.",
           "stage": "Этот этап уже позади.", "eddies": "Не хватает денег.", "ap": "Не хватает ОД."}


def _stage_lines(content: Content, op: dict) -> list[str]:
    stages = svc.stages_of(op)
    now = int(op.get("stage", 0) or 0)
    marks = [("✅ " if i < now else "▶️ " if i == now else "▫️ ") + eng.STAGE_RU[k] for i, k in enumerate(stages)]
    return ["Этапы: " + " → ".join(marks)]


def challenge_screen(content: Content, ch: ActiveCharacter, rec: dict, note: str | None = None) -> Screen:
    op = rec.get("open")
    report = rec.get("report") or {}
    c = content.balance.challenge
    lines = ["⚔️ <b>Вызовы</b>",
             "Раз в сутки город оценивает каждого. Кто перерос улицы или разбогател, получает личный вызов: Claude "
             "придумывает его против билда или капитала. Вызов из трёх этапов, и пока он открыт, враг бьёт каждый день. "
             "Каждый следующий вдвое сильнее.", ""]
    if report:
        lines.append(f"Последняя оценка силы: побед над МаксТаком и Милитехом {int(report.get('win', 0) * 100)}%, "
                     f"здоровья после боя {int(report.get('hp', 0) * 100)}%. Сила {report.get('score', 0)} "
                     f"(боевые вызовы с {c.score_from}). Вызовы богатства — с {views.money(c.wealth_from)} эдди или места в совете.")
    lines.append(f"Ступени: бой <b>{int(rec.get('level', 1))}</b>, богатство <b>{int(rec.get('wlevel', 1) or 1)}</b>.")
    hist = rec.get("history") or []
    if hist:
        lines.append("Пройдено: " + ", ".join(f"«{texts.esc(t)}»" for t in hist[-5:]) + ".")
    rows: list[list[Button]] = []
    if op:
        level = int(op["level"])
        wealth = svc.track_of(op) == svc.WEALTH
        stage = svc.stage_of(op)
        hit = eng.daily_hit(content, svc.track_of(op), level)
        lines += ["", f"<b>{'💼' if wealth else '⚔️'} Вызов {level}: «{texts.esc(op['title'])}»</b>",
                  f"<i>{texts.esc(op['intro'])}</i>",
                  f"Враг: {texts.esc(op.get('foe') or '—')}" + (f" — «{texts.esc(op['taunt'])}»" if op.get("taunt") else ""),
                  f"В бою против тебя: {texts.esc(eng.COUNTER_RU.get(op['counter'], op['counter']))}.", *_stage_lines(content, op)]
        if wealth:
            esc = int(op.get("escrow", 0) or 0)
            reward = min(c.w_reward_cap, c.w_reward * eng.mult(level))
            lines.append(f"Каждый день, пока вызов открыт: −{hit['pct']}% денег на руках. "
                         + (f"Заморожено {views.money(esc)} эдди до победы." if op.get("frozen") else
                            f"На первом шаге заморозится {min(c.escrow_cap_pct, c.escrow_pct * eng.mult(level))}% денег, вернутся только с победой."))
            lines.append(f"Награда: заморозка назад и +{views.money(reward)} эдди, слава +{c.fame_per_level * level}.")
        else:
            reward = min(c.reward_cap, c.reward_base * eng.mult(level))
            lines.append(f"Каждый день, пока вызов открыт: {-hit['hp']} здоровья и {views.money(-hit['eddies'])} эдди.")
            lines.append(f"Награда: {views.money(reward)} эдди, слава +{c.fame_per_level * level}, репутация "
                         f"+{c.cred_per_level * level}.")
        lines.append("Не пройти до срока — улица решит, что ты отступаешь" + (", заморозка сгорит." if wealth else "."))
        lines.append("")
        dv = eng.check_dv(content, level)
        if stage in ("fight", "boss", "hit"):
            lines.append({"fight": "Сейчас: бой с отрядом врага.", "boss": "Сейчас: бой с главарём, он крепче отряда.",
                          "hit": "Сейчас: покушение. Убийцы уже в пути, встреть их сам."}[stage])
            rows.append([Button("В бой", "go", style="danger")])
        elif stage == "track":
            lines.append(f"Сейчас: найти главаря. Проверка, сложность {dv}, {c.check_ap} ОД. Провал — засада и раны.")
            rows.append([Button("Через Сеть (ИНТ)", "act", "net"), Button("Через улицу (ХЛАД)", "act", "street")])
        else:
            price = eng.pay_price(content, level, stage)
            text = op.get("assets") if stage == "assets" else op.get("board")
            lines.append(f"Сейчас: {texts.esc(text or eng.STAGE_RU[stage])}")
            lines.append(f"Заплатить {views.money(price)} эдди или справиться самому: проверка, сложность {dv}, "
                         f"{c.check_ap} ОД. Провал — минус {eng.fail_pct(content, level)}% денег.")
            rows.append([Button(f"Заплатить {views.money(price)}"[:24], "act", "pay"),
                         Button("Своими силами", "act", "check")])
    else:
        lines += ["", "Открытого вызова нет. Следующая оценка — в полдень."]
    if note:
        lines += ["", note]
    rows.append([Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="chall")


@on_screen("chall")
async def chall_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    c = ctx.content
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "go":
            r = await svc.accept(s, c, ch, random.Random(), year, ctx.ui.settings.timezone)
            if not isinstance(r, str):
                from app.bot.handlers.combat import step_screen

                return step_screen(c, ch, r)  # type: ignore[arg-type]
            note = BLOCKED.get(r, r)
        elif action == "act" and arg:
            blocked, out, chk = await svc.act(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, arg)
            note = BLOCKED.get(blocked, blocked) if blocked else "\n".join(([views.check_line(chk)] if chk else [])
                                                                          + [texts.esc(x) for x in out])
        rec = await svc.record(s, ch)
    return challenge_screen(c, ch, rec, note)
