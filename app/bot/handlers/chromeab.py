"""«⚙️ Возможности хрома» (owner request 26.10, docs/PLAN_CHROME.md): what the installed chrome lets you do outside a
fight, and the build lines with their bonuses. «Профиль» or «Риппер» → «⚙️ Возможности хрома»."""

from __future__ import annotations

import random
import re

from app.bot import texts
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen, pack
from app.content.loader import Content
from app.db import session as db
from app.engine import abilities as eng
from app.services import abilities as svc
from app.services import game
from app.services.game import ActiveCharacter

WHY = {"cd": "Ещё не готово: хрому нужен отдых.", "ap": "Не хватает ОД.", "jail": "Из камеры не выйдет.",
       "none": "Сейчас не к чему это применить.", "full": "Ты цел: чинить нечего.", "gone": "Этой возможности у тебя нет."}


def ab_screen(content: Content, ch: ActiveCharacter, tz: str, note: str | None = None, back: str = "profile") -> Screen:
    st = ch.state
    have = eng.owned(st, content)
    rk = eng.ranks(st, content)
    lines = ["⚙️ <b>Возможности хрома</b>",
             "Что умеет твоё тело сверх обычного. У каждого импланта своя возможность, которой больше нет ни у кого. "
             "Кнопка запускает сцену; в событиях появляются варианты с иконкой твоей возможности. В бою — кнопка "
             "«⚙️ Хром»: способность срабатывает сразу и не тратит ход. Каждая ещё и помогает в проверках своего рода. "
             "Новая версия того же импланта поднимает ранг: сильнее в бою, больше выигрыш в сценах, с 4-го ранга "
             "короче отдых.", ""]
    btns: list[Button] = []
    if not have:
        lines.append("Пока ничего: поставь импланты у риппера, у каждого есть своя возможность.")
    # owner request 26.10: one implant, one ability of its own; with many of them the list is one short line each
    full = len(have) <= 8
    for key, src in sorted(have.items(), key=lambda kv: content.balance.chrome_abilities[kv[0]].name):
        ab = content.balance.chrome_abilities[key]
        ok = svc.ready(ch, key, tz)
        free = svc.free_on(ch, key)
        r = rk.get(key, 1)
        line = f"{ab.icon} <b>{texts.esc(ab.name)}</b>" + (f" · ранг {r}" if r > 1 else "")
        if full:
            line += f" — {texts.esc(ab.text)}"
        if any(re.search(r"\(\d+\)$", x) for x in src):  # a set ability («Сборка (6)»); an implant one is its own
            line += f" <i>({texts.esc(', '.join(dict.fromkeys(src)))})</i>"
        lines.append(line + ("" if ok else f" <i>· снова с {free[8:10]}.{free[5:7]}</i>"))
        if full:  # owner request 27.10: what it does in a fight and in checks
            from app.engine import ab_power

            fam = eng.family(content, key)
            extra = [x for x in (("в бою: " + ab_power.fight_line(content, fam, r)) if ab_power.fight_line(content, fam, r) else "",
                                 ab_power.checks_line(content, fam, r)) if x]
            if extra:
                lines.append("   <i>" + texts.esc("; ".join(extra)) + "</i>")
        if ok and ab.events:
            btns.append(Button(f"{ab.icon} {ab.button}"[:24], "use", key))
    if not full:
        lines.append("<i>Что делает каждая, написано в карточке импланта у риппера.</i>")
    counts = eng.set_counts(st, content)
    if counts:
        lines += ["", "🧩 <b>Сборки</b>"]
        for key, n in counts.most_common():
            cs = content.balance.chrome_sets.get(key)
            if cs is None:
                continue
            got = [f"{need}: {b.text}" for need, b in sorted(cs.bonuses.items()) if n >= int(need)]
            nxt = next(((need, b) for need, b in sorted(cs.bonuses.items()) if n < int(need)), None)
            lines.append(f"{cs.icon} <b>{texts.esc(cs.name)}</b> · {n} дет." + (f" · действует: {texts.esc('; '.join(got))}" if got else ""))
            if nxt:
                lines.append(f"   <i>с {nxt[0]} деталей: {texts.esc(nxt[1].text)}</i>")
    if note:
        lines += ["", note]
    rows = pack(btns, content.balance.ui.button_chars, max_per_row=2)
    rows.append([Button(texts.BACK, "back", back)])
    return Screen("\n".join(lines), rows, name="chromeab", state={"back": back})


@on_screen("chromeab")
async def chromeab_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    tz = ctx.ui.settings.timezone
    back = str(ctx.state.get("back") or arg or "profile") if action != "open" else (arg or "profile")
    if action == "back":
        return await goto(ctx, "ripper" if back == "ripper" else "profile", "open")
    play = False
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            from app.bot.handlers.menu import menu_screen

            return await menu_screen(ctx)
        if action == "use" and arg:
            why = await svc.use(s, ctx.content, ch, random.Random(), tz, arg)
            if why:
                return ab_screen(ctx.content, ch, tz, WHY.get(why, why), back)
            play = True
    if play:
        return await goto(ctx, "evchoice", "resume")
    return ab_screen(ctx.content, ch, tz, None, back)
