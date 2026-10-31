"""The Blackwall window (owner request 24.10): city -> «Чёрный заслон». The taint, the Avatar chain, tainting the city,
the cult «Церковь Тихого Шума» and its arsenal. See engine/blackwall.py and services/blackwall.py."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import blackwall as eng
from app.services import blackwall as svc
from app.services import game
from app.services.game import ActiveCharacter

BLOCKED = {
    "jail": "Из камеры Заслон не слышно: деку и импланты связи забрали на входе.",
    "abroad": "За границей другой Заслон и другие голоса. Вернись в Найт-Сити.",
    "silent": "Заслон тебя пока не знает. Он найдёт тебя сам: на нырке в Сеть, через проповедника на улице, через шёпот в старых серверах.",
    "done": "Цепочка пройдена.",
    "needs": "Заслон ещё не готов принять тебя дальше. Смотри, чего не хватает.",
    "day": "Сегодня уже было. Заслон умеет ждать: до завтра.",
    "month": "В этом месяце шаг уже сделан. Проживи месяц, и Заслон позовёт снова.",
    "none": "Такого нет.",
    "deck": "Для этого нужна дека.",
    "cult": "Это только для Церкви Тихого Шума.",
    "max": "Этот район уже отравлен до предела.",
    "ap": "ОД кончились.",
    "avatar": "Это могут только аватары Заслона.",
    "member": "Ты уже в Церкви.",
    "eddies": "Не хватает эдди.",
    "rank": "Ранг в Церкви пока мал.",
    "have": "Это у тебя уже есть.",
    "avatar_bound": "Аватара не очистить: корона вросла в нейропорт.",
    "clean": "Чистить нечего.",
}


def _ok(flag: bool) -> str:
    return "✅" if flag else "▫️"


def _note(r: svc.Result, content: Content) -> str:
    note = texts.esc(r.text) if r.text else ""
    if r.check is not None:
        note = views.check_line(r.check) + "\n" + note  # type: ignore[arg-type]
    if r.applied is not None:
        extra = "\n".join(views.effects_lines(r.applied, content))
        if extra:
            note += "\n" + extra
    if r.level:
        note += f"\n⚫ Порча района: {r.level}/{eng.cfg(content).level_max}"
    return note


def main_screen(content: Content, ch: ActiveCharacter, city: dict[str, int], note: str | None = None) -> Screen:
    st = ch.state
    c = eng.cfg(content)
    r = eng.rot(st)
    t = eng.tier(st, content)
    total = svc.total(city)
    here = city.get(st.district, 0)
    lines = [style.header("glitches", "Чёрный заслон"),
             f"⚫ Порча: {style.bar(r, c.rot_max)} {r}/{c.rot_max} · <b>{c.tier_names[min(t, len(c.tier_names) - 1)]}</b>",
             f"Мощь Заслона: <b>{eng.power(st, content)}</b> (скрипты {eng.scripts(st)}, оружие {eng.weapons(st, content)}, "
             f"ранг в Церкви {eng.cult_rank(st, content)})",
             f"Порча города: <b>{total}</b> · твой район: {here}/{c.level_max} · ОД {st.ap}",
             ""]
    wh = eng.weekly_fx(st, content).get("humanity", 0)
    lines.append("<i>Порча даёт силу: лёд, RAM, урон, бой. И забирает: настроение, людей вокруг"
                 + (f", человечность {wh} в неделю" if wh else "") + ". Порча растёт от скриптов и оружия Заслона, "
                 "нырков к нему, бдений Церкви, отравления районов и шагов цепочки.</i>")
    rows: list[list[Button]] = []
    if eng.is_avatar(st):
        lines += ["", "👁 <b>Ты Аватар Чёрного заслона.</b> Корона из помех вросла в нейропорт. Дверь открыта: "
                  "«Шагнуть за Заслон», и ты станешь ИИ. Каждый день можно устроить прорыв в своём районе."]
    else:
        step = eng.next_step(st, content, total)
        lines += ["", f"<b>Цепочка аватаров: {eng.stage(st)}/{len(c.steps)}</b>. Только аватар Заслона может оставить "
                  "тело и стать ИИ."]
        if step is not None:
            lines.append(f"Следующий шаг: <b>{texts.esc(step.title)}</b>")
            for n in step.needs:
                lines.append(f"  {_ok(n.ok)} {n.label}: {n.have}/{n.need}")
            lines.append(f"<i>Один шаг за прожитый месяц, {c.follow_ap} ОД. Отказ оставляет шаг открытым.</i>")
            if step.ready:
                rows.append([Button(f"👁 Следовать зову · {c.follow_ap} ОД", "follow", style="danger")])
    if note:
        lines += ["", note]
    lines += ["", "<b>Отравить район</b> (тот, где живёшь): каждый уровень порчи района ближе к прорыву, а весь город "
              "считается для цепочки."]
    for key, w in c.corrupt.items():
        need = {"deck": " · нужна дека", "cult": " · только Церковь"}.get(w.needs or "", "")
        lines.append(f"• {texts.esc(w.name)} · {views_stat(w.stat)}{need}")
        rows.append([Button(f"{w.name.split()[0]} · {w.ap} ОД", "corrupt", key)])
    if eng.is_avatar(st):
        rows.append([Button(f"Прорыв · {c.surge_ap} ОД", "surge", style="danger")])
        rows.append([Button("Шагнуть за Заслон", "ascend", style="danger")])
    rows.append([Button("Церковь Тихого Шума", "cult")])
    if r > 0 and not eng.is_avatar(st):
        rows.append([Button(f"Очищение · {c.cleanse_eddies}", "cleanse")])
    rows.append([Button(texts.BACK, "city"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="bw")


STAT_RU = {"BODY": "ТЕЛО", "REF": "РЕФЛ", "TECH": "ТЕХ", "INT": "ИНТ", "COOL": "ХЛАД", "EMP": "ЭМП", "LUCK": "УДАЧА"}


def views_stat(stat: str) -> str:
    return STAT_RU.get(stat, stat)


def cult_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    c = eng.cfg(content)
    rank = eng.cult_rank(st, content)
    lines = [style.header("glitches", "Церковь Тихого Шума"),
             "<i>Подвалы под закрытыми кинотеатрами, серые балахоны, провода вместо чёток. Они слушают белый шум и "
             "верят, что за Заслоном ждёт тишина, в которой нет боли.</i>", ""]
    rows: list[list[Button]] = []
    if rank == 0:
        lines.append("Ты не в общине. Если Заслон тебя знает, двери откроются.")
        rows.append([Button("Найти Церковь", "join")])
    else:
        fav = eng.favor(st)
        nxt = next((n for n in c.favor_ranks if n > fav), None)
        lines += [f"Ранг: <b>{texts.esc(eng.rank_name(content, rank))}</b> ({rank}/{len(c.favor_ranks)}) · милость {fav}"
                  + (f", до следующего ранга {nxt}" if nxt is not None else ""),
                  f"Бдение: порча +{c.vigil_rot}, милость +{c.vigil_favor}, человечность и настроение вниз, раз в сутки.",
                  f"Десятина: {views.money(c.tithe_eddies)} → милость +{c.tithe_favor}.",
                  "Ранг открывает арсенал Церкви, «Проповедь шума» и лишние очки мощи Заслона."]
        rows.append([Button(f"Бдение · {c.vigil_ap} ОД", "vigil"), Button(f"Десятина · {c.tithe_eddies}", "tithe")])
        rows.append([Button("Арсенал Церкви", "shop")])
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "open"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="bw")


def shop_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    c = eng.cfg(content)
    rank = eng.cult_rank(st, content)
    lines = [style.header("glitches", "Арсенал Церкви"),
             f"Твой ранг: {rank} · эдди {views.money(st.eddies)}. Скрипты и оружие из-за Заслона: огромная сила, "
             "порча и человечность в уплату.", ""]
    rows: list[list[Button]] = []
    owned_cw = {cw.key for cw in st.cyberware}
    owned_w = {i.key for i in st.inventory if i.kind == "weapon"}
    for key, it in c.shop.items():
        if it.kind == "cyberware":
            d = content.cyberware.get(it.key)
            have = it.key in owned_cw
        else:
            d = content.weapons.get(it.key)
            have = it.key in owned_w
        if d is None:
            continue
        mark = "✅" if have else ("▫️" if rank >= it.rank else "🔒")
        extra = f" · урон {d.damage}" if it.kind == "weapon" else ""
        lines.append(f"{mark} <b>{texts.esc(d.name)}</b> · ранг {it.rank} · {views.money(it.price)} · порча +{it.rot}{extra}\n"
                     f"    <i>{texts.esc(d.description)}</i>")
        if not have and rank >= it.rank:
            short = d.name.replace("Скрипт Заслона ", "").replace("«", "").replace("»", "")
            rows.append([Button(f"{short} · {it.price}"[:24], "buy", key)])
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "cult"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="bw")


@on_screen("bw")
async def bw_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    if action == "menu":
        return await menu_screen(ctx)
    if action == "city":
        return await goto(ctx, "city", "open")
    if action == "ascend":
        return await goto(ctx, "net", "ascend")
    tz = ctx.ui.settings.timezone
    if action == "follow":
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            r = await svc.follow(s, c, ch, tz)
            if not r.event:
                return main_screen(c, ch, await svc.city(s), BLOCKED.get(r.blocked or "none", ""))
        return await goto(ctx, "evchoice", "resume")
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        rng = random.Random()
        if action == "cult":
            return cult_screen(c, ch)
        if action == "shop":
            return shop_screen(c, ch)
        if action in ("join", "vigil", "tithe"):
            fn = {"join": svc.join, "vigil": svc.vigil, "tithe": svc.tithe}[action]
            r = await fn(s, c, ch, rng, year, tz)
            return cult_screen(c, ch, BLOCKED.get(r.blocked, "") if r.blocked else _note(r, c))
        if action == "buy" and arg:
            r = await svc.buy(s, c, ch, rng, year, tz, arg)
            return shop_screen(c, ch, BLOCKED.get(r.blocked, "") if r.blocked else _note(r, c))
        note = None
        if action == "corrupt" and arg:
            r = await svc.corrupt(s, c, ch, rng, year, tz, arg)
            note = BLOCKED.get(r.blocked, "") if r.blocked else _note(r, c)
        elif action == "surge":
            r = await svc.surge(s, c, ch, rng, year, tz)
            note = BLOCKED.get(r.blocked, "") if r.blocked else _note(r, c)
        elif action == "cleanse":
            r = await svc.cleanse(s, c, ch, rng, year, tz)
            note = BLOCKED.get(r.blocked, "") if r.blocked else _note(r, c)
        return main_screen(c, ch, await svc.city(s), note)
