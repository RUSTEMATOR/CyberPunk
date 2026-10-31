"""The shared home, the crew headquarters and the megacorp tower (owner request 23.10). property -> hubs."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen, pack
from app.db import session as db
from app.services import crews, game
from app.services import hubs as svc
from app.services.game import ActiveCharacter

BLOCKED = {
    "small": "В капсуле вдвоём не жить. Нужна комната или лучше.",
    "guest": "Ты живёшь в чужом доме. Звать может только хозяин.",
    "away": "Вы в разных городах. Жить вместе можно только в одном: сначала переезд («Мир»).",
    "full": "Места больше нет: комната на двоих, квартира на четверых, лофт на шестерых.",
    "gone": "Этого уже нет.",
    "already": "Уже позван или уже живёт с тобой.",
    "jail": "Из камеры не переехать.",
    "ap": "ОД кончились. Нажми «Прожить месяц».",
    "none": "Ты ни с кем не живёшь.",
    "max": "Укреплять дальше некуда.",
    "eddies": "Не хватает эдди.",
    "nocrew": "Ты не в банде.",
    "leader": "Это решает главарь банды.",
    "nohq": "У банды нет штаба.",
}


async def hubs_screen(ctx: Ctx, ch: ActiveCharacter, note: str | None = None) -> Screen:
    c = ctx.content
    cfg = svc.cfg(c)
    st = ch.state
    lines = [style.header("home", "Общий дом и штаб"), f"Эдди: {views.money(st.eddies)} · ОД: {st.ap}", ""]
    if note:
        lines += [note, ""]
    rows: list[list[Button]] = []
    async with db.session_scope() as s:
        hub = await svc.sync_self(s, c, ch)
        await game.save_active(s, ch, ctx.ui.settings.timezone)
        invites = await svc.invites_for(s, ch)
        crew = await crews.crew_of(s, ch.player_tg_id)
        shelters = await svc.shelters(s, c, ch)
        names = {cid: n for cid, n in await svc.candidates(s, ch)}
    home = c.housing.get(st.housing)
    lines.append("<b>Общий дом</b>")
    if hub is not None and len(hub["members"]) >= 2:
        who = ", ".join("ты" if int(m) == ch.id else texts.esc(names.get(int(m), "?")) for m in hub["members"])
        price = svc.fort_price(c, int(hub.get("fort", 0)))
        lines.append(f"Живёте вместе: {who}. Аренда делится на {len(hub['members'])}, настроение и общение лучше.")
        lines.append(f"Укрепления дома: уровень {hub.get('fort', 0)} (оборона +{int(hub.get('fort', 0)) * cfg.fort_def} в Судную ночь).")
        if price is not None:
            rows.append([Button(f"Укрепить дом · {views.money(price)}", "fort")])
        if int(hub["host"]) == ch.id:
            rows.append([Button("Позвать ещё", "inv"), Button("Выселить", "kickl")])
            rows.append([Button("Жить отдельно", "leave", style="danger")])
        else:
            rows.append([Button("Съехать", "leave", style="danger")])
    else:
        cap = svc.capacity(c, st.housing)
        if home is None or cap < 2:
            lines.append("В капсуле или без крыши вдвоём не жить. Комната — на двоих, квартира — на четверых, лофт — на шестерых.")
        else:
            lines.append(f"«{texts.esc(home.name)}» вмещает {cap}. Позови других игроков: аренда делится на всех, "
                         "дом можно укреплять вместе и прятаться в нём в Судную ночь.")
            rows.append([Button("Позвать жить вместе", "inv")])
    for h in invites:
        host = names.get(int(h["host"]), "?")
        hh = c.housing.get(h["housing"])
        lines.append(f"📨 {texts.esc(host)} зовёт жить вместе: «{texts.esc(hh.name if hh else '?')}». "
                     f"Переезд в район {texts.esc(views.district_name(c, h['district']))}, {cfg.move_in_ap} ОД.")
        rows.append([Button(f"Переехать к {host}"[:24], "yes", h["id"]), Button("Отказать", "no", h["id"])])
    lines += ["", "<b>Штаб банды</b>"]
    if crew is None:
        lines.append("Ты не в банде. Банда игроков — /crew.")
    else:
        hq = svc.hq_of(crew)
        t = svc.hq_tier(c, str(hq.get("tier"))) if hq else None
        leader = crew.leader_tg == ch.player_tg_id
        if t is None:
            lines.append(f"У банды «{texts.esc(crew.name)}» штаба нет. Главарь снимает его и платит аренду каждый месяц "
                         "(не хватит — из общака).")
        else:
            price = svc.fort_price(c, int(hq.get("fort", 0)))
            lines.append(f"«{texts.esc(t.name)}»: аренда {views.money(t.weekly)}/нед. (платит главарь), оборона "
                         f"{t.base_def + int(hq.get('fort', 0)) * cfg.fort_def}, бизнес банды +{t.biz_pct}%.")
            lines.append(f"<i>{texts.esc(t.description)}</i>")
            if price is not None:
                rows.append([Button(f"Укрепить штаб · {views.money(price)}", "hqfort")])
        if leader:
            rows.append([Button("Выбрать штаб", "hqlist")] + ([Button("Сдать штаб", "hqdrop", style="danger")] if t else []))
    tower = [x for x in shelters if x.id.startswith("corp:")]
    if tower:
        lines += ["", "<b>Башня корпорации</b>"]
        for x in tower:
            lines.append(f"{texts.esc(x.name)}: оборона {x.defense}. Прятаться можно владельцу, его банде и штату корпорации. "
                         "Охрана башни растёт в «Империи».")
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="hubs")


async def invite_screen(ctx: Ctx, ch: ActiveCharacter, kick: bool = False) -> Screen:
    c = ctx.content
    async with db.session_scope() as s:
        people = await svc.candidates(s, ch)
        hub = await svc.home_of(s, ch)
    if kick:
        ids = [int(m) for m in (hub or {}).get("members", []) if int(m) != ch.id]
        people = [(cid, n) for cid, n in people if cid in ids]
    lines = [style.header("home", "Выселить" if kick else "Позвать жить вместе"), ""]
    buttons = []
    for i, (cid, name) in enumerate(people, 1):
        lines.append(f"{i}. {texts.esc(name)}")
        buttons.append(Button(str(i), "k" if kick else "p", str(cid)))
    if not people:
        lines.append("Некого.")
    rows = pack(buttons, c.balance.ui.button_chars, max_per_row=5)
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="hubpick")


def hq_list_screen(ctx: Ctx) -> Screen:
    c = ctx.content
    lines = [style.header("home", "Штаб банды"), "Аренда — каждую неделю, платится раз в месяц с главаря (или из общака). "
             "Первый месяц сразу.", ""]
    rows: list[list[Button]] = []
    for kind, title in (("gang", "Для банды"), ("corp", "Для корпорации")):
        lines.append(f"<b>{title}</b>")
        for t in svc.cfg(c).hq:
            if t.kind != kind:
                continue
            lines.append(f"{texts.esc(t.name)}: {views.money(t.weekly)}/нед., оборона {t.base_def}, бизнес +{t.biz_pct}%. "
                         f"<i>{texts.esc(t.description)}</i>")
            rows.append([Button(f"{t.name}"[:24], "t", t.key)])
        lines.append("")
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="hubhq")


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


@on_screen("hubs")
async def hubs_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "property")
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if action == "inv":
        return await invite_screen(ctx, ch)
    if action == "kickl":
        return await invite_screen(ctx, ch, kick=True)
    if action == "hqlist":
        return hq_list_screen(ctx)
    tz, rng = ctx.ui.settings.timezone, random.Random()
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if action in ("yes", "no") and arg:
            r = await svc.answer(s, c, ch, arg, action == "yes", rng, year, tz)
        elif action == "leave":
            r = await svc.leave(s, c, ch, tz)
        elif action == "fort":
            r = await svc.fortify_home(s, c, ch, rng, year, tz)
        elif action == "hqfort":
            r = await svc.hq_fortify(s, c, ch, rng, year, tz)
        elif action == "hqdrop":
            r = await svc.hq_drop(s, ch)
        else:
            r = None
    if r is None:
        return await hubs_screen(ctx, ch)
    return await hubs_screen(ctx, ch, BLOCKED.get(r.blocked, r.blocked) if r.blocked else texts.esc(r.text))


@on_screen("hubpick")
async def hubpick_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if action in ("p", "k") and arg and arg.isdigit():
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            if action == "p":
                r = await svc.invite(s, c, ch, int(arg), ctx.ui.settings.timezone)
            else:
                r = await svc.kick(s, c, ch, int(arg))
        return await hubs_screen(ctx, ch, BLOCKED.get(r.blocked, r.blocked) if r.blocked else texts.esc(r.text))
    return await hubs_screen(ctx, ch)


@on_screen("hubhq")
async def hubhq_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action == "t" and arg:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            r = await svc.hq_rent(s, ctx.content, ch, arg, random.Random(), year, ctx.ui.settings.timezone)
        return await hubs_screen(ctx, ch, BLOCKED.get(r.blocked, r.blocked) if r.blocked else texts.esc(r.text))
    return await hubs_screen(ctx, ch)
