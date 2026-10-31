"""Screens of the world's beats in a player's window (DESIGN.md 9a, personal time): the era the character's date walked
into and the canon events or generated beats it reached."""

from __future__ import annotations

from app.bot import texts, texts_world
from app.bot.ui import style
from app.content.loader import Content
from app.services.worldfx import Beat


def era_text(content: Content, era_key: str) -> str:
    """What the player reads when his date enters an era: its name, years, a line and what it does to implant prices."""
    era = content.eras[era_key]
    lines = [texts_world.ERA_CHANGED.format(
        name=texts.esc(era.name), from_year=era.from_year, to_year=era.to_year, player_text=texts.esc(era.player_text),
    )]
    if abs(era.chrome_price_mult - 1.0) > 1e-9:
        lines.append(texts_world.ERA_PRICES_UP.format(mult=era.chrome_price_mult))
    else:
        lines.append(texts_world.ERA_PRICES_SAME)
    if abs(era.chrome_humanity_mult - 1.0) > 1e-9:
        lines.append(texts_world.ERA_HUMANITY.format(mult=era.chrome_humanity_mult))
    return "\n".join(lines)


def beat_window_text(content: Content, beats: list[Beat], era_keys: list[str]) -> str:
    """The window screen shown before the week report when the new date reached beats or an era."""
    parts: list[str] = []
    from app.bot import texts_glossary as G  # owner request 24.10: notes on unknown words, what changes in the game

    for beat in beats:
        block = (texts_world.BEAT_WINDOW_TITLE.format(year=beat.year, title=texts.esc(beat.title)) + "\n"
                 + style.quote(texts.esc(beat.text)))
        extra = [x for x in [G.effects_note(content, beat.effects), *G.notes(f"{beat.title} {beat.text}", limit=3)] if x]
        if extra:
            block += "\n" + "\n".join(extra)
        parts.append(block)
    parts.extend(era_text(content, key) for key in era_keys)
    return "\n\n".join(parts)
