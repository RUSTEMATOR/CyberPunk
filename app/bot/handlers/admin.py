"""Admin commands (DESIGN.md section 16, PLAN 3.4). Only ADMIN_IDS; anybody else gets no reaction.

Implemented now: /admin_setgroup, /admin_give, /admin_set, /admin_kill, /admin_grant_cyber, /admin_grant_item. The rest
depend on later milestones and answer "не реализовано". Every answer is a temporary message.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner, Message

from app.bot import texts
from app.bot import texts_payoff as tp
from app.bot import texts_death as death_texts
from app.bot.ui.kit import UiKit
from app.content.schemas import DEATH_CAUSES
from app.db import repo
from app.db import session as db
from app.engine.effects import apply_effects
from app.services import death as death_service
from app.services import game

log = logging.getLogger(__name__)

router = Router(name="admin")

# /admin_set may change these integer fields of the living character (a test tool, not a game action).
SETTABLE_FIELDS: tuple[str, ...] = (
    "eddies", "hp", "mood", "humanity", "street_cred", "status", "fame", "heat",
    "age_years", "age_weeks", "ap", "glitches", "luck_pool", "weeks_lived",
)
# Commands that wait for later milestones: M4 events. (/admin_budget lives in admin_llm.py, M10 commands in admin_world.py.)
NOT_IMPLEMENTED: tuple[str, ...] = ("admin_event",)


async def _target_id(message: Message, token: str | None) -> int | None:
    """tg id of the target: `@username`, a number, or the author of the replied-to message."""
    if token:
        if token.lstrip("-").isdigit():
            return int(token)
        async with db.session_scope() as s:
            player = await repo.find_player_by_username(s, token)
        return player.tg_id if player else None
    reply = message.reply_to_message
    if reply is not None and reply.from_user is not None and not reply.from_user.is_bot:
        return reply.from_user.id
    return None


def _found(ui: UiKit, tg_id: int, kind: str, item: str, weapon_key: str | None = None) -> None:
    """Owner request 24.10: a granted thing comes with a story of how it was found: a post in the group (tagging the
    player) and the same story in the character's window. Claude writes it (`find_story`), a ready line when the
    narrator is off. Call AFTER the grant is committed: the job loads and saves the character again."""
    ui.spawn(_found_job(ui, tg_id, kind, item, weapon_key))


async def _found_job(ui: UiKit, tg_id: int, kind: str, item: str, weapon_key: str | None) -> None:
    from app.bot import texts_finds as F
    from app.bot.ui.cleanup import KIND_NEWS
    from app.narrator import narrator
    from app.services import narration, notices

    content = ui.content
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, tg_id)
        if ch is None:
            return
        name = f"{ch.state.name} {ch.state.surname}".strip()
        card = narration.character_card(content, ch.state)
        card.update(name=name, gender=str(ch.state.flags.get("gender") or "m"))
        year = ch.state.current_year
    pool = {"weapon": F.WEAPON, "armor": F.ARMOR, "cyber": F.CYBER, "vehicle": F.VEHICLE, "eddies": F.EDDIES}[kind]
    tpl = random.choice(pool)
    quoted = item if kind == "eddies" or "«" in item else f"«{item}»"
    w = content.weapons.get(weapon_key or "") if weapon_key else None
    rec = w or content.armor.get(weapon_key or "") or None
    thing: dict[str, object] = {"name": item, "kind": {"weapon": "оружие", "armor": "броня", "cyber": "имплант",
                                                       "vehicle": "транспорт", "eddies": "деньги"}[kind]}
    desc = getattr(rec, "description", "") if rec is not None else ""
    if not desc:
        src = content.cyberware.get(weapon_key or "") or content.vehicles.get(weapon_key or "")
        desc = getattr(src, "description", "") if src is not None else ""
    if desc:
        thing["description"] = desc
    if w is not None and w.voice is not None:
        thing["voice"] = w.voice.persona
    hint = tpl.format(who=name, item=quoted)
    story = await narrator.find_story({"character": card, "item": thing, "hint": hint}, year=year)
    extra = ""
    if not story and w is not None and w.voice is not None and w.voice.idle:
        extra = F.TALKS.format(item=texts.esc(w.name), line=texts.esc(random.choice(w.voice.idle)))
    tag = f'<a href="tg://user?id={tg_id}">{texts.esc(name)}</a>'
    if story:
        body = texts.esc(story)
        post_story = body.replace(texts.esc(name), tag, 1) if texts.esc(name) in body else f"{tag}. {body}"
        note_story = body.replace(texts.esc(name), f"<b>{texts.esc(name)}</b>", 1)
    else:
        post_story = tpl.format(who=tag, item=texts.esc(quoted)) + extra
        note_story = tpl.format(who=f"<b>{texts.esc(name)}</b>", item=texts.esc(quoted)) + extra
    ui.post(F.POST.format(story=post_story), 24 * 3600, KIND_NEWS)
    if kind in ("weapon", "armor"):
        note_story += " " + F.NOTICE_NEW
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, tg_id)
        if ch is not None:
            notices.push(ch, "🎁 " + note_story)
            await game.save_active(s, ch, ui.settings.timezone)


def _is_admin(message: Message, ui: UiKit) -> bool:
    return message.from_user is not None and ui.is_admin(message.from_user.id)


@router.message(Command("admin_setgroup"))
async def admin_setgroup(message: Message, ui: UiKit) -> None:
    claimed = False
    if not _is_admin(message, ui):
        # first run without ADMIN_IDS: the first person to register a group becomes admin
        if not (ui.can_bootstrap_admin() and message.from_user is not None
                and message.chat.type in ("group", "supergroup")):
            return
        await ui.claim_bootstrap_admin(message.from_user.id)
        claimed = True
    if message.chat.type not in ("group", "supergroup"):
        ui.reply_temp(message.chat.id, texts.ADMIN_SETGROUP_ONLY_GROUP)
        return
    await ui.set_group_id(message.chat.id)
    from app.bot.commands import register_commands  # the admins' "/" menu follows the game group

    ui.spawn(register_commands(ui.bot, message.chat.id, set(ui.settings.admin_ids) | set(ui.bootstrap_admins)))
    reply = texts.ADMIN_SETGROUP_DONE.format(chat_id=message.chat.id)
    if claimed:
        reply += "\n" + texts.ADMIN_BOOTSTRAP_CLAIMED.format(user_id=message.from_user.id)
    try:
        member = await ui.bot.get_chat_member(message.chat.id, ui.bot.id)
        can_delete = isinstance(member, ChatMemberOwner) or (
            isinstance(member, ChatMemberAdministrator) and bool(member.can_delete_messages)
        )
        if not can_delete:
            reply += "\n" + texts.ADMIN_SETGROUP_NO_RIGHTS
    except Exception:
        log.exception("admin_setgroup: could not read the bot's rights")
        reply += "\n" + texts.ADMIN_SETGROUP_CHECK_FAILED
    ui.reply_temp(message.chat.id, reply)


@router.message(Command("admin_give"))
async def admin_give(message: Message, command: CommandObject, ui: UiKit) -> None:
    if not _is_admin(message, ui):
        return
    parts = (command.args or "").split()
    amount_token = parts[-1] if parts else ""
    who_token = parts[0] if len(parts) >= 2 else None
    try:
        amount = int(amount_token)
    except ValueError:
        ui.reply_temp(message.chat.id, texts.ADMIN_USAGE_GIVE)
        return
    tg_id = await _target_id(message, who_token)
    if tg_id is None:
        ui.reply_temp(message.chat.id, texts.ADMIN_NO_PLAYER)
        return
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, tg_id)
        if ch is None:
            ui.reply_temp(message.chat.id, texts.ADMIN_NO_PLAYER)
            return
        year = await game.current_year(s, ui.content, tg_id)
        before = ch.state.eddies
        apply_effects(ch.state, {"eddies": amount}, ui.content, random.Random(), year)
        await game.save_active(s, ch, ui.settings.timezone)
        name, eddies = ch.state.name, ch.state.eddies
    if amount > 0:
        from app.bot import views

        _found(ui, tg_id, "eddies", f"{views.money(amount)} эдди")
    ui.reply_temp(message.chat.id, texts.ADMIN_GIVE_DONE.format(name=texts.esc(name), delta=eddies - before, eddies=eddies))


@router.message(Command("admin_set"))
async def admin_set(message: Message, command: CommandObject, ui: UiKit) -> None:
    if not _is_admin(message, ui):
        return
    fields = ", ".join(SETTABLE_FIELDS)
    parts = (command.args or "").split()
    reply = message.reply_to_message is not None and len(parts) == 2
    if len(parts) != 3 and not reply:
        ui.reply_temp(message.chat.id, texts.ADMIN_USAGE_SET.format(fields=fields))
        return
    who_token, field_name, raw = (None, parts[0], parts[1]) if reply else (parts[0], parts[1], parts[2])
    if field_name not in SETTABLE_FIELDS:
        ui.reply_temp(message.chat.id, texts.ADMIN_SET_BAD_FIELD.format(field=texts.esc(field_name), fields=fields))
        return
    if not raw.isdigit():
        ui.reply_temp(message.chat.id, texts.ADMIN_SET_BAD_VALUE)
        return
    tg_id = await _target_id(message, who_token)
    if tg_id is None:
        ui.reply_temp(message.chat.id, texts.ADMIN_NO_PLAYER)
        return
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, tg_id)
        if ch is None:
            ui.reply_temp(message.chat.id, texts.ADMIN_NO_PLAYER)
            return
        setattr(ch.state, field_name, int(raw))
        await game.save_active(s, ch, ui.settings.timezone)
        name = ch.state.name
    ui.reply_temp(message.chat.id, texts.ADMIN_SET_DONE.format(name=texts.esc(name), field=field_name, value=int(raw)))


@router.message(Command("admin_kill"))
async def admin_kill(message: Message, command: CommandObject, ui: UiKit) -> None:
    """/admin_kill [@user | id] [cause]: ends the living character through the ordinary death (obituary, graveyard, legacy)."""
    if not _is_admin(message, ui):
        return
    causes = ", ".join(DEATH_CAUSES)
    parts = (command.args or "").split()
    cause = "combat"
    if parts and parts[-1] in DEATH_CAUSES:
        cause = parts.pop()
    elif len(parts) > 1 or (parts and message.reply_to_message is not None):
        ui.reply_temp(message.chat.id, death_texts.ADMIN_KILL_USAGE.format(causes=causes))
        return
    tg_id = await _target_id(message, parts[0] if parts else None)
    if tg_id is None:
        ui.reply_temp(message.chat.id, texts.ADMIN_NO_PLAYER)
        return
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, tg_id)
        if ch is None:
            ui.reply_temp(message.chat.id, texts.ADMIN_NO_PLAYER)
            return
        year = await game.current_year(s, ui.content, tg_id)
        name = f"{ch.state.name} {ch.state.surname}".strip()
        await death_service.kill(s, ui.content, ch, cause, ui.settings.timezone, year=year)
    ui.reply_temp(message.chat.id, death_texts.ADMIN_KILL_DONE.format(name=texts.esc(name), cause=texts.DEATH_CAUSE_NAMES.get(cause, cause)))


REVIVE_STORY = ("Морг Травма Тим, ящик номер четырнадцать. Дежурный медик уже заполняет бирку, когда монитор над "
                "столом пищит. Платиновый контракт пишет мелким шрифтом: «реанимация в течение 72 часов». "
                "Через шесть часов {who} открывает глаза под лампой операционной. Счёт за воскрешение придёт отдельно.")


async def revive_and_post(ui: UiKit, tg_id: int, story: str = "") -> str | None:
    """Owner request 24.10: the admin brings a dead character back and the group hears how. Returns the name or None."""
    from app.bot.ui.cleanup import KIND_NEWS
    from app.services import notices

    async with db.session_scope() as s:
        ch = await death_service.revive(s, ui.content, tg_id, ui.settings.timezone)
        if ch is None:
            return None
        name = f"{ch.state.name} {ch.state.surname}".strip()
        tag = f'<a href="tg://user?id={tg_id}">{texts.esc(name)}</a>'
        text = texts.esc(story) if story else REVIVE_STORY.format(who=tag)
        notices.push(ch, "🫀 " + (texts.esc(story) if story else REVIVE_STORY.format(who=f"<b>{texts.esc(name)}</b>")))
        await game.save_active(s, ch, ui.settings.timezone)
    ui.post(f"🫀 <b>Воскрешение</b>\n{text}", 24 * 3600, KIND_NEWS)
    return name


@router.message(Command("admin_revive"))
async def admin_revive(message: Message, command: CommandObject, ui: UiKit) -> None:
    """/admin_revive [@user | id] [story]: the last dead character of the player lives again; the story goes to the group."""
    if not _is_admin(message, ui):
        return
    parts = (command.args or "").split(maxsplit=1)
    token = parts[0] if parts and (parts[0].startswith("@") or parts[0].lstrip("-").isdigit()) else None
    story = (parts[1] if token and len(parts) > 1 else ("" if token else (command.args or ""))).strip()
    tg_id = await _target_id(message, token)
    if tg_id is None:
        ui.reply_temp(message.chat.id, "Кого воскресить: /admin_revive @ник [история] или ответом на сообщение игрока.")
        return
    name = await revive_and_post(ui, tg_id, story)
    if name is None:
        ui.reply_temp(message.chat.id, "Некого воскрешать: игрок жив в другом персонаже или мёртвых персонажей нет.")
        return
    log.warning("admin %s revived tg %s (%s)", message.from_user.id if message.from_user else "?", tg_id, name)
    ui.reply_temp(message.chat.id, f"{texts.esc(name)} снова жив(а).")


async def _grant(message: Message, command: CommandObject, ui: UiKit, kind: str) -> None:
    """/admin_grant_cyber and /admin_grant_item: `[@user | id] <key>` (or a reply to the player's message).
    The implant or item is given for free through `apply_effects` (`cyberware_grant` / `item_grant`): compensation for
    a payoff a broken event never delivered, or a test tool."""
    usage = tp.ADMIN_USAGE_GRANT_CYBER if kind == "cyber" else tp.ADMIN_USAGE_GRANT_ITEM
    parts = (command.args or "").split()
    if not parts or len(parts) > 2:
        ui.reply_temp(message.chat.id, usage)
        return
    key = parts[-1]
    content = ui.content
    if kind == "cyber":
        catalog, effect = content.cyberware, f"cyberware_grant:{key}"
    else:
        catalog = content.weapons if key in content.weapons else content.armor
        effect = f"item_grant:{key}"
    rec = catalog.get(key)
    if rec is None:
        ui.reply_temp(message.chat.id, tp.ADMIN_GRANT_UNKNOWN.format(key=texts.esc(key)))
        return
    tg_id = await _target_id(message, parts[0] if len(parts) == 2 else None)
    if tg_id is None:
        ui.reply_temp(message.chat.id, texts.ADMIN_NO_PLAYER)
        return
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, tg_id)
        if ch is None:
            ui.reply_temp(message.chat.id, texts.ADMIN_NO_PLAYER)
            return
        year = ch.state.current_year
        applied = apply_effects(ch.state, {effect: True}, content, random.Random(), year)
        await game.save_active(s, ch, ui.settings.timezone)
        name = texts.esc(f"{ch.state.name} {ch.state.surname}".strip())
    got = [content.cyberware[k].name for k in applied.cyberware_added] if kind == "cyber" else \
        [(content.weapons.get(k) or content.armor.get(k) or content.vehicles[k]).name for _, k in applied.items_added]
    if not got:
        why = "; ".join(applied.notes) or "уже есть"
        reply = tp.ADMIN_GRANT_NOTHING.format(name=name, why=texts.esc(why))
    else:
        reply = tp.ADMIN_GRANT_DONE.format(name=name, what=texts.esc(", ".join(got)))
        if key not in content.available("cyberware" if kind == "cyber" else
                                        ("weapons" if key in content.weapons else "armor"), year):
            reply += "\n" + tp.ADMIN_GRANT_NOT_IN_YEAR.format(key=texts.esc(key), year=year)
    log.warning("admin %s granted %s to tg %s: %s", message.from_user.id if message.from_user else "?", effect, tg_id, got)
    if kind == "cyber" and applied.cyberware_added:
        _found(ui, tg_id, "cyber", content.cyberware[applied.cyberware_added[0]].name, applied.cyberware_added[0])
    elif kind != "cyber" and applied.items_added:
        k = applied.items_added[0][1]
        _found(ui, tg_id, "weapon" if k in content.weapons else "armor",
               (content.weapons.get(k) or content.armor.get(k)).name, k)
    ui.reply_temp(message.chat.id, reply)


@router.message(Command("admin_grant_cyber"))
async def admin_grant_cyber(message: Message, command: CommandObject, ui: UiKit) -> None:
    """/admin_grant_cyber [@user | id] <cyberware key>: install the implant for free (requirements come with it)."""
    if _is_admin(message, ui):
        await _grant(message, command, ui, "cyber")


@router.message(Command("admin_grant_item"))
async def admin_grant_item(message: Message, command: CommandObject, ui: UiKit) -> None:
    """/admin_grant_item [@user | id] <weapon or armor key>: put the item into the inventory for free."""
    if _is_admin(message, ui):
        await _grant(message, command, ui, "item")


@router.message(Command("admin_give_vehicle"))
async def admin_give_vehicle(message: Message, command: CommandObject, ui: UiKit) -> None:
    """/admin_give_vehicle [@user | id] <vehicle key>: put the ride into the player's garage (replaces the old one)."""
    if not _is_admin(message, ui):
        return
    parts = (command.args or "").split()
    if not parts or len(parts) > 2 or parts[-1] not in ui.content.vehicles:
        keys = ", ".join(sorted(ui.content.vehicles))
        ui.reply_temp(message.chat.id, f"Формат: /admin_give_vehicle [@ник] ключ. Ключи: {texts.esc(keys)}")
        return
    tg_id = await _target_id(message, parts[0] if len(parts) == 2 else None)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, tg_id) if tg_id is not None else None
        if ch is None:
            ui.reply_temp(message.chat.id, texts.ADMIN_NO_PLAYER)
            return
        ch.state.flags["vehicle"] = {"key": parts[-1], "tune": 0, "dmg": 0, "stolen": False}
        await game.save_active(s, ch, ui.settings.timezone)
        name = texts.esc(f"{ch.state.name} {ch.state.surname}".strip())
    _found(ui, tg_id, "vehicle", ui.content.vehicles[parts[-1]].name, parts[-1])
    log.warning("admin %s gave vehicle %s to tg %s", message.from_user.id if message.from_user else "?", parts[-1], tg_id)
    ui.reply_temp(message.chat.id, f"{name}: в гараже {texts.esc(ui.content.vehicles[parts[-1]].name)}.")


@router.message(Command("admin_bounty_clear"))
async def admin_bounty_clear(message: Message, command: CommandObject, ui: UiKit) -> None:
    """/admin_bounty_clear [@user | id]: drop the bounty on the player's character, the money goes back to who paid."""
    if not _is_admin(message, ui):
        return
    from app.db.models import PlayerMail
    from app.services import bounties

    tg_id = await _target_id(message, (command.args or "").strip() or None)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, tg_id) if tg_id is not None else None
        if ch is None:
            ui.reply_temp(message.chat.id, texts.ADMIN_NO_PLAYER)
            return
        b = await bounties.all_bounties(s)
        rec = b.pop(str(ch.id), None)
        if rec:
            for tg, amount in (rec.get("by") or {}).items():
                s.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name="Фиксер", kind="city",
                                 payload={"eddies": int(amount), "text": f"Заказ отменён. Фиксер возвращает {amount} эдди."}))
            await repo.ws_set(s, bounties.KEY, b)
    ui.reply_temp(message.chat.id, "Награда снята, деньги вернутся заказчикам." if rec else "Награды за эту голову нет.")


@router.message(Command("admin_pboss"))
async def admin_pboss(message: Message, command: CommandObject, ui: UiKit) -> None:
    """/admin_pboss [@user | id] [сумма]: the player's character becomes the raid boss with a bounty (owner request 27.10)."""
    if not _is_admin(message, ui):
        return
    from app.bot.ui.cleanup import KIND_NEWS
    from app.services import pboss

    parts = (command.args or "").split()
    amount = int(parts.pop()) if parts and parts[-1].isdigit() and len(parts[-1]) > 4 else None
    tg_id = await _target_id(message, parts[0] if parts else None)
    if tg_id is None:
        ui.reply_temp(message.chat.id, texts.ADMIN_NO_PLAYER)
        return
    async with db.session_scope() as s:
        rec = await pboss.declare(s, ui.content, tg_id, amount)
    if rec is None:
        ui.reply_temp(message.chat.id, texts.ADMIN_NO_PLAYER)
        return
    ui.post(pboss.announce_text(rec), 24 * 3600, KIND_NEWS)


@router.message(Command("admin_pboss_off"))
async def admin_pboss_off(message: Message, ui: UiKit) -> None:
    """/admin_pboss_off: the hunt is over, nobody gets the bounty."""
    if not _is_admin(message, ui):
        return
    from app.services import pboss

    async with db.session_scope() as s:
        rec = await pboss.clear(s)
    ui.reply_temp(message.chat.id, f"Охота на {texts.esc(rec['name'])} снята." if rec else "Рейд-босса сейчас нет.")


def _stub(cmd: str) -> Any:
    @router.message(Command(cmd))
    async def handler(message: Message, ui: UiKit) -> None:
        if _is_admin(message, ui):
            ui.reply_temp(message.chat.id, texts.ADMIN_NOT_IMPLEMENTED.format(cmd="/" + cmd))

    return handler


for _cmd in NOT_IMPLEMENTED:
    _stub(_cmd)
