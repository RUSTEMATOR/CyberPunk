"""The garage in the window (owner request): your ride, the dealer, theft, tuning, repairs, street races.

garage -> vdealer (tabs by kind, pages) | vrace (tiers) | vsell (confirm). Entry: «Гараж» on the property screen.
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.handlers.week import death_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, has_screen, on_screen
from app.bot.ui.screen import Button, Screen, list_rows, pack
from app.content.loader import Content
from app.content.schemas import Vehicle
from app.db import session as db
from app.services import game
from app.services import vehicles as svc
from app.services.game import ActiveCharacter

KIND_TABS = {"bike": "Мото", "car": "Авто", "truck": "Грузовые", "aero": "Аэродины", "joke": "Приколы"}
PAGE = 6

BLOCKED = {
    svc.BLOCK_HAVE: "У тебя уже есть тачка. Сначала продай её.",
    svc.BLOCK_NONE: "Тачки нет.",
    svc.BLOCK_EDDIES: "Не хватает эдди.",
    svc.BLOCK_CRED: "Продавец тебя не знает: мало уличной репутации.",
    svc.BLOCK_MAX: "Больше тюнинговать некуда.",
    svc.BLOCK_GONE: "Этого уже нет.",
    svc.BLOCK_CLEAN: "Тут нечего делать: всё и так в порядке.",
    svc.BLOCK_NO_AP: "Не хватает очков действий.",
    svc.BLOCK_JAIL: "Из камеры машину не водят.",
    svc.BLOCK_FULL: "Гараж полон. Продай что-нибудь из парка.",
}


def _stats(v: Vehicle) -> str:
    return f"скорость {v.speed}, броня {v.armor}, стиль {v.style}"


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


def garage_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    cfg = content.balance.vehicles
    st = ch.state
    lines = [style.header("move", "Гараж"), f"Эдди: {views.money(st.eddies)} · ОД: {st.ap}", ""]
    rows: list[list[Button]] = []
    r, v = svc.ride(ch), svc.model(content, ch)
    if r and v:
        tune, dmg = int(r.get("tune", 0) or 0), int(r.get("dmg", 0) or 0)
        lines.append(f"<b>{texts.esc(v.name)}</b>" + (" · <i>угнана</i>" if r.get("stolen") else ""))
        lines.append(f"<i>{texts.esc(v.description)}</i>")
        lines.append(f"Скорость {svc.speed(content, ch)} (тюнинг {tune}/{cfg.tune_max}), броня {v.armor}, стиль {v.style}")
        lines.append(f"Повреждения: {style.bar(dmg, 100)} {dmg}%")
        lines.append("Тачка помогает уходить от погонь и выглядеть солидно; гонки приносят деньги и славу.")
        if r.get("stolen"):
            lines.append("Угнанная тачка: розыск спадает медленнее, пока не перебьёшь номера.")
        rows.append([Button("Гонки", "races")])
        if tune < cfg.tune_max:
            rows.append([Button(f"Тюнинг ({views.money(svc.tune_cost(content, v, tune))})", "tune")])
        if dmg:
            rows.append([Button(f"Ремонт ({views.money(svc.repair_cost(content, v, dmg))})", "repair")])
        if r.get("stolen"):
            rows.append([Button(f"Перебить номера ({views.money(cfg.renumber_cost)})", "renumber")])
        rows.append([Button(f"Продать ({views.money(svc.sell_price(content, v, r))})", "sell")])
    else:
        lines.append("Своей тачки нет. Можно купить в автосалоне или угнать с улицы (1 ОД, проверка РЕФЛ + вождение, "
                     f"сложность {cfg.steal_dv}). Угнанную продать дёшево, а розыск с ней спадает медленнее.")
    g = svc.parked(ch)  # owner request 24.10: the fleet; a new ride parks the old one here
    lines += ["", f"<b>Парк</b> ({len(g)}/{cfg.garage_max}): новая тачка ставит старую сюда, а не продаёт."]
    if not g:
        lines.append("<i>Пусто.</i>")
    for i, pr in enumerate(g):
        pv = content.vehicles.get(str(pr.get("key")))
        if pv is None:
            continue
        price = svc.sell_price(content, pv, pr)
        lines.append(f"• {texts.esc(pv.name)} · скорость {pv.speed}, повреждения {int(pr.get('dmg', 0) or 0)}%"
                     + (" · угнана" if pr.get("stolen") else "") + f" · продать за {views.money(price)}")
        short = pv.name if len(pv.name) <= 14 else pv.name[:13] + "…"
        rows.append([Button(f"Сесть: {short}"[:24], "drive", str(i)),
                     Button(f"Продать · {views.money(price)}"[:24], "sellp", str(i))])
    rows.append([Button("Автосалон", "dealer"), Button("Угнать себе тачку", "steal")])
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="garage")


def dealer_screen(content: Content, ch: ActiveCharacter, year: int, tab: str | None = None, page: int = 0,
                  note: str | None = None) -> Screen:
    items = svc.dealer(content, year)
    tabs = [k for k in KIND_TABS if any(v.kind == k for v in items)]
    if tab not in tabs:
        tab = tabs[0] if tabs else None
    shown_all = [v for v in items if v.kind == tab]
    pages = max(1, -(-len(shown_all) // PAGE))
    page = max(0, min(page, pages - 1))
    shown = shown_all[page * PAGE:(page + 1) * PAGE]
    lines = [style.header("move", "Автосалон" + (f": {KIND_TABS[tab]}" if tab else "")),
             f"Эдди: {views.money(ch.state.eddies)}" + (f" · Стр. {page + 1}/{pages}" if pages > 1 else "")]
    buy = [v for v in shown if ch.state.street_cred >= v.min_street_cred and ch.state.eddies >= v.price]
    numbered, rows = list_rows([v.name for v in buy], "buy", [v.key for v in buy], content.balance.ui.button_chars)
    for v in shown:
        extra = f" · нужна репутация {v.min_street_cred}" if ch.state.street_cred < v.min_street_cred else ""
        num = f"{buy.index(v) + 1}. " if numbered and v in buy else ""
        lines.append(f"\n{num}<b>{texts.esc(v.name)}</b> · {views.money(v.price)}{extra}\n{_stats(v)}\n<i>{texts.esc(v.description)}</i>")
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(Button(f"« Стр. {page}", "pg", str(page - 1)))
        if page < pages - 1:
            nav.append(Button(f"Стр. {page + 2} »", "pg", str(page + 1)))
        rows.append(nav)
    if len(tabs) > 1:
        rows += pack([Button(("• " if t == tab else "") + KIND_TABS[t], "tab", t) for t in tabs],
                     content.balance.ui.button_chars, max_per_row=3)
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "garage")])
    return Screen("\n".join(lines), rows, name="vdealer", state={"tab": tab, "page": page})


def races_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    lines = [style.header("move", "Уличные гонки"),
             f"Проверка: РЕФЛ + вождение + (скорость тачки − 5). Твоя скорость: {svc.speed(content, ch)}.",
             "Победа удваивает ставку и даёт славу. Поражение забирает ставку, иногда с аварией.", ""]
    rows = []
    for t in svc.race_tiers(content):
        cred = f", нужна репутация {t.min_street_cred}" if t.min_street_cred else ""
        lines.append(f"<b>{texts.esc(t.name)}</b>: сложность {t.dv}, ставка {views.money(t.stake)}, слава +{t.fame}{cred}")
        rows.append([Button(f"{t.name} ({views.money(t.stake)})", "go", t.key)])
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "garage")])
    return Screen("\n".join(lines), rows, name="vrace")


async def open_garage(ctx: Ctx, note: str | None = None) -> Screen:
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    return garage_screen(ctx.content, ch, note)


async def _run(ctx: Ctx, fn, *args):  # noqa: ANN001, ANN202
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return None, None
        year = await game.current_year(s, ctx.content)
        res = await fn(s, ctx.content, ch, random.Random(), year, ctx.ui.settings.timezone, *args)
    return ch, res


@on_screen("garage")
async def garage_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "property")
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action == "dealer":
        return dealer_screen(c, ch, year)
    if action == "races":
        return races_screen(c, ch)
    if action in ("drive", "sellp") and arg and arg.isdigit():
        ch, res = await _run(ctx, svc.drive if action == "drive" else svc.sell_parked, int(arg))
        if ch is None:
            return await menu_screen(ctx)
        if res.blocked:
            return garage_screen(c, ch, BLOCKED.get(res.blocked, res.blocked))
        name = texts.esc(c.vehicles[res.got].name) if res.got in c.vehicles else "тачка"
        return garage_screen(c, ch, f"Ты за рулём: {name}." if action == "drive" else f"Продано: {name}, {views.money(res.amount)}.")
    if action == "sell":
        r, v = svc.ride(ch), svc.model(c, ch)
        if not r or not v:
            return garage_screen(c, ch)
        return Screen(f"Продать {texts.esc(v.name)} за {views.money(svc.sell_price(c, v, r))}?",
                      [[Button("Да, продать", "yes"), Button(texts.CANCEL, "no")]], name="vsell")
    fns = {"tune": svc.tune, "repair": svc.repair, "renumber": svc.renumber, "steal": svc.steal}
    if action not in fns:
        return garage_screen(c, ch)
    ch, res = await _run(ctx, fns[action])
    if ch is None:
        return await menu_screen(ctx)
    if res.blocked:
        return garage_screen(c, ch, BLOCKED.get(res.blocked, res.blocked))
    if action == "steal":
        if res.died:
            return death_screen(ch.state.name, res.death_cause, [])
        parts = [views.check_line(res.check)] if res.check else []
        if res.got:
            v = c.vehicles[res.got]
            parts.append(f"Замок поддаётся, мотор заводится с пол-оборота. Теперь у тебя {texts.esc(v.name)}. "
                         "Номера горячие, лучше их перебить.")
        else:
            parts.append("Сигнализация орёт на весь квартал. Ты убегаешь пешком, а камеры всё запомнили.")
        if res.applied:
            parts.append(views.effects_summary(res.applied, c))
        parts += res.lines or []
        if (res.lines and has_screen("jail")) and int(ch.state.flags.get("jail_weeks", 0) or 0) > 0:
            return Screen("\n".join(x for x in parts if x), [[Button(texts.MENU, "menu")]], name="garage")
        return garage_screen(c, ch, "\n".join(x for x in parts if x))
    notes = {"tune": "Тюнинг готов: {n}.", "repair": "Как новая. Ремонт: {n}.", "renumber": "Номера чистые. Обошлось в {n}."}
    return garage_screen(c, ch, notes[action].format(n=views.money(res.amount)))


@on_screen("vsell")
async def vsell_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action != "yes":
        return await open_garage(ctx)
    ch, res = await _run(ctx, svc.sell)
    if ch is None:
        return await menu_screen(ctx)
    note = BLOCKED.get(res.blocked, res.blocked) if res.blocked else f"Продано за {views.money(res.amount)}."
    return garage_screen(ctx.content, ch, note)


@on_screen("vdealer")
async def vdealer_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    tab, page = ctx.state.get("tab"), int(ctx.state.get("page") or 0)
    if action == "garage":
        return garage_screen(c, ch)
    if action == "tab" and arg in KIND_TABS:
        return dealer_screen(c, ch, year, arg, 0)
    if action == "pg" and arg and arg.isdigit():
        return dealer_screen(c, ch, year, tab, int(arg))
    if action == "buy" and arg:
        ch, res = await _run(ctx, svc.buy, arg)
        if ch is None:
            return await menu_screen(ctx)
        if res.blocked:
            return dealer_screen(c, ch, year, tab, page, BLOCKED.get(res.blocked, res.blocked))
        return garage_screen(c, ch, f"Ключи твои: {texts.esc(c.vehicles[arg].name)} за {views.money(res.amount)}.")
    return dealer_screen(c, ch, year, tab, page)


@on_screen("vrace")
async def vrace_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    if action != "go" or not arg:
        return await open_garage(ctx)
    ch, res = await _run(ctx, svc.race, arg)
    if ch is None:
        return await menu_screen(ctx)
    if res.blocked:
        return races_screen(c, ch, BLOCKED.get(res.blocked, res.blocked))
    if res.died:
        return death_screen(ch.state.name, res.death_cause, ["Гонка закончилась на отбойнике."])
    parts = [views.check_line(res.check)] if res.check else []
    if res.won:
        parts.append("Финиш твой. Толпа ревёт, соперник бьёт кулаком по рулю.")
    elif res.wrecked:
        parts.append("На повороте тачку заносит, удар, крыша, тишина. Тебя вытаскивают из обломков. Машины больше нет.")
    elif res.crashed:
        parts.append("Ты не вписываешься в поворот и целуешь отбойник. Обошлось без травм, а вот тачке досталось.")
    else:
        parts.append("Соперник уходит на последней прямой. Ставка сгорела.")
    if res.applied:
        parts.append(views.effects_summary(res.applied, c))
    parts += res.lines or []
    if svc.ride(ch) is None:
        return garage_screen(c, ch, "\n".join(x for x in parts if x))
    return races_screen(c, ch, "\n".join(x for x in parts if x))
