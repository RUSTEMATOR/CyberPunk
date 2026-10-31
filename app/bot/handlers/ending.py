"""Ending a life by choice (owner request): from the profile, «Конец пути». Two ways out: leave Night City for good,
or end the life. Both close the character like any death (obituary, graveyard, the heir). Two confirmations."""

from __future__ import annotations

from app.bot import texts
from app.bot.handlers.menu import menu_screen
from app.bot.handlers.week import death_screen
from app.bot.ui.callbacks import Ctx, goto, on_screen, on_text
from app.bot.ui.screen import Button, Screen
from app.db import session as db
from app.services import death, game

WAYS = {"left_city": "Уехать из Найт-Сити", "own_will": "Уйти из жизни", "legend": "Уйти легендой"}


async def chronicle_screen(ctx: Ctx, post: bool = False) -> Screen:
    """The life chronicle by Claude (owner request): once a day, can be posted to the group."""
    from app.bot.ui.cleanup import KIND_NEWS
    from app.narrator import narrator
    from app.services import chronicle

    tz = ctx.ui.settings.timezone
    rows = [[Button("Выложить в группу", "chron_post")], [Button(texts.BACK, "profile")]]
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
    if ch is None:
        return await menu_screen(ctx)
    last = str(ch.state.flags.get("chronicle_last") or "")
    if post:
        if last:
            ctx.ui.post(f"📜 <b>Летопись: {texts.esc(ch.state.name)} {texts.esc(ch.state.surname)}</b>\n\n{texts.esc(last)}",
                        48 * 3600, KIND_NEWS)
        return Screen(("📜 Летопись ушла в группу.\n\n" + texts.esc(last)) if last else "Летописи ещё нет.",
                      [[Button(texts.BACK, "profile")]], name="chronicle")
    if chronicle.used_today(ch, tz) and last:
        return Screen("📜 <b>Летопись</b> (новая будет завтра)\n\n" + texts.esc(last), rows, name="chronicle")
    if not narrator.is_live():
        return Screen("Летописец сейчас молчит: рассказчик отключён.", [[Button(texts.BACK, "profile")]], name="chronicle")
    await ctx.show(Screen("📜 Летописец вспоминает твою жизнь…", [], name="chronicle"))
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        text = await chronicle.write(s, ctx.content, ch, tz) if ch is not None else None
    if not text:
        return Screen("Летописец сбился на полуслове. Попробуй позже.", [[Button(texts.BACK, "profile")]], name="chronicle")
    return Screen("📜 <b>Летопись</b>\n\n" + texts.esc(text), rows, name="chronicle")


@on_screen("chronicle")
async def chronicle_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "chron_post":
        return await chronicle_screen(ctx, post=True)
    return await goto(ctx, "profile")


def ending_screen(legend_ok: bool = False) -> Screen:
    from app.services import legend

    text = ("<b>Конец пути</b>\n"
            "Можно закончить жизнь этого персонажа прямо сейчас. Это навсегда: персонаж уходит в некролог и на кладбище, "
            "а род продолжает наследник, как после любой смерти.\n\n"
            "«Уехать из Найт-Сити» — персонаж бросает всё и уезжает навсегда.\n"
            "«Уйти из жизни» — персонаж решает, что с него хватит.\n"
            "«Уйти легендой» — живым в Зал легенд, когда город уже мал: " + legend.RULE + "\n"
            "«Финалы жизни» — долгая дорога к концу с боями и даром роду: штурм башни, кочевники, орбита, вечный мэр.")
    rows = [[Button(WAYS["left_city"], "pick", "left_city")], [Button(WAYS["own_will"], "pick", "own_will")]]
    if legend_ok:
        rows.append([Button("👑 Уйти легендой", "pick", "legend", style="success")])
        rows.append([Button("🌅 Финалы жизни", "finale")])
    rows.append([Button(texts.BACK, "back")])
    return Screen(text, rows, name="ending")


@on_screen("ending")
async def ending_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await goto(ctx, "profile")
    if action == "finale":  # owner request 25.10: the endings of a life
        return await goto(ctx, "endg", "open")
    if action == "pick" and arg == "legend":
        from app.services import legend

        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            ok = ch is not None and await legend.eligible(s, ctx.content, ch)
            bonus = legend.heir_bonus_text(ctx.content, await legend.legends_of(s, ctx.user_id) + 1)
        if not ok:
            return ending_screen()
        return Screen("<b>Уйти легендой?</b>\nПерсонаж живым уходит в Зал легенд: имя, титулы и итоги жизни остаются в "
                      f"городе навсегда. Род продолжит наследник: {bonus}\nОтменить нельзя.",
                      [[Button("Да, в легенду", "yes", "legend", style="success")], [Button("Нет, ещё поживу", "back")]],
                      name="ending")
    if action == "pick" and arg in WAYS:
        return Screen(f"Точно? «{WAYS[arg]}» нельзя отменить. Персонаж закончит свою историю сейчас.",
                      [[Button("Да, это конец", "yes", arg)], [Button("Нет, ещё поживу", "back")]], name="ending")
    if action == "yes" and arg == "legend":  # owner request 27.10: first the last deed, in the player's own words
        return legend_act_screen()
    if action == "noact":
        return await _finish_legend(ctx, "")
    if action == "yes" and arg in WAYS:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, ctx.content)
            name = ch.state.name
            if arg == "legend":  # owner request 25.10: alive into the Hall of Legends
                from app.services import legend

                if not await legend.eligible(s, ctx.content, ch):
                    return ending_screen()
                await legend.retire(s, ctx.content, ch)
            cause = "legend_alive" if arg == "legend" else arg
            await death.kill(s, ctx.content, ch, cause, ctx.ui.settings.timezone, year=year)
        return death_screen(name, "legend_alive" if arg == "legend" else arg, [])
    return ending_screen()


# -- the last deed of a legend (owner request 27.10, docs/PLAN_LEGEND_SCENE.md) -----------------------------------------

AWAIT_ACT = "legend_act"


def legend_act_screen(note: str | None = None) -> Screen:
    from app.services import legend

    text = ("👑 <b>Последнее дело</b>\n"
            "Легенды уходят не молча. Напиши одним сообщением, каким делом персонаж закончит свою историю: что делает, "
            f"где и ради кого. До {legend.ACT_MAX} знаков. Город превратит это в сцену, её увидит вся беседа, и с ней "
            "имя войдёт в Зал легенд.\n\n"
            "<i>Например: «Поднимаюсь на крышу Арасаки и включаю над городом запись правды о них» или «Последний "
            "заезд по Бэдлендс, а за мной весь МаксТак».</i>")
    if note:
        text += "\n\n" + note
    return Screen(text, [[Button("Пусть город решит сам", "noact")], [Button("Передумать", "back")]],
                  name="ending", awaiting_text=AWAIT_ACT)


@on_text(AWAIT_ACT)
async def legend_act_text(ctx: Ctx, text: str) -> Screen | None:
    from app.services import legend

    act = " ".join(text.split())
    if len(act) < 5:
        return legend_act_screen("Слишком коротко: опиши дело хотя бы парой слов.")
    return await _finish_legend(ctx, act[: legend.ACT_MAX])


async def _finish_legend(ctx: Ctx, act: str) -> Screen:
    from app.services import legend

    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if not await legend.eligible(s, ctx.content, ch):
            return ending_screen()
        year = await game.current_year(s, ctx.content)
        payload = await legend.scene_payload(s, ctx.content, ch, act)
        cid = ch.id
    await ctx.show(Screen("👑 Город складывает легенду о тебе. Это займёт до минуты…", [], name="ending"))
    scene, _ = await legend.write_scene(payload, year, ch.state)  # outside the database session: it takes a while
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None or ch.id != cid:
            return await menu_screen(ctx)
        name = ch.state.name
        await legend.retire(s, ctx.content, ch, scene=scene)
        await legend.post_scene(s, ch, scene)
        await death.kill(s, ctx.content, ch, "legend_alive", ctx.ui.settings.timezone, year=year)
    shown = texts.esc(scene) if len(scene) <= 2600 else \
        texts.esc(scene[:2600].rsplit(" ", 1)[0]) + "… <i>Целиком — в беседе.</i>"
    return death_screen(name, "legend_alive", ["", shown])
