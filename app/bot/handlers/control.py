"""The window of the streets (owner request 25.10): «Город» → «Улицы: банды и корпы». Two meters per district of Night
City, the cleansing of the gangs and the strikes at the corps (services/control.py)."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import control as eng
from app.services import control as svc
from app.services import game
from app.services import turf as turf_svc
from app.services.game import ActiveCharacter

BLOCK = {"city": "Это работает только в Найт-Сити.", "jail": "Из камеры не выйти.", "busy": "Сначала разберись с тем, что уже ждёт.",
         "today": "На сегодня хватит: зачистки и удары — не больше двух в день.", "ap": "Не хватает ОД.",
         "nogang": "Чужих банд в районе нет.", "mine": "Район и так твой.", "notmine": "Это не твой район.",
         "gone": "Такого варианта нет."}


def turf_block(content: Content, why: str) -> str:
    c = content.balance.turf
    return {"army": f"Нужна своя банда от {c.need_army} бойцов («Город» → «Свои люди»).",
            "meters": f"Район слишком крепко держат: банды и корпорации должны быть не выше {c.max_meter}%."}.get(why) \
        or BLOCK.get(why, why)


def turf_lines(content: Content, ch: ActiveCharacter, holder: dict | None, m: dict | None) -> tuple[list[str], list[list[Button]]]:
    """Owner request 25.10: the district held by a player: who, the tax, the law; take or contest it."""
    c = content.balance.turf
    lines: list[str] = []
    rows: list[list[Button]] = []
    if holder is not None:
        mine = int(holder.get("owner", 0) or 0) == ch.player_tg_id
        tax = int(holder.get("tax", 0) or 0)
        lw = turf_svc.law(content, str(holder.get("law", "")))
        lines.append(f"👑 <b>Район держит {texts.esc(str(holder.get('name', '')))}</b> ({texts.esc(str(holder.get('army', '')))}, "
                     f"бойцов {int(holder.get('size', 0) or 0)}).")
        lines.append(f"Налог: {c.tax_names[tax] if tax < len(c.tax_names) else tax}. "
                     f"Закон: {texts.esc(lw.name) if lw else '—'}" + (f" — {texts.esc(lw.desc)}" if lw and lw.desc else ""))
        if mine:
            lines.append("Твои люди каждый день сдерживают банды и корпорации района. Налог приходит каждый прожитый месяц.")
            rows.append([Button("Налог района", "tax"), Button("Закон района", "law")])
        else:
            lines.append(f"Отбить район: война против армии хозяина ({c.war_ap} ОД, нужна своя банда от {c.need_army}).")
            rows.append([Button(f"Отбить район · {c.war_ap} ОД", "take", style="danger")])
    elif m is not None:
        lines.append(f"👑 Район ничей. Захват: своя банда от {c.need_army} бойцов, банды и корпорации не выше {c.max_meter}%, "
                     f"война за район ({c.war_ap} ОД). Хозяин ставит налог и закон.")
        rows.append([Button(f"Захватить район · {c.war_ap} ОД", "take", style="danger")])
    return lines, rows


def tax_screen(content: Content, holder: dict, district: str) -> Screen:
    c = content.balance.turf
    danger = content.districts[district].danger
    cur = int(holder.get("tax", 0) or 0)
    lines = [style.header("district", "Налог района"),
             "Выше налог — больше денег, но злые улицы кормят банды.", ""]
    rows: list[list[Button]] = []
    for i, name in enumerate(c.tax_names):
        inc = c.tax_income[i] * (6 - danger) if i < len(c.tax_income) else 0
        gang = c.tax_gang[i] if i < len(c.tax_gang) else 0
        lines.append(f"{'▶ ' if i == cur else ''}<b>{name}</b>: +{inc} эдди в месяц" + (f", банды +{gang:g}% в день" if gang else ""))
        rows.append([Button(("✓ " if i == cur else "") + name, "settax", str(i))])
    rows.append([Button(texts.BACK, "open")])
    return Screen("\n".join(lines), rows, name="ctrl")


def law_screen(content: Content, holder: dict) -> Screen:
    cur = str(holder.get("law", ""))
    lines = [style.header("district", "Закон района"), "Закон действует на всех, кто живёт в районе.", ""]
    rows: list[list[Button]] = []
    for lw in content.balance.turf.laws:
        lines.append(f"{'▶ ' if lw.key == cur else ''}<b>{texts.esc(lw.name)}</b>: {texts.esc(lw.desc)}")
        rows.append([Button(("✓ " if lw.key == cur else "") + lw.name, "setlaw", lw.key)])
    rows.append([Button(texts.BACK, "open")])
    return Screen("\n".join(lines), rows, name="ctrl")


def control_screen(content: Content, ch: ActiveCharacter, state: dict[str, dict[str, int]], note: str | None = None,
                   turf: dict[str, dict] | None = None) -> Screen:
    c = eng.cfg(content)
    here = ch.state.district
    m = state.get(here)
    turf = turf or {}
    lines = [style.header("district", f"Улицы: {texts.esc(views.district_name(content, here))}")]
    rows: list[list[Button]] = []
    if m is None:
        lines.append(BLOCK["city"])
    else:
        g, k = m[eng.GANG], m[eng.CORP]
        lines += [f"🏴 Банды       {style.bar(g, 100, 10)} {g}%", f"🏢 Корпорации {style.bar(k, 100, 10)} {k}%",
                  f"<b>{eng.word(g, k).capitalize()}.</b>", ""]
        now: list[str] = []
        if eng.ambush_chance(content, g) > 0:
            extra = eng.ambush_extra(content, g)
            now.append(f"банды нападают на жителей: {round(eng.ambush_chance(content, g) * 100)}% в месяц, "
                       f"опасность {eng.ambush_danger(content, g)}" + (f", толпой (+{extra})" if extra else ""))
        if k > c.tight_from:
            now.append(f"слежка корпораций: розыск растёт ×{eng.heat_mult(content, k):.2f}")
        if eng.sweep_chance(content, k) > 0:
            now.append(f"охрана корпораций зачищает кварталы: {round(eng.sweep_chance(content, k) * 100)}% в месяц")
        lines.append("Сейчас: " + ("; ".join(now) if now else "тихо") + ".")
        lines += ["", "Банды растут каждый день, и тем быстрее, чем больше в городе новых банд. Корпорации растут от "
                  "своих офисов и скупают всё, откуда ушли банды. Кто сильнее, тот вытесняет другого. Держать надо обе стороны.",
                  f"Зачистка банд ({c.cleanse_ap} ОД): бой с бандой района. Банды −{c.cleanse_gang}%, корпорации +{c.cleanse_corp}%.",
                  f"Удар по корпорации ({c.strike_ap} ОД): взлом сети (проверка) или налёт на пост охраны (бой). "
                  f"Корпорации −{c.strike_corp}…{c.strike_fight_corp}%, банды +{c.strike_gang}%, розыск растёт.",
                  "Налёт на банду, налёт на корпорацию, штурм логова или офиса новой корпорации тоже двигают шкалы. "
                  f"Не больше {c.per_day} зачисток и ударов в день."]
        rows.append([Button(f"Зачистка банд · {c.cleanse_ap} ОД", "cleanse", style="danger")])
        rows.append([Button("Взломать сеть корпы", "hack"), Button("Налёт на охрану", "patrol", style="danger")])
        tl, tr = turf_lines(content, ch, turf.get(here), m)
        if tl:
            lines += ["", *tl]
            rows += tr
    lines += ["", *map_lines(content, state, here, turf)]
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="ctrl")


def map_lines(content: Content, state: dict[str, dict[str, int]], here: str = "", turf: dict[str, dict] | None = None) -> list[str]:
    """Owner request 25.10: the map of the city, north to south, who holds each district."""
    out = ["<b>Карта Найт-Сити</b>"]
    counts: dict[str, int] = {}
    for area, row in eng.map_rows(content, state):
        cells = []
        for key, sd, g, k in row:
            counts[sd] = counts.get(sd, 0) + 1
            name = texts.esc(views.district_name(content, key))
            crown = "👑" if turf and key in turf else ""
            cells.append(f"{eng.SIDE_ICON[sd]}{crown} {'<u>' + name + '</u>' if key == here else name} {g}/{k}")
        out.append(f"<i>{area}:</i> " + " · ".join(cells))
    out.append(" ".join(f"{eng.SIDE_ICON[s]} {eng.SIDE_WORD[s]}" for s in ("gang", "gang_up", "corp", "corp_up", "war", "free")))
    out.append("👑 район держит игрок. Цифры: банды/корпорации, %. Подчёркнут твой район.")
    if turf:
        out.append("Хозяева: " + "; ".join(f"{texts.esc(views.district_name(content, d))} — {texts.esc(str(r.get('name', '')))}"
                                          for d, r in turf.items()))
    held = [f"банды {counts.get('gang', 0)}", f"корпорации {counts.get('corp', 0)}", f"война {counts.get('war', 0)}"]
    out.append("Районов держат: " + ", ".join(held) + f", под давлением {counts.get('gang_up', 0) + counts.get('corp_up', 0)}, "
               f"ничьих {counts.get('free', 0)}.")
    return out


@on_screen("ctrl")
async def ctrl_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "city", "open")
    c = ctx.content
    tz = ctx.ui.settings.timezone
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        rng = random.Random()
        r = None
        if action in ("tax", "law"):
            holder = (await turf_svc.book(s)).get(ch.state.district)
            if holder is not None and int(holder.get("owner", 0) or 0) == ch.player_tg_id:
                return tax_screen(c, holder, ch.state.district) if action == "tax" else law_screen(c, holder)
            note = BLOCK["notmine"]
        elif action in ("settax", "setlaw") and arg:
            why = await turf_svc.set_rule(s, c, ch, "tax" if action == "settax" else "law", arg)
            note = BLOCK.get(why, why) if why else ("Налог установлен." if action == "settax" else "Закон объявлен.")
        elif action == "take":
            r = await turf_svc.take(s, c, ch, rng, year, tz)
            if isinstance(r, str):
                note, r = turf_block(c, r), None
        if action == "cleanse":
            r = await svc.cleanse(s, c, ch, rng, year, tz)
        elif action in ("hack", "patrol"):
            r = await svc.strike(s, c, ch, rng, year, tz, "hack" if action == "hack" else "fight")
        if isinstance(r, str):
            note = BLOCK.get(r, r)
        elif isinstance(r, tuple):
            ok, chk, out = r
            note = "\n".join([views.check_line(chk)] + [texts.esc(x) for x in out])
        elif r is not None:
            from app.bot.handlers.combat import step_screen

            return step_screen(c, ch, r)  # type: ignore[arg-type]
        state = await svc.load(s, c)
        turf = await turf_svc.book(s)
    return control_screen(c, ch, state, note, turf)
