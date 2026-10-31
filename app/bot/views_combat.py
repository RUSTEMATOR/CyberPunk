"""Texts of the fight window (PLAN 5.6): the state of both sides and the log of the last ticks.

The engine records structured events; this module turns them into Russian lines. Names typed by
players go through `esc`. Without `enemy_scan` the enemy chrome is "not identified" and only the
implants that work are named.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from app.bot import texts_chrome as TC
from app.bot import texts_combat as T
from app.bot.ui import style
from app.content.loader import Content
from app.engine import combat as cb


MAX_CHARS = 3300  # a window over this loses its oldest log ticks (Telegram caps a message at 4096)
HP_BAR = 8  # cells of your health bar
ENEMY_BAR = 6  # cells of an enemy's health bar
CHARGE_BAR = 5  # cells of an implant's charge bar


def fight_result_word(result: str) -> str:
    return T.FIGHT_RESULT_WORDS.get(result, result)


def faction_name(content: Content, key: str) -> str:
    if key in content.gangs:
        return content.gangs[key].name
    if key in content.corps:
        return content.corps[key].name
    return T.FACTION_NAMES.get(key, key)


def faction_dat(content: Content, key: str) -> str:
    """The faction in the dative ("к Рейфам"): gangs carry the form, corporations stay as they are."""
    if key in content.gangs:
        return content.gangs[key].name_dat or content.gangs[key].name
    return faction_name(content, key)


def implant_name(content: Content, key: str) -> str:
    cw = content.cyberware.get(key)
    return cw.name if cw else key


# owner request 29.10: the hired player's window shows the employer's fight from the hired player's side:
# {"me": ally id, "emp": the employer's name}; None = the usual view of the fight's owner
_VIEW: ContextVar[dict[str, str] | None] = ContextVar("combat_view", default=None)


def _name(cs: cb.CombatState, cid: str, *, cap: bool) -> str:
    v = _VIEW.get()
    if v is not None:
        if cid == v["me"]:
            return T.LOG_YOU_CAP if cap else T.LOG_YOU
        if cid == cb.PLAYER_ID:
            return T.esc(v["emp"])
    if cid == cb.PLAYER_ID:
        return T.LOG_YOU_CAP if cap else T.LOG_YOU
    return T.esc(cs.combatant(cid).name)


def _guard_line(cs: cb.CombatState, k: str, by: str) -> str:
    """The cover lines in three voices: the hired player themself, the employer, anybody else in the fight."""
    v = _VIEW.get()
    name = T.esc(cs.combatant(by).name)
    if v is not None and v["me"] == by:
        return {"guard_up": "🛡 Ты закрываешь нанимателя собой: удары по нему идут в тебя, урон вполовину.",
                "guard": "🛡 Ты принимаешь удары на себя.",
                "guard_hold": "⚔️ Ты встаёшь вместо нанимателя: все удары по нему теперь твои.",
                "guard_off": "↩️ Ты отходишь за спину нанимателя."}[k]
    if v is None:  # the employer's own window
        return {"guard_up": f"🛡 {name} закрывает тебя собой: удары по тебе принимает на себя, урон вполовину.",
                "guard": f"🛡 {name} принимает удары на себя.",
                "guard_hold": f"⚔️ {name} встаёт вместо тебя: все удары по тебе теперь идут в бойца отряда.",
                "guard_off": f"↩️ {name} отходит тебе за спину."}[k]
    return {"guard_up": f"🛡 {name} закрывает нанимателя собой.", "guard": f"🛡 {name} принимает удары на себя.",
            "guard_hold": f"⚔️ {name} встаёт вместо нанимателя.", "guard_off": f"↩️ {name} отходит за спину нанимателя."}[k]


def render_events(content: Content, cs: cb.CombatState, events: list[dict[str, Any]]) -> list[str]:
    """One line per thing that happened. A roll and the hit or dodge that follows it make one line."""
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    last_shot = ""
    i = 0
    while i < len(events):
        e = events[i]
        k = e.get("k")
        nxt = events[i + 1] if i + 1 < len(events) else None
        if k == "roll":
            att, dfn = _name(cs, e["by"], cap=True), _name(cs, e["to"], cap=False)
            w = content.weapons.get(str(e.get("src") or ""))
            if w is not None:  # owner report 23.10: which weapon the player used
                att = f"{att} ({T.esc(w.name)})"
            crit = {"success": T.LOG_CRIT, "fail": T.LOG_FUMBLE}.get(e.get("crit") or "", "")
            if not e["hit"]:
                lines.append(T.LOG_MISS.format(att=att, dfn=dfn, total=e["total"], dv=e["dv"]) + crit)
            elif nxt and nxt.get("k") == "dodge":
                lines.append(T.LOG_DODGE.format(att=att, dfn=dfn, imp=implant_name(content, nxt["imp"])))
                i += 1
            elif nxt and nxt.get("k") == "hit":
                lines.append(T.LOG_HIT.format(icon=_hit_icon(e["to"]), att=att, dfn=dfn, total=e["total"], dv=e["dv"],
                                              dmg=nxt["dmg"], down=_down(nxt)) + crit)
                i += 1
            if w is not None and w.voice is not None and e.get("by") == cb.PLAYER_ID:  # owner request 24.10
                said = voice_line(w, e, nxt, cs.tick + i)
                if said:
                    lines.append(said)
        elif k == "blackwall":  # owner request 23.10: the static of a Blackwall weapon
            lines.append(T.LOG_BLACKWALL.format(dfn=_name(cs, e["to"], cap=False)))
        elif k == "hit":
            if e["kind"] == "blackwall":
                lines.append(T.LOG_BLACKWALL_ECHO.format(icon=_hit_icon(e["to"]), dfn=_name(cs, e["to"], cap=False),
                                                         dmg=e["dmg"], down=_down(e)))
            elif e["kind"] == "rocket":
                lines.append(T.LOG_ROCKET.format(
                    icon=_hit_icon(e["to"]), att=_name(cs, e["by"], cap=True), dfn=_name(cs, e["to"], cap=False),
                    imp=implant_name(content, last_shot), dmg=e["dmg"], down=_down(e)))
        elif k == "shot":
            last_shot = e["imp"]
            lines.append(T.LOG_SHOT.format(att=_name(cs, e["by"], cap=True), imp=implant_name(content, e["imp"]), n=e["n"]))
        elif k == "activate":
            lines.append(T.LOG_ACTIVATE.format(who=_name(cs, e["by"], cap=True), imp=implant_name(content, e["imp"])))
        elif k == "cancel":
            lines.append(T.LOG_CANCEL.format(who=_name(cs, e["by"], cap=True), imp=implant_name(content, e["imp"])))
        elif k == "expired":
            lines.append(T.LOG_EXPIRED.format(who=_name(cs, e["by"], cap=True), imp=implant_name(content, e["imp"])))
        elif k == "overload":
            lines.append(T.LOG_OVERLOAD)
        elif k == "cover":
            lines.append(T.LOG_COVER)
        elif k in ("guard_up", "guard", "guard_hold", "guard_off"):  # owner request 29.10: a hired player shields
            if k == "guard":
                if ("guard", e["by"]) in seen:
                    continue_line = True
                else:
                    seen.add(("guard", e["by"]))
                    continue_line = False
            else:
                continue_line = False
            if not continue_line:
                lines.append(_guard_line(cs, k, e["by"]))
        elif k == "ghost":
            lines.append("👻 Выстрел уходит в пустоту: тебя там уже нет.")
        elif k == "deflect":
            lines.append("⚔️ Клинок встречает пулю. Звон, искры — мимо.")
        elif k == "deadeye":
            lines.append("🎯 Мёртвый глаз: первый выстрел точно в цель.")
        elif k == "ability":  # owner request 27.10: a chrome ability in the fight (engine/ab_power.py)
            lines.append(str(e.get("txt") or ""))
        elif k == "second_wind":
            lines.append(f"💪 Второе дыхание: здоровье +{e.get('n', 0)}.")
        elif k == "dual":
            w = content.weapons.get(str(e.get("src", "")))
            mark = "🔫🔫" if e.get("kind") == cb.RANGED else "⚔️⚔️"
            lines.append(f"{mark} Вторая рука: {T.esc(w.name) if w else 'второй ствол'}.")
        elif k == "glutton":
            lines.append("💉 Обжора войны: заряд инжектора вернулся.")
        elif k == "bloodlust":
            lines.append(f"🩸 Жажда крови: здоровье +{e.get('n', 0)}.")
        elif k == "edgerunner":
            lines.append(f"⚡ Эджраннер: урон +{2 * int(e.get('n', 1) or 1)} до конца боя.")
        elif k == "bolt":
            lines.append("🔩 Болт: выстрел проходит сквозь броню.")
        elif k == "finisher":
            lines.append(f"🗡 Добивание: {_name(cs, e['to'], cap=False)} падает.")
        elif k == "chal_stun":
            lines.append("💫 Шокер вызова: ты теряешь ход.")
        elif k == "wsp":  # owner request 25.10: the effect of a special weapon
            w = content.weapons.get(str(e.get("src", "")))
            wn = T.esc(w.name) if w else "оружие"
            to = _name(cs, e["to"], cap=False)
            lines.append({"shock": f"⚡ {wn}: разряд по хрому, {to} корчится.",
                          "stun": f"💫 {wn}: {to} теряет ход.",
                          "blind": f"🌫 {wn}: {to} слепнет, атаки −{e.get('n', 0)} на два хода.",
                          "drain": f"🩸 {wn}: здоровье +{e.get('n', 0)}.",
                          "splash": f"💥 {wn}: задевает ещё и {to}."}.get(str(e.get("what")), f"✨ {wn}."))
        elif k == "quake":
            lines.append(f"🌋 Землетрясение: {_name(cs, e['to'], cap=False)} теряет ход от удара.")
        elif k == "emp":
            lines.append(f"💥 ЭМИ из хрома: {_name(cs, e['to'], cap=False)} получает разряд.")
        elif k == "heal":
            lines.append(T.LOG_HEAL.format(n=e.get("n", 0)))
        elif k == "reinf":
            lines.append(f"📞 Подкрепление на месте: {T.esc(str(e.get('name', '')))}" +
                         (f" ×{e.get('n')}" if int(e.get("n", 1) or 1) > 1 else "") + ".")
        elif k == "hack":
            prog = content.quickhacks.get(e["prog"])
            pname = T.esc(prog.name) if prog else e["prog"]
            att, dfn = _name(cs, e["by"], cap=True), _name(cs, e["to"], cap=False)
            if e["ok"]:
                lines.append(T.LOG_HACK_OK.format(att=att, dfn=dfn, prog=pname, fx=_hack_fx(e.get("fx") or {})))
            else:
                lines.append(T.LOG_HACK_FAIL.format(att=att, dfn=dfn, prog=pname, total=e["total"], dv=e["dv"]))
        elif k == "stunned":
            lines.append(T.LOG_STUNNED.format(who=_name(cs, e["by"], cap=True)))
        elif k == "flee":
            tpl = {"morale": T.LOG_FLEE_MORALE, "blackwall": T.LOG_FLEE_BLACKWALL}.get(e.get("why") or "", T.LOG_FLEE_COWARD)
            lines.append(tpl.format(who=_name(cs, e["by"], cap=True)))
        elif k == "flee_fail":  # owner request 27.10: the raid boss tried to get away
            lines.append(f"{_name(cs, e['by'], cap=True)} пытается уйти, но путь отрезан.")
        elif k == "exit_fail":
            lines.append(T.LOG_EXIT_FAIL)
        elif k == "reinforce":
            lines.append(T.LOG_REINFORCE)
        elif k == "join":  # M6.6: MaxTac lands in a cyberpsychosis breakdown
            lines.append(TC.PSY_JOIN)
        elif k.startswith("el_"):  # owner request 25.10: the elite fight
            line = _elite_line(cs, e)
            if line:
                lines.append(line)
        i += 1
    return lines


def _elite_line(cs: cb.CombatState, e: dict[str, Any]) -> str:
    k = e["k"]
    who = _name(cs, e["by"], cap=True) if e.get("by") else ""
    if k == "el_aim":
        return f"🎯 {who} берёт тебя на прицел."
    if k == "el_snipe":
        return f"💥 {who} стреляет прицельно" + (" — укрытие спасает половину." if e.get("cover") else ": без укрытия это четверть здоровья.")
    if k == "el_heal":
        return f"💉 {who} лечит: {_name(cs, e['to'], cap=False)} +{e.get('n', 0)}."
    if k == "el_heavy":
        return f"⚡ {who} бьёт со всей силы."
    if k == "el_barrier":
        return f"🔷 Барьер: {_name(cs, e['to'], cap=False)} гасит {e.get('n', 0)} урона (осталось {e.get('left', 0)})."
    if k == "el_phase":
        return (f"⚠️ {who}: барьер снова поднят, на помощь подходят ещё двое." if e.get("n") == 1
                else f"🔥 {who} в ярости: удары сильнее в полтора раза.")
    if k == "el_wave":
        return f"🚪 Волна {e.get('n')}: в комнату заходят новые."
    if k == "el_guard":
        return "🛡 Ты принимаешь удар, предназначенный охраняемому."
    if k == "el_guarding":
        return "🛡 Ты встаёшь между охраняемым и стволами."
    if k == "el_cover":
        return f"🧱 {who} отсиживается в укрытии и ждёт медика."
    return ""


def voice_line(w, roll: dict[str, Any], nxt: dict[str, Any] | None, salt: int) -> str:  # noqa: ANN001
    """What a talking weapon says after its shot: every kill, every other hit or miss (no randomness: by the tick)."""
    v = w.voice
    killed = bool(roll.get("hit") and nxt and nxt.get("k") == "hit" and nxt.get("down") in ("kill", "takedown"))
    pool = v.kill if killed else (v.hit if roll.get("hit") else v.miss)
    if not pool or (not killed and not v.chatty and salt % 2):
        return ""
    return f"🗣 <i>{T.esc(w.name)}: «{T.esc(pool[salt % len(pool)])}»</i>"


def _hit_icon(target: str) -> str:
    """A hit on you bleeds (🩸), a hit you deal explodes (💥)."""
    return T.LOG_HURT_ICON if target == cb.PLAYER_ID or target.startswith("a") else T.LOG_HIT_ICON


def _down(hit: dict[str, Any]) -> str:
    return {"kill": T.LOG_DOWN_KILL, "takedown": T.LOG_DOWN_TAKEN, "downed": T.LOG_DOWN_PLAYER,
            "ally": T.LOG_DOWN_ALLY}.get(hit.get("down") or "", "")


def _hack_fx(fx: dict[str, Any]) -> str:
    out = ""
    if fx.get("damage"):
        out += T.LOG_FX_DAMAGE.format(n=fx["damage"])
    if fx.get("stun"):
        out += T.LOG_FX_STUN.format(n=fx["stun"])
    if fx.get("penalty"):
        out += T.LOG_FX_PENALTY.format(n=fx["penalty"])
    if fx.get("drain"):
        out += T.LOG_FX_DRAIN.format(n=fx["drain"])
    if fx.get("disable"):
        out += T.LOG_FX_DISABLE
    return out


def _player_block(content: Content, cs: cb.CombatState) -> list[str]:
    p = cs.player
    head = T.CM_PLAYER.format(bar=style.bar(p.hp, p.max_hp, HP_BAR), hp=p.hp, max=p.max_hp)
    if p.stun:
        head += T.CM_STUNNED
    lines = [head, T.CM_ARMOR.format(armor=cb.armor_effect(p, content))]
    if p.arsenal:  # owner report 23.10: every weapon at hand, each has its own button
        names = [T.esc(content.weapons[k].name) for k in p.arsenal if k in content.weapons]
        lines[-1] += " · оружие: " + ", ".join(names)
    else:
        gun = (p.attacks.get(cb.RANGED) or p.attacks.get(cb.MELEE)) if p.attacks else None
        if gun is not None:  # owner request: what is in the hand
            src = content.weapons.get(gun.source) or content.cyberware.get(gun.source)
            lines[-1] += " · " + T.CM_WEAPON.format(name=T.esc(src.name) if src else T.CM_FISTS)
    if p.ram_max:
        lines.append(T.CM_RAM_LINE.format(bar=style.bar(p.ram, p.ram_max, CHARGE_BAR), ram=p.ram, max=p.ram_max))
    for imp in p.implants:
        spec = cb.spec_of(p, imp, content)
        name = T.esc(implant_name(content, imp.key))
        bar = style.bar(imp.charge, 100, CHARGE_BAR)
        if imp.disabled_ticks > 0:
            lines.append(T.CM_IMPLANT_OFF.format(name=name))
        elif imp.running:
            lines.append(T.CM_IMPLANT_RUNNING.format(name=name, bar=bar, charge=imp.charge))
        elif spec.mode == "reactive" and spec.trigger_toggle:
            lines.append(T.CM_IMPLANT_AUTO.format(name=name, bar=bar, charge=imp.charge,
                                                  state=T.CM_ON if imp.auto else T.CM_OFF))
        else:
            lines.append(T.CM_IMPLANT_IDLE.format(name=name, bar=bar, charge=imp.charge))
    return lines


def _enemy_lines(content: Content, cs: cb.CombatState, target: int) -> list[str]:
    scan = cs.player.scan
    lines = [T.CM_ENEMIES]
    for i, e in enumerate(cs.enemies):
        if e.out:
            lines.append(T.CM_ENEMY_OUT_LINE.format(
                mark=T.CM_ENEMY_MARKS.get(e.out, T.CM_ENEMY_MARKS["killed"]), i=i + 1, name=T.esc(e.name),
                status=T.CM_ENEMY_OUT.get(e.out, e.out)))
            continue
        mark = T.CM_ENEMY_MARKS["target" if i == target and e.active else "idle"]
        status = T.CM_ENEMY_STUNNED if e.stun else ""
        lines.append(T.CM_ENEMY.format(mark=mark, i=i + 1, name=T.esc(e.name), bar=style.bar(e.hp, e.max_hp, ENEMY_BAR),
                                       hp=e.hp, max=e.max_hp, status=status))
        if e.role:  # owner request 25.10: the elite fight: the role, the barrier, the resistance, the intent
            from app.engine import elite

            bits = [elite.ROLE_RU.get(e.role, e.role)]
            if e.barrier:
                bits.append(f"🔷 барьер {e.barrier}")
            if e.resist:
                bits.append(f"броня против: {elite.RESIST_RU[e.resist]} (урон ×½)")
            lines.append("   " + " · ".join(bits))
            it = elite.INTENT_RU.get(e.intent, "")
            if it:
                lines.append("   <b>" + it + "</b>")
        running = [T.esc(implant_name(content, imp.key)) for imp in e.implants if imp.running]
        if scan and e.implants:
            items = ", ".join(
                T.CM_ENEMY_CHROME_ITEM.format(name=T.esc(implant_name(content, imp.key)), charge=imp.charge)
                for imp in e.implants
            )
            lines.append("   " + T.CM_ENEMY_CHROME.format(items=items))
        elif running:
            lines.append("   " + T.CM_ENEMY_RUNNING.format(names=", ".join(running)))
        elif e.has_chrome and not scan:
            lines.append("   " + T.CM_CHROME_UNKNOWN)
    return lines


def combat_text(content: Content, cs: cb.CombatState, target: int, notes: list[str] | None = None) -> str:
    """The whole window: header, both sides, the folded old ticks, the last three ticks, the tick in progress."""
    out = [_title(content, cs), ""]
    if cs.reinforced:
        out += [T.CM_REINFORCED, ""]
    out += _player_block(content, cs)
    if cs.allies:  # owner request 24.10: the squad fights too
        out.append("")
        out.append("<b>Твой отряд</b>")
        guard = cb.cover_guard(cs)
        for a in cs.allies:
            if a.active:
                out.append(f"🤝 {T.esc(a.name)} {style.bar(a.hp, a.max_hp, ENEMY_BAR)} {a.hp}/{a.max_hp}"
                           + (" · 🛡 прикрывает тебя" if guard is a else ""))
            else:
                out.append(f"✝ <s>{T.esc(a.name)}</s> — выбыл")
    out.append("")
    out += _enemy_lines(content, cs, target)
    return _with_log(content, cs, out, notes)


def _title(content: Content, cs: cb.CombatState) -> str:
    maxr = content.balance.combat.max_rounds * (3 if cs.ctx.get("elite") else 1)
    named = cs.ctx.get("title") or cs.ctx.get("foe") or (f"«{cs.ctx['dyn_name']}»" if cs.ctx.get("dyn_name") else "")
    fname = T.esc(str(named) if named else faction_name(content, cs.faction))
    return T.CM_TITLE.format(faction=fname, tick=min(cs.tick, maxr), max=maxr) if not cs.reinforced \
        else T.CM_TITLE_LATE.format(faction=fname, tick=cs.tick)


def _with_log(content: Content, cs: cb.CombatState, head: list[str], notes: list[str] | None) -> str:
    blocks: list[list[str]] = []
    for t in cs.log:
        body = render_events(content, cs, t["ev"])
        if body:
            blocks.append([T.CM_TICK.format(tick=t["tick"]), *body])
    now = render_events(content, cs, cs.cur_events)
    if now:
        blocks.append([T.CM_NOW, *now])
    head = list(head)
    if notes:
        head_tail = ["", *[T.CM_NOTE_PREFIX.format(text=n) for n in notes]]
    else:
        head_tail = []

    n_log = len(blocks) - (1 if now else 0)  # the finished ticks; the tick in progress ("Сейчас") is the last block

    def build(from_block: int) -> str:
        """The earlier ticks fold into an expandable quote (a tap opens them); the last finished tick and the tick in
        progress stay plain, so the latest exchange reads without a tap."""
        folded: list[str] = []
        if cs.old:
            tpl = T.CM_OLD_ONE if cs.old["from"] == cs.old["to"] else T.CM_OLD
            folded.append(tpl.format(from_=cs.old["from"], to=cs.old["to"], dealt=cs.old["dealt"], taken=cs.old["taken"]))
        plain: list[str] = []
        for k, b in enumerate(blocks[from_block:], from_block):
            (folded if k < n_log - 1 else plain).extend(b)
        log = ([style.expandable("\n".join(folded))] if folded else []) + plain
        return "\n".join(head + (["", *log] if log else []) + head_tail)

    start = 0
    text = build(start)
    while len(text) > MAX_CHARS and start < len(blocks) - 1:  # drop the oldest tick of the log
        start += 1
        text = build(start)
    return text


def _fighter_block(content: Content, p: cb.Combatant) -> list[str]:
    """«Ты» with health, armor, weapons, RAM and chrome, for any fighter of the fight (the hired player's copy too)."""
    fake = cb.CombatState(faction="", danger=0, player=p, enemies=[])
    return _player_block(content, fake)


def ally_text(content: Content, cs: cb.CombatState, aid: str, emp_name: str, notes: list[str] | None = None) -> str:
    """Owner request 29.10: the employer's fight as the hired player sees it — like the own fight window, «Ты» is the
    hired player, the employer is named."""
    me = next((a for a in cs.allies if a.id == aid), None)
    token = _VIEW.set({"me": aid, "emp": emp_name})
    try:
        out = [_title(content, cs).replace("Бой:", "Бой нанимателя:", 1), ""]
        if me is not None and me.active:
            out += _fighter_block(content, me)
        else:
            out.append("✝ <b>Ты</b> выбыл из этого боя.")
        guard = cb.cover_guard(cs)
        p = cs.player
        mark = ""
        if guard is not None:
            mark = " · 🛡 ты прикрываешь" if guard.id == aid else f" · 🛡 прикрывает {T.esc(guard.name)}"
        out += ["", f"🧑‍💼 <b>{T.esc(emp_name)}</b> {style.bar(max(0, p.hp), p.max_hp, HP_BAR)} {max(0, p.hp)}/{p.max_hp}{mark}"]
        others = [a for a in cs.allies if a.id != aid]
        if others:
            for a in others:
                out.append(f"🤝 {T.esc(a.name)} {style.bar(a.hp, a.max_hp, ENEMY_BAR)} {a.hp}/{a.max_hp}" if a.active
                           else f"✝ <s>{T.esc(a.name)}</s> — выбыл")
        out.append("")
        out += _enemy_lines(content, cs, -1)
        return _with_log(content, cs, out, notes)
    finally:
        _VIEW.reset(token)


# -- the exits before or inside a fight (owner report 24.10: «не информативное окно») -----------------------------------

DANGER_WORDS = {1: "шпана", 2: "крепкие ребята", 3: "серьёзные бойцы", 4: "элита", 5: "смертельно опасно"}
def surrender_odds(content: Content, faction: str) -> str:
    """'отпустят 40%, оберут 30%, …' for a faction, or "" when it has no disposition."""
    from app.engine import exits as ex

    disp = ex.disposition_for(content, faction)
    if disp is None:
        return ""
    w = {k: v for k, v in disp.surrender.model_dump().items() if v > 0}
    total = sum(w.values()) or 1
    return ", ".join(f"{SURRENDER_WORDS.get(k, k)} {round(100 * v / total)}%" for k, v in sorted(w.items(), key=lambda x: -x[1]))


def surrender_confirm_text(content: Content, faction: str) -> str:
    """Owner request 25.10: serious buttons ask first."""
    odds = surrender_odds(content, faction)
    return ("<b>Точно сдаться?</b>\nОтменить нельзя: бой кончится, и что с тобой сделают, решат враги."
            + (f"\nЧего ждать: {odds}." if odds else ""))


SURRENDER_WORDS = {"released": "отпустят", "robbed": "оберут", "beaten": "изобьют", "chrome_stripped": "вырежут хром",
                   "arrested": "арест", "captured": "выкуп", "executed": "казнь", "vanished": "увезут на недели"}
_EXIT_CHECK = {"bribe": ("COOL", "persuasion", ("bribe", "social")), "talk": ("COOL", "persuasion", ("talk_exit", "social")),
               "flee": ("REF", "stealth", ("escape",))}


def exit_base(state, content: Content, kind: str) -> int:  # noqa: ANN001
    from app.engine import derived

    stat, skill, ctx = _EXIT_CHECK[kind]
    extra = derived.mod(state, content, "flee") if kind == "flee" else 0
    return (derived.stat_total(state, content, stat) + derived.skill_total(state, content, skill)
            + derived.check_bonus(state, content, ctx) + extra)


def exit_pct(state, content: Content, kind: str, dv: int) -> int:  # noqa: ANN001
    from app.engine import checks

    return round(100 * checks.pass_chance(exit_base(state, content, kind), dv, content.balance.checks.die_sides))


def you_line(content: Content, state) -> str:  # noqa: ANN001
    """HP, armour, weapons, money, luck and glitches of the character before a fight."""
    p = cb.build_player(state, content)
    names = [T.esc(content.weapons[k].name) for k in p.arsenal if k in content.weapons]
    weapons = ", ".join(names) if names else "кулаки"
    return (f"❤️ {style.bar(p.hp, p.max_hp, HP_BAR)} {p.hp}/{p.max_hp} · броня {cb.armor_effect(p, content)} · "
            f"оружие: {weapons}\n💰 {state.eddies} эдди · удача {state.luck_pool} · глюки {state.glitches} · "
            f"розыск {state.heat}/5")


def exit_lines(content: Content, state, faction: str, danger: int | None, opts) -> list[str]:  # noqa: ANN001
    """What each way out checks, the odds and the price of a failure."""
    from app.engine import exits as ex

    out = [you_line(content, state)]
    if danger:
        out.append(f"Опасность {danger}/5: {DANGER_WORDS.get(int(danger), '')}.")
    out.append("")
    label = {"COOL": "ХЛАД", "REF": "РЕФЛ"}
    skill = {"persuasion": "убеждение", "stealth": "скрытность"}
    taken = round(content.balance.exits.bribe.fail_taken_chance * 100)
    for o in opts:
        if not o.possible or o.dv is None:
            continue
        if o.kind not in _EXIT_CHECK:
            continue
        stat, sk, _ = _EXIT_CHECK[o.kind]
        base = exit_base(state, content, o.kind)
        pct = exit_pct(state, content, o.kind, o.dv)
        head = {"bribe": f"Взятка {o.cost}", "talk": "Уговорить", "flee": "Бежать"}[o.kind]
        fail = {"bribe": f"провал: деньги заберут с шансом {taken}%, и всё равно бой",
                "talk": "провал: бьют первыми", "flee": "провал: удар в спину и бой"}[o.kind]
        out.append(f"• <b>{head}</b>: {label[stat]} + {skill[sk]} = {base:+d} к d10 против {o.dv} → шанс ~{pct}%; {fail}.")
    odds = surrender_odds(content, faction)
    if odds:
        out.append("• <b>Сдаться</b>: " + odds + ".")
    out.append("• <b>Принять бой</b>: пошаговый бой; выйти из него можно и по ходу, через кнопку выхода.")
    return out
