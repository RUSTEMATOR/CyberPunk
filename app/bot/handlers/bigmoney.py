"""Screens of big money (owner request, docs/PLAN_ECONOMY.md stage 6).

property -> empire (steps, own megacorp: security, repair, the name)
crime -> influence | corps (the players' megacorps, attack)
/auction -> auction (the lot of the group, bids)
"""

from __future__ import annotations

import random
import time

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot import effects_text, texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, make_ctx, on_screen, on_text
from app.bot.ui.kit import UiKit
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.services import bigmoney as svc
from app.services import game
from app.services.game import ActiveCharacter

router = Router(name="bigmoney")

MSG = {
    svc.R_EDDIES: "Не хватает эдди.",
    svc.R_LOW: "Ставка слишком мала.",
    svc.R_NONE: "Сейчас нечего.",
    svc.R_OWN: "Свою корпорацию атаковать нельзя.",
    svc.R_GONE: "Этого уже нет.",
    svc.R_HAVE: "Уже есть.",
    svc.R_NO_AP: "ОД кончились. Нажми «Прожить месяц».",
    svc.R_COOLDOWN: "Охрана ещё на ушах после твоего прошлого визита. Позже.",
    svc.R_JAIL: "За решёткой этого не сделать.",
    svc.R_NAME: "Нужно название корпорации (до 30 символов).",
    svc.R_STAFF: "Это твоя корпорация: ты в её штате.",
}


def _staff(corp: dict) -> dict:
    from app.services import pcorp

    return pcorp.staff_of(corp)


def _sdv(content: Content, corp: dict) -> int:
    from app.services import pcorp

    return pcorp.staff_dv(content, corp)


def _note(lines: list[str], note: str | None) -> list[str]:
    return lines + ["", note] if note else lines


def _mods(mods: dict) -> str:
    return effects_text.cap(", ".join(effects_text.mods_phrases(mods))) if mods else ""


# -- auction -------------------------------------------------------------------------------------------------------------


async def auction_screen(ctx: Ctx, note: str | None = None) -> Screen:
    async with db.session_scope() as s:
        a = await svc.auction(s)
        ch = await game.get_active_character(s, ctx.user_id)
    lines = [style.header("legends", "Аукцион Найт-Сити")]
    rows: list[list[Button]] = []
    if a is None:
        lines.append("Сейчас торгов нет. Новый лот появится скоро.")
    else:
        left = max(0, int(float(a["end"]) - time.time()))
        need = svc.min_bid(ctx.content, a)
        lines += [f"Лот: <b>{texts.esc(a['name'])}</b> (без аукциона его не купить).",
                  f"Ставок: {a.get('bids', 0)}. Лидирует: {texts.esc(str(a.get('top_name') or 'никто'))}"
                  + (f" — {views.money(int(a['top_amount']))}" if a.get("top_amount") else "") + ".",
                  f"До конца: {left // 3600} ч {left % 3600 // 60} мин. Минимальная ставка: {views.money(need)}.",
                  "<i>Деньги замораживаются сразу; если перебьют — вернутся письмом. Ставка в последний час продлевает "
                  "торги на час.</i>"]
        if ch is not None:
            lines.append(f"У тебя: {views.money(ch.state.eddies)}.")
            rows.append([Button(f"Ставка {need}", "bid", str(need)), Button("Своя сумма", "ask")])
    rows.append([Button(texts.MENU, "menu")])
    return Screen("\n".join(_note(lines, note)), rows, name="auction")


async def _do_bid(ctx: Ctx, amount: int) -> Screen:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        name = f"{ch.state.name} {ch.state.surname}".strip()
        res = await svc.bid(s, ctx.content, ch, ctx.user_id, name, amount, ctx.ui.settings.timezone)
    if res.blocked:
        extra = f" Нужно от {views.money(res.need)}." if res.need else ""
        return await auction_screen(ctx, MSG.get(res.blocked, res.blocked) + extra)
    if res.post:
        ctx.ui.post(res.post, ctx.content.balance.economy.auction.ttl_post_h * 3600)
    return await auction_screen(ctx, f"Ставка {views.money(res.amount)} принята. Ты лидируешь.")


@on_screen("auction")
async def auction_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "bid" and arg and arg.isdigit():
        return await _do_bid(ctx, int(arg))
    if action == "ask":
        return Screen(f"{style.header('legends', 'Аукцион')}\nНапиши в чат сумму ставки в эдди.",
                      [[Button(texts.BACK, "back")]], awaiting_text="auction_bid", name="auction")
    return await auction_screen(ctx)


@on_text("auction_bid")
async def auction_bid_text(ctx: Ctx, text: str) -> Screen | None:
    import re

    m = re.search(r"\d[\d\s]*", text or "")
    if not m:
        return await auction_screen(ctx, "Нужна сумма числом.")
    return await _do_bid(ctx, int(m.group(0).replace(" ", "")))


@router.message(Command("auction"))
async def cmd_auction(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    ctx = await make_ctx(ui, user, message.chat.id)
    await ui.windows.open(message.chat.id, user.id, await auction_screen(ctx))


# -- influence -----------------------------------------------------------------------------------------------------------


def influence_screen(content: Content, ch: ActiveCharacter, year: int, note: str | None = None) -> Screen:
    cfg = content.balance.economy.influence
    st = ch.state
    ap = cfg.ap
    lines = [style.header("crime", "Влияние"), f"Эдди: {views.money(st.eddies)} · Розыск: {st.heat} · ОД: {st.ap}", "",
             "Деньги, которые меняют город для всех. Каждое дело стоит и ОД: "
             f"война {ap.get('war', 0)}, тишина {ap.get('silence', 0)}, фиксер {ap.get('fixer', 0)}, "
             f"вечер {ap.get('charity', 0)}, пиар {ap.get('pr', 0)}.",
             "",
             f"<b>Оплатить войну против банды</b> · {views.money(cfg.war_price)}: {cfg.war_days} дней войны, "
             "громкий пост в группу.",
             f"<b>Купить тишину</b> · {views.money(cfg.silence_per_heat)} за единицу розыска: розыск в ноль.",
             f"<b>Подмазать фиксера района</b> · {views.money(cfg.fixer_bribe)}: отношения +{cfg.fixer_rel}.",
             f"<b>Благотворительный вечер</b> · {views.money(cfg.charity_price)}: статус, слава, человечность.",
             f"<b>Пиар-кампания</b> · {views.money(cfg.pr_price)}: слава и статус."]
    rows: list[list[Button]] = []
    district = content.districts.get(st.district)
    gangs = [g for g in (district.gangs if district else []) if g in content.available("gangs", year)]
    for g in gangs:
        rows.append([Button(f"Война: {content.gangs[g].name}"[:24], "war", g)])
    if st.heat:
        rows.append([Button(f"Тишина · {cfg.silence_per_heat * st.heat}"[:24], "silence")])
    rows.append([Button("Фиксер", "fixer"), Button("Вечер", "charity"), Button("Пиар", "pr")])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_note(lines, note)), rows, name="influence")


@on_screen("influence")
async def influence_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "crime")
    c = ctx.content
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action in ("war", "silence", "fixer", "charity", "pr"):
            res = await svc.influence(s, c, ch, year, ctx.ui.settings.timezone, action, arg)
            if res.blocked:
                note = MSG.get(res.blocked, res.blocked)
            else:
                note = res.text + f" −{views.money(res.price)}."
    return influence_screen(c, ch, year, note)


# -- empire and own megacorp -----------------------------------------------------------------------------------------------


async def empire_screen(ctx: Ctx, ch: ActiveCharacter, note: str | None = None) -> Screen:
    c = ctx.content
    st = ch.state
    steps = c.balance.economy.empire
    n = int(st.flags.get("empire", 0) or 0)
    lines = [style.header("status", "Империя"), f"Эдди: {views.money(st.eddies)}", "",
             "Большие цели: каждая ступень даёт титул, бонусы и доход каждую неделю, но требует содержания. "
             "Не заплатишь содержание — верхняя ступень рушится. Верхнюю ступень можно продать за "
             f"{c.balance.economy.empire_sell_pct}% цены."]
    rows: list[list[Button]] = []
    for i, step in enumerate(steps):
        mark = "✅" if i < n else ("▶" if i == n else "▫")
        lines.append("")
        lines.append(f"{mark} <b>{texts.esc(step.name)}</b> · {views.money(step.price)}, доход "
                     f"{views.money(step.income[0])}–{views.money(step.income[1])}/нед., содержание {views.money(step.weekly)}/нед.\n"
                     f"Титул «{texts.esc(step.title)}». {texts.esc(_mods(step.mods))}\n<i>{texts.esc(step.description)}</i>")
    if n < len(steps):
        step = steps[n]
        if step.key == "megacorp":
            rows.append([Button("Основать корпорацию", "found")])
        else:
            rows.append([Button(f"Купить: {step.name}"[:24], "buy")])
    if n > 0:
        top = steps[n - 1]
        rows.append([Button(f"Продать: {top.name}"[:24], "sell", style="danger")])
    async with db.session_scope() as s:
        corp = (await svc.corps(s)).get(str(ctx.user_id))
    if corp is not None:
        cfg = c.balance.economy.megacorp
        lvl = int(corp.get("security", 0) or 0)
        lines += ["", f"🏢 <b>{texts.esc(corp['name'])}</b>: целостность {corp['integrity']}/{cfg.integrity_max}, "
                      f"охрана {lvl} (сложность атаки {cfg.security_dv[min(lvl, len(cfg.security_dv) - 1)] + _sdv(c, corp)})."]
        if lvl + 1 < len(cfg.security_price):
            rows.append([Button(f"Охрана → {lvl + 1}", "sec")])
        if corp["integrity"] < cfg.integrity_max:
            rows.append([Button("Ремонт", "repair")])
        from app.services import pcorp

        staff = pcorp.staff_of(corp)
        lines.append(f"Штат: {len(staff)}/{pcorp.staff_cap(c, corp)}. Нанимай игроков: зарплата с тебя, выручка от их поручений тебе.")
        rows.append([Button(f"Сотрудники ({len(staff)})", "staff"), Button("Отделы корпорации", "ups")])
        rows.append([Button("⚔️ Силовые операции", "force", style="danger")])  # owner request 26.10
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_note(lines, note)), rows, name="empire")


def corpups_screen(c: Content, ch: ActiveCharacter, corp: dict, note: str | None = None) -> Screen:
    """Owner request 25.10: the departments of the own megacorp."""
    have = corp.get("up") or {}
    lines = [style.header("status", f"Отделы «{texts.esc(corp['name'])}»"), f"Эдди: {views.money(ch.state.eddies)}", ""]
    rows: list[list[Button]] = []
    for u in c.balance.economy.megacorp.upgrades:
        lvl = int(have.get(u.key, 0) or 0)
        top = len(u.prices)
        price = u.prices[lvl] if lvl < top else None
        lines.append(f"<b>{texts.esc(u.name)}</b> · уровень {lvl}/{top}" + (f" · следующий {views.money(price)}" if price else " · максимум"))
        lines.append(f"<i>{texts.esc(u.text)}</i> {texts.esc(_up_effect(u))}")
        lines.append("")
        if price:
            rows.append([Button(f"{u.name} → {lvl + 1}"[:24], "up", u.key)])
    if note:
        lines.append(note)
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="corpups")


def _up_effect(u) -> str:  # noqa: ANN001
    parts = []
    if u.income_pct:
        parts.append(f"доход империи +{u.income_pct}%")
    if u.sec_dv:
        parts.append(f"сложность штурма +{u.sec_dv}")
    if u.dmg_cut_pct:
        parts.append(f"урон по башне −{u.dmg_cut_pct}%")
    if u.mods:
        parts.append(_mods(u.mods))
    if u.soldiers:
        parts.append(f"+{u.soldiers} боец в месяц")
    if u.staff:
        parts.append(f"мест в штате +{u.staff}")
    return ("За уровень: " + ", ".join(p for p in parts if p) + ".") if parts else ""


@on_screen("corpups")
async def corpups_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if action == "back":
            return await empire_screen(ctx, ch)
        note = None
        if action == "up" and arg:
            year = await game.current_year(s, c)
            why, price, name = await svc.corp_upgrade(s, c, ch, year, ctx.ui.settings.timezone, arg)
            note = MSG.get(why, why) if why else f"{texts.esc(name)}: новый уровень. −{views.money(price)}."
        corp = (await svc.corps(s)).get(str(ctx.user_id))
    if corp is None:
        return await empire_screen(ctx, ch, "Своей корпорации нет.")
    return corpups_screen(c, ch, corp, note)


@on_screen("empire")
async def empire_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "property")
    c = ctx.content
    if action == "staff":
        return await goto(ctx, "pcstaff", "open")
    if action == "ups":
        return await goto(ctx, "corpups", "open")
    if action == "force":  # owner request 26.10: the megacorp's force on the city
        return await goto(ctx, "cforce", "open")
    if action == "found":
        return Screen(f"{style.header('status', 'Своя мегакорпорация')}\nНапиши в чат название корпорации "
                      f"(до {c.balance.economy.megacorp.name_max_chars} символов). Его увидит весь город.",
                      [[Button(texts.BACK, "cancel")]], awaiting_text="corp_name", name="empire")
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        tz = ctx.ui.settings.timezone
        if action == "buy":
            why, name = await svc.buy_step(s, c, ch, year, tz)
            note = MSG.get(why, why) if why else f"«{texts.esc(name)}» — теперь твоё. Город заметил."
        elif action == "sell":
            why, name, back = await svc.sell_step(s, c, ch, year, tz)
            note = MSG.get(why, why) if why else f"«{texts.esc(name)}» продано. +{views.money(back)}."
        elif action == "sec":
            why, price = await svc.upgrade_security(s, c, ch, year, tz)
            note = MSG.get(why, why) if why else f"Охрана усилена. −{views.money(price)}."
        elif action == "repair":
            why, price = await svc.repair(s, c, ch, year, tz)
            note = MSG.get(why, why) if why else f"Ремонт и новые турели. −{views.money(price)}."
    return await empire_screen(ctx, ch, note)


@on_text("corp_name")
async def corp_name_text(ctx: Ctx, text: str) -> Screen | None:
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        why, _ = await svc.buy_step(s, c, ch, year, ctx.ui.settings.timezone, corp_name=text.strip())
    note = MSG.get(why, why) if why else f"Корпорация «{texts.esc(text.strip())}» основана. Башня строится."
    return await empire_screen(ctx, ch, note)


# -- the players' megacorps -------------------------------------------------------------------------------------------------


async def corps_screen(ctx: Ctx, note: str | None = None) -> Screen:
    c = ctx.content
    cfg = c.balance.economy.megacorp
    async with db.session_scope() as s:
        all_ = await svc.corps(s)
    lines = [style.header("crime", "Мегакорпорации игроков"), "",
             f"Можно ударить по чужой корпорации: штурм (РЕФЛ + стрельба) или взлом (ИНТ + нетраннинг) против её охраны, "
             f"{cfg.attack_ap} ОД. Успех — урон целостности, {cfg.attack_reward} эдди, слава и розыск; провал — ранение. "
             "Целостность в ноль — корпорация разнесена."]
    rows: list[list[Button]] = []
    if not all_:
        lines += ["", "Пока ни одной. Империя ждёт своего героя (или жертву)."]
    for owner, corp in all_.items():
        lvl = int(corp.get("security", 0) or 0)
        lines += ["", f"🏢 <b>{texts.esc(corp['name'])}</b> — {texts.esc(str(corp.get('owner_name', '')))}. "
                      f"Целостность {corp['integrity']}/{cfg.integrity_max}, охрана {lvl}, штат {len(_staff(corp))}."]
        if owner != str(ctx.user_id):
            rows.append([Button(f"Штурм: {corp['name']}"[:24], "storm", owner),
                         Button("Взлом", "hack", owner)])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_note(lines, note)), rows, name="corps")


@on_screen("corps")
async def corps_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "crime")
    note = None
    if action in ("storm", "hack") and arg:
        c = ctx.content
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            res = await svc.attack(s, c, ch, year, ctx.ui.settings.timezone, arg, action, random.Random())
        if res.blocked:
            note = MSG.get(res.blocked, res.blocked)
        else:
            head = views.check_line(res.check) if res.check is not None else ""
            if res.razed:
                note = f"{head}\nКорпорация «{texts.esc(res.corp)}» разнесена в ноль."
            elif res.ok:
                note = f"{head}\nОхрана пробита: целостность «{texts.esc(res.corp)}» −{res.damage}."
            else:
                note = f"{head}\nОхрана оказалась лучше. Уходишь с ранением."
            if res.applied is not None:
                note += "\n" + "\n".join(views.effects_lines(res.applied, c))
    return await corps_screen(ctx, note)
