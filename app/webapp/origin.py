"""Where the update that changes a window came from. "webapp": the player already sees the screen in the Mini App, so
the edit of the group window waits for headroom in the chat budget and never delays other players."""

from __future__ import annotations

import contextvars

ORIGIN: contextvars.ContextVar[str] = contextvars.ContextVar("webapp_window_origin", default="bot")
