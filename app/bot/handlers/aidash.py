"""«🧬 Мой ИИ» (owner request 27.10, docs/PLAN_AIDASH.md): what every AI in the character gives, what it takes, and
the moves with Capri. «Профиль» → «🧬 Мой ИИ»."""

from __future__ import annotations

import random
import time

from app.bot import texts, views
from app.bot.effects_text import mods_line
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import blackwall as bw_eng
from app.engine import capri as capri_eng
from app.engine import possession as pos_eng
from app.services import capri as capri_svc
from app.services import game
from app.services.game import ActiveCharacter

WHY = {"none": "Капри в тебе нет.", "reject": "Тело отторгает её: сейчас не до разговоров, к рипперу.",
       "today": "Сегодня вы уже решили, кто ведёт. Завтра снова.", "ap": "Не хватает ОД.",
       "last": "Ещё один её день — и тебя не останется. Она это знает. Ты тоже."}


def has_ai(ch: ActiveCharacter) -> bool:
    f = ch.state.flags
    return bool(capri_eng.host(ch.state) or pos_eng.rec(ch.state) or f.get(bw_eng.FLAG_AVATAR))


FX_RU = {"eddies": "эдди", "street_cred": "уличная репутация", "humanity": "человечность", "mood": "настроение",
         "fame": "слава", "heat": "розыск", "hp": "здоровье"}


def _fx_words(fx: dict[str, int]) -> str:
    return ", ".join(f"{FX_RU.get(k, k)} {style.signed(int(v))}" for k, v in fx.items() if v)


def _hours(h: float) -> str:
    h = max(0.0, h)
    if h >= 48:
        return f"~{h / 24:.1f} сут"
    return f"~{int(h)} ч" if h >= 1 else "меньше часа"


def _capri(content: Content, ch: ActiveCharacter, rec: dict, tz: str) -> tuple[list[str], list[list[Button]]]:
    from app.clock import kyiv_date

    c = capri_svc.cfg(content)
    h = (rec.get("hosts") or {}).get(str(ch.player_tg_id)) or {}
    full = h.get("part", "full") == "full"
    sync = float(h.get("sync", 0) or 0)
    rate = c.sync_day_full if full else c.sync_day_half
    lines = [f"🧩 <b>Капри</b> · {'целая' if full else 'половина'}",
             f"Синхронизация {style.bar(int(sync), 100)} {int(sync)}%"]
    rows: list[list[Button]] = []
    reject = float(h.get("reject", 0) or 0)
    if reject:
        left = (reject - time.time()) / 3600
        lines += [f"⚠️ <b>Тело её отторгает.</b> До конца осталось {_hours(left)}. Извлечь у риппера: "
                  f"{views.money(c.extract_price)}. Не успеешь — смерть.",
                  "Пока идёт отторжение, она ничего не даёт."]
        rows.append([Button(f"Извлечь · {views.money(c.extract_price)}"[:24], "cextract", style="danger")])
        return lines, rows
    lines += ["", "<b>Даёт:</b> " + (mods_line(capri_eng.mods(ch.state, content)) or "ничего")
              + (". При вселении зарастила все увечья." if full else ". Половина силы целой Капри."),
              "", "<b>Берёт:</b>",
              f"• синхронизацию: +{rate:g}% в сутки, до 100% {_hours((100 - sync) / rate * 24)}. На 100% тело её: "
              f"тебя не станет, по городу будет ходить Капри с твоим лицом;"]
    if sync < c.takeover_from:
        lines.append(f"• с {c.takeover_from}% (через {_hours((c.takeover_from - sync) / rate * 24)}) раз в сутки с шансом "
                     f"{int(c.takeover_chance * 100)}% просит отдать ей день (событие «Капри хочет день»);")
    else:
        lines.append(f"• раз в сутки с шансом {int(c.takeover_chance * 100)}% просит отдать ей день "
                     f"(событие «Капри хочет день»): уступишь — синхронизация +{c.yield_sync}%;")
    lines.append(f"• охотники корпораций: раз в {c.hunter_cooldown_h:g} ч с шансом {int(c.hunter_chance * 100)}% "
                 f"нападают в Найт-Сити; проиграешь — вырежут чип прямо на улице.")
    lines += ["", "<b>Что можно с ней:</b>",
              "• поговорить: она помнит разговоры и отвечает за себя;",
              f"• сдержать: ХЛАД + d10 против {c.resist_dv}, {c.act_ap} ОД. Успех −{c.resist_sync}% синхронизации, "
              f"провал +{c.resist_fail_sync}% и боль;",
              f"• отдать ей день: +{c.yield_sync}% синхронизации, взамен "
              f"{_fx_words(c.yield_fx) or 'её подарок'} и полное здоровье."
              " Сдержать или отдать — одно в сутки."]
    exits = [f"извлечь у риппера ({views.money(c.extract_price)})"]
    if not full:
        exits.append("отдать свою половину второму носителю")
    exits.append(f"последний полёт к спутнику (целая, с {c.flight_from}%)" if full else "")
    lines.append("<b>Выходы:</b> " + "; ".join(x for x in exits if x) + ". В окне «🧩 Каприкорн».")
    rows.append([Button("💬 Поговорить с Капри", "ctalk")])
    if ch.state.flags.get(capri_svc.FLAG_ACT_DAY) != kyiv_date(tz):
        rows.append([Button(f"✋ Сдержать · {c.act_ap} ОД", "cresist"), Button("🤝 Отдать ей день", "cyield")])
    else:
        lines.append("<i>Сегодня вы уже решили, кто ведёт.</i>")
    rows.append([Button("🧩 Каприкорн", "capri")])
    return lines, rows


def _possessed(content: Content, ch: ActiveCharacter) -> tuple[list[str], list[list[Button]]]:
    r = pos_eng.rec(ch.state) or {}
    c = pos_eng.cfg(content)
    t = pos_eng.tier(content, r)
    sync = int(r.get("sync", 0))
    lines = [f"🧠 <b>{texts.esc(str(r.get('name')))}</b> · в голове",
             f"Синхронизация {style.bar(sync, 100)} {sync}%" + (f" · «{t.name}»" if t else "")
             + f" · доверие {int(r.get('trust', 50))}",
             "<b>Даёт:</b> " + ((mods_line(t.mods) if t and t.mods else "") or "ничего")
             + (" (блокаторы: молчит до конца месяца)" if r.get("quiet") else ""),
             f"<b>Берёт:</b> синхронизация +{c.sync_month} за прожитый месяц (быстрее, если доверяет), просьбы-события; "
             f"на {c.final_at}% решать, кто вы друг другу: стать одним, отдать тело или отпустить."]
    return lines, [[Button(f"🧠 {str(r.get('name'))}: подробнее"[:24], "possess")]]


def _avatar(content: Content, ch: ActiveCharacter) -> list[str]:
    c = bw_eng.cfg(content)
    return ["⚫ <b>Аватар Чёрного заслона</b>",
            "<b>Даёт:</b> " + (mods_line(c.avatar_mods) or "ничего"),
            f"<b>Берёт:</b> человечность {style.signed(c.avatar_weekly_humanity)} каждый месяц. Дорога за Заслон открыта."]


async def dash_screen(ctx: Ctx, note: str | None = None) -> Screen:
    content = ctx.content
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            from app.bot.handlers.menu import menu_screen

            return await menu_screen(ctx)
        rec = await capri_svc.sync(s, ch)
        capri_svc.ensure_npc(ch)
        await game.save_active(s, ch, tz)
    lines = [style.header("🧬", "Мой ИИ"), "Кто живёт в тебе, что даёт и что забирает."]
    rows: list[list[Button]] = []
    if capri_eng.host(ch.state):
        part, btn = _capri(content, ch, rec, tz)
        lines += ["", *part]
        rows += btn
    if pos_eng.rec(ch.state):
        part, btn = _possessed(content, ch)
        lines += ["", *part]
        rows += btn
    if ch.state.flags.get(bw_eng.FLAG_AVATAR):
        lines += ["", *_avatar(content, ch)]
    if len(lines) == 2:
        lines += ["", "Сейчас в тебе никого. Только ты."]
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="aidash")


@on_screen("aidash")
async def aidash_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        from app.bot.handlers.menu import menu_screen

        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "profile", "open")
    if action == "capri":
        return await goto(ctx, "capri", "open")
    if action == "possess":
        return await goto(ctx, "possess", "open")
    if action == "cextract":
        return await goto(ctx, "capri", "extract")
    tz = ctx.ui.settings.timezone
    if action == "ctalk":
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await dash_screen(ctx)
            await capri_svc.sync(s, ch)
            i = capri_svc.ensure_npc(ch)
            await game.save_active(s, ch, tz)
        if i is None:
            return await dash_screen(ctx, WHY["none"])
        from app.bot.handlers.npcchat import open_chat

        return await open_chat(ctx, i)
    if action in ("cresist", "cyield"):
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await dash_screen(ctx)
            year = await game.current_year(s, ctx.content)
            fn = capri_svc.resist_now if action == "cresist" else capri_svc.give_day
            why, lines, extra = await fn(s, ctx.content, ch, random.Random(), year, tz)
            await game.save_active(s, ch, tz)
        if why:
            return await dash_screen(ctx, WHY.get(why, why))
        parts = []
        if action == "cresist" and extra is not None:
            parts.append(views.check_line(extra))
        parts += lines
        if action == "cyield" and extra is not None:
            sm = views.effects_summary(extra, ctx.content)
            if sm:
                parts.append(sm)
        return await dash_screen(ctx, "\n".join(parts))
    return await dash_screen(ctx)
