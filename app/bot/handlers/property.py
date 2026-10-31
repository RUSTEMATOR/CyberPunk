"""The property and move sections in the window (PLAN 4.6).

property (home, gear, subscription) -> prhome (rentals of the district) | prshop (weapons / armor)
| prequip (what is carried) | prtrauma (Trauma Team). move (districts) -> movec (confirm).
A refused or done action re-renders the same list with a note on top. Callback arguments carry
no colon: the shop tab is the action letter (`bw` buy weapon, `ba` buy armor).
"""

from __future__ import annotations

import random

from app.bot import effects_text, texts, texts_actions, views
from app.bot import texts_death as death_texts
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen, list_rows, pack
from app.content.loader import Content
from app.content.schemas import Armor, Weapon
from app.db import session as db
from app.engine import property as prop
from app.engine.state import KIND_ARMOR, KIND_WEAPON
from app.engine.week import housing_costs, trauma_price
from app.services import game
from app.services import property as pr_service
from app.services import worldfx
from app.engine import world as world_engine
from app.engine.world import WorldFx
from app.services.game import ActiveCharacter

_KIND_LETTER = {KIND_WEAPON: "w", KIND_ARMOR: "a"}
_LETTER_KIND = {v: k for k, v in _KIND_LETTER.items()}


def _note_text(reason: str | None) -> str:
    return texts.PR_BLOCKED.get(reason or "", reason or "")


def _with_note(lines: list[str], note: str | None) -> list[str]:
    return [note, ""] + lines if note else lines


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


# -- property hub ---------------------------------------------------------------------------------------


def property_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    rent, food = housing_costs(st, content)
    home = content.housing.get(st.housing)
    lines = [texts.PR_TITLE]
    lines.append(
        texts.PR_HOME.format(home=texts.esc(home.name), rent=views.money(rent), food=views.money(food))
        if home else texts.PR_NO_HOME.format(food=views.money(food))
    )
    weapon, armor = content.weapons.get(st.weapon_key or ""), content.armor.get(st.armor_key or "")
    lines.append(texts.PR_WEAPON.format(name=texts.esc(weapon.name) if weapon else texts.PR_NONE))
    lines.append(texts.PR_ARMOR.format(name=texts.esc(armor.name) if armor else texts.PR_NONE))
    tier = prop.current_tier(st, content)
    if tier:
        lines.append(texts.PR_TRAUMA.format(tier=content.balance.trauma_team[tier].name, price=views.money(trauma_price(st, content))))
    else:
        lines.append(texts.PR_NO_TRAUMA)
    rows = [
        [Button(texts.PR_BTN_HOME, "h"), Button(texts.PR_BTN_EQUIP, "e")],
        [Button(texts.PR_BTN_WEAPONS, "sw"), Button(texts.PR_BTN_ARMOR, "sa")],
        [Button(texts.PR_BTN_TRAUMA, "t")],
        [Button(texts_actions.BTN_BUSINESS, "biz"), Button("Гараж", "garage")],
        [Button("Мастерская", "workshop"), Button("Обустройство", "homeitems")],
        [Button("Уровень жизни", "lifestyle"), Button("Персонал", "staff")],
        [Button("Империя", "empire"), Button("Общий дом и штаб", "hubs")],
        [Button(f"Долги · {views.money(st.debt_total)}" if st.debts else "Долги", "debts"), Button("Займы", "loans")],
        [Button(death_texts.ENGRAM_BTN, "soul")],
        [Button(texts.BACK, "back")],
    ]
    return Screen("\n".join(_with_note(lines, note)), rows, name="property")


@on_screen("property")
async def property_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await menu_screen(ctx)
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if action == "h":
        return home_screen(c, ch)
    if action in ("sw", "sa"):
        return await shop_screen(ctx, ch, year, _LETTER_KIND[action[1]])
    if action == "e":
        return equip_screen(c, ch)
    if action == "t":
        return trauma_screen(c, ch, year)
    if action in ("workshop", "homeitems"):
        from app.bot.handlers import upgrades as up_handlers

        return (up_handlers.workshop_screen if action == "workshop" else up_handlers.home_screen)(c, ch, year)
    if action == "debts":
        return debts_screen(c, ch)
    if action == "loans":
        from app.bot.handlers.loans import loans_screen

        return loans_screen(c, ch)
    if action == "hubs":
        from app.bot.handlers.hubs import hubs_screen

        return await hubs_screen(ctx, ch)
    if action == "empire":
        from app.bot.handlers import bigmoney

        return await bigmoney.empire_screen(ctx, ch)
    if action in ("lifestyle", "staff"):
        from app.bot.handlers import economy

        return economy.lifestyle_screen(c, ch) if action == "lifestyle" else economy.staff_screen(c, ch, year)
    if action == "garage":
        from app.bot.handlers.vehicles import garage_screen

        return garage_screen(c, ch)
    if action == "biz":
        from app.bot.handlers.actions import biz_screen

        return biz_screen(c, ch)
    if action == "soul":  # Secure Your Soul (PLAN 7.3)
        from app.bot.handlers.legacy import engram_screen

        return engram_screen(c, ch, year)
    return property_screen(c, ch)


# -- housing ----------------------------------------------------------------------------------------------


def home_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    district = views.district_name(content, st.district)
    homes = prop.housing_in(content, st.district)
    lines = [texts.PRH_TITLE.format(district=district)]
    rows: list[list[Button]] = []
    if not homes:
        lines.append(texts.PRH_EMPTY)
    else:
        lines.append(texts.PRH_DEPOSIT_NOTE.format(
            weeks=content.balance.housing.move_deposit_weeks, ap=content.balance.property.housing_ap))
        others = [(n, h) for n, h in enumerate(homes, 1) if st.housing != h.key]
        numbered, rows = list_rows([h.name for _, h in others], "r", [h.key for _, h in others],
                                   content.balance.ui.button_chars, [n for n, _ in others])
        for n, h in enumerate(homes, 1):
            here = st.housing == h.key
            lines.append("")
            lines.append((f"{n}. " if numbered else "") + texts.PRH_LINE.format(
                name=texts.esc(h.name), mark=texts.PRH_MARK_HERE if here else "", desc=texts.esc(h.description),
                rent=views.money(prop.rent_of(st, h)), food=views.money(h.food), deposit=views.money(prop.deposit(content, h, st)),
            ))
            if h.min_status:
                lines.append(texts.PRH_NEEDS_STATUS.format(n=h.min_status))
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(_with_note(lines, note)), rows, name="prhome")


@on_screen("prhome")
async def prhome_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if action == "r" and arg:
        rng = random.Random()
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            res = await pr_service.rent_home(s, c, ch, rng, year, ctx.ui.settings.timezone, arg)
        if not res.ok:
            return home_screen(c, ch, _note_text(res.reason))
        name = texts.esc(c.housing[arg].name)
        note = (texts.PRH_DONE.format(name=name, deposit=views.money(res.paid)) if res.paid
                else texts.PRH_DONE_FREE.format(name=name))
        return home_screen(c, ch, note)
    if action == "back":
        return property_screen(c, ch)
    return home_screen(c, ch)


# -- the arms shop -------------------------------------------------------------------------------------------


SHOP_PAGE = 6  # items per page: a full shelf does not fit one Telegram message
WEAPON_TABS = {"pistol": "Пистолеты", "smg": "ПП", "shotgun": "Дробовики", "rifle": "Винтовки",
               "heavy": "Тяжёлое", "melee": "Холодное", "exotic": "Экзотика"}


def weapon_type(w: Weapon) -> str:
    return w.type or ("melee" if w.skill == "melee" else "pistol")


async def shop_screen(
    ctx: Ctx, ch: ActiveCharacter, year: int, kind: str, note: str | None = None, tab: str | None = None, page: int = 0,
) -> Screen:
    c = ctx.content
    st = ch.state
    async with db.session_scope() as s:
        fx = await worldfx.load_fx(s, c, ctx.user_id)
        mult = fx.price("weapons")
    items = prop.shop_items(st, c, year, kind, mult, fx)
    tabs: list[str] = []
    if kind == KIND_WEAPON:  # weapons go by type tabs; armor is one shelf with pages
        present = {weapon_type(c.weapons[i.key]) for i in items if i.key in c.weapons}
        tabs = [t for t in WEAPON_TABS if t in present]
        if tab not in tabs:
            tab = tabs[0] if tabs else None
        items = [i for i in items if i.key in c.weapons and weapon_type(c.weapons[i.key]) == tab]
    pages = max(1, -(-len(items) // SHOP_PAGE))
    page = max(0, min(page, pages - 1))
    shown = items[page * SHOP_PAGE:(page + 1) * SHOP_PAGE]
    lines = [texts.PRS_TITLE_W + (f": {WEAPON_TABS[tab]}" if tab else "") if kind == KIND_WEAPON else texts.PRS_TITLE_A]
    pct = prop.shop_discount_pct(st, c)
    head = [texts.PRS_AP.format(ap=c.balance.property.buy_ap)]
    if pct > 0:
        head.append(texts.PRS_DISCOUNT.format(pct=pct))
    if pages > 1:
        head.append(f"Стр. {page + 1}/{pages}")
    lines.append(" ".join(head))
    letter = _KIND_LETTER[kind]
    if not shown:
        lines.append(texts.PRS_EMPTY)
    buyable = [(n, it) for n, it in enumerate(shown, 1) if it.reason is None]
    numbered, rows = list_rows([it.name for _, it in buyable], f"b{letter}", [it.key for _, it in buyable],
                               c.balance.ui.button_chars, [n for n, _ in buyable])
    for n, it in enumerate(shown, 1):
        lines.append("")
        extra = (texts.PRS_CRED.format(cred=it.min_street_cred) if it.reason == prop.REASON_CRED else "") \
            + (texts.PRS_OWNED if it.owned else "")
        rec = (c.weapons if kind == KIND_WEAPON else c.armor).get(it.key)
        if isinstance(rec, Weapon):
            stat = effects_text.cap(", ".join(effects_text.weapon_phrases(rec)))
        elif isinstance(rec, Armor):
            stat = effects_text.cap(", ".join(effects_text.armor_phrases(rec)))
        else:
            stat = it.stat_line
        lines.append((f"{n}. " if numbered else "") + texts.PRS_LINE.format(
            name=texts.esc(it.name), stat=texts.esc(stat), desc=texts.esc(it.description),
            price=views.money(it.price), extra=extra,
        ))
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(Button(f"« Стр. {page}", "pg", str(page - 1)))
        if page < pages - 1:
            nav.append(Button(f"Стр. {page + 2} »", "pg", str(page + 1)))
        rows.append(nav)
    if len(tabs) > 1:
        rows += pack([Button(("• " if t == tab else "") + WEAPON_TABS[t], "tab", t) for t in tabs],
                     c.balance.ui.button_chars, max_per_row=4)
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(_with_note(lines, note)), rows, name="prshop",
                  state={"kind": kind, "tab": tab, "page": page})


@on_screen("prshop")
async def prshop_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    kind = ctx.state.get("kind") or KIND_WEAPON
    tab = ctx.state.get("tab")
    page = int(ctx.state.get("page") or 0)
    if action == "back":
        return property_screen(c, ch)
    if action == "tab" and arg in WEAPON_TABS:
        return await shop_screen(ctx, ch, year, kind, tab=arg, page=0)
    if action == "pg" and arg and arg.isdigit():
        return await shop_screen(ctx, ch, year, kind, tab=tab, page=int(arg))
    if action in ("bw", "ba") and arg:
        kind = _LETTER_KIND[action[1]]
        rng = random.Random()
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            res = await pr_service.buy_item(s, c, ch, rng, year, ctx.ui.settings.timezone, kind, arg)
        if not res.ok:
            return await shop_screen(ctx, ch, year, kind, _note_text(res.reason), tab=tab, page=page)
        catalog = c.weapons if kind == KIND_WEAPON else c.armor
        name = texts.esc(catalog[arg].name)
        tpl = texts.PRS_DONE_EQUIP if res.equipped else texts.PRS_DONE
        return await shop_screen(ctx, ch, year, kind, tpl.format(name=name, price=views.money(res.paid)), tab=tab, page=page)
    if action in ("sw", "sa"):  # opened from the property hub
        return await shop_screen(ctx, ch, year, _LETTER_KIND[action[1]])
    return await shop_screen(ctx, ch, year, kind, tab=tab, page=page)


# -- equipment -----------------------------------------------------------------------------------------------


def equip_screen(content: Content, ch: ActiveCharacter, note: str | None = None, back: str = "property") -> Screen:
    """What is in the hands and on the body, what is in the bag; take, swap or put away (owner request)."""
    st = ch.state
    lines = [texts.PRE_TITLE, texts.PRE_SLOTS.format(n=prop.WEAPON_SLOTS)]
    rows: list[list[Button]] = []
    armor = content.armor.get(st.armor_key or "")
    held = [content.weapons[k] for k in prop.equipped_weapons(st) if k in content.weapons]
    lines.append(texts.PRE_IN_HAND.format(name="; ".join(texts.esc(_weapon_line(w)) for w in held) if held else texts.PRE_FISTS)
                 + f" ({len(held)}/{prop.WEAPON_SLOTS})")
    lines.append(texts.PRE_ON_BODY.format(name=texts.esc(_armor_line(armor)) if armor else texts.PRE_NO_ARMOR))
    any_item = False
    for kind, head, catalog in ((KIND_WEAPON, texts.PRE_WEAPONS, content.weapons), (KIND_ARMOR, texts.PRE_ARMORS, content.armor)):
        items = prop.owned_items(st, kind)
        if not items:
            continue
        any_item = True
        lines.append("")
        lines.append(head)
        for it in items:
            rec = catalog.get(it.key)
            name = rec.name if rec else it.key
            desc = (_weapon_line(rec) if kind == KIND_WEAPON else _armor_line(rec)) if rec else name
            lines.append(("▸ " if it.equipped else "· ") + texts.esc(desc) + (texts.PRE_MARK if it.equipped else ""))
            info = [Button("ℹ️", f"i{_KIND_LETTER[kind]}", it.key)] if rec else []  # owner request 25.10: the full card
            if it.equipped:
                rows.append([Button(texts.PRE_PUT_AWAY.format(name=name), f"u{_KIND_LETTER[kind]}", it.key)] + info)
            else:
                verb = texts.PRE_TAKE if kind == KIND_WEAPON else texts.PRE_WEAR
                row = [Button(verb.format(name=name), f"e{_KIND_LETTER[kind]}", it.key)] + info
                price = pr_service.item_price(content, kind, it.key)  # owner request 24.10: sell your things
                if price:
                    row.append(Button(f"Продать · {views.money(price)}", f"s{_KIND_LETTER[kind]}", it.key))
                rows.append(row)
    if not any_item:
        lines += ["", texts.PRE_EMPTY]
    for it in prop.owned_items(st, KIND_WEAPON):  # owner request 24.10: a talking smart weapon
        w = content.weapons.get(it.key)
        if w is not None and w.voice is not None:
            short = w.name[w.name.find("«"):] if "«" in w.name else w.name
            rows.append([Button(f"🗣 Говорить: {short}"[:24], "talk", it.key)])
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(_with_note(lines, note)), rows, name="prequip", state={"back": back})


def item_card(content: Content, kind: str, key: str, back: str) -> Screen:
    """Owner request 25.10: everything a weapon or an armor does, in words."""
    from app.bot import effects_text

    rec = (content.weapons if kind == KIND_WEAPON else content.armor).get(key)
    if rec is None:
        return Screen("Этого больше нет.", [[Button(texts.BACK, "card_back")]], name="prequip", state={"back": back})
    lines = [f"<b>{texts.esc(rec.name)}</b>", f"<i>{texts.esc(rec.description)}</i>", ""]
    if kind == KIND_WEAPON:
        skill = "стрельба (РЕФЛ + стрельба)" if rec.skill == "firearms" else "ближний бой (ТЕЛО + ближний бой)"
        lines.append(f"⚔️ Урон {rec.damage} · {skill}")
        if rec.extra_attempts:
            lines.append(f"🔁 Атак за ход: {1 + rec.extra_attempts}")
        if rec.close_range_only:
            lines.append("📏 Полный урон только в упор: после первых ходов урон падает.")
        if rec.special is not None:
            lines.append(f"✨ <b>Особое:</b> {texts.esc(rec.special.text)}")
        if rec.voice is not None:
            lines.append("🗣 Говорит: внутри ИИ, с ним можно поболтать в экипировке.")
        one = prop_engine_one_handed(content, key)
        lines.append("✋ Одноручное: можно взять второе в другую руку." if one else "🙌 Двуручное.")
    else:
        lines.append(f"🦺 Защита {rec.defense}: столько вычитается из каждого попадания по тебе.")
    mods = dict(rec.mods or {})
    if mods:
        lines.append(f"📈 Пока в руках: {texts.esc(effects_text.mods_line(mods))}.")
    price = pr_service.item_price(content, kind, key)
    if price:
        lines.append(f"💰 Продать можно за {views.money(price)} эдди.")
    return Screen("\n".join(x for x in lines if x is not None), [[Button(texts.BACK, "card_back")]], name="prequip",
                  state={"back": back})


def prop_engine_one_handed(content: Content, key: str) -> bool:
    from app.engine import combat as cb

    w = content.weapons.get(key)
    return bool(w) and cb._one_handed(content, key, cb.RANGED if w.skill == "firearms" else cb.MELEE)  # noqa: SLF001


def _weapon_line(w) -> str:  # noqa: ANN001
    skill = "стрельба" if w.skill == "firearms" else "ближний бой"
    return f"{w.name}: урон {w.damage}, {skill}" + (" ✨" if w.special is not None else "")


def _armor_line(a) -> str:  # noqa: ANN001
    return f"{a.name}: защита {a.defense}"


@on_screen("prequip")
async def prequip_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    back = str(ctx.state.get("back") or "property")
    if action == "back":
        if back == "profile":
            return await goto(ctx, "profile")
        return property_screen(c, ch)
    if action in ("iw", "ia") and arg:  # owner request 25.10: the card of a weapon or an armor
        return item_card(c, _LETTER_KIND[action[1]], arg, back)
    if action == "card_back":
        return equip_screen(c, ch, back=back)
    if action == "talk" and arg:
        from app.bot.handlers.smartgun import open_chat

        scr = await open_chat(ctx, arg)
        if scr is not None:
            return scr
    if action in ("sw", "sa") and arg:
        kind = _LETTER_KIND[action[1]]
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            ok, price = await pr_service.sell_item(s, c, ch, ctx.ui.settings.timezone, kind, arg)
        catalog = c.weapons if kind == KIND_WEAPON else c.armor
        name = texts.esc(catalog[arg].name) if arg in catalog else arg
        return equip_screen(c, ch, f"Продано: {name}, +{views.money(price)} эдди." if ok else "Это не продать.", back=back)
    if action in ("ew", "ea", "uw", "ua"):
        kind = _LETTER_KIND[action[1]]
        if action[0] == "e" and not arg:
            return equip_screen(c, ch, back=back)
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            if action[0] == "e":
                res = await pr_service.equip_item(s, c, ch, ctx.ui.settings.timezone, kind, arg or "")
            else:
                res = await pr_service.unequip_item(s, c, ch, ctx.ui.settings.timezone, kind, arg or None)
        if not res.ok:
            return equip_screen(c, ch, _note_text(res.reason), back=back)
        if action[0] == "u":
            return equip_screen(c, ch, texts.PRE_PUT, back=back)
        catalog = c.weapons if kind == KIND_WEAPON else c.armor
        return equip_screen(c, ch, texts.PRE_DONE.format(name=texts.esc(catalog[arg or ""].name)), back=back)
    return equip_screen(c, ch, back=back)


# -- Trauma Team ---------------------------------------------------------------------------------------------


def trauma_screen(content: Content, ch: ActiveCharacter, year: int, note: str | None = None) -> Screen:
    st = ch.state
    tiers = content.balance.trauma_team
    now = prop.current_tier(st, content)
    lines = [texts.PRT_TITLE]
    rows: list[list[Button]] = []
    if not prop.trauma_offered(content, year):
        lines.append(texts.PRT_CLOSED)
        if now:
            rows.append([Button(texts.PRT_CANCEL, "x")])
    else:
        lines.append(texts.PRT_TEXT)
        lines.append("")
        for key, t in tiers.items():
            lines.append(texts.PRT_TIER.format(
                name=texts.esc(t.name), price=views.money(t.weekly_price), evac=views.money(t.evac_price),
                chance=round(t.rescue_chance * 100),
            ))
            if key != now:
                rows.append([Button(texts.PRT_BUTTON.format(name=t.name, price=views.money(t.weekly_price)), "t", key)])
        lines.append("")
        lines.append(texts.PRT_NOW.format(name=texts.esc(tiers[now].name)) if now else texts.PRT_NONE)
        if now:
            rows.append([Button(texts.PRT_CANCEL, "x")])
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(_with_note(lines, note)), rows, name="prtrauma")


@on_screen("prtrauma")
async def prtrauma_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if action == "back":
        return property_screen(c, ch)
    if action in ("t", "x"):
        tier = arg if action == "t" else None
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            res = await pr_service.change_trauma(s, c, ch, year, ctx.ui.settings.timezone, tier)
        if not res.ok:
            return trauma_screen(c, ch, year, _note_text(res.reason))
        note = texts.PRT_DONE.format(name=texts.esc(c.balance.trauma_team[tier].name)) if tier else texts.PRT_CANCELLED
        return trauma_screen(c, ch, year, note)
    return trauma_screen(c, ch, year)


# -- moving ---------------------------------------------------------------------------------------------------


async def _world_view(ctx: Ctx, year: int) -> tuple[WorldFx, dict[str, int]]:
    """The world's effects at the player's own date and the gang wars on there, for the danger shown next to a district."""
    async with db.session_scope() as s:
        fx = await worldfx.load_fx(s, ctx.content, ctx.user_id)
        return fx, await worldfx.active_wars(s, ctx.content, fx, year)


MOVE_PAGE = 6  # districts per page: the city has more of them than one message holds with descriptions


def move_screen(
    content: Content, ch: ActiveCharacter, year: int, note: str | None = None,
    world: tuple[WorldFx, dict[str, int]] | None = None, page: int = 0,
) -> Screen:
    st = ch.state
    fx, wars = world or (WorldFx(), {})
    keys = prop.districts_to_move(st, content, year, fx)
    districts = world_engine.districts_now(content, year, fx, None)
    lines = [texts.MV_TITLE, texts.MV_TEXT.format(
        district=views.district_name(content, st.district), ap=content.balance.property.move_ap)]
    rows: list[list[Button]] = []
    if not keys:
        lines.append(texts.MV_EMPTY)
    pages = max(1, (len(keys) + MOVE_PAGE - 1) // MOVE_PAGE)
    page = max(0, min(page, pages - 1))
    if pages > 1:
        lines.append(f"Стр. {page + 1}/{pages}")
    for k in keys[page * MOVE_PAGE:(page + 1) * MOVE_PAGE]:
        d = districts[k]
        lines.append("")
        lines.append(texts.MV_LINE.format(
            name=texts.esc(d.name), danger=world_engine.effective_danger(content, fx, wars, k), desc=texts.esc(d.description)))
        rows.append([Button(d.name, "d", k)])
    if pages > 1:
        nav: list[Button] = []
        if page > 0:
            nav.append(Button(f"«« Стр. {page}", "pg", str(page - 1)))
        if page < pages - 1:
            nav.append(Button(f"Стр. {page + 2} »»", "pg", str(page + 1)))
        rows.append(nav)
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(_with_note(lines, note)), rows, name="move", state={"page": page})


def move_confirm_screen(
    content: Content, ch: ActiveCharacter, year: int, district: str,
    world: tuple[WorldFx, dict[str, int]] | None = None,
) -> Screen:
    fx, wars = world or (WorldFx(), {})
    d = world_engine.districts_now(content, year, fx, None)[district]
    fixer = game.district_fixer(content, district, year)
    home = content.housing.get(ch.state.housing)
    return Screen(
        texts.MV_CONFIRM.format(
            name=texts.esc(d.name), desc=texts.esc(d.description), danger=world_engine.effective_danger(content, fx, wars, district),
            fixer=texts.esc(fixer.name) if fixer else "—",
            home_note=texts.MV_HOME_LOST.format(home=texts.esc(home.name)) if home else texts.MV_NO_HOME_LOST,
            ap=content.balance.property.move_ap,
        ),
        [[Button(texts.MV_GO, "go")], [Button(texts.BACK, "back")]],
        name="movec", state={"district": district},
    )


@on_screen("move")
async def move_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await menu_screen(ctx)
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    world = await _world_view(ctx, year)
    page = int(ctx.state.get("page") or 0)
    if action == "pg":
        return move_screen(c, ch, year, None, world, int(arg) if arg and arg.isdigit() else 0)
    if action == "d" and arg:
        reason = prop.can_move(ch.state, c, year, arg, world[0])
        if reason is not None:
            return move_screen(c, ch, year, _note_text(reason), world, page)
        return move_confirm_screen(c, ch, year, arg, world)
    return move_screen(c, ch, year, None, world, page)


@on_screen("movec")
async def movec_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    district = ctx.state.get("district")
    if action != "go" or not district:
        return move_screen(c, ch, year, None, await _world_view(ctx, year))
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        res = await pr_service.move_district(s, c, ch, year, ctx.ui.settings.timezone, district)
    if not res.ok:
        return move_screen(c, ch, year, _note_text(res.reason), await _world_view(ctx, year))
    name = views.district_name(c, district)
    tail = texts.MV_DONE_RENTALS if prop.housing_in(c, district) else texts.MV_DONE_NO_RENTALS
    return property_screen(c, ch, f"{texts.MV_DONE.format(district=name)} {tail}")


# -- paying debts off (owner request 24.10) --------------------------------------------------------------------------


def debts_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    lines = [style.header("debt", "Долги"), f"Эдди: {views.money(st.eddies)} · долг всего: {views.money(st.debt_total)}", ""]
    if note:
        lines += [note, ""]
    rows: list[list[Button]] = []
    if not st.debts:
        lines.append("Долгов нет. Свобода, как ветер над заливом.")
    for i, d in enumerate(st.debts):
        lines.append(f"{i + 1}. {texts.esc(d.creditor)}: {views.money(d.principal)} эдди, {views._pct(d.weekly_rate)} в неделю"
                     + (f", просрочено недель: {d.missed_weeks}" if d.missed_weeks else ""))
        rows.append([Button(f"Погасить {i + 1} · {views.money(d.principal)}", "pay", str(i))])
    if len(st.debts) > 1:
        rows.append([Button(f"Погасить всё · {views.money(st.debt_total)}", "payall", style="success")])
    lines += ["", "<i>Каждую неделю с тебя и так берут минимальный платёж и проценты. Погасить сразу — значит "
                  "больше не платить проценты.</i>"]
    rows.append([Button("Взять в долг", "loans")])
    rows.append([Button(texts.BACK, "prop"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="debts")


@on_screen("debts")
async def debts_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.services import debts as debt_svc

    if action == "menu":
        return await menu_screen(ctx)
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if action == "prop":
        return await goto(ctx, "property")
    if action == "loans":
        return await goto(ctx, "loans", "open")
    if action not in ("pay", "payall"):
        return debts_screen(c, ch)
    idx = int(arg) if action == "pay" and arg and arg.isdigit() else None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        why, applied, who = await debt_svc.pay_off(s, c, ch, year, ctx.ui.settings.timezone, idx)
    msg = {"eddies": "Не хватает эдди на всю сумму.", "none": "Долгов нет.", "gone": "Этого долга уже нет."}
    if why:
        return debts_screen(c, ch, msg.get(why, why))
    note = f"Долг закрыт: {texts.esc(who)}. Больше никаких процентов."
    if applied is not None:
        sm = views.effects_summary(applied, c)
        if sm:
            note += "\n" + sm
    return debts_screen(c, ch, note)
