"""The window of a raid on a rival megacorp (owner request 25.10): «Криминал» → «Налёт на корпорацию»."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.clock import kyiv_date
from app.content.loader import Content
from app.db import session as db
from app.engine import challenge as chal
from app.services import corpraid as svc
from app.services import game
from app.services.game import ActiveCharacter

BLOCK = {"member": "Налёт на корпорацию — для своих (работа в мегакорпорации, место в совете, своя корпорация) "
                   "или для тех, кого город уже не меряет: после первого пройденного вызова.",
         "gone": "Такой цели нет.", "jail": "Из камеры не напасть.", "busy": "Сначала разберись с тем, что уже ждёт.",
         "today": "На эту корпорацию сегодня уже три налёта. Завтра.", "ap": "Не хватает ОД.",
         "tower": "Башня откроется после первого пройденного вызова.",
         "down": "Эта корпорация уже лежит. Добивать нечего, пока не встанет."}


async def list_screen(s, content: Content, ch: ActiveCharacter, year: int, tz: str,  # noqa: ANN001
                      note: str | None = None) -> Screen:
    from app.services import corpstand

    st = ch.state
    mem = await svc.allowed(s, content, ch)
    lines = [style.header("war", "Налёт на корпорацию")]
    rows: list[list[Button]] = []
    if mem is None:
        lines.append(BLOCK["member"])
    else:
        sc = corpstand.cfg(content)
        lines += [f"Ты {mem[1]}. Склад, лаборатория или башня: настоящий бой с охраной, на каждую корпорацию до трёх "
                  "налётов в сутки.",
                  f"Каждая победа бьёт по стойкости корпорации: склад −{sc.dmg['depot']}, лаборатория −{sc.dmg['lab']}, "
                  f"башня −{sc.dmg['tower']} (больше на высоких ступенях). За сутки корпорация восстанавливает "
                  f"{sc.regen}. На нуле она рушится на {sc.down_days} дн.: город узнаёт, район уходит из её рук, "
                  f"все, кто бил, делят {views.money(sc.fall_bonus)} эдди трофеев по доле урона.",
                  "Цель запомнит: отношение падает, розыск растёт, иногда служба безопасности назначает охотника.", ""]
        today = kyiv_date(tz)
        cap = svc.cfg(content).per_day
        b = await corpstand.book(s)
        lines.append("<b>Мегакорпорации</b>")
        for k in svc.targets(content, st, year):
            corp = content.corps[k]
            r = corpstand.rec_of(content, b, k)
            n = svc.raids_today(st, k, today)
            mine = int(r["dmg"].get(str(ch.player_tg_id), 0))
            lines.append(f"• {texts.esc(corp.name)} · {corpstand.bar(content, r)} · налётов сегодня {n}/{cap}"
                         + (f" · твой урон {mine}" if mine else ""))
            if not corpstand.down(r):
                rows.append([Button(corp.name[:24], "pick", k)])
        dyns = await svc.dyn_corps(s)
        if dyns:
            dc = svc.cfg(content)
            lines += ["", f"<b>Новые корпорации города</b> · {dc.dyn_ap} ОД за удар, каждый снимает {dc.dyn_power} силы, "
                          "на нуле корпорации нет"]
            for f in dyns:
                n = svc.raids_today(st, f"dyn{f['id']}", today)
                where = content.districts.get(str(f.get("district", "")))
                lines.append(f"• «{texts.esc(str(f['name']))}» · {where.name if where else ''} · сила {f['power']}/100 · "
                             f"ударов сегодня {n}/{cap}")
                rows.append([Button(f"Удар: {f['name']}"[:24], "dyn", str(f["id"]), style="danger")])
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="craid")


def sites_screen(content: Content, ch: ActiveCharacter, key: str, ladder: int) -> Screen:
    c = svc.cfg(content)
    corp = content.corps[key]
    lines: list[str] = []
    rows: list[list[Button]] = []
    for site in svc.SITES:
        if site == "tower" and ladder < 2:
            lines.append(f"<b>{svc.SITE_RU[site]}</b> · закрыто\n{BLOCK['tower']}")
            continue
        lv = svc.site_level(site, ladder)
        lo, hi = c.loot[site]
        m = max(1, chal.mult(lv) // 2) if lv >= 2 else 1
        power = f"враги ×{chal.mult(lv)} (ступень {lv})" if lv >= 2 else (
            f"опасность {svc.depot_danger(content, ch.state)}" if site == "depot" else "опасность 5")
        lines.append(f"<b>{svc.SITE_RU[site]}</b> · {c.ap[site]} ОД · {power}\n{svc.SITE_DESC[site]}. "
                     f"Добыча {views.money(min(c.loot_cap, lo * m))}–{views.money(min(c.loot_cap, hi * m))} эдди.")
        if site == "tower":  # owner request 26.10: the tower is a dungeon now (services/dungeon.py)
            from app.services import dungeon

            dc = dungeon.cfg(content)
            lines[-1] = (f"<b>Башня: штурм</b> · вход {dc.ap_enter} ОД · пять этажей, два босса\nДанж: этажи с выбором пути, "
                         f"тревога, начальник охраны и глава СБ в пентхаусе. Соло или отрядом до {dc.party_max}.")
            rows.append([Button("Башня: штурм", "tower", key, style="danger")])
            continue
        rows.append([Button(f"{svc.SITE_RU[site]} · {c.ap[site]} ОД", "go", f"{key}:{site}", style="danger")])
    rows.append([Button(texts.BACK, "list"), Button(texts.MENU, "menu")])
    head = style.header("war", f"Налёт на {texts.esc(corp.name)}")
    return Screen(head + "\n\n" + "\n\n".join(lines), rows, name="craid")


@on_screen("craid")
async def craid_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "crime")
    if action == "tower" and arg:  # owner request 26.10: the tower dungeon
        return await goto(ctx, "tdun", "open", arg)
    c = ctx.content
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "pick" and arg in svc.targets(c, ch.state, year):
            return sites_screen(c, ch, arg, await svc.ladder(s, ch))
        if action == "dyn" and arg and arg.isdigit():  # owner request 25.10: a corp that came and goes
            r = await svc.strike_dyn(s, c, ch, random.Random(), year, tz, int(arg))
            if not isinstance(r, str):
                from app.bot.handlers.combat import step_screen

                return step_screen(c, ch, r)  # type: ignore[arg-type]
            return await list_screen(s, c, ch, year, tz, BLOCK.get(r, r))
        if action == "go" and arg and ":" in arg:
            key, site = arg.split(":", 1)
            r = await svc.raid(s, c, ch, random.Random(), year, tz, key, site)
            if not isinstance(r, str):
                from app.bot.handlers.combat import step_screen

                return step_screen(c, ch, r)  # type: ignore[arg-type]
            return await list_screen(s, c, ch, year, tz, BLOCK.get(r, r))
        return await list_screen(s, c, ch, year, tz)
