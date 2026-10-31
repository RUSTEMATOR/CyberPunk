"""Main menu, profile, the 'soon' stub and the error screen (PLAN 3.2, 3.3).

Every section of DESIGN.md section 4 has a button. A section that has no screen of its own yet
(no handler registered under its name) opens the 'soon' stub with a Back button; when a later
milestone registers `@on_screen("gigs")` the same button opens the real screen.
"""

from __future__ import annotations

import logging
import random
from typing import Sequence

from app.bot import texts, views
from app.bot import texts_death as death_texts
from app.bot.ui.callbacks import Ctx, goto, has_screen, on_screen
from app.bot.ui import style
from app.bot.ui.screen import DEFAULT_BUTTON_CHARS, Button, Screen, row_budget, width
from app.db import session as db
from app.engine import ap as engine_ap
from app.engine import jail as jail_engine
from app.engine.state import FLAG_DEATH_PENDING, FLAG_FUGITIVE
from app.services import ap as ap_services
from app.services import events as ev_service
from app.services import game, notices


async def world_year(ctx: Ctx) -> int:
    async with db.session_scope() as s:
        return await game.current_year(s, ctx.content)


JAIL_SECTIONS: tuple[str, ...] = ("profile", "activities", "relations", "week")  # what a cell allows from the old menu
FUGITIVE_SECTIONS: tuple[str, ...] = ("profile", "ripper", "activities", "relations", "week", "city")  # what a hunted psycho may open


def menu_keyboard(ap_zero: bool = False, budgets: Sequence[int] = DEFAULT_BUTTON_CHARS) -> list[list[Button]]:
    """All sections, or at 0 AP only the profile and 'live a week' (PLAN 4.1). Every button carries its section icon;
    labels that fit two to a row on a narrow phone are paired, the longer ones get a row of their own (in menu order)."""
    sections = [
        (key, label) for key, label in texts.MENU_SECTIONS
        if not ap_zero or key in ap_services.AP_ZERO_SECTIONS
    ]
    buttons = [Button(style.label(key, label), "go", key) for key, label in sections if key != "week"]
    short = [b for b in buttons if width(b.text) <= row_budget(2, budgets)]
    rows = [short[i:i + 2] for i in range(0, len(short), 2)]
    rows += [[b] for b in buttons if b not in short]
    rows += [[Button(style.label(key, label), "go", key)] for key, label in sections if key == "week"]
    rows.append([Button("Баг или идея", "go", "reqask"), Button(texts.CLOSE, "close")])  # owner request: requests
    return rows


async def menu_screen(ctx: Ctx, greeting: str | None = None) -> Screen:
    """The main menu of the player's living character; no character leads to creation."""
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
        news = await notices.take(s, ch, ctx.ui.settings.timezone) if ch is not None else []
        if ch is not None and not ch.state.flags.get("netrun_refund_v1"):  # owner request 24.10
            from app.services import migrations

            news = migrations.refund_netrun_humanity(ctx.content, ch, year) + news
            await game.save_active(s, ch, ctx.ui.settings.timezone)
        if ch is not None and not ch.state.flags.get("monk_fix_v1"):  # owner report 24.10: random monks
            from app.services import migrations

            news = migrations.monk_fix(ctx.content, ch) + news
            await game.save_active(s, ch, ctx.ui.settings.timezone)
        if ch is not None and ch.state.flags.get("abroad"):  # owner request 25.10: the cities of the world
            from app.engine import abroad as abroad_engine

            if abroad_engine.needs_move_in(ch.state, ctx.content):
                from app.services import migrations

                news = migrations.abroad_move_in(ctx.content, ch) + news
                await game.save_active(s, ch, ctx.ui.settings.timezone)
        if ch is not None and not ev_service.has_pending(ch):  # owner request 26.10: the police come in waves
            from app.services import wanted as wanted_svc

            had = dict(ch.state.flags.get("chase") or {}) if isinstance(ch.state.flags.get("chase"), dict) else None
            wline = wanted_svc.tick(ctx.content, ch)
            if not wline:  # owner request 26.10: project «Каприкорн»: the host's copy, the corp hunters
                from app.services import capri as capri_svc

                had_c = ch.state.flags.get("capri_host")
                await capri_svc.sync(s, ch)
                wline = capri_svc.hunter_tick(ctx.content, ch, random.Random(), year)
                if wline or had_c != ch.state.flags.get("capri_host"):
                    await game.save_active(s, ch, ctx.ui.settings.timezone)
            if not wline:  # owner request 26.10: gangs on top of an unbalanced city riot in the streets
                from app.services import equilib as equilib_svc

                wline = equilib_svc.riot(ctx.content, ch, random.Random(), year)
                if wline:
                    await game.save_active(s, ch, ctx.ui.settings.timezone)
            if wline or had != ch.state.flags.get("chase"):
                await game.save_active(s, ch, ctx.ui.settings.timezone)
            if wline:
                news = [wline] + news
        if ch is not None and ch.state.flags.get("dg_enter"):  # owner request 24.10: the deadly games begin
            from app.services import dgame as dgame_service

            line = await dgame_service.enter_if_due(s, ctx.content, ch, random.Random(), ctx.ui.settings.timezone)
            if line:
                news = [line] + news
    if ch is None:
        if has_screen("legacy"):  # a dead character's heir is chosen before a new life starts (PLAN 7.3)
            screen = await goto(ctx, "legacy")
            if screen is not None:
                return screen
        screen = await goto(ctx, "preset")
        assert screen is not None
        return screen
    if has_screen("aimode"):  # the Blackwall ending: the character plays as a rogue AI
        from app.bot.handlers.ai import ai_screen, ascend_if_due

        if ch.state.flags.get("ascend_ai"):
            await ascend_if_due(ctx)
        if ch.state.flags.get("ascend_ai") or ch.state.flags.get("ai_mode"):
            scr = await ai_screen(ctx)
            if scr is not None:
                return scr
    if ch.state.flags.get(FLAG_DEATH_PENDING) and has_screen("deathoffer"):
        return await goto(ctx, "deathoffer")  # a failed death save waits for the glitch decision (PLAN 7.1)
    if ev_service.has_pending(ch) and has_screen("evchoice"):
        return await goto(ctx, "evchoice", "resume")  # an unfinished event comes before the menu
    if ch.state.flags.get("raven_hunt") and not jail_engine.in_jail(ch.state) and has_screen("ravenhunt"):
        from app.bot.handlers.blacktears import hunt_screen  # owner request 26.10: Raven came for this character

        return hunt_screen(ctx, ch)
    if jail_engine.in_jail(ch.state) and has_screen("jail"):
        return await goto(ctx, "jail")  # a cell has its own, shorter menu (PLAN 9.5)
    if ch.state.flags.get("dgame") and has_screen("dgame"):
        return await goto(ctx, "dgame", "open")  # owner request 24.10: the island has no menu
    if ch.state.flags.get("sortie") and has_screen("sortie"):
        return await goto(ctx, "sortie", "open")  # owner request 24.10: a sortie runs until the exit
    if ch.state.flags.get(FLAG_FUGITIVE) and has_screen("fugitive"):
        return await goto(ctx, "fugitive")  # a hunted psycho has a reduced menu too (PLAN 6.6a)
    if has_screen("plist"):  # letters from other players (docs/EPIC_PLAYERS.md) arrive with the menu
        from app.bot.handlers.players import deliver_for_menu  # local: players imports this module

        news = await deliver_for_menu(ctx, ch) + news
    from app.engine import perks as perks_engine  # owner request 24.10: free points are shown in the menu

    _pr = perks_engine.rec(ch.state, ctx.content) if isinstance(ch.state.flags.get(perks_engine.FLAG), dict) else None
    if _pr and int(_pr.get("attr", 0)) + int(_pr.get("perk", 0)) > 0:
        news = news + ["⭐ Есть очки прокачки: «Профиль» → «Прокачка»."]
    import random as _random

    from app.engine import blackwall as bw_engine  # owner request 24.10: the Blackwall is always near, unseen

    _wh = bw_engine.whisper(ch.state, ctx.content, _random.Random())
    if _wh:
        news = news + [_wh]
    ap_zero = engine_ap.is_ap_zero(ch.state)
    header = views.menu_header(ctx.content, ch.state, year)
    if ap_zero:
        header += "\n" + texts.MENU_AP_ZERO
    keyboard = menu_keyboard(ap_zero, ctx.content.balance.ui.button_chars)
    if has_screen("raid"):  # the city raid: a line and a button while a boss is on
        from app.bot.handlers.raid import menu_line  # local: the raid module imports this one
        from app.bot import texts_raid

        raid = await menu_line(ctx)
        if raid is not None:
            header += "\n" + raid[0]
            if not ap_zero:
                keyboard.insert(-1, [Button(style.label("war", texts_raid.MENU_BUTTON), "go", "raid")])
    from app.services import retention  # owner request: first steps and the shared goal of the day

    nxt = retention.next_step_line(ch)  # the full list lives in the profile
    if nxt:
        header += "\n\n" + nxt
    async with db.session_scope() as s:
        goal = retention.goal_text(ctx.content, await retention.goal_state(s, ctx.content, ctx.ui.settings.timezone))
    if goal:
        header += "\n🎯 Цель дня для всех: " + goal
    try:  # owner request 24.10: offers from other players are one tap away when there are any
        from app.services import players as players_svc

        async with db.session_scope() as s:
            waiting = len(await players_svc.offers_for(s, ctx.content, ctx.user_id, year))
    except Exception:  # noqa: BLE001 - the menu must open even if the offers fail
        logging.getLogger(__name__).exception("menu: offers count failed")
        waiting = 0
    if waiting:
        keyboard.insert(0, [Button(f"📨 Предложения ({waiting})", "offers", style="success")])
    try:  # owner request 26.10: a contract of «Чёрные слёзы» waits
        from app.services import blacktears as bt_svc

        async with db.session_scope() as s:
            bt_offer = await bt_svc.offer_of(s, ctx.user_id, ctx.ui.settings.timezone)
        if bt_offer is not None:
            keyboard.insert(0, [Button("🖤 Шёпот Raven", "btears", style="danger")])
    except Exception:  # noqa: BLE001 - the menu must open even if this fails
        logging.getLogger(__name__).exception("menu: blacktears failed")
    try:  # owner request 26.10: the employer is in a fight and this character stands at the side
        from app.services import pally as pally_svc

        async with db.session_scope() as s:
            plive = await pally_svc.live_for(s, ctx.user_id, ctx.content)
        if plive and not plive.get("down"):
            keyboard.insert(0, [Button("⚔️ Бой нанимателя", "pally", style="danger")])
    except Exception:  # noqa: BLE001 - the menu must open even if this fails
        logging.getLogger(__name__).exception("menu: pally failed")
    if ch.state.heat > 0:  # owner request 26.10: the chase and the ways to drop the heat
        from app.bot.handlers.wanted import chase_line

        cl = chase_line(ctx.content, ch)
        if cl:
            header += "\n" + cl
        keyboard.insert(0, [Button(f"🚨 Розыск {ch.state.heat}", "wanted",
                                   style="danger" if cl else None)])
    if ch.state.flags.get("tower_run"):  # owner request 26.10: the tower dungeon in progress
        keyboard.insert(0, [Button("🏢 Вернуться в башню", "tdun", style="danger")])
    try:  # owner request 25.10: an open challenge waits in the menu
        from app.services import challenge as challenge_svc

        async with db.session_scope() as s:
            chop = await challenge_svc.current(s, ch)
        if chop:
            icon = "💼" if chop.get("track") == "wealth" else "⚔️"
            keyboard.insert(0, [Button(f"{icon} Вызов {chop.get('level', 1)}"[:24], "chall", style="danger")])
    except Exception:  # noqa: BLE001 - the menu must open even if the challenge fails
        logging.getLogger(__name__).exception("menu: challenge failed")
    try:  # owner request 24.10: the city crisis is in every menu
        from app.engine import crisis as crisis_engine
        from app.services import crisis as crisis_svc

        async with db.session_scope() as s:
            await crisis_svc.harvest(s, ctx.content, ch, ctx.ui.settings.timezone)
            cline = await crisis_svc.menu_line(s, ch)
            live = await crisis_svc.current(s)
        if cline:
            header += "\n" + cline
        if live:
            cdef = crisis_engine.get(live["key"]) or {}
            keyboard.insert(0, [Button(f"{cdef.get('icon', '🔥')} {cdef.get('name', 'Кризис')}"[:24], "crisis",
                                       style="danger")])
    except Exception:  # noqa: BLE001 - the menu must open even if the crisis fails
        logging.getLogger(__name__).exception("menu: crisis failed")
    text = f"{style.quote(greeting)}\n{header}" if greeting else header
    if news:
        text = style.quote("\n".join(texts.esc_notice(n) for n in news)) + "\n" + text
    return Screen(text, keyboard, name="menu")


def soon_screen(section: str) -> Screen:
    label = texts.SECTION_LABELS.get(section, section)
    return Screen(
        f"{texts.SOON_TITLE.format(section=texts.esc(label))}\n{texts.SOON_TEXT}",
        [[Button(texts.BACK, "back")]],
        name="soon",
        state={"section": section},
    )


@on_screen("menu")
async def menu_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "close":
        await ctx.ui.windows.collapse(ctx.user_id)
        return None
    if action == "btears":  # owner request 26.10: «Чёрные слёзы»
        return await goto(ctx, "btears", "open")
    if action == "pally":  # owner request 26.10: the employer's fight, the own skills
        return await goto(ctx, "pally", "open")
    if action == "wanted":  # owner request 26.10: the chase window
        from app.bot.handlers.wanted import wanted_screen

        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
        return wanted_screen(ctx, ch) if ch is not None else await menu_screen(ctx)
    if action == "tdun":  # owner request 26.10: back into the tower dungeon
        return await goto(ctx, "tdun", "show")
    if action == "chall":  # owner request 25.10: the challenge window
        return await goto(ctx, "chall", "open")
    if action == "crisis":  # owner request 24.10: the city crisis window, open at zero AP too
        return await goto(ctx, "crisis", "open")
    if action == "offers":  # owner request 24.10: offers from other players right from the main menu
        from app.bot.handlers.players import offers_screen

        return await offers_screen(ctx)
    if action == "go" and arg == "reqask":  # owner request: the requests window from the main menu, always open
        from app.bot.handlers.requests import ask_screen

        return ask_screen()
    if action == "go" and arg:
        if arg not in JAIL_SECTIONS:
            async with db.session_scope() as s:
                ch = await game.get_active_character(s, ctx.user_id)
            if ch is not None and jail_engine.in_jail(ch.state):
                return await menu_screen(ctx)  # an old button of the full menu: the cell has its own
        if arg not in FUGITIVE_SECTIONS:
            async with db.session_scope() as s:
                ch = await game.get_active_character(s, ctx.user_id)
            if ch is not None and ch.state.flags.get(FLAG_FUGITIVE):
                return await menu_screen(ctx)  # an old button of the full menu: the hunted have their own
        if arg not in ap_services.AP_ZERO_SECTIONS:
            async with db.session_scope() as s:
                ch = await game.get_active_character(s, ctx.user_id)
            if ch is not None and engine_ap.is_ap_zero(ch.state):
                return await menu_screen(ctx)  # an old button of the full menu: show the AP-zero one
        if has_screen(arg):
            return await goto(ctx, arg)
        return soon_screen(arg)
    return await menu_screen(ctx)


@on_screen("soon")
async def soon_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    return await menu_screen(ctx)


@on_screen("error")
async def error_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    return await menu_screen(ctx)


@on_screen("profile")
async def profile_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await menu_screen(ctx)
    if action == "vows" and has_screen("vows"):
        return await goto(ctx, "vows")
    if action == "perks" and has_screen("perks"):
        return await goto(ctx, "perks", "open")
    if action == "rename":
        from app.bot.handlers.rename import open_rename  # local: avoid an import cycle with onboarding

        screen = await open_rename(ctx)
        if screen is not None:
            return screen
    if action == "lineage" and has_screen("lineage"):
        return await goto(ctx, "lineage")
    if action in ("chronicle", "chron_post"):
        from app.bot.handlers.ending import chronicle_screen  # local: the ending module imports this one

        return await chronicle_screen(ctx, post=action == "chron_post")
    if action == "traits":
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
        if ch is not None:
            return traits_screen(ctx.content, ch)
    if action == "faq":
        return await goto(ctx, "handbook")
    if action == "amb":  # owner request 25.10: the big goals
        return await goto(ctx, "amb", "open")
    if action == "budget":  # owner request 25.10: where the money goes
        return await goto(ctx, "budget", "open")
    if action == "aidash":  # owner request 27.10: what the AI in the character gives and takes
        return await goto(ctx, "aidash", "open")
    if action == "possess":  # owner request 26.10: the AI in the head
        return await goto(ctx, "possess", "open")
    if action == "avatar":  # owner request 26.10: the character's picture
        return await goto(ctx, "avatar", "open")
    if action == "look":  # owner request 26.10: the look editor, the figure in the fight
        return await goto(ctx, "look", "open")
    if action == "chromeab":  # owner request 26.10: what the chrome lets you do
        return await goto(ctx, "chromeab", "open", "profile")
    if action == "idea":  # owner request: requests for fixes and features
        from app.bot.handlers.requests import ask_screen, list_screen

        if arg == "list" and ctx.ui.is_admin(ctx.user_id):
            return await list_screen(ctx)
        return ask_screen()
    if action == "equip":  # owner request: what is in the hand, take and put away from the profile
        from app.bot.handlers.property import equip_screen

        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
        if ch is not None:
            return equip_screen(ctx.content, ch, back="profile")
    if action == "ending":
        from app.bot.handlers.ending import ending_screen  # local: the ending module imports this one
        from app.services import legend

        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            ok = ch is not None and await legend.eligible(s, ctx.content, ch)
        return ending_screen(ok)
    if action == "legends":
        from app.bot.handlers.legends import post_legends  # local: the router module imports the kit

        await post_legends(ctx.ui, ctx.chat_id)
        return None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    if ch is None:
        return await menu_screen(ctx)
    from app.engine import perks as perks_engine  # owner request 24.10: levels and perk trees

    pr = perks_engine.rec(ch.state, ctx.content)
    pts = int(pr.get("attr", 0)) + int(pr.get("perk", 0))
    rows = [
        [Button(f"⭐ Прокачка · ур. {pr['level']}" + (f" · +{pts}" if pts else ""), "perks",
                style="success" if pts else None)],
        [Button(style.label("vows", texts.BTN_VOWS), "vows"), Button(style.label("legends", texts.BTN_LEGENDS), "legends")],
        [Button(texts.BTN_RENAME, "rename"), Button(style.label("lineage", death_texts.BTN_LINEAGE), "lineage")],
        [Button("Летопись", "chronicle"), Button("Черты", "traits")],
        [Button("🏆 Амбиции", "amb"), Button("Бюджет месяца", "budget")],  # owner request 25.10
        [Button("🧍 Внешность", "look"), Button("🖼 Аватар", "avatar")],  # owner request 26.10
        [Button("⚙️ Возможности хрома", "chromeab")],
        [Button("Экипировка", "equip"), Button("Справочник", "faq")],
        [Button("Баг или идея", "idea"), Button("Конец пути", "ending")],
        [Button(texts.MENU, "back")],
    ]
    from app.bot.handlers.aidash import has_ai

    if has_ai(ch):  # owner request 27.10: one dashboard for every AI in the character (was «🧠 В голове»)
        rows.insert(1, [Button("🧬 Мой ИИ", "aidash", style="success")])
    if ctx.ui.is_admin(ctx.user_id):
        rows.insert(-1, [Button("Заявки игроков", "idea", "list")])
    text = views.profile_text(ctx.content, ch.state, year)
    from app.services import bounties, leaderboard, retention  # owner request: titles, bounty, first steps

    async with db.session_scope() as s:
        held = (await leaderboard.titles(s)).get(ctx.user_id, [])
        pot = await bounties.on(s, ch.id)
        from app.services import echoes, family  # owner report 24.10: money put aside was hard to find

        fund = await family.fund_of(s, ctx.user_id)
        stashes = [x for x in await echoes.my_stashes(s, ctx.user_id) if x.status == "hidden"]
    extra = []
    if fund:
        extra.append(f"🏦 Семейный фонд: {views.money(fund)} эдди. Достанется наследнику целиком, забрать нельзя "
                     "(«Отношения» → «Семейный фонд»).")
    if stashes:
        extra.append(f"🗝 Тайники на будущее: {len(stashes)}, всего {views.money(sum(x.eddies for x in stashes))} эдди. "
                     "Их найдёт кто-то другой или наследник («Отношения» → «Свои» → «Тайник»).")
    if held:
        extra.append("🏆 Титулы недели: " + ", ".join(f"«{texts.esc(t)}»" for t in held))
    from app.services import ambition as amb_svc  # owner request 25.10: the legend titles of the ambitions

    if amb_svc.titles(ch.state):
        extra.append("👑 Титулы легенды: " + ", ".join(f"«{texts.esc(t)}»" for t in amb_svc.titles(ch.state)))
    if ch.state.relics:  # owner request 25.10: relics, one of each in the city
        from app.services import relic as relic_svc

        extra.append("💎 Реликвии: " + ", ".join(
            f"{texts.esc(r.name)} («{texts.esc(r.title)}»)" for k in ch.state.relics if (r := relic_svc.get(ctx.content, k))))
    if pot:
        extra.append(f"🎯 За твою голову назначено {views.money(pot)}. Берегись стычек.")
    async with db.session_scope() as s:  # owner request 24.10: the pressure
        from app.services import pressure

        _, ptier = await pressure.snapshot(s, ctx.content, ch)
    extra.append(pressure.profile_line(ctx.content, ch.state, ptier))
    from app.engine import injury as injury_engine  # owner request 26.10: lost limbs and damaged implants

    hurt = injury_engine.describe(ch.state, ctx.content)
    if hurt:
        extra.append("🩸 <b>Увечья</b> (лечит риппер или корпорация клонов)\n" + "\n".join(hurt))
    extra.extend(upkeep_lines(ctx.content, ch))
    steps = retention.menu_block(ch)
    if steps:
        extra.append(steps)
    if extra:
        text += "\n\n" + "\n".join(extra)
    return Screen(text, rows, name="profile")


def upkeep_lines(content, ch) -> list[str]:  # noqa: ANN001
    """Lifestyle, staff, suppressants and the drug habit in the profile (owner request)."""
    from app.engine import upkeep as up

    st = ch.state
    out: list[str] = []
    from app.engine import paths as pe

    wl = views.weather_line(content, ch.state)
    if wl:
        out.append(wl)
    from app.engine import living

    lt = living.tier(st, content)
    lmods = content.balance.living.tier_mods.get(lt) or {}
    lnote = {0: "без крыши над головой: настроение и здоровье падают, болеешь чаще",
             1: "тесно и сыро: настроение и здоровье хуже, иногда болеешь",
             2: "жильё как у всех: ни штрафов, ни бонусов",
             3: "хорошая квартира: настроение, общение, учёба легче",
             4: "лофт: настроение, общение, карьера"}.get(lt, "")
    if lnote:
        out.append(f"🏠 Условия жизни: {lnote}." if lmods or lt == 2 else f"🏠 {lnote}.")
    if living.sick(st):
        out.append(f"🤒 Болеешь ещё {living.sick(st)} нед.: штрафы к проверкам, здоровье падает. Медик в «Городе» вылечит сразу.")
    if st.flags.get("infection"):
        out.append("🦠 Инфекция: каждую неделю минус здоровье. Лечение: «Риппер» → «Клиника: лечение».")
    pd, rk = pe.path_of(st, content), pe.rank_def(st, content)
    if pd is not None and rk is not None:
        out.append(f"🧭 Путь: {texts.esc(pd.name)}, {texts.esc(rk.name)}" + (f" (+{views.money(rk.income)}/мес.)" if rk.income else ""))
    from app.engine import chrono as chrono_engine
    from app.engine import companion as comp

    if st.flags.get("corp_asset"):
        out.append(f"🏢 Актив корпорации: крыша от NCPD, но каждый месяц {int(st.flags.get('corp_asset_pct', 3))}% денег уходят куратору.")
    from app.engine import elysium as elysium_engine

    if elysium_engine.citizen(st):
        out.append("🛰 Гражданин Элизиума: бонусы станции, медкапсула раз в месяц.")
    if elysium_engine.radiation(st):
        out.append(f"☢️ Лучевая болезнь: осталось {elysium_engine.radiation(st)} нед. Спасёт медкапсула Элизиума.")
    if chrono_engine.has(st):
        out.append(f"⏳ Часы Эона: осталось {chrono_engine.label(st)} Старость не убьёт, ноль на часах убьёт.")

    for m in comp.active_members(st):  # owner request 24.10: the squad
        out.append(f"🤝 Отряд: {texts.esc(str(m.get('name', '?')))} ({texts.esc(str(m.get('role', '')))}), "
                   f"ещё {int(m.get('until', 0)) - st.weeks_lived} нед.")
    lvl = up.lifestyle(st, content)
    if lvl is not None:
        out.append(f"🏠 Уровень жизни: {texts.esc(lvl.name)}" + (f" ({views.money(lvl.weekly)}/нед.)" if lvl.weekly else ""))
    team = up.staff(st, content)
    if team:
        out.append("👤 Персонал: " + ", ".join(texts.esc(m.name) for m in team))
    need = up.needed_grade(st, content)
    cur = up.grade(content, st.flags.get(up.FLAG_IMMUNO))
    if need is not None:
        ok = cur is not None and cur.strength >= need.strength and up.can_take(st, cur)
        out.append(f"🦾 Иммуноподавители: {texts.esc(cur.name) if cur else 'нет'}"
                   + ("" if ok else f" — нужны «{texts.esc(need.name)}», тело отторгает хром"))
    tier = up.wealth_tier(st, content)
    if tier is not None and tier.chance > 0:
        out.append(f"💸 Известность богатства: {texts.esc(tier.name)} — грабители, рэкет и конкуренты присматриваются "
                   "(охрана, бухгалтер и защита дома помогают)")
    hooked = up.addicted(st, content)
    if hooked:
        names = [up.drug(content, k).name for k in hooked if up.drug(content, k)]
        out.append("💊 Зависимость: " + ", ".join(texts.esc(n) for n in names))
    return out


def traits_screen(content, ch) -> Screen:  # noqa: ANN001
    """What the traits, marks and obsessions of the character are and what they do (owner request)."""
    from app.bot import effects_text

    st = ch.state
    lines = [style.header("profile", "Черты"), ""]
    for title, table, keys in (("Черта", content.traits, st.traits), ("Примета", content.marks, st.marks),
                               ("Одержимость", content.obsessions, st.obsessions)):
        for k in keys:
            e = table.get(k)
            if e is None:
                continue
            mods = getattr(e, "mods", None) or {}
            fx = effects_text.cap(", ".join(effects_text.mods_phrases(mods))) if mods else ""
            lines.append(f"<b>{title}: {texts.esc(e.name)}</b>\n{texts.esc(e.description)}" + (f"\n<i>{texts.esc(fx)}</i>" if fx else ""))
            lines.append("")
    if len(lines) == 2:
        lines.append("Ничего особенного. Пока.")
    return Screen("\n".join(lines).rstrip(), [[Button(texts.BACK, "traits_back"), Button(texts.MENU, "back")]], name="profile")


# the greeting uses its own rng so that menu code stays deterministic in checks
_greeting_rng = random.Random()


def greeting_text(ctx: Ctx, district_key: str, name: str, year: int) -> str:
    fixer = game.district_fixer(ctx.content, district_key, year)
    district = ctx.content.districts.get(district_key)
    return texts.fixer_greeting(
        _greeting_rng, fixer.name if fixer else None, name, district.name if district else district_key
    )
