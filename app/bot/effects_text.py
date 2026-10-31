"""What an item does, in plain Russian: one helper for every shop, card and list.

Turns `mods`, cyberware (weapon, deck, resurrection, the active spec), weapons, armor and quickhacks into short
phrases: «Стрельба +2», «урон в ближнем бою +3», «зарплата корпората +15%», «+1 ОД в неделю».

Every mod key the grammar allows has a label here (`missing_labels` lists the ones that do not). At runtime an
unknown key falls back to the raw key; `mod_phrase(..., strict=True)` raises instead, for checks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.bot.texts import SKILL_LABELS, STAT_LABELS
from app.content.loader import Content
from app.content.schemas import (
    MOD_ACTIVE_FIELDS, MOD_CHECK_CONTEXTS, MOD_PLAIN_KEYS, SKILLS, STATS,
    ActiveSpec, Armor, Cyberware, Quickhack, Weapon,
)

# -- small helpers ---------------------------------------------------------------------------------------------------


def signed(n: int) -> str:
    return f"+{n}" if n > 0 else f"−{abs(n)}" if n < 0 else "0"


def plural(n: int, words: tuple[str, str, str]) -> str:
    n10, n100 = abs(n) % 10, abs(n) % 100
    if n10 == 1 and n100 != 11:
        return words[0]
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return words[1]
    return words[2]


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


TICKS = ("ход", "хода", "ходов")
TARGETS = ("цель", "цели", "целей")
UPTO_TARGETS = ("цели", "целей", "целей")  # after «до»: до 1 цели, до 2 целей

# -- mod labels ------------------------------------------------------------------------------------------------------

# {v} = signed value, {n} = absolute value, {inv} = the value with the sign flipped (a bonus that lowers a number),
# {targets} = «цель/цели/целей» for the value. "plus|minus" = separate wording for a positive and a negative value.
PLAIN: dict[str, str] = {
    "hp_max": "максимум здоровья {v}",
    "armor": "защита {v}",
    "damage_reduction": "входящий урон {inv}",
    "melee_damage": "урон в ближнем бою {v}",
    "ranged_damage": "урон при стрельбе {v}",
    "strength": "проверки силы {v}",
    "flee": "побег из боя {v}",
    "bleed": "урон от кровотечения {inv}",
    "hp_regen": "здоровье {v} в неделю",
    "mood_weekly": "настроение {v} в неделю",
    "ap_week": "{v} ОД в неделю",
    "gig_pay_pct": "оплата заказов {v}%",
    "crime_reward_pct": "добыча с криминала {v}%",
    "shop_discount_pct": "скидка в магазинах {n}%|наценка в магазинах {n}%",
    "ripper_discount_pct": "скидка у рипперов {n}%|наценка у рипперов {n}%",
    "relation_gain": "рост отношений {v}",
    "heat_decay": "розыск спадает быстрее на {n} в неделю|розыск спадает медленнее на {n} в неделю",
    "humanity_loss_pct": "потеря человечности от новых имплантов {v}%",
    "therapy_bonus": "терапия {v} человечности",
    "glitch_max": "максимум глюков {v}",
    "ram_max": "RAM деки {v}",
    "ram_recharge": "подзарядка RAM {v} за ход",
    "quickhack_cost": "цена взломов {v} RAM",
    "quickhack_targets": "{v} {targets} у каждого взлома",
    "ice": "защита от взломов {v}",
    "enemy_scan": "в бою видны импланты и заряд врагов",
    "humanity_floor": "человечность не падает ниже {n}, киберпсихоз не грозит",
    "wealth_guard": "угрозы богатым реже на {n}%|угрозы богатым чаще на {n}%",
    "robbery_loss_pct": "потери при грабежах и поборах {v}%",
    "immuno_cost_pct": "иммунодепрессанты {v}% к цене",
    "age_weeks": "стареешь быстрее: +{n} нед. возраста в неделю",
    "chrome_load_pct": "нагрузка хрома на тело −{n}%",
    "med_charges": "заряды инжектора в бою {v}",
    "food_x": "жор: еда стоит +{n}× сверх обычного",
}
# Keys where a smaller number is the good side: a minus on them is a bonus, a plus is a side effect.
LOWER_IS_BETTER: frozenset[str] = frozenset({"humanity_loss_pct", "quickhack_cost", "min_charge", "cost",
                                            "robbery_loss_pct", "immuno_cost_pct", "age_weeks", "food_x"})

CHECKS: dict[str, str] = {
    "combat": "проверки в бою",
    "gig_loud": "заказы «громко»",
    "gig_quiet": "заказы «тихо»",
    "gig_talk": "заказы «договориться»",
    "gig_hack": "заказы «взлом»",
    "crime": "криминал",
    "career": "проверки на работе",
    "social": "общение и уговоры",
    "escape": "побег и скрытность",
    "heal": "лечение",
    "bribe": "взятка в бою",
    "talk_exit": "переговоры в бою",
    "meditation": "медитация",
}
SKILL_GENITIVE: dict[str, str] = {
    "firearms": "Стрельбы", "melee": "Ближнего боя", "stealth": "Скрытности", "netrunning": "Нетраннинга",
    "tech": "Техники", "medicine": "Медицины", "persuasion": "Убеждения", "driving": "Вождения",
}
SALARY: dict[str, str] = {
    "all": "зарплата {v}%",
    "corpo": "зарплата корпората {v}%",
    "ncpd": "зарплата в NCPD {v}%",
    "trauma_team": "зарплата в Trauma Team {v}%",
    "media": "зарплата в медиа {v}%",
    "rockerboy": "гонорары рокербоя {v}%",
    "mechanic": "зарплата механика {v}%",
    "maxtac": "зарплата в МаксТаке {v}%",
}
# Active implant groups (`exclusive_group` of an implant with an `active` block) and `all`.
GROUPS: dict[str, str] = {
    "all": "все активные импланты",
    "sandevistan": "Сандевистан",
    "kerenzikov": "Керензиков",
    "berserk": "Берсерк",
    "projectile_launcher": "ракетная установка",
    "camo": "камуфляж",
}


def _active_field(group: str, field: str, val: int) -> str | None:
    n = abs(val)
    if field == "min_charge":
        return f"{group}: порог включения {'ниже' if val < 0 else 'выше'} на {n}%"
    if field == "recharge_per_tick":
        return f"{group}: подзарядка в бою {signed(val)}% за ход"
    if field == "recharge_on_kill":
        return f"{group}: {signed(val)}% заряда за убийство"
    if field == "recharge_on_takedown":
        return f"{group}: {signed(val)}% заряда за вырубленного врага"
    if field == "duration_ticks":
        return f"{group}: длительность {signed(val)} {plural(val, TICKS)}"
    if field == "cost":
        return f"{group}: срабатывание {'дешевле' if val < 0 else 'дороже'} на {n}% заряда"
    return None


def mod_phrase(key: str, val: int, *, strict: bool = False) -> str:
    """One modifier as a short phrase, lower case: 'Стрельба +2', 'урон в ближнем бою +3'."""
    base, _, rest = key.partition(".")
    sv = signed(val)
    out: str | None = None
    if not rest and base in PLAIN:
        tpl = PLAIN[base]
        if "|" in tpl:
            pos, neg = tpl.split("|")
            tpl = pos if val > 0 else neg
        out = tpl.format(v=sv, n=abs(val), inv=signed(-val), targets=plural(val, TARGETS))
    elif base == "stat" and rest in STAT_LABELS:
        out = f"{STAT_LABELS[rest]} {sv}"
    elif base == "skill" and rest in SKILL_LABELS:
        out = f"{SKILL_LABELS[rest]} {sv}"
    elif base == "xp" and rest == "all":
        out = f"опыт всех навыков {sv}%"
    elif base == "xp" and rest in SKILL_GENITIVE:
        out = f"опыт {SKILL_GENITIVE[rest]} {sv}%"
    elif base == "check" and rest in CHECKS:
        out = f"{CHECKS[rest]} {sv}"
    elif base == "salary_pct" and rest in SALARY:
        out = SALARY[rest].format(v=sv)
    elif base == "active":
        group, _, field = rest.partition(".")
        if group in GROUPS:
            out = _active_field(GROUPS[group], field, val)
    if out is None:
        if strict:
            raise KeyError(f"no plain-language label for mod key {key!r}")
        return f"{key} {sv}"
    return out


def is_bonus(key: str, val: int) -> bool:
    """True when this modifier helps the character (a side effect is False)."""
    field = key.rsplit(".", 1)[-1]
    return val < 0 if field in LOWER_IS_BETTER else val > 0


def mods_phrases(mods: Mapping[str, int]) -> list[str]:
    """Every modifier, bonuses first in the authored order, then the side effects."""
    items = [(k, int(v)) for k, v in mods.items() if v]
    good = [mod_phrase(k, v) for k, v in items if is_bonus(k, v)]
    bad = [mod_phrase(k, v) for k, v in items if not is_bonus(k, v)]
    return good + bad


def mods_line(mods: Mapping[str, int]) -> str:
    return ", ".join(mods_phrases(mods))


def _pick(good: list[str], bad: list[str], limit: int) -> list[str]:
    """The most important phrases: the first bonuses, and the first side effect if there is one."""
    if not bad:
        return good[:limit]
    return good[:max(1, limit - 1)] + bad[:1] if good else bad[:limit]


# -- active implants -------------------------------------------------------------------------------------------------


def active_effects(spec: ActiveSpec) -> list[str]:
    """What a running implant gives (duration mode) or what a trigger does (reactive)."""
    e = spec.effects
    out: list[str] = []
    if e.extra_actions:
        out.append(f"{signed(e.extra_actions)} {plural(e.extra_actions, ('атака', 'атаки', 'атак'))} за ход")
    if e.melee_bonus:
        out.append(f"Ближний бой {signed(e.melee_bonus)}")
    if e.check_bonus:
        out.append(f"проверки в бою {signed(e.check_bonus)}")
    if e.damage_reduction:
        out.append(f"входящий урон {signed(-e.damage_reduction)}")
    if e.enemy_attack_penalty:
        out.append(f"атаки врагов {signed(-e.enemy_attack_penalty)}")
    if e.dodge:
        out.append("уклонение от попадания")
    if e.next_attack_bonus:
        out.append(f"следующая атака {signed(e.next_attack_bonus)}")
    return out


def _recharge(spec: ActiveSpec) -> str:
    bits: list[str] = []
    if spec.recharge_per_tick:
        bits.append(f"+{spec.recharge_per_tick}% за ход")
    if spec.recharge_on_kill:
        bits.append(f"+{spec.recharge_on_kill}% за убийство")
    if spec.recharge_on_takedown:
        bits.append(f"+{spec.recharge_on_takedown}% за вырубленного врага")
    return ", ".join(bits)


def active_short(spec: ActiveSpec) -> str:
    """One line: «Сандевистан: включается от 60%, 3 хода на полном заряде, можно выключить раньше; пока работает: …»."""
    eff = ", ".join(active_effects(spec))
    rech = _recharge(spec)
    tail = f"; подзарядка {rech}" if rech else ""
    if spec.mode == "duration":
        start = "включается только на полном заряде" if spec.min_charge >= 100 else f"включается от {spec.min_charge}%"
        stop = "можно выключить раньше" if spec.can_cancel else "не выключается до конца заряда"
        n = spec.duration_ticks
        return f"{spec.button}: {start}, {n} {plural(n, TICKS)} на полном заряде, {stop}; пока работает: {eff}{tail}"
    if spec.mode == "reactive":
        auto = ", автосрабатывание можно выключить" if spec.trigger_toggle else ""
        return (f"{spec.button}: срабатывает сам, когда по тебе попадают, от {spec.min_charge}% заряда, "
                f"тратит {spec.cost}%{auto}; {eff}{tail}")
    t = spec.effects.targets
    return (f"{spec.button}: {spec.effects.damage} за ракету, до {t} {plural(t, UPTO_TARGETS)}, {spec.cost}% заряда за ракету, "
            f"стрелять можно от {spec.min_charge}%{tail}")


def active_compact(spec: ActiveSpec) -> str:
    """For a list line, next to the implant's name: the effect first, then from what charge it works."""
    eff = ", ".join(active_effects(spec))
    if spec.mode == "duration":
        n = spec.duration_ticks
        start = "включается с полного заряда" if spec.min_charge >= 100 else f"включается от {spec.min_charge}%"
        return f"{eff}; {start}, {n} {plural(n, TICKS)}"
    if spec.mode == "reactive":
        return f"{eff}; срабатывает сам от {spec.min_charge}% заряда"
    t = spec.effects.targets
    return f"ракеты {spec.effects.damage}, до {t} {plural(t, UPTO_TARGETS)}; от {spec.min_charge}% заряда"


def active_full(spec: ActiveSpec, content: Content) -> list[str]:
    """The card's block (the button is in its header): threshold, duration, switching off, effects, recharge."""
    lines: list[str] = []
    eff = ", ".join(active_effects(spec))
    if spec.mode == "duration":
        n = spec.duration_ticks
        if spec.min_charge >= 100:
            lines.append(f"Включается только на полном заряде и работает {n} {plural(n, TICKS)}.")
        else:
            lines.append(f"Включается от {spec.min_charge}% заряда. На полном заряде работает {n} {plural(n, TICKS)}, "
                         f"на половине — вдвое меньше.")
        lines.append(f"Пока работает: {eff}.")
        lines.append("Можно выключить раньше, остаток заряда сохранится." if spec.can_cancel
                     else "Выключить раньше нельзя: раз включился, горит до нуля.")
    elif spec.mode == "reactive":
        lines.append(f"Срабатывает сам, когда по тебе попадают: нужно от {spec.min_charge}% заряда, "
                     f"за раз уходит {spec.cost}%.")
        lines.append(f"При срабатывании: {eff}.")
        lines.append("Автосрабатывание можно выключить, чтобы не тратить заряд на слабые попадания." if spec.trigger_toggle
                     else "Выключить нельзя: срабатывает на любом попадании.")
    else:
        t = spec.effects.targets
        lines.append(f"Ракета бьёт на {spec.effects.damage}, залп — до {t} {plural(t, UPTO_TARGETS)}. "
                     f"Каждая ракета стоит {spec.cost}% заряда, стрелять можно от {spec.min_charge}%.")
    if spec.recharge_per_tick:
        idle = ", пока имплант выключен" if spec.mode == "duration" else ""
        lines.append(f"Подзарядка в бою: +{spec.recharge_per_tick}% за ход{idle}.")
    kills: list[str] = []
    if spec.recharge_on_kill:
        kills.append(f"за убийство +{spec.recharge_on_kill}%")
    if spec.recharge_on_takedown:
        kills.append(f"за вырубленного врага +{spec.recharge_on_takedown}%")
    if kills:
        lines.append(f"{cap(', '.join(kills))}, даже пока имплант работает.")
    act = content.balance.cyber.active
    lines.append(f"Вне боя заряд растёт на {act.recharge_per_ap}% за каждое потраченное ОД, к концу недели он полный.")
    lines.append(f"При человечности {act.overload_humanity_threshold} и ниже каждое включение "
                 f"бьёт по ней: человечность −{act.overload_humanity_cost}.")
    return lines


# -- cyberware -------------------------------------------------------------------------------------------------------


def _weapon_phrase(cw: Cyberware, content: Content) -> str:
    out = f"оружие ближнего боя {cw.weapon_damage}"
    if cw.weapon_targets > 1:
        out += f", бьёт до {cw.weapon_targets} {plural(cw.weapon_targets, UPTO_TARGETS)} за удар"
    if cw.key in content.balance.combat.nonlethal_cyberware:
        out += ", вырубает, а не убивает"
    return out


def _dependants(cw: Cyberware, content: Content) -> list[Cyberware]:
    return [c for c in content.cyberware.values() if cw.key in c.requires and not c.not_for_sale]


def _base_phrase(cw: Cyberware, content: Content) -> str | None:
    deps = _dependants(cw, content)
    if not deps:
        return None
    if any(d.deck is not None for d in deps):
        return "без него не поставить кибердеку и сетевой хром"
    return f"основа для: {', '.join(d.name for d in deps[:3])}"


def _special(cw: Cyberware, content: Content, full: bool) -> list[str]:
    """Weapon, deck, resurrection, full conversion: the parts that are not `mods`."""
    out: list[str] = []
    if cw.weapon_damage:
        out.append(_weapon_phrase(cw, content))
    if cw.deck is not None:
        d = cw.deck
        if full:
            bonus = f", бонус к броску взлома {signed(d.hack_bonus)}" if d.hack_bonus else ""
            out.append(f"кибердека: RAM {d.ram_max}, подзарядка {d.ram_recharge} RAM за ход{bonus}; "
                       f"запускает быстрые взломы из «Софта»")
        else:
            out.append(f"кибердека, RAM {d.ram_max}")
    for key in cw.grants_hacks:
        qh = content.quickhacks.get(key)
        if qh is not None:
            out.append(f"программа «{qh.name}» для кибердеки ({qh.ram_cost} RAM)" + (f": {qh.description}" if full else ""))
    if cw.resurrection:
        pct = round(content.balance.cyber.second_heart_revive_hp_pct * 100)
        out.append(f"один раз спасает от смерти: встаёшь с {pct}% здоровья, после этого сгорает" if full
                   else "одно спасение от смерти")
    if cw.borg_risk:  # owner request 27.10
        fc = content.balance.cyber.full_conversion
        lo, hi = round(min(fc.black_death.values()) * 100), round(max(fc.black_death.values()) * 100)
        out.append(f"подпольная операция: на столе умирают от {lo}% до {hi}% по типу тела, сильное ТЕЛО снижает риск"
                   if full else f"риск умереть на столе {lo}–{hi}%")
    if cw.smasher_only or cw.borg_risk:
        emp = content.balance.cyber.full_conversion.emp_fixed
        out.append(f"тело меняют целиком: киберпсихоз больше не грозит, но ЭМП падает до {emp}, "
                   f"а родные и партнёр перестают видеть в тебе человека" if full
                   else "новое тело: киберпсихоз не грозит, ЭМП падает")
    return out


def cyberware_phrases(cw: Cyberware, content: Content) -> list[str]:
    """Every passive effect of an implant (no active block), bonuses first."""
    out = _special(cw, content, full=True) + mods_phrases(cw.mods)
    if not out and cw.active is None:
        base = _base_phrase(cw, content)
        out.append(base if base else "сам по себе ничего не даёт")
    return out


def cyberware_points(cw: Cyberware, content: Content, limit: int = 4) -> list[str]:
    """Owner request 25.10: the most important effects, one per bullet (the ripper's catalog)."""
    lead = _special(cw, content, full=False)
    if cw.active is not None:
        lead.insert(0, active_compact(cw.active))
    items = [(k, int(v)) for k, v in cw.mods.items() if v]
    good = lead + [mod_phrase(k, v) for k, v in items if is_bonus(k, v)]
    bad = [mod_phrase(k, v) for k, v in items if not is_bonus(k, v)]
    picked = _pick(good, bad, limit) or [_base_phrase(cw, content) or "сам по себе ничего не даёт"]
    return [cap(x) for x in picked]


def cyberware_summary(cw: Cyberware, content: Content, limit: int = 3, capitalize: bool = True) -> str:
    """One line for a list: the 2-3 most important effects."""
    lead = _special(cw, content, full=False)
    if cw.active is not None:
        lead.insert(0, active_compact(cw.active))
    items = [(k, int(v)) for k, v in cw.mods.items() if v]
    good = lead + [mod_phrase(k, v) for k, v in items if is_bonus(k, v)]
    bad = [mod_phrase(k, v) for k, v in items if not is_bonus(k, v)]
    picked = _pick(good, bad, limit)
    if not picked:
        picked = [_base_phrase(cw, content) or "сам по себе ничего не даёт"]
    line = "; ".join(picked) if cw.active is not None else ", ".join(picked)
    return cap(line) if capitalize else line


# -- weapons and armor -----------------------------------------------------------------------------------------------


def weapon_phrases(w: Weapon) -> list[str]:
    out = [f"{SKILL_LABELS[w.skill]}, урон {w.damage}"]
    if w.extra_attempts:
        out.append(f"{signed(w.extra_attempts)} {plural(w.extra_attempts, ('выстрел', 'выстрела', 'выстрелов'))} за ход")
    if w.close_range_only:
        out.append("полный урон только вблизи")
    return out + mods_phrases(w.mods)


def armor_phrases(a: Armor) -> list[str]:
    return [f"защита {a.defense}"] + mods_phrases(a.mods)


# -- quickhacks ------------------------------------------------------------------------------------------------------


def quickhack_phrases(qh: Quickhack) -> list[str]:
    e = qh.effects
    out: list[str] = []
    if e.damage:
        out.append(f"урон {e.damage}")
    if e.stun_ticks:
        out.append(f"цель пропускает {e.stun_ticks} {plural(e.stun_ticks, TICKS)}")
    if e.attack_penalty:
        out.append(f"атаки цели −{e.attack_penalty} на {e.penalty_ticks} {plural(e.penalty_ticks, TICKS)}")
    if e.drain_charge:
        out.append(f"заряд имплантов цели −{e.drain_charge}%")
    if e.disable_ticks:
        out.append(f"работающий имплант цели гаснет на {e.disable_ticks} {plural(e.disable_ticks, TICKS)}")
    if e.spread:
        out.append("перескакивает на вторую цель")
    if qh.environmental:
        out.append("идёт через камеры и свет, берёт и тех, кто без хрома")
    return out


def quickhack_line(qh: Quickhack) -> str:
    return f"{qh.ram_cost} RAM за запуск: " + ", ".join(quickhack_phrases(qh))


# -- the label check -------------------------------------------------------------------------------------------------


def active_groups(content: Content) -> list[str]:
    return sorted({cw.exclusive_group for cw in content.cyberware.values() if cw.active is not None and cw.exclusive_group})


def all_mod_keys(content: Content) -> list[str]:
    """Every key the mod grammar allows with this content, plus every key the content actually uses."""
    keys: list[str] = list(MOD_PLAIN_KEYS)
    keys += [f"stat.{s}" for s in STATS] + [f"skill.{s}" for s in SKILLS]
    keys += ["xp.all"] + [f"xp.{s}" for s in SKILLS]
    keys += [f"check.{c}" for c in MOD_CHECK_CONTEXTS] + ["check.meditation"]
    keys += ["salary_pct.all"] + [f"salary_pct.{c}" for c in content.careers]
    keys += [f"active.{g}.{f}" for g in ["all", *active_groups(content)] for f in MOD_ACTIVE_FIELDS]
    keys += list(_content_mod_keys(content))
    return sorted(set(keys))


def _content_mod_keys(content: Content) -> Iterable[str]:
    from pydantic import BaseModel

    seen: set[int] = set()

    def walk(o: object) -> Iterable[str]:
        if isinstance(o, BaseModel):
            if id(o) in seen:
                return
            seen.add(id(o))
            for name in type(o).model_fields:
                v = getattr(o, name)
                if name == "mods" and isinstance(v, dict):
                    yield from v.keys()
                else:
                    yield from walk(v)
        elif isinstance(o, dict):
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                yield from walk(v)

    for attr in vars(content).values():
        yield from walk(attr)


def missing_labels(content: Content) -> list[str]:
    """Mod keys that would show up raw. Empty means every key reads in plain Russian."""
    bad: list[str] = []
    for key in all_mod_keys(content):
        for val in (1, -1):
            try:
                mod_phrase(key, val, strict=True)
            except KeyError:
                bad.append(key)
                break
    return bad
