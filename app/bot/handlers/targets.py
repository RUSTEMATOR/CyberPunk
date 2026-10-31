"""Who a command points at: `@username`, a mention of a user without a username, or a reply."""

from __future__ import annotations

from aiogram.types import Message

from app.db import repo
from app.db import session as db


async def target_player(message: Message) -> int | None:
    """tg id of the player a command points at, None when it points at nobody we know.

    Order: a `text_mention` (a user without a username), an `@username` that is in the players table,
    the author of the replied-to message. Bots never count.
    """
    text = message.text or ""
    for ent in message.entities or []:
        if ent.type == "text_mention" and ent.user is not None and not ent.user.is_bot:
            return ent.user.id
    for ent in message.entities or []:
        if ent.type == "mention":
            username = ent.extract_from(text)
            async with db.session_scope() as s:
                player = await repo.find_player_by_username(s, username)
            if player is not None:
                return player.tg_id
            return None
    reply = message.reply_to_message
    if reply is not None and reply.from_user is not None and not reply.from_user.is_bot:
        return reply.from_user.id
    return None


def has_target_token(message: Message) -> bool:
    """True when the command names somebody (so an unknown name can be told apart from a missing one)."""
    return any(e.type in ("mention", "text_mention") for e in message.entities or []) or (
        message.reply_to_message is not None
    )


def number_arg(args: str | None) -> int | None:
    """The first whole number among the arguments (a stake), None when there is none."""
    for tok in (args or "").split():
        if tok.isdigit():
            return int(tok)
    return None
