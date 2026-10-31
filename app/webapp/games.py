"""Telegram Games: «Протокол взлома», an HTML5 game with Telegram's own high-score table (docs/WEBAPP.md, W11).

- `/hack` in the game group sends the game message (`sendGame`, short name GAME). The game has to exist in BotFather
  (`/newgame`, short name `breach`): until then the command says so and nothing else happens.
- «Играть» on that message: the bot answers the callback with the game page and a signed token (who, which message).
- The page plays on its own (`webapp/game/`), then POSTs the score; the bot records it with `setGameScore`
  (Telegram keeps the best one and shows the table under the game message). `GET /api/game/top` reads the table.

The game is a mini-game for fun: its score changes nothing in Night City Life.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.webapp import settings as wsettings

log = logging.getLogger(__name__)

GAME = "breach"
TOKEN_TTL_S = 6 * 3600
SCORE_MAX = 1_000_000
NOT_READY = "Игра «Протокол взлома» ещё не заведена в BotFather (/newgame, короткое имя breach)."

router = Router(name="webapp_games")
UI: Any = None


def _key(bot_token: str) -> bytes:
    return hashlib.sha256(("game|" + bot_token).encode()).digest()


def make_token(bot_token: str, user_id: int, chat_id: int | None, message_id: int | None, inline_id: str | None) -> str:
    body = base64.urlsafe_b64encode(json.dumps(
        {"u": user_id, "c": chat_id, "m": message_id, "i": inline_id, "t": int(time.time())},
        separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(_key(bot_token), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def read_token(bot_token: str, token: str) -> dict[str, Any] | None:
    try:
        body, sig = token.rsplit(".", 1)
        good = hmac.new(_key(bot_token), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if time.time() - int(data.get("t", 0)) > TOKEN_TTL_S:
            return None
        return data
    except Exception:
        return None


@router.message(Command("hack"))
async def cmd_hack(message: Message, ui) -> None:  # noqa: ANN001
    try:
        await ui.bot.send_game(message.chat.id, GAME)
    except Exception as exc:
        log.info("games: sendGame failed: %s", exc)
        ui.reply_temp(message.chat.id, NOT_READY)


@router.callback_query(F.game_short_name == GAME)
async def on_play(query: CallbackQuery, ui) -> None:  # noqa: ANN001
    msg = query.message
    token = make_token(ui.settings.bot_token, query.from_user.id, msg.chat.id if msg else None,
                       msg.message_id if msg else None, query.inline_message_id)
    url = wsettings.get().url.rstrip("/") + "/game/#t=" + token
    try:
        await query.answer(url=url)
    except Exception as exc:
        log.warning("games: could not open the game: %s", exc)


async def set_score(bot_token: str, token: str, score: int) -> dict[str, Any]:
    data = read_token(bot_token, token)
    if data is None:
        return {"status": "auth"}
    score = max(0, min(SCORE_MAX, int(score)))
    if UI is None:
        return {"status": "error"}
    kw: dict[str, Any] = {"inline_message_id": data["i"]} if data.get("i") else {"chat_id": data["c"],
                                                                                   "message_id": data["m"]}
    try:
        await UI.bot.set_game_score(user_id=int(data["u"]), score=score, **kw)
        return {"status": "ok", "score": score}
    except Exception as exc:  # BOT_SCORE_NOT_MODIFIED: an older score is higher; that is not an error for the player
        txt = str(exc)
        if "NOT_MODIFIED" in txt.upper():
            return {"status": "kept", "score": score}
        log.info("games: setGameScore failed: %s", exc)
        return {"status": "error"}


async def top(bot_token: str, token: str) -> dict[str, Any]:
    data = read_token(bot_token, token)
    if data is None or UI is None:
        return {"status": "auth", "top": []}
    kw: dict[str, Any] = {"inline_message_id": data["i"]} if data.get("i") else {"chat_id": data["c"],
                                                                                   "message_id": data["m"]}
    try:
        rows = await UI.bot.get_game_high_scores(user_id=int(data["u"]), **kw)
    except Exception as exc:
        log.info("games: getGameHighScores failed: %s", exc)
        return {"status": "error", "top": []}
    me = int(data["u"])
    return {"status": "ok", "top": [{"pos": r.position, "name": " ".join(x for x in (r.user.first_name, r.user.last_name) if x),
                                     "score": r.score, "me": r.user.id == me} for r in rows]}
