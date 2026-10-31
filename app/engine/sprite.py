"""Pixel figures and the fight scene (docs/PLAN_MODELS.md). Pure code: the same input draws the same picture.

`figure(look, gear)` draws a 28×36 fighter facing right: legs, body, arms, head, hair, eyes, the weapon in the front
hand, then a dark outline. `fight_scene` puts the character and allies on the left, the enemies on the right (turned
to face left), their health bars and target numbers, and what happened on the last tick: shots, blows, hacks.
"""

from __future__ import annotations

import random
import zlib
from typing import Any

from app.engine import look as lk
from app.engine.pix import RGB, Canvas, Sprite, mix, shade

W, H = 28, 36
OX = 4                       # the body is drawn 4 px from the left edge, the weapon reaches out to the right
OUTLINE: RGB = (14, 12, 22)
METAL: RGB = (176, 184, 198)
GUN: RGB = (46, 48, 58)
WOOD: RGB = (112, 72, 40)
STEEL: RGB = (214, 226, 236)


def _torso(build: str) -> tuple[int, int]:
    return {"slim": (7, 12), "avg": (6, 13), "heavy": (5, 14)}.get(build, (6, 13))


def figure(look: dict[str, str], gear: lk.Gear | None = None) -> Sprite:
    g = gear or lk.Gear()
    lo = lk.normalize(look)
    sp = Sprite(W, H)
    skin = lk.colour("skin", "chrome" if g.borg and lo["skin"] not in ("synth", "chrome") else lo["skin"])
    hair = lk.colour("hair_c", lo["hair_c"])
    top = lk.colour("top_c", lo["top_c"])
    legs = lk.colour("legs_c", lo["legs_c"])
    neon = lk.colour("accent", lo["accent"])
    if lo["top_c"] == lo["legs_c"] and lo["top"] != "jumpsuit":
        legs = shade(legs, -0.25)
    tl, tr = _torso(lo["build"])
    tl, tr = tl + OX, tr + OX
    cx = 10 + OX  # the line between the legs
    hx0, hx1, hy0, hy1 = 7 + OX, 12 + OX, 4, 10

    def part(x0: int, y0: int, x1: int, y1: int, c: RGB) -> None:
        """A block with light on its left edge and shade on its right (light from the upper left)."""
        sp.rect(x0, y0, x1, y1, c)
        if x1 - x0 >= 2:
            sp.rect(x1, y0, x1, y1, shade(c, -0.3))
            sp.rect(x0, y0, x0, y1, shade(c, 0.12))

    # hood and afro sit behind the head
    if lo["hair"] == "hood":
        sp.rect(hx0 - 1, hy0 - 1, hx1 + 1, hy1 + 1, shade(top, -0.15))
    if lo["hair"] == "afro":
        sp.rect(hx0 - 2, hy0 - 3, hx1 + 2, hy0 + 3, hair)
        sp.rect(hx0 - 1, hy0 - 4, hx1 + 1, hy0 - 4, hair)
    if lo["hair"] in ("long", "dreads"):
        sp.rect(hx0 - 1, hy0, hx0, 15, shade(hair, -0.2))
    if lo["hair"] == "ponytail":
        sp.rect(hx0 - 2, hy0 + 2, hx0 - 1, hy0 + 7, shade(hair, -0.15))

    # legs and boots
    gap = 0 if lo["build"] == "heavy" else 1
    pants = legs
    if lo["top"] == "jumpsuit":
        pants = shade(top, -0.1)
    part(tl + 1, 22, tr - 1, 24, pants)
    part(tl + 1, 25, cx - 1, 31, pants)
    part(cx + gap, 25, tr - 1, 31, pants)
    boot = (30, 28, 34)
    sp.rect(tl + 1, 32, cx - 1, 33, boot)
    sp.rect(cx + gap, 32, tr, 33, boot)
    sp.set(tr, 33, shade(boot, 0.2))

    # body
    bare_arms = lo["top"] in ("vest", "tank")
    sleeve = skin if bare_arms else top
    part(tl, 12, tr, 21, top)
    if lo["top"] == "jacket":
        sp.rect(cx - 1, 13, cx, 21, shade(top, -0.45))
        sp.set(cx - 2, 12, shade(top, 0.3))
        sp.set(cx + 1, 12, shade(top, 0.3))
    elif lo["top"] == "coat":
        part(tl, 22, tl + 2, 28, top)
        part(tr - 2, 22, tr, 28, top)
        sp.rect(cx - 1, 13, cx - 1, 28, shade(top, -0.45))
    elif lo["top"] == "suit":
        sp.rect(cx - 1, 12, cx, 14, (236, 236, 240))
        sp.rect(cx - 1, 13, cx - 1, 19, neon)
    elif lo["top"] == "hoodie":
        sp.rect(tl + 2, 18, tr - 2, 20, shade(top, -0.2))
        sp.set(cx - 1, 13, (230, 230, 230))
        sp.set(cx, 13, (230, 230, 230))
    elif lo["top"] == "armor":
        sp.rect(tl + 1, 13, tr - 1, 17, shade(top, 0.22))
        sp.rect(tl - 1, 12, tl + 1, 13, shade(top, 0.35))
        sp.rect(tr - 1, 12, tr + 1, 13, shade(top, 0.35))
        sp.rect(tl, 21, tr, 21, (40, 38, 44))
        sp.set(cx, 15, neon)
    elif lo["top"] == "vest":
        sp.rect(cx - 1, 13, cx, 21, shade(top, -0.45))
    elif lo["top"] == "tank":
        sp.rect(tl, 12, tr, 12, skin)
    elif lo["top"] == "jumpsuit":
        sp.rect(tl, 16, tr, 16, neon)
    elif lo["top"] == "robe":
        part(tl, 22, tr, 29, top)
        sp.rect(tl, 20, tr, 20, neon)
        sp.line(cx + 2, 12, cx - 2, 20, shade(top, -0.4))
    sp.rect(cx - 1, 11, cx, 11, skin)  # neck

    # arms: the back one hangs, the front one holds the weapon
    back_c = METAL if g.chrome_arms >= 2 else sleeve
    front_c = METAL if g.chrome_arms >= 1 else sleeve
    back_hand = METAL if g.chrome_arms >= 2 else skin
    front_hand = METAL if g.chrome_arms >= 1 else skin
    part(tl - 2, 12, tl - 1, 20, back_c)
    sp.rect(tl - 2, 21, tl - 1, 21, back_hand)
    if g.chrome_arms >= 2:
        sp.set(tl - 2, 15, (255, 255, 255))
    w = g.weapon if g.weapon in lk.WEAPONS else "none"
    gun = w in ("pistol", "smg", "rifle", "shotgun", "heavy", "exotic")
    if gun:
        part(tr + 1, 12, tr + 2, 15, front_c)
        sp.rect(tr + 1, 16, tr + 4, 17, front_c)
        sp.rect(tr + 5, 16, tr + 5, 17, front_hand)
        hx, hy = tr + 5, 16
        if w == "pistol":
            sp.rect(hx, 15, hx + 3, 15, GUN)
            sp.set(hx + 1, 16, GUN)
        elif w == "smg":
            sp.rect(hx - 1, 15, hx + 4, 15, GUN)
            sp.rect(hx + 1, 16, hx + 1, 18, GUN)
        elif w in ("rifle", "exotic"):
            sp.rect(tr - 1, 15, hx + 6, 15, GUN)
            sp.rect(tr - 1, 16, tr, 16, GUN)
            sp.set(hx + 2, 16, GUN)
            if w == "exotic":
                sp.rect(hx - 1, 14, hx + 2, 14, neon)
        elif w == "shotgun":
            sp.rect(tr - 1, 15, hx + 5, 15, GUN)
            sp.rect(tr - 1, 16, tr + 1, 16, WOOD)
            sp.rect(hx + 1, 16, hx + 3, 16, WOOD)
        elif w == "heavy":
            sp.rect(tr - 2, 14, hx + 6, 16, GUN)
            sp.rect(hx + 1, 17, hx + 2, 18, GUN)
            sp.set(hx + 6, 15, neon)
    else:
        part(tr + 1, 12, tr + 2, 19, front_c)
        sp.rect(tr + 1, 20, tr + 2, 20, front_hand)
        if w == "blade":
            sp.line(tr + 2, 20, tr + 8, 8, STEEL)
            sp.line(tr + 3, 20, tr + 9, 8, shade(STEEL, -0.25))
            sp.rect(tr + 1, 21, tr + 3, 21, (60, 50, 50))
        elif w == "blunt":
            sp.line(tr + 2, 21, tr + 7, 11, WOOD)
            sp.rect(tr + 6, 9, tr + 8, 11, GUN)
        elif w == "mantis":
            sp.line(tr + 2, 18, tr + 8, 11, mix(STEEL, neon, 0.35))
            sp.line(tl - 2, 18, tl - 7, 12, mix(STEEL, neon, 0.35))

    # head
    part(hx0, hy0, hx1, hy1, skin)
    sp.set(hx1, hy0 + 4, shade(skin, -0.2))   # the nose turns to the right
    sp.set(hx0 + 3, hy1 - 1, shade(skin, -0.35))
    eye_y = hy0 + 3
    ex1, ex2 = hx0 + 2, hx0 + 4
    eyes = lo["eyes"]
    if eyes == "plain" and g.glow:
        eyes = "glow"
    if eyes == "plain":
        sp.set(ex1, eye_y, (30, 24, 30))
        sp.set(ex2, eye_y, (30, 24, 30))
    elif eyes == "glow":
        sp.set(ex1, eye_y, neon)
        sp.set(ex2, eye_y, neon)
    elif eyes == "kiroshi":
        sp.set(ex1, eye_y, (255, 214, 60))
        sp.set(ex2, eye_y, (255, 214, 60))
        sp.set(ex2 + 1, eye_y - 1, (120, 110, 90))
    elif eyes == "visor":
        sp.rect(hx0, eye_y, hx1, eye_y, neon)
    elif eyes == "optic":
        sp.set(ex1, eye_y, (30, 24, 30))
        sp.rect(ex2, eye_y - 1, ex2, eye_y, (255, 40, 40))
    elif eyes == "shades":
        sp.rect(ex1 - 1, eye_y, ex2 + 1, eye_y, (18, 18, 24))
        sp.set(ex2, eye_y, shade(neon, -0.3))
    face = lo["face"]
    if face == "tattoo":
        sp.set(hx0 + 1, eye_y + 1, shade(neon, -0.35))
        sp.set(hx0 + 1, eye_y + 2, shade(neon, -0.35))
    elif face == "lines":
        sp.rect(hx0 + 1, eye_y + 1, hx0 + 1, eye_y + 3, neon)
        sp.rect(hx1 - 1, eye_y + 1, hx1 - 1, eye_y + 3, neon)
    elif face == "chrome_jaw":
        sp.rect(hx0 + 1, hy1, hx1, hy1, METAL)
        sp.set(hx1, hy1 - 1, METAL)
    elif face == "mask":
        sp.rect(hx0, eye_y + 2, hx1, hy1, (52, 54, 62))
        sp.rect(hx0 + 1, eye_y + 3, hx1 - 1, eye_y + 3, neon)
    elif face == "scar":
        sp.line(ex2 + 1, eye_y - 2, ex2, eye_y + 2, shade(skin, -0.45))

    # hair on top of the head
    h = lo["hair"]
    hs = shade(hair, 0.2)
    if h == "buzz":
        sp.rect(hx0, hy0, hx1, hy0, hair)
    elif h == "short":
        sp.rect(hx0, hy0 - 1, hx1, hy0, hair)
        sp.rect(hx0, hy0 + 1, hx0, hy0 + 2, hair)
        sp.set(hx0 + 2, hy0 - 1, hs)
    elif h == "mohawk":
        sp.rect(hx0 + 2, hy0 - 4, hx0 + 3, hy0, hair)
        sp.set(hx0 + 2, hy0 - 4, hs)
    elif h == "spikes":
        sp.rect(hx0, hy0 - 1, hx1, hy0, hair)
        for i, x in enumerate(range(hx0, hx1 + 1)):
            sp.set(x, hy0 - 2 - (i % 2), hair)
    elif h == "bob":
        sp.rect(hx0 - 1, hy0 - 1, hx1 + 1, hy0 + 1, hair)
        sp.rect(hx0 - 1, hy0 + 2, hx0, hy1 - 1, hair)
        sp.rect(hx1 + 1, hy0 + 2, hx1 + 1, hy1 - 2, hair)
        sp.set(hx0 + 1, hy0 - 1, hs)
    elif h in ("long", "dreads"):
        sp.rect(hx0 - 1, hy0 - 1, hx1, hy0 + 1, hair)
        sp.rect(hx0 - 1, hy0 + 2, hx0, hy1 + 3, hair)
        if h == "dreads":
            for x in range(hx0 - 1, hx1 + 1, 2):
                sp.set(x, hy0 + 1, shade(hair, -0.3))
        sp.set(hx0 + 1, hy0 - 1, hs)
    elif h == "ponytail":
        sp.rect(hx0, hy0 - 1, hx1, hy0 + 1, hair)
        sp.rect(hx0 - 1, hy0, hx0 - 1, hy0 + 2, hair)
    elif h == "bun":
        sp.rect(hx0, hy0 - 1, hx1, hy0, hair)
        sp.rect(hx0 + 1, hy0 - 3, hx0 + 3, hy0 - 2, hair)
    elif h == "afro":
        sp.rect(hx0 - 1, hy0 - 1, hx1 + 1, hy0, hair)
    elif h == "hood":
        sp.rect(hx0 - 1, hy0 - 1, hx1 + 1, hy0, top)
        sp.rect(hx0 - 1, hy0 + 1, hx0 - 1, hy1, top)
        sp.set(hx1 + 1, hy0 + 1, top)

    if g.leader:
        sp.rect(tl, 12, tl + 1, 12, neon)
        sp.rect(tr - 1, 12, tr, 12, neon)
    sp.outline(OUTLINE)
    return sp


# -- the street behind ----------------------------------------------------------------------------------------------


_SKIES = (
    ((18, 10, 40), (120, 34, 96)),     # purple into pink smog
    ((6, 14, 34), (24, 86, 120)),      # night blue into cyan
    ((24, 8, 10), (150, 60, 30)),      # red haze
    ((6, 20, 18), (40, 110, 70)),      # toxic green
    ((10, 10, 26), (70, 50, 120)),     # violet
)
_WINDOWS = ((255, 214, 120), (120, 240, 255), (255, 120, 200), (200, 255, 160))


def street(cv: Canvas, seed: int, ground: int) -> None:
    """Sky, two rows of towers with lit windows and neon, wet asphalt with a reflection."""
    rng = random.Random(seed)
    top, low = _SKIES[seed % len(_SKIES)]
    cv.vgrad(0, ground, top, low)
    win = _WINDOWS[(seed // 7) % len(_WINDOWS)]
    for layer, (dark, lit_p) in enumerate((((30, 24, 48), 0.10), ((12, 10, 20), 0.18))):
        x = -rng.randint(0, 10)
        while x < cv.w:
            bw = rng.randint(10, 24) if layer == 0 else rng.randint(14, 30)
            bh = rng.randint(int(ground * 0.35), int(ground * 0.8)) if layer == 0 else rng.randint(int(ground * 0.2),
                                                                                                    int(ground * 0.6))
            y0 = ground - bh
            cv.rect(x, y0, x + bw - 1, ground, mix(dark, low, 0.15 if layer == 0 else 0.05))
            for wy in range(y0 + 2, ground - 2, 3):
                for wx in range(x + 2, x + bw - 2, 3):
                    if rng.random() < lit_p:
                        cv.px(wx, wy, win, 0.7 if layer == 0 else 1.0)
            if layer == 1 and rng.random() < 0.35:
                nc = _WINDOWS[rng.randint(0, len(_WINDOWS) - 1)]
                sx = x + rng.randint(2, max(2, bw - 4))
                cv.rect(sx, y0 + 3, sx + 1, min(ground - 4, y0 + 3 + rng.randint(6, 14)), nc)
            x += bw + rng.randint(0, 3)
    cv.rect(0, ground + 1, cv.w - 1, cv.h - 1, (22, 22, 30))
    cv.rect(0, ground + 1, cv.w - 1, ground + 1, mix(low, (255, 255, 255), 0.2))
    for _ in range(cv.w // 8):
        px, py = rng.randint(0, cv.w - 1), rng.randint(ground + 3, cv.h - 1)
        cv.rect(px, py, px + rng.randint(2, 6), py, mix((22, 22, 30), low, 0.5))


def place_seed(text: str) -> int:
    return zlib.crc32(text.encode()) & 0x7FFFFFFF


# -- pictures -----------------------------------------------------------------------------------------------------------


def portrait(look: dict[str, str], gear: lk.Gear | None, seed: int) -> Canvas:
    """The editor's picture: the figure on its street."""
    cv = Canvas(64, 44)
    street(cv, seed, 36)
    sp = figure(look, gear)
    cv.blit(sp, 18, 43 - H + 1)
    return cv


def lineup(figs: list[tuple[dict[str, str], lk.Gear]], seed: int) -> Canvas:
    """A group of fighters side by side (the gallery of a gang)."""
    n = max(1, len(figs))
    cv = Canvas(max(96, 12 + n * 26), 52)
    street(cv, seed, 42)
    for i, (look, gear) in enumerate(figs):
        cv.blit(figure(look, gear), 4 + i * 26, 51 - H + 1 - (i % 2) * 3)
    return cv


def _bar(cv: Canvas, x: int, y: int, w: int, hp: int, mx: int) -> None:
    frac = 0 if mx <= 0 else max(0.0, min(1.0, hp / mx))
    col = (70, 220, 90) if frac > 0.5 else (240, 200, 50) if frac > 0.25 else (240, 60, 60)
    cv.rect(x - 1, y - 1, x + w, y + 2, (8, 8, 12))
    cv.rect(x, y, x + w - 1, y + 1, (70, 20, 26))
    if frac > 0:
        cv.rect(x, y, x + max(0, round(w * frac) - 1), y + 1, col)


def fight_scene(fighters: list[dict[str, Any]], events: list[dict[str, Any]], seed: int, cover: bool = False) -> Canvas:
    """The fight: fighters = [{id, side ("player" | "ally" | "enemy"), look, gear, hp, max_hp, out, n (target
    number), target (bool), barrier}], events = the last tick's events (combat log)."""
    cv = Canvas(192, 108)
    ground = 90
    street(cv, seed, ground)
    allies = [f for f in fighters if f["side"] == "ally"]
    enemies = [f for f in fighters if f["side"] == "enemy"]
    player = next((f for f in fighters if f["side"] == "player"), None)
    pos: dict[str, tuple[int, int]] = {}
    # spots: (x of the sprite's left edge, y of the feet)
    if player is not None:
        pos[player["id"]] = (30, 104)
    for i, a in enumerate(allies[:4]):
        pos[a["id"]] = ((4, 52, 16, 64)[i], 97)
    n = len(enemies)
    front = (n + 1) // 2 if n > 3 else n
    xs_front = _spread(front, 104, 164)
    xs_back = _spread(n - front, 116, 172)
    for i, e in enumerate(enemies):
        pos[e["id"]] = (xs_front[i], 104) if i < front else (xs_back[i - front], 97)
    order = sorted(fighters, key=lambda f: pos.get(f["id"], (0, 0))[1])
    hurt = {ev.get("to") for ev in events if ev.get("k") == "hit" or (ev.get("k") == "roll" and ev.get("hit"))}
    sprites: dict[str, tuple[Sprite, int, int]] = {}
    for f in order:
        if f["id"] not in pos or f.get("out") == "fled":
            continue
        x, feet = pos[f["id"]]
        sp = figure(f["look"], f["gear"])
        if f["side"] == "enemy":
            sp = sp.mirrored()
        back = feet < 104
        if f.get("out"):
            lay = sp.lying()
            cv.blit(lay, max(0, min(cv.w - lay.w, x - 2)), feet - lay.h + 1, dim=0.45, tint=(90, 20, 24) if f.get("out") == "killed" else None,
                    tint_a=0.25)
            continue
        cv.blit(sp, x, feet - H + 1, dim=0.25 if back else 0.0, tint=(255, 255, 255) if f["id"] in hurt else None,
                tint_a=0.35)
        sprites[f["id"]] = (sp, x, feet - H + 1)
        if f.get("barrier"):
            bx0, by0, bx1, by1 = sp.bounds()
            for yy in range(by0 - 1, by1 + 2):
                cv.px(x + (bx0 - 2 if f["side"] != "enemy" else bx0 - 2), feet - H + 1 + yy, (120, 230, 255), 0.5)
                cv.px(x + bx1 + 2, feet - H + 1 + yy, (120, 230, 255), 0.5)
    if cover and player is not None and player["id"] in pos and not player.get("out"):
        x, feet = pos[player["id"]]
        cv.rect(x + 18, feet - 9, x + 27, feet, (92, 74, 52))
        cv.rect(x + 18, feet - 9, x + 27, feet - 9, (130, 106, 76))
        cv.line(x + 18, feet - 8, x + 27, feet, (66, 52, 36))
    # bars and numbers over the heads
    for f in fighters:
        if f["id"] not in sprites:
            continue
        sp, x, y = sprites[f["id"]]
        bx0, by0, bx1, _ = sp.bounds()
        mid = x + (bx0 + bx1) // 2
        _bar(cv, mid - 6, y + by0 - 4, 12, int(f.get("hp", 0)), int(f.get("max_hp", 1)))
        if f["side"] == "enemy" and f.get("n"):
            col = (255, 226, 60) if f.get("target") else (220, 220, 230)
            cv.text(mid - 2 * len(str(f["n"])), y + by0 - 11, str(f["n"]), col)
            if f.get("target"):
                cv.rect(mid - 1, y + by0 - 14, mid + 1, y + by0 - 14, col)
                cv.px(mid, y + by0 - 13, col)
        if f["side"] == "player":
            cv.px(mid, y + by0 - 7, (120, 255, 160))
            cv.rect(mid - 1, y + by0 - 8, mid + 1, y + by0 - 8, (120, 255, 160))
    # the last tick
    for ev in events:
        k = ev.get("k")
        if k not in ("roll", "hack"):
            continue
        a, d = ev.get("by"), ev.get("to")
        if a not in sprites or d not in sprites:
            continue
        sa, ax, ay = sprites[a]
        sd, dx, dy = sprites[d]
        enemy_shot = a.startswith("e")
        a0, a1 = (ax + 3, ay + 15) if enemy_shot else (ax + W - 2, ay + 15)
        d0, d1 = dx + W // 2, dy + 16
        if k == "hack":
            pts = 6
            px, py = a0, ay + 7
            for i in range(1, pts + 1):
                nx = a0 + (d0 - a0) * i // pts
                ny = (ay + 7) + ((dy + 7) - (ay + 7)) * i // pts + (2 if i % 2 else -2)
                cv.line(px, py, nx, ny, (90, 240, 255), 0.6)
                px, py = nx, ny
            if ev.get("ok"):
                for sx, sy in ((-2, -1), (2, 1), (0, -3), (1, 2)):
                    cv.px(d0 + sx, dy + 7 + sy, (180, 255, 255))
            continue
        hit = bool(ev.get("hit"))
        if ev.get("kind") == "ranged":
            if hit:
                cv.line(a0, a1, d0, d1, (255, 240, 170), 0.9)
                cv.px(a0, a1, (255, 255, 255))
                for sx, sy in ((0, 0), (1, -1), (-1, 1), (1, 1)):
                    cv.px(d0 + sx, d1 + sy, (255, 60, 60))
            else:
                miss_y = d1 - 8
                cv.line(a0, a1, d0, miss_y, (200, 200, 210), 0.5, dash=2)
        else:
            col = (255, 255, 255) if hit else (150, 150, 160)
            for i in range(5):
                cv.px(d0 - 2 + i, d1 - 4 + i, col, 0.95 if hit else 0.5)
            if hit:
                cv.px(d0 + 1, d1, (255, 60, 60))
    return cv


def _spread(n: int, x0: int, x1: int) -> list[int]:
    if n <= 0:
        return []
    if n == 1:
        return [(x0 + x1) // 2]
    step = (x1 - x0) / (n - 1)
    return [round(x0 + i * step) for i in range(n)]
