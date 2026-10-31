"""The world beyond Night City (owner request 23.10). city -> world (countries) -> wcountry (one country)."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen, pack
from app.content.loader import Content
from app.db import session as db
from app.engine import abroad as ae
from app.services import abroad as svc
from app.services import game
from app.services.game import ActiveCharacter

BLOCKED = {
    "jail": "Из камеры не улететь.",
    "fugitive": "Тебя ищет МаксТак. В аэропорт не сунуться.",
    "fight": "Сначала разберись с боем.",
    "airport": "С таким розыском в аэропорт не пустят. Остаётся контрабандист.",
    "eddies": "Не хватает эдди.",
    "ap": "ОД кончились. Нажми «Прожить месяц».",
    "here": "Ты уже здесь.",
    "home": "Ты и так в Найт-Сити.",
    "gone": "Этого больше нет.",
    "story_done": "Сюжет этой страны пройден до конца.",
    "story_day": "Следующий шаг сюжета будет завтра.",
    "busy": "Сначала разберись с тем, что уже ждёт.",
}


def _head(ch: ActiveCharacter) -> str:
    st = ch.state
    return f"Эдди: {views.money(st.eddies)} · ОД: {st.ap} · розыск {st.heat}/5"


# Owner request 30.10 (docs/PLAN_WORLD2.md): the crossover cities are real cities. Their own windows work for residents.
CROSSOVER_WINDOWS = {"elysium": ("elysium", "🛰 Станция и медотсек"), "libria": ("libria", "💉 Прозиум и Подполье"),
                     "aeon": ("aeon", "⏳ Часы и биржа")}


def resident(content: Content, ch: ActiveCharacter, key: str) -> bool:
    from app.engine import place

    return place.place_of(ch.state, content) == key


def gate_screen(content: Content, ch: ActiveCharacter, key: str) -> Screen:
    """The country window with the reason, for someone who does not live there."""
    c = content.countries[key]
    return country_screen(content, ch, key, f"{c.name} — отдельный город, как Найт-Сити. Жить и действовать там можно, "
                                            "только переехав: кнопка «Переехать» ниже.")


def where_line(content: Content, ch: ActiveCharacter) -> str:
    c = ae.country(ch.state, content)
    return (f"✈️ Ты живёшь за границей: {c.name}, {c.city}. Игроки других городов тебя не достанут, ты их тоже."
            if c else "🏙 Ты в Найт-Сити.")


REGIONS = {"america": "Америка", "europe": "Европа", "asia": "Азия", "africa": "Африка", "oceania": "Океания",
           "crossover": "Другие миры"}  # owner request 30.10: Elysium, Libria and Aeon as cities


def world_screen(content: Content, ch: ActiveCharacter, year: int, note: str | None = None, region: str = "") -> Screen:
    lines = [style.header("district", "Мир"), _head(ch), where_line(content, ch), ""]
    if note:
        lines += [note, ""]
    here = ae.where(ch.state)
    countries = list(content.available("countries", year).values())
    if not region:
        cur = content.countries.get(here or "")
        region = cur.region if cur else "america"
    lines.append("<i>За стеной Найт-Сити целая планета. В каждом городе свои районы, фиксеры с заказами, банды, "
                 "риппер, жильё, жители и свой сюжет. Бизнес, банда и семья в Найт-Сити работают удалённо. "
                 "Судная ночь и повестки Найт-Сити за границу не дотягиваются.</i>")
    lines += ["", f"<b>{REGIONS.get(region, region)}</b>"]
    rows: list[list[Button]] = []
    shown = [c for c in countries if c.region == region]
    for c in shown:
        mark = " · ты здесь" if c.key == here else ""
        lines.append(f"{texts.esc(c.name)} ({texts.esc(c.city)}){mark}: билет {views.money(svc.ticket(ch, content, c.key))}, "
                     f"жизнь {views.money(c.weekly_cost)}/нед.")
    rows += pack([Button(c.name, "c", c.key) for c in shown], content.balance.ui.button_chars, max_per_row=2)
    rows += pack([Button(("• " if k == region else "") + v, "r", k) for k, v in REGIONS.items()
                  if any(c.region == k for c in countries)], content.balance.ui.button_chars, max_per_row=3)
    if here:
        home = ae.country(ch.state, content)
        rows.append([Button(f"Домой · {views.money(home.flight if home else 0)}", "ask", "home", style="success")])
        rows.append([Button("Сдаться миграционке", "ask", "deport")])
    rows.append([Button(texts.BACK, "city"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="world", state={"r": region})


async def _people_in(session, content: Content, ch: ActiveCharacter, where: str) -> list[str]:  # noqa: ANN001
    """Other players' living characters in that place (the move screen shows who lives there)."""
    from app.db import repo
    from app.engine import place

    out = []
    for row in await repo.living_characters(session):
        if row.player_tg_id != ch.player_tg_id and not (row.flags or {}).get("ai_mode") \
                and place.of_district(content, row.district) == where:
            out.append(f"{row.name} {row.surname}".strip())
    return out


async def move_screen(session, content: Content, ch: ActiveCharacter, key: str | None, how: str) -> Screen:  # noqa: ANN001
    """Owner request 30.10 (docs/PLAN_WORLD2.md): a move is a decision. What changes, what it costs, who lives there."""
    from app.engine import place

    here = place.place_of(ch.state, content)
    to = key or place.NC
    to_name = place.name_of(content, to)
    lines = [style.header("district", f"Переезд: {place.name_of(content, here)} → {to_name}"), _head(ch), ""]
    if key:
        c = content.countries[key]
        price = c.smuggle_price if how == "smug" else svc.ticket(ch, content, key)
        way = (f"контрабандист, {views.money(price)}, проверка ХЛАД + скрытность против 14 (провал — деньги пропали)"
               if how == "smug" else f"билет и виза, {views.money(price)}")
        n = svc.story_step(ch.state, key)
        story = (f"История города «{texts.esc(c.story_title)}» продолжится с шага {n + 1} из {len(c.story)}."
                 if 0 < n < len(c.story) else "История города пройдена, но город живёт дальше."
                 if c.story and n >= len(c.story) else "История города начнётся с первой главы.")
        lines += [f"<b>{texts.esc(c.name)}, {texts.esc(c.city)}.</b> Дорога: {way}, {svc.FLIGHT_AP} ОД.",
                  f"Жизнь: отель {views.money(c.weekly_cost)} в неделю или своё жильё там. Жильё в Найт-Сити ждёт без аренды.",
                  story]
    elif how == "deport":
        lines.append("Сдаться миграционке: бесплатно, но в наручниках, +1 розыск и −3 настроения.")
    else:
        home = ae.country(ch.state, content)
        lines.append(f"Обратный билет: {views.money(home.flight if home else 0)}. Тебя ждёт твой дом и район.")
    lines += ["", "<b>Что изменится</b>",
              f"• Ты будешь жить {place.name_in(content, to)}: заказы, жители, банды, риппер, жильё и вылазки — местные.",
              f"• Те, кто остался {place.name_in(content, here)}, не смогут на тебя напасть, позвать на свидание, "
              "в дуэль, в дело или жить вместе. Ты на них тоже. Преследовать кого-то можно, только переехав за ним.",
              "• Через границу работают записки, деньги, поручительство, банда игроков, лента и Сеть.",
              "• Охрана и штат встают рядом в бою, только когда вы в одном городе."]
    people = await _people_in(session, content, ch, to)
    lines += ["", ("Там сейчас живут: " + texts.esc(", ".join(people[:12])) + ".") if people else "Из игроков там пока никого."]
    act = {"fly": "fly", "smug": "smug", "home": "home", "deport": "deport"}[how]
    rows = [[Button("Подтвердить переезд", "go", act, style="success")], [Button("Остаться", "stay")]]
    return Screen("\n".join(lines), rows, name="wmove", state={"c": key or "", "how": how})


@on_screen("wmove")
async def wmove_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    key = str(ctx.state.get("c") or "")
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        rng, tz = random.Random(), ctx.ui.settings.timezone
        if action == "go" and arg in ("fly", "smug") and key in c.countries:
            r = await svc.fly(s, c, ch, rng, year, tz, key, smuggler=arg == "smug")
            return country_screen(c, ch, key, _note(c, r))
        if action == "go" and arg in ("home", "deport"):
            r = await svc.go_home(s, c, ch, rng, year, tz, deport=arg == "deport")
            return world_screen(c, ch, year, _note(c, r))
        if key in c.countries:
            return country_screen(c, ch, key)
        return world_screen(c, ch, year)


def _mods_line(content: Content, mods: dict) -> str:
    from app.bot import effects_text

    return effects_text.mods_line(mods) or "без особенностей"


def country_screen(content: Content, ch: ActiveCharacter, key: str, note: str | None = None) -> Screen:
    c = content.countries[key]
    here = ae.where(ch.state) == key
    lines = [style.header("district", f"{c.name}: {c.city}"), _head(ch), where_line(content, ch), ""]
    if note:
        lines += [note, ""]
    lines += [f"<i>{texts.esc(c.description)}</i>", "",
              f"Жизнь здесь: {_mods_line(content, dict(c.mods))}.",
              f"Отель {views.money(c.weekly_cost)} в неделю, платится каждый месяц. Своё жильё в городе заменяет отель. "
              "Нечем платить — депортация."]
    lines += city_lines(content, ch, c)
    rows: list[list[Button]] = []
    if here:
        n = svc.story_step(ch.state, key)
        if c.story and n < len(c.story):
            rows.append([Button(f"Сюжет: шаг {n + 1} из {len(c.story)}", "story", style="success")])
        if key in CROSSOVER_WINDOWS:
            rows.append([Button(CROSSOVER_WINDOWS[key][1], "local", style="success")])
        if ae.in_city(ch.state, content):
            rows.append([Button("Заказы", "gigs"), Button("Город", "town"), Button("Жильё", "home_rent")])
        w = c.work
        wl = f"{w.button} ({w.ap} ОД)"
        rows.append([Button(wl if len(wl) <= 24 else w.button, "work")])
        for d in c.deeds:
            label = d.button + (f" ({d.ap} ОД)" if d.ap else "")
            label = label if len(label) <= 24 else d.button
            rows.append([Button(label, "d", d.key)])
    else:
        legal = ch.state.heat <= c.max_heat
        lines.append(f"Аэропорт пускает с розыском до {c.max_heat}. Контрабандист — {views.money(c.smuggle_price)}, "
                     "ХЛАД + скрытность против 14.")
        if legal:
            rows.append([Button(f"Переехать · {views.money(svc.ticket(ch, content, key))}", "ask", "fly", style="success")])
        rows.append([Button(f"Контрабандист · {views.money(c.smuggle_price)}", "ask", "smug")])
    rows.append([Button(texts.BACK, "world"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="wcountry", state={"c": key})


def city_lines(content: Content, ch: ActiveCharacter, c) -> list[str]:  # noqa: ANN001
    """Owner request 25.10: the city of the country, like Night City: districts, fixers, gangs, ripper, story."""
    if not c.districts:
        return []
    out = ["", f"<b>Город {texts.esc(c.city)}</b>"]
    for dk in c.districts:
        d = content.districts[dk]
        fixer = content.fixers.get(d.fixer)
        gangs = ", ".join(content.gangs[g].name for g in d.gangs if g in content.gangs)
        here = " · ты здесь" if ch.state.district == dk else ""
        out.append(f"• <b>{texts.esc(d.name)}</b> (опасность {d.danger}){here}: фиксер {texts.esc(fixer.name if fixer else '—')}"
                   + (f", банды: {texts.esc(gangs)}" if gangs else "") + ".")
    rip = [r for r in content.ripperdocs.values() if r.district in c.districts]
    if rip:
        out.append(f"Риппер: {texts.esc(', '.join(r.name for r in rip))}.")
    if c.story:
        n = min(svc.story_step(ch.state, c.key), len(c.story))
        out.append(f"Сюжет «{texts.esc(c.story_title)}»: пройдено {n} из {len(c.story)}, один шаг в сутки.")
    out.append("<i>Заказы, «Город» (места и жители), вылазки, жильё и переезд между районами работают здесь, "
               "как в Найт-Сити.</i>")
    return out


def _note(content: Content, r: svc.AbroadResult) -> str:
    if r.blocked is not None:
        return BLOCKED.get(r.blocked, r.blocked)
    parts: list[str] = []
    if r.check is not None:
        parts.append(views.check_line(r.check))  # type: ignore[arg-type]
    if r.text:
        parts.append(style.quote(texts.esc(r.text)))
    if r.applied is not None:
        sm = views.effects_summary(r.applied, content)
        if sm:
            parts.append(sm)
    parts.extend(texts.esc(x) for x in r.lines)
    return "\n".join(parts)


@on_screen("world")
async def world_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    c = ctx.content
    if action == "city":
        return await goto(ctx, "city")
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "c" and arg in c.countries:
            return country_screen(c, ch, arg)
        if action == "r" and arg:
            return world_screen(c, ch, year, region=arg)
        if action == "ask" and arg in ("home", "deport"):  # owner request 30.10: a move asks first
            return await move_screen(s, c, ch, None, arg)
        if action in ("home", "deport"):
            r = await svc.go_home(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, deport=action == "deport")
            return world_screen(c, ch, year, _note(c, r))
    return world_screen(c, ch, year)


@on_screen("wcountry")
async def wcountry_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "gigs":
        return await goto(ctx, "gigs", "open")
    if action == "town":
        return await goto(ctx, "city", "open")
    if action == "home_rent":
        return await goto(ctx, "property", "h")
    if action == "local" and str(ctx.state.get("c") or "") in CROSSOVER_WINDOWS:
        return await goto(ctx, "city", CROSSOVER_WINDOWS[str(ctx.state.get("c"))][0])
    c = ctx.content
    key = str(ctx.state.get("c") or "")
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "world" or key not in c.countries:
            return world_screen(c, ch, year, region=c.countries[key].region if key in c.countries else "")
        rng, tz = random.Random(), ctx.ui.settings.timezone
        if action == "story":  # owner request 25.10: the story of the country
            r = await svc.story_next(s, c, ch, tz)
            if r.event:
                return await goto(ctx, "evchoice", "resume")
        elif action == "ask" and arg in ("fly", "smug"):  # owner request 30.10: a move asks first
            return await move_screen(s, c, ch, key, arg)
        elif action in ("fly", "smug"):
            r = await svc.fly(s, c, ch, rng, year, tz, key, smuggler=action == "smug")
        elif action == "work":
            r = await svc.work(s, c, ch, rng, year, tz)
        elif action == "d" and arg:
            r = await svc.deed(s, c, ch, rng, year, tz, arg)
        else:
            return country_screen(c, ch, key)
    return country_screen(c, ch, key, _note(c, r))


@on_screen("gigsaway")
async def gigsaway_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "world":
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, ctx.content)
        return world_screen(ctx.content, ch, year)
    return await menu_screen(ctx)
