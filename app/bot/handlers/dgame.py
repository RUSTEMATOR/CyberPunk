"""The deadly games of the rich in the window (owner request 24.10). While the flag `dgame` is set, the menu opens this
screen instead of the city: no AP, no week, only the next round. The look changes like the cyberpsycho window does."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui.callbacks import Ctx, on_screen, on_text
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import derived
from app.services import dgame as svc
from app.services import game
from app.services.game import ActiveCharacter

BAR = "⭕🔺⬜⭕🔺⬜⭕🔺⬜⭕🔺⬜"


def _frame(lines: list[str]) -> str:
    return "\n".join([BAR, *lines, BAR])


def dgame_screen(content: Content, ch: ActiveCharacter, note: str | None = None, others: list[dict] | None = None) -> Screen:
    run = svc.run_of(ch)
    if run is None:
        return Screen(_frame(["Игра окончена.", note or ""]), [[Button("Дальше", "menu")]], name="dgame")
    st = ch.state
    rk = svc.round_key(run)
    head = [f"🎭 <b>ИГРОК №{run['number']:03d}</b>",
            f"Живых: <b>{run['alive']}</b> из {svc.cfg(content).players} · копилка над залом: "
            f"<b>{svc.pool(content, run)}</b> эдди",
            f"❤️ {st.hp}/{derived.max_hp(st, content)} · глюков {st.glitches}"]
    if others:
        head.append("Ещё здесь: " + ", ".join(f"№{o['number']:03d} {texts.esc(o['name'])}" for o in others[:5]))
    if run.get("host"):  # owner request 24.10: the game of a player
        title = run.get("host_title") or ""
        head.append(f"<i>Игра{' «' + texts.esc(title) + '»' if title else ''}. Хозяин в маске сидит за стеклом. "
                    "Говорят, он из своих.</i>")
    lines = head + ["", f"<b>{svc.TITLES[rk]}</b>", texts.esc(svc._intro(run, rk))]
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    p = run.get("pending")
    if p:
        lines += ["", "<b>Не вышло.</b> Решай быстро."]
        if st.glitches >= 1 and not p.get("rerolled"):
            rows.append([Button("Переброс за глюк", "glitch", style="primary")])
        rows.append([Button("Принять", "take", style="danger")])
    else:
        for i, (label, *_rest) in enumerate(svc.OPTIONS[rk]):
            extra = _rest[-1]
            rows.append([Button(label, "c", str(i), style="danger" if extra.get("die") or extra.get("fight") else None)])
        if int(run["round"]) > 0 and rk != "final" and run.get("voted_round") != run["round"]:
            rows.append([Button("Голосовать за выход", "vote")])
    lines += ["", "<i>Охрана в масках молчит. Камеры смотрят. Где-то наверху смеются гости.</i>"]
    return Screen(_frame(lines), rows, name="dgame")


@on_screen("dgame")
async def dgame_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.bot.handlers.combat import step_screen
    from app.bot.handlers.week import death_screen

    c = ctx.content
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if svc.run_of(ch) is None or action == "menu":
            if svc.run_of(ch) is None:
                return await menu_screen(ctx)
        year = await game.current_year(s, c)
        rng = random.Random()
        name = f"{ch.state.name} {ch.state.surname}".strip()
        if action == "c" and arg and arg.isdigit():
            r = await svc.choose(s, c, ch, rng, year, tz, int(arg))
        elif action == "take":
            r = await svc.take_fail(s, c, ch, rng, year, tz)
        elif action == "glitch":
            r = await svc.glitch(s, c, ch, rng, year, tz)
        elif action == "vote":
            r = await svc.vote(s, c, ch, rng, tz)
        else:
            return dgame_screen(c, ch, others=await svc.others(s, ch))
        others = await svc.others(s, ch)
    if r.step is not None:
        scr = step_screen(c, ch, r.step)
        if r.lines:
            scr = Screen("\n".join(texts.esc(x) for x in r.lines) + "\n\n" + scr.text, scr.keyboard, name=scr.name,
                         state=scr.state, awaiting_text=scr.awaiting_text)
        return scr
    if r.died:
        return death_screen(name, "dgame", [texts.esc(x) for x in r.lines])
    note = "\n".join(texts.esc(x) for x in r.lines)
    if r.check is not None:
        note = views.check_line(r.check) + ("\n" + note if note else "")
    if r.won or r.quit:
        return Screen(_frame([note, "", "Остров позади. Город прежний. Ты — нет."]), [[Button("Домой", "menu")]],
                      name="dgame")
    if r.blocked == "glitch":
        note = "Глюков нет или переброс уже был."
    return dgame_screen(c, ch, note or None, others)



# -- running your own game (owner request 24.10) ----------------------------------------------------------------------

HOST_BLOCKED = {"closed": "После штурма острова игры закрыты. Подожди.", "busy": "Остров уже занят чужой игрой.",
                "poor": "Свою игру может устроить только тот, у кого есть {min} эдди сверх цены, или владелец корпорации.",
                "eddies": "Не хватает эдди на остров.", "mine": "Твоя игра уже идёт.", "nohost": "У тебя нет игры.",
                "gone": "Этого игрока нет.", "inside": "Этот игрок уже на острове."}


async def host_screen(ctx: Ctx, note: str | None = None) -> Screen:
    from app.services import reinforce

    c = ctx.content
    cfg = svc.cfg(c)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        h = await svc.host_active(s)
        why = await svc.can_host(s, c, ch)
        people = await reinforce.helpers(s, ch) if h and str(h.get("tg")) == str(ctx.user_id) else []
        inside = await svc.others(s, ch)
    lines = ["🎭 <b>Свои игры богачей</b>", f"Эдди: {views.money(ch.state.eddies)}", "",
             f"Остров, охрана в масках, куклы и камеры: {views.money(cfg.host_price)} эдди, игра идёт {cfg.host_days} дн. "
             f"Розыск +{cfg.host_heat}. Устроить может владелец корпорации или тот, у кого больше "
             f"{views.money(cfg.host_min_eddies)} сверх цены.",
             f"Пока игра твоя, все, кто попадает на остров, играют в ней. Выбыл игрок — тебе {cfg.host_dead_pct}% копилки. "
             f"Выиграл — {cfg.host_win_pct}% со ставок гостей. Можно рассылать визитки игрокам: аванс "
             f"{views.money(cfg.invite_advance)} им платит остров, идти или нет — решают они.",
             "Если остров возьмут штурмом, имя хозяина узнает весь город."]
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    if h and str(h.get("tg")) == str(ctx.user_id):
        left_h = max(0, int((float(h.get("until", 0)) - __import__("time").time()) // 3600))
        lines += ["", f"<b>Твоя игра идёт</b>{' «' + texts.esc(str(h.get('title'))) + '»' if h.get('title') else ''}: "
                      f"ещё {left_h} ч. Заработано: {views.money(int(h.get('earned', 0) or 0))} эдди."]
        if inside:
            lines.append("На острове: " + ", ".join(f"№{o['number']:03d} {texts.esc(o['name'])}" for o in inside[:8]))
        rows += [[Button(f"Визитка: {name.split()[0]}"[:24], "inv", str(tg))] for tg, name in people[:8]]
    elif h:
        lines += ["", "Остров сейчас занят чужой игрой."]
    elif why is None:
        rows.append([Button(f"Устроить игру · {views.money(cfg.host_price)}", "host")])
    else:
        lines += ["", HOST_BLOCKED.get(why, why).format(min=views.money(cfg.host_min_eddies))]
    rows.append([Button(texts.BACK, "crime"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="dghost")


@on_screen("dghost")
async def dghost_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.bot.ui.callbacks import goto

    c = ctx.content
    if action == "menu":
        return await menu_screen(ctx)
    if action == "crime":
        return await goto(ctx, "crime")
    if action == "host":
        return Screen("🎭 <b>Название игры</b>\nНапиши в чат, как назвать игру (до 40 символов), или нажми «Без названия».",
                      [[Button("Без названия", "go")], [Button(texts.BACK, "open")]], name="dghost",
                      awaiting_text="dg_title")
    if action == "go":
        return await _do_host(ctx, "")
    if action == "inv" and arg and arg.lstrip("-").isdigit():
        from app.bot.handlers.players import _ping_target

        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            why = await svc.invite(s, c, ch, year, int(arg))
        if why:
            return await host_screen(ctx, HOST_BLOCKED.get(why, why))
        await _ping_target(ctx, int(arg), "offer")
        return await host_screen(ctx, "Человек в костюме уже едет к нему с визиткой.")
    return await host_screen(ctx)


async def _do_host(ctx: Ctx, title: str) -> Screen:
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        why = await svc.host(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, title.strip())
    if why:
        return await host_screen(ctx, HOST_BLOCKED.get(why, why).format(min=views.money(svc.cfg(c).host_min_eddies)))
    return await host_screen(ctx, "Остров готов. Визитки печатаются, куклы смазаны.")


@on_text("dg_title")
async def dg_title_text(ctx: Ctx, text: str) -> Screen | None:
    return await _do_host(ctx, text.strip()[:40])
