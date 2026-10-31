"""The window of the city crisis (owner request 24.10): main menu -> the crisis button."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui.callbacks import Ctx, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import crisis as eng
from app.services import crisis as svc
from app.services import game
from app.services.game import ActiveCharacter

BLOCKED = {
    "none": "Сейчас в городе нет кризиса.", "jail": "Из камеры кризис видно только в новостях.",
    "abroad": "Ты за границей. Отсюда Найт-Сити можно помочь только деньгами.", "side": "Сначала выбери сторону.",
    "eddies": "Не хватает эдди.", "paid": "Деньгами в этот кризис больше не помочь: только делом.", "ap": "ОД кончились.", "busy": "Сначала разберись с тем, что уже ждёт.",
}
STAT_RU = {"BODY": "ТЕЛО", "REF": "РЕФЛ", "TECH": "ТЕХ", "INT": "ИНТ", "COOL": "ХЛАД", "EMP": "ЭМП", "LUCK": "УДАЧА"}
MOD_RU = {"shop_discount_pct": "цены в магазинах", "gig_pay_pct": "оплата заказов", "salary_pct.all": "зарплата",
          "mood_weekly": "настроение в неделю", "crime_reward_pct": "доход с криминала", "check.escape": "побег",
          "robbery_loss_pct": "потери при грабеже", "hp_regen": "восстановление здоровья", "ice": "лёд",
          "humanity_loss_pct": "потеря человечности от хрома", "ripper_discount_pct": "цены у риппера",
          "ram_max": "RAM", "check.gig_hack": "взломы", "check.combat": "бой", "food_x": "цена еды",
          "check.social": "общение", "heat_decay": "спад розыска"}


def _mods_text(mods: dict) -> str:
    out = []
    for k, v in (mods or {}).items():
        name = MOD_RU.get(k, k)
        if k in ("shop_discount_pct", "ripper_discount_pct"):
            out.append(f"{name} {'+' if v < 0 else '−'}{abs(v)}%")
        elif k == "food_x":
            out.append(f"{name} ×{1 + v}")
        elif k.endswith("_pct") or k.startswith("salary_pct"):
            out.append(f"{name} {'+' if v > 0 else '−'}{abs(v)}%")
        else:
            out.append(f"{name} {'+' if v > 0 else '−'}{abs(v)}")
    return ", ".join(out)


def crisis_screen(content: Content, ch: ActiveCharacter, st: dict | None, note: str | None = None) -> Screen:
    if st is None:
        lines = ["🕊 <b>В городе тихо</b>", "Кризиса сейчас нет. Новый начнётся сам, о нём напишут в беседе."]
        if note:
            lines += ["", note]
        return Screen("\n".join(lines), [[Button(texts.MENU, "menu")]], name="crisis")
    c = eng.get(st["key"]) or {}
    day, total = svc.day_of(st)
    side = eng.sync_side(ch.state, st.get("id"))
    my = int(((st.get("by") or {}).get(str(ch.player_tg_id)) or {}).get("pts", 0) or 0)
    s = eng.settings()
    lines = [f"<b>{svc.title(st)}</b> · день {day} из {total}",
             f"<i>{texts.esc(str(c.get('intro', '')))}</i>", "",
             f"Счёт: <b>{svc.score_line(st)}</b>",
             f"Твой вклад: <b>{my}</b>" + (f" · сторона: <b>{texts.esc(eng.side_name(c, side))}</b>" if side else ""),
             "",
             f"Пока идёт кризис, у всех: {_mods_text(c.get('mods') or {})}.",
             f"Каждый «Прожить месяц»: {texts.esc(str(c.get('press_text', '')))} И одно событие кризиса.",
             f"Награда за вклад: {views.money(int(s.get('reward_base', 1500)))} + {int(s.get('reward_per_pt', 150))} за очко "
             f"(до {views.money(int(s.get('reward_cap', 15000)))}). "
             + ("Проигравшая сторона получает только компенсацию." if c.get("kind") == eng.WAR else
                f"Цель: {c.get('target')} очков, иначе город платит последствиями.")]
    fm = c.get("fail_mail")  # owner request 24.10: what a lost external threat costs everyone
    if isinstance(fm, dict):
        if c.get("kind") == eng.WAR:
            for sk, f in fm.items():
                if isinstance(f, dict):
                    lines.append(f"⚠️ Если победит сторона «{texts.esc(eng.side_name(c, sk))}», каждый в городе потеряет "
                                 f"{f.get('pct', 0)}% денег, здоровье и настроение.")
        else:
            lines.append(f"⚠️ Провал ударит по каждому: −{fm.get('pct', 0)}% денег"
                         + (f", здоровье {fm['hp']}" if fm.get("hp") else "") + (f", настроение {fm['mood']}" if fm.get("mood") else "") + ".")
    rows: list[list[Button]] = []
    if c.get("kind") == eng.WAR and not side:
        lines += ["", "Выбери сторону. Передумать до конца войны нельзя."]
        rows.append([Button(eng.side_name(c, "a"), "join", "a"), Button(eng.side_name(c, "b"), "join", "b")])
    else:
        lines += ["", "<b>Что можно сделать</b>"]
        for key, a in (c.get("actions") or {}).items():
            kind = a.get("kind")
            if kind == "pay":
                cost, how = f"{views.money(int(a.get('eddies', 0)))} эдди", f"+{a.get('pts')} очка, до {s.get('pay_limit', 3)} раз"
            elif kind == "fight":
                cost, how = f"{a.get('ap', 2)} ОД", f"бой, победа +{s.get('fight_pts', 5)}"
            else:
                cost = f"{a.get('ap', 1)} ОД"
                how = f"{STAT_RU.get(str(a.get('stat')), a.get('stat'))}, сложность {a.get('dv')}, +{a.get('pts')}"
            lines.append(f"• {texts.esc(str(a.get('name')))}: {cost}, {how}")
            label = f"{a.get('short') or a.get('name')} · {cost}"
            rows.append([Button(label if len(label) <= 24 else str(a.get("name"))[:24], "act", key)])
    if note:
        lines += ["", note]
    rows.append([Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="crisis")


@on_screen("crisis")
async def crisis_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    if action == "menu":
        return await menu_screen(ctx)
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        await svc.harvest(s, c, ch, tz)
        note = None
        if action == "join" and arg:
            why = await svc.join(s, c, ch, tz, arg)
            note = BLOCKED.get(why, "Сторона уже выбрана.") if why else "Сторона выбрана. Теперь твой вклад идёт ей."
        elif action == "act" and arg:
            year = await game.current_year(s, c)
            r = await svc.act(s, c, ch, random.Random(), year, tz, arg)
            if r.blocked:
                note = BLOCKED.get(r.blocked, r.blocked)
            elif r.fight is not None:
                from app.bot.handlers.combat import step_screen

                return step_screen(c, ch, r.fight)  # type: ignore[arg-type]
            else:
                note = texts.esc(r.text)
                if r.check is not None:
                    note = views.check_line(r.check) + "\n" + note  # type: ignore[arg-type]
                if r.applied is not None:
                    extra = "\n".join(views.effects_lines(r.applied, c))
                    if extra:
                        note += "\n" + extra
                if r.pts:
                    note += f"\n🔥 Вклад +{r.pts}"
        st = await svc.current(s)
    return crisis_screen(c, ch, st, note)
