"""The window «capri» (owner request 26.10, docs/PLAN_CAPRI.md): project «Каприкорн» — where the chip is, the hunt, what
to do with it in hand, life as her host (the sync, the extraction, the split, the last flight)."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.db import session as db
from app.services import capri as svc
from app.services import game


async def capri_screen(ctx: Ctx, note: str | None = None) -> Screen:
    content = ctx.content
    c = svc.cfg(content)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        rec = await svc.sync(s, ch)
        await game.save_active(s, ch, ctx.ui.settings.timezone)
    tg = str(ctx.user_id)
    st = ch.state
    status = rec.get("status")
    lines = [style.header("event", "Проект «Каприкорн»"),
             "Секретный проект Арасаки. Капри — конструкт, сшитый из десятка оцифрованных личностей. Она спит на "
             "датачипе и просыпается только в человеке: даёт силу в обмен на тело и с каждым днём забирает его "
             "больше. Первые носители умирали за двое суток. За ней охотятся все корпорации города.", ""]
    rows: list[list[Button]] = []
    host = rec.get("hosts", {}).get(tg)
    if status == "dormant":
        lines.append("Сейчас чип в корпоративном сейфе. Он ещё всплывёт.")
    elif status == "loose":
        dn = content.districts[rec["district"]].name if rec.get("district") in content.districts else "?"
        pts = int((rec.get("points") or {}).get(tg, 0))
        lines += [f"Чип в городе, след ведёт в район {texts.esc(dn)}.",
                  f"Охота: бой с охотниками корпораций, {c.hunt_ap} ОД, раз в {c.hunt_cooldown_h:g} ч. Победа — шаг по "
                  f"следу, поражение — шаг назад. Кто первым сделает {c.hunt_need}, забирает чип.",
                  f"Твой след: {pts}/{c.hunt_need}."]
        rows.append([Button(f"Охота · {c.hunt_ap} ОД", "hunt", style="danger")])
    elif status == "chip" and str(rec.get("holder")) == tg:
        lines += ["<b>Чип у тебя.</b> Он тёплый.",
                  f"• Вставить: стать носителем. Проверка переносимости: ТЕЛО + ХЛАД + человечность/10 + d10 против "
                  f"{c.host_dv}. Провал — {c.reject_h:g} часов, чтобы извлечь, иначе смерть.",
                  f"• Продать корпорации: {views.money(c.sell_price)}.",
                  "• Уничтожить ЭМИ: человечность, Капри исчезнет надолго."]
        rows.append([Button("Вставить", "install", style="danger")])
        rows.append([Button(f"Продать · {views.money(c.sell_price)}"[:24], "sell"), Button("Уничтожить", "destroy")])
    elif status == "chip":
        lines.append("Чип у одного из игроков. Охотники корпораций уже знают у кого.")
    elif status == "host" and host is not None:
        sync = int(float(host.get("sync", 0)))
        part = "целая" if host.get("part") == "full" else "половина"
        lines += [f"<b>Капри в тебе ({part}).</b>",
                  f"Синхронизация {style.bar(sync, 100)} {sync}%. На 100% тебя больше не будет."]
        if float(host.get("reject", 0) or 0):
            lines.append(f"⚠️ Тело отторгает конструкт. Извлеки чип, пока не поздно ({views.money(c.extract_price)}).")
        else:
            lines.append("Бой, урон и здоровье выше" + (" вдвое меньше" if host.get("part") != "full" else "")
                         + f". С {c.takeover_from}% она иногда берёт тело на день. Охотники корпораций приходят сами.")
        rows.append([Button(f"Извлечь · {views.money(c.extract_price)}"[:24], "extract")])
        if len(rec.get("hosts", {})) == 2:
            lines.append("Вторая половина в другом человеке. Отдай ей свою — и Капри соберётся там.")
            rows.append([Button("Отдать свою половину", "give")])
        if host.get("part") == "full" and sync >= c.flight_from:
            lines.append("<i>Она смотрит на небо. Там спутник. «Полетели?»</i>")
            rows.append([Button("Последний полёт", "flight", style="danger")])
    elif status == "host":
        n = len(rec.get("hosts", {}))
        lines.append("Капри проснулась в человеке." if n == 1 else "Капри расколота надвое, в двух телах.")
        if n == 1:
            lines.append(f"Можно украсть половину: ИНТ + нетраннинг против {c.split_dv}, {c.split_ap} ОД, раз в сутки.")
            rows.append([Button(f"Украсть половину · {c.split_ap} ОД", "steal", style="danger")])
    if st.flags.get("raven_her_known"):
        lines += ["", "<i>Raven: «Люси её чует. Говорит, корпоративная подделка: десяток мёртвых, сшитых в одну. "
                  "Но голодная, сука, как настоящая»."]
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="capri")


@on_screen("capri")
async def capri_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "city", "open")
    note = None
    tz = ctx.ui.settings.timezone
    rng = random.Random()
    if action in ("hunt", "install", "sell", "destroy", "extract", "steal", "give", "flight"):
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, ctx.content)
            ui = ctx.ui
            if action == "hunt":
                r = await svc.hunt(s, ctx.content, ch, rng, year, tz)
                if not isinstance(r, str):
                    from app.bot.handlers.combat import step_screen

                    return step_screen(ctx.content, ch, r)
                note = r
            elif action == "install":
                note = await svc.install(s, ctx.content, ch, rng, year, tz, ui)
            elif action == "sell":
                note = await svc.sell(s, ctx.content, ch, rng, year, ui)
            elif action == "destroy":
                note = await svc.destroy(s, ctx.content, ch, rng, year, ui)
            elif action == "extract":
                note = await svc.extract(s, ctx.content, ch, rng, year, ui)
            elif action == "steal":
                note = await svc.steal_half(s, ctx.content, ch, rng, year, tz, ui)
            elif action == "give":
                note = await svc.give_half(s, ctx.content, ch, ui)
            elif action == "flight":
                note = await svc.flight(s, ctx.content, ch, tz, ui)
                if note is None:
                    from app.bot.handlers.week import death_screen

                    return death_screen(ch.state.name, "end_orbit", [
                        "В голове десяток голосов говорит одно слово: «наконец»."])
            await game.save_active(s, ch, tz)
    return await capri_screen(ctx, note)
