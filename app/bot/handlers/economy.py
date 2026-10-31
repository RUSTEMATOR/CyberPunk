"""Screens of the upkeep (owner request, docs/PLAN_ECONOMY.md).

ripper -> immuno (the suppressant course) | clinic (rehab of the drug habit; humanity and Mk come later)
property -> lifestyle | staff
crime -> dealer (drugs)
"""

from __future__ import annotations

import random

from app.bot import effects_text, texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import upkeep as up
from app.services import economy as svc
from app.services import game
from app.services.game import ActiveCharacter

WEEKS = 4  # a press of «Прожить» lives four weeks: prices are shown per week and per press

BLOCKED = {
    svc.R_GONE: "Этого уже нет.",
    svc.R_BLACK: "Это достают только через чёрный рынок.",
    svc.R_EDDIES: "Не хватает эдди.",
    svc.R_STATUS: "С таким статусом этот человек к тебе не пойдёт.",
    svc.R_HAVE: "Уже работает на тебя.",
    svc.R_NO_AP: "ОД кончились. Нажми «Прожить месяц».",
    svc.R_JAIL: "За решёткой этого не достать.",
    svc.R_CLEAN: "Тут лечить нечего.",
}


def _mods(mods: dict) -> str:
    return effects_text.cap(", ".join(effects_text.mods_phrases(mods))) if mods else ""


def _fx(effects: dict) -> str:
    out = []
    for k, v in effects.items():
        label = texts.EFFECT_LABELS.get(k)
        if label and isinstance(v, int) and not isinstance(v, bool):
            out.append(f"{label} {style.signed(v)}")
    return ", ".join(out)


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


def _note(lines: list[str], note: str | None) -> list[str]:
    return lines + ["", note] if note else lines


# -- immunosuppressants -------------------------------------------------------------------------------------------------


def immuno_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    load = up.chrome_load(st, content)
    need = up.needed_grade(st, content)
    cur = up.grade(content, st.flags.get(up.FLAG_IMMUNO))
    lines = [style.header("ripper", "Иммуноподавители"), f"Эдди: {views.money(st.eddies)}", "",
             f"Нагрузка хрома на тело: <b>{load}</b>."]
    miss = content.balance.economy.immuno.miss
    lines.append("<i>Подавители не дают телу отторгать хром и глушат фоновую боль, поэтому поднимают настроение каждую "
                 "оплаченную неделю (сильнее курс — больше). Пока курс не слабее нужного, штрафов нет. Если хром телу "
                 "не мешает, курс не покупается и настроение не даёт.</i>")
    if need is None:
        lines.append("Тело справляется само: подавители не нужны, деньги за курс не снимаются." if load
                     else "Хрома нет, подавители не нужны.")
    else:
        lines.append(f"Нужны как минимум: <b>{texts.esc(need.name)}</b>. Без них (или с курсом слабее) каждая неделя "
                     f"стоит: здоровье −{miss.hp}, настроение −{miss.mood} за каждую ступень нехватки; со "
                     f"{miss.humanity_from_week}-й недели подряд ещё человечность −{miss.humanity}; с "
                     f"{miss.reject_from_week}-й — шанс {round(miss.reject_chance * 100)}%, что тело вытолкнет имплант.")
        if cur is None:
            lines.append("⚠️ Курса нет: штрафы идут каждую неделю.")
        elif cur.strength < need.strength:
            lines.append("⚠️ Курс слабее нужного: штрафы идут каждую неделю.")
        elif cur.strength > need.strength:
            lines.append(f"✅ Курс держит хром и сильнее, чем нужно: защиты больше не станет, зато настроение +{cur.mood} "
                         f"в неделю. Хватит «{texts.esc(need.name)}» за {views.money(up.immuno_price(st, content, need))}/нед.")
        else:
            lines.append("✅ Курс держит хром. Штрафов нет.")
    if int(st.flags.get(up.FLAG_IMMUNO_MISS, 0) or 0):
        lines.append(f"<i>Недель без нужного курса подряд: {st.flags[up.FLAG_IMMUNO_MISS]}.</i>")
    lines.append(f"Сейчас: <b>{texts.esc(cur.name) if cur else 'ничего'}</b>.")
    lines.append("<i>Цена зависит от нагрузки и того, как тело принимает хром. Деньги снимаются каждую неделю.</i>")
    rows: list[list[Button]] = []
    for g in up.grades(content):
        price = up.immuno_price(st, content, g)
        enough = need is None or g.strength >= need.strength
        mark = " · <i>не хватит</i>" if not enough else (
            " · <i>сильнее, чем нужно</i>" if need is not None and g.strength > need.strength else
            (" · <i>как раз</i>" if need is not None else ""))
        lock = " · <i>только чёрный рынок</i>" if not up.can_take(st, g) else ""
        lines.append("")
        lines.append(f"<b>{texts.esc(g.name)}</b> · {views.money(price)}/нед. ({views.money(price * WEEKS)} за месяц)"
                     f" · настроение +{g.mood}/нед.{mark}{lock}\n<i>{texts.esc(g.description)}</i>")
        if up.can_take(st, g) and (cur is None or cur.key != g.key):
            rows.append([Button(f"Курс: {g.name}"[:30], "set", g.key)])
    if cur is not None:
        rows.append([Button("Бросить курс", "off")])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_note(lines, note)), rows, name="immuno")


@on_screen("immuno")
async def immuno_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "ripper")
    c = ctx.content
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if action == "set" and arg:
            why = await svc.set_immuno(s, c, ch, ctx.ui.settings.timezone, arg)
            note = BLOCKED.get(why, why) if why else "Курс назначен. Деньги будут сниматься каждую неделю."
        elif action == "off":
            await svc.set_immuno(s, c, ch, ctx.ui.settings.timezone, None)
            note = "Курс брошен."
    return immuno_screen(c, ch, note)


# -- the rehab clinic ----------------------------------------------------------------------------------------------------


def clinic_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    lines = [style.header("ripper", "Клиника: лечение"), f"Эдди: {views.money(st.eddies)} · ОД: {st.ap}", ""]
    rows: list[list[Button]] = []
    from app.engine import derived

    from app.services import ripper as rip_svc

    rc = content.balance.ripper
    missing = rip_svc.heal_missing(ch, content)
    lines.append(f"<b>Раны</b>: здоровье {st.hp}/{derived.max_hp(st, content)}. Штопают за деньги, без ОД: "
                 f"{rc.heal_per_hp} эдди за единицу здоровья, не меньше {views.money(rc.heal_min)} за визит.")
    if missing > 0:
        heal_row = []
        if missing > rc.heal_step:
            heal_row.append(Button(f"+{rc.heal_step} · {views.money(rip_svc.heal_price(content, rc.heal_step))}", "heal",
                                   str(rc.heal_step)))
        heal_row.append(Button(f"Полностью · {views.money(rip_svc.heal_price(content, missing))}"[:24], "heal", "all"))
        rows.append(heal_row)
    else:
        lines.append("Ран нет.")
    lines.append("")
    nr = content.balance.economy.neuro
    lines.append(f"<b>Нейрореабилитация</b>: курс возвращает до {nr.humanity} человечности, которую забрал хром, — "
                 "навсегда, с ростом потолка и Эмпатии. Раз в месяц, 1 ОД, каждый следующий курс дороже.")
    lines.append(f"Потеряно навсегда: {st.humanity_lost}. Человечность {st.humanity}/{derived.humanity_max(st, content)}.")
    if st.humanity_lost > 0:
        price = svc.neuro_price(content, ch)
        lines.append(f"Курс: {views.money(price)}.")
        rows.append([Button(f"Нейрокурс · {price}"[:24], "neuro")])
    lines.append("")
    lines.append("<b>Инфекции</b>")
    from app.engine import ripper as rip_eng

    if rip_eng.infected(st):
        cure = rip_eng.cure_price(content)
        lines.append(f"🦠 В тебе сидит инфекция: каждую неделю она отнимает здоровье ({content.balance.cyber.infection_damage_dice}), "
                     f"пока её не вылечить. Курс антибиотиков и чистка порта: {views.money(cure)} эдди, "
                     f"{content.balance.ripper.cure_ap} ОД, без проверки. Можно и самому — у любого риппера в карточке "
                     "(ТЕХ + медицина), или у уличного медика в городе.")
        rows.append([Button(f"Вылечить инфекцию · {cure}"[:30], "cure")])
    else:
        lines.append("Заразы нет. Подцепить её можно у подпольного риппера или в грязной драке.")
    lines.append("")
    lines.append("<b>Зависимости</b>")
    hb = up.habit(st)
    th = content.balance.economy.drugs.addiction_threshold
    if not hb:
        lines.append("Зависимостей нет. Доктор доволен и немного удивлён.")
    else:
        lines.append("Курс лечения стоит денег и 1 ОД. Помогает не всегда: чем дольше стаж, тем меньше шанс.")
    for k, pts in hb.items():
        d = up.drug(content, k)
        if d is None:
            continue
        price = up.rehab_price(st, content, k)
        chance = round(up.rehab_chance(st, content, k) * 100)
        state = "зависимость" if pts >= th else "привыкание"
        lines.append("")
        lines.append(f"<b>{texts.esc(d.name)}</b> · {state} ({pts}) · лечение {views.money(price)}, шанс {chance}%")
        rows.append([Button(f"Лечить: {d.name} ({chance}%)"[:30], "rehab", k)])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_note(lines, note)), rows, name="clinic")


@on_screen("clinic")
async def clinic_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "ripper")
    c = ctx.content
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "neuro":
            why, paid, back = await svc.neuro_rehab(s, c, ch, year, ctx.ui.settings.timezone)
            if why == svc.R_HAVE:
                note = "Мозгу нужно время: следующий курс — в следующем месяце."
            elif why:
                note = BLOCKED.get(why, why)
            else:
                note = f"Неделя в капсуле, разговоры, сканы. Человечность +{back} навсегда. −{views.money(paid)}."
        elif action == "heal":
            from app.services import ripper as rip_svc

            amount = int(arg) if arg and arg.isdigit() else None
            why, hp, paid = await rip_svc.heal_wounds(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, amount)
            if why == "full":
                note = "Ран нет. Риппер пожимает плечами."
            elif why == "eddies":
                note = f"Не хватает эдди: нужно {views.money(paid)}."
            else:
                note = f"Швы, скобы, стимулятор в вену. Здоровье +{hp}. −{views.money(paid)}."
        elif action == "cure":
            from app.services import ripper as rip_svc

            r = await rip_svc.cure_at_clinic(s, c, ch, random.Random(), year, ctx.ui.settings.timezone)
            if r.ok:
                note = f"Капельница, чистка порта, горсть таблеток. Инфекция ушла. −{views.money(r.paid)}."
            elif r.reason == rip_svc.NO_AP:
                note = BLOCKED[svc.R_NO_AP]
            elif r.reason == "eddies":
                note = BLOCKED[svc.R_EDDIES]
            else:
                note = "Лечить нечего."
        elif action == "rehab" and arg:
            res = await svc.rehab(s, c, ch, year, ctx.ui.settings.timezone, arg, random.Random())
            if res.blocked:
                note = BLOCKED.get(res.blocked, res.blocked)
            elif res.ok:
                note = (f"Курс прошёл: тяга к «{texts.esc(res.name)}» ушла. Врач жмёт руку и просит не возвращаться. "
                        f"−{views.money(res.price)}.")
            else:
                note = (f"Две недели капельниц, а тяга к «{texts.esc(res.name)}» осталась. Деньги не вернут. "
                        f"−{views.money(res.price)}.")
    return clinic_screen(c, ch, note)


# -- lifestyle -----------------------------------------------------------------------------------------------------------


def lifestyle_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    cur = up.lifestyle(st, content)
    cfg = content.balance.economy.lifestyle
    lines = [style.header("property", "Уровень жизни"), f"Эдди: {views.money(st.eddies)}", "",
             "Сколько тратишь на себя каждую неделю. Деньги снимаются сами; не хватило — уровень падает до «Скромно». "
             f"К хорошему привыкаешь: после {cfg.habit_weeks} недель на уровне понижение бьёт по настроению."]
    rows: list[list[Button]] = []
    for lvl in up.levels(content):
        mark = " · <i>сейчас</i>" if cur is not None and cur.key == lvl.key else ""
        cost = f"{views.money(lvl.weekly)}/нед." if lvl.weekly else "бесплатно"
        lines.append("")
        lines.append(f"<b>{texts.esc(lvl.name)}</b> · {cost}{mark}\n{texts.esc(_mods(lvl.mods))}\n<i>{texts.esc(lvl.description)}</i>")
        if cur is None or cur.key != lvl.key:
            rows.append([Button(lvl.name, "set", lvl.key)])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_note(lines, note)), rows, name="lifestyle")


@on_screen("lifestyle")
async def lifestyle_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "property")
    c = ctx.content
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if action == "set" and arg:
            why = await svc.set_lifestyle(s, c, ch, ctx.ui.settings.timezone, arg)
            note = BLOCKED.get(why, why) if why else "Уровень жизни изменён."
    return lifestyle_screen(c, ch, note)


# -- staff ---------------------------------------------------------------------------------------------------------------


def staff_screen(content: Content, ch: ActiveCharacter, year: int, note: str | None = None) -> Screen:
    st = ch.state
    have = {m.key for m in up.staff(st, content)}
    lines = [style.header("profile", "Персонал"), f"Эдди: {views.money(st.eddies)} · Статус: {st.status}", "",
             "Люди, которые работают на тебя. Зарплата снимается каждую неделю; не заплатишь — уходят."]
    rows: list[list[Button]] = []
    for m in content.balance.economy.staff:
        if m.available_from and m.available_from > year:
            continue
        mark = " · <i>работает</i>" if m.key in have else ""
        need = f" · статус {m.min_status}+" if m.min_status else ""
        lines.append("")
        lines.append(f"<b>{texts.esc(m.name)}</b> · {views.money(m.weekly)}/нед., найм {views.money(m.hire_fee)}{need}{mark}\n"
                     f"{texts.esc(_mods(m.mods))}\n<i>{texts.esc(m.description)}</i>")
        rows.append([Button(f"Уволить: {m.name}"[:30], "fire", m.key)] if m.key in have
                    else [Button(f"Нанять: {m.name}"[:30], "hire", m.key)])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_note(lines, note)), rows, name="staff")


@on_screen("staff")
async def staff_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "property")
    c = ctx.content
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "hire" and arg:
            why = await svc.hire(s, c, ch, year, ctx.ui.settings.timezone, arg)
            note = BLOCKED.get(why, why) if why else "Новый человек в команде."
        elif action == "fire" and arg:
            await svc.fire(s, c, ch, ctx.ui.settings.timezone, arg)
            note = "Уволен. Без выходного пособия."
    return staff_screen(c, ch, year, note)


# -- the dealer ----------------------------------------------------------------------------------------------------------


def dealer_screen(content: Content, ch: ActiveCharacter, year: int, note: str | None = None, black: bool = False) -> Screen:
    st = ch.state
    hb = up.habit(st)
    th = content.balance.economy.drugs.addiction_threshold
    taken = up.dosed(st)
    lines = [style.header("crime", "Барыга" if not black else "Чёрный рынок: вещества"), f"Эдди: {views.money(st.eddies)}", "",
             "Доза действует до следующего «Прожить месяц». Каждая доза — шаг к привыканию; подсел — без дозы ломка. "
             "Вторая доза того же за месяц может кончиться передозом."]
    withdraw = up.withdrawal(st, content)
    if withdraw:
        lines.append("<b>Ломка:</b> " + ", ".join(texts.esc(up.drug(content, k).name) for k in withdraw if up.drug(content, k)))
    rows: list[list[Button]] = []
    for d in content.balance.economy.drugs.drugs:
        if d.black_market != black:
            continue
        pts = hb.get(d.key, 0)
        habit_line = (" · <b>зависимость</b>" if pts >= th else f" · привыкание {pts}/{th}") if pts else ""
        dose = " · <i>принято</i>" if d.key in taken else ""
        side = _fx(d.effects)
        lines.append("")
        lines.append(f"<b>{texts.esc(d.name)}</b> · {views.money(d.price)}{habit_line}{dose}\n"
                     f"{texts.esc(_mods(d.mods))}" + (f"; сразу: {side}" if side else "") +
                     f"\nЛомка: {texts.esc(_mods(d.withdrawal))}\n<i>{texts.esc(d.description)}</i>")
        rows.append([Button(f"{d.name} · {d.price}"[:24], "dose", d.key)])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_note(lines, note)), rows, name="dealer", state={"black": black})


@on_screen("dealer")
async def dealer_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    black = bool(ctx.state.get("black"))
    if action == "back":
        return await goto(ctx, "blackmarket" if black else "crime")
    c = ctx.content
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "dose" and arg:
            res = await svc.take_dose(s, c, ch, year, ctx.ui.settings.timezone, arg, random.Random(), black_market=black)
            if res.blocked:
                note = BLOCKED.get(res.blocked, res.blocked)
            else:
                note = f"Доза «{texts.esc(res.name)}» принята. Мир становится ярче."
                if res.overdose:
                    note += " <b>Передоз</b>: сердце скачет, пол уходит из-под ног."
                if res.hooked:
                    note += " <b>Зависимость.</b> Без дозы теперь будет ломка."
                if res.applied is not None:
                    extra = views.effects_lines(res.applied, c)
                    if extra:
                        note += "\n" + "\n".join(extra)
    return dealer_screen(c, ch, year, note, black)


# -- Mk steps of installed chrome -----------------------------------------------------------------------------------------


def _mk_mods(content: Content, mods: dict, step: int) -> dict:
    from math import ceil

    steps = content.balance.economy.mk.steps
    if not 0 < step <= len(steps):
        return dict(mods)
    mult = steps[step - 1].mod_mult
    return {k: (ceil(v * mult) if v > 0 else v) for k, v in mods.items()}


def _mk_change(content: Content, mods: dict, cur: int, has_next: bool) -> str:
    """Owner request 25.10: what the next step changes, «реакция +1 → +2»; at the top, what it gives now."""
    from app.bot import effects_text

    now = _mk_mods(content, mods, cur)
    if not has_next:
        return "Сейчас: " + effects_text.mods_line(now)
    nxt = _mk_mods(content, mods, cur + 1)
    changed = []
    for k in mods:
        if now.get(k) == nxt.get(k):
            continue
        ph, sv = effects_text.mod_phrase(k, now[k]), effects_text.signed(now[k])
        changed.append(ph.replace(sv, f"{sv} → {effects_text.signed(nxt[k])}", 1) if sv in ph
                       else f"{ph} → {effects_text.mod_phrase(k, nxt[k])}")
    same = {k: v for k, v in now.items() if now.get(k) == nxt.get(k)}
    out = "Улучшится: " + ", ".join(changed) if changed else "Ступень почти ничего не меняет"
    if same:
        out += ". Без изменений: " + effects_text.mods_line(same)
    return out


def chromeup_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    from app.engine import upkeep as upk

    st = ch.state
    steps = content.balance.economy.mk.steps
    lines = [style.header("ripper", "Прокачка хрома"), f"Эдди: {views.money(st.eddies)} · ОД: {st.ap}", "",
             "Риппер перебирает уже стоящий имплант: " + ", ".join(
                 f"{x.name} — бонусы ×{x.mod_mult:g}" for x in steps)
             + ". Минусы импланта не меняются, нагрузка на иммунитет растёт на четверть за ступень. Человечность не тратится, 1 ОД."]
    rows: list[list[Button]] = []
    if not st.cyberware:
        lines.append("")
        lines.append("Хрома нет — прокачивать нечего.")
    for item in st.cyberware:
        cw = content.cyberware.get(item.key)
        if cw is None or not cw.mods:
            continue
        cur = upk.mk_of(st, item.key)
        price = svc.mk_price(content, item.key, cur + 1)
        tag = f" · {steps[cur - 1].name}" if cur else ""
        lines.append("")
        nxt = f"{steps[cur].name}: {views.money(price)}" if price is not None else "максимум"
        lines.append(f"<b>{texts.esc(cw.name)}</b>{tag} · {nxt}")
        lines.append(f"<i>{texts.esc(_mk_change(content, cw.mods, cur, price is not None))}</i>")  # owner request 25.10
        if price is not None:
            rows.append([Button(f"{cw.name[:14]} → {steps[cur].name}", "mk", item.key)])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_note(lines, note)), rows, name="chromeup")


@on_screen("chromeup")
async def chromeup_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "ripper")
    c = ctx.content
    note = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "mk" and arg:
            why, price = await svc.mk_up(s, c, ch, year, ctx.ui.settings.timezone, arg)
            note = BLOCKED.get(why, why) if why else f"Готово: имплант перебран и разогнан. −{views.money(price)}."
    return chromeup_screen(c, ch, note)


# -- the black market ------------------------------------------------------------------------------------------------------


def blackmarket_screen(content: Content, ch: ActiveCharacter, year: int, note: str | None = None) -> Screen:
    from app.services import blackmarket as bm

    st = ch.state
    cfg = content.balance.economy.black_market
    lines = [style.header("crime", "Чёрный рынок"), f"Эдди: {views.money(st.eddies)}", ""]
    rows: list[list[Button]] = []
    why = bm.blocked(content, ch)
    if why == bm.R_CRED:
        lines.append(f"Сюда пускают своих: нужна уличная репутация {cfg.min_street_cred}+.")
    elif why == bm.R_JAIL:
        lines.append("За решёткой рынок другой.")
    else:
        lines.append("Прототипы из лабораторий, которые официально появятся через годы. Дорого, без гарантий: "
                     f"иногда продавец исчезает с деньгами. Каждая сделка — розыск +{cfg.heat}. "
                     "Витрина меняется каждый месяц.")
        lots = bm.showcase(content, ch, year)
        if not lots:
            lines.append("")
            lines.append("Витрина пуста. Загляни в следующем месяце.")
        for lot in lots:
            lines.append("")
            lines.append(f"<b>{texts.esc(lot.name)}</b> · {bm.KIND_NAMES[lot.kind]} из {lot.year} года · {views.money(lot.price)}")
            rows.append([Button(f"{lot.name[:16]} · {lot.price}"[:24], "buy", f"{lot.kind}:{lot.key}")])
        lines.append("")
        if st.flags.get("immuno_black"):
            lines.append("Поставщик «Чёрного льда» знает тебя: курс доступен у риппера.")
        else:
            lines.append(f"Поставщик «Чёрного льда» (подавители для тех, в ком хрома больше, чем мяса): "
                         f"знакомство {views.money(cfg.black_ice_contact)}.")
            rows.append([Button("Поставщик Чёрного льда", "ice")])
        rows.append([Button("Вещества", "drugs")])
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(_note(lines, note)), rows, name="blackmarket")


@on_screen("blackmarket")
async def blackmarket_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.services import blackmarket as bm

    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "crime")
    c = ctx.content
    note = None
    msgs = {bm.R_CRED: "Сюда пускают своих.", bm.R_GONE: "Этого уже нет.", bm.R_EDDIES: "Не хватает эдди.",
            bm.R_HAVE: "Это у тебя уже есть.", bm.R_JAIL: "За решёткой рынок другой."}
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        if action == "drugs":
            return dealer_screen(c, ch, year, None, black=True)
        if action == "ice":
            why = await bm.black_ice_contact(s, c, ch, year, ctx.ui.settings.timezone)
            note = msgs.get(why, why) if why else "Тебе дают номер без имени. Курс «Чёрного льда» теперь можно назначить у риппера."
        elif action == "buy" and arg:
            res = await bm.buy(s, c, ch, year, ctx.ui.settings.timezone, arg, random.Random())
            if res.blocked:
                note = msgs.get(res.blocked, res.blocked)
            elif res.scam:
                note = f"Продавец берёт {views.money(res.lot.price)}, уходит «за товаром» и не возвращается. Добро пожаловать на рынок."
            else:
                note = f"Сделка: «{texts.esc(res.lot.name)}» — теперь у тебя."
                if res.complication:
                    note += " Рыночный риппер торопился: шов кривой, человечности ушло больше."
                for a in res.applied[1:]:
                    extra = views.effects_lines(a, c)
                    if extra:
                        note += "\n" + "\n".join(extra)
                if res.lines:
                    note += "\n" + "\n".join(res.lines)
    return blackmarket_screen(c, ch, year, note)
