"""The command list shown in Telegram's "/" menu (set_my_commands) and the plain command texts.

`GROUP_COMMANDS` is the single list of player commands; later milestones add to it. Admin
commands are never listed. A command whose handler does not exist yet is answered with "Скоро"
by the fallback router (`app/bot/handlers/commands.py`), which is included last.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat,
    BotCommandScopeChatMember,
    BotCommandScopeDefault,
)

log = logging.getLogger(__name__)

GROUP_COMMANDS: list[BotCommand] = [
    BotCommand(command="play", description="Открыть окно игрока"),
    BotCommand(command="menu", description="То же, что /play"),
    BotCommand(command="daily", description="Ежедневка: карта Мисти и награды"),
    BotCommand(command="raid", description="Городской рейд: общий босс"),
    BotCommand(command="market", description="Рынок: оружие и броня от игроков"),
    BotCommand(command="crew", description="Твоя банда"),
    BotCommand(command="top", description="Рейтинг города"),
    BotCommand(command="auction", description="Аукцион легендарных лотов"),
    BotCommand(command="who", description="Кто жив и где"),
    BotCommand(command="legends", description="Топы: улица, статус, слава"),
    BotCommand(command="graveyard", description="Кладбище"),
    BotCommand(command="lineage", description="Твой род"),
    BotCommand(command="caliber", description="Калибр ствола"),
    BotCommand(command="caliber_top", description="Топ калибров"),
    BotCommand(command="car", description="Тачка"),
    BotCommand(command="race", description="Гонка с игроком"),
    BotCommand(command="cat", description="Кибер-кот"),
    BotCommand(command="catfight", description="Кошачий бой"),
    BotCommand(command="coin", description="Эдди-крипта"),
    BotCommand(command="dirt", description="Компромат"),
    BotCommand(command="dump", description="Слить компромат на игрока"),
    BotCommand(command="purge", description="Судная ночь"),
    BotCommand(command="faq", description="Справочник: все команды и механики"),
    BotCommand(command="idea", description="Баг или идея: заявка владельцу"),
    BotCommand(command="bug", description="Сообщить о баге"),
    BotCommand(command="help", description="Помощь"),
]

# Owner request 24.10: the admin commands are in the "/" menu of the admins only (scope chat_member in the group and
# their private chat with the bot). Every handler checks `ui.is_admin` anyway: a player who types one gets no reaction.
ADMIN_COMMANDS: list[BotCommand] = [
    BotCommand(command="requests", description="Админ: заявки игроков (баги и идеи)"),
    BotCommand(command="admin_give", description="Админ: выдать эдди игроку"),
    BotCommand(command="admin_set", description="Админ: поменять поле персонажа"),
    BotCommand(command="admin_grant_item", description="Админ: выдать оружие или броню"),
    BotCommand(command="admin_grant_cyber", description="Админ: поставить имплант"),
    BotCommand(command="admin_give_vehicle", description="Админ: выдать тачку"),
    BotCommand(command="admin_bounty_clear", description="Админ: снять награду за голову"),
    BotCommand(command="admin_pboss", description="Админ: объявить игрока рейд-боссом"),
    BotCommand(command="admin_pboss_off", description="Админ: снять рейд-босса"),
    BotCommand(command="admin_kill", description="Админ: убить персонажа"),
    BotCommand(command="admin_revive", description="Админ: воскресить персонажа"),
    BotCommand(command="admin_raid", description="Админ: запустить рейд сейчас"),
    BotCommand(command="admin_purge", description="Админ: запустить Судную ночь"),
    BotCommand(command="admin_tick", description="Админ: прогнать день мира"),
    BotCommand(command="admin_news", description="Админ: сводка за сутки сейчас"),
    BotCommand(command="admin_year", description="Админ: год игрока"),
    BotCommand(command="admin_history", description="Админ: история мира"),
    BotCommand(command="admin_veto", description="Админ: отменить событие мира"),
    BotCommand(command="admin_budget", description="Админ: бюджет нарратора"),
    BotCommand(command="admin_setgroup", description="Админ: сделать этот чат игровым"),
]

DEFAULT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Как играть"),
    BotCommand(command="help", description="Помощь"),
]

# Commands that open the window (all aliases of /play).
PLAY_COMMANDS: tuple[str, ...] = ("play", "menu", "start")
# Commands with a real handler of their own; the rest of GROUP_COMMANDS answer "Скоро".
IMPLEMENTED: set[str] = set(PLAY_COMMANDS) | {
    "help", "who", "legends", "graveyard", "lineage", "daily", "caliber", "caliber_top", "car", "race", "cat", "catfight", "coin", "dirt", "dump",
    "top", "market", "crew", "raid", "auction", "faq", "purge", "idea", "bug", "requests",
} | {c.command for c in ADMIN_COMMANDS}

SOON = "Скоро. Город ещё не достроен."

def soon_command_names() -> list[str]:
    return [c.command for c in GROUP_COMMANDS if c.command not in IMPLEMENTED]


async def register_commands(bot: Bot, group_id: int | None = None, admins: set[int] | None = None) -> None:
    """Publish the command menus: the players' list in groups, and for every admin the players' list plus the admin
    commands (in the game group and in the private chat). A failure is logged and never blocks startup."""
    try:
        for lang in (None, "ru"):
            await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats(), language_code=lang)
            await bot.set_my_commands(DEFAULT_COMMANDS, scope=BotCommandScopeDefault(), language_code=lang)
        log.info("bot commands registered")
    except Exception:
        log.exception("could not register bot commands; continuing without the / menu")
    for admin in sorted(admins or set()):
        for lang in (None, "ru"):
            if group_id is not None:
                try:
                    await bot.set_my_commands(GROUP_COMMANDS + ADMIN_COMMANDS, language_code=lang,
                                              scope=BotCommandScopeChatMember(chat_id=group_id, user_id=admin))
                except Exception as exc:
                    log.warning("admin commands in the group for %s: %s", admin, exc)
            try:  # a private chat exists only after the admin opened the bot
                await bot.set_my_commands(DEFAULT_COMMANDS + ADMIN_COMMANDS, language_code=lang,
                                          scope=BotCommandScopeChat(chat_id=admin))
            except Exception as exc:
                log.warning("admin commands in private for %s: %s", admin, exc)
