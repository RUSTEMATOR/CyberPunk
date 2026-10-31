"""The relations section in the window (PLAN 9.1-9.2).

relations (the list) -> relcard (one person: the actions) -> [relintro: whom to introduce | relbreak: confirm]
-> relout (what happened) -> back to the list. Fixers have a card with one action, the gift.
The NPC is addressed by its index in `state.npcs` and its name (a changed list redraws the screen).
"""

from __future__ import annotations

import random

from app.bot import texts, views
from app.bot import texts_life as T
from app.bot import texts_narrator as T_N
from app.bot import texts_players as TP
from app.bot.handlers.menu import menu_screen
from app.bot.handlers.week import death_screen
from app.bot.ui.callbacks import Ctx, goto, has_screen, on_screen
from app.bot.ui.screen import Button, Screen, list_rows
from app.content.loader import Content
from app.db import session as db
from app.engine import relations as rel
from app.engine.state import FLAG_PARTNER_MOVED_IN, NpcState
from app.narrator import narrator
from app.services import game
from app.services import relations as rel_service
from app.services.game import ActiveCharacter

esc = texts.esc


async def _load(ctx: Ctx) -> ActiveCharacter | None:
    async with db.session_scope() as s:
        return await game.get_active_character(s, ctx.user_id)


def _role_word(npc: NpcState) -> str:
    if npc.kind == "family":
        return T.FAMILY_ROLES.get(npc.role or "", T.KIND_WORD["family"])
    return T.KIND_WORD.get(npc.kind, npc.kind)


def _marks(ch: ActiveCharacter, content: Content, npc: NpcState) -> str:
    bits: list[str] = []
    age = rel.age_of(ch.state, npc, content)
    if age is not None:
        bits.append(views.years(age))
    if npc.kind == "partner" and ch.state.flags.get(FLAG_PARTNER_MOVED_IN):
        bits.append(T.MOVED_IN_MARK)
    if npc.kind == "partner":  # owner request 25.10: the partner needs time together
        from app.services import romance

        w = romance.weeks_apart(ch.state, npc)
        if w is not None:
            bits.append("вместе на этой неделе" if w == 0 else f"вместе последний раз: {w} нед. назад")
            if romance.neglected(ch.state, npc, content):
                bits.append("<b>скучает, нужно время вдвоём</b>")
    if npc.data.get("enemy"):
        bits.append(T.ENEMY_MARK)
    if npc.data.get("married"):
        bits.append("в браке")
    if npc.data.get("co_parent"):  # owner request 25.10: a child of two players' characters
        bits.append(f"второй родитель: {esc(str(npc.data['co_parent']))}")
    if npc.data.get("adopted"):
        bits.append("приёмный ребёнок")
    e = rel.edu(npc)
    if e:
        names = {"INT": "ум", "BODY": "тело", "COOL": "улица"}
        bits.append("воспитание: " + ", ".join(f"{names.get(k, k)} {v}" for k, v in e.items()))
    return ", ".join(bits)


def _line(ch: ActiveCharacter, content: Content, npc: NpcState) -> str:
    marks = _marks(ch, content, npc)
    base = T.REL_NPC_LINE.format(name=esc(npc.name), role=_role_word(npc), rel=npc.relation)
    return base + (f" ({marks})" if marks else "")


def relations_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    st = ch.state
    lines = [T.REL_TITLE, T.REL_STATUS.format(eddies=views.money(st.eddies), ap=st.ap)]
    if rel.in_jail(st):
        lines.append(T.REL_JAIL_NOTE)
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    living = [(i, n) for i, n in enumerate(st.npcs) if n.alive]
    fixers = [(k, v) for k, v in st.fixer_rel.items() if k in content.fixers]
    if not living and not fixers:
        lines += ["", T.REL_EMPTY]
    entries: list[tuple[str, str, str]] = []  # (button title, action, arg) in screen order
    for kind in T.REL_GROUP_ORDER:
        group = [(i, n) for i, n in living if n.kind == kind]
        if group:
            entries += [(n.name, "n", str(i)) for i, n in group]
    entries += [(content.fixers[key].name, "f", key) for key, _ in fixers]
    numbered, rows = list_rows([e[0] for e in entries], [e[1] for e in entries], [e[2] for e in entries],
                               content.balance.ui.button_chars)
    num = 0
    for kind in T.REL_GROUP_ORDER:
        group = [(i, n) for i, n in living if n.kind == kind]
        if not group:
            continue
        lines += ["", f"<b>{T.REL_GROUPS[kind]}</b>"]
        for i, n in group:
            num += 1
            lines.append((f"{num}. " if numbered else "") + _line(ch, content, n))
    if fixers:
        lines += ["", f"<b>{T.REL_FIXERS}</b>"]
        for key, value in fixers:
            num += 1
            name = content.fixers[key].name
            lines.append((f"{num}. " if numbered else "") + T.REL_NPC_LINE.format(name=esc(name), role="фиксер", rel=value))
    if rel.in_jail(st) and has_screen("jailcontacts"):
        rows.append([Button(T.REL_LAWYER_BUTTON, "lawyer")])
    fam = []  # owner request 23.10: the family
    if not st.npcs_of("partner"):
        fam.append(Button("Искать пару · 1 ОД", "findpartner"))
    fam.append(Button("Семейный фонд", "fund"))
    rows.append(fam)
    cfg = content.balance.relations  # owner request 25.10: children made simple
    if len(st.npcs_of("child")) < cfg.child_max:
        rows.append([Button(f"Усыновить · {views.money(cfg.adopt_cost)}", "adopt")])
    lines += ["", f"<i>{T.KIDS_HINT.format(rel=cfg.baby_min_rel)}</i>"]
    rows.append([Button(TP.BUTTON, "players")])
    rows.append([Button(T.BACK, "back")])
    return Screen("\n".join(lines), rows, name="relations", state={"names": [n.name for n in st.npcs]})


def card_screen(content: Content, ch: ActiveCharacter, idx: int, note: str | None = None) -> Screen:
    st = ch.state
    npc = rel_service.find(ch, idx)
    if npc is None or not npc.alive:
        return relations_screen(content, ch)
    cfg = content.balance.relations
    lines = [T.CARD_HEAD.format(name=esc(npc.name)), T.CARD_KIND.format(kind=_role_word(npc), rel=npc.relation)]
    marks = _marks(ch, content, npc)
    if marks:
        lines.append(marks)
    lines.append(T.CARD_STATUS.format(eddies=views.money(st.eddies), ap=st.ap))
    lines.append(T.CARD_AP.format(ap=cfg.action_ap))
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    for action in rel.actions_for(st, npc):
        label = T.BREAK_LABELS.get(npc.kind, T.ACTION_LABELS[action]) if action == rel.BREAK else T.ACTION_LABELS[action]
        if action == rel.GIFT:
            text = T.ACTION_BUTTON_PRICE.format(label=label, eddies=cfg.gift_cost)
        elif action in (rel.DATE, rel.PROPOSE, rel.SCHOOL, rel.BABY):
            price = {rel.DATE: cfg.date_cost, rel.PROPOSE: cfg.wedding_cost, rel.SCHOOL: cfg.school_cost,
                     rel.BABY: cfg.baby_cost}[action]
            text = T.ACTION_BUTTON_PRICE.format(label=label, eddies=views.money(price))
        elif action == rel.TEACH and cfg.protege_ap != cfg.action_ap:
            text = T.ACTION_BUTTON.format(label=label, ap=cfg.protege_ap)
        else:
            text = label
        if action == rel.BREAK:
            rows.append([Button(text, "ask_break")])
        elif action == rel.INTRO:
            rows.append([Button(text, "intro")])
        else:
            rows.append([Button(text, "do", action)])
    if narrator.is_live():  # PLAN 8.6: a talk is written by Claude, so the button exists only while Claude can answer
        rows.append([Button(T_N.CHAT_BUTTON, "talk")])
    rows.append([Button(T.BACK, "list")])
    return Screen("\n".join(lines), rows, name="relcard", state={"idx": idx, "name": npc.name})


def fixer_card_screen(content: Content, ch: ActiveCharacter, key: str, note: str | None = None) -> Screen:
    st = ch.state
    fixer = content.fixers.get(key)
    if fixer is None or key not in st.fixer_rel:
        return relations_screen(content, ch)
    cfg = content.balance.relations
    value = st.fixer_rel[key]
    lines = [T.CARD_HEAD.format(name=esc(fixer.name)), T.CARD_FIXER_KIND.format(rel=value),
             T.CARD_STATUS.format(eddies=views.money(st.eddies), ap=st.ap), T.CARD_AP.format(ap=cfg.action_ap)]
    if value >= cfg.contact_discount_rel:
        lines.append(T.CARD_DISCOUNT.format(n=cfg.contact_discount_rel))
    if note:
        lines += ["", note]
    rows = [
        [Button(T.ACTION_BUTTON_PRICE.format(label=T.ACTION_LABELS["gift"], eddies=cfg.gift_cost), "gift")],
        [Button(T.BACK, "list")],
    ]
    return Screen("\n".join(lines), rows, name="relfixer", state={"key": key})


def intro_screen(content: Content, ch: ActiveCharacter, idx: int) -> Screen:
    npc = rel_service.find(ch, idx)
    if npc is None:
        return relations_screen(content, ch)
    cands = [ch.state.npcs[i] for i in rel.introduce_candidates(ch.state, npc)]
    numbered, rows = list_rows([c.name for c in cands], "o", [str(i) for i in rel.introduce_candidates(ch.state, npc)],
                               content.balance.ui.button_chars)
    text = T.INTRO_TITLE.format(name=esc(npc.name))
    text += "".join(T.INTRO_LINE.format(n=f"{k}. " if numbered else "", name=esc(c.name), rel=c.relation)
                    for k, c in enumerate(cands, 1))
    rows.append([Button(T.BACK, "back")])
    return Screen(text, rows, name="relintro",
                  state={"idx": idx, "name": npc.name})


def break_screen(ch: ActiveCharacter, idx: int) -> Screen:
    npc = rel_service.find(ch, idx)
    label = T.BREAK_LABELS.get(npc.kind if npc else "", T.ACTION_LABELS["break"])
    name = esc(npc.name) if npc else ""
    return Screen(f"<b>{label}</b>\n{name}", [[Button(label, "yes")], [Button(texts.CANCEL, "no")]],
                  name="relbreak", state={"idx": idx, "name": npc.name if npc else ""})


# -- the result ---------------------------------------------------------------------------------------------


def _pick(rng: random.Random, pool: tuple[str, ...]) -> str:
    return rng.choice(pool)


def result_screen(content: Content, ch: ActiveCharacter, res: rel_service.RelResult, rng: random.Random) -> Screen:
    if res.died:
        return death_screen(ch.state.name, res.death_cause, res.lines)
    name = esc(res.npc)
    a = res.action
    if a == rel.HANG:
        line = _pick(rng, T.HANG_TEXT.get(res.kind, T.HANG_TEXT["friend"])).format(name=name)
    elif a == rel.ASK:
        line = _pick(rng, T.ASK_TEXT).format(name=name, sum=views.money(res.money))
    elif a == rel.QUARREL:
        line = _pick(rng, T.QUARREL_TEXT).format(name=name)
        if res.enemy:
            line += "\n" + T.QUARREL_ENEMY.format(name=name)
    elif a == rel.GIFT:
        line = (T.GIFT_FIXER if res.kind == "fixer" else _pick(rng, T.GIFT_TEXT)).format(name=name)
    elif a == rel.INTRO:
        line = T.INTRO_TEXT.format(name=name, other=esc(res.other))
    elif a == rel.BREAK:
        line = T.BREAK_TEXT.get(res.kind, T.BREAK_TEXT["friend"]).format(name=name)
    elif a == rel.MOVE_IN:
        line = T.MOVEIN_TEXT.format(name=name)
    elif a == rel.MOVE_OUT:
        line = T.MOVEOUT_TEXT.format(name=name)
    elif a == rel.DATE:
        line = _pick(rng, T.DATE_TEXT).format(name=name)
    elif a == rel.NIGHT:
        line = _pick(rng, T.NIGHT_TEXT).format(name=name)
    elif a == rel.BABY:  # owner request 25.10
        kid = res.applied.npcs_added[-1].name if res.applied is not None and res.applied.npcs_added else "малыш"
        line = T.BABY_TEXT.format(name=name, child=esc(kid))
    elif a == "adopt":
        line = T.ADOPT_TEXT.format(child=esc(res.npc), age=views.years(res.money))
    elif a == rel.PROPOSE:
        line = T.PROPOSE_TEXT.format(name=name)
    elif a in T.RAISE_TEXT:
        line = T.RAISE_TEXT[a].format(name=name)
    else:
        line = _pick(rng, T.TEACH_TEXT).format(name=name)
    lines = [line]
    if res.applied is not None:
        summary = views.effects_summary(res.applied, content)
        if summary:
            lines.append(summary)
    if res.rel_delta:
        lines.append(T.REL_DELTA.format(delta=views.signed(res.rel_delta)))
    lines.extend(res.lines)
    rows = [[Button(T.RESULT_BACK, "list"), Button(T.MENU, "menu")]]
    return Screen("\n".join(lines), rows, name="relout")


def _blocked_note(res: rel_service.RelResult) -> str:
    if res.no_ap:
        return T.NO_AP
    return T.BLOCKED.get(res.blocked or "", res.blocked or "")


# -- handlers -----------------------------------------------------------------------------------------------------


async def fund_screen(ctx: Ctx, ch: ActiveCharacter, note: str | None = None) -> Screen:
    """Owner request 23.10: money put aside for the next life; the heir gets all of it, not half."""
    from app.services import family

    cfg = ctx.content.balance.relations
    async with db.session_scope() as s:
        have = await family.fund_of(s, ctx.user_id)
    lines = ["<b>Семейный фонд</b>", T.REL_STATUS.format(eddies=views.money(ch.state.eddies), ap=ch.state.ap), "",
             "Деньги для следующей жизни. Обычный наследник получает половину денег, а фонд переходит к нему целиком "
             "(к любому, даже начавшему с нуля). Забрать обратно нельзя.", "", f"В фонде: <b>{views.money(have)}</b>"]
    if note:
        lines += ["", note]
    steps = [x for x in cfg.fund_steps if x <= ch.state.eddies]
    rows = [[Button(f"Внести {views.money(x)}", "fundput", str(x)) for x in steps[i:i + 2]] for i in range(0, len(steps), 2)]
    rows.append([Button(T.BACK, "list")])
    return Screen("\n".join(lines), rows, name="relations")


@on_screen("relations")
async def relations_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await menu_screen(ctx)
    ch = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action == "list":
        return relations_screen(ctx.content, ch)
    if action == "fund":
        return await fund_screen(ctx, ch)
    if action == "adopt":  # owner request 25.10: a child without a partner
        import random as _r

        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, ctx.content)
            rng = _r.Random()
            res = await rel_service.adopt(s, ctx.content, ch, rng, year, ctx.ui.settings.timezone)
        if not res.done:
            return relations_screen(ctx.content, ch, _blocked_note(res))
        return result_screen(ctx.content, ch, res, rng)
    if action in ("findpartner", "fundput"):
        import random as _r

        from app.services import family

        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, ctx.content)
            if action == "fundput":
                total = await family.deposit(s, ctx.content, ch, _r.Random(), year, ctx.ui.settings.timezone,
                                             int(arg) if arg and arg.isdigit() else 0)
            else:
                fr = await family.find_partner(s, ctx.content, ch, _r.Random(), year, ctx.ui.settings.timezone)
        if action == "fundput":
            return await fund_screen(ctx, ch, "Не хватает эдди." if total is None else f"Внесено. В фонде {views.money(total)}.")
        if fr.blocked:
            return relations_screen(ctx.content, ch, {"have": "Пара уже есть.", "jail": T.BLOCKED["jail"], "ap": T.NO_AP}.get(fr.blocked, fr.blocked))
        parts = [views.check_line(fr.check)] if fr.check is not None else []  # type: ignore[arg-type]
        parts.append(f"Бар, клуб, приложение знакомств. Ты встречаешь <b>{esc(fr.name)}</b>. Кажется, это надолго."
                     if fr.ok else "Вечер в баре, пара неловких разговоров и ни одного номера. В другой раз.")
        if fr.applied is not None:
            sm = views.effects_summary(fr.applied, ctx.content)
            if sm:
                parts.append(sm)
        return relations_screen(ctx.content, ch, "\n".join(parts))
    c = ctx.content
    if action == "n" and arg is not None and arg.isdigit():
        names = ctx.state.get("names") or []
        idx = int(arg)
        if idx < len(names) and rel_service.find(ch, idx, names[idx]) is not None:
            return card_screen(c, ch, idx)
        return relations_screen(c, ch)
    if action == "f" and arg is not None:
        return fixer_card_screen(c, ch, arg)
    if action == "lawyer" and has_screen("jailcontacts"):
        return await goto(ctx, "jailcontacts")
    if action == "players" and has_screen("plist"):
        return await goto(ctx, "plist", "open")
    return relations_screen(c, ch)


@on_screen("relcard")
async def relcard_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    idx = int(ctx.state.get("idx", -1))
    name = ctx.state.get("name")
    npc = rel_service.find(ch, idx, name)
    if action == "list" or npc is None:
        return relations_screen(c, ch)
    if action == "intro":
        reason = rel.blocked(ch.state, c, rel.INTRO, npc)
        if reason is not None:
            return card_screen(c, ch, idx, T.BLOCKED.get(reason, reason))
        return intro_screen(c, ch, idx)
    if action == "ask_break":
        return break_screen(ch, idx)
    if action == "talk":
        from app.bot.handlers.npcchat import open_chat  # late: npcchat imports this module

        return await open_chat(ctx, idx)
    if action == "do" and arg in rel.ACTIONS_BY_KIND.get(npc.kind, ()) + (rel.MOVE_OUT,):
        return await _do(ctx, idx, arg)
    return card_screen(c, ch, idx)


async def _do(ctx: Ctx, idx: int, action: str, other_idx: int | None = None) -> Screen:
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        res = await rel_service.perform(s, ctx.content, ch, rng, year, tz, idx, action, other_idx=other_idx)
    if not res.done:
        if res.blocked == rel.BLOCK_DEAD or rel_service.find(ch, idx) is None:
            return relations_screen(ctx.content, ch)
        return card_screen(ctx.content, ch, idx, _blocked_note(res))
    return result_screen(ctx.content, ch, res, rng)


@on_screen("relintro")
async def relintro_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    idx = int(ctx.state.get("idx", -1))
    if rel_service.find(ch, idx, ctx.state.get("name")) is None:
        return relations_screen(ctx.content, ch)
    if action == "o" and arg is not None and arg.isdigit():
        return await _do(ctx, idx, rel.INTRO, int(arg))
    return card_screen(ctx.content, ch, idx)


@on_screen("relbreak")
async def relbreak_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    idx = int(ctx.state.get("idx", -1))
    if rel_service.find(ch, idx, ctx.state.get("name")) is None:
        return relations_screen(ctx.content, ch)
    if action == "yes":
        return await _do(ctx, idx, rel.BREAK)
    return card_screen(ctx.content, ch, idx)


@on_screen("relfixer")
async def relfixer_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    key = str(ctx.state.get("key", ""))
    if action != "gift" or key not in ctx.content.fixers:
        return relations_screen(ctx.content, ch)
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        year = await game.current_year(s, ctx.content)
        res = await rel_service.gift_fixer(s, ctx.content, ch, rng, year, tz, key)
    if not res.done:
        return fixer_card_screen(ctx.content, ch, key, _blocked_note(res))
    res.npc = ctx.content.fixers[key].name
    return result_screen(ctx.content, ch, res, rng)


@on_screen("relout")
async def relout_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    ch = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    return relations_screen(ctx.content, ch)


# -- the protege (Activities) -----------------------------------------------------------------------------


def protege_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    cfg = content.balance.relations
    st = ch.state
    lines = [T.PRO_TITLE, T.REL_STATUS.format(eddies=views.money(st.eddies), ap=st.ap)]
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    got = rel_service.protege_of(ch)
    if got is None:
        lines += ["", T.PRO_NONE.format(cred=cfg.protege_min_cred)]
        rows.append([Button(T.PRO_TAKE.format(ap=cfg.protege_ap), "take")])
    else:
        _, npc = got
        skill = texts.SKILL_LABELS.get(str(npc.data.get("skill", "")), "").lower()
        lines += ["", T.PRO_LINE.format(
            name=esc(npc.name), age=views.years(int(npc.data.get("age", 0) or 0)), skill=skill,
            lessons=int(npc.data.get("lessons", 0) or 0), rel=npc.relation)]
        rows.append([Button(T.PRO_TEACH.format(ap=cfg.protege_ap), "teach")])
        rows.append([Button(T.PRO_CARD, "card")])
    rows.append([Button(T.BACK, "back")])
    return Screen("\n".join(lines), rows, name="protege")


@on_screen("protege")
async def protege_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "back":
        return await goto(ctx, "activities")
    ch = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    rng = random.Random()
    tz = ctx.ui.settings.timezone
    if action == "card":
        got = rel_service.protege_of(ch)
        return card_screen(c, ch, got[0]) if got else protege_screen(c, ch)
    if action == "take":
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            res = await rel_service.take_protege(s, c, ch, rng, year, tz)
        if res.no_ap:
            return protege_screen(c, ch, T.NO_AP)
        if res.blocked:
            return protege_screen(c, ch, T.PRO_BLOCKED[res.blocked].format(cred=c.balance.relations.protege_min_cred)
                                  if res.blocked in T.PRO_BLOCKED else T.BLOCKED.get(res.blocked, res.blocked))
        got = rel_service.protege_of(ch)
        assert got is not None
        npc = got[1]
        tmpl = T.PRO_TAKEN_F if npc.data.get("gender") == "f" else T.PRO_TAKEN
        text = tmpl.format(name=esc(npc.name), age=views.years(int(npc.data.get("age", 0))),
                           skill=texts.SKILL_LABELS.get(res.skill, res.skill).lower())
        return Screen(text, [[Button(T.PRO_BACK, "back")]], name="proout")
    if action == "teach":
        got = rel_service.protege_of(ch)
        if got is None:
            return protege_screen(c, ch)
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            year = await game.current_year(s, c)
            got = rel_service.protege_of(ch)
            if got is None:
                return protege_screen(c, ch)
            res = await rel_service.perform(s, c, ch, rng, year, tz, got[0], rel.TEACH)
        if not res.done:
            return protege_screen(c, ch, _blocked_note(res))
        out = result_screen(c, ch, res, rng)
        if res.skill:
            out.text += "\n" + T.TEACH_XP.format(skill=texts.SKILL_LABELS.get(res.skill, res.skill).lower())
        return Screen(out.text, [[Button(T.PRO_BACK, "back"), Button(T.MENU, "menu")]], name="proout")
    return protege_screen(c, ch)


@on_screen("proout")
async def proout_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    ch = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    return protege_screen(ctx.content, ch)
