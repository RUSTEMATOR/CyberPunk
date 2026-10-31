"""Character creation inside the window (PLAN 3.1).

preset list (paged, the civilian always present) -> generated sheet (Accept / one Reroll)
-> name (random, another random, or typed) -> save -> fixer greeting + main menu.
The draft lives in the screen state of the window, nothing is saved before the last step.
"""

from __future__ import annotations

import random
import re

from app.bot import texts, views
from app.bot import texts_death as death_texts
from app.bot.handlers.menu import greeting_text, menu_screen, world_year
from app.bot.ui.callbacks import Ctx, goto, on_screen, on_text
from app.bot.ui.screen import Button, Screen, pack
from app.bot.ui.style import header
from app.content.loader import Content
from app.db import session as db
from app.engine import character_gen
from app.services import game
from app.services import legacy as legacy_service

_DOMAIN_RE = re.compile(r"[\w-]+\.[a-z]{2,}")  # looks like a host name: vasya.ru, t.me
_ALLOWED_PUNCT = " -'’."


def validate_name(content: Content, raw: str) -> tuple[str | None, str | None]:
    """(clean name, None) or (None, reason in Russian). 2-24 chars by balance, no links."""
    ch = content.balance.character
    name = " ".join(raw.split())
    if len(name) < ch.name_length_min:
        return None, texts.CREATE_NAME_TOO_SHORT.format(min=ch.name_length_min)
    if len(name) > ch.name_length_max:
        return None, texts.CREATE_NAME_TOO_LONG.format(max=ch.name_length_max)
    low = name.lower()
    if any(bad in low for bad in content.balance.ui.name_blocklist) or _DOMAIN_RE.search(name):
        return None, texts.CREATE_NAME_LINK
    if not all(c.isalnum() or c in _ALLOWED_PUNCT for c in name):
        return None, texts.CREATE_NAME_BAD
    return name, None


# -- screens -------------------------------------------------------------------------------------------


def _split_presets(content: Content, year: int) -> tuple[list[str], list[str]]:
    keys = character_gen.offer_presets(random.Random(), content, year)
    always = [k for k in keys if content.presets[k].always_offered]
    regular = [k for k in keys if k not in always]
    return regular, always


PRESET_GROUPS = {"street": "Улица", "crime": "Криминал", "wheels": "Колёса", "order": "Корпо и закон",
                 "tech": "Сеть и техника", "stage": "Сцена"}
PRESET_TABS = {"street": "Улица", "crime": "Криминал", "wheels": "Колёса", "order": "Корпо",
               "tech": "Сеть", "stage": "Сцена"}  # short labels: three tabs to a row


async def preset_screen(ctx: Ctx, page: int | str = 0) -> Screen:
    """The starting kits by group tabs (owner request: many presets); the civilian is always at the end."""
    year = await world_year(ctx)
    regular, always = _split_presets(ctx.content, year)
    groups = [g for g in PRESET_GROUPS if any(ctx.content.presets[k].group == g for k in regular)]
    group = page if isinstance(page, str) and page in groups else (groups[0] if groups else "street")
    shown = [k for k in regular if ctx.content.presets[k].group == group] + always
    # owner report 24.10: a column of 15 long buttons was a pain. Now: the kits as a numbered list in the text, number
    # buttons five to a row, the sections as tabs three to a row, random and close in one row.
    rows: list[list[Button]] = []
    nums = [Button(str(i), "pick", k) for i, k in enumerate(shown, 1)]
    rows += [nums[i:i + 5] for i in range(0, len(nums), 5)]
    tabs = [Button(("• " if g == group else "") + PRESET_TABS.get(g, PRESET_GROUPS[g]), "grp", g) for g in groups]
    rows += [tabs[i:i + 3] for i in range(0, len(tabs), 3)]
    rows.append([Button("🎲 Случайно", "rand"), Button(texts.CLOSE, "close")])
    async with db.session_scope() as s:
        heir_plan = await legacy_service.load_plan(s, ctx.user_id)
    if heir_plan is not None:  # an heir of a dead character may still change the way
        rows.append([Button(death_texts.BTN_LEG_CHANGE, "legacy")])
    c = ctx.content
    lines = [texts.CREATE_PRESET_TITLE, f"<i>На дворе {year} год. Раздел: {PRESET_GROUPS.get(group, '')}.</i>", ""]
    for i, k in enumerate(shown, 1):
        lines.append(f"<b>{i}. {texts.esc(c.presets[k].name)}</b> — {texts.esc(c.presets[k].description)}")
    lines += ["", f"<i>Выбери номер. Разделы — кнопками ниже, всего {len(regular)} вариантов. "
                  "«Случайно» — любой из всех.</i>"]
    return Screen("\n".join(lines), rows, name="preset", state={"group": group})


async def sheet_screen(ctx: Ctx, draft_data: dict, rerolls_left: int) -> Screen:
    year = await world_year(ctx)
    draft = game.draft_from_dict(draft_data)
    text = views.sheet_text(ctx.content, draft.state, draft.preset_key, draft.origin_key, year, rerolls_left)
    if draft.heir:
        from app.bot.handlers.legacy import heir_block  # local: handlers.legacy imports the menu, as this module does

        text += "\n\n" + heir_block(ctx.content, draft.heir, views.full_name(draft.state))
    row = [Button(texts.CREATE_ACCEPT, "accept")]
    if rerolls_left > 0:
        row.append(Button(texts.CREATE_REROLL, "reroll"))
    return Screen(
        text,
        [row, [Button(texts.CREATE_BACK_TO_PRESETS, "back")]],
        name="sheet",
        state={"draft": draft_data, "rerolls_left": rerolls_left},
    )


async def name_screen(ctx: Ctx, draft_data: dict, notice: str = "") -> Screen:
    """The character editor (owner request 23.10): name, surname, gender, age, roots, birth country, background, looks."""
    from app.engine import creator

    c = ctx.content
    draft = game.draft_from_dict(draft_data)
    st = draft.state
    g = texts.CREATE_GENDER_M if st.flags.get("gender") == "m" else texts.CREATE_GENDER_F
    h = c.heritages.get(creator.heritage_of(st) or "")
    birth = c.countries.get(str(st.flags.get("birth_country") or ""))
    origin = c.origins.get(draft.origin_key)
    look = creator.look_of(st)
    lines = [header("profile", "Настройка персонажа"), notice.strip() if notice else "",
             f"Имя: <b>{texts.esc(views.full_name(st))}</b>",
             f"Пол: <b>{g}</b> · возраст: <b>{st.age_years}</b>",
             f"Корни: <b>{texts.esc(h.name) if h else 'не выбраны'}</b>",
             f"Родина: <b>{texts.esc(birth.name) if birth else 'Найт-Сити'}</b>",
             f"Предыстория: <b>{texts.esc(origin.name) if origin else '?'}</b>",
             f"Внешность: {texts.esc(creator.profile_line(c, st).split(' · ')[-1]) if look else 'не задана'}",
             "",
             "<i>Корни, родина и внешность на характеристики не влияют. Предыстория влияет: навыки, район, "
             "репутация, враги. Родина даёт визу без пошлины в свою страну.</i>"]
    rows = [
        [Button("Начать жизнь", "keep", style="success")],
        [Button(texts.CREATE_NAME_RANDOM, "random"), Button(texts.CREATE_NAME_TYPE, "type")],
        [Button("Другая фамилия", "surname"), Button("Ввести фамилию", "typesur")],
        [Button(texts.CREATE_GENDER_M, "gender", "m"), Button(texts.CREATE_GENDER_F, "gender", "f")],
        [Button("Возраст −", "age", "-1"), Button("Возраст +", "age", "1")],
        [Button("Корни", "roots"), Button("Родина", "birth")],
        [Button("Внешность", "look")] + ([] if draft.heir else [Button("Предыстория", "origin")]),
        [Button("Всё случайно", "rnd")],
    ]
    return Screen("\n".join(x for x in lines if x is not None), rows, name="name", state={"draft": draft_data})


def _pick_screen(ctx: Ctx, draft_data: dict, what: str) -> Screen:
    """A list to pick from: roots, birth country or background."""
    from app.engine import creator

    c = ctx.content
    draft = game.draft_from_dict(draft_data)
    rows: list[list[Button]] = []
    lines: list[str] = []
    if what == "roots":
        lines = [header("profile", "Корни семьи"), "Подбирают имя и фамилию. На характеристики не влияют.", ""]
        for i, h in enumerate(c.heritages.values(), 1):
            lines.append(f"{i}. <b>{texts.esc(h.name)}</b> — {texts.esc(h.description)}")
            rows.append([Button(h.name, "set", h.key)])
    elif what == "birth":
        lines = [header("profile", "Родина"), "Место рождения. В свою страну виза бесплатная.", ""]
        rows.append([Button("Найт-Сити", "set", creator.HOME)])
        rows += pack([Button(co.name, "set", co.key) for co in c.countries.values()], c.balance.ui.button_chars,
                     max_per_row=2)
    else:
        year = draft.state.birth_year + draft.state.age_years
        lines = [header("profile", "Предыстория"), "Меняет навыки от происхождения, стартовый район, репутацию и врагов.", ""]
        for i, key in enumerate(creator.origins_for(c, draft, year), 1):
            o = c.origins[key]
            mark = " ✔" if key == draft.origin_key else ""
            lines.append(f"{i}. <b>{texts.esc(o.name)}</b>{mark} — {texts.esc(o.description)}")
            rows.append([Button(f"{i}. {o.name}"[:24], "set", key)])
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="edpick", state={"draft": draft_data, "what": what})


def _look_screen(ctx: Ctx, draft_data: dict) -> Screen:
    from app.engine import creator

    draft = game.draft_from_dict(draft_data)
    look = creator.look_of(draft.state)
    lines = [header("profile", "Внешность"), "Каждое нажатие — следующий вариант. На характеристики не влияет.", ""]
    rows: list[list[Button]] = []
    for part, title in creator.LOOK_TITLES.items():
        cur = look.get(part, "не задано")
        lines.append(f"{title}: <b>{texts.esc(cur)}</b>")
        rows.append([Button(f"{title}: {cur}"[:24], "cyc", part)])
    rows.append([Button("Случайно", "rnd"), Button("Готово", "back")])
    return Screen("\n".join(lines), rows, name="edlook", state={"draft": draft_data})


def name_input_screen(content: Content, draft_data: dict, error: str | None = None, surname: bool = False) -> Screen:
    ch = content.balance.character
    prompt = texts.CREATE_NAME_PROMPT.format(min=ch.name_length_min, max=ch.name_length_max)
    if surname:
        prompt = f"Напиши фамилию сообщением в чат: от {ch.name_length_min} до {ch.name_length_max} символов."
    text = (texts.CREATE_NAME_ERROR.format(reason=texts.esc(error)) + prompt) if error else prompt
    return Screen(
        text,
        [[Button(texts.CANCEL, "cancel")]],
        awaiting_text="char_surname" if surname else "char_name",
        name="nameinput",
        state={"draft": draft_data},
    )


# -- handlers ---------------------------------------------------------------------------------------------


@on_screen("preset")
async def preset_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    async with db.session_scope() as s:
        if await game.get_active_character(s, ctx.user_id) is not None:
            return await menu_screen(ctx)
        needs_heir = await legacy_service.needs_choice(s, ctx.user_id)
    if needs_heir or action == "legacy":  # a dead character's heir is chosen first (PLAN 7.3)
        screen = await goto(ctx, "legacy", "change" if action == "legacy" else "open")
        if screen is not None:
            return screen
    if action == "close":
        await ctx.ui.windows.collapse(ctx.user_id)
        return None
    if action == "grp" and arg:
        return await preset_screen(ctx, arg)
    if action == "page":
        return await preset_screen(ctx, str(ctx.state.get("group") or ""))
    if action == "rand":
        regular, _ = _split_presets(ctx.content, await world_year(ctx))
        arg = random.choice(regular) if regular else None
        action = "pick" if arg else action
    if action == "pick" and arg:
        year = await world_year(ctx)
        if arg not in ctx.content.available("presets", year):
            return await preset_screen(ctx)
        async with db.session_scope() as s:
            draft = await game.new_draft(s, ctx.content, ctx.user_id, arg)
        return await sheet_screen(ctx, game.draft_to_dict(draft), ctx.content.balance.character.rerolls)
    return await preset_screen(ctx)


@on_screen("sheet")
async def sheet_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    data = ctx.state.get("draft")
    if not data:
        return await preset_screen(ctx)
    left = int(ctx.state.get("rerolls_left", 0))
    if action == "accept":
        if (data.get("heir") or {}).get("fixed_name"):  # a relative, the protege or the engram keep their own name
            return await _save(ctx, data)
        return await name_screen(ctx, data)
    if action == "reroll":
        if left <= 0:
            return await sheet_screen(ctx, data, 0)
        async with db.session_scope() as s:
            draft = await game.new_draft(s, ctx.content, ctx.user_id, data["preset"])
        return await sheet_screen(ctx, game.draft_to_dict(draft), left - 1)
    return await preset_screen(ctx)


@on_screen("name")
async def name_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    data = ctx.state.get("draft")
    if not data:
        return await preset_screen(ctx)
    if action == "random":
        from app.engine import creator

        draft = game.draft_from_dict(data)
        if not creator.heritage_name(random.Random(), ctx.content, draft):
            game.rename_draft(random.Random(), ctx.content, draft, None)
        return await name_screen(ctx, game.draft_to_dict(draft))
    if action == "gender" and arg in ("m", "f"):  # the gender: a typed name stays, a name of the other gender rerolls
        from app.engine import creator

        draft = game.draft_from_dict(data)
        h = ctx.content.heritages.get(creator.heritage_of(draft.state) or "")
        if h is not None:  # with roots: a name of the other gender comes from the roots' pool
            draft.state.flags["gender"] = arg
            if draft.state.name not in (h.male if arg == "m" else h.female):
                creator.heritage_name(random.Random(), ctx.content, draft)
            draft.state.surname = character_gen.surname_for(ctx.content, draft.state.surname, arg)
        else:
            game.set_draft_gender(random.Random(), ctx.content, draft, arg)
        return await name_screen(ctx, game.draft_to_dict(draft))
    if action == "surname":  # owner request: a new surname, the family takes it too
        from app.engine import creator

        draft = game.draft_from_dict(data)
        if not creator.heritage_surname(random.Random(), ctx.content, draft):
            game.reroll_surname(random.Random(), ctx.content, draft)
        return await name_screen(ctx, game.draft_to_dict(draft))
    if action == "type":
        return name_input_screen(ctx.content, data)
    if action == "typesur":
        return name_input_screen(ctx.content, data, surname=True)
    if action == "keep":  # owner request 26.10: the birth registration card first
        return await birth_screen(ctx, data)
    from app.engine import creator

    if action == "age" and arg in ("-1", "1"):
        draft = game.draft_from_dict(data)
        creator.set_age(draft, ctx.content, draft.state.age_years + int(arg))
        return await name_screen(ctx, game.draft_to_dict(draft))
    if action in ("roots", "birth", "origin"):
        return _pick_screen(ctx, data, action)
    if action == "look":
        return _look_screen(ctx, data)
    if action == "rnd":
        draft = game.draft_from_dict(data)
        creator.randomize(random.Random(), ctx.content, draft)
        return await name_screen(ctx, game.draft_to_dict(draft))
    return await name_screen(ctx, data)


@on_screen("edpick")
async def edpick_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.engine import creator

    data = ctx.state.get("draft")
    if not data:
        return await preset_screen(ctx)
    if action == "set" and arg:
        draft = game.draft_from_dict(data)
        what = str(ctx.state.get("what") or "")
        note = ""
        if what == "roots":
            creator.set_heritage(random.Random(), ctx.content, draft, arg)
        elif what == "birth":
            creator.set_birth(draft, ctx.content, arg)
        elif what == "origin":
            year = draft.state.birth_year + draft.state.age_years
            if not creator.set_origin(ctx.content, draft, arg, year):
                note = "Эта предыстория не подходит к выбранному набору."
        return await name_screen(ctx, game.draft_to_dict(draft), note)
    return await name_screen(ctx, data)


@on_screen("edlook")
async def edlook_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.engine import creator

    data = ctx.state.get("draft")
    if not data:
        return await preset_screen(ctx)
    draft = game.draft_from_dict(data)
    if action == "cyc" and arg:
        creator.cycle_look(draft, arg)
        return _look_screen(ctx, game.draft_to_dict(draft))
    if action == "rnd":
        creator.random_look(random.Random(), draft)
        return _look_screen(ctx, game.draft_to_dict(draft))
    return await name_screen(ctx, data)


@on_screen("nameinput")
async def name_input_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    data = ctx.state.get("draft")
    if not data:
        return await preset_screen(ctx)
    return await name_screen(ctx, data)  # Cancel: back to the proposed name, the wait is dropped


@on_text("char_name")
async def typed_name(ctx: Ctx, text: str) -> Screen | None:
    data = ctx.state.get("draft")
    if not data:
        return await preset_screen(ctx)
    name, error = validate_name(ctx.content, text)
    if name is None:
        return name_input_screen(ctx.content, data, error)
    draft = game.draft_from_dict(data)
    game.rename_draft(random.Random(), ctx.content, draft, name)
    return await name_screen(ctx, game.draft_to_dict(draft))


@on_text("char_surname")
async def typed_surname(ctx: Ctx, text: str) -> Screen | None:
    data = ctx.state.get("draft")
    if not data:
        return await preset_screen(ctx)
    name, error = validate_name(ctx.content, text)
    if name is None:
        return name_input_screen(ctx.content, data, error, surname=True)
    draft = game.draft_from_dict(data)
    draft.state.surname = character_gen.surname_for(ctx.content, name, str(draft.state.flags.get("gender") or "m"))
    return await name_screen(ctx, game.draft_to_dict(draft))


MONTHS = ("январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре",
          "декабре")
QUINTILE_RU = {1: "в числе беднейших 20% города", 2: "доход ниже среднего", 3: "средний доход",
               4: "доход выше среднего", 5: "в числе богатейших 20% города"}


def birth_card(content: Content, data: dict, year: int) -> dict:
    """Owner request 26.10 (after 1in8b.life): the birth registration of a new life. The same dict goes to the screen
    state (`birth`), so the Mini App can draw it as a card."""
    from app.engine import creator

    draft = game.draft_from_dict(data)
    st = draft.state
    c = content.balance.birth_card
    rng = random.Random(f"{st.name}|{st.surname}|{st.birth_year}|{draft.origin_key}")
    origin = content.origins.get(draft.origin_key)
    q = int(c.quintile.get(draft.origin_key, 2))
    country = content.countries.get(str(st.flags.get("birth_country") or ""))
    district = content.districts.get(origin.start_district if origin else st.district)
    place = country.name if country else (f"Найт-Сити, {district.name}" if district else "Найт-Сити")
    h = content.heritages.get(creator.heritage_of(st) or "")
    parents = [n for n in st.npcs if n.kind == "family" and n.role == "parent"]
    jobs = list(c.jobs.get(q) or c.jobs.get(2) or ["работа без названия"])
    rng.shuffle(jobs)
    pa = []
    for i, word in enumerate(("Мать", "Отец")):
        name = parents[i].name.split()[0] if i < len(parents) and parents[i].name else ""  # the surname is the card's
        pa.append({"who": word, "name": name, "age": 18 + rng.randint(2, 14), "job": jobs[i % len(jobs)]})
    order = rng.choice(c.orders)
    return {
        "no": f"NC-{year - st.age_years}-{rng.randint(1_000_000, 9_999_999)}",
        "name": views.full_name(st), "month": MONTHS[rng.randrange(12)], "year": year - st.age_years,
        "gender": "мужской" if st.flags.get("gender") == "m" else "женский",
        "place": place, "roots": h.name if h else "", "quintile": q, "income": QUINTILE_RU.get(q, ""),
        "parents": pa, "order": order, "origin": origin.name if origin else "",
        "story": origin.description if origin else "", "age": st.age_years,
    }


async def birth_screen(ctx: Ctx, data: dict) -> Screen:
    b = birth_card(ctx.content, data, await world_year(ctx))
    dots = "●" * b["quintile"] + "○" * (5 - b["quintile"])
    par = " · ".join(f"{p['who']}, {p['age']}" + (f" ({texts.esc(p['name'])})" if p["name"] else "") for p in b["parents"])
    work = " · ".join(f"{p['who'].lower()}: {texts.esc(p['job'])}" for p in b["parents"])
    lines = [f"🪪 <b>Регистрация рождения</b> · <code>№ {b['no']}</code>", "",
             f"<b>{texts.esc(b['name'])}</b>",
             f"Рождение в {b['month']} {b['year']} года, {texts.esc(b['place'])}. Семья: {b['income']}. {b['order']}.",
             "",
             f"<b>Пол</b>: {b['gender']}" + (f" · <b>Корни</b>: {texts.esc(b['roots'])}" if b["roots"] else ""),
             f"<b>Достаток семьи</b>: {dots}",
             f"<b>Родители</b>: {par}",
             f"<b>Работа родителей</b>: {work}",
             f"<b>Предыстория</b>: {texts.esc(b['origin'])}",
             "",
             f"<i>{texts.esc(b['story'])}</i>",
             "",
             f"Жизнь начинается в {b['age']}. Дальше — как сложится."]
    rows = [[Button("Начать эту жизнь", "go", style="success")], [Button("Изменить", "edit")]]
    return Screen("\n".join(lines), rows, name="birth", state={"draft": data, "birth": b})


@on_screen("birth")
async def birth_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    data = ctx.state.get("draft")
    if not data:
        return await preset_screen(ctx)
    if action == "go":
        return await _save(ctx, data)
    return await name_screen(ctx, data)


async def _save(ctx: Ctx, data: dict) -> Screen | None:
    draft = game.draft_from_dict(data)
    try:
        async with db.session_scope() as s:
            await game.create_character_from_draft(
                s, ctx.content, ctx.user_id, ctx.username, draft, ctx.ui.settings.timezone
            )
    except ValueError:  # already has a character: a double click got through
        return await menu_screen(ctx)
    year = await world_year(ctx)
    st = draft.state
    return await menu_screen(ctx, greeting_text(ctx, st.district, st.name, year))


async def entry_screen(ctx: Ctx) -> Screen:
    """What /play opens: the menu of a living character, else character creation."""
    return await menu_screen(ctx)

