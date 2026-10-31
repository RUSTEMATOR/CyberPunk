"""The city raid in the window (/raid, or the «Рейд» button of the menu while a raid is on).

raid (the boss, the damage table, four attack buttons) -> the same screen with the result of the hit on top.
The group gets a post when a boss appears and when it falls or escapes (`post_spawn`, `post_close`).
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.bot import texts, views
from app.bot import texts_raid as T
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, make_ctx, on_screen
from app.bot.ui.kit import UiKit
from app.bot.ui.screen import Button, Screen, pack
from app.content.loader import Content
from app.db.models import Raid
from app.db import session as db
from app.engine import raid as engine
from app.services import game
from app.services import raid as svc

router = Router(name="raid")


def _left(raid: Raid) -> tuple[int, int]:
    secs = max(0, int((raid.ends_at - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()))
    return secs // 3600, (secs % 3600) // 60


def table(raid: Raid, limit: int = 5) -> list[str]:
    hits = (raid.data or {}).get("hits") or {}
    rows = sorted(hits.values(), key=lambda h: -int(h.get("dmg", 0)))[:limit]
    return [T.TOP_LINE.format(n=i, name=texts.esc(str(h.get("name", ""))) + (f" «{texts.esc(h['crew'])}»" if h.get("crew") else ""),
                              dmg=int(h.get("dmg", 0)))
            for i, h in enumerate(rows, 1)]


async def raid_screen(ctx: Ctx, note: str | None = None) -> Screen:
    c = ctx.content
    cfg = c.balance.players.raid
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        raid = await svc.active(s)
    if raid is None:
        text = (f"{note}\n\n" if note else "") + T.NONE
        return Screen(text, [[Button(T.BTN_MENU, "menu")]], name="raid")
    boss = svc.boss_of(c, raid)
    lines = [T.TITLE.format(name=texts.esc(boss.name if boss else raid.boss))]
    if note:
        lines += [note, ""]
    if boss is not None:
        lines.append(style.quote(texts.esc(boss.text)))
    lines.append(T.HP.format(bar=style.bar(raid.hp, raid.hp_max, 10), hp=raid.hp, max=raid.hp_max))
    h, m = _left(raid)
    lines.append(T.TIME_LEFT.format(h=h, m=m))
    support = int((raid.data or {}).get("support", 0))
    if support:
        lines.append(T.SUPPORT.format(bonus=cfg.support_bonus, n=support))
    lines += ["", T.TOP_HEAD]
    lines += table(raid) or [T.TOP_EMPTY]
    mine = ((raid.data or {}).get("hits") or {}).get(str(ctx.user_id), {})
    lines.append("")
    rows: list[list[Button]] = []
    if mine.get("day") == svc.today(tz):
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
        g = str(ch.state.flags.get("gender", "m")) if ch is not None else "m"
        lines.append(T.YOU_DONE.format(a="а" if g == "f" else "", dmg=int(mine.get("dmg", 0))))
    else:
        lines.append(T.YOU_READY.format(ap=cfg.ap))
        lines.append(T.APPROACH_HELP.format(bonus=cfg.support_bonus))
        weak = boss.weak if boss else ""
        rows = pack([Button(T.APPROACH[a] + (T.WEAK_MARK if a == weak else ""), "hit", a) for a in engine.APPROACHES],
                    c.balance.ui.button_chars, max_per_row=2)
    rows.append([Button(T.BTN_REFRESH, "open"), Button(T.BTN_MENU, "menu")])
    return Screen("\n".join(lines), rows, name="raid")


@on_screen("raid")
async def raid_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "hit" and arg in engine.APPROACHES:
        return await _hit(ctx, arg)
    return await raid_screen(ctx)


async def _hit(ctx: Ctx, approach: str) -> Screen:
    c = ctx.content
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        res = await svc.hit(s, c, ch, random.Random(), year, tz, approach)
    if res.no_ap:
        return await raid_screen(ctx, T.NO_AP)
    if res.blocked is not None or res.hit is None:
        return await raid_screen(ctx, T.BLOCKED.get(res.blocked or "none", ""))
    h = res.hit
    lines = [views.check_line(h.check),
             T.HIT.format(damage=h.damage, weak=T.HIT_WEAK if h.weak else "",
                          support=T.HIT_SUPPORT.format(n=h.supported) if h.supported else "")]
    if approach == engine.SUPPORT and h.check.ok:
        lines.append(T.HIT_SUPPORT_LEFT.format(bonus=c.balance.players.raid.support_bonus))
    if h.hp_loss:
        lines.append(T.HIT_BACK.format(hp=h.hp_loss))
    if res.closed and res.raid is not None:
        lines.append(T.KILLED)
        post_close(ctx.ui, res.raid, res.closed, won=True)
    return await raid_screen(ctx, "\n".join(lines))


# -- the group posts -----------------------------------------------------------------------------------------------------


def post_spawn(ui: UiKit, raid: Raid) -> None:
    boss = svc.boss_of(ui.content, raid)
    if boss is None:
        return
    ttl = ui.content.balance.players.raid.duration_hours * 3600
    ui.post(T.POST_SPAWN.format(name=texts.esc(boss.name), text=texts.esc(boss.text), hp=raid.hp_max), ttl)


def post_close(ui: UiKit, raid: Raid, shares: list[engine.Share], *, won: bool) -> None:
    boss = svc.boss_of(ui.content, raid)
    if boss is None:
        return
    rows = [T.POST_ROW.format(n=i, name=texts.esc(s.name), dmg=s.dmg, eddies=views.money(s.eddies))
            for i, s in enumerate(shares[:10], 1)] or [T.POST_NOBODY]
    tpl = T.POST_WON if won else T.POST_ESCAPED
    ui.post(tpl.format(name=texts.esc(boss.name), text=texts.esc(boss.win_text if won else boss.escape_text),
                       table="\n".join(rows)), 24 * 3600)


async def raid_tick(ui: UiKit) -> None:
    """Scheduler job: an escaped boss is closed; a new boss appears when it is due."""
    c = ui.content
    async with db.session_scope() as s:
        gone = await svc.expire(s, c)
    if gone is not None:
        post_close(ui, gone[0], gone[1], won=False)
    async with db.session_scope() as s:
        if not await svc.due_to_spawn(s, c, ui.settings.timezone):
            return
        raid = await svc.spawn(s, c, random.Random())
    if raid is not None:
        post_spawn(ui, raid)


# -- commands ------------------------------------------------------------------------------------------------------------


@router.message(Command("raid"))
async def cmd_raid(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    ctx = await make_ctx(ui, user, message.chat.id)
    await ui.windows.open(message.chat.id, user.id, await raid_screen(ctx))


@router.message(Command("admin_raid"))
async def cmd_admin_raid(message: Message, ui: UiKit, command: CommandObject) -> None:
    """Admin: start a raid now (optionally a boss key: /admin_raid scav_nest)."""
    user = message.from_user
    if user is None or not ui.is_admin(user.id):
        return
    async with db.session_scope() as s:
        raid = await svc.spawn(s, ui.content, random.Random(), (command.args or "").strip() or None)
    if raid is None:
        ui.reply_temp(message.chat.id, "Рейд уже идёт.")
        return
    post_spawn(ui, raid)


async def menu_line(ctx: Ctx) -> tuple[str, bool] | None:
    """The menu line and whether to show the button, while a raid is on."""
    async with db.session_scope() as s:
        raid = await svc.active(s)
    if raid is None:
        return None
    boss = svc.boss_of(ctx.content, raid)
    pct = round(100 * raid.hp / raid.hp_max) if raid.hp_max else 0
    return T.MENU_LINE.format(name=texts.esc(boss.name if boss else raid.boss), pct=pct), True
