"""A tiny pixel canvas and a PNG writer without dependencies (docs/PLAN_MODELS.md; the bot's Python has no Pillow).

`Canvas` holds RGB pixels, draws rectangles, lines, sprites and a 3×5 digit font; `png(scale)` returns the picture
blown up `scale` times (nearest neighbour, so the pixel art stays sharp). A `Sprite` is a grid of colours or None.
Pure code: no randomness, no I/O.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

RGB = tuple[int, int, int]


def mix(a: RGB, b: RGB, t: float) -> RGB:
    """`a` moved toward `b` by t (0..1)."""
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t), round(a[2] + (b[2] - a[2]) * t))


def shade(c: RGB, f: float) -> RGB:
    """f < 0 darkens toward black, f > 0 lightens toward white."""
    return mix(c, (0, 0, 0), -f) if f < 0 else mix(c, (255, 255, 255), f)


def hexc(s: str) -> RGB:
    s = s.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


@dataclass
class Sprite:
    w: int
    h: int
    px: list[list[RGB | None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.px:
            self.px = [[None] * self.w for _ in range(self.h)]

    def get(self, x: int, y: int) -> RGB | None:
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.px[y][x]
        return None

    def set(self, x: int, y: int, c: RGB | None) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y][x] = c

    def rect(self, x0: int, y0: int, x1: int, y1: int, c: RGB) -> None:
        """Inclusive corners."""
        for y in range(min(y0, y1), max(y0, y1) + 1):
            for x in range(min(x0, x1), max(x0, x1) + 1):
                self.set(x, y, c)

    def line(self, x0: int, y0: int, x1: int, y1: int, c: RGB) -> None:
        for x, y in _bresenham(x0, y0, x1, y1):
            self.set(x, y, c)

    def outline(self, c: RGB) -> None:
        """A one-pixel border around everything drawn (the pixel-art look)."""
        edge: list[tuple[int, int]] = []
        for y in range(self.h):
            for x in range(self.w):
                if self.px[y][x] is not None:
                    continue
                if any(self.get(x + dx, y + dy) is not None for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    edge.append((x, y))
        for x, y in edge:
            self.px[y][x] = c

    def mirrored(self) -> "Sprite":
        return Sprite(self.w, self.h, [list(reversed(row)) for row in self.px])

    def lying(self) -> "Sprite":
        """Turned 90° (a fallen fighter): the head to the left."""
        out = Sprite(self.h, self.w)
        for y in range(self.h):
            for x in range(self.w):
                out.px[self.w - 1 - x][y] = self.px[y][x]
        return out

    def bounds(self) -> tuple[int, int, int, int]:
        xs = [x for y in range(self.h) for x in range(self.w) if self.px[y][x] is not None]
        ys = [y for y in range(self.h) for x in range(self.w) if self.px[y][x] is not None]
        if not xs:
            return 0, 0, 0, 0
        return min(xs), min(ys), max(xs), max(ys)


def _bresenham(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        out.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return out
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


# 3×5 glyphs: digits and a few marks, rows top to bottom, "1" = lit
_FONT = {
    "0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"), "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "111"),
    "+": ("000", "010", "111", "010", "000"), "-": ("000", "000", "111", "000", "000"),
    "!": ("010", "010", "010", "000", "010"), "x": ("000", "101", "010", "101", "000"),
}


class Canvas:
    def __init__(self, w: int, h: int, bg: RGB = (0, 0, 0)) -> None:
        self.w, self.h = w, h
        self.data = bytearray(bytes(bg) * (w * h))

    def get(self, x: int, y: int) -> RGB:
        i = (y * self.w + x) * 3
        return self.data[i], self.data[i + 1], self.data[i + 2]

    def px(self, x: int, y: int, c: RGB, a: float = 1.0) -> None:
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        if a < 1.0:
            c = mix(self.get(x, y), c, a)
        i = (y * self.w + x) * 3
        self.data[i:i + 3] = bytes(c)

    def rect(self, x0: int, y0: int, x1: int, y1: int, c: RGB, a: float = 1.0) -> None:
        for y in range(max(0, min(y0, y1)), min(self.h, max(y0, y1) + 1)):
            for x in range(max(0, min(x0, x1)), min(self.w, max(x0, x1) + 1)):
                self.px(x, y, c, a)

    def vgrad(self, y0: int, y1: int, top: RGB, bottom: RGB) -> None:
        span = max(1, y1 - y0)
        for y in range(y0, y1 + 1):
            self.rect(0, y, self.w - 1, y, mix(top, bottom, (y - y0) / span))

    def line(self, x0: int, y0: int, x1: int, y1: int, c: RGB, a: float = 1.0, dash: int = 0) -> None:
        for n, (x, y) in enumerate(_bresenham(x0, y0, x1, y1)):
            if dash and (n // dash) % 2:
                continue
            self.px(x, y, c, a)

    def blit(self, sp: Sprite, x: int, y: int, dim: float = 0.0, tint: RGB | None = None, tint_a: float = 0.0,
             fade: float = 1.0) -> None:
        """The sprite with its top left corner at (x, y). `dim` darkens, `tint` washes it over, `fade` < 1 shows
        the background through."""
        for sy in range(sp.h):
            row = sp.px[sy]
            for sx in range(sp.w):
                c = row[sx]
                if c is None:
                    continue
                if dim:
                    c = shade(c, -dim)
                if tint is not None and tint_a:
                    c = mix(c, tint, tint_a)
                self.px(x + sx, y + sy, c, fade)

    def text(self, x: int, y: int, s: str, c: RGB, shadow: RGB | None = (0, 0, 0)) -> int:
        """3×5 digits and marks; returns the width drawn."""
        cx = x
        for ch in s:
            g = _FONT.get(ch)
            if g is None:
                cx += 2
                continue
            for gy, row in enumerate(g):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        if shadow is not None:
                            self.px(cx + gx + 1, y + gy + 1, shadow)
                        self.px(cx + gx, y + gy, c)
            cx += 4
        return cx - x

    def png(self, scale: int = 1) -> bytes:
        scale = max(1, int(scale))
        raw = bytearray()
        stride = self.w * 3
        for y in range(self.h):
            row = self.data[y * stride:(y + 1) * stride]
            if scale > 1:
                wide = bytearray()
                for x in range(self.w):
                    wide += row[x * 3:x * 3 + 3] * scale
                row = wide
            line = b"\x00" + bytes(row)
            for _ in range(scale):
                raw += line
        w, h = self.w * scale, self.h * scale

        def chunk(tag: bytes, body: bytes) -> bytes:
            return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))
