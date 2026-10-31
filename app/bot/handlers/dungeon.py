"""The tower dungeon window «tdun» (owner request 26.10, docs/PLAN_DUNGEON.md): «Налёт на корпорацию» → корпорация →
«Башня: штурм». Outside a run: open runs of the corp to join and the start; inside: the floors, the alarm, the party,
the rooms of the floor or the boss, the retreat."""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.db import session as db
from app.engine import derived
from app.services import dungeon as svc
from app.services import game
from app.services.game import ActiveCharacter

WHY = {"member": "Штурм башни — для своих в корпорации или тех, кого город уже не меряет.",
       "gone": "Этого штурма уже нет.", "down": "Эта корпорация уже лежит.", "tower": "Башня откроется после первого "
       "пройденного вызова.", "off": "Башня закрыта.", "jail": "Из камеры не выйти.",
       "busy": "Сначала разберись с тем, что уже ждёт.", "inside": "Ты уже в башне.",
       "today": "Сегодня штурм у тебя уже был. Башня ждёт до завтра.", "ap": "Не хватает ОД.", "full": "Отряд уже полный."}


def _floors_line(content, run: dict) -> str:  # noqa: ANN001
    cur = int(run.get("floor", 0))
    out = []
    for i, f in enumerate(svc.cfg(content).floors):
        mark = "✅" if i < cur else ("▶" if i == cur and run.get("status") == svc.OPEN else "▫️")
        out.append(f"{mark} {f.name}" + (" 💀" if f.boss else ""))
    return "\n".join(out)


def _party(run: dict) -> str:
    return ", ".join(texts.esc(str(m.get("name", ""))) + (" (выбит)" if m.get("out") else "")
                     for m in (run.get("members") or {}).values())


async def corp_screen(ctx: Ctx, ch: ActiveCharacter, corp: str, note: str | None = None) -> Screen:
    content = ctx.content
    c = svc.cfg(content)
    name = content.corps[corp].name if corp in content.corps else corp
    lines = [style.header("war", f"Башня {texts.esc(name)}: штурм"),
             f"Пять этажей: вход, офисы, пост начальника охраны, исполнительный этаж и пентхаус с главой СБ. На этажах "
             f"выбираешь путь: бой, тихий обход, взлом, медпункт. Шум поднимает тревогу: охрана крепче, бои длиннее. "
             f"Здоровье между комнатами не восстанавливается.",
             f"Вход {c.ap_enter} ОД, комната {c.ap_room} ОД, бой с боссом {c.ap_boss} ОД. Один штурм в сутки. Можно соло, "
             f"можно отрядом до {c.party_max}: этаж и тревога общие, боссы с общим запасом здоровья. Проигранный бой не "
             f"убивает: охрана выбрасывает на улицу, из добычи остаётся {c.wipe_keep_pct}%."]
    rows: list[list[Button]] = []
    async with db.session_scope() as s:
        runs = await svc.open_runs(s, content, corp)
    if runs:
        lines += ["", "<b>Открытые штурмы</b>"]
        for r in runs:
            f = svc.floor_of(content, r)
            lines.append(f"• Отряд: {_party(r)} · этаж {int(r.get('floor', 0)) + 1}"
                         + (f" ({f.name})" if f else "") + f" · тревога {r.get('alarm', 0)}")
            rows.append([Button(f"Присоединиться #{r['id']}"[:24], "join", str(r["id"]), style="success")])
    rows.append([Button(f"Начать штурм · {c.ap_enter} ОД", "start", corp, style="danger")])
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="tdun", state={"corp": corp})


async def dungeon_screen(ctx: Ctx, ch: ActiveCharacter, note: str | None = None) -> Screen:
    content = ctx.content
    c = svc.cfg(content)
    async with db.session_scope() as s:
        run = await svc.current(s, content, ch)
    if run is None:
        return await menu_screen(ctx) if note is None else Screen(note, [[Button(texts.MENU, "menu")]], name="tdun")
    corp = svc.corp_name(content, run)
    me = (run.get("members") or {}).get(str(ch.player_tg_id), {})
    hp_max = derived.max_hp(ch.state, content)
    lines = [style.header("war", f"Штурм башни {texts.esc(corp)}"), _floors_line(content, run), "",
             f"🚨 Тревога {style.bar(int(run.get('alarm', 0)), c.alarm_max)} {run.get('alarm', 0)}/{c.alarm_max}"
             + (f" · босс ослаблен на {run.get('weaken')}%" if run.get("weaken") else ""),
             f"👥 Отряд: {_party(run)}",
             f"❤️ Здоровье {ch.state.hp}/{hp_max} · 💰 карман штурма {views.money(int(me.get('pocket', 0)))}"]
    rows: list[list[Button]] = []
    floor = svc.floor_of(content, run)
    if run.get("status") != svc.OPEN or me.get("out"):
        lines += ["", "Штурм окончен." if run.get("status") == svc.DONE else "Штурм для тебя окончен."]
        rows.append([Button("Выйти с добычей", "leave", style="success")])
    elif floor is not None and floor.boss:
        fi = int(run["floor"])
        left = svc.boss_left(run, fi)
        lines += ["", f"<b>{texts.esc(floor.name)}</b>. {texts.esc(floor.text.replace('{corp}', corp))}",
                  f"💀 {texts.esc(svc.boss_name(content, run, floor))}: {style.bar(round(left * 100), 100)} {round(left * 100)}%"
                  + (" · охрана, потом он сам" if floor.waves > 1 else "")]
        rows.append([Button(f"В бой с боссом · {c.ap_boss} ОД", "boss", style="danger")])
    elif floor is not None:
        lines += ["", f"<b>{texts.esc(floor.name)}</b>. {texts.esc(floor.text.replace('{corp}', corp))}", ""]
        for r in floor.rooms:
            tag = {"fight": "бой", "check": "проверка", "heal": "лечение", "loot": "добыча"}[r.kind]
            lines.append(f"• <b>{texts.esc(r.name)}</b> ({tag}): {texts.esc(r.text)}")
            rows.append([Button(f"{r.name} · {c.ap_room} ОД"[:24], "room", r.key,
                                style="danger" if r.kind == "fight" else None)])
    if run.get("log"):
        lines += ["", "<i>" + texts.esc(" · ".join(run["log"][-3:])) + "</i>"]
    if note:
        lines += ["", note]
    if run.get("status") == svc.OPEN and not me.get("out"):
        rows.append([Button("Отступить с добычей", "leave")])
    rows.append([Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="tdun")


@on_screen("tdun")
async def tdun_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "back":
        return await goto(ctx, "craid", "open")
    content = ctx.content
    tz = ctx.ui.settings.timezone
    rng = random.Random()
    step = None
    lines: list[str] = []
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, content)
        corp = str(arg or ctx.state.get("corp") or "")
        if action == "start" and corp:
            r = await svc.start(s, content, ch, year, tz, corp, rng)
            if isinstance(r, str):
                return await corp_screen(ctx, ch, corp, WHY.get(r, r))
            lines = ["Ты в вестибюле. Охрана ещё не знает, что сегодня плохой день."]
        elif action == "join" and arg and arg.isdigit():
            r = await svc.join(s, content, ch, year, tz, int(arg))
            if isinstance(r, str):
                return await corp_screen(ctx, ch, corp, WHY.get(r, r)) if corp else await menu_screen(ctx)
            lines = ["Ты догоняешь отряд."]
        elif action == "room" and arg:
            lines, step = await svc.act(s, content, ch, rng, year, tz, arg)
        elif action == "boss":
            lines, step = await svc.fight_boss(s, content, ch, rng, year, tz)
        elif action == "leave":
            lines = await svc.leave(s, content, ch, rng, year, tz)
            return Screen("\n".join(lines) or "Ты выходишь из башни.", [[Button(texts.MENU, "menu")]], name="tdun")
        elif action == "open" and corp and not ch.state.flags.get(svc.FLAG):
            return await corp_screen(ctx, ch, corp)
    if step is not None:
        from app.bot.handlers.combat import step_screen

        scr = step_screen(content, ch, step)
        scr.text = "\n".join(lines) + "\n\n" + scr.text
        return scr
    return await dungeon_screen(ctx, ch, "\n".join(lines) if lines else None)
