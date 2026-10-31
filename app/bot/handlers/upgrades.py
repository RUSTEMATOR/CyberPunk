"""The workshop (weapon attachments) and the home (things that work every week). Owner request.

workshop (for the equipped weapon) | homeitems. Entry: «Мастерская» and «Обустройство» on the property screen.
"""

from __future__ import annotations

from app.bot import effects_text, texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.services import game
from app.services import upgrades as svc
from app.services.game import ActiveCharacter

BLOCKED = {
    svc.BLOCK_NO_WEAPON: "Сначала возьми оружие в руки: «Экипировка».",
    svc.BLOCK_FIT: "На это оружие такое не ставится.",
    svc.BLOCK_FULL: "Больше двух модулей на одно оружие не влезет. Сними что-нибудь.",
    svc.BLOCK_HAVE: "Это уже есть.",
    svc.BLOCK_EDDIES: "Не хватает эдди.",
    svc.BLOCK_GONE: "Этого уже нет.",
}


PAGE = 8  # entries per page: a long list with descriptions does not fit one Telegram message


def _split(arg: str | None) -> tuple[str | None, int]:
    """`key:page` from a button argument (the page is kept so a purchase does not jump back to page one)."""
    if not arg:
        return None, 0
    key, _, page = arg.partition(":")
    return key or None, int(page) if page.isdigit() else 0


def _pager(rows: list[list[Button]], total: int, page: int) -> None:
    pages = max(1, (total + PAGE - 1) // PAGE)
    if pages < 2:
        return
    nav: list[Button] = []
    if page > 0:
        nav.append(Button(f"«« Стр. {page}", "pg", str(page - 1)))
    if page < pages - 1:
        nav.append(Button(f"Стр. {page + 2} »»", "pg", str(page + 1)))
    rows.append(nav)


def _fx(mods: dict) -> str:
    return effects_text.cap(", ".join(effects_text.mods_phrases(mods)))


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


def workshop_screen(content: Content, ch: ActiveCharacter, year: int, note: str | None = None,
                    page: int = 0) -> Screen:
    st = ch.state
    w = content.weapons.get(st.weapon_key or "")
    lines = [style.header("weapon", "Мастерская"), f"Эдди: {views.money(st.eddies)}", ""]
    rows: list[list[Button]] = []
    if w is None:
        lines.append("В руках ничего нет. Возьми оружие в «Экипировке», потом приходи.")
    else:
        have = svc.installed(ch)
        lines.append(f"Оружие в руках: <b>{texts.esc(w.name)}</b>. Модулей: {len(have)}/{content.balance.upgrades.max_weapon_mods}. "
                     "Модули работают, пока это оружие в руках; снять можно бесплатно, но деньги не вернутся.")
        mods = svc.mods_for(content, ch, year)
        page = max(0, min(page, (len(mods) - 1) // PAGE)) if mods else 0
        for m in mods[page * PAGE:(page + 1) * PAGE]:
            mark = " · <i>стоит</i>" if m.key in have else ""
            lines.append(f"\n<b>{texts.esc(m.name)}</b> · {views.money(m.price)}{mark}\n{texts.esc(_fx(m.mods))}\n<i>{texts.esc(m.description)}</i>")
            if m.key in have:
                rows.append([Button(f"✕ {m.name}"[:24], "rm", f"{m.key}:{page}")])
            else:
                rows.append([Button(m.name[:24], "put", f"{m.key}:{page}")])
        _pager(rows, len(mods), page)
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="workshop")


def home_screen(content: Content, ch: ActiveCharacter, year: int, note: str | None = None, page: int = 0) -> Screen:
    st = ch.state
    own = svc.owned_home(ch)
    lines = [style.header("property", "Обустройство"), f"Эдди: {views.money(st.eddies)}",
             "Вещи для дома работают каждую неделю и переезжают вместе с тобой.", ""]
    rows: list[list[Button]] = []
    items = list(svc.home_items(content, year))
    page = max(0, min(page, (len(items) - 1) // PAGE)) if items else 0
    for h in items[page * PAGE:(page + 1) * PAGE]:
        mark = " · <i>есть</i>" if h.key in own else ""
        lines.append(f"<b>{texts.esc(h.name)}</b> · {views.money(h.price)}{mark}\n{texts.esc(_fx(h.mods))}\n<i>{texts.esc(h.description)}</i>\n")
        if h.key not in own:
            rows.append([Button(h.name[:24], "buy", f"{h.key}:{page}")])
    _pager(rows, len(items), page)
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="homeitems")


@on_screen("workshop")
async def workshop_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "property")
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        err, note = None, None
        key, page = _split(arg)
        if action == "pg":
            page = int(arg) if arg and arg.isdigit() else 0
        elif action == "put" and key:
            err = await svc.install(s, c, ch, year, ctx.ui.settings.timezone, key)
            note = "Модуль установлен." if err is None else None
        elif action == "rm" and key:
            err = await svc.remove(s, c, ch, ctx.ui.settings.timezone, key)
            note = "Модуль снят." if err is None else None
    return workshop_screen(c, ch, year, BLOCKED.get(err, err) if err else note, page)


@on_screen("homeitems")
async def homeitems_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "property")
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        err, note = None, None
        key, page = _split(arg)
        if action == "pg":
            page = int(arg) if arg and arg.isdigit() else 0
        elif action == "buy" and key:
            err = await svc.buy_home(s, c, ch, year, ctx.ui.settings.timezone, key)
            note = "Куплено. Дома стало уютнее." if err is None else None
    return home_screen(c, ch, year, BLOCKED.get(err, err) if err else note, page)
