"""Small games started by a word in the chat (owner request).

«слот», «спин», «крутка» ... spin the 🎰 for a small stake right in the chat (the result is a temporary message);
«рыбалка» opens the fishing screen in the player's window. A newcomer without a character gets one of a random preset.
"""

from __future__ import annotations

import random

from aiogram.types import Message

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, goto, has_screen, make_ctx, on_screen
from app.bot.ui.kit import UiKit
from app.bot.ui.screen import Button, Screen
from app.content.loader import Content
from app.db import session as db
from app.engine import chatgames as eng
from app.services import chatgames as svc
from app.services import game
from app.services.game import ActiveCharacter

BLOCKED = {
    svc.R_DEAD: "Твой персонаж погиб: сначала выбери, кем жить дальше, в /play.",
    svc.R_COOLDOWN: "Автомат ещё крутится. Подожди немного.",
    svc.R_DAILY: "На сегодня автомат тебе больше не наливает. Приходи завтра.",
    svc.R_EDDIES: "Не хватает эдди на ставку.",
    svc.R_JAIL: "За решёткой ни автоматов, ни рыбалки.",
    svc.R_NO_ROD: "Сначала нужна удочка.",
    svc.R_CASTS: "На сегодня хватит: рыба устала от тебя. Приходи завтра.",
    svc.R_HAVE: "Такая или лучше уже есть.",
    svc.R_GONE: "Этого уже нет.",
    svc.R_EMPTY: "Садок пуст: продавать нечего.",
}


def _who_line(ch: ActiveCharacter) -> str:
    return texts.esc(f"{ch.state.name} {ch.state.surname}".strip())


async def on_text(ui: UiKit, message: Message) -> bool:
    """A word of a chat game in an ordinary message. Returns True when a game took it."""
    user = message.from_user
    text = (message.text or "").strip()
    if user is None or not text or text.startswith("/"):
        return False
    cfg = ui.content.balance.chat_games
    if eng.match_word(text, cfg.slots.words, cfg.slots.max_message_chars):
        await _slots(ui, message)
        return True
    if eng.match_word(text, ui.content.balance.caliber.words, cfg.slots.max_message_chars):
        from app.bot.handlers.minigames import caliber_roll

        await caliber_roll(ui, message)
        return True
    if eng.match_word(text, cfg.fishing.words, cfg.slots.max_message_chars):
        ctx = await make_ctx(ui, user, message.chat.id)
        await ui.windows.open(message.chat.id, user.id, await fishing_open(ctx, greet=True))
        return True
    return False


async def _slots(ui: UiKit, message: Message) -> None:
    user = message.from_user
    assert user is not None
    chat = message.chat.id
    content = ui.content
    cfg = content.balance.chat_games.slots
    tz = ui.settings.timezone
    stake = eng.stake_from(message.text or "", cfg)
    async with db.session_scope() as s:
        who = await svc.ensure_character(s, content, user.id, user.username, tz)
        if who.blocked or who.ch is None:
            ui.reply_temp(chat, BLOCKED.get(who.blocked or "", BLOCKED[svc.R_DEAD]))
            return
        ch = who.ch
        stake = min(stake, max(cfg.stake_min, ch.state.eddies))
        why = svc.slots_block(content, ch, stake, tz)
    if who.created:
        ui.reply_temp(chat, f"В Найт-Сити новый житель: <b>{_who_line(ch)}</b>. Жизнь — в /play, а пока к автомату.", 600)
    if why is not None:
        ui.reply_temp(chat, BLOCKED[why], 30)
        return
    value = await ui.roll_dice(chat, "🎰")
    if value is None:
        value = random.randint(1, 64)
    else:
        await ui.reveal()
    async with db.session_scope() as s:
        ch2 = await game.get_active_character(s, user.id)
        if ch2 is None or ch2.state.eddies < stake:
            ui.reply_temp(chat, BLOCKED[svc.R_EDDIES], 30)
            return
        res = await svc.spin(s, content, ch2, stake, value, tz)
    name = _who_line(ch2)
    line = eng.slot_line(res.reels)
    if res.mult >= cfg.jackpot_mult:
        tail = f"<b>ДЖЕКПОТ!</b> +{res.win - res.stake} эдди."
    elif res.mult:
        tail = f"выигрыш ×{res.mult}: +{res.win - res.stake} эдди."
    else:
        tail = f"мимо, −{res.stake} эдди."
    ui.reply_temp(chat, f"🎰 <b>{name}</b>: {line} — ставка {res.stake}, {tail} На счету {views.money(ch2.state.eddies)}.",
                  cfg.result_ttl_s)


# -- fishing -------------------------------------------------------------------------------------------------------------


async def fishing_open(ctx: Ctx, greet: bool = False) -> Screen:
    async with db.session_scope() as s:
        who = await svc.ensure_character(s, ctx.content, ctx.user_id, ctx.username, ctx.ui.settings.timezone)
        if who.blocked or who.ch is None:
            return Screen(BLOCKED[svc.R_DEAD], [[Button(texts.MENU, "menu")]], name="fishing")
        year = await game.current_year(s, ctx.content)
    note = None
    if who.created:
        note = f"В Найт-Сити новый житель: <b>{_who_line(who.ch)}</b>. Остальная жизнь — в меню."
    return fishing_screen(ctx.content, who.ch, year, ctx.ui.settings.timezone, note)


def fishing_screen(content: Content, ch: ActiveCharacter, year: int, tz: str, note: str | None = None) -> Screen:
    cfg = content.balance.chat_games.fishing
    st = ch.state
    rec = svc.fishing_state(ch, tz)
    rod = eng.rod(content, rec.get("rod"))
    net = svc.keepnet(ch)
    total, sale = eng.sale(content, net, st.district)
    harbor = st.district == cfg.harbor_district
    lines = [style.header("cat", "Рыбалка"), f"Эдди: {views.money(st.eddies)} · Район: {views.district_name(content, st.district)}"]
    if harbor:
        lines.append("<i>Портовый район: редкая рыба клюёт чаще, рынок платит на 30% больше.</i>")
    elif st.district in cfg.dry_districts:
        lines.append("<i>Воды тут мало, рыбы ещё меньше. Лучше ехать в Портовый район.</i>")
    lines.append("")
    rows: list[list[Button]] = []
    if rod is None:
        lines.append("Удочки нет. Можно купить или взять в кредит у ростовщика (с наценкой "
                     f"{cfg.credit_markup_pct}%, уйдёт в долг).")
    else:
        lines.append(f"Удочка: <b>{texts.esc(rod.name)}</b>. Забросов сегодня: {rec.get('casts', 0)}/{cfg.daily_casts}. "
                     f"Наживка {cfg.bait_cost} эдди за заброс.")
        rows.append([Button(f"Закинуть ({cfg.bait_cost})", "cast")])
    if sale:
        lines.append("")
        lines.append("<b>Садок:</b> " + ", ".join(f"{texts.esc(x.name)} ×{x.count}" for x in sale)
                     + f" — на рынке {views.money(total)}.")
        rows.append([Button(f"Продать улов ({total})", "sell")])
    better = [r for r in eng.rods_for(content, year) if rod is None or r.tier > rod.tier]
    if better:
        lines.append("")
        lines.append("<b>Удочки:</b>")
        for r in better:
            lines.append(f"{texts.esc(r.name)} · {views.money(r.price)}\n<i>{texts.esc(r.description)}</i>")
            rows.append([Button(f"{r.name} · {r.price}"[:30], "buy", r.key),
                         Button("В кредит", "credit", r.key)])
    if note:
        lines += ["", note]
    rows.append([Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="fishing")


@on_screen("fishing")
async def fishing_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "event" and has_screen("evchoice"):
        return await goto(ctx, "evchoice", "resume")
    c = ctx.content
    tz = ctx.ui.settings.timezone
    note: str | None = None
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await fishing_open(ctx)
        year = await game.current_year(s, c)
        if action in ("buy", "credit") and arg:
            why, paid = await svc.buy_rod(s, c, ch, year, tz, arg, credit=action == "credit")
            if why:
                note = BLOCKED.get(why, why)
            else:
                name = eng.rod(c, arg).name  # type: ignore[union-attr]
                note = (f"{texts.esc(name)} твоя. В долг записано {views.money(paid)}."
                        if action == "credit" else f"{texts.esc(name)} куплена за {views.money(paid)}.")
        elif action == "cast":
            res = await svc.cast(s, c, ch, year, tz, random.Random())
            if res.blocked:
                note = BLOCKED.get(res.blocked, res.blocked)
            elif res.catch is not None:
                fc = res.catch
                head = {"junk": "Улов", "fish": "Улов", "loot": "Находка", "legend": "ЛЕГЕНДА"}[fc.kind]
                note = f"<b>{head}: {texts.esc(fc.name)}</b>\n{texts.esc(fc.text)}"
                if res.applied is not None and fc.kind == "loot":
                    extra = views.effects_lines(res.applied, c)
                    if extra:
                        note += "\n" + "\n".join(extra)
                if res.event:
                    screen = fishing_screen(c, ch, year, tz, note)
                    screen.keyboard.insert(0, [Button("Прочитать записку", "event")])
                    return screen
        elif action == "sell":
            why, total = await svc.sell(s, c, ch, year, tz)
            note = BLOCKED.get(why, why) if why else f"Рыбный рынок забирает улов: +{views.money(total)}."
    return fishing_screen(c, ch, year, tz, note)
