"""Screens that exist outside any feature: the error screen."""

from __future__ import annotations

from app.bot import texts
from app.bot.ui.screen import Button, Screen


def error_screen() -> Screen:
    """Shown in the player's window when a handler failed. Its handler (screen 'error') leads to the menu."""
    return Screen(texts.ERROR_TITLE, [[Button(texts.MENU, "menu")]], name="error")
