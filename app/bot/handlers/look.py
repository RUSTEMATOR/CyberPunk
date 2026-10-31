"""«🧍 Внешность» (owner request 26.10: «редактор персонажа, как в Зеленогорье… нагенерить противников и показывать
модельки персонажей во время боя»; docs/PLAN_MODELS.md).

`look`: the character's figure over the window and its parts; a part opens `lookpart`, where every option is a button
and the figure changes at once. `lookgal`: the fighters of a gang or a corporation of the character's city and year.
The look lives in the character's flag `look`; a character who never opened the editor has a stable random one.
"""

from __future__ import annotations

import random

from app.bot import texts
from app.bot.handlers.menu import menu_screen
from app.bot.ui.callbacks import Ctx, goto, on_screen
from app.bot.ui.screen import Button, Screen, pack
from app.db import session as db
from app.engine import combat as cb
from app.engine import look as lk
from app.services import game
from app.services import look as svc
from app.services.game import ActiveCharacter

KEEP = "look"


def _picture(ctx: Ctx, ch: ActiveCharacter, screen: Screen) -> Screen:
    try:
        screen.picture = svc.portrait_png(ctx.content, ch)
    except Exception:  # noqa: BLE001 - the editor works without the picture
        screen.picture = None
    screen.picture_keep = KEEP
    return screen


def look_screen(ctx: Ctx, ch: ActiveCharacter, note: str | None = None) -> Screen:
    lo = svc.look_of(ch)
    gear = lk.player_gear(ctx.content, ch.state)
    lines = [f"🧍 <b>Внешность: {texts.esc(ch.state.name)} {texts.esc(ch.state.surname)}</b>".replace("  ", " "),
             "Так тебя видно в бою и в редакторе. Хром рисуется сам: импланты рук дают хромированные руки, оптика "
             "светится, в руке то оружие, что экипировано.", ""]
    for key, name, _ in lk.PARTS:
        lines.append(f"{name}: <b>{lk.label(key, lo[key])}</b>")
    weapon = {"none": "пустые руки", "pistol": "пистолет", "smg": "пистолет-пулемёт", "rifle": "винтовка",
              "shotgun": "дробовик", "heavy": "тяжёлый ствол", "blade": "клинок", "blunt": "дубина",
              "mantis": "клинки-богомолы", "exotic": "экзотика"}.get(gear.weapon, gear.weapon)
    chrome = "" if not gear.chrome_arms else ", хромированные руки"
    lines += ["", f"В руках: {weapon}{chrome}" + (", светящаяся оптика" if gear.glow else "") + "."]
    on = svc.fight_on(ctx.content, ch)
    lines.append("Картинка боя над окном: " + ("показывается." if on else "выключена."))
    if not ctx.content.balance.look.fight_picture:
        lines.append("Сейчас картинки боя выключены для всех.")
    if note:
        lines += ["", note]
    short = {"build": "Тело", "skin": "Кожа", "hair": "Волосы", "hair_c": "Цв. волос", "eyes": "Глаза", "face": "Лицо",
             "top": "Одежда", "top_c": "Цв. верха", "legs_c": "Цв. низа", "accent": "Неон"}
    rows = pack([Button(short[k], "part", k) for k in lk.PART_KEYS], ctx.content.balance.ui.button_chars, max_per_row=3)
    rows.append([Button("🎲 Случайно", "rand"), Button("Картинка боя: " + ("вкл" if on else "выкл"), "fight")])
    rows.append([Button("👥 Кто на улицах", "gal")])
    rows.append([Button(texts.BACK, "profile"), Button(texts.MENU, "menu")])
    return _picture(ctx, ch, Screen("\n".join(lines), rows, name="look"))


def part_screen(ctx: Ctx, ch: ActiveCharacter, part: str) -> Screen:
    lo = svc.look_of(ch)
    name = lk.PART_NAMES.get(part, part)
    opts = next((o for k, _, o in lk.PARTS if k == part), [])
    lines = [f"🧍 <b>{name}</b>", f"Сейчас: <b>{lk.label(part, lo.get(part, ''))}</b>. Нажми вариант, картинка сменится сразу."]
    btns = [Button(("• " if lo.get(part) == key else "") + lab, "set", f"{part}.{key}") for key, lab in opts]
    rows = pack(btns, ctx.content.balance.ui.button_chars, max_per_row=3)
    rows.append([Button("Готово", "open", style="success")])
    return _picture(ctx, ch, Screen("\n".join(lines), rows, name="lookpart", state={"part": part}))


async def _save(ctx: Ctx, ch: ActiveCharacter, look: dict[str, str]) -> None:
    async with db.session_scope() as s:
        cur = await game.get_active_character(s, ctx.user_id)
        if cur is None or cur.id != ch.id:
            return
        cur.state.flags[lk.FLAG] = lk.normalize(look)
        await game.save_active(s, cur, ctx.ui.settings.timezone)
        ch.state.flags[lk.FLAG] = cur.state.flags[lk.FLAG]


async def _ch(ctx: Ctx) -> ActiveCharacter | None:
    async with db.session_scope() as s:
        return await game.get_active_character(s, ctx.user_id)


@on_screen("look")
async def look_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "profile":
        return await goto(ctx, "profile", "open")
    ch = await _ch(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action == "part" and arg in lk.PART_KEYS:
        return part_screen(ctx, ch, arg)
    if action == "rand":
        lo = lk.random_look(random.Random())
        await _save(ctx, ch, lo)
        return look_screen(ctx, ch, "Новая внешность. Не нравится — нажми ещё раз или поправь по частям.")
    if action == "fight":
        async with db.session_scope() as s:
            cur = await game.get_active_character(s, ctx.user_id)
            if cur is not None:
                if cur.state.flags.get(lk.FLAG_FIGHT_OFF):
                    cur.state.flags.pop(lk.FLAG_FIGHT_OFF, None)
                else:
                    cur.state.flags[lk.FLAG_FIGHT_OFF] = True
                await game.save_active(s, cur, ctx.ui.settings.timezone)
                ch = cur
        return look_screen(ctx, ch)
    if action == "gal":
        return await gallery_screen(ctx, ch, None, 0)
    return look_screen(ctx, ch)


@on_screen("lookpart")
async def lookpart_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch = await _ch(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action == "set" and arg and "." in arg:
        part, _, value = arg.partition(".")
        if part in lk.PART_KEYS:
            lo = svc.look_of(ch)
            lo[part] = value
            await _save(ctx, ch, lo)
            return part_screen(ctx, ch, part)
    return look_screen(ctx, ch)


# -- the gallery ----------------------------------------------------------------------------------------------------


def _factions(ctx: Ctx, ch: ActiveCharacter) -> list[tuple[str, str]]:
    """Gangs of the character's city and the corporations of the year that have fighters."""
    content = ctx.content
    year = ch.state.current_year
    d0 = content.districts.get(ch.state.district)
    city = d0.city if d0 is not None else "nc"
    gangs_avail = content.available("gangs", year)
    seen: list[str] = []
    for d in content.available("districts", year).values():
        if d.city != city:
            continue
        for g in d.gangs:
            if g in gangs_avail and g not in seen and cb._faction_pool(content, g, year):  # noqa: SLF001
                seen.append(g)
    out = [(g, content.gangs[g].name) for g in seen]
    for k, c in content.available("corps", year).items():
        if cb._faction_pool(content, k, year):  # noqa: SLF001
            out.append((k, c.name))
    return out


async def gallery_screen(ctx: Ctx, ch: ActiveCharacter, faction: str | None, seed: int) -> Screen:
    facs = _factions(ctx, ch)
    names = dict(facs)
    if faction not in names and facs:
        faction = facs[0][0]
    lines = ["👥 <b>Кто на улицах</b>",
             "Бойцы банд и корпораций твоего города в твоём году — так они выглядят в бою. «Ещё» показывает других."]
    if faction:
        fac = ctx.content.gangs.get(faction) or ctx.content.corps.get(faction)
        lines += ["", f"<b>{texts.esc(names[faction])}</b>"]
        desc = getattr(fac, "description", "") or ""
        if desc:
            lines.append(texts.esc(desc))
    rows = pack([Button(("• " if k == faction else "") + n, "fac", k) for k, n in facs],
                ctx.content.balance.ui.button_chars, max_per_row=3)
    rows.append([Button("🎲 Ещё", "more", f"{faction}.{seed + 1}"), Button(texts.BACK, "open")])
    screen = Screen("\n".join(lines), rows, name="lookgal", state={"fac": faction, "seed": seed})
    if faction:
        try:
            screen.picture = svc.gallery_png(ctx.content, faction, ch.state.current_year, seed)
        except Exception:  # noqa: BLE001
            screen.picture = None
    screen.picture_keep = KEEP
    return screen


@on_screen("lookgal")
async def lookgal_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch = await _ch(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action == "fac" and arg:
        return await gallery_screen(ctx, ch, arg, 0)
    if action == "more" and arg:
        fac, _, n = arg.rpartition(".")
        return await gallery_screen(ctx, ch, fac or None, int(n) if n.isdigit() else 0)
    return look_screen(ctx, ch)
