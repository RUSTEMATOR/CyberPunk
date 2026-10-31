"""The events of the week in the window (PLAN 4.4): one event per screen.

evchoice (the event and its buttons) -> [evcheck: luck / glitch / accept after a failed check]
-> evout (the outcome) or evexit (a fight offered: pay, talk, flee, surrender, or fight) -> next.
Closing the window loses nothing: the queue, a rolled check and a pending fight live on the
character (services/events.py), and `resume` rebuilds the right screen.
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot.handlers.menu import menu_screen
from app.bot.handlers.week import death_screen
from app.bot.ui import late, style
from app.bot.ui.callbacks import Ctx, on_screen
from app.bot.ui.screen import Button, Screen, choices
from app.content.loader import Content
from app.content.schemas import Event
from app.db import session as db
from app.engine import checks, exits
from app.engine import events as engine_events
from app.engine import vows as engine_vows
from app.engine.state import FLAG_COMBAT
from app.engine.checks import CheckResult
from app.services import combat as svc_combat
from app.services import events as ev_service
from app.services import game
from app.services.game import ActiveCharacter
from app.services.week import pending_events


async def _load(ctx: Ctx):
    """(session-less) the character and the world year; the session is closed, the state is a copy."""
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


def _event_header(event: Event, queue_len: int) -> str:
    tail = texts.EVENT_QUEUE.format(n=queue_len - 1) if queue_len > 1 else texts.EVENT_ONE
    return f"{style.header('event', texts.esc(event.title))}\n{tail}"


def _first_sentence(t: str) -> str:
    t = (t or "").strip()
    i = t.find(". ")
    return t if i < 0 or i > 160 else t[: i + 1]


def _offered_lines(content: Content, event: Event) -> list[str]:
    """What the implants, weapons, armor and rides on offer in the choices actually do."""
    from app.bot import effects_text as ET

    keys: list[tuple[str, str]] = []
    for c in event.choices:
        pools = [c.effects or {}]
        for out in (c.success, c.fail):
            if out is not None:
                pools.append(out.effects or {})
        for fx in pools:
            for k in fx:
                base, _, param = str(k).partition(":")
                if base in ("cyberware_grant", "item_grant", "vehicle_grant") and param and (base, param) not in keys:
                    keys.append((base, param))
    out: list[str] = []
    for base, key in keys[:4]:
        if base == "cyberware_grant" and key in content.cyberware:
            cw = content.cyberware[key]
            what = ET.cyberware_summary(cw, content, limit=3)
            out.append(f"🦾 <b>{texts.esc(cw.name)}</b>: {texts.esc(what)}. Человечность −{cw.humanity_cost}.\n"
                       f"<i>{texts.esc(_first_sentence(cw.description))}</i>")
        elif base == "item_grant" and key in content.weapons:
            w = content.weapons[key]
            out.append(f"🔫 <b>{texts.esc(w.name)}</b>: {texts.esc(', '.join(ET.weapon_phrases(w)[:3]))}.\n"
                       f"<i>{texts.esc(_first_sentence(w.description))}</i>")
        elif base == "item_grant" and key in content.armor:
            a = content.armor[key]
            out.append(f"🦺 <b>{texts.esc(a.name)}</b>: {texts.esc(', '.join(ET.armor_phrases(a)[:3]))}.\n"
                       f"<i>{texts.esc(_first_sentence(a.description))}</i>")
        elif base == "vehicle_grant" and key in content.vehicles:
            v = content.vehicles[key]
            out.append(f"🚗 <b>{texts.esc(v.name)}</b>\n<i>{texts.esc(_first_sentence(getattr(v, 'description', '')))}</i>")
    return out


def event_screen(content: Content, ch: ActiveCharacter, event: Event, authored_text: str | None = None) -> Screen:
    queue = pending_events(ch)
    text = f"{_event_header(event, len(queue))}\n{style.quote(texts.esc(authored_text or event.text))}"
    labels = []
    keys = []
    from app.engine import abilities as ab_eng  # owner request 26.10: chrome choices only for their owners

    have = ab_eng.owned(ch.state, content) if any(c.ability for c in event.choices) else {}
    for i, c in enumerate(event.choices):
        mine = ab_eng.mine_for(ch.state, content, c.ability, have) if c.ability else None
        if c.ability and mine is None:
            continue
        keys.append(str(i))
        label = c.label
        if c.ability:
            ab = content.balance.chrome_abilities.get(mine or c.ability)  # the icon of your own ability
            label = f"{ab.icon if ab else '⚙️'} {label}"
        cost = -int((c.effects or {}).get("eddies", 0) or 0) if c.check is None else 0
        if cost > 0:
            # a paid choice shows its price; without the money it still works, the rest goes into debt
            if not any(ch_.isdigit() for ch_ in label):
                label = f"{label} · {views.money(cost)}"
            if ch.state.eddies < cost:
                label = f"{label} ({texts.EVENT_ON_DEBT})"
        if engine_vows.choice_breaks(ch.state, content, c):
            label = f"{label} ({texts.ACT_VOW_WARN})"
        labels.append(label)
    listed, rows = choices(labels, "c", content.balance.ui.button_chars, keys)
    if listed:
        text += "\n" + "\n".join(listed)
    offered = _offered_lines(content, event)  # owner report 24.10: «не понятно, что беру»
    if offered:
        text += "\n\n" + "\n".join(offered)
    from app.bot import texts_glossary as G  # owner request 24.10: notes on words a player may not know

    found = G.notes(f"{event.title} {authored_text or event.text}", limit=2, skip=G.COMMON)
    if found:
        text += "\n\n" + "\n".join(found)
    return Screen(text, rows, name="evchoice", state={"event": event.id})


def check_screen(content: Content, ch: ActiveCharacter, event: Event, idx: int, check: CheckResult, rev: int) -> Screen:
    st = ch.state
    choice = event.choices[idx]
    lines = [style.header("event", texts.esc(event.title)), f"<i>{texts.esc(choice.label)}</i>", "", views.check_line(check)]
    rows: list[list[Button]] = []
    if check.ok:
        lines.append(texts.CHECK_PASS_LUCK)
        rows.append([Button(texts.EVENT_CONTINUE, "take", str(rev))])
    else:
        offer = engine_events.luck_offer(check, st, content)
        can_glitch = checks.can_glitch_reroll(check, st.glitches)
        lines.append(texts.CHECK_FAIL if (offer or can_glitch) else texts.CHECK_FAIL_NOTHING)
        if offer:
            rows.append([Button(texts.EVENT_LUCK.format(points=offer.points, pool=offer.pool), "luck", str(rev))])
        if can_glitch:
            rows.append([Button(texts.EVENT_GLITCH.format(glitches=st.glitches), "glitch", str(rev))])
        rows.append([Button(texts.EVENT_ACCEPT_FAIL, "take", str(rev))])
    return Screen("\n".join(lines), rows, name="evcheck", state={"rev": rev})


def vc_confirm(content: Content, faction: str) -> str:
    from app.bot import views_combat as vc

    return vc.surrender_confirm_text(content, faction)


def _fight_exit_screen(content: Content, ch: ActiveCharacter, head: str) -> Screen:
    pc = ev_service.pending_combat(ch)
    assert pc is not None
    faction = pc["faction"]
    fname = str(pc["foe"]) if pc.get("foe") else f"«{pc['dyn_name']}»" if pc.get("dyn_name") else (
        content.gangs[faction].name if faction in content.gangs
        else content.corps[faction].name if faction in content.corps
        else texts.EXIT_FACTION_NAMES.get(faction, faction)
    )
    lines = ([head, ""] if head else []) + [texts.EXIT_TITLE, texts.EXIT_FACTION.format(faction=texts.esc(fname), danger=pc["danger"])]
    rows: list[list[Button]] = []
    notes: list[str] = []
    opts = ev_service.exit_options(content, ch)
    from app.bot import views_combat as vc  # owner report 24.10: stats, odds and the price of each way out

    lines[-1] += f" ({vc.DANGER_WORDS.get(int(pc['danger']), '')})" if pc.get("danger") else ""
    lines += vc.exit_lines(content, ch.state, faction, None, opts)
    for opt in opts:
        pct = vc.exit_pct(ch.state, content, opt.kind, opt.dv) if opt.possible and opt.dv is not None and opt.kind != exits.SURRENDER else 0
        if opt.kind == exits.BRIBE:
            if opt.possible:
                rows.append([Button(f"Взятка {views.money(opt.cost)} · {pct}%", f"b{opt.step}")])
            else:
                notes.append(texts.EXIT_NO_BRIBE)
        elif opt.kind == exits.TALK:
            if opt.possible:
                rows.append([Button(f"Уговорить · {pct}%", "talk")])
            else:
                notes.append(texts.EXIT_NO_TALK_GATE if opt.note == "gate" else texts.EXIT_NO_TALK)
        elif opt.kind == exits.FLEE:
            rows.append([Button(f"Бежать · {pct}%", "flee")])
        else:
            rows.append([Button(texts.EXIT_SURRENDER, "surrender")])
    rows.append([Button(texts.EXIT_FIGHT, "fight")])
    if notes:
        lines.append("<i>" + " ".join(dict.fromkeys(notes)) + "</i>")
    return Screen("\n".join(lines), rows, name="evexit")


def resolution_screen(content: Content, ch: ActiveCharacter, res: ev_service.Resolution) -> Screen:
    """The screen after a resolved choice: the outcome, then the exits when a fight waits."""
    if res.died:
        return death_screen(ch.state.name, res.death_cause, res.lines)
    choice = res.event.choices[res.choice_idx]
    lines = [style.header("event", texts.esc(res.event.title)), f"<i>{texts.esc(choice.label)}</i>"]
    if res.check is not None:
        lines.append(views.check_line(res.check))
    lines.append(style.quote(texts.esc(res.text)))
    summary = views.effects_summary(res.applied, content)
    if summary:
        lines.append(summary)
    lines.extend(res.lines)
    head = "\n".join(lines)
    if res.combat_danger:
        return _fight_exit_screen(content, ch, head)
    return Screen(head, [[Button(texts.EVENT_CONTINUE, "next")]], name="evout")


def shown_resolution(ctx: Ctx, ch: ActiveCharacter, res: ev_service.Resolution) -> Screen:
    """The outcome at once with the authored text; Claude's retelling replaces it when it comes (owner request)."""
    scr = resolution_screen(ctx.content, ch, res)
    if not res.late or res.died:
        return scr

    async def job() -> Screen | None:
        text = await ev_service.late_outcome(res)
        if not text:
            return None
        res.text = text
        return resolution_screen(ctx.content, ch, res)

    return late.attach(ctx, scr, job)


def shown_event(ctx: Ctx, ch: ActiveCharacter, event: Event) -> Screen:
    """The event with the authored text at once; Claude's version replaces it when it comes (owner request)."""
    from app.narrator import narrator

    known, text = ev_service.cached_event_text(ch, event)
    if known or event.forced_only or not narrator.is_live() or not ctx.content.balance.event_text.retell_event:
        return event_screen(ctx.content, ch, event, text)
    scr = event_screen(ctx.content, ch, event, None)

    async def job() -> Screen | None:
        t = await ev_service.event_text(ctx.content, ch, event)
        return event_screen(ctx.content, ch, event, t) if t else None

    return late.attach(ctx, scr, job)


def exit_result_screen(content: Content, ch: ActiveCharacter, res: ev_service.ExitResult) -> Screen:
    if res.died:
        return death_screen(ch.state.name, res.death_cause, res.lines)
    lines: list[str] = []
    if res.check is not None:
        lines.append(views.check_line(res.check))
    if res.kind == exits.SURRENDER:
        lines.append(texts.EXIT_SURRENDER_OUT.get(res.note, ""))
    elif res.ok:
        lines.append({exits.BRIBE: texts.EXIT_OK_BRIBE, exits.TALK: texts.EXIT_OK_TALK}.get(res.kind, texts.EXIT_OK_FLEE))
    elif res.kind == exits.BRIBE:
        lines.append(texts.EXIT_FAIL_BRIBE.format(
            taken=texts.EXIT_FAIL_BRIBE_TAKEN if res.note == "taken" else texts.EXIT_FAIL_BRIBE_KEPT))
    elif res.kind == exits.TALK:
        lines.append(texts.EXIT_FAIL_TALK)
    elif res.kind == exits.FLEE:
        lines.append(texts.EXIT_FAIL_FLEE)
    lines.extend(res.lines)
    return Screen("\n".join(x for x in lines if x), [[Button(texts.EVENT_CONTINUE, "next")]], name="evout")


async def resume_screen(ctx: Ctx, ch: ActiveCharacter | None = None) -> Screen:
    """The screen of whatever is unfinished: a fight, a rolled check, the next event, else the menu."""
    if ch is None:
        ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    content = ctx.content
    from app.bot.handlers import combat as combat_handlers  # M5: a fight in progress, then a gig in progress
    from app.bot.handlers import gigs as gig_handlers

    if ch.state.flags.get(FLAG_COMBAT):
        return await combat_handlers.combat_resume_screen(ctx, ch)
    pe = svc_combat.pending_exit(ch)
    if pe is not None and pe.get("where") == "fight" and svc_combat.state_of(ch) is None:
        # owner report 24.10: a fight exit left over without its fight locked the window in a loop
        async with db.session_scope() as s:
            fresh = await game.get_active_character(s, ctx.user_id)
            if fresh is not None:
                fresh.state.flags.pop(svc_combat.FLAG_PENDING_EXIT, None)
                await game.save_active(s, fresh, ctx.ui.settings.timezone)
        ch.state.flags.pop(svc_combat.FLAG_PENDING_EXIT, None)
        pe = None
    if pe is not None:
        return combat_handlers.exitchk_screen(content, ch)
    pc = ev_service.pending_combat(ch)
    if pc is not None:
        return _fight_exit_screen(content, ch, "")
    from app.bot.handlers import psychosis as psy_handlers  # M6.6a: the capture verdict, the MaxTac duty call

    psy_screen = await psy_handlers.psy_resume_screen(ctx, ch)
    if psy_screen is not None:
        return psy_screen
    gig_screen = await gig_handlers.resume_gig_screen(ctx, ch)
    if gig_screen is not None:
        return gig_screen
    pk = ev_service.pending_check(ch)
    if pk is not None and pk.get("kind") == "life":
        from app.bot.handlers.lifecheck import check_screen as life_check_screen  # M9 checked actions

        return life_check_screen(content, ch)
    if pk is not None and pk.get("kind") == "gamble":
        from app.bot.handlers.activities import gamble_check_screen  # the gamble lives with the activities
        return gamble_check_screen(content, ch)
    if pk is not None and pk["event"] in content.events:
        return check_screen(content, ch, ev_service.personal(content, ch, content.events[pk["event"]]), int(pk["choice"]),
                            ev_service.check_from_dict(pk["check"]), int(pk["rev"]))
    event = ev_service.current_event(content, ch)
    if event is not None:
        return shown_event(ctx, ch, event)
    if ch.state.flags.get("dgame"):  # owner request 24.10: back to the island after a fight
        from app.bot.handlers.dgame import dgame_screen

        return dgame_screen(content, ch)
    if ch.state.flags.get("purge_back"):  # owner request 24.10: back to the Purge night after its fight or story
        from app.services import purge as purge_service

        async with db.session_scope() as s:
            pst = await purge_service.state(s)
        if pst.get("status") == purge_service.NIGHT and pst.get("id") == ch.state.flags.get("purge_back"):
            from app.bot.handlers.purge import purge_screen

            return await purge_screen(ctx)
    if ch.state.flags.get("tower_run"):  # owner request 26.10: back to the tower dungeon after a fight
        from app.bot.handlers.dungeon import dungeon_screen

        return await dungeon_screen(ctx, ch)
    if ch.state.flags.get("sortie"):  # owner request 24.10: back to the map of the sortie after a fight
        from app.bot.handlers.sortie import sortie_screen

        return sortie_screen(content, ch)
    return await menu_screen(ctx)


@on_screen("evchoice")
async def evchoice_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "c" and arg is not None and arg.isdigit():
        rng = random.Random()
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, ctx.content)
            try:
                got = await ev_service.choose(s, ctx.content, ch, rng, year, ctx.ui.settings.timezone, int(arg))
            except LookupError:
                return await resume_screen(ctx, ch)
        if isinstance(got, CheckResult):
            event = ev_service.current_event(ctx.content, ch)
            assert event is not None
            return check_screen(ctx.content, ch, event, int(arg), got, 0)
        return shown_resolution(ctx, ch, got)
    return await resume_screen(ctx)


@on_screen("evcheck")
async def evcheck_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    if arg is None or not arg.lstrip("-").isdigit():
        return await resume_screen(ctx)
    rev = int(arg)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        try:
            if action in ("luck", "glitch"):
                fn = ev_service.spend_luck if action == "luck" else ev_service.spend_glitch
                got = await fn(s, ctx.content, ch, rng, year, tz, rev)
                pk = ev_service.pending_check(ch)
                assert pk is not None
                return check_screen(ctx.content, ch, ev_service.personal(ctx.content, ch, ctx.content.events[pk["event"]]), int(pk["choice"]), got, int(pk["rev"]))
            if action == "take":
                res = await ev_service.accept_check(s, ctx.content, ch, rng, year, tz, rev)
                return shown_resolution(ctx, ch, res)
        except LookupError:
            return await resume_screen(ctx, ch)
    return await resume_screen(ctx)


@on_screen("evout")
async def evout_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    return await resume_screen(ctx)


@on_screen("evexit")
async def evexit_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.bot.handlers import combat as combat_handlers

    rng = random.Random()
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is not None and svc_combat.pending_exit(ch) is not None:
            return combat_handlers.exitchk_screen(ctx.content, ch)
        if ch is None or ev_service.pending_combat(ch) is None:
            return await resume_screen(ctx, ch)
        year = await game.current_year(s, ctx.content)
        try:
            if action == "fight":
                step = await ev_service.accept_fight(s, ctx.content, ch, rng, year, tz)
                return combat_handlers.step_screen(ctx.content, ch, step)
            elif action == "surrender":  # owner request 25.10: a serious button asks first
                pc = ev_service.pending_combat(ch) or {}
                return Screen(vc_confirm(ctx.content, str(pc.get("faction", ""))),
                              [[Button("Да, сдаться", "surrender_yes", style="danger")], [Button("Назад", "back")]],
                              name="evexit")
            elif action in ("talk", "flee", "surrender_yes"):
                res = await ev_service.attempt_exit(s, ctx.content, ch, rng, year, tz,
                                                    "surrender" if action == "surrender_yes" else action)
            elif action.startswith("b") and action[1:].isdigit():
                res = await ev_service.attempt_exit(s, ctx.content, ch, rng, year, tz, exits.BRIBE, int(action[1:]))
            else:
                return await resume_screen(ctx, ch)
        except LookupError:
            return await resume_screen(ctx, ch)
    return combat_handlers.exit_outcome_screen(ctx, ch, res)
