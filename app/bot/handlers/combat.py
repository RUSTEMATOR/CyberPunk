"""The fight in the window (PLAN 5.6): one screen per tick.

Screens: `cm` (the tick: target, attacks, implants, cover, exits), `cmhack` (the program menu),
`cmexit` (pay, talk, flee, surrender), `cmend` (the result). Every button carries `tick.target[.extra]`,
so a stale press (an old window, a double click) only redraws the screen. The fight lives on the
character (`flags["combat"]`), so a closed window resumes through `combat_resume_screen`.
"""

from __future__ import annotations

import random
from typing import Any

from app.bot import texts_combat as T
from app.bot import combat_fx, qte_modes, views, views_combat
from app.bot.handlers.menu import menu_screen
from app.bot.handlers.week import death_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, on_screen
from app.bot.ui.screen import Button, Screen, pack
from app.content.loader import Content
from app.db import session as db
from app.engine import checks as engine_checks
from app.engine import combat as cb
from app.engine import events as engine_events
from app.engine import exits
from app.engine.checks import CheckResult
from app.services import combat as svc
from app.services import events as ev_service
from app.services import game
from app.services.game import ActiveCharacter

esc = T.esc


# -- helpers ---------------------------------------------------------------------------------------------------


def _arg(cs: cb.CombatState, target: int, *extra: str) -> str:
    return ".".join([str(cs.tick), str(target), *extra])


def _parse(arg: str | None) -> tuple[int, int, list[str]] | None:
    if not arg:
        return None
    parts = arg.split(".")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].lstrip("-").isdigit():
        return None
    return int(parts[0]), int(parts[1]), parts[2:]


def _target(cs: cb.CombatState, target: int) -> int:
    """A valid target: the chosen enemy while it is in the fight, else the first one."""
    if 0 <= target < len(cs.enemies) and cs.enemies[target].active:
        return target
    for i, _ in cs.active_enemies():
        return i
    return 0


def _with_fx(content: Content, cs: cb.CombatState, screen: Screen, extra: list[dict[str, Any]] | None = None) -> Screen:
    """Owner request 30.10: the Mini App plays the player's fresh chrome (Сандевистан, Берсерк...) as a screen effect."""
    fx = combat_fx.recent(content, cs) + list(extra or [])
    if fx:
        screen.state = {**(screen.state or {}), "fx": fx[-3:]}
    return screen


def _notes(step: svc.Step | None) -> list[str]:
    if step is None:
        return []
    out = [T.ERRORS.get(x, x) for x in step.lines]
    if step.exit is not None:
        out = exit_lines(step.exit) + out
    return out


def exit_lines(info: svc.ExitInfo) -> list[str]:
    """What an exit attempt did, as lines: the check, then the result."""
    lines: list[str] = []
    if info.check is not None:
        lines.append(views.check_line(info.check))
    if info.kind == exits.SURRENDER:
        lines.append(T.SURRENDER_LINES.get(info.note, ""))
    elif info.ok:
        lines.append(T.EXIT_LINES[{exits.BRIBE: "bribe_ok", exits.TALK: "talk_ok"}.get(info.kind, "flee_ok")])
    else:
        key = {exits.BRIBE: "bribe_fail", exits.TALK: "talk_fail"}.get(info.kind, "flee_fail")
        lines.append(T.EXIT_LINES[key].format(taken=T.EXIT_TAKEN if info.note == "taken" else T.EXIT_KEPT))
    lines.extend(info.lines)
    return [x for x in lines if x]


# -- the tick screen -----------------------------------------------------------------------------------------------


def _with_scene(content: Content, ch: ActiveCharacter, cs: cb.CombatState, target: int, screen: Screen) -> Screen:
    """Owner request 26.10 (docs/PLAN_MODELS.md): the fight as a picture over the window."""
    from app.services import look as look_svc

    screen.picture = look_svc.fight_png(content, ch, cs, target)
    screen.picture_keep = "cm"
    screen.picture_private = True  # owner report 27.10: in the group the scene is shown only on «🖼 Сцена»
    return screen


def combat_screen(content: Content, ch: ActiveCharacter, cs: cb.CombatState, target: int, notes: list[str] | None = None) -> Screen:
    target = _target(cs, target)
    screen = _with_scene(content, ch, cs, target, _combat_screen(content, ch, cs, target, notes))
    if screen.name == "cm":  # owner request 30.10: the Mini App's quick-reaction game of every weapon at hand
        screen.state = {**(screen.state or {}), "wq": qte_modes.modes(content, cs)}
        if _fight3d_open(content, ch, cs):  # owner request 31.10: the same fight in first person (docs/PLAN_FIGHT3D.md)
            screen.keyboard.append([Button("🎮 Бой в 3D", "f3d", _arg(cs, target))])
    return screen


def _fight3d_open(content: Content, ch: ActiveCharacter, cs: cb.CombatState) -> bool:
    from app.engine import realtime
    from app.services import pally, unity3d

    f = content.balance.fight3d
    if not f.enabled or cs.reinforced or realtime.text_only(cs) or unity3d.current() is None:
        return False
    return not f.admins_only or (pally.UI is not None and pally.UI.is_admin(ch.player_tg_id))


def _combat_screen(content: Content, ch: ActiveCharacter, cs: cb.CombatState, target: int, notes: list[str] | None = None) -> Screen:
    p = cs.player
    text = views_combat.combat_text(content, cs, target, notes)
    chars = content.balance.ui.button_chars
    rows: list[list[Button]] = []
    flee_dv = next((o.dv for o in svc.exit_options(content, ch, cs) if o.kind == exits.FLEE), None)
    if flee_dv is not None:
        text += "\n" + T.CM_FLEE_DV.format(dv=flee_dv)
    flee_btn = Button(style.label("flee", T.B_FLEE), "flee", _arg(cs, target))
    if cs.reinforced:
        rows.append([flee_btn, Button(style.label("surrender", T.CX_SURRENDER), "surrender", _arg(cs, target))])
        return Screen(text, rows, name="cm", state={"tick": cs.tick, "target": target})

    alive = cs.active_enemies()
    if len(alive) > 1:
        rows.extend(pack([
            Button(T.B_TARGET.format(mark=T.CM_MARK_TARGET if i == target else "", n=i + 1), "tg", _arg(cs, i))
            for i, e in alive
        ], chars, max_per_row=3))
    main: list[Button] = []
    # owner report 23.10: one button per weapon at hand, so the player knows and picks what fires or cuts
    guns = [a for a in p.arsenal.values() if a.kind == cb.RANGED]
    blades = [a for a in p.arsenal.values() if a.kind == cb.MELEE] if not cs.nonlethal else []

    def _wname(a: cb.Attack) -> str:
        w = content.weapons.get(a.source)
        return w.name if w else a.source

    if guns:
        for a in guns:
            main.append(Button(style.label("weapon", _wname(a)), "shoot", _arg(cs, target, a.source)))
    elif cb.RANGED in p.attacks:
        main.append(Button(style.label("weapon", T.B_SHOOT), "shoot", _arg(cs, target)))
    for a in blades:
        main.append(Button(style.label("punch", _wname(a)), "melee", _arg(cs, target, a.source)))
    if not blades:
        main.append(Button(style.label("punch", T.B_MELEE), "melee", _arg(cs, target)))
    rows.extend(pack(main, chars, max_per_row=2))
    rows.append([Button(style.label("cover", T.B_COVER), "cover", _arg(cs, target)), flee_btn])
    if cs.ctx.get("elite") and (cs.ctx["elite"] or {}).get("vip"):  # owner request 25.10: protect the person
        from app.engine import elite

        if elite.vip_alive(cs):
            rows.append([Button("🛡 Прикрыть охраняемого", "guard", _arg(cs, target))])
    if cs.med_max:  # owner request 24.10: the med injector, charges come back during the fight
        left = cb.med_left(cs, content)
        lbl = T.B_HEAL.format(left=left, max=cs.med_max)
        if not left:
            lbl += f" · {cb.med_next(cs, content)} х."
        rows.append([Button("💉 " + lbl, "heal", _arg(cs, target), style="success" if left else None)])
    rf = cs.ctx.get("reinf")  # owner request 24.10: called help
    if rf and not rf.get("done"):
        rows.append([Button(f"📞 {rf.get('name', '')}: через {max(0, int(rf.get('at', 0)) - cs.tick)} х."[:30], "reinf",
                            _arg(cs, target))])
    elif not rf and not cs.ctx.get("solo"):
        rows.append([Button("📞 Подкрепление", "reinf", _arg(cs, target))])
    rows.append([Button(style.label("nonlethal", T.B_NONLETHAL.format(state=T.CM_ON if cs.nonlethal else T.CM_OFF)),
                        "nl", _arg(cs, target))])
    imp_buttons: list[Button] = []
    for imp in p.implants:
        spec = cb.spec_of(p, imp, content)
        label = spec.button
        if spec.mode == "duration":
            if imp.running and spec.can_cancel:
                imp_buttons.append(Button(T.B_IMPLANT_OFF.format(button=label), "imp", _arg(cs, target, imp.key)))
            elif not imp.running and not cb.activation_block(p, imp, content):
                imp_buttons.append(Button(T.B_IMPLANT_ON.format(button=label, charge=imp.charge), "imp", _arg(cs, target, imp.key)))
        elif spec.mode == "reactive" and spec.trigger_toggle:
            imp_buttons.append(Button(
                T.B_AUTO.format(button=label, state=T.CM_ON if imp.auto else T.CM_OFF), "auto", _arg(cs, target, imp.key)))
        elif spec.mode == "shot" and not cs.shot_used:
            for n in range(1, spec.effects.targets + 1):
                if not cb.shot_block(p, imp, content, n):
                    imp_buttons.append(Button(T.B_SHOT.format(button=label, n=n), "shot", _arg(cs, target, imp.key, str(n))))
    rows.extend(pack(imp_buttons, chars, max_per_row=2))
    if cs.ctx.get("pallies"):  # owner request 26.10: the hired players take orders
        rows.append([Button("🎖 Отряд: приказы", "squad", _arg(cs, target))])
    last: list[Button] = []
    if p.quickhacks and p.ram_max:
        last.append(Button(style.label("hack", T.B_HACK), "hack", _arg(cs, target)))
    last.append(Button(style.label("exit", T.B_EXITS), "exits", _arg(cs, target)))
    from app.engine import ab_power

    fams = [f for f in ab_power.families(ch.state, content) if (ab_power.spec(content, f) or None) is not None
            and ab_power.spec(content, f).fight] if content.balance.chrome_power.enabled else []
    if fams:  # owner request 27.10: the chrome abilities as free actions
        ready = sum(1 for f in fams if not ab_power.ready_in(cs, f)) if not ab_power.used_this_tick(cs) else 0
        last.insert(0, Button(f"⚙️ Хром ({ready})", "ab", _arg(cs, target), style="success" if ready else None))
    from app.services import look as look_svc

    if look_svc.fight_on(content, ch):  # owner report 27.10: in the group the scene comes only on request, for a minute
        last.append(Button("🖼 Сцена", "scene", _arg(cs, target)))
    rows.append(last)
    return Screen(text, rows, name="cm", state={"tick": cs.tick, "target": target})


def squad_screen(content: Content, cs: cb.CombatState, target: int) -> Screen:
    """Owner request 26.10: the hired players at the side and their skills; an order works on the next tick."""
    from app.engine import pally as pally_eng

    pallies = cs.ctx.get("pallies") or {}
    orders = cs.ctx.get("orders") or {}
    lines = ["🎖 <b>Отряд</b>", "Нанятые игроки бьются своими навыками. Без приказа — сами: "
             "«как играет» по их собственным боям или «по очереди». Приказ действует на следующий ход."]
    rows: list[list[Button]] = []
    for a in cs.allies:
        rec = pallies.get(a.id)
        if rec is None:
            continue
        mode = "как играет" if rec.get("mode") != pally_eng.TURNS else "по очереди"
        state = f"{max(0, a.hp)}/{a.max_hp}" if a.active else "выбит"
        lines.append("")
        lines.append(f"<b>{esc(a.name)}</b> · {state} · сам: {mode}")
        if rec.get("last"):
            lines.append(f"Последнее: {esc(pally_eng.label(content, str(rec['last'])))}")
        if orders.get(a.id):
            lines.append(f"Приказ: {esc(pally_eng.label(content, str(orders[a.id])))}")
        if not a.active:
            continue
        can = set(pally_eng.skills(cs, a, content))
        btns = [Button(f"{str(rec.get('name', '')).split(' ')[0][:10]}: {pally_eng.label(content, sk)}"[:28], "ord",
                       _arg(cs, target, a.id, str(i)))
                for i, sk in enumerate(pally_eng.all_skills(a, content)) if sk in can]
        rows.extend(pack(btns, content.balance.ui.button_chars, max_per_row=2))
    rows.append([Button(T.BACK, "tg", _arg(cs, target))])
    return Screen("\n".join(lines), rows, name="cm", state={"tick": cs.tick, "target": target})


def ab_screen(content: Content, ch: ActiveCharacter, cs: cb.CombatState, target: int,
              notes: list[str] | None = None) -> Screen:
    """Owner request 27.10: the chrome abilities in the fight — a free action, one a tick, each with its cooldown."""
    from app.engine import ab_power

    target = _target(cs, target)
    fams = ab_power.families(ch.state, content)
    lines = ["⚙️ <b>Хром в бою</b>",
             "Способность не тратит ход: применил — и действуешь дальше. Одна за ход, у каждой свой откат.", ""]
    if cs.enemies:
        lines.append(f"Цель: <b>{esc(cs.enemies[target].name)}</b>")
    if ab_power.used_this_tick(cs):
        lines.append("<i>В этом ходу способность уже была.</i>")
    buttons: list[Button] = []
    for fam, (rank, key) in sorted(fams.items(), key=lambda kv: content.balance.chrome_abilities[kv[1][1]].name):
        sp = ab_power.spec(content, fam)
        if sp is None or not sp.fight:
            continue
        ab = content.balance.chrome_abilities[key]
        wait = ab_power.ready_in(cs, fam)
        state = "готово" if not wait else ("уже использовано" if wait > 99 else f"через {wait} х.")
        lines.append(f"{ab.icon} <b>{esc(ab.name)}</b> · ранг {rank} · {state}")
        lines.append(f"   {esc(ab_power.fight_line(content, fam, rank))}")
        if not wait and not ab_power.used_this_tick(cs):
            buttons.append(Button(f"{ab.icon} {ab.name}"[:24], "use", _arg(cs, target, fam)))
    if notes:
        lines += ["", "<i>" + " ".join(notes) + "</i>"]
    rows = pack(buttons, content.balance.ui.button_chars, max_per_row=2)
    rows.append([Button(T.BACK, "back", _arg(cs, target))])
    return Screen("\n".join(lines), rows, name="cmab", state={"tick": cs.tick, "target": target})


def hack_screen(content: Content, cs: cb.CombatState, target: int) -> Screen:
    target = _target(cs, target)
    p = cs.player
    foe = cs.enemies[target]
    lines = [T.HACK_TITLE.format(target=esc(foe.name), ram=p.ram, max=p.ram_max), ""]
    buttons: list[Button] = []
    for key in p.quickhacks:
        qh = content.quickhacks.get(key)
        if qh is None:
            continue
        why = cb.hack_block(p, key, foe, content)
        lines.append(T.HACK_LINE.format(name=esc(qh.name), ram=cb.hack_cost(p, qh), dv=cb.hack_dv(qh, foe)))
        if not why:
            buttons.append(Button(T.HACK_BUTTON.format(name=qh.name), "go", _arg(cs, target, key)))
        elif why == "no_chrome":
            lines[-1] += " · " + T.HACK_NO_CHROME
    if not buttons:
        lines.append(T.HACK_NONE)
    rows = pack(buttons, content.balance.ui.button_chars, max_per_row=2)
    rows.append([Button(T.BACK, "back", _arg(cs, target))])
    return Screen("\n".join(lines), rows, name="cmhack", state={"tick": cs.tick, "target": target})


def exits_screen(content: Content, ch: ActiveCharacter, cs: cb.CombatState, target: int) -> Screen:
    lines = [T.CX_TITLE.format(faction=esc(views_combat.faction_name(content, cs.faction)))]
    if cb.pack_half_lost(cs, content):
        lines.append(T.CX_HALF)
    lines.append(T.CX_FAIL_COST)
    rows: list[list[Button]] = []
    notes: list[str] = []
    opts = svc.exit_options(content, ch, cs)
    lines += [""] + views_combat.exit_lines(content, ch.state, cs.faction, None, opts)[2:]  # odds (owner report 24.10)
    for opt in opts:
        pct = views_combat.exit_pct(ch.state, content, opt.kind, opt.dv) if opt.possible and opt.dv is not None and opt.kind != exits.SURRENDER else 0
        if opt.kind == exits.BRIBE:
            if opt.possible:
                rows.append([Button(f"Взятка {views.money(opt.cost)} · {pct}%", f"b{opt.step}", _arg(cs, target))])
            else:
                notes.append(T.CX_NO_BRIBE)
        elif opt.kind == exits.TALK:
            if opt.possible:
                rows.append([Button(f"Уговорить · {pct}%", "talk", _arg(cs, target))])
            else:
                notes.append(T.CX_NO_TALK_GATE if opt.note == "gate" else T.CX_NO_TALK)
        elif opt.kind == exits.FLEE:
            rows.append([Button(f"Бежать · {pct}%", "flee", _arg(cs, target))])
        else:
            rows.append([Button(T.CX_SURRENDER, "surrender", _arg(cs, target))])
    if notes:
        lines.append("<i>" + " ".join(dict.fromkeys(notes)) + "</i>")
    rows.append([Button(T.BACK, "back", _arg(cs, target))])
    return Screen("\n".join(lines), rows, name="cmexit", state={"tick": cs.tick, "target": target})


def end_screen(content: Content, ch: ActiveCharacter, step: svc.Step) -> Screen:
    end = step.end
    assert end is not None
    s = end.summary
    lines = [T.END_TITLES.get(s["result"], "")]
    if step.exit is not None:
        lines += exit_lines(step.exit)
    if s["ticks"]:
        lines.append(T.END_STATS.format(kills=s["kills"], takedowns=s["takedowns"], ticks=s["ticks"], dealt=s["dealt"], taken=s["taken"]))
    if end.xp_skill:
        lines.append(T.END_XP.format(skill=T.SKILL_WORDS.get(end.xp_skill, end.xp_skill), n=content.balance.combat.win_xp))
    fname = esc(views_combat.faction_name(content, s["faction"]))
    for _, delta in end.rep.items():
        if delta:
            lines.append(T.END_REP.format(faction=esc(views_combat.faction_dat(content, s["faction"])), delta=views.signed(delta)))
    if end.betrayed:
        lines.append(T.END_BETRAYED.format(faction=fname))
    lines.extend(end.lines)
    from app.services import loot  # owner request 26.10: search the bodies or leave

    lt = loot.pending(ch.state)
    if lt is not None and not end.died:
        risk = round(loot.ambush_chance(content, lt) * 100)
        lines += ["", f"🔍 Тела ещё тёплые ({lt['n']}). Обыскать — это эдди, иногда ствол, но шум привлекает "
                      f"внимание: риск, что придут ещё, {risk}%."]
        return _with_scene(content, ch, step.cs, -1, Screen(
            "\n".join(x for x in lines if x),
            [[Button("Обыскать тела", "loot", style="success"), Button("Уйти", "leave")]], name="cmend"))
    return _with_scene(content, ch, step.cs, -1,
                       Screen("\n".join(x for x in lines if x), [[Button(T.END_CONTINUE, "next")]], name="cmend"))


def exitchk_screen(content: Content, ch: ActiveCharacter) -> Screen:
    """A failed exit check: luck, a glitch or take the failure (same shape as an event check)."""
    p = svc.pending_exit(ch)
    assert p is not None
    check = CheckResult(**p["check"])
    rev = str(p["rev"])
    lines = [T.EXITCHK_HEAD.get(p["kind"], ""), views.check_line(check)]
    rows: list[list[Button]] = []
    if check.ok:
        lines.append(T.CHECK_PASS_LUCK)
        rows.append([Button(T.B_OK, "take", rev)])
    else:
        offer = engine_events.luck_offer(check, ch.state, content)
        can_glitch = engine_checks.can_glitch_reroll(check, ch.state.glitches)
        lines.append(T.CHECK_FAIL if (offer or can_glitch) else T.CHECK_FAIL_NOTHING)
        if offer:
            rows.append([Button(T.BTN_LUCK.format(points=offer.points, pool=offer.pool), "luck", rev)])
        if can_glitch:
            rows.append([Button(T.BTN_GLITCH.format(glitches=ch.state.glitches), "glitch", rev)])
        rows.append([Button(T.BTN_ACCEPT_FAIL, "take", rev)])
    return Screen("\n".join(x for x in lines if x), rows, name="exitchk", state={"rev": int(rev)})


def step_screen(content: Content, ch: ActiveCharacter, step: svc.Step, target: int = 0) -> Screen:
    """The screen after a service step: the next tick or the end of the fight."""
    if step.exit is not None and step.exit.pending:
        return exitchk_screen(content, ch)
    if step.end is not None:
        if step.end.died:
            return death_screen(ch.state.name, step.end.cause, step.end.lines)
        return end_screen(content, ch, step)
    return combat_screen(content, ch, step.cs, target, _notes(step))


async def combat_resume_screen(ctx: Ctx, ch: ActiveCharacter) -> Screen:
    cs = svc.state_of(ch)
    if cs is None:
        return await menu_screen(ctx)
    return combat_screen(ctx.content, ch, cs, 0)


# -- handlers ---------------------------------------------------------------------------------------------------------


async def _resume(ctx: Ctx, ch: ActiveCharacter | None = None) -> Screen:
    from app.bot.handlers.events import resume_screen

    return await resume_screen(ctx, ch)


def surrender_confirm(content: Content, cs: cb.CombatState, target: int) -> Screen:
    return Screen(views_combat.surrender_confirm_text(content, cs.faction),
                  [[Button("Да, сдаться", "surrender_yes", _arg(cs, target), style="danger")],
                   [Button("Нет, драться дальше", "back", _arg(cs, target))]],
                  name="cmexit", state={"tick": cs.tick, "target": target})


SCENE_TTL_S = 60


async def _show_scene(ctx: Ctx, png: bytes) -> None:
    """Owner report 27.10: in the group the fight picture is a short-lived photo on request, the window stays put."""
    from aiogram.types import BufferedInputFile

    res = await ctx.ui.outbox.send_photo(ctx.chat_id, BufferedInputFile(png, filename="scene.png"))
    if res.ok and res.message_id is not None:
        await ctx.ui.cleanup.schedule_delete(ctx.chat_id, res.message_id, SCENE_TTL_S, "temp")


@on_screen("cm")
async def cm_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    parsed = _parse(arg)
    if parsed is None:
        return await _resume(ctx)
    tick, target, extra = parsed
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    content = ctx.content
    if action == "reinf":
        from app.bot.handlers.reinforce import reinf_screen

        return await reinf_screen(ctx)
    if action in ("shoot", "melee", "cover", "heal", "guard"):  # owner request 26.10: the joined hired players move too
        from app.bot.handlers.pally import hold_for_allies
        from app.bot.handlers.pboss import hold_for_boss

        await hold_for_allies(ctx)
        await hold_for_boss(ctx)  # owner request 27.10: the raid boss plays the other side in person
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, content)
        cs0 = svc.state_of(ch)
        if cs0 is None:
            return await _resume(ctx, ch)
        try:
            if action == "f3d":  # owner request 31.10: open the same fight in the Unity client
                from app.services import fight3d

                link = fight3d.url(content, ctx.ui.settings.bot_token, ctx.user_id) if _fight3d_open(content, ch, cs0) else None
                scr = combat_screen(content, ch, cs0, target)
                if link:
                    scr.text = f'🎮 <a href="{link}">Открыть бой в 3D</a>. Каждое попадание считается теми же бросками, что здесь.\n\n' + scr.text
                return scr
            if action == "tg":
                if cs0.tick != tick:
                    raise svc.StaleError
                return combat_screen(content, ch, cs0, target)
            if action == "hack":
                if cs0.tick != tick:
                    raise svc.StaleError
                return hack_screen(content, cs0, target)
            if action == "exits":
                if cs0.tick != tick:
                    raise svc.StaleError
                return exits_screen(content, ch, cs0, target)
            if action == "squad":
                if cs0.tick != tick:
                    raise svc.StaleError
                return squad_screen(content, cs0, target)
            if action == "ab":
                if cs0.tick != tick:
                    raise svc.StaleError
                return ab_screen(content, ch, cs0, target)
            if action == "scene":
                if cs0.tick != tick:
                    raise svc.StaleError
                screen = combat_screen(content, ch, cs0, target)
                if ctx.chat_id < 0 and screen.picture is not None:
                    await _show_scene(ctx, screen.picture)
                return screen
            if action == "ord" and len(extra) == 2 and extra[1].isdigit():
                step = await svc.order_ally(s, content, ch, rng, year, tz, tick, extra[0], int(extra[1]))
                return squad_screen(content, step.cs, target) if not step.cs.over else step_screen(content, ch, step, target)
            if action == "nl":
                step = await svc.toggle_nonlethal(s, content, ch, rng, year, tz, tick)
            elif action == "imp" and extra:
                step = await svc.toggle_implant(s, content, ch, rng, year, tz, tick, extra[0])
            elif action == "auto" and extra:
                step = await svc.toggle_auto(s, content, ch, rng, year, tz, tick, extra[0])
            elif action == "shot" and len(extra) == 2 and extra[1].isdigit():
                step = await svc.fire_shot(s, content, ch, rng, year, tz, tick, extra[0], int(extra[1]), _target(cs0, target))
            elif action in ("shoot", "melee", "cover", "heal", "guard"):
                kind = {"shoot": cb.A_SHOOT, "melee": cb.A_MELEE, "cover": cb.A_COVER, "heal": cb.A_HEAL,
                        "guard": cb.A_GUARD}[action]
                qt = [x for x in extra if x[:2] == "qt" and x[2:].isdigit()]  # the Mini App's quick reaction: .qt1 / .qt2
                extra = [x for x in extra if x not in qt]
                weapon = extra[0] if extra and action in ("shoot", "melee") else ""
                grade = int(qt[0][2:]) if qt and action in ("shoot", "melee") else 0
                step = await svc.act(s, content, ch, rng, year, tz, tick,
                                     cb.PlayerAction(kind, _target(cs0, target), weapon=weapon, qte=grade))
            elif action == "surrender":  # owner request 25.10: a serious button asks first
                if cs0.tick != tick:
                    raise svc.StaleError
                return surrender_confirm(content, cs0, target)
            elif action == "flee":
                step = await svc.exit_attempt(s, content, ch, rng, year, tz, tick, exits.FLEE)
            else:
                return await _resume(ctx, ch)
        except svc.StaleError:
            return await _resume(ctx, ch)
    return _with_fx(content, step.cs, step_screen(content, ch, step, target))


@on_screen("cmhack")
async def cmhack_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    parsed = _parse(arg)
    if parsed is None:
        return await _resume(ctx)
    tick, target, extra = parsed
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    if action == "go":  # owner request 26.10: the joined hired players move too
        from app.bot.handlers.pally import hold_for_allies
        from app.bot.handlers.pboss import hold_for_boss

        await hold_for_allies(ctx)
        await hold_for_boss(ctx)  # owner request 27.10: the raid boss plays the other side in person
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        cs0 = svc.state_of(ch)
        if cs0 is None or cs0.tick != tick:
            return await _resume(ctx, ch)
        if action == "back":
            return combat_screen(ctx.content, ch, cs0, target)
        if action == "go" and extra:
            try:
                step = await svc.act(s, ctx.content, ch, rng, year, tz, tick,
                                     cb.PlayerAction(cb.A_HACK, _target(cs0, target), program=extra[0]))
            except svc.StaleError:
                return await _resume(ctx, ch)
            return step_screen(ctx.content, ch, step, target)
    return await _resume(ctx)


@on_screen("cmab")
async def cmab_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    """Owner request 27.10: a chrome ability in the fight."""
    parsed = _parse(arg)
    if parsed is None:
        return await _resume(ctx)
    tick, target, extra = parsed
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        cs0 = svc.state_of(ch)
        if cs0 is None or cs0.tick != tick:
            return await _resume(ctx, ch)
        if action == "use" and extra:
            from app.engine import ab_power

            was = ab_power.used_this_tick(cs0)
            fam_key = ab_power.families(ch.state, ctx.content).get(extra[0])
            try:
                step = await svc.use_ability(s, ctx.content, ch, rng, year, tz, tick, extra[0], _target(cs0, target))
            except svc.StaleError:
                return await _resume(ctx, ch)
            more: list[dict[str, Any]] = []
            if fam_key and not was and ab_power.used_this_tick(step.cs):
                kind, word = combat_fx.ability_fx(ctx.content, extra[0], fam_key[1])
                more.append({"id": f"ab:{tick}:{extra[0]}", "kind": kind, "word": word})
            return _with_fx(ctx.content, step.cs, step_screen(ctx.content, ch, step, target), more)
        return combat_screen(ctx.content, ch, cs0, target)


@on_screen("cmexit")
async def cmexit_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    parsed = _parse(arg)
    if parsed is None:
        return await _resume(ctx)
    tick, target, _ = parsed
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        cs0 = svc.state_of(ch)
        if cs0 is None or cs0.tick != tick:
            return await _resume(ctx, ch)
        if action == "back":
            return combat_screen(ctx.content, ch, cs0, target)
        if action == "surrender":  # owner request 25.10: a serious button asks first
            return surrender_confirm(ctx.content, cs0, target)
        try:
            if action in ("talk", "flee", "surrender_yes"):
                kind = {"talk": exits.TALK, "flee": exits.FLEE, "surrender_yes": exits.SURRENDER}[action]
                step = await svc.exit_attempt(s, ctx.content, ch, rng, year, tz, tick, kind)
            elif action.startswith("b") and action[1:].isdigit():
                step = await svc.exit_attempt(s, ctx.content, ch, rng, year, tz, tick, exits.BRIBE, int(action[1:]))
            else:
                return await _resume(ctx, ch)
        except svc.StaleError:
            return await _resume(ctx, ch)
    return step_screen(ctx.content, ch, step, target)


def exit_outcome_screen(ctx: Ctx, ch: ActiveCharacter, res: "ev_service.ExitResult") -> Screen:
    """The screen after an exit tried before the fight: pending luck, the fight it started, or the plain result."""
    from app.bot.handlers.events import exit_result_screen

    if res.pending:
        return exitchk_screen(ctx.content, ch)
    if res.fight is not None:  # the exit failed and the fight began
        if res.died:
            return death_screen(ch.state.name, res.death_cause, res.lines)
        res.fight.exit = svc.ExitInfo(res.kind, res.ok, res.check, note=res.note)
        return step_screen(ctx.content, ch, res.fight)
    return exit_result_screen(ctx.content, ch, res)


@on_screen("exitchk")
async def exitchk_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if arg is None or not arg.lstrip("-").isdigit():
        return await _resume(ctx)
    rev = int(arg)
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        try:
            if action == "luck":
                await svc.exit_luck(s, ctx.content, ch, rng, year, tz, rev)
                return exitchk_screen(ctx.content, ch)
            if action == "glitch":
                await svc.exit_glitch(s, ctx.content, ch, rng, year, tz, rev)
                return exitchk_screen(ctx.content, ch)
            if action == "take":
                got = await svc.exit_take(s, ctx.content, ch, rng, year, tz, rev)
            else:
                return await _resume(ctx, ch)
        except (svc.StaleError, LookupError):
            return await _resume(ctx, ch)
    if isinstance(got, svc.Step):
        return step_screen(ctx.content, ch, got)
    return exit_outcome_screen(ctx, ch, got)


@on_screen("cmend")
async def cmend_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action in ("loot", "leave"):  # owner request 26.10: the bodies after a won fight
        import random as _r

        from app.services import loot

        tz = ctx.ui.settings.timezone
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None or loot.pending(ch.state) is None:
                return await _resume(ctx)
            if action == "leave":
                await loot.leave(s, ch, tz)
                return await _resume(ctx, ch)
            year = await game.current_year(s, ctx.content)
            res = await loot.search(s, ctx.content, ch, _r.Random(), year, tz)
        if res.fight is not None:
            scr = step_screen(ctx.content, ch, res.fight)  # type: ignore[arg-type]
            scr.text = "\n".join(res.lines) + "\n\n" + scr.text
            return scr
        return Screen("\n".join(res.lines), [[Button(T.END_CONTINUE, "next")]], name="cmend")
    return await _resume(ctx)
