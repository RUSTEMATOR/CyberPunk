"""The daily loop in the window (PLAN 4.7, DESIGN.md 12a): /daily, quests, the Afterlife shop.

Screens: `daily` (the card and the streak) -> `quests` (claim) / `shop` (buy). The window opens on `daily`
from the menu button or the /daily command. All of it works without a living character.
"""

from __future__ import annotations

import random

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot import commands, texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.ui.callbacks import Ctx, goto, make_ctx, on_screen
from app.bot.ui import style
from app.bot.ui.kit import UiKit
from app.bot.ui.screen import Button, Screen, list_rows, pack
from app.clock import kyiv_date
from app.db import repo
from app.db import session as db
from app.engine import daily as engine_daily
from app.services import daily as daily_service
from app.services import game, quests, shop

router = Router(name="daily")


def _tz(ctx: Ctx) -> str:
    return ctx.ui.settings.timezone


def _keyboard() -> list[list[Button]]:
    return [
        [Button(style.label("quest", texts.BTN_QUESTS), "quests"), Button(style.label("crypto", texts.BTN_COIN), "coin")],
        [Button(style.label("afterlife", texts.BTN_SHOP), "shop")],
        [Button(style.label("car", texts.BTN_CAR), "car"), Button(style.label("cat", texts.BTN_CAT), "cat")],
        [Button(texts.MENU, "back")],
    ]


def card_lines(card, hidden: bool) -> list[str]:  # card: TarotCard
    """The card with its arcana icon, its text and its effect. A card just drawn is hidden under a spoiler:
    the player taps to turn it over."""
    icon = style.ARCANA.get(card.key, style.icon("daily"))
    name = texts.DAILY_CARD.format(icon=icon, name=texts.esc(card.name))
    text = f"<i>{texts.esc(card.text)}</i>"
    if hidden:
        lines = [texts.DAILY_REVEAL, style.spoiler(name), style.quote(style.spoiler(text))]
    else:
        lines = [texts.DAILY_CARD_LINE, name, style.quote(text)]
    if card.mods:
        lines.append(texts.DAILY_EFFECT.format(mods=views.mods_text(dict(card.mods))))
    return lines


def _card_lines(res: daily_service.DailyResult) -> list[str]:
    """The card, its effect and what the claim paid."""
    assert res.card is not None
    return card_lines(res.card, hidden=not res.already)


async def daily_screen(ctx: Ctx, res: daily_service.DailyResult | None = None) -> Screen:
    """The daily window. `res` is the result of a claim that just happened (None = just looking)."""
    tz = _tz(ctx)
    today = kyiv_date(tz)
    async with db.session_scope() as s:
        player = await repo.ensure_player(s, ctx.user_id, ctx.username)
        ch = await game.get_active_character(s, ctx.user_id)
        card = await daily_service.current_card(s, ctx.content, ctx.user_id, today)
        streak, freeze, tokens = int(player.streak or 0), int(player.freeze_count or 0), int(player.tokens or 0)
        claimed_today = player.last_daily == today
        at_risk = engine_daily.streak_at_risk(player.last_daily, today, streak, freeze)
    lines = [texts.DAILY_TITLE]
    rows: list[list[Button]] = []
    if res is not None and res.card is not None:
        if res.already:
            lines.append(texts.DAILY_ALREADY)
        lines += _card_lines(res)
        if not res.already:
            lines.append(_payout_line(res))
            lines.append(texts.DAILY_STREAK.format(streak=res.streak))
            if res.streak_broken:
                lines.append(texts.DAILY_STREAK_BROKEN)
            if res.freezes_used:
                lines.append(texts.DAILY_FREEZE_USED.format(n=res.freezes_used))
            for day in res.chests:
                reward = ctx.content.balance.daily.streak_rewards[day]
                bits = []
                if reward.eddies:
                    bits.append(texts.REWARD_EDDIES.format(n=views.money(reward.eddies)))
                if reward.tokens:
                    bits.append(texts.REWARD_TOKENS.format(n=reward.tokens))
                lines.append(texts.DAILY_CHEST.format(day=day, reward=", ".join(bits)))
    elif claimed_today and card is not None:
        lines.append(texts.DAILY_ALREADY)
        lines += card_lines(card, hidden=False)
    else:
        lines.append(texts.DAILY_WAITING)
        rows.append([Button(style.label("misty", texts.BTN_DAILY_CLAIM), "claim")])
    lines.append(style.SEP)
    lines.append(texts.DAILY_STATUS.format(streak=streak, freeze=freeze))
    lines.append(texts.DAILY_TOKENS.format(tokens=tokens))
    if at_risk and not claimed_today:
        lines.append(texts.DAILY_RISK)
    if ch is None:
        lines.append(texts.DAILY_NO_CHAR)
    return Screen("\n".join(lines), rows + _keyboard(), name="daily")


def _payout_line(res: daily_service.DailyResult) -> str:
    total = res.payout + res.chest_eddies
    value = views.signed(total)
    return (texts.DAILY_PAYOUT if res.paid else texts.DAILY_PAYOUT_WAIT).format(value=value)


async def _claim(ctx: Ctx) -> Screen:
    tz = _tz(ctx)
    rng = random.Random()
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
        res = await daily_service.claim(s, ctx.content, ctx.user_id, ctx.username, ch, rng, year, kyiv_date(tz), tz)
    return await daily_screen(ctx, res)


@on_screen("daily")
async def daily_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await menu_screen(ctx)
    if action == "claim":
        return await _claim(ctx)
    if action == "quests":
        return await quests_screen(ctx)
    if action == "shop":
        return await shop_screen(ctx)
    if action in ("car", "cat", "coin"):
        return await goto(ctx, action)
    return await daily_screen(ctx)


# -- quests ----------------------------------------------------------------------------------------------------


async def quests_screen(ctx: Ctx, note: str | None = None) -> Screen:
    content = ctx.content
    today = kyiv_date(_tz(ctx))
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        rows = await quests.ensure(s, content, ctx.user_id, bool(ch and ch.state.chrome_free), today)
    lines = [texts.QUESTS_TITLE, texts.QUESTS_NOTE]
    buttons: list[list[Button]] = []
    if note:
        lines += ["", note]
    claims: list[Button] = []
    num = 0
    for period, head in (("daily", texts.QUESTS_DAILY_HEAD), ("weekly", texts.QUESTS_WEEKLY_HEAD)):
        lines += ["", head]
        part = [r for r in rows if r.period == period]
        if not part:
            lines.append(texts.QUESTS_NONE)
        for r in part:
            q = quests.quest_of(content, r)
            if q is None:
                continue
            mark = texts.QUEST_MARK_CLAIMED if r.claimed else texts.QUEST_MARK_DONE if r.done else texts.QUEST_MARK_OPEN
            reward = texts.QUEST_REWARD.format(parts=", ".join(views.reward_parts(content, q.period, q.rewards)))
            num += 1
            lines.append(f"{num}. " + texts.QUEST_LINE.format(
                mark=mark, text=texts.esc(q.text), progress=r.progress, target=r.target,
                reward="" if r.claimed else reward,
            ).strip())
            if r.done and not r.claimed:
                claims.append(Button(texts.QUEST_BTN_CLAIM.format(n=num), "claim", str(r.id)))
    buttons += pack(claims, content.balance.ui.button_chars, max_per_row=2)
    buttons.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), buttons, name="quests")


@on_screen("quests")
async def quests_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await daily_screen(ctx)
    if action == "claim" and arg and arg.isdigit():
        tz = _tz(ctx)
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            year = await game.current_year(s, ctx.content)
            res = await quests.claim(s, ctx.content, ctx.user_id, int(arg), ch, random.Random(), year, tz)
        if not res.ok:
            note = {"gone": texts.QUEST_CLAIM_GONE, "not_done": texts.QUEST_CLAIM_NOT_DONE,
                    "claimed": texts.QUEST_CLAIM_ALREADY}.get(res.reason or "", texts.QUEST_CLAIM_GONE)
            return await quests_screen(ctx, note)
        assert res.quest is not None
        parts = views.reward_parts(ctx.content, res.quest.period, res.quest.rewards)
        note = texts.QUEST_CLAIMED.format(text=texts.esc(res.quest.text), parts=", ".join(parts))
        if res.lost:
            note += " " + texts.QUEST_CLAIM_LOST
        if res.lines:
            note += "\n" + "\n".join(texts.esc(x) for x in res.lines)
        return await quests_screen(ctx, note)
    return await quests_screen(ctx)


# -- the shop --------------------------------------------------------------------------------------------------


async def shop_screen(ctx: Ctx, note: str | None = None) -> Screen:
    content = ctx.content
    async with db.session_scope() as s:
        player = await repo.ensure_player(s, ctx.user_id, ctx.username)
        tokens = int(player.tokens or 0)
    lines = [texts.SHOP_TITLE, texts.SHOP_NOTE.format(tokens=tokens)]
    if note:
        lines += ["", note]
    items = shop.items(content)
    numbered, buttons = list_rows([i.name for i in items], "buy", [i.key for i in items], content.balance.ui.button_chars)
    for n, item in enumerate(items, 1):
        lines += ["", (f"{n}. " if numbered else "") + texts.SHOP_LINE.format(
            name=texts.esc(item.name), price=item.price_tokens, desc=texts.esc(item.description))]
    buttons.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), buttons, name="shop")


@on_screen("shop")
async def shop_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await daily_screen(ctx)
    if action == "buy" and arg:
        tz = _tz(ctx)
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            year = await game.current_year(s, ctx.content)
            res = await shop.buy(s, ctx.content, ctx.user_id, arg, ch, random.Random(), year, kyiv_date(tz), tz)
        if not res.ok:
            return await shop_screen(ctx, texts.SHOP_BLOCKED.get(res.reason or "", texts.SHOP_BLOCKED["unknown"]))
        assert res.item is not None
        kind, _ = shop.effect_kind(res.item)
        name = texts.esc(res.item.name)
        if kind == "tarot_reroll":
            note = texts.SHOP_BOUGHT_CARD.format(name=name, card=texts.esc(res.detail))
        elif kind == "title":
            note = texts.SHOP_BOUGHT_TITLE.format(name=name, title=texts.esc(res.detail))
        else:
            note = texts.SHOP_BOUGHT.format(name=name)
        return await shop_screen(ctx, note)
    return await shop_screen(ctx)


# -- the command -----------------------------------------------------------------------------------------------


@router.message(Command("daily"))
async def cmd_daily(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    ctx = await make_ctx(ui, user, message.chat.id)
    await ui.windows.open(message.chat.id, user.id, await daily_screen(ctx))
