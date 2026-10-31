"""Other players' characters in the window (docs/EPIC_PLAYERS.md, phase 1).

Отношения -> «Свои» (plist) -> plcard (one character: actions) -> [plgift: amount | plnote: typed text |
plduel: stake] -> plout (what happened). plist -> ploffers: the offers that wait for this player's answer.
"""

from __future__ import annotations

import random

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot import texts, views
from app.bot import texts_players as T
from app.bot.handlers.menu import menu_screen
from app.bot.ui.callbacks import Ctx, goto, make_ctx, on_screen, on_text
from app.bot.ui.kit import UiKit
from app.bot.ui.screen import Button, Screen, list_rows, pack
from app.content.loader import Content
from app.content.schemas import SKILLS
from app.db import repo
from app.db import session as db
from app.engine import players as pl
from app.services import game
from app.services import legends
from app.services import crews, market
from app.services import players as svc
from app.services.game import ActiveCharacter


def _name(o: svc.Other) -> str:
    """'Ольга Хван (@olga)': the character and the Telegram username of the player."""
    return texts.esc(o.name) + (T.USERNAME.format(u=texts.esc(o.username)) if o.username else "")


def _who(content: Content, o: svc.Other) -> tuple[str, str]:
    from app.engine import place

    where = place.of_district(content, o.district)  # owner request 30.10: the city shows when it is not Night City
    d = views.district_name(content, o.district) + ("" if where == place.NC else f", {place.name_of(content, where)}")
    return d, legends.roles_label(content, o.role_affinity)


def ping(ctx: Ctx, tg_id: int, who: str, what: str) -> None:
    """A short group message that tags the recipient (at most one per `ping_cooldown_s`)."""
    group = ctx.ui.group_id
    if group is None or not svc.ping_due(ctx.content, tg_id):
        return
    from app.services import dm_relay  # owner request 26.10: a player outside the group gets it in private

    ctx.ui.spawn(dm_relay.ping(ctx.ui, tg_id, T.PING.format(tg=tg_id, who=texts.esc(who), what=T.PING_WHAT.get(what, what)),
                               None, "temp"))


async def _ping_target(ctx: Ctx, tg_id: int, what: str) -> None:
    async with db.session_scope() as s:
        who = await repo.display_name(s, tg_id)
    ping(ctx, tg_id, who, what)


def _date(iso: str) -> str:
    """'2026-10-30T02:21' -> '30.10'."""
    try:
        return f"{iso[8:10]}.{iso[5:7]}"
    except Exception:  # noqa: BLE001
        return iso


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


# -- the list ----------------------------------------------------------------------------------------------------------


async def list_screen(ctx: Ctx, note: str | None = None) -> Screen:
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    async with db.session_scope() as s:
        people = await svc.others(s, c, ch, ctx.ui.settings.timezone)
        waiting = len(await svc.offers_for(s, c, ctx.user_id, year))
    async with db.session_scope() as s:
        merc = await svc.merc_get(s, c, ctx.user_id)
    async with db.session_scope() as s:
        off = await svc.pvp_off(s, ctx.user_id)
    lines = [T.TITLE, T.INTRO, "",
             T.MERC_STATUS_ON.format(share=merc["share"]) if merc["on"] else T.MERC_STATUS_OFF,
             T.PVP_STATUS_OFF if off else T.PVP_STATUS_ON]
    if note:
        lines += ["", note]
    lines.append("")
    if not people:
        lines.append(T.EMPTY)
    async with db.session_scope() as s:
        mine = await svc.active_quests(s, ctx.user_id)
    if mine:
        block = ["", T.QUESTS_HEAD]
        for q in mine:
            g = pl.quest_goal(c, str(q.get("goal")))
            block.append(T.QUESTS_LINE.format(text=texts.esc(g.text if g else "?"), name=texts.esc(str(q.get("from_name", ""))),
                                              reward=views.money(int(q.get("reward", 0))),
                                              until=_date(str(q.get("until", "")))))
        lines[5:5] = block
    numbered, rows = list_rows([o.name for o in people], "o", [str(o.char_id) for o in people],
                               c.balance.ui.button_chars)
    async with db.session_scope() as s:
        tags = {o.tg_id: (cr.name if (cr := await crews.crew_of(s, o.tg_id)) else None) for o in people}
        from app.services import bounties, leaderboard

        held = await leaderboard.titles(s)
        pots = await bounties.all_bounties(s)
    for i, o in enumerate(people, 1):
        district, roles = _who(c, o)
        tag = T.CREW_TAG.format(crew=texts.esc(tags[o.tg_id])) if tags.get(o.tg_id) else ""
        extra = "".join(f" «{texts.esc(t)}»" for t in held.get(o.tg_id, []))
        if pots.get(str(o.char_id)):
            extra += f" 🎯{views.money(int(pots[str(o.char_id)]['amount']))}"
        entry = T.LINE.format(name=_name(o) + tag + extra, district=district, roles=roles, label=o.bond_label, bond=o.bond)
        lines.append((f"{i}. " if numbered else "") + entry)
    if waiting:
        rows.insert(0, [Button(T.OFFERS_BUTTON.format(n=waiting), "offers")])
    rows.append([Button(T.BTN_CREW, "crew"), Button(T.BTN_MARKET, "market")])
    rows.append([Button(T.BTN_MERC, "merc"), Button(T.BTN_STASH, "stash")])
    rows.append([Button("Охрана", "guard")])  # owner request 25.10: players hired as bodyguards
    rows.append([Button(T.BTN_PVP_ON if off else T.BTN_PVP_OFF, "pvp")])
    rows.append([Button(T.BACK, "back")])
    return Screen("\n".join(lines), rows, name="plist")


@on_screen("plist")
async def plist_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await goto(ctx, "relations")
    if action == "offers":
        return await offers_screen(ctx)
    if action == "merc":
        return await merc_screen(ctx)
    if action == "guard":
        from app.bot.handlers.pguard import guard_screen

        return await guard_screen(ctx)
    if action == "crew":
        return await crew_screen(ctx)
    if action == "market":
        return await market_screen(ctx)
    if action == "stash":
        return await stash_screen(ctx)
    if action == "pvp":
        async with db.session_scope() as s:
            off = await svc.pvp_off(s, ctx.user_id)
            err = await svc.set_pvp_off(s, ctx.content, ctx.user_id, not off)
        return await list_screen(ctx, _pvp_block(ctx.content, err) if err else None)
    if action == "o" and arg and arg.isdigit():
        return await card_screen(ctx, int(arg))
    return await list_screen(ctx)


# -- one character -----------------------------------------------------------------------------------------------------


async def card_screen(ctx: Ctx, char_id: int, note: str | None = None) -> Screen:
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    cfg = c.balance.players
    async with db.session_scope() as s:
        o = await svc.other(s, c, ch, char_id, ctx.ui.settings.timezone)
    if o is None:
        return await list_screen(ctx, T.BLOCKED["gone"])
    district, roles = _who(c, o)
    lines = [
        T.CARD.format(name=_name(o), district=district, roles=roles, label=o.bond_label, bond=o.bond,
                      max=cfg.bond_max),
        T.CARD_STATUS.format(eddies=views.money(ch.state.eddies), ap=ch.state.ap),
        "",
        T.CARD_HELP.format(fee=cfg.transfer_fee_pct, mood=cfg.drink.mood),
    ]
    if note:
        lines += ["", note]

    def done(action: str) -> str:
        return T.DONE_TODAY if action in o.used_today else ""

    budgets = c.balance.ui.button_chars
    rows = pack([
        Button(T.BTN_GIFT, "gift"),
        Button(T.BTN_NOTE, "note"),
        Button(T.BTN_DRINK.format(ap=cfg.drink.ap) + done(pl.DRINK), "do", pl.DRINK),
        Button(T.BTN_VOUCH.format(ap=cfg.vouch.ap) + done(pl.VOUCH), "do", pl.VOUCH),
        Button(T.BTN_TOGETHER.format(ap=cfg.together.ap) + done(pl.TOGETHER), "together"),
        Button(T.BTN_QUEST, "quest"),
        Button(T.BTN_JOB, "job"),
    ], budgets, max_per_row=2)
    async with db.session_scope() as s:
        my_crew = await crews.crew_of(s, ctx.user_id)
        their_crew = await crews.crew_of(s, o.tg_id)
    if my_crew is not None and their_crew is None:
        rows.append([Button(T.BTN_INVITE, "offer", pl.CREW)])
    from app.services import bigmoney as bm_svc
    from app.services import pcorp as pcorp_svc

    async with db.session_scope() as s:
        my_corp = (await bm_svc.corps(s)).get(str(ctx.user_id))
    if my_corp is not None and str(o.tg_id) not in pcorp_svc.staff_of(my_corp):  # owner request 24.10
        rows.append([Button("Нанять в корпорацию", "corphire")])
    async with db.session_scope() as s:
        revenge = await svc.revenge_until(s, ctx.user_id, o.tg_id)
    pv = c.balance.players.pvp
    rows.append([Button((T.BTN_REVENGE if revenge else T.BTN_ATTACK).format(ap=pv.ap), "attack"),
                 Button("Награда за голову", "bounty")])
    from app.services import bounties

    async with db.session_scope() as s:
        pot = await bounties.on(s, char_id)
    if pot:
        lines.insert(1, f"🎯 За голову назначено: {views.money(pot)}. Победа в стычке забирает всё.")
    rows += pack([
        Button(T.BTN_HOOKUP, "offer", pl.HOOKUP),
        Button(T.BTN_DATE, "offer", pl.DATE),
        Button(T.BTN_DUEL, "duel"),
        Button(T.BTN_CHILD, "offer", pl.CHILD),  # owner request 25.10
    ], budgets, max_per_row=3)
    rows.append([Button(T.BACK, "list")])
    return Screen("\n".join(lines), rows, name="plcard", state={"id": char_id, "name": o.name})


def _block_text(content: Content, reason: str) -> str:
    pc = content.balance.players  # a text may name the daily cap or the quest limit (KeyError 23.10: {max})
    return T.BLOCKED.get(reason, reason).format(cap=views.money(pc.transfer_daily_cap), max=pc.quest.max_active)


async def _do(ctx: Ctx, char_id: int, action: str, *, amount: int = 0, text: str = "", kind: str = "",
              skill: str | None = None) -> Screen:
    c = ctx.content
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        res = await svc.act(s, c, ch, random.Random(), year, tz, char_id, action, amount=amount, text=text,
                            kind=kind, skill=skill)
    if res.no_ap:
        return await card_screen(ctx, char_id, T.NO_AP)
    if res.blocked is not None or res.target is None:
        if res.blocked == pl.BLOCK_GONE:
            return await list_screen(ctx, T.BLOCKED["gone"])
        return await card_screen(ctx, char_id, _block_text(c, res.blocked or "gone"))
    o = res.target
    g = svc.gender(ch)
    who = T.name(o.name)
    if action == pl.GIFT:
        line = (T.GIFT_DONE.format(name=who, net=views.money(res.net), fee=views.money(amount - res.net))
                if amount - res.net > 0 else f"{who} получит <b>{views.money(res.net)}</b> эдди. Свои, без комиссии.")
    elif action == pl.DRINK:
        line = T.DRINK_DONE.format(mood=c.balance.players.drink.mood)
    elif action == pl.VOUCH:
        line = T.VOUCH_DONE.format(name=who, a=T.a(g))
    elif action == pl.TOGETHER:
        line = T.TOGETHER_DONE[kind].format(skill=texts.SKILL_LABELS.get(skill or "", "").lower())
        if res.applied is not None:
            summary = views.effects_summary(res.applied, c)
            if summary:
                line += "\n" + summary
    else:
        line = T.NOTE_DONE.format(name=who)
    await _ping_target(ctx, o.tg_id, action)
    return await card_screen(ctx, char_id, line + "\n" + T.BOND_LINE.format(
        name=who, label=pl.bond_label(c, res.bond), bond=res.bond))


@on_screen("plcard")
async def plcard_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    char_id = int(ctx.state.get("id", 0) or 0)
    if action == "list" or not char_id:
        return await list_screen(ctx)
    if action == "gift":
        return await gift_screen(ctx, char_id)
    if action == "note":
        return note_screen(ctx, char_id, str(ctx.state.get("name", "")))
    if action == "duel":
        return duel_screen(ctx, char_id, str(ctx.state.get("name", "")))
    if action == "do" and arg in (pl.DRINK, pl.VOUCH):
        return await _do(ctx, char_id, arg)
    if action == "offer" and arg in (pl.HOOKUP, pl.DATE, pl.CREW, pl.CHILD):
        return await _offer(ctx, char_id, arg)
    if action == "together":
        return together_screen(ctx, char_id, str(ctx.state.get("name", "")))
    if action == "attack":
        return await attack_screen(ctx, char_id, str(ctx.state.get("name", "")))
    if action == "bounty":
        return bounty_screen(ctx, char_id, str(ctx.state.get("name", "")))
    if action == "job":
        return job_screen(ctx, char_id, str(ctx.state.get("name", "")))
    if action == "corphire":
        from app.bot.handlers.pcorp import post_screen

        return await post_screen(ctx, cid=char_id, back="card")
    if action == "quest":
        return quest_screen(ctx, char_id, str(ctx.state.get("name", "")))
    return await card_screen(ctx, char_id)


# -- bounties ------------------------------------------------------------------------------------------------------------


def bounty_screen(ctx: Ctx, char_id: int, name: str) -> Screen:
    from app.services import bounties

    rows = pack([Button(views.money(a), "put", str(a)) for a in bounties.AMOUNTS], ctx.content.balance.ui.button_chars,
                max_per_row=2)
    rows.append([Button(T.BACK, "back")])
    text = (f"Награда за голову: {texts.esc(name)}\n"
            "Деньги уходят фиксеру сразу. Кто победит эту цель в стычке, заберёт весь банк. "
            "Если цель умрёт раньше, деньги вернутся. О награде узнает весь город.")
    return Screen(text, rows, name="plbounty", state={"id": char_id, "name": name})


@on_screen("plbounty")
async def plbounty_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.services import bounties

    char_id = int(ctx.state.get("id", 0) or 0)
    if action != "put" or not arg or not arg.isdigit() or not char_id:
        return await card_screen(ctx, char_id) if char_id else await list_screen(ctx)
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        err = await bounties.place(s, c, ch, year, ctx.ui.settings.timezone, char_id, int(arg))
    notes = {bounties.BLOCK_SELF: "На себя награду не назначают.", bounties.BLOCK_GONE: T.BLOCKED["gone"],
             bounties.BLOCK_EDDIES: "Не хватает эдди."}
    return await card_screen(ctx, char_id, notes.get(err, err) if err else
                             f"Награда назначена: {views.money(int(arg))}. Город уже знает.")


# -- time together -----------------------------------------------------------------------------------------------------


def together_screen(ctx: Ctx, char_id: int, name: str) -> Screen:
    t = ctx.content.balance.players.together
    text = "\n".join([T.TOGETHER_TITLE.format(name=texts.esc(name)), T.TOGETHER_TEXT.format(
        ap=t.ap, train_cost=views.money(t.train.cost), train_xp=t.train.xp, walk_mood=t.walk.mood,
        ride_mood=t.ride.mood, ride_xp=t.ride.xp)])
    rows = [[Button(T.BTN_T_TRAIN, "k", pl.T_TRAIN), Button(T.BTN_T_WALK, "k", pl.T_WALK)],
            [Button(T.BTN_T_RIDE, "k", pl.T_RIDE)], [Button(T.BACK, "back")]]
    return Screen(text, rows, name="pltogether", state={"id": char_id, "name": name})


@on_screen("pltogether")
async def pltogether_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    char_id = int(ctx.state.get("id", 0) or 0)
    name = str(ctx.state.get("name", ""))
    if not char_id:
        return await list_screen(ctx)
    if action == "k" and arg == pl.T_TRAIN:
        buttons = [Button(texts.SKILL_LABELS[s], "s", s) for s in SKILLS]
        rows = pack(buttons, ctx.content.balance.ui.button_chars, max_per_row=2)
        rows.append([Button(T.BACK, "back")])
        return Screen(T.TOGETHER_SKILL.format(name=texts.esc(name)), rows, name="pltskill",
                      state={"id": char_id, "name": name})
    if action == "k" and arg in (pl.T_WALK, pl.T_RIDE):
        return await _do(ctx, char_id, pl.TOGETHER, kind=arg)
    return await card_screen(ctx, char_id)


@on_screen("pltskill")
async def pltskill_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    char_id = int(ctx.state.get("id", 0) or 0)
    if not char_id:
        return await list_screen(ctx)
    if action == "s" and arg in SKILLS:
        return await _do(ctx, char_id, pl.TOGETHER, kind=pl.T_TRAIN, skill=arg)
    return together_screen(ctx, char_id, str(ctx.state.get("name", "")))


# -- player quests -----------------------------------------------------------------------------------------------------


def quest_screen(ctx: Ctx, char_id: int, name: str) -> Screen:
    q = ctx.content.balance.players.quest
    lines = [T.QUEST_TITLE.format(name=texts.esc(name)), T.QUEST_TEXT.format(days=q.days), ""]
    lines += [T.QUEST_LINE.format(n=i, text=texts.esc(g.text)) for i, g in enumerate(q.goals, 1)]
    buttons = [Button(str(i), "g", g.key) for i, g in enumerate(q.goals, 1)]
    rows = pack(buttons, ctx.content.balance.ui.button_chars, max_per_row=5)
    rows.append([Button(T.BACK, "back")])
    return Screen("\n".join(lines), rows, name="plquest", state={"id": char_id, "name": name})


@on_screen("plquest")
async def plquest_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    char_id = int(ctx.state.get("id", 0) or 0)
    name = str(ctx.state.get("name", ""))
    if not char_id:
        return await list_screen(ctx)
    g = pl.quest_goal(ctx.content, arg or "") if action == "g" else None
    if g is None:
        return await card_screen(ctx, char_id)
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    q = ctx.content.balance.players.quest
    text = T.QUEST_REWARD_TITLE.format(name=texts.esc(name), text=texts.esc(g.text), eddies=views.money(ch.state.eddies))
    buttons = [Button(views.money(r), "r", str(r)) for r in q.rewards]
    rows = pack(buttons, ctx.content.balance.ui.button_chars, max_per_row=2)
    rows.append([Button(T.BACK, "back")])
    return Screen(text, rows, name="plqreward", state={"id": char_id, "name": name, "goal": g.key})


@on_screen("plqreward")
async def plqreward_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    char_id = int(ctx.state.get("id", 0) or 0)
    if not char_id:
        return await list_screen(ctx)
    if action == "r" and arg and arg.isdigit():
        return await _offer(ctx, char_id, pl.QUEST, stake=int(arg), goal=str(ctx.state.get("goal", "")))
    return quest_screen(ctx, char_id, str(ctx.state.get("name", "")))


# -- transfer ----------------------------------------------------------------------------------------------------------


async def gift_screen(ctx: Ctx, char_id: int, error: str | None = None) -> Screen:
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    cfg = c.balance.players
    async with db.session_scope() as s:
        o = await svc.other(s, c, ch, char_id, ctx.ui.settings.timezone)
        sent = await svc.sent_today(s, ctx.user_id, ctx.ui.settings.timezone)
        from app.services import crews

        same = o is not None and await crews.same_crew(s, ctx.user_id, o.tg_id)
    if o is None:
        return await list_screen(ctx, T.BLOCKED["gone"])
    left = max(0, cfg.transfer_daily_cap - sent["eddies"])
    head = T.GIFT_TITLE.format(name=texts.esc(o.name))
    if same:  # owner request 24.10: inside a crew money moves freely
        body = f"Свои из банды: без посредника, без комиссии и без лимита. На счету {views.money(ch.state.eddies)}."
    else:
        body = T.GIFT_TEXT.format(fee=cfg.transfer_fee_pct, left=views.money(left), eddies=views.money(ch.state.eddies))
    text = "\n".join([head, body, "", "Можно нажать сумму или «Своя сумма» и написать число в чат."])
    if error:
        text = error + "\n\n" + text
    buttons = [Button(T.GIFT_AMOUNT.format(amount=views.money(a)), "amt", str(a)) for a in cfg.transfer_amounts]
    rows = pack(buttons, c.balance.ui.button_chars, max_per_row=2)
    rows.append([Button("Своя сумма", "custom")])
    rows.append([Button(T.BACK, "back")])
    return Screen(text, rows, name="plgift", state={"id": char_id})


@on_text("pl_gift_amount")
async def gift_amount_text(ctx: Ctx, text: str) -> Screen | None:
    char_id = int(ctx.state.get("id", 0) or 0)
    if not char_id:
        return await list_screen(ctx)
    raw = text.replace(" ", "").replace("\u00a0", "").replace("_", "")
    if not raw.isdigit() or int(raw) <= 0:
        return await gift_screen(ctx, char_id, "Нужно целое число больше нуля, например 12500.")
    return await _do(ctx, char_id, pl.GIFT, amount=int(raw))


@on_screen("plgift")
async def plgift_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    char_id = int(ctx.state.get("id", 0) or 0)
    if not char_id:
        return await list_screen(ctx)
    if action == "amt" and arg and arg.isdigit():
        return await _do(ctx, char_id, pl.GIFT, amount=int(arg))
    if action == "custom":
        return Screen("Своя сумма\nНапиши сумму числом в чат, например 12500.",
                      [[Button(T.BACK, "back")]], awaiting_text="pl_gift_amount", name="plgift", state={"id": char_id})
    return await card_screen(ctx, char_id)


# -- note --------------------------------------------------------------------------------------------------------------


def note_screen(ctx: Ctx, char_id: int, name: str) -> Screen:
    text = T.NOTE_PROMPT.format(name=texts.esc(name), max=ctx.content.balance.players.note.max_len)
    return Screen(text, [[Button(T.CANCEL, "cancel")]], awaiting_text="pl_note", name="plnote",
                  state={"id": char_id, "name": name})


@on_screen("plnote")
async def plnote_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    char_id = int(ctx.state.get("id", 0) or 0)
    return await card_screen(ctx, char_id) if char_id else await list_screen(ctx)


@on_text("pl_note")
async def typed_note(ctx: Ctx, text: str) -> Screen | None:
    char_id = int(ctx.state.get("id", 0) or 0)
    if not char_id:
        return await list_screen(ctx)
    return await _do(ctx, char_id, pl.NOTE, text=text)


# -- offers ------------------------------------------------------------------------------------------------------------


def duel_screen(ctx: Ctx, char_id: int, name: str) -> Screen:
    cfg = ctx.content.balance.players.duel
    text = "\n".join([
        T.DUEL_TITLE.format(name=texts.esc(name)),
        T.DUEL_TEXT.format(stat=texts.STAT_LABELS.get(cfg.stat, cfg.stat),
                           skill=texts.SKILL_LABELS.get(cfg.skill, cfg.skill).lower(), dice=cfg.loser_hp_dice),
    ])
    buttons = [Button(T.DUEL_STAKE.format(stake=views.money(k)) if k else T.DUEL_NO_STAKE, "stake", str(k))
               for k in cfg.stakes]
    rows = pack(buttons, ctx.content.balance.ui.button_chars, max_per_row=2)
    rows.append([Button(T.BACK, "back")])
    return Screen(text, rows, name="plduel", state={"id": char_id, "name": name})


@on_screen("plduel")
async def plduel_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    char_id = int(ctx.state.get("id", 0) or 0)
    if not char_id:
        return await list_screen(ctx)
    if action == "stake" and arg and arg.isdigit():
        return await _offer(ctx, char_id, pl.DUEL, stake=int(arg))
    return await card_screen(ctx, char_id)


async def _offer(ctx: Ctx, char_id: int, offer: str, *, stake: int = 0, goal: str = "", approach: str = "") -> Screen:
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        res = await svc.make_offer(s, c, ch, year, ctx.ui.settings.timezone, char_id, offer, stake=stake, goal=goal,
                                   rng=random.Random(), approach=approach)
    if res.blocked is not None or res.target is None:
        if res.blocked == pl.BLOCK_GONE:
            return await list_screen(ctx, T.BLOCKED["gone"])
        return await card_screen(ctx, char_id, _block_text(c, res.blocked or "gone"))
    stake_txt = T.OFFER_STAKE.format(stake=views.money(stake)) if stake else ""
    if offer == pl.QUEST:
        g = pl.quest_goal(c, goal)
        line = T.QUEST_SENT.format(text=texts.esc(g.text if g else goal), reward=views.money(stake))
    else:
        line = T.OFFER_SENT[offer].format(name=T.name(res.target.name), a=T.a(svc.gender(ch)), stake=stake_txt)
    await _ping_target(ctx, res.target.tg_id, "offer")
    return await card_screen(ctx, char_id, line)


async def offers_screen(ctx: Ctx, note: str | None = None) -> Screen:
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    async with db.session_scope() as s:
        waiting = await svc.offers_for(s, ctx.content, ctx.user_id, year)
    lines = [T.OFFERS_TITLE]
    if note:
        lines += [note, ""]
    if not waiting:
        lines.append(T.NO_OFFERS)
        return Screen("\n".join(lines), [[Button(T.BACK, "list"), Button(texts.MENU, "menu")]], name="ploffers")
    m = waiting[0]
    p = m.payload or {}
    offer = str(p.get("offer"))
    stake = int(p.get("stake", 0) or 0)
    stake_txt = T.OFFER_DUEL_STAKE.format(stake=views.money(stake)) if stake else T.OFFER_DUEL_FREE
    if offer == "contract":  # owner request: a fixer's job with a plan and a split
        from app.engine import paths as pe

        job = pe.contract_job(ctx.content, str(p.get("job", "")))
        fixer_path = pe.path_def(ctx.content, "fixer")
        rank_n = int(p.get("rank", 1) or 1)
        rank_name = fixer_path.ranks[min(rank_n, len(fixer_path.ranks)) - 1].name if fixer_path else "?"
        share = int(p.get("share", 50) or 50)
        pool = job.pool if job else 0
        fcut, mine = pe.split(pool, share)
        approach = str(p.get("approach", "loud"))
        stat, skill = pl.JOB_APPROACHES.get(approach, ("REF", "firearms"))
        lines.append(T.OFFER_CONTRACT.format(
            name=texts.esc(m.from_name), rank=texts.esc(rank_name.lower()), job=texts.esc(job.name if job else "?"),
            text=texts.esc(job.text if job else ""), approach=T.JOB_APPROACHES.get(approach, "?"),
            check=f"{texts.STAT_LABELS.get(stat, stat)} + {texts.SKILL_LABELS.get(skill, skill).lower()}",
            dv=job.dv if job else 0, bonus=pe.plan_bonus(ctx.content, rank_n), pool=views.money(pool),
            fixer_pct=share, fixer=views.money(fcut), mine_pct=100 - share, mine=views.money(mine)))
        if len(waiting) > 1:
            lines += ["", T.OFFERS_LEFT.format(n=len(waiting) - 1)]
        rows = [[Button(T.BTN_CONTRACT_TAKE, "contract", str(m.id)), Button(T.BTN_DECLINE, "no", str(m.id))],
                [Button(T.BACK, "list"), Button(texts.MENU, "menu")]]
        return Screen("\n".join(lines), rows, name="ploffers")
    if offer == pl.JOB:
        job = pl.job_of(ctx.content, str(p.get("job", "")))
        lines.append(T.OFFER_TEXT["job"].format(name=texts.esc(m.from_name), job=texts.esc(job.name if job else "?"),
                                                pay=views.money(job.pay if job else 0),
                                                approach=T.JOB_APPROACHES.get(str(p.get("approach")), "?").lower()))
        lines.append(T.JOB_APPROACH_HELP)
        rows = pack([Button(T.JOB_APPROACHES[a], "job", f"{m.id}:{a}") for a in pl.JOB_APPROACHES],
                    ctx.content.balance.ui.button_chars, max_per_row=2)
        rows += [[Button(T.BTN_DECLINE, "no", str(m.id))], [Button(T.BACK, "list"), Button(texts.MENU, "menu")]]
        if len(waiting) > 1:
            lines += ["", T.OFFERS_LEFT.format(n=len(waiting) - 1)]
        return Screen("\n".join(lines), rows, name="ploffers")
    if offer == "dginv":  # owner request 24.10: an invitation to a player's game of the rich
        title = str(p.get("title") or "")
        # owner report 24.10: the card tells nothing; whoever calls finds out the rest
        number = texts.esc(title) if title else str(m.id * 7919 % 900000 + 100000)
        lines.append(f"🎭 Человек в идеальном костюме молча протягивает визитку: круг, треугольник, квадрат и номер "
                     f"<b>{number}</b>. На обороте от руки: «{views.money(int(p.get('advance', 0) or 0))} эдди — просто "
                     "за звонок». Он кланяется и растворяется в толпе.")
        if len(waiting) > 1:
            lines += ["", T.OFFERS_LEFT.format(n=len(waiting) - 1)]
        rows = [[Button("Позвонить", "dginv", f"{m.id}:1"), Button("Порвать", "dginv", f"{m.id}:0")], [Button(T.BACK, "list"), Button(texts.MENU, "menu")]]
        return Screen("\n".join(lines), rows, name="ploffers")
    if offer == "reinf":  # owner request 24.10: a call for help in a fight
        lines.append(f"📞 <b>{texts.esc(m.from_name)}</b> зовёт тебя на помощь: идёт бой, платит "
                     f"{views.money(int(p.get('price', 0) or 0))} эдди. Согласишься, пока бой идёт, — твой персонаж встанет "
                     "рядом, когда доедет. Твоё здоровье и патроны не тратятся.")
        if len(waiting) > 1:
            lines += ["", T.OFFERS_LEFT.format(n=len(waiting) - 1)]
        rows = [[Button("Еду", "reinf", f"{m.id}:1"), Button(T.BTN_DECLINE, "reinf", f"{m.id}:0")], [Button(T.BACK, "list"), Button(texts.MENU, "menu")]]
        return Screen("\n".join(lines), rows, name="ploffers")
    if offer == "guard":  # owner request 25.10: a post in a player's guard
        lines.append(f"🛡 <b>{texts.esc(m.from_name)}</b> зовёт тебя в личную охрану: "
                     f"{views.money(int(p.get('wage', 0) or 0))} эдди в месяц, письмом. Согласишься — твой персонаж встаёт "
                     "рядом в каждом бою нанимателя, если вы в одном городе и ты на свободе. Твоё здоровье и патроны не "
                     "тратятся. Уйти можно в любой момент: «Свои» → «Охрана».")
        if len(waiting) > 1:
            lines += ["", T.OFFERS_LEFT.format(n=len(waiting) - 1)]
        rows = [[Button(T.BTN_ACCEPT, "guard", f"{m.id}:1"), Button(T.BTN_DECLINE, "guard", f"{m.id}:0")],
                [Button(T.BACK, "list"), Button(texts.MENU, "menu")]]
        return Screen("\n".join(lines), rows, name="ploffers")
    if offer == "corp":  # owner request 24.10: a job in a player's megacorp
        lines.append(f"<b>{texts.esc(m.from_name)}</b> зовёт тебя в штат своей корпорации «{texts.esc(str(p.get('corp', '')))}»: "
                     f"{texts.esc(str(p.get('post', '')))}, {views.money(int(p.get('weekly', 0) or 0))} в неделю. "
                     "Это идёт вместе с основной работой: зарплата письмом раз в месяц, поручения корпорации в «Работе», "
                     "башня в Судную ночь.")
        if len(waiting) > 1:
            lines += ["", T.OFFERS_LEFT.format(n=len(waiting) - 1)]
        rows = [[Button(T.BTN_ACCEPT, "corp", str(m.id)), Button(T.BTN_DECLINE, "no", str(m.id))],
                [Button(T.BACK, "list"), Button(texts.MENU, "menu")]]
        return Screen("\n".join(lines), rows, name="ploffers")
    if offer == pl.CREW:
        lines.append(T.OFFER_TEXT["crew"].format(name=texts.esc(m.from_name), crew=texts.esc(str(p.get("crew_name", ""))),
                                                 stake=""))
    elif offer == pl.QUEST:
        g = pl.quest_goal(ctx.content, str(p.get("goal", "")))
        lines.append(T.OFFER_QUEST.format(name=texts.esc(m.from_name), text=texts.esc(g.text if g else "?"),
                                          reward=views.money(stake), days=ctx.content.balance.players.quest.days))
    else:
        lines.append(T.OFFER_TEXT.get(offer, offer).format(name=texts.esc(m.from_name), stake=stake_txt))
    if len(waiting) > 1:
        lines += ["", T.OFFERS_LEFT.format(n=len(waiting) - 1)]
    rows = [[Button(T.BTN_ACCEPT, "yes", str(m.id)), Button(T.BTN_DECLINE, "no", str(m.id))],
            [Button(T.BACK, "list"), Button(texts.MENU, "menu")]]
    return Screen("\n".join(lines), rows, name="ploffers")


@on_screen("ploffers")
async def ploffers_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":  # owner request 24.10: the offers open from the main menu too
        return await menu_screen(ctx)
    if action == "job" and arg and ":" in arg:
        return await _answer_job(ctx, arg)
    if action == "contract" and arg and arg.isdigit():
        return await _answer_contract(ctx, int(arg))
    if action == "dginv" and arg and ":" in arg:
        from app.services import dgame as dg_svc

        mid, _, yes = arg.partition(":")
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, ctx.content)
            line = await dg_svc.answer_invite(s, ctx.content, ch, random.Random(), year, ctx.ui.settings.timezone,
                                              int(mid), yes == "1")
        if yes == "1" and "За тобой приедут" in line:
            return await menu_screen(ctx, texts.esc(line))
        return await offers_screen(ctx, texts.esc(line))
    if action == "guard" and arg and ":" in arg:  # owner request 25.10: a post in a player's guard
        from app.services import pguard as pg_svc

        mid, _, yes = arg.partition(":")
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, ctx.content)
            line = await pg_svc.answer(s, ctx.content, ch, year, int(mid), yes == "1")
            from app.db.models import PlayerMail

            m = await s.get(PlayerMail, int(mid))
            from_tg = m.from_tg_id if m is not None else 0
        if from_tg:
            await _ping_target(ctx, from_tg, "reply")
        return await offers_screen(ctx, texts.esc(line))
    if action == "reinf" and arg and ":" in arg:
        from app.services import reinforce as rf_svc

        mid, _, yes = arg.partition(":")
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            line = await rf_svc.answer(s, ch, int(mid), yes == "1")
        return await offers_screen(ctx, texts.esc(line))
    if action == "corp" and arg and arg.isdigit():
        from app.bot.handlers.pcorp import BLOCKED as PC_BLOCKED
        from app.services import pcorp as pcorp_svc

        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, ctx.content)
            r = await pcorp_svc.answer(s, ctx.content, ch, year, ctx.ui.settings.timezone, int(arg))
        if not r.blocked:
            from app.db.models import PlayerMail

            async with db.session_scope() as s:
                m = await s.get(PlayerMail, int(arg))
                from_tg = m.from_tg_id if m is not None else 0
            if from_tg:
                await _ping_target(ctx, from_tg, "reply")
        return await offers_screen(ctx, PC_BLOCKED.get(r.blocked, r.blocked) if r.blocked else texts.esc(r.text))
    if action not in ("yes", "no") or not arg or not arg.isdigit():
        return await list_screen(ctx)
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        res = await svc.answer(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, int(arg), action == "yes")
    if res is None:
        return await offers_screen(ctx, T.STALE)
    if res.blocked is not None:
        return await offers_screen(ctx, T.CREW_BLOCKED.get(res.blocked) if res.blocked in ("full",) else
                                   _block_text(c, res.blocked))
    who = T.name(res.from_name)
    if not res.accepted:
        line = T.DECLINED.format(a=T.a(svc.gender(ch)), name=who)
    elif res.offer == pl.CREW:
        async with db.session_scope() as s:
            crew = await crews.crew_of(s, ctx.user_id)
        line = T.CREW_JOINED.format(crew=texts.esc(crew.name if crew else ""))
        if crew is not None:
            ctx.ui.post(T.POST_JOINED.format(who=texts.esc(ch.state.name), crew=texts.esc(crew.name)), 6 * 3600)
    elif res.offer == pl.QUEST:
        goal = pl.quest_goal(c, res.goal)
        line = T.QUEST_TAKEN.format(text=texts.esc(goal.text if goal else "?"), days=c.balance.players.quest.days)
    elif res.roll is not None:
        r = res.roll
        line = (T.DUEL_WIN if r.won else T.DUEL_LOSE).format(name=who, mine=r.mine, theirs=r.theirs)
    else:
        line = T.ANSWER_DONE.get(res.offer, "").format(child=texts.esc(res.goal))
    if res.applied is not None:
        summary = views.effects_summary(res.applied, c)
        if summary:
            line += "\n" + summary
    await _ping_target(ctx, res.from_tg, "reply")
    return await offers_screen(ctx, line)


# -- the merc exchange ------------------------------------------------------------------------------------------------


async def merc_screen(ctx: Ctx) -> Screen:
    c = ctx.content
    async with db.session_scope() as s:
        rec = await svc.merc_get(s, c, ctx.user_id)
    lines = [T.MERC_TITLE, T.MERC_TEXT, "",
             T.MERC_NOW_ON.format(share=rec["share"]) if rec["on"] else T.MERC_NOW_OFF]
    shares = [Button((T.BTN_SHARE_ON if k == rec["share"] else T.BTN_SHARE).format(share=k), "share", str(k))
              for k in c.balance.players.hire.shares]
    rows = [[Button(T.BTN_MERC_OFF, "off")] if rec["on"] else [Button(T.BTN_MERC_ON, "on")]]
    rows += pack(shares, c.balance.ui.button_chars, max_per_row=4)
    rows.append([Button(T.BACK, "back")])
    return Screen("\n".join(lines), rows, name="plmerc")


@on_screen("plmerc")
async def plmerc_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    if action in ("on", "off"):
        async with db.session_scope() as s:
            await svc.merc_set(s, c, ctx.user_id, on=action == "on")
        return await merc_screen(ctx)
    if action == "share" and arg and arg.isdigit():
        async with db.session_scope() as s:
            await svc.merc_set(s, c, ctx.user_id, share=int(arg))
        return await merc_screen(ctx)
    return await list_screen(ctx)


# -- joint deeds ---------------------------------------------------------------------------------------------------------


def job_screen(ctx: Ctx, char_id: int, name: str) -> Screen:
    cfg = ctx.content.balance.players.joint
    lines = [T.JOB_TITLE.format(name=texts.esc(name)), T.JOB_TEXT.format(syn=cfg.synergy), ""]
    lines += [T.JOB_LINE.format(n=i, name=texts.esc(j.name), pay=views.money(j.pay), dv=j.dv, text=texts.esc(j.text))
              for i, j in enumerate(cfg.jobs, 1)]
    rows = pack([Button(str(i), "j", j.key) for i, j in enumerate(cfg.jobs, 1)], ctx.content.balance.ui.button_chars,
                max_per_row=5)
    rows.append([Button(T.BACK, "back")])
    return Screen("\n".join(lines), rows, name="pljob", state={"id": char_id, "name": name})


@on_screen("pljob")
async def pljob_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    char_id = int(ctx.state.get("id", 0) or 0)
    name = str(ctx.state.get("name", ""))
    if not char_id:
        return await list_screen(ctx)
    if action == "j" and arg and pl.job_of(ctx.content, arg) is not None:
        job = pl.job_of(ctx.content, arg)
        rows = pack([Button(T.JOB_APPROACHES[a], "a", f"{arg}:{a}") for a in pl.JOB_APPROACHES],
                    ctx.content.balance.ui.button_chars, max_per_row=2)
        rows.append([Button(T.BACK, "back")])
        text = T.JOB_APPROACH_TITLE.format(job=texts.esc(job.name)) + "\n" + T.JOB_APPROACH_HELP
        return Screen(text, rows, name="pljob", state={"id": char_id, "name": name})
    if action == "a" and arg and ":" in arg:
        key, approach = arg.split(":", 1)
        return await _offer(ctx, char_id, pl.JOB, goal=key, approach=approach)
    return await card_screen(ctx, char_id)


async def _answer_job(ctx: Ctx, arg: str) -> Screen:
    mail_id, approach = arg.split(":", 1)
    if not mail_id.isdigit():
        return await offers_screen(ctx)
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        res = await svc.answer_job(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, int(mail_id), approach)
    if res is None:
        return await offers_screen(ctx, T.STALE)
    if res.blocked is not None or res.roll is None:
        return await offers_screen(ctx, T.BLOCKED["gone"])
    r = res.roll
    job = pl.job_of(c, res.job)
    line = T.JOB_RESULT[r.outcome] + "\n" + T.JOB_SCORES.format(
        mine=r.mine.total, dv=job.dv if job else 0, theirs=r.theirs.total,
        syn=T.JOB_SYN.format(n=c.balance.players.joint.synergy) if r.synergy else "")
    if res.applied is not None:
        summary = views.effects_summary(res.applied, c)
        if summary:
            line += "\n" + summary
    await _ping_target(ctx, res.from_tg, "reply")
    return await offers_screen(ctx, line)


async def _answer_contract(ctx: Ctx, mail_id: int) -> Screen:
    from app.engine import paths as pe
    from app.services import paths as paths_svc

    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        res = await paths_svc.answer_contract(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, mail_id)
    if res is None:
        return await offers_screen(ctx, T.STALE)
    if res.blocked is not None or res.roll is None:
        return await offers_screen(ctx, _block_text(c, res.blocked or "gone"))
    job = pe.contract_job(c, res.job)
    r = res.roll
    line = (T.CONTRACT_DONE.format(mine=views.money(res.cut)) if r.ok else T.CONTRACT_FAIL) + "\n" + \
        T.CONTRACT_SCORE.format(total=r.check.total, dv=job.dv if job else 0, bonus=r.bonus)  # type: ignore[attr-defined]
    if res.applied is not None:
        summary = views.effects_summary(res.applied, c)
        if summary:
            line += "\n" + summary
    await _ping_target(ctx, res.from_tg, "reply")
    return await offers_screen(ctx, line)


# -- stashes -------------------------------------------------------------------------------------------------------------


def _years_word(n: int) -> str:
    from app.bot.effects_text import plural

    return plural(n, ("год", "года", "лет"))


async def stash_screen(ctx: Ctx, note: str | None = None) -> Screen:
    from app.services import echoes

    c = ctx.content
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    async with db.session_scope() as s:
        mine = await echoes.my_stashes(s, ctx.user_id)
    lines = [T.STASH_TITLE, T.STASH_TEXT.format(district=views.district_name(c, ch.state.district))]
    if note:
        lines += ["", note]
    if mine:
        lines += ["", T.STASH_MINE]
        for st in mine:
            left = max(0, st.open_year - year)
            lines.append(T.STASH_LINE.format(district=views.district_name(c, st.district), eddies=views.money(st.eddies),
                                             left=left, years=_years_word(left),
                                             found=T.STASH_FOUND_MARK if st.status == "found" else ""))
    lines += ["", T.STASH_PICK_AMOUNT.format(eddies=views.money(ch.state.eddies))]
    rows = pack([Button(views.money(a), "amt", str(a)) for a in c.balance.players.echo.stash_amounts],
                c.balance.ui.button_chars, max_per_row=3)
    rows.append([Button("Своя сумма", "ask")])  # owner request 24.10
    rows.append([Button(T.BACK, "back")])
    return Screen("\n".join(lines), rows, name="plstash")


def _stash_years(content: Content, amount: int) -> Screen:
    cfg = content.balance.players.echo
    rows = pack([Button(T.BTN_YEARS.format(n=n, years=_years_word(n)), "yrs", f"{amount}:{n}") for n in cfg.stash_years],
                content.balance.ui.button_chars, max_per_row=2)
    rows.append([Button(T.BACK, "stash")])
    return Screen(T.STASH_PICK_YEARS.format(eddies=views.money(amount)), rows, name="plstash")


@on_text("stash_amount")
async def stash_amount_text(ctx: Ctx, text: str) -> Screen | None:
    raw = "".join(ch for ch in text if ch.isdigit())
    if not raw or int(raw) <= 0:
        return Screen("Нужна сумма числом, например 12500.", [[Button(T.BACK, "stash")]], name="plstash",
                      awaiting_text="stash_amount")
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    amount = int(raw)
    if amount > ch.state.eddies:
        return await stash_screen(ctx, f"На счету только {views.money(ch.state.eddies)} эдди.")
    return _stash_years(ctx.content, amount)


@on_screen("plstash")
async def plstash_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.services import echoes

    c = ctx.content
    cfg = c.balance.players.echo
    if action == "amt" and arg and arg.isdigit():
        return _stash_years(c, int(arg))
    if action == "ask":
        return Screen(f"{T.STASH_TITLE}\nНапиши в чат сумму, которую спрятать. Любую, лишь бы хватало на счету.",
                      [[Button(T.BACK, "stash")]], name="plstash", awaiting_text="stash_amount")
    if action == "yrs" and arg and ":" in arg:
        amount, years = (int(x) for x in arg.split(":", 1))
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            r = await echoes.hide(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, amount, years)
        if r.blocked is not None or r.stash is None:
            return await stash_screen(ctx, T.STASH_BLOCKED.get(r.blocked or "gone", "").format(max=cfg.stash_max))
        return await stash_screen(ctx, T.STASH_HIDDEN.format(eddies=views.money(amount), n=years, years=_years_word(years),
                                                             district=views.district_name(c, r.stash.district)))
    if action == "stash":
        return await stash_screen(ctx)
    return await list_screen(ctx)


# -- pvp ---------------------------------------------------------------------------------------------------------------


def _pvp_block(content: Content, reason: str) -> str:
    cfg = content.balance.players.pvp
    if reason in T.PVP_BLOCKED:
        return T.PVP_BLOCKED[reason].format(weeks=cfg.newbie_weeks, days=cfg.cooldown_days)
    return _block_text(content, reason)


async def attack_screen(ctx: Ctx, char_id: int, name: str) -> Screen:
    cfg = ctx.content.balance.players.pvp
    me, _ = await _load(ctx)
    if me is None:
        return await menu_screen(ctx)
    async with db.session_scope() as s:
        o = await svc.other(s, ctx.content, me, char_id, ctx.ui.settings.timezone)
        revenge = await svc.revenge_until(s, ctx.user_id, o.tg_id) if o is not None else None
    if o is None:
        return await list_screen(ctx, T.BLOCKED["gone"])
    lines = [T.ATTACK_TITLE.format(name=texts.esc(name)),
             T.ATTACK_TEXT.format(def_=cfg.defense_bonus, pct=cfg.rob_pct, cap=views.money(cfg.rob_cap),
                                  dice=cfg.beat_dice, days=cfg.revenge_days)]
    if revenge:
        lines.append(T.ATTACK_REVENGE)
    rows = [[Button(T.BTN_ROB, "mode", pl.ROB), Button(T.BTN_BEAT, "mode", pl.BEAT)], [Button(T.BACK, "back")]]
    return Screen("\n".join(lines), rows, name="plattack", state={"id": char_id, "name": name})


@on_screen("plattack")
async def plattack_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    char_id = int(ctx.state.get("id", 0) or 0)
    if not char_id:
        return await list_screen(ctx)
    if action != "mode" or arg not in pl.PVP_MODES:
        return await card_screen(ctx, char_id)
    c = ctx.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        res = await svc.attack(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, char_id, arg)
    if res.no_ap:
        return await card_screen(ctx, char_id, T.NO_AP)
    if res.blocked is not None or res.roll is None or res.target is None:
        return await card_screen(ctx, char_id, _pvp_block(c, res.blocked or "gone"))
    r = res.roll
    line = (T.ATTACK_WIN if r.won else T.ATTACK_LOSE).format(mine=r.mine, theirs=r.theirs)
    if r.won:
        line += T.ATTACK_ROB_DONE.format(steal=views.money(r.steal)) if arg == pl.ROB else T.ATTACK_BEAT_DONE
    if res.applied is not None:
        summary = views.effects_summary(res.applied, c)
        if summary:
            line += "\n" + summary
    if res.bounty:
        line += f"\nНаграда за голову твоя: +{views.money(res.bounty)}."
    if res.relic:  # owner request 25.10
        line += (f"\n💎 Реликвия «{texts.esc(res.relic[1:])}» теперь твоя." if res.relic.startswith("+")
                 else f"\n💎 Реликвия «{texts.esc(res.relic[1:])}» ушла к противнику.")
    result = T.POST_RESULT[arg if r.won else "fail"]
    ctx.ui.post(T.POST_ATTACK.format(who=texts.esc(ch.state.name), target=texts.esc(res.target.name.split()[0]),
                                     result=result), 6 * 3600)
    await _ping_target(ctx, res.target.tg_id, "attack")
    return await card_screen(ctx, char_id, line)


# -- the market ----------------------------------------------------------------------------------------------------------


def _item_name(content: Content, kind: str, key: str) -> str:
    item = market.item_of(content, kind, key)
    return item.name if item else key


async def market_screen(ctx: Ctx, note: str | None = None) -> Screen:
    c = ctx.content
    async with db.session_scope() as s:
        lots = await market.open_lots(s)
    lines = [T.MARKET_TITLE, T.MARKET_INTRO.format(fee=c.balance.players.market.fee_pct)]
    if note:
        lines += ["", note]
    lines.append("")
    for i, lot in enumerate(lots, 1):
        lines.append(T.MARKET_LINE.format(n=i, item=texts.esc(_item_name(c, lot.kind, lot.item_key)),
                                          price=views.money(lot.price), seller=texts.esc(lot.seller_name),
                                          mine=T.MARKET_MINE if lot.seller_tg == ctx.user_id else ""))
    if not lots:
        lines.append(T.MARKET_EMPTY)
    rows = pack([Button(str(i), "lot", str(lot.id)) for i, lot in enumerate(lots, 1)], c.balance.ui.button_chars,
                max_per_row=5)
    rows.append([Button(T.BTN_SELL, "sell")])
    rows.append([Button(T.BACK, "back")])
    return Screen("\n".join(lines), rows, name="plmarket")


def _market_block(content: Content, reason: str) -> str:
    return T.MARKET_BLOCKED.get(reason, reason).format(max=content.balance.players.market.max_listings)


@on_screen("plmarket")
async def plmarket_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    tz = ctx.ui.settings.timezone
    if action == "back":
        return await list_screen(ctx)
    if action == "lot" and arg and arg.isdigit():
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            from app.db.models import Listing

            lot = await s.get(Listing, int(arg))
            own = lot is not None and lot.seller_tg == ctx.user_id
            fn = market.withdraw if own else market.buy
            r = await fn(s, c, ch, random.Random(), year, tz, int(arg))
        if r.blocked is not None or r.lot is None:
            return await market_screen(ctx, _market_block(c, r.blocked or "gone"))
        name = texts.esc(_item_name(c, r.lot.kind, r.lot.item_key))
        if own:
            return await market_screen(ctx, T.MARKET_WITHDRAWN.format(item=name))
        await _ping_target(ctx, r.lot.seller_tg, "sold")
        return await market_screen(ctx, T.MARKET_BOUGHT.format(item=name, price=views.money(r.lot.price)))
    if action == "sell":
        ch, _ = await _load(ctx)
        if ch is None:
            return await menu_screen(ctx)
        items = market.sellable(c, ch)
        lines = [T.SELL_TITLE]
        if not items:
            lines.append(T.SELL_NONE)
        for n, (i, kind, key) in enumerate(items, 1):
            item = market.item_of(c, kind, key)
            lines.append(T.SELL_LINE.format(n=n, item=texts.esc(item.name), price=views.money(item.price),
                                            eq=T.SELL_EQ if ch.state.inventory[i].equipped else ""))
        rows = pack([Button(str(n), "item", f"{i}:{key}") for n, (i, _, key) in enumerate(items, 1)],
                    c.balance.ui.button_chars, max_per_row=5)
        rows.append([Button(T.BACK, "market")])
        return Screen("\n".join(lines), rows, name="plmarket")
    if action == "item" and arg and ":" in arg:
        idx, key = arg.split(":", 1)
        ch, _ = await _load(ctx)
        if ch is None:
            return await menu_screen(ctx)
        i = int(idx)
        if not 0 <= i < len(ch.state.inventory) or ch.state.inventory[i].key != key:
            return await market_screen(ctx, _market_block(c, "gone"))
        item = market.item_of(c, ch.state.inventory[i].kind, key)
        if item is None:
            return await market_screen(ctx, _market_block(c, "gone"))
        rows = pack([Button(T.PRICE_BTN.format(pct=p, price=views.money(item.price * p // 100)), "price", f"{i}:{key}:{p}")
                     for p in c.balance.players.market.price_pcts], c.balance.ui.button_chars, max_per_row=2)
        rows.append([Button(T.BACK, "market")])
        return Screen(T.PRICE_TITLE.format(item=texts.esc(item.name), price=views.money(item.price)), rows, name="plmarket")
    if action == "price" and arg and arg.count(":") == 2:
        idx, key, pct = arg.split(":")
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            i = int(idx)
            if not 0 <= i < len(ch.state.inventory) or ch.state.inventory[i].key != key:
                return await market_screen(ctx, _market_block(c, "gone"))
            r = await market.list_item(s, c, ch, tz, i, int(pct))
        if r.blocked is not None or r.lot is None:
            return await market_screen(ctx, _market_block(c, r.blocked or "gone"))
        return await market_screen(ctx, T.MARKET_LISTED.format(item=texts.esc(_item_name(c, r.lot.kind, r.lot.item_key)),
                                                               price=views.money(r.lot.price)))
    return await market_screen(ctx)


# -- crews -------------------------------------------------------------------------------------------------------------


async def crew_screen(ctx: Ctx, note: str | None = None) -> Screen:
    c = ctx.content
    cfg = c.balance.players.crew
    async with db.session_scope() as s:
        crew = await crews.crew_of(s, ctx.user_id)
        if crew is None:
            listing = [(cr, len(await crews.members(s, cr.id))) for cr in await crews.all_crews(s)]
        else:
            tgs = await crews.members(s, crew.id)
            names = {tg: await repo.display_name(s, tg) for tg in tgs}
    if crew is None:
        lines = [T.CREW_TITLE, T.CREW_INTRO.format(raid=c.balance.players.crew.raid_bonus), ""]
        if note:
            lines += [note, ""]
        lines.append(T.CREW_LIST_HEAD)
        lines += [T.CREW_LIST_LINE.format(name=texts.esc(cr.name), n=n, raids=int((cr.data or {}).get("raids_won", 0)))
                  for cr, n in listing] or [T.CREW_LIST_EMPTY]
        rows = [[Button(T.BTN_FOUND.format(cost=views.money(cfg.found_cost)), "found")], [Button(T.BACK, "back")]]
        return Screen("\n".join(lines), rows, name="plcrew")
    leader = crew.leader_tg == ctx.user_id
    lines = [T.CREW_HEAD.format(name=texts.esc(crew.name))]
    if note:
        lines += [note, ""]
    lines += [
        T.CREW_LEADER.format(name=texts.esc(names.get(crew.leader_tg, "?"))),
        T.CREW_BANK.format(bank=views.money(crew.bank)),
        T.CREW_RAIDS.format(n=int((crew.data or {}).get("raids_won", 0))),
        "", T.CREW_MEMBERS.format(n=len(tgs), max=cfg.max_members),
    ]
    lines += [T.CREW_MEMBER.format(name=texts.esc(names[tg]), leader=T.CREW_MEMBER_LEADER if tg == crew.leader_tg else "")
              for tg in tgs]
    biz = crews.crew_biz(crew)
    if biz:
        from app.services import business as biz_svc

        kind = biz_svc.kind_of(c, biz["kind"])
        if kind:
            lvl = int(biz["level"])
            day = (crew.data or {}).get("biz_day") or {}
            lines.append(f"\nБизнес банды: <b>{texts.esc(kind.name)}</b>, уровень {lvl}. "
                         f"Каждый час в общак идёт ~{views.money(crews.biz_hourly(c, biz))}"
                         + (f", за сегодня: {views.money(int(day.get('sum', 0)))}." if day else "."))
    log = list((crew.data or {}).get("log") or [])[-5:]
    if log:
        lines += ["", T.CREW_LOG] + [f"• {texts.esc(x)}" for x in log]
    h = crews.heist_of(crew)
    if h:
        tier = next((t for t in c.balance.players.crew.heists if t.key == h["tier"]), None)
        got = sum(int(v) for v in h["joined"].values())
        lines.append(f"\n💥 Идёт налёт: <b>{texts.esc(tier.name if tier else '?')}</b>, очки {got}/{crews.heist_need(tier, len(tgs)) if tier else '?'}, "
                     f"вступили {len(h['joined'])}/{len(tgs)}. Итог — когда вступят все или через сутки.")
    rows = [[Button(T.BTN_DEPOSIT, "dep")]]
    rows.append([Button("Налёт банды", "heist")])
    if len(tgs) < cfg.max_members:  # owner request 24.10: recruit from the crew screen, not only from a card
        rows.append([Button("Позвать в банду", "recruit")])
    if leader:
        rows[0].append(Button(T.BTN_PAYOUT, "pay"))
        rows.append([Button(T.BTN_KICK, "kick"), Button("Бизнес банды", "biz")])
    rows.append([Button(T.BTN_LEAVE, "leave")])
    rows.append([Button(T.BACK, "back")])
    return Screen("\n".join(lines), rows, name="plcrew")


def _crew_block(content: Content, reason: str) -> str:
    cfg = content.balance.players.crew
    return T.CREW_BLOCKED.get(reason, reason).format(min=cfg.name_min, max=cfg.name_max)


async def _member_pick(ctx: Ctx, what: str, action: str) -> Screen:
    async with db.session_scope() as s:
        crew = await crews.crew_of(s, ctx.user_id)
        tgs = [tg for tg in (await crews.members(s, crew.id) if crew else []) if tg != ctx.user_id]
        names = [await repo.display_name(s, tg) for tg in tgs]
    numbered, rows = list_rows(names, action, [str(tg) for tg in tgs], ctx.content.balance.ui.button_chars)
    lines = [T.PICK_MEMBER.format(what=what)]
    if numbered:
        lines += [f"{i}. {texts.esc(n)}" for i, n in enumerate(names, 1)]
    rows.append([Button(T.BACK, "crew")])
    return Screen("\n".join(lines), rows, name="plcrewpick")


@on_screen("plcrew")
async def plcrew_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    cfg = c.balance.players.crew
    if action == "back":
        return await list_screen(ctx)
    if action == "found":
        text = T.FOUND_PROMPT.format(min=cfg.name_min, max=cfg.name_max, cost=views.money(cfg.found_cost))
        return Screen(text, [[Button(T.CANCEL, "crew")]], awaiting_text="crew_name", name="plcrewpick")
    if action == "dep":
        ch, _ = await _load(ctx)
        if ch is None:
            return await menu_screen(ctx)
        async with db.session_scope() as s:
            crew = await crews.crew_of(s, ctx.user_id)
        if crew is None:
            return await crew_screen(ctx)
        buttons = [Button(views.money(a), "dep", str(a)) for a in cfg.deposits]
        rows = pack(buttons, c.balance.ui.button_chars, max_per_row=2)
        rows.append([Button("Своя сумма", "depc")])  # owner request 24.10: any amount
        rows.append([Button(T.BACK, "crew")])
        return Screen(T.DEPOSIT_TITLE.format(name=texts.esc(crew.name), eddies=views.money(ch.state.eddies)), rows,
                      name="plcrewpick")
    if action == "pay":
        return await _member_pick(ctx, T.WHAT_PAYOUT, "payto")
    if action == "biz":
        return await crew_biz_screen(ctx)
    if action == "heist":
        return await heist_screen(ctx)
    if action == "recruit":
        return await _recruit_screen(ctx)
    if action == "kick":
        return await _member_pick(ctx, T.WHAT_KICK, "kickto")
    if action == "leave":
        async with db.session_scope() as s:
            crew = await crews.crew_of(s, ctx.user_id)
        if crew is None:
            return await crew_screen(ctx)
        return Screen(T.LEAVE_CONFIRM.format(name=texts.esc(crew.name)),
                      [[Button(T.BTN_LEAVE_YES, "leave_yes")], [Button(T.BACK, "crew")]], name="plcrewpick")
    return await crew_screen(ctx)


async def heist_screen(ctx: Ctx, note: str | None = None) -> Screen:
    c = ctx.content
    cfg = c.balance.players.crew
    async with db.session_scope() as s:
        crew = await crews.crew_of(s, ctx.user_id)
        tgs = await crews.members(s, crew.id) if crew else []
    if crew is None:
        return await crew_screen(ctx)
    h = crews.heist_of(crew)
    mult = crews.heist_mult(c, crew)
    lines = [f"<b>Налёт банды «{texts.esc(crew.name)}»</b>",
             f"Раз в неделю лидер объявляет налёт. У банды сутки: каждый участник тратит 1 ОД и бросает свою проверку "
             f"(сложность {cfg.heist_dv}): успех — 1 очко, критический — 2, провал — минус здоровье. Набрали нужное — "
             f"общак и доля каждому участнику. Бонус за выигранные рейды: +{round((mult - 1) * 100)}%.", ""]
    if note:
        lines += [note, ""]
    rows: list[list[Button]] = []
    if h:
        tier = next((t for t in cfg.heists if t.key == h["tier"]), None)
        got = sum(int(v) for v in h["joined"].values())
        lines.append(f"Цель: <b>{texts.esc(tier.name if tier else '?')}</b>. Очки {got}/{crews.heist_need(tier, len(tgs)) if tier else '?'}, "
                     f"вступили {len(h['joined'])}/{len(tgs)}.")
        if str(ctx.user_id) not in h["joined"]:
            rows += [[Button(label, "hjoin", key)] for key, (_, _, label) in crews.HEIST_APPROACHES.items()]
        else:
            lines.append("Ты уже в деле. Ждём остальных.")
    elif crew.leader_tg == ctx.user_id:
        for t in cfg.heists:
            lines.append(f"<b>{texts.esc(t.name)}</b>: очков нужно {crews.heist_need(t, len(tgs))}, общак до +{views.money(int(t.bank * mult))}, "
                         f"каждому {views.money(int(t.share * mult))}")
            rows.append([Button(t.name, "hstart", t.key)])
    else:
        lines.append("Налёта сейчас нет. Объявить его может лидер.")
    rows.append([Button(T.BACK, "crew")])
    return Screen("\n".join(lines), rows, name="plcrewpick")


async def crew_biz_screen(ctx: Ctx, note: str | None = None) -> Screen:
    from app.services import business as biz_svc

    c = ctx.content
    async with db.session_scope() as s:
        crew = await crews.crew_of(s, ctx.user_id)
    if crew is None:
        return await crew_screen(ctx)
    b = crews.crew_biz(crew)
    lines = [f"<b>Бизнес банды «{texts.esc(crew.name)}»</b>", f"Общак: {views.money(crew.bank)}. Покупает и расширяет "
             "только лидер, за деньги общака. Каждый час в общак приходит доля дохода: за сутки — две недели работы.", ""]
    if note:
        lines += [note, ""]
    rows: list[list[Button]] = []
    if b:
        kind = biz_svc.kind_of(c, b["kind"])
        lvl = int(b["level"])
        lines.append(f"Сейчас: {texts.esc(kind.name) if kind else '?'}, уровень {lvl}/{c.balance.players.business.max_level}.")
        if kind and lvl < c.balance.players.business.max_level:
            rows.append([Button(f"Расширить ({views.money(biz_svc.upgrade_cost(c, kind, lvl))})", "cbuy", kind.key)])
    else:
        for k in c.balance.players.business.kinds:
            lines.append(f"<b>{texts.esc(k.name)}</b> · {views.money(k.price)} · ~{views.money(crews.biz_hourly(c, {'kind': k.key, 'level': 1}) * 24)} в сутки")
            rows.append([Button(k.name, "cbuy", k.key)])
    rows.append([Button(T.BACK, "crew")])
    return Screen("\n".join(lines), rows, name="plcrewpick")


def _typed_amount(text: str) -> int | None:
    raw = text.replace(" ", "").replace("\u00a0", "").replace("_", "")
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


@on_text("crew_dep_amount")
async def crew_dep_amount(ctx: Ctx, text: str) -> Screen | None:
    amount = _typed_amount(text)
    if amount is None:
        return await crew_screen(ctx, "Нужно целое число больше нуля.")
    return await plcrewpick_handler(ctx, "dep", str(amount))


@on_text("crew_pay_amount")
async def crew_pay_amount(ctx: Ctx, text: str) -> Screen | None:
    amount = _typed_amount(text)
    tg = int(ctx.state.get("tg", 0) or 0)
    if amount is None or not tg:
        return await crew_screen(ctx, "Нужно целое число больше нуля.")
    return await plcrewpick_handler(ctx, "payamt", f"{tg}:{amount}")


async def _recruit_screen(ctx: Ctx, note: str | None = None) -> Screen:
    """Owner request 24.10: the living players without a crew; a button sends the crew invite."""
    async with db.session_scope() as s:
        people: list[tuple[int, str]] = []
        for row in await repo.living_characters(s):
            if row.player_tg_id == ctx.user_id or (row.flags or {}).get("ai_mode"):
                continue
            if await crews.crew_of(s, row.player_tg_id) is None:
                people.append((row.id, f"{row.name} {row.surname or ''}".strip()))
    lines = ["Позвать в банду", "Приглашение уходит игроку; он примет или откажет в «Свои» → предложения.", ""]
    if note:
        lines += [note, ""]
    lines += [f"{i}. {texts.esc(n)}" for i, (_, n) in enumerate(people, 1)] or ["Все живые уже в бандах."]
    rows = pack([Button(str(i), "recruit", str(cid)) for i, (cid, _) in enumerate(people, 1)],
                ctx.content.balance.ui.button_chars, max_per_row=5)
    rows.append([Button(T.BACK, "crew")])
    return Screen("\n".join(lines), rows, name="plcrewpick")


@on_screen("plcrewpick")
async def plcrewpick_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    c = ctx.content
    tz = ctx.ui.settings.timezone
    if action == "recruit" and arg and arg.isdigit():
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            res = await svc.make_offer(s, c, ch, year, tz, int(arg), pl.CREW)
        if res.blocked is not None or res.target is None:
            why = "Этот игрок уже в банде." if res.blocked == "in_crew" else _block_text(c, res.blocked or "gone")
            return await _recruit_screen(ctx, why)
        await _ping_target(ctx, res.target.tg_id, "offer")
        return await _recruit_screen(ctx, T.OFFER_SENT["crew"].format(name=T.name(res.target.name), a=T.a(svc.gender(ch)),
                                                                      stake=""))
    if action == "dep" and arg and arg.isdigit():
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            r = await crews.deposit(s, c, ch, random.Random(), year, tz, int(arg))
        return await crew_screen(ctx, _crew_block(c, r.blocked) if r.blocked else
                                 T.CREW_DEPOSITED.format(amount=views.money(int(arg))))
    if action == "hstart" and arg:
        async with db.session_scope() as s:
            err = await crews.heist_start(s, c, ctx.user_id, arg)
        notes = {crews.BLOCK_LEADER: "Объявить налёт может только лидер.", "running": "Налёт уже идёт.",
                 "cooldown": "Налёт был меньше недели назад.", crews.BLOCK_GONE: "Такой цели нет."}
        if err is None:
            async with db.session_scope() as s:
                crew = await crews.crew_of(s, ctx.user_id)
                tgs = await crews.members(s, crew.id) if crew else []
            for tg in tgs:
                if tg != ctx.user_id:
                    await _ping_target(ctx, tg, "crew")
        return await heist_screen(ctx, notes.get(err, err) if err else "Налёт объявлен. У банды сутки.")
    if action == "hjoin" and arg:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            err, res, pts = await crews.heist_join(s, c, ch, random.Random(), year, tz, arg)
        notes = {"none": "Налёта нет.", "joined": "Ты уже в деле.", "jail": "Из камеры на дело не ходят.",
                 "no_ap": "Не хватает очков действий."}
        if err:
            return await heist_screen(ctx, notes.get(err, err))
        line = (views.check_line(res) + "\n") if res is not None else ""
        line += f"Ты приносишь банде {pts} {'очко' if pts == 1 else 'очка' if pts == 2 else 'очков'}."
        return await heist_screen(ctx, line)
    if action == "cbuy" and arg:
        async with db.session_scope() as s:
            err = await crews.biz_buy(s, c, ctx.user_id, arg)
        notes = {"bank": "В общаке не хватает денег.", "have": "У банды уже есть бизнес.", "max": "Больше расширять некуда.",
                 crews.BLOCK_LEADER: "Это решает только лидер."}
        return await crew_biz_screen(ctx, notes.get(err, err) if err else "Готово. Общак работает на банду.")
    if action == "payto" and arg and arg.isdigit():
        async with db.session_scope() as s:
            crew = await crews.crew_of(s, ctx.user_id)
            who = await repo.display_name(s, int(arg))
        bank = crew.bank if crew else 0
        amounts = [a for a in c.balance.players.crew.deposits if a <= bank]
        rows = pack([Button(views.money(a), "payamt", f"{arg}:{a}") for a in amounts], c.balance.ui.button_chars,
                    max_per_row=2)
        if bank > 0:  # owner request 24.10: any amount, or the whole bank
            rows.append([Button("Своя сумма", "payc", arg), Button(f"Весь общак · {views.money(bank)}", "payamt", f"{arg}:{bank}")])
        rows.append([Button(T.BACK, "crew")])
        return Screen(T.PAYOUT_AMOUNT.format(name=texts.esc(who), bank=views.money(bank)), rows, name="plcrewpick")
    if action == "payamt" and arg and ":" in arg:
        tg, amount = (int(x) for x in arg.split(":", 1))
        async with db.session_scope() as s:
            r = await crews.pay_out(s, ctx.user_id, tg, amount)
            who = await repo.display_name(s, tg)
        if r.blocked is None:
            await _ping_target(ctx, tg, "gift")
        return await crew_screen(ctx, _crew_block(c, r.blocked) if r.blocked else
                                 T.CREW_PAID.format(name=texts.esc(who), amount=views.money(amount)))
    if action == "depc":
        return Screen("Своя сумма в общак\nНапиши число в чат, например 25000.", [[Button(T.BACK, "crew")]],
                      awaiting_text="crew_dep_amount", name="plcrewpick")
    if action == "payc" and arg and arg.isdigit():
        return Screen("Своя сумма из общака\nНапиши число в чат, например 25000.", [[Button(T.BACK, "crew")]],
                      awaiting_text="crew_pay_amount", name="plcrewpick", state={"tg": int(arg)})
    if action == "kickto" and arg and arg.isdigit():
        async with db.session_scope() as s:
            r = await crews.kick(s, ctx.user_id, int(arg))
            who = await repo.display_name(s, int(arg))
        return await crew_screen(ctx, _crew_block(c, r.blocked) if r.blocked else T.CREW_KICKED.format(name=texts.esc(who)))
    if action == "leave_yes":
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            await crews.leave(s, ctx.user_id, ch.state.name if ch else "")
        return await crew_screen(ctx, T.CREW_LEFT)
    return await crew_screen(ctx)


@on_text("crew_name")
async def typed_crew_name(ctx: Ctx, text: str) -> Screen | None:
    c = ctx.content
    name = " ".join(text.split())
    from app.bot.handlers.onboarding import validate_name

    clean, error = validate_name(c, name)
    if clean is None and len(name) >= c.balance.players.crew.name_min:
        return await crew_screen(ctx, texts.esc(error or ""))
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, c)
        r = await crews.found(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, name)
    if r.blocked is not None or r.crew is None:
        return await crew_screen(ctx, _crew_block(c, r.blocked or "gone"))
    ctx.ui.post(T.POST_FOUNDED.format(name=texts.esc(r.crew.name), who=texts.esc(ch.state.name)), 12 * 3600)
    return await crew_screen(ctx, T.CREW_FOUNDED.format(name=texts.esc(r.crew.name)))


# -- delivery (called by the menu) -------------------------------------------------------------------------------------


def delivered_lines(content: Content, got: list[svc.Delivered]) -> list[str]:
    """Menu notices for delivered letters. Plain text (the menu escapes notices)."""
    out: list[str] = []
    for d in got:
        p = d.payload
        g = str(p.get("g", "m"))
        name = d.from_name
        if d.kind == pl.GIFT:
            line = T.GOT_GIFT.format(name=name, a=T.a(g), net=views.money(int(p.get("eddies", 0))))
        elif d.kind == pl.DRINK:
            line = T.GOT_DRINK.format(name=name, a=T.a(g), mood=int(p.get("mood", 0)))
        elif d.kind == pl.VOUCH:
            line = (T.GOT_VOUCH.format(name=name, a=T.a(g), fixer=d.fixer, n=int(p.get("fixer_rel", 0)))
                    if d.fixer else T.GOT_VOUCH_NOFIXER.format(name=name, a=T.a(g)))
        elif d.kind == pl.NOTE:
            line = T.GOT_NOTE.format(name=name, text=str(p.get("text", "")))
        elif d.kind == pl.REPLY:
            line = _reply_line(content, name, g, p)
        elif d.kind == pl.TOGETHER:
            kind = str(p.get("kind", ""))
            if kind not in T.GOT_TOGETHER:
                continue
            line = T.GOT_TOGETHER[kind].format(name=name, a=T.a(g), skill=texts.SKILL_LABELS.get(str(p.get("skill")), "").lower(),
                                               xp=int(p.get("xp", 0) or 0), mood=int(p.get("mood", 0) or 0))
        elif d.kind == "ai_hit":
            from app.bot import texts_ai as TA

            line = TA.GOT_HIT.format(name=name, steal=views.money(int(p.get("lost", 0) or 0)),
                                     hum=int(p.get("humanity", 0) or 0))
        elif d.kind == "ai_glitch":
            from app.bot import texts_ai as TA

            line = TA.GOT_GLITCH.format(name=name)
        elif d.kind == "cameo":
            line = T.GOT_CAMEO.format(name=name, a=T.a(g))
        elif d.kind == "stashfound":
            eddies = views.money(int(p.get("eddies", 0) or 0))
            line = T.GOT_STASH_OWN.format(eddies=eddies) if p.get("own") else T.GOT_STASH.format(name=name, a=T.a(g), eddies=eddies)
        elif d.kind == pl.ATTACKED:
            cfg = content.balance.players.pvp
            lost = int(p.get("lost", 0) or 0)
            if p.get("won"):
                if p.get("revenge"):
                    tpl = T.GOT_REVENGE_ROB if p.get("mode") == pl.ROB else T.GOT_REVENGE_BEAT
                else:
                    tpl = T.GOT_ATTACK_ROB if p.get("mode") == pl.ROB else T.GOT_ATTACK_BEAT
            else:
                tpl = T.GOT_REVENGE_FAIL if p.get("revenge") else T.GOT_ATTACK_FAIL
            line = tpl.format(name=name, a=T.a(g), asy=T.asy(g), lost=views.money(lost), days=cfg.revenge_days,
                              cred=cfg.win_cred)
        elif d.kind == "msold":
            line = T.GOT_SOLD.format(item=_item_name(content, str(p.get("kind", "")), str(p.get("item", ""))),
                                     eddies=views.money(int(p.get("eddies", 0) or 0)))
        elif d.kind == "crewpay":
            tpl = T.GOT_CREW_DISSOLVED if p.get("dissolved") else T.GOT_CREWPAY
            line = tpl.format(crew=str(p.get("crew", "")), eddies=views.money(int(p.get("eddies", 0) or 0)))
        elif d.kind == "city":
            line = texts.esc(str(p.get("text", "")))
        elif d.kind == "btears":  # owner request 26.10
            line = (f"🖤 <b>Чёрные слёзы</b> ({texts.esc(str(p.get('title', '')))}…). Последствие: "
                    f"{texts.esc(str(p.get('text', '')))}")
        elif d.kind == "dialog":  # owner request 26.10
            head = "После твоих дел в городе" if p.get("deeds") else f"После разговора с {texts.esc(str(p.get('npc', '')))}"
            line = f"💬 <b>{head}</b>: {texts.esc(str(p.get('text', '')))}"
        elif d.kind == "proxy":  # owner request 29.10
            from app.bot import texts_combat as TC
            from app.bot import texts_proxy as TXP

            hurt = str(p.get("sum", ""))
            line = TXP.GOT.format(
                boss=name, title=str(p.get("title", "")),
                outcome=TC.OUTCOME_TITLES.get(str(p.get("outcome")), str(p.get("outcome"))).lower(),
                days=int(p.get("days", 0) or 0), fee=views.money(int(p.get("eddies", 0) or 0)),
                hurt=TXP.GOT_HURT.format(hurt=hurt) if hurt else TXP.GOT_NO_HURT)
        elif d.kind == "crewinfo":
            line = (T.GOT_CREW_LEADER if p.get("line") == "leader" else T.GOT_CREW_KICKED).format(crew=str(p.get("crew", "")))
        elif d.kind == "raid":
            from app.bot import texts_raid as TR

            if p.get("won"):
                line = TR.GOT_WON.format(name=name, place=int(p.get("place", 0)), eddies=views.money(int(p.get("eddies", 0))),
                                         cred=int(p.get("cred", 0)))
                if p.get("item"):
                    item = content.armor.get(str(p["item"])) or content.weapons.get(str(p["item"]))
                    line += TR.GOT_TOP.format(item=item.name if item else p["item"])
            else:
                line = TR.GOT_ESCAPED.format(name=name, eddies=views.money(int(p.get("eddies", 0))))
            out.append(line)
            continue
        elif d.kind in (pl.Q_PAY, pl.Q_DONE, pl.Q_FAIL):
            goal = pl.quest_goal(content, str(p.get("goal", "")))
            qtext = goal.text if goal else "?"
            if d.kind == pl.Q_PAY:
                line = T.GOT_QPAY.format(text=qtext, name=name, eddies=views.money(int(p.get("eddies", 0) or 0)))
            elif d.kind == pl.Q_DONE:
                line = T.GOT_QDONE.format(name=name, a=T.a(g), text=qtext)
            else:
                line = T.GOT_QFAIL.format(text=qtext, name=name)
        elif d.kind == pl.HIRE:
            skill = p.get("skill")
            xp = T.GOT_HIRE_XP.format(skill=texts.SKILL_LABELS.get(str(skill), str(skill)).lower(), n=int(p.get("xp", 0)))\
                if skill and int(p.get("xp", 0) or 0) else ""
            from app.bot.texts_combat import OUTCOME_TITLES  # local: the gig texts live there

            line = T.GOT_HIRE.format(name=name, a=T.a(g), title=str(p.get("title", "")),
                                     outcome=OUTCOME_TITLES.get(str(p.get("outcome")), str(p.get("outcome"))).lower(),
                                     cut=views.money(int(p.get("eddies", 0) or 0)), xp=xp)
        else:
            continue
        out.append((T.HEIR_PREFIX if d.heir else "") + line)
    return out


def _reply_line(content: Content, name: str, g: str, p: dict) -> str:
    offer = str(p.get("offer"))
    if offer == "contract" and p.get("accepted"):
        from app.engine import paths as pe

        job = pe.contract_job(content, str(p.get("job", "")))
        jname = job.name if job else "?"
        if p.get("ok"):
            return T.REPLY_CONTRACT_OK.format(job=jname, name=name, cut=views.money(int(p.get("cut", 0) or 0)))
        return T.REPLY_CONTRACT_FAIL.format(job=jname, name=name)
    if offer == pl.JOB and p.get("accepted"):
        job = pl.job_of(content, str(p.get("job", "")))
        cfg = content.balance.players.joint
        outcome = str(p.get("outcome", "fail"))
        pay = (job.pay if job else 0) if outcome == "full" else (job.pay // 2 if job else 0)
        return T.REPLY_JOB[outcome].format(job=job.name if job else "?", name=name, pay=views.money(pay), cred=cfg.cred,
                                           heat=cfg.fail_heat, hp=int(p.get("hp", 0) or 0))
    if offer == "corp" and p.get("accepted"):
        return (f"{name} принимает работу в «{texts.esc(str(p.get('corp', '')))}»: {texts.esc(str(p.get('post', '')))}. "
                "Зарплата — с тебя каждый месяц.")
    if offer == pl.CREW and p.get("accepted"):
        return T.REPLY_YES["crew"].format(name=name, crew=str(p.get("crew_name", "")))
    if offer == pl.QUEST:
        goal = pl.quest_goal(content, str(p.get("goal", "")))
        qtext = goal.text if goal else "?"
        refund = views.money(int(p.get("refund", 0) or 0))
        if p.get("expired"):
            return T.REPLY_QUEST_EXPIRED.format(text=qtext, refund=refund)
        if p.get("failed"):
            return T.REPLY_QUEST_FAIL.format(name=name, asy=T.asy(g), text=qtext, refund=refund)
        if not p.get("accepted"):
            return T.REPLY_QUEST_NO.format(name=name, asy=T.asy(g), text=qtext, refund=refund)
        return T.REPLY_QUEST_YES.format(name=name, asy=T.asy(g), text=qtext)
    if p.get("expired"):
        return T.REPLY_EXPIRED.format(offer=T.OFFER_NAMES.get(offer, offer))
    if not p.get("accepted"):
        return T.REPLY_NO.format(name=name, a=T.a(g), offer=T.OFFER_NAMES.get(offer, offer))
    if offer == pl.DUEL:
        stake = int(p.get("stake", 0) or 0)
        if p.get("they_won"):
            tpl = T.REPLY_DUEL_LOST if stake else T.REPLY_DUEL_LOST_FREE
        else:
            tpl = T.REPLY_DUEL_WON if stake else T.REPLY_DUEL_WON_FREE
        return tpl.format(name=name, stake=views.money(stake), hp=int(p.get("hp", 0) or 0),
                          cred=int(p.get("cred", 0) or 0))
    if offer == pl.CHILD:  # owner request 25.10
        return T.REPLY_CHILD.format(name=name, child=texts.esc(str(p.get("child", ""))))
    return T.REPLY_YES.get(offer, "{name}").format(name=name, asy=T.asy(g), mood=int(p.get("mood", 0) or 0))


async def deliver_for_menu(ctx: Ctx, ch: ActiveCharacter) -> list[str]:
    """Deliver the waiting letters to the player's character; the menu shows the returned lines."""
    c = ctx.content
    async with db.session_scope() as s:
        year = await game.current_year(s, c)
        got = await svc.deliver(s, c, ch, random.Random(), year, ctx.ui.settings.timezone)
        waiting = await svc.offers_for(s, c, ctx.user_id, year)
    lines = delivered_lines(c, got)
    if waiting:
        lines.append(T.GOT_OFFER.format(name=waiting[0].from_name))
    return lines



# -- commands ------------------------------------------------------------------------------------------------------------

router = Router(name="players")


@router.message(Command("top"))
async def cmd_top(message: Message, ui: UiKit) -> None:
    """The city leaderboard (owner request), posted for a while in the group."""
    from app.services import leaderboard

    async with db.session_scope() as s:
        text = await leaderboard.text(s)
    ui.reply_temp(message.chat.id, text, 3600)


@router.message(Command("market"))
async def cmd_market(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    ctx = await make_ctx(ui, user, message.chat.id)
    await ui.windows.open(message.chat.id, user.id, await market_screen(ctx))


@router.message(Command("crew"))
async def cmd_crew(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    ctx = await make_ctx(ui, user, message.chat.id)
    await ui.windows.open(message.chat.id, user.id, await crew_screen(ctx))
