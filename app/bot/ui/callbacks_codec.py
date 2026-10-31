"""Callback data: `<screen>:<action>[:<arg>]`, at most 64 bytes (Telegram limit).

The owner is not in the data: the window row found by the message id of the pressed message says
whose it is (PLAN 3.0), and the screen prefix is checked against the window's current screen.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_BYTES = 64
SEP = ":"


@dataclass(frozen=True)
class CallbackData:
    screen: str
    action: str
    arg: str | None = None


def encode(screen: str, action: str, arg: str | None = None) -> str:
    if SEP in screen or SEP in action or not screen or not action:
        raise ValueError(f"bad callback parts: {screen!r} {action!r}")
    data = f"{screen}{SEP}{action}" + (f"{SEP}{arg}" if arg not in (None, "") else "")
    if len(data.encode("utf-8")) > MAX_BYTES:
        raise ValueError(f"callback data over {MAX_BYTES} bytes: {data!r}")
    return data


def decode(data: str | None) -> CallbackData | None:
    if not data:
        return None
    parts = data.split(SEP, 2)
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return CallbackData(parts[0], parts[1], parts[2] if len(parts) == 3 else None)
