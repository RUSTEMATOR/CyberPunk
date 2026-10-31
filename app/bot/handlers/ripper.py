"""The ripper section in the window (PLAN 6.1-6.5, 6.1a, 6.7).

ripper (clinics of the district) -> ripdoc (one clinic: catalog, my implants, scan, infection, software)
ripdoc -> ripcat (shelf by category, pages) -> ripcard (the implant card) -> ripbuy (confirm) -> ripdone (result)
ripdoc -> ripmine (installed) -> ripdel (confirm) -> ripdone
ripdoc | ripper -> ripsoft (programs for the deck) | ripcure (the infection) -> lifechk -> ripdone

The clinic, the category and the page live in the screen state, so every handler reads where the player is.
"""

from __future__ import annotations

import random

from app.bot import effects_text as FX
from app.bot import texts, texts_world, views
from app.bot import texts_chrome as T
from app.bot import views_chrome as V
from app.bot.handlers import lifecheck as lc
from app.bot.handlers.menu import menu_screen
from app.bot.ui.callbacks import Ctx, goto, has_screen, on_screen
from app.bot.ui.screen import Button, Screen, list_rows, pack
from app.content.loader import Content
from app.db import session as db
from app.engine import derived
from app.engine import ripper as eng
from app.engine.ripper import WorldMult
from app.services import events as ev_service
from app.services import game
from app.services import ripper as svc
from app.services.game import ActiveCharacter
from app.services.lifecheck import LifeResult

esc = texts.esc
ALL = "all"


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int, float]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
        mult = await svc.world_mult(s, ctx.content, ctx.user_id)
    return ch, year, mult


def _ap(content: Content, n: int) -> str:
    return T.RP_AP_WORD.format(n=n)


def _note(lines: list[str], note: str | None) -> list[str]:
    return [note, ""] + lines if note else lines


def _reason(content: Content, reason: str | None, needs: list[str] | None = None) -> str:
    text = T.RP_REASONS.get(reason or "", reason or "")
    return text.format(needs=", ".join(esc(n) for n in needs or []), ap=_ap(content, content.balance.ripper.install_ap))


# -- the list of clinics -------------------------------------------------------------------------------------------------------------


def ripper_screen(content: Content, ch: ActiveCharacter, year: int, note: str | None = None) -> Screen:
    st = ch.state
    docs = eng.rippers_here(st, content, year)
    lines = [T.RP_TITLE]
    rows: list[list[Button]] = []
    if eng.is_fugitive(st):
        lines.append(T.RP_FUGITIVE_NOTE)
    if not docs:
        lines.append(T.RP_NONE_FUGITIVE if eng.is_fugitive(st) else T.RP_NONE)
    else:
        lines.append(T.RP_INTRO)
        numbered, rows = list_rows([d.name for d in docs], "d", [d.key for d in docs], content.balance.ui.button_chars)
        for n, d in enumerate(docs, 1):
            lines.append("")
            lines.append((f"{n}. " if numbered else "") + T.RP_DOC_LINE.format(
                name=esc(d.name), tag=T.RP_TAG_UNDERGROUND if d.underground else T.RP_TAG_LICENSED, desc=esc(d.description)))
    if eng.infected(st):  # owner request: the cure must be easy to find
        lines += ["", "🦠 <b>Инфекция.</b> Каждую неделю отнимает здоровье. Лечение — «Клиника» ниже или у риппера в карточке."]
    if eng.software_dealer_open(st, content, year):
        rows.append([Button(T.RP_BTN_DEALER, "soft")])
    rows.append([Button("Иммуноподавители", "immuno"), Button("Клиника: лечение", "clinic")])  # owner request: upkeep
    rows.append([Button("Прокачка хрома", "chromeup"), Button("⚙️ Возможности хрома", "chromeab")])  # owner request 26.10
    rows.append([Button("🧬 Клиника клонов", "clones")])  # owner request 26.10: flesh grown back, the clone
    from app.engine import wear as wear_eng  # owner request 25.10: the price of power

    wl = wear_eng.level(st, content)
    if wl:
        from app.services import wear as wear_svc

        lines += ["", f"🦴 <b>Износ тела {wl}</b>: {wear_svc.describe(content, wl)}. Обслуживание снимет весь износ."]
        rows.append([Button(f"Обслуживание · {views.money(wear_eng.price(content, wl))}"[:24], "wear")])
    from app.engine import equilib as eq_eng  # owner request 26.10: injuries; mobilised rippers
    from app.engine import injury as inj

    if eq_eng.mobilised():
        lines += ["", "⚠️ <b>Риперов мобилизовали.</b> Город в смуте, Агентство слабо: обслуживать и чинить импланты "
                      "некому, пока равновесие не вернётся («Город» → «⚖️ Равновесие»)."]
    hurt = inj.describe(st, content)
    if hurt:
        lines += ["", "🩸 <b>Увечья</b>"] + hurt
        if inj.lost(st):
            lines.append("Утраченную часть вернёт любой имплант в её слот (руки, ноги, глаза) или выращенная плоть "
                         "у корпорации клонов.")
        if not eq_eng.mobilised():
            for i, key in enumerate(inj.damaged(st)):
                name = content.cyberware[key].name if key in content.cyberware else key
                rows.append([Button(f"Починить {name} · {views.money(inj.repair_price(content, key))}"[:28], "fix",
                                    str(i))])
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(_note(lines, note)), rows, name="ripper")


@on_screen("ripper")
async def ripper_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await menu_screen(ctx)
    ch, year, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    docs = eng.rippers_here(ch.state, c, year)
    if action == "d" and arg and eng.ripper_by_key(ch.state, c, year, arg):
        return doc_screen(c, ch, year, arg)
    if action == "soft":
        return soft_screen(c, ch, year, None, await _mult(ctx))
    if action == "clones":  # owner request 26.10
        from app.bot.ui.callbacks import goto

        return await goto(ctx, "clones", "open")
    if action == "chromeab":
        from app.bot.ui.callbacks import goto

        return await goto(ctx, "chromeab", "open", "ripper")
    if action == "fix" and arg and arg.isdigit():  # owner request 26.10: a damaged implant
        import random

        from app.engine import equilib as eq_eng
        from app.engine import injury as inj
        from app.engine.effects import apply_effects

        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            keys = inj.damaged(ch.state) if ch else []
            i = int(arg)
            note = "Чинить нечего."
            if ch and 0 <= i < len(keys) and not eq_eng.mobilised():
                price = inj.repair_price(c, keys[i])
                if ch.state.eddies < price:
                    note = "Не хватает денег на ремонт."
                else:
                    apply_effects(ch.state, {"eddies": -price}, c, random.Random(), year)
                    inj.repair(ch.state, keys[i])
                    await game.save_active(s, ch, ctx.ui.settings.timezone)
                    note = "Риппер перепаял контакты: имплант снова слушается."
        return ripper_screen(c, ch, year, note)
    if action == "wear":  # owner request 25.10: the body maintenance takes the wear off
        import random

        from app.engine import equilib as eq_mob

        if eq_mob.mobilised():
            return ripper_screen(c, ch, year, "Риперов мобилизовали: обслуживать тело некому.")

        from app.services import wear as wear_svc

        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            why = await wear_svc.maintain(s, c, ch, random.Random(), year, ctx.ui.settings.timezone) if ch else "clean"
        note = {"clean": "Износа нет.", "eddies": "Не хватает денег на обслуживание."}.get(why or "", "Риппер перебрал тело: износ снят.")
        return ripper_screen(c, ch, year, note)
    if action in ("immuno", "clinic", "chromeup"):
        from app.bot.handlers import economy

        if action == "chromeup":
            return economy.chromeup_screen(c, ch)
        return economy.immuno_screen(c, ch) if action == "immuno" else economy.clinic_screen(c, ch)
    return ripper_screen(c, ch, year)


async def _mult(ctx: Ctx) -> float:
    async with db.session_scope() as s:
        return await svc.world_mult(s, ctx.content, ctx.user_id)


# -- one clinic -------------------------------------------------------------------------------------------------------------------------


def doc_screen(content: Content, ch: ActiveCharacter, year: int, doc_key: str, note: str | None = None) -> Screen:
    st = ch.state
    doc = eng.ripper_by_key(st, content, year, doc_key)
    if doc is None:
        return ripper_screen(content, ch, year, note)
    lines = [T.RP_DOC_TITLE.format(name=esc(doc.name)), esc(doc.description), ""]
    lines.append(T.RP_DOC_PRICE.format(price=V.price_word(doc.price_mod)))
    lines.append(T.RP_DOC_RISK.format(pct=round(doc.complication_chance * 100)))
    spec = ", ".join(T.RP_CAT_NAMES.get(x, x).lower() for x in eng.specialty(doc))  # owner request 25.10
    lines.append(f"Специализация: {spec} — всегда в наличии и на {eng.SPECIALTY_DISCOUNT}% дешевле. "
                 "Остальное у каждого риппера своё и меняется каждый месяц: за нужным имплантом стоит съездить в другой район.")
    if doc.infection_chance:
        lines.append(T.RP_DOC_INFECTION.format(pct=round(doc.infection_chance * 100)))
    pct = eng.discount_pct(st, content)
    if pct > 0:
        lines.append(T.RP_DOC_DISCOUNT.format(pct=pct))
    lines.append(T.RP_DOC_STATE.format(
        eddies=views.money(st.eddies), ap=st.ap, h=st.humanity, hmax=derived.humanity_max(st, content)))
    tol = V.tolerance_label(content, st)
    lines.append(f"Кибертолерантность: {tol}" if tol else T.RP_TOLERANCE_UNKNOWN)
    if eng.smasher_visible(st):
        lines.append(T.RP_DOC_CONVERSION)
    if eng.infected(st):
        lines.append("")
        lines.append(T.RP_DOC_INFECTED)
    rows = [[Button(T.RP_BTN_CATALOG, "cat"), Button(T.RP_BTN_MINE, "mine")],
            [Button(T.RP_BTN_SCAN.format(price=views.money(content.balance.cyber.scan_cost)), "scan")]]
    if eng.infected(st):
        rows.append([Button(T.RP_BTN_CURE, "cure")])
    if doc.sells_software:
        rows.append([Button(T.RP_BTN_SOFT, "soft")])
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(_note(lines, note)), rows, name="ripdoc", state={"doc": doc_key})


@on_screen("ripdoc")
async def ripdoc_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year, mult = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    doc = str(ctx.state.get("doc") or "")
    if eng.ripper_by_key(ch.state, c, year, doc) is None:
        return ripper_screen(c, ch, year)
    if action == "back":
        return ripper_screen(c, ch, year)
    if action == "cat":
        return catalog_screen(c, ch, year, doc, ALL, 0, mult)
    if action == "mine":
        return mine_screen(c, ch, year, doc, mult)
    if action == "soft":
        return soft_screen(c, ch, year, doc, mult)
    if action == "cure":
        return cure_screen(c, ch, doc)
    if action == "scan":
        rng = random.Random()
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            res = await svc.scan_tolerance(s, c, ch, rng, year, ctx.ui.settings.timezone, doc)
        if not res.ok:
            return doc_screen(c, ch, year, doc, _reason(c, res.reason))
        name = esc(derived.tolerance_entry(c, res.data["shown"]).name)
        text = T.RP_SCAN_OK.format(paid=views.money(res.paid), name=name, hint=T.RP_SCAN_HINTS[res.data["shown"]])
        d = eng.ripper_by_key(ch.state, c, year, doc)
        if d is not None and d.scan_lie_chance > 0:
            text += "\n<i>" + T.RP_SCAN_NOTE_LIE + "</i>"
        return done_screen(text, doc)
    return doc_screen(c, ch, year, doc)


def done_screen(text: str, doc: str | None, *, label: str = T.NEXT) -> Screen:
    return Screen(text, [[Button(label, "next")]], name="ripdone", state={"doc": doc or ""})


@on_screen("ripdone")
async def ripdone_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if ev_service.has_pending(ch) and has_screen("evchoice"):
        return await goto(ctx, "evchoice", "resume")  # a breakdown or a warning event came out of the operation
    doc = str(ctx.state.get("doc") or "")
    if doc and eng.ripper_by_key(ch.state, ctx.content, year, doc):
        return doc_screen(ctx.content, ch, year, doc)
    return ripper_screen(ctx.content, ch, year)


# -- the shelf ---------------------------------------------------------------------------------------------------------------------------


def _short(text: str, limit: int = 110) -> str:
    """The first sentence of a description, cut to `limit` (owner report 26.10: «нет описания для имплантов»)."""
    t = " ".join(str(text).split())
    first = t.split(". ")[0]
    first = first if first.endswith((".", "!", "?")) or len(first) == len(t) else first + "."
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def catalog_screen(
    content: Content, ch: ActiveCharacter, year: int, doc_key: str, cat: str, page: int, mult: WorldMult,
    note: str | None = None,
) -> Screen:
    """Owner request 26.10 «удобнее меню выбора имплантов»: first pick a slot, a build line or a kind, then short lines
    (name, price, humanity, ability icons, build line) by pages; every effect is on the card."""
    st = ch.state
    doc = eng.ripper_by_key(st, content, year, doc_key)
    if doc is None:
        return ripper_screen(content, ch, year, note)
    offers = eng.catalog(st, content, year, doc, mult)
    chars = content.balance.ui.button_chars
    sets = content.balance.chrome_sets
    if cat == ALL:  # the overview
        lines = [T.RP_CAT_TITLE.format(doc=esc(doc.name)),
                 f"На полке {len(offers)} имплантов. Выбери слот тела, сборку или вид.", ""]
        if not offers:
            lines.append(T.RP_CAT_EMPTY)
        by_slot: dict[str, int] = {}
        by_set: dict[str, int] = {}
        for o in offers:
            cw = content.cyberware[o.key]
            by_slot[cw.slot] = by_slot.get(cw.slot, 0) + 1
            if cw.build:
                by_set[cw.build] = by_set.get(cw.build, 0) + 1
        from app.engine import abilities as ab_eng

        mine = ab_eng.set_counts(st, content)
        rows = [[Button("Всё подряд", "c", "list")]]
        lines.append("<b>Слоты</b>: " + ", ".join(f"{V.SLOT_NAMES.get(k, k)} {n}" for k, n in by_slot.items()))
        rows += pack([Button(f"{V.SLOT_NAMES.get(k, k).capitalize()} · {n}", "c", f"slot:{k}") for k, n in by_slot.items()],
                     chars, max_per_row=3)
        if by_set:
            lines.append("")
            lines.append("<b>Сборки</b> (детали одной линии дают бонус за 2, 4 и 6):")
            for k, n in sorted(by_set.items(), key=lambda kv: (-mine.get(kv[0], 0), sets[kv[0]].name)):
                lines.append(f"{sets[k].icon} {esc(sets[k].name)}: {n} на полке" + (f", у тебя {mine[k]}" if mine.get(k) else ""))
            rows += pack([Button(f"{sets[k].icon} {sets[k].name}"[:24], "c", f"set:{k}") for k in sorted(by_set, key=lambda k: sets[k].name)],
                         chars, max_per_row=2)
        cats = eng.categories(offers)
        rows += pack([Button(T.RP_CAT_TABS.get(k, T.RP_CAT_NAMES[k]), "c", k) for k in cats], chars, max_per_row=3)
        rows.append([Button(texts.BACK, "back")])
        return Screen("\n".join(_note(lines, note)), rows, name="ripcat",
                      state={"doc": doc_key, "cat": cat, "page": 0, "slots": by_slot, "sets": by_set,  # Mini App
                             "mine": dict(mine)})
    if cat == "list":
        shown, title = offers, "всё подряд"
    elif cat.startswith("slot:"):
        shown = [o for o in offers if content.cyberware[o.key].slot == cat[5:]]
        title = V.SLOT_NAMES.get(cat[5:], cat[5:])
    elif cat.startswith("set:"):
        shown = [o for o in offers if content.cyberware[o.key].build == cat[4:]]
        cs = sets.get(cat[4:])
        title = f"{cs.icon} {cs.name}" if cs else cat[4:]
    else:
        shown = [o for o in offers if o.category == cat]
        title = T.RP_CAT_NAMES.get(cat, cat).lower()
    size = max(content.balance.ripper.page_size, 10)
    pages = max(1, -(-len(shown) // size))
    page = max(0, min(page, pages - 1))
    chunk = shown[page * size:(page + 1) * size]
    lines = [T.RP_CAT_TITLE.format(doc=esc(doc.name)) + f" · {esc(title)}",
             "⚙️ — возможности вне боя, 🧩 — сборка. Полная карточка импланта — по номеру."]
    if cat.startswith("set:") and sets.get(cat[4:]):
        cs = sets[cat[4:]]
        lines.append(f"<i>{esc(cs.text)}</i>")
        lines += [f"▫️ {need} дет.: {esc(b.text)}" for need, b in sorted(cs.bonuses.items())]
    if not shown:
        lines.append(T.RP_CAT_EMPTY)
    else:
        lines.append(T.RP_CAT_PAGE.format(page=page + 1, pages=pages))
    numbered, picks = list_rows([o.name for o in chunk], "i", [o.key for o in chunk], chars)
    for n, o in enumerate(chunk, 1):
        cw = content.cyberware[o.key]
        mark = ""
        if o.replaces:
            mark = " ↺"
        if o.reason == eng.R_NEEDS:
            mark += " · нужен другой имплант"
        elif o.reason == eng.R_EDDIES:  # owner request 25.10: the shortage in bold, with the amount
            mark += f" · <b>не хватает {views.money(max(0, o.total - st.eddies))}</b>"
        icons = V.ability_icons(content, cw)
        cs = sets.get(cw.build or "")
        lines.append((f"{n}. " if numbered else "• ") + f"<b>{esc(o.name)}</b> · {views.money(o.total)}"
                     + (f" · −{o.pch} чел." if o.pch else "") + mark
                     + (f" · {icons}" if icons else "") + (f" · {cs.icon}" if cs and not cat.startswith("set:") else "")
                     + (f"\n   <i>{esc(_short(cw.description))}</i>" if cw.description else ""))  # owner report 26.10
    rows = picks
    nav: list[Button] = []
    if page > 0:
        nav.append(Button(T.RP_PREV, "p", str(page - 1)))
    if page < pages - 1:
        nav.append(Button(T.RP_MORE, "p", str(page + 1)))
    if nav:
        rows.append(nav)
    rows.append([Button("К выбору", "c", ALL), Button(texts.BACK, "back")])
    items = [{"key": o.key, "name": o.name, "price": o.total, "pch": o.pch, "slot": content.cyberware[o.key].slot,
              "build": content.cyberware[o.key].build, "abilities": list(content.cyberware[o.key].abilities),
              "tier": content.cyberware[o.key].tier, "replaces": o.replaces, "reason": o.reason,
              "desc": _short(content.cyberware[o.key].description)} for o in shown]
    return Screen("\n".join(_note(lines, note)), rows, name="ripcat",
                  state={"doc": doc_key, "cat": cat, "page": page, "items": items})  # items: the whole filter, Mini App


@on_screen("ripcat")
async def ripcat_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year, mult = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    doc = str(ctx.state.get("doc") or "")
    cat = str(ctx.state.get("cat") or ALL)
    page = int(ctx.state.get("page") or 0)
    if eng.ripper_by_key(ch.state, c, year, doc) is None:
        return ripper_screen(c, ch, year)
    if action == "back":
        return doc_screen(c, ch, year, doc)
    if action == "c" and arg:
        return catalog_screen(c, ch, year, doc, arg, 0, mult)
    if action == "p" and arg and arg.isdigit():
        return catalog_screen(c, ch, year, doc, cat, int(arg), mult)
    if action == "i" and arg:
        return card_screen(c, ch, year, doc, arg, mult, cat, page)
    return catalog_screen(c, ch, year, doc, cat, page, mult)


def card_screen(
    content: Content, ch: ActiveCharacter, year: int, doc_key: str, key: str, mult: WorldMult, cat: str, page: int,
    note: str | None = None,
) -> Screen:
    st = ch.state
    doc = eng.ripper_by_key(st, content, year, doc_key)
    offer = eng.offer_for(st, content, year, doc, key, mult) if doc is not None else None
    if doc is None or offer is None:
        return catalog_screen(content, ch, year, doc_key, cat, page, mult, T.RP_REASONS["gone"])
    cw = content.cyberware[key]
    lines = [V.card_text(content, st, cw, offer, doc.name, year)]
    if offer.banned:
        lines.append(texts_world.RIPPER_BANNED_NOTE.format(heat=content.balance.world.banned_install_heat))
    rows: list[list[Button]] = []
    if offer.reason is None:
        label = (T.RP_BTN_REPLACE if offer.replaces else T.RP_BTN_BUY).format(total=views.money(offer.total))
        rows.append([Button(label, "buy")])
    elif offer.reason == eng.R_EDDIES:  # owner request 25.10: the shortage in bold, with the amount
        lines += ["", T.RP_CARD_SHORT.format(short=views.money(max(0, offer.total - st.eddies)),
                                             have=views.money(st.eddies), total=views.money(offer.total))]
    elif offer.reason != eng.R_NEEDS:
        lines += ["", _reason(content, offer.reason)]
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(_note(lines, note)), rows, name="ripcard",
                  state={"doc": doc_key, "cat": cat, "page": page, "item": key})


@on_screen("ripcard")
async def ripcard_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year, mult = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    st = ctx.state
    doc, key = str(st.get("doc") or ""), str(st.get("item") or "")
    cat, page = str(st.get("cat") or ALL), int(st.get("page") or 0)
    if action == "buy":
        return confirm_screen(c, ch, year, doc, key, mult, cat, page)
    return catalog_screen(c, ch, year, doc, cat, page, mult)


def confirm_screen(
    content: Content, ch: ActiveCharacter, year: int, doc_key: str, key: str, mult: WorldMult, cat: str, page: int,
) -> Screen:
    st = ch.state
    doc = eng.ripper_by_key(st, content, year, doc_key)
    offer = eng.offer_for(st, content, year, doc, key, mult) if doc is not None else None
    if doc is None or offer is None or offer.reason is not None:
        return card_screen(content, ch, year, doc_key, key, mult, cat, page, _reason(content, offer.reason if offer else "gone", offer.needs if offer else None))
    ap = _ap(content, content.balance.ripper.install_ap)
    pct = round(doc.complication_chance * 100)
    name = esc(offer.name)
    if offer.replaces:
        old = content.cyberware.get(offer.replaces)
        text = T.RP_CONFIRM_REPLACE.format(old=esc(old.name if old else offer.replaces), name=name,
                                           total=views.money(offer.total), ap=ap, pct=pct)
    else:
        text = T.RP_CONFIRM.format(name=name, total=views.money(offer.total), ap=ap, pct=pct)
    rows = [[Button(T.RP_BTN_CONFIRM.format(total=views.money(offer.total)), "go")], [Button(texts.BACK, "back")]]
    return Screen(text, rows, name="ripbuy", state={"doc": doc_key, "cat": cat, "page": page, "item": key})


def install_text(content: Content, res: svc.OpResult) -> str:
    name = esc(res.name)
    lines: list[str] = []
    if res.malfunction:
        lines.append(T.RP_MALFUNCTION.format(name=name, paid=views.money(res.paid), loss=res.loss))
    else:
        if res.replaced:
            lines.append(T.RP_OK_REPLACE.format(
                old=esc(res.replaced), name=name, paid=views.money(res.paid), loss=res.loss,
                net=max(0, res.loss - res.returned)))
        else:
            lines.append(T.RP_OK.format(name=name, paid=views.money(res.paid), loss=res.loss))
        if res.complication:
            lines.append(T.RP_COMPLICATION)
    if res.infection:
        lines.append(T.RP_INFECTION)
    if res.banned_heat:
        lines.append(texts_world.RIPPER_BANNED_HEAT.format(heat=res.banned_heat))
    if res.borg and res.data.get("borg_chrome"):  # owner request 27.10: a borg by chrome
        lines.append("🤖 <b>Ты теперь борг.</b> Железа в теле больше, чем мяса. Город уже знает.")
    elif res.borg:  # owner request 25.10
        lines.append("🤖 <b>Ты теперь борг.</b> Полная конверсия: тело из металла, киберпсихоз больше не грозит, "
                     "эмпатия застыла на минимуме. Город уже знает.")
    if res.family:
        lines.append(T.RP_FAMILY)
    lines.extend(res.lines)
    return "\n".join(lines)


@on_screen("ripbuy")
async def ripbuy_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    st = ctx.state
    doc, key = str(st.get("doc") or ""), str(st.get("item") or "")
    cat, page = str(st.get("cat") or ALL), int(st.get("page") or 0)
    if action != "go":
        ch, year, mult = await _load(ctx)
        if ch is None:
            return await menu_screen(ctx)
        return card_screen(c, ch, year, doc, key, mult, cat, page)
    rng = random.Random()
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        mult = await svc.world_mult(s, c, ctx.user_id)
        res = await svc.install(s, c, ch, rng, year, ctx.ui.settings.timezone, doc, key)
    if res.died:
        from app.bot.handlers.week import death_screen

        return death_screen(ch.state.name, res.cause, res.lines)
    if res.reason is not None:
        offer = None
        d = eng.ripper_by_key(ch.state, c, year, doc)
        if d is not None:
            offer = eng.offer_for(ch.state, c, year, d, key, mult)
        return card_screen(c, ch, year, doc, key, mult, cat, page, _reason(c, res.reason, offer.needs if offer else None))
    return done_screen(install_text(c, res), doc)


# -- my implants and the removal -------------------------------------------------------------------------------------------


def mine_screen(content: Content, ch: ActiveCharacter, year: int, doc_key: str, mult: WorldMult, note: str | None = None) -> Screen:
    st = ch.state
    doc = eng.ripper_by_key(st, content, year, doc_key)
    if doc is None:
        return ripper_screen(content, ch, year, note)
    lines = [T.RP_MINE_TITLE.format(doc=esc(doc.name))]
    rows: list[list[Button]] = []
    if not st.cyberware:
        lines.append(T.RP_MINE_EMPTY)
    else:
        lines.append(T.RP_MINE_HINT)
        items = [(i, content.cyberware[i.key]) for i in st.cyberware if i.key in content.cyberware]
        off = [(n, i, cw) for n, (i, cw) in enumerate(items, 1) if svc.removable(content, i.key)]
        numbered, rows = list_rows([cw.name for _, _, cw in off], "x", [i.key for _, i, _ in off],
                                   content.balance.ui.button_chars, [n for n, _, _ in off])
        for n, (item, cw) in enumerate(items, 1):
            lines.append("")
            num = f"{n}. " if numbered else ""
            effects = esc(FX.cyberware_summary(cw, content))
            if not svc.removable(content, item.key):
                lines.append(num + T.RP_MINE_LINE_FIXED.format(name=esc(cw.name), effects=effects))
                continue
            price = eng.removal_price(st, content, year, item.key, doc, mult)
            back = _back_share(content, st, item)
            lines.append(num + T.RP_MINE_LINE.format(name=esc(cw.name), price=views.money(price), back=back, effects=effects))
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(_note(lines, note)), rows, name="ripmine", state={"doc": doc_key})


def _back_share(content: Content, st, item) -> int:  # noqa: ANN001 - CharacterState, CyberItem
    from app.engine.dice import half_up

    paid = item.humanity_paid
    if paid <= 0:
        base = content.cyberware[item.key].humanity_cost if item.key in content.cyberware else 0
        paid = derived.humanity_loss(base, st, content)
    return half_up(paid * content.balance.cyber.removal_return_pct)


@on_screen("ripmine")
async def ripmine_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year, mult = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    doc = str(ctx.state.get("doc") or "")
    if action == "x" and arg and ch.state.cyber_item(arg) is not None and svc.removable(c, arg):
        d = eng.ripper_by_key(ch.state, c, year, doc)
        if d is None:
            return ripper_screen(c, ch, year)
        price = eng.removal_price(ch.state, c, year, arg, d, mult)
        item = ch.state.cyber_item(arg)
        dependants = [c.cyberware[k].name for k in ch.state.cyber_keys if k != arg and arg in c.cyberware[k].requires]
        cascade = T.RP_REMOVE_CASCADE.format(names=", ".join(esc(n) for n in dependants)) if dependants else ""
        text = T.RP_REMOVE_CONFIRM.format(
            name=esc(c.cyberware[arg].name), price=views.money(price), cascade=cascade,
            back=_back_share(c, ch.state, item), ap=_ap(c, c.balance.ripper.remove_ap))
        rows = [[Button(T.RP_REMOVE_BTN.format(price=views.money(price)), "go", arg)],
                [Button(texts.BACK, "back")]]
        return Screen(text, rows, name="ripdel", state={"doc": doc, "item": arg})
    if action == "back":
        return doc_screen(c, ch, year, doc)
    return mine_screen(c, ch, year, doc, mult)


@on_screen("ripdel")
async def ripdel_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    doc = str(ctx.state.get("doc") or "")
    ch, year, mult = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action != "go" or not arg:
        return mine_screen(c, ch, year, doc, mult)
    rng = random.Random()
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        res = await svc.remove(s, c, ch, rng, year, ctx.ui.settings.timezone, doc, arg)
    if res.died:
        from app.bot.handlers.week import death_screen

        return death_screen(ch.state.name, res.cause, res.lines)
    if res.reason is not None:
        return mine_screen(c, ch, year, doc, mult, _reason(c, res.reason))
    text = T.RP_REMOVE_OK.format(name=esc(res.name), paid=views.money(res.paid), returned=res.returned)
    if res.cascade:
        text += T.RP_REMOVE_OK_CASCADE.format(names=", ".join(esc(n) for n in res.cascade))
    if res.lines:
        text += "\n" + "\n".join(res.lines)
    return done_screen(text, doc)


# -- the infection ------------------------------------------------------------------------------------------------------------------


def cure_screen(content: Content, ch: ActiveCharacter, doc_key: str, note: str | None = None) -> Screen:
    st = ch.state
    if not eng.infected(st):
        return done_screen(T.RP_CURE_CLEAN, doc_key)
    ck = content.balance.cyber.infection_cure_check
    text = T.RP_CURE_TEXT.format(ap=_ap(content, content.balance.ripper.cure_ap), price=views.money(eng.cure_price(content)))
    rows = [[Button(T.RP_CURE_BTN_CHECK.format(dv=ck.dv), "check")],
            [Button(T.RP_CURE_BTN_CLINIC.format(price=views.money(eng.cure_price(content))), "clinic")],
            [Button(texts.BACK, "back")]]
    return Screen("\n".join(_note([T.RP_CURE_TITLE, text], note)), rows, name="ripcure", state={"doc": doc_key})


def render_cure(content: Content, ch: ActiveCharacter, res: LifeResult) -> Screen:
    lines = [views.check_line(res.check)] if res.check else []
    lines.append(T.RP_CURE_CHECK_OK if res.ok else T.RP_CURE_CHECK_FAIL)
    return done_screen("\n".join(lines), None)


lc.register_renderer(svc.CURE, render_cure, lambda content, args: T.RP_CURE_HEAD)


@on_screen("ripcure")
async def ripcure_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    doc = str(ctx.state.get("doc") or "")
    tz = ctx.ui.settings.timezone
    if action in ("check", "clinic"):
        rng = random.Random()
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            if action == "clinic":
                res = await svc.cure_at_clinic(s, c, ch, rng, year, tz)
                if res.ok:
                    return done_screen(T.RP_CURE_CLINIC_OK.format(paid=views.money(res.paid)), doc)
                return cure_screen(c, ch, doc, _reason(c, res.reason))
            got = await svc.cure_by_check(s, c, ch, rng, year, tz)
        if not isinstance(got, LifeResult):
            return lc.check_screen(c, ch)
        if not got.done:
            return cure_screen(c, ch, doc, _reason(c, "no_ap" if got.no_ap else got.blocked))
        return lc.result_screen(c, ch, got)
    return doc_screen(c, ch, year, doc) if doc else ripper_screen(c, ch, year)


# -- software ----------------------------------------------------------------------------------------------------------------------


SOFT_PAGE = 8  # programs per page: the whole catalog with descriptions does not fit one Telegram message


def soft_screen(
    content: Content, ch: ActiveCharacter, year: int, doc_key: str | None, mult: WorldMult, note: str | None = None,
    page: int = 0,
) -> Screen:
    st = ch.state
    doc = eng.ripper_by_key(st, content, year, doc_key) if doc_key else None
    if doc_key and (doc is None or not doc.sells_software):
        return ripper_screen(content, ch, year, note)
    if doc is None and not eng.software_dealer_open(st, content, year):
        return ripper_screen(content, ch, year, note)
    who = esc(doc.name) if doc else T.SW_DEALER_NAME
    offers = eng.software_offers(st, content, year, ripper=doc, world_mult=mult)
    lines = [T.SW_TITLE.format(who=who), T.SW_INTRO]
    if not svc.has_deck(content, ch):
        lines.append(T.SW_NO_DECK)
    if not offers:
        lines.append(T.SW_EMPTY)
    pages = max(1, (len(offers) + SOFT_PAGE - 1) // SOFT_PAGE)
    page = max(0, min(page, pages - 1))
    shown = list(enumerate(offers, 1))[page * SOFT_PAGE:(page + 1) * SOFT_PAGE]
    buy = [(n, o) for n, o in shown if o.reason is None]
    numbered, rows = list_rows([o.name for _, o in buy], "b", [o.key for _, o in buy], content.balance.ui.button_chars,
                               [n for n, _ in buy])
    if pages > 1:
        lines.append(T.RP_CAT_PAGE.format(page=page + 1, pages=pages))
        nav: list[Button] = []
        if page > 0:
            nav.append(Button(f"«« Стр. {page}", "pg", str(page - 1)))
        if page < pages - 1:
            nav.append(Button(f"Стр. {page + 2} »»", "pg", str(page + 1)))
        rows.append(nav)
    for n, o in shown:
        qh = content.quickhacks[o.key]
        lines.append("")
        lines.append((f"{n}. " if numbered else "") + T.SW_LINE.format(
            name=esc(o.name), price=views.money(o.price), effect=esc(FX.cap(FX.quickhack_line(qh)))))
        lines.append(f"<i>{esc(qh.description)}</i>")
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(_note(lines, note)), rows, name="ripsoft", state={"doc": doc_key or "", "page": page})


@on_screen("ripsoft")
async def ripsoft_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    doc = str(ctx.state.get("doc") or "") or None
    page = int(ctx.state.get("page") or 0)
    ch, year, mult = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action == "back":
        return doc_screen(c, ch, year, doc) if doc else ripper_screen(c, ch, year)
    if action == "pg":
        return soft_screen(c, ch, year, doc, mult, None, int(arg) if arg and arg.isdigit() else 0)
    if action == "b" and arg:
        rng = random.Random()
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            res = await svc.buy_program(s, c, ch, rng, year, ctx.ui.settings.timezone, doc, arg)
        if not res.ok:
            return soft_screen(c, ch, year, doc, mult, T.SW_REASONS.get(res.reason or "", T.RP_REASONS.get(res.reason or "", "")),
                               page)
        return soft_screen(c, ch, year, doc, mult, T.SW_OK.format(name=esc(res.name), paid=views.money(res.paid)), page)
    return soft_screen(c, ch, year, doc, mult, None, page)
