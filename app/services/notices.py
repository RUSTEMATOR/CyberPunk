"""One-off lines for the player's window: "the city starts to call you ...", a broken vow.

Listeners of the game-event bus run in the middle of someone else's action, so they cannot draw a
screen. They park a line in the character flag `notices`; the next screen that is built from the
character (the menu, the week report) takes the lines out and shows them once.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.state import FLAG_NOTICES, CharacterState
from app.services.game import ActiveCharacter, save_active

MAX_KEPT = 6  # the oldest lines are dropped, a long absence should not paste a wall of text


def push_state(st: CharacterState, text: str) -> None:
    lines = list(st.flags.get(FLAG_NOTICES) or [])
    lines.append(text)
    st.flags[FLAG_NOTICES] = lines[-MAX_KEPT:]


def push(ch: ActiveCharacter, text: str) -> None:
    push_state(ch.state, text)


def peek(ch: ActiveCharacter) -> list[str]:
    return list(ch.state.flags.get(FLAG_NOTICES) or [])


async def take(session: AsyncSession, ch: ActiveCharacter, tz: str) -> list[str]:
    """The waiting lines; they are removed from the character and the character is saved."""
    lines = peek(ch)
    if lines:
        ch.state.flags.pop(FLAG_NOTICES, None)
        await save_active(session, ch, tz)
    return lines
