"""Mini-games in the group (PLAN 4.7, DESIGN.md 12a): /caliber, /car, /race, /cat, /catfight, /coin, /dirt, /dump.

One-shot results (caliber, races, fights, dirt) are temporary group messages that live
`balance.ui.minigame_ttl_s`; the dump is a public post that lives `balance.dirt.post_ttl_h`. The car,
the cat and the coin have a window screen each (buttons for tuning, feeding, trading).
"""

from __future__ import annotations

import random

from aiogram import Router
from aiogram.enums import DiceEmoji
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.bot import mention, texts, views
from app.bot.handlers.daily import daily_screen
from app.bot.handlers.targets import has_target_token, number_arg, target_player
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, make_ctx, on_screen, on_text
from app.bot.ui.cleanup import KIND_ALERT
from app.bot.ui.kit import UiKit
from app.bot.ui.screen import Button, Screen, pack
from app.clock import kyiv_date
from app.content.loader import Content
from app.db import repo
from app.db import session as db
from app.engine import minigames as engine_mg
from app.engine.dice import TG_DIE_FACES, roll_from_face
from app.services import game, minigames, shop

router = Router(name="minigames")


def _ttl(ui: UiKit) -> float:
    return float(ui.cfg.minigame_ttl_s)


async def throw(ui: UiKit, chat_id: int, expr: str, n: int) -> tuple[list[int | None], list[int] | None]:
    """Throw `n` animated 🎲 in the chat and map each face onto the roll `expr` of the balance
    (`dice.roll_from_face`). Returns (faces, rolls); rolls is None when a dice could not be sent, and the service then
    rolls with its own rng. Waits for the animation, so the result lands after the dice."""
    faces: list[int | None] = []
    for _ in range(n):
        faces.append(await ui.roll_dice(chat_id, DiceEmoji.DICE.value))
    if any(f is None for f in faces):
        return faces, None
    await ui.reveal()
    return faces, [roll_from_face(expr, int(f), TG_DIE_FACES) for f in faces if f is not None]


def _roll_text(face: int | None, roll: int) -> str:
    """'кубик 4 → 34' when the roll came from a Telegram dice, else 'бросок 34'."""
    return texts.ROLL_DICE.format(face=face, roll=roll) if face is not None else texts.ROLL_PLAIN.format(roll=roll)


async def _name(s, tg_id: int, fallback_user=None) -> str:  # noqa: ANN001
    player = await repo.ensure_player(s, tg_id, getattr(fallback_user, "username", None))
    char = await repo.display_name(s, tg_id, getattr(fallback_user, "full_name", None) or "игрок")
    return mention.plain(player.username, char, tg_id)


# -- caliber ---------------------------------------------------------------------------------------------------


@router.message(Command("caliber"))
async def cmd_caliber(message: Message, ui: UiKit) -> None:
    await caliber_roll(ui, message)


async def caliber_roll(ui: UiKit, message: Message) -> None:
    """The caliber of the day (/caliber or a word in the chat, owner request)."""
    user = message.from_user
    if user is None:
        return
    today = kyiv_date(ui.settings.timezone)
    async with db.session_scope() as s:
        probe = await minigames.caliber_daily(s, ui.content, user.id, user.username, random.Random(), today, dry_run=True)
    faces: list[int | None] = [None]
    rolls: list[int] | None = None
    if not probe.already:
        faces, rolls = await throw(ui, message.chat.id, ui.content.balance.caliber.change_dice, 1)
    async with db.session_scope() as s:
        who = await _name(s, user.id, user)
        out = await minigames.caliber_daily(s, ui.content, user.id, user.username, random.Random(), today,
                                            change=rolls[0] if rolls else None)
        ch = await game.get_active_character(s, user.id)  # the caliber helps the living character (owner request)
        if ch is not None and ch.state.flags.get("caliber_mm") != out.mm:
            ch.state.flags["caliber_mm"] = out.mm
            await game.save_active(s, ch, ui.settings.timezone)
    if out.already:
        text = texts.CALIBER_ALREADY.format(who=who, mm=out.mm, title=out.title)
    else:
        assert out.result is not None
        change = views.signed(out.result.change)
        lines = [texts.CALIBER_HEAD.format(who=who)]
        if rolls:
            lines.append(texts.CALIBER_DICE.format(face=faces[0], change=change))
        lines.append(texts.CALIBER_ROLL.format(mm=out.mm, change=change, title=out.title))
        text = "\n".join(lines)
        if out.result.title_changed:
            text += "\n" + texts.CALIBER_NEW_TITLE.format(title=out.title)
        bonus = next((t for t in sorted(ui.content.balance.caliber.titles, key=lambda t: -t.mm) if out.mm >= t.mm), None)
        if bonus is not None and bonus.mods:
            from app.bot import effects_text

            text += "\n<i>В игре: " + texts.esc(", ".join(effects_text.mods_phrases(bonus.mods))) + ".</i>"
    ui.reply_temp(message.chat.id, text, _ttl(ui))


@router.message(Command("caliber_top"))
async def cmd_caliber_top(message: Message, ui: UiKit) -> None:
    async with db.session_scope() as s:
        rows = await minigames.caliber_top(s, ui.content, ui.content.balance.reminders.legends_top_n)
        lines = [texts.CALIBER_TOP_TITLE]
        for n, (player, mm, title) in enumerate(rows, 1):
            who = await _name(s, player.tg_id)
            lines.append(texts.CALIBER_TOP_ROW.format(n=style.rank(n), who=who, mm=mm, title=title))
    if not rows:
        lines.append(texts.CALIBER_TOP_EMPTY)
    ui.reply_temp(message.chat.id, "\n".join(lines), _ttl(ui))


# -- dirt and dump -----------------------------------------------------------------------------------------------


@router.message(Command("dirt"))
async def cmd_dirt(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    today = kyiv_date(ui.settings.timezone)
    cost = ui.content.balance.dirt.dump_cost
    async with db.session_scope() as s:
        who = await _name(s, user.id, user)
        out = await minigames.dirt_daily(s, ui.content, user.id, user.username, random.Random(), today)
    text = (texts.DIRT_ALREADY.format(who=who, total=out.total, cost=cost) if out.already
            else texts.DIRT_GAIN.format(who=who, gain=out.gain, total=out.total, cost=cost))
    ui.reply_temp(message.chat.id, text, _ttl(ui))


@router.message(Command("dump"))
async def cmd_dump(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    chat = message.chat.id
    target_id = await target_player(message)
    if target_id is None:
        ui.reply_temp(chat, texts.DUMP_BLOCKED["no_player"] if has_target_token(message) else texts.DUMP_USAGE)
        return
    tz = ui.settings.timezone
    cfg = ui.content.balance.dirt
    async with db.session_scope() as s:
        await repo.ensure_player(s, user.id, user.username)
        year = await game.current_year(s, ui.content)
        out = await minigames.dump(s, ui.content, user.id, target_id, random.Random(), year, kyiv_date(tz), tz)
        target = await repo.get_player(s, target_id)
        t_ping = mention.ping(target.username if target else None, out.target_name or None, target_id)
        who = await _name(s, user.id, user)
    if out.status in (minigames.DUMP_OK, minigames.DUMP_DEFENDED):
        if out.status == minigames.DUMP_OK:
            text = texts.DUMP_OK.format(target=t_ping, heat=f"+{out.heat}" if out.heat else "на пределе", who=who)
        else:
            tpl = texts.DUMP_DEFENDED_FIXER if out.defense == "fixer" else texts.DUMP_DEFENDED_CHECK
            text = tpl.format(who=who, target=t_ping)
        ui.post(text, cfg.post_ttl_h * 3600, KIND_ALERT)
        return
    tpl = texts.DUMP_BLOCKED[out.status]
    ui.reply_temp(chat, tpl.format(cost=cfg.dump_cost, have=out.attacker_dirt, days=out.days_left))


# -- car -------------------------------------------------------------------------------------------------------


async def car_screen(ctx: Ctx, note: str | None = None) -> Screen:
    content = ctx.content
    today = kyiv_date(ctx.ui.settings.timezone)
    async with db.session_scope() as s:
        player = await minigames.get_car(s, content, ctx.user_id, ctx.username)
        hp, name = int(player.car_hp), player.car_name or content.balance.car.default_name
        missing = list(player.car_parts or [])
        tuned = (player.mini_last or {}).get("car") == today
    lines = [texts.CAR_TITLE.format(name=texts.esc(name)), texts.CAR_POWER.format(hp=hp)]
    lines.append(texts.CAR_PARTS_MISSING.format(parts=", ".join(minigames.part_name(content, k) for k in missing))
                 if missing else texts.CAR_PARTS_OK)
    lines.append(texts.CAR_TUNE_DONE if tuned else texts.CAR_TUNE_READY)
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    if not tuned:
        rows.append([Button(texts.CAR_TUNE_BTN, "tune")])
    for key in missing:
        rows.append([Button(texts.CAR_REPAIR_BTN.format(part=minigames.part_name(content, key),
                                                        cost=content.balance.car.repair_cost), "fix", key)])
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="car")


@on_screen("car")
async def car_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    tz = ctx.ui.settings.timezone
    if action == "back":
        return await daily_screen(ctx)
    if action == "tune":
        async with db.session_scope() as s:
            out = await minigames.tune(s, ctx.content, ctx.user_id, random.Random(), kyiv_date(tz))
        if out.already or out.result is None:
            return await car_screen(ctx)
        r = out.result
        note = (texts.CAR_TUNE_UP if r.after > r.before else texts.CAR_TUNE_DOWN if r.after < r.before
                else texts.CAR_TUNE_ZERO).format(change=views.signed(r.change), hp=r.after)
        if out.lost_part_name:
            note += "\n" + texts.CAR_PART_LOST.format(part=out.lost_part_name, loss=r.extra_loss)
        return await car_screen(ctx, note)
    if action == "fix" and arg:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            year = await game.current_year(s, ctx.content)
            res = await minigames.repair(s, ctx.content, ctx.user_id, ch, arg, random.Random(), year, tz)
        if res == minigames.REPAIR_OK:
            note = texts.CAR_REPAIRED.format(part=minigames.part_name(ctx.content, arg), cost=ctx.content.balance.car.repair_cost)
        else:
            note = texts.CAR_REPAIR_BLOCKED[res]
        return await car_screen(ctx, note)
    return await car_screen(ctx)


@router.message(Command("car"))
async def cmd_car(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    ctx = await make_ctx(ui, user, message.chat.id)
    await ui.windows.open(message.chat.id, user.id, await car_screen(ctx))


def _stakes(content: Content, kind: str) -> str:
    st = content.balance.car.stakes_tokens if kind == "car" else content.balance.cat.stakes_tokens
    return ", ".join(str(x) for x in st)


@router.message(Command("race"))
async def cmd_race(message: Message, command: CommandObject, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    chat = message.chat.id
    target_id = await target_player(message)
    if target_id is None:
        ui.reply_temp(chat, texts.RACE_BLOCKED["no_player"] if has_target_token(message)
                      else texts.RACE_USAGE.format(stakes=_stakes(ui.content, "car")))
        return
    stake = number_arg(command.args) or 0
    today = kyiv_date(ui.settings.timezone)
    async with db.session_scope() as s:
        await repo.ensure_player(s, user.id, user.username)
        probe = await minigames.race(s, ui.content, user.id, target_id, stake, random.Random(), today, dry_run=True)
    faces: list[int | None] = [None, None]
    rolls: list[int] | None = None
    if probe.status == minigames.RACE_OK:
        faces, rolls = await throw(ui, chat, ui.content.balance.car.race_roll, 2)
    async with db.session_scope() as s:
        out = await minigames.race(s, ui.content, user.id, target_id, stake, random.Random(), today,
                                   rolls=(rolls[0], rolls[1]) if rolls else None)
        a, b = await _name(s, user.id, user), await _name(s, target_id)
    if out.status != minigames.RACE_OK or out.result is None:
        ui.reply_temp(chat, texts.RACE_BLOCKED[out.status].format(stakes=_stakes(ui.content, "car")))
        return
    r = out.result
    lost = lambda n: texts.RACE_LOST_PARTS.format(n=n) if n else ""  # noqa: E731
    lines = [
        texts.RACE_HEAD.format(a=a, b=b),
        texts.RACE_SCORES.format(
            a=a, hp=out.hp[0], lost=lost(out.missing[0]), roll=_roll_text(faces[0] if rolls else None, r.rolls[0]),
            score=r.scores[0], b=b, hp2=out.hp[1], lost2=lost(out.missing[1]),
            roll2=_roll_text(faces[1] if rolls else None, r.rolls[1]), score2=r.scores[1],
        ),
    ]
    if r.winner is None:
        lines.append(texts.RACE_DRAW)
    else:
        lines.append(texts.RACE_WIN.format(who=a if r.winner == 0 else b,
                                           stake=texts.RACE_STAKE_LINE.format(n=out.stake) if out.stake else ""))
    ui.reply_temp(chat, "\n".join(lines), _ttl(ui))


# -- cat -------------------------------------------------------------------------------------------------------


async def cat_screen(ctx: Ctx, note: str | None = None) -> Screen:
    content = ctx.content
    cfg = content.balance.cat
    today = kyiv_date(ctx.ui.settings.timezone)
    async with db.session_scope() as s:
        player, cat = await minigames.get_cat(s, content, ctx.user_id, today, ctx.username)
        tokens = int(player.tokens or 0)
    state = engine_mg.cat_state(content, cat, today)
    level = int(cat["level"])
    lines = [texts.CAT_TITLE.format(name=texts.esc(str(cat["name"])), level_name=engine_mg.cat_level_name(content, level))]
    nxt = next((lv.feedings for lv in cfg.levels if lv.level == level + 1), None)
    lines.append(texts.CAT_LEVEL.format(
        level=level, max=len(cfg.levels), feedings=cat["feedings"],
        next=texts.CAT_NEXT.format(n=nxt - int(cat["feedings"])) if nxt is not None else "",
    ))
    lines.append(texts.CAT_MOOD[state])
    chrome = [c.name for c in cfg.chrome if c.key in (cat.get("chrome") or [])]
    lines.append(texts.CAT_CHROME.format(items=", ".join(chrome)) if chrome else texts.CAT_NO_CHROME)
    if state != engine_mg.CAT_GONE:
        lines.append(texts.CAT_FEED_READY if engine_mg.can_feed(cat, today) else texts.CAT_FED)
        lines.append(texts.CAT_FIGHTS_TODAY.format(
            state=texts.CAT_FIGHT_OPEN if engine_mg.fights_left(content, cat, today) > 0 else texts.CAT_FIGHT_USED))
    lines.append(texts.DAILY_TOKENS.format(tokens=tokens))
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    if state == engine_mg.CAT_GONE:
        rows.append([Button(texts.CAT_RETURN_BTN.format(cost=cfg.return_cost_tokens), "back_cat")])
    elif engine_mg.can_feed(cat, today):
        rows.append([Button(texts.CAT_FEED_BTN, "feed")])
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="cat")


@on_screen("cat")
async def cat_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    today = kyiv_date(ctx.ui.settings.timezone)
    if action == "back":
        return await daily_screen(ctx)
    if action == "feed":
        async with db.session_scope() as s:
            out = await minigames.feed(s, ctx.content, ctx.user_id, today)
        if out.status != "ok":
            return await cat_screen(ctx)
        note = texts.CAT_FED_NOW.format(feedings=out.cat["feedings"])
        if out.new_level:
            note += " " + texts.CAT_LEVEL_UP.format(level_name=engine_mg.cat_level_name(ctx.content, out.new_level))
        return await cat_screen(ctx, note)
    if action == "back_cat":
        async with db.session_scope() as s:
            res = await minigames.bring_cat_back(s, ctx.content, ctx.user_id, today)
        return await cat_screen(ctx, texts.CAT_RETURN_OK if res == "ok" else texts.CAT_RETURN_NO if res == "tokens" else None)
    return await cat_screen(ctx)


@router.message(Command("cat"))
async def cmd_cat(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    ctx = await make_ctx(ui, user, message.chat.id)
    await ui.windows.open(message.chat.id, user.id, await cat_screen(ctx))


@router.message(Command("catfight"))
async def cmd_catfight(message: Message, command: CommandObject, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    chat = message.chat.id
    target_id = await target_player(message)
    if target_id is None:
        ui.reply_temp(chat, texts.CATFIGHT_BLOCKED["no_player"] if has_target_token(message)
                      else texts.CATFIGHT_USAGE.format(stakes=_stakes(ui.content, "cat")))
        return
    stake = number_arg(command.args) or 0
    today = kyiv_date(ui.settings.timezone)
    async with db.session_scope() as s:
        await repo.ensure_player(s, user.id, user.username)
        probe = await minigames.cat_fight(s, ui.content, user.id, target_id, stake, random.Random(), today, dry_run=True)
    faces: list[int | None] = [None, None]
    rolls: list[int] | None = None
    if probe.status == "ok":
        faces, rolls = await throw(ui, chat, ui.content.balance.cat.fight_roll, 2)
    async with db.session_scope() as s:
        out = await minigames.cat_fight(s, ui.content, user.id, target_id, stake, random.Random(), today,
                                        rolls=(rolls[0], rolls[1]) if rolls else None)
        a, b = await _name(s, user.id, user), await _name(s, target_id)
    if out.status != "ok":
        ui.reply_temp(chat, texts.CATFIGHT_BLOCKED[out.status].format(stakes=_stakes(ui.content, "cat")))
        return
    na, nb = texts.esc(out.names[0]), texts.esc(out.names[1])
    lines = [
        texts.CATFIGHT_HEAD.format(a=f"{na} ({a})", b=f"{nb} ({b})"),
        texts.CATFIGHT_SCORES.format(
            a=na, sulk=texts.CATFIGHT_SULK if out.sulking[0] else "", score=out.scores[0],
            roll=_roll_text(faces[0] if rolls else None, out.rolls[0]),
            b=nb, sulk2=texts.CATFIGHT_SULK if out.sulking[1] else "", score2=out.scores[1],
            roll2=_roll_text(faces[1] if rolls else None, out.rolls[1]),
        ),
    ]
    if out.winner_id is None:
        lines.append(texts.CATFIGHT_DRAW)
    else:
        lines.append(texts.CATFIGHT_WIN.format(
            who=na if out.winner_id == user.id else nb,
            stake=texts.RACE_STAKE_LINE.format(n=out.stake) if out.stake else ""))
    ui.reply_temp(chat, "\n".join(lines), _ttl(ui))


# -- coin ------------------------------------------------------------------------------------------------------


def _coins(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") or "0"


async def coin_screen(ctx: Ctx, note: str | None = None) -> Screen:
    content = ctx.content
    cfg = content.balance.coin
    today = kyiv_date(ctx.ui.settings.timezone)
    async with db.session_scope() as s:
        rate = await minigames.ensure_coin_rate(s, content, today)
        history = await minigames.coin_history(s)
        player = await repo.ensure_player(s, ctx.user_id, ctx.username)
        wallet = float(player.coins or 0.0)
        ch = await game.get_active_character(s, ctx.user_id)
        top = await minigames.coin_top(s, rate, cfg.top_n)
        top_rows = [(await _name(s, p.tg_id), c, w) for p, c, w in top]
    delta = ""
    if len(history) >= 2 and history[-2]["rate"]:
        pct = (history[-1]["rate"] / history[-2]["rate"] - 1.0) * 100
        delta = texts.COIN_DELTA.format(pct=f"{pct:+.1f}%".replace("-", "−"))
    lines = [texts.COIN_TITLE, texts.COIN_RATE.format(rate=f"{rate:g}", delta=delta)]
    if len(history) >= 2:
        rates = [h["rate"] for h in history]
        lines.append(texts.COIN_RANGE.format(days=len(rates), lo=f"{min(rates):g}", hi=f"{max(rates):g}"))
    lines.append(texts.COIN_WALLET.format(coins=_coins(wallet), worth=views.money(round(wallet * rate))))
    if ch is not None:
        lines.append(texts.COIN_EDDIES.format(eddies=views.money(ch.state.eddies)))
    else:
        lines.append(texts.COIN_NO_CHAR)
    lines.append(texts.COIN_FEE.format(pct=f"{cfg.commission_pct * 100:g}"))
    if top_rows:
        lines += ["", texts.COIN_TOP_HEAD]
        lines += [texts.COIN_TOP_ROW.format(n=style.rank(n), who=w, coins=_coins(c), worth=views.money(v)) for n, (w, c, v) in enumerate(top_rows, 1)]
    if note:
        lines += ["", note]
    lines += ["", texts.COIN_BUY_HINT]
    rows = pack([Button(views.money(n), "buy", str(n)) for n in cfg.buy_steps], content.balance.ui.button_chars)
    rows.append([Button(texts.COIN_SELL_HALF, "sell", "half"), Button(texts.COIN_SELL_ALL, "sell", "all")])
    rows.append([Button("Купить на сумму", "ask", "buy"), Button("Продать на сумму", "ask", "sell")])  # owner request
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="coin")


@on_screen("coin")
async def coin_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    tz = ctx.ui.settings.timezone
    today = kyiv_date(tz)
    if action == "back":
        return await daily_screen(ctx)
    if action == "ask" and arg in ("buy", "sell"):
        what = "купить крипты" if arg == "buy" else "продать крипты"
        return Screen(f"{texts.COIN_TITLE}\nНапиши в чат сумму в эдди, на которую {what}. Например: 2500.",
                      [[Button(texts.BACK, "cancel")]], awaiting_text="coin_amount", name="coin", state={"mode": arg})
    if action == "cancel":
        return await coin_screen(ctx)
    if action in ("buy", "sell") and arg:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            year = await game.current_year(s, ctx.content)
            if action == "buy":
                trade = await minigames.coin_buy(s, ctx.content, ch, int(arg), random.Random(), year, today, tz)
            else:
                trade = await minigames.coin_sell(s, ctx.content, ch, ctx.user_id, 1.0 if arg == "all" else 0.5,
                                                  random.Random(), year, today, tz)
        if trade.status != minigames.COIN_OK:
            return await coin_screen(ctx, texts.COIN_BLOCKED[trade.status].format(min=ctx.content.balance.coin.min_deal_eddies))
        if action == "buy":
            note = texts.COIN_BOUGHT.format(coins=_coins(trade.coins), eddies=views.money(trade.eddies), rate=f"{trade.rate:g}")
        else:
            note = texts.COIN_SOLD.format(coins=_coins(trade.coins), rate=f"{trade.rate:g}",
                                          eddies=views.money(trade.eddies), fee=views.money(trade.fee))
        return await coin_screen(ctx, note)
    return await coin_screen(ctx)


@on_text("coin_amount")
async def coin_amount_text(ctx: Ctx, text: str) -> Screen | None:
    """Owner request: buy or sell the Eddy-crypto for a typed sum of eddies."""
    import re

    m = re.search(r"\d[\d\s]*", text or "")
    if not m:
        return await coin_screen(ctx, "Нужна сумма числом, например 2500.")
    amount = int(m.group(0).replace(" ", ""))
    mode = str(ctx.state.get("mode") or "buy")
    tz = ctx.ui.settings.timezone
    today = kyiv_date(tz)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
        if mode == "buy":
            trade = await minigames.coin_buy(s, ctx.content, ch, amount, random.Random(), year, today, tz)
        else:
            rate = await minigames.ensure_coin_rate(s, ctx.content, today)
            player = await repo.ensure_player(s, ctx.user_id, ctx.username)
            worth = float(player.coins or 0.0) * rate
            share = 1.0 if worth <= 0 else min(1.0, amount / worth)
            trade = await minigames.coin_sell(s, ctx.content, ch, ctx.user_id, share, random.Random(), year, today, tz)
    if trade.status != minigames.COIN_OK:
        return await coin_screen(ctx, texts.COIN_BLOCKED[trade.status].format(min=ctx.content.balance.coin.min_deal_eddies))
    if mode == "buy":
        note = texts.COIN_BOUGHT.format(coins=_coins(trade.coins), eddies=views.money(trade.eddies), rate=f"{trade.rate:g}")
    else:
        note = texts.COIN_SOLD.format(coins=_coins(trade.coins), rate=f"{trade.rate:g}",
                                      eddies=views.money(trade.eddies), fee=views.money(trade.fee))
    return await coin_screen(ctx, note)


@router.message(Command("coin"))
async def cmd_coin(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    ctx = await make_ctx(ui, user, message.chat.id)
    await ui.windows.open(message.chat.id, user.id, await coin_screen(ctx))
