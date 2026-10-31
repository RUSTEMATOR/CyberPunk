"""Text builders for screens that show game data: the character sheet, the profile, the menu header.

Pure functions of a `CharacterState` and the content. Telegram HTML; names typed by players are
escaped. Nothing hidden is shown: the cyber tolerance appears only when `tolerance_known` is set.
"""

from __future__ import annotations

import math

from app.bot import effects_text
from app.bot import texts_payoff as tp
from app.bot.texts import SKILL_LABELS, STAT_LABELS, esc
from app.bot.ui import style
from app.bot.ui.style import line as iline
from app.content.loader import Content
from app.content.schemas import SKILLS, STATS
from app.engine import derived
from app.engine.character_gen import FLAG_GENDER
from app.engine.checks import CheckResult
from app.engine.effects import AppliedEffects
from app.engine.state import FLAG_JAIL_WEEKS, FLAG_TOLERANCE_SHOWN, CharacterState, CyberItem
from app.engine.week import WeekReport


def money(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def years(n: int) -> str:
    """'21 год', '23 года', '30 лет'."""
    if 11 <= n % 100 <= 14:
        word = "лет"
    elif n % 10 == 1:
        word = "год"
    elif 2 <= n % 10 <= 4:
        word = "года"
    else:
        word = "лет"
    return f"{n} {word}"


def _name_of(catalog: dict, key: str) -> str:
    rec = catalog.get(key)
    return esc(rec.name) if rec is not None else esc(key)


def full_name(st: CharacterState) -> str:
    return esc(f"{st.name} {st.surname}".strip())


def district_name(content: Content, key: str) -> str:
    return _name_of(content.districts, key)


def _pct(rate: float) -> str:
    return f"{rate * 100:g}%"


def stats_line(st: CharacterState, content: Content, totals: bool) -> str:
    parts = []
    for stat in STATS:
        value = derived.stat_total(st, content, stat) if totals else st.stats.get(stat, 0)
        parts.append(f"{STAT_LABELS[stat]} {value}")
    return " · ".join(parts)


def skills_line(st: CharacterState, content: Content, totals: bool) -> str:
    parts = []
    for skill in SKILLS:
        value = derived.skill_total(st, content, skill) if totals else st.skill_level(skill)
        if value > 0:
            parts.append(f"{SKILL_LABELS[skill]} {value}")
    return ", ".join(parts) if parts else "нет"


SKILL_BAR_WIDTH = 6


def skill_progress(st: CharacterState, content: Content, skill: str, sessions: int | None = None,
                   sessions_word: tuple[str, str, str] = ("раз", "раза", "раз")) -> str:
    """'Стрельба 3 (+1) ▰▰▱▱▱▱ 7/16 · ещё 3 раза': the level, a bonus from chrome and perks, the xp
    to the next level as a bar, and how many more sessions it takes (when `sessions` is given)."""
    level = st.skill_level(skill)
    bonus = derived.skill_total(st, content, skill) - level
    head = f"{SKILL_LABELS[skill]} <b>{level}</b>" + (f" ({style.signed(bonus)})" if bonus else "")
    if level >= content.balance.character.skill_cap:
        return f"{head} · предел"
    sk = st.skills.get(skill)
    xp = sk.xp if sk else 0
    need = derived.skill_xp_needed(level, content)
    out = f"{head} {style.bar(xp, need, SKILL_BAR_WIDTH)} {xp}/{need}"
    if sessions is not None:
        out += f" · ещё {sessions} {effects_text.plural(sessions, sessions_word)}"
    return out


def skills_block(st: CharacterState, content: Content) -> list[str]:
    """Every skill on its own line with its xp bar (the profile)."""
    return [f"    {skill_progress(st, content, s)}" for s in SKILLS]


def debts_lines(st: CharacterState) -> list[str]:
    return [
        f"{esc(d.creditor)}: {money(d.principal)} эдди под {_pct(d.weekly_rate)} в неделю"
        + (f", просрочено недель: {d.missed_weeks}" if d.missed_weeks else "")
        for d in st.debts
    ]


def gear_names(st: CharacterState, content: Content, with_vehicle: bool = True) -> list[str]:
    names: list[str] = []
    for item in st.inventory:
        if item.kind == "weapon":
            names.append(_name_of(content.weapons, item.key))
        elif item.kind == "armor":
            names.append(_name_of(content.armor, item.key))
    ride = st.flags.get("vehicle")
    if with_vehicle and isinstance(ride, dict) and ride.get("key") in content.vehicles:
        names.append(content.vehicles[ride["key"]].name)
    return names


def gear_lines(st: CharacterState, content: Content) -> list[str]:
    """In the hand, on the body, in the bag (owner request: it was unclear what the character holds)."""
    a = content.armor.get(st.armor_key or "")
    held = [content.weapons[it.key].name for it in st.inventory
            if it.kind == "weapon" and it.equipped and it.key in content.weapons]
    out = [iline("weapon", f"Оружие: {esc(', '.join(held)) if held else 'ничего (кулаки)'}" + (f" · на тебе: {esc(a.name)}" if a else ""))]
    from app.engine import combat as cb  # owner request 24.10: two one-handed weapons strike together

    keys = [it.key for it in st.inventory if it.kind == "weapon" and it.equipped and it.key in content.weapons]
    guns = [k for k in keys if content.weapons[k].skill == "firearms" and cb._one_handed(content, k, cb.RANGED)]  # noqa: SLF001
    blades = [k for k in keys if content.weapons[k].skill == "melee" and cb._one_handed(content, k, cb.MELEE)]  # noqa: SLF001
    if len(guns) >= 2:
        out.append(iline("weapon", "🔫🔫 Два ствола в руках: второй стреляет следом за первым (без перка «Два ствола» — со штрафом к попаданию)."))
    if len(blades) >= 2:
        out.append(iline("weapon", "⚔️⚔️ Два клинка в руках: второй бьёт следом за первым (без перка «Два клинка» — со штрафом к попаданию)."))
    bag = [_name_of(content.weapons if i.kind == "weapon" else content.armor, i.key)
           for i in st.inventory if i.kind in ("weapon", "armor") and not i.equipped]
    if bag:
        out.append(iline("weapon", f"В сумке: {esc(', '.join(bag))}. Сменить: «Экипировка»"))
    return out


def implant_line(item: CyberItem, content: Content, effects: bool = False) -> str:
    """'Name (заряд 80%)'; with `effects`, the short form of what it gives after a colon."""
    rec = content.cyberware.get(item.key)
    name = esc(rec.name) if rec is not None else esc(item.key)
    if rec is not None and rec.active is not None:  # only active implants run on charge
        name = f"{name} (заряд {item.charge}%)"
    if effects and rec is not None:
        name = f"{name}: {esc(effects_text.cyberware_summary(rec, content, limit=2, capitalize=False))}"
    return name


def family_line(st: CharacterState) -> str:
    parents = [n for n in st.npcs if n.kind == "family" and n.role == "parent"]
    siblings = [n for n in st.npcs if n.kind == "family" and n.role == "sibling"]
    alive = sum(1 for p in parents if p.alive)
    return f"живых родителей {alive} из {len(parents)}, братьев и сестёр {len(siblings)}"


def picks_line(st: CharacterState, content: Content) -> str:
    bits = []
    if st.traits:
        bits.append(f"Черта: {_name_of(content.traits, st.traits[0])}")
    if st.marks:
        bits.append(f"Примета: {_name_of(content.marks, st.marks[0])}")
    if st.obsessions:
        bits.append(f"Одержимость: {_name_of(content.obsessions, st.obsessions[0])}")
    for k in (st.flags.get("habit") or {}):  # owner request: a vice from the start
        d = next((x for x in content.balance.economy.drugs.drugs if x.key == k), None)
        if d is not None:
            bits.append(f"Порок: зависимость от «{esc(d.name)}»")
    if int(st.flags.get("addiction", 0) or 0) >= content.balance.activities.braindance.addiction_event_threshold:
        bits.append("Порок: зависимость от брейнданса")
    return "\n".join(iline("traits", b) for b in bits)


# -- preset list ----------------------------------------------------------------------------------------


def preset_list_text(content: Content, keys: list[str], year: int, page: int, pages: int, header: str) -> str:
    lines = [header, f"<i>На дворе {year} год.</i>", ""]
    for key in keys:
        p = content.presets[key]
        lines.append(f"<b>{esc(p.name)}</b> — {esc(p.description)}")
    if pages > 1:
        lines.append("")
        lines.append(f"<i>Страница {page + 1} из {pages}</i>")
    return "\n".join(lines)


# -- character sheet at creation ---------------------------------------------------------------------------


def sheet_text(
    content: Content, st: CharacterState, preset_key: str, origin_key: str, year: int,
    rerolls_left: int | None = None, show_name: bool = False,
) -> str:
    preset = content.presets[preset_key]
    origin = content.origins[origin_key]
    lines = [style.header("profile", full_name(st) if show_name else "Лист персонажа")]
    lines.append(f"{esc(preset.name)} · {esc(origin.name)}, {years(st.age_years)}")
    lines.append(iline("district", district_name(content, st.district)))
    lines.append(style.quote(f"<i>{esc(origin.description)}</i>"))
    lines.append(iline("stats", stats_line(st, content, totals=False)))
    lines.append(iline("skills", f"Навыки: {skills_line(st, content, totals=False)}"))
    lines.append("")
    lines.append(iline("hp", f"Здоровье {st.hp}"))
    lines.append(iline("humanity", f"Человечность {st.humanity}"))
    lines.append(iline("luck", f"Удача {st.luck_pool}"))
    lines.append(iline("glitches", f"Глюки {st.glitches}"))
    lines.append(iline("eddies", f"{money(st.eddies)} эдди"))
    for d in debts_lines(st):
        lines.append(iline("debt", f"Долг: {d}"))
    lines.extend(gear_lines(st, content))
    if st.cyberware:
        lines.append(iline("chrome", f"Импланты: {', '.join(implant_line(c, content) for c in st.cyberware)}"))
    elif st.chrome_free:
        lines.append(iline("chrome", "Чистое тело: ни одного импланта."))
    lines.append("")
    picks = picks_line(st, content)
    if picks:
        lines.append(picks)
    lines.append(iline("family", f"Семья: {family_line(st)}."))
    if rerolls_left is not None:
        lines.append("")
        lines.append(f"<i>Перебросов осталось: {rerolls_left}.</i>" if rerolls_left > 0 else "<i>Перебросов нет.</i>")
    return "\n".join(lines)


# -- profile ---------------------------------------------------------------------------------------------------


def role_lines(st: CharacterState, content: Content) -> list[str]:
    rows = derived.ranked_roles(st, content)
    out = []
    for i, (key, aff, rank) in enumerate(rows):
        if i > 0 and rank < 1:
            continue
        label = "основная" if i == 0 else "вторая" if i == 1 else "побочная"
        role = content.roles[key]
        more = ""
        if rank < len(role.rank_thresholds):
            more = f", до ранга {rank + 1} ещё {max(1, math.ceil(role.rank_thresholds[rank] - aff))}"
        out.append(f"{_name_of(content.roles, key)}, ранг {rank} ({label}{more})")
    return out


def vow_lines(st: CharacterState, content: Content) -> list[str]:
    return [f"{_name_of(content.vows, key)}: серия {weeks} нед." for key, weeks in st.vows.items()]


def tolerance_name(st: CharacterState, content: Content) -> str | None:
    """The tolerance type, but only after a ripper scan revealed it."""
    if not st.tolerance_known:
        return None
    shown = str(st.flags.get(FLAG_TOLERANCE_SHOWN, st.cyber_tolerance))  # a lying scan's word until a true one
    return esc(derived.tolerance_entry(content, shown).name)


def life_lines(st: CharacterState, content: Content) -> list[str]:
    """Owner request: the ride, the gang, the business, the home, the weapon attachments in the profile."""
    out: list[str] = []
    up = content.balance.upgrades
    mods = (st.flags.get("weapon_mods") or {}).get(st.weapon_key or "") if st.weapon_key else None
    if mods:
        names = [m.name for m in up.weapon_mods if m.key in mods]
        out.append(iline("weapon", "Модули оружия: " + esc(", ".join(names))))
    ride = st.flags.get("vehicle")
    if isinstance(ride, dict) and ride.get("key") in content.vehicles:
        v = content.vehicles[ride["key"]]
        extra = [f"тюнинг {int(ride.get('tune', 0) or 0)}"]
        if int(ride.get("dmg", 0) or 0):
            extra.append(f"повреждения {int(ride['dmg'])}%")
        if ride.get("stolen"):
            extra.append("угнана")
        out.append(iline("move", f"Тачка: {esc(v.name)} ({', '.join(extra)})"))
    gang = (st.flags.get("gang") or {}).get("key")
    if gang in content.gangs:
        rep = int((st.faction_rep or {}).get(gang, 0))
        rank = content.balance.players.gangs.ranks[0].name
        for r in content.balance.players.gangs.ranks:
            if rep >= r.rep:
                rank = r.name
        out.append(iline("crime", f"Банда: {esc(content.gangs[gang].name)}, {esc(rank)}"))
    from app.engine import biz as biz_engine

    owned = []
    for b in biz_engine.list_of(st):
        kind = next((k for k in content.balance.players.business.kinds if k.key == b.get("kind")), None)
        if kind:
            nm = f"«{b['name']}» ({kind.name.lower()})" if b.get("name") else kind.name
            owned.append(f"{nm}, ур. {int(b.get('level', 1))}")
    if owned:
        out.append(iline("property", ("Свой бизнес: " if len(owned) == 1 else "Свои бизнесы: ") + esc("; ".join(owned))))
    home = [h.name for h in up.home if h.key in (st.flags.get("home") or [])]
    if home:
        out.append(iline("property", "Дома: " + esc(", ".join(home))))
    return out


def profile_text(content: Content, st: CharacterState, year: int) -> str:
    """The profile: who, the scales as bars, reputation, money, chrome, roles. One icon per line."""
    bal = content.balance
    hp_max = derived.max_hp(st, content)
    hum_max = derived.humanity_max(st, content)
    w = BAR_WIDTH
    lines = [style.header("profile", full_name(st))]
    preset = content.presets.get(st.preset)
    origin = content.origins.get(st.origin)
    who = [years(st.age_years)]
    if preset is not None:
        who.append(esc(preset.name))
    if origin is not None:
        who.append(esc(origin.name))
    lines.append(" · ".join(who))
    pr = st.flags.get("possessed")  # owner request 26.10: the AI in the head
    if isinstance(pr, dict) and pr.get("name"):
        lines.append(f"🧠 <b>В голове</b>: {esc(str(pr['name']))}, синхронизация {int(pr.get('sync', 0))}%")
    if derived.is_full_conversion(st, content):  # owner request 25.10: a borg says so in the profile
        lines.append("🤖 <b>Борг</b>: полная конверсия, киберпсихоз не грозит")
    from app.engine import creator

    looks = creator.profile_line(content, st)  # owner request 23.10: the roots, the homeland, the looks
    if looks:
        lines.append(f"<i>{esc(looks)}</i>")
    from app.engine import abroad

    away = abroad.country(st, content)
    if away is not None:
        lines.append(f"✈️ Живёт за границей: {esc(away.name)}, {esc(away.city)}")
    lines.append(iline("district", f"{district_name(content, st.district)}, "
                       f"{_name_of(content.housing, st.housing) if st.housing else 'без жилья'} · {year} год"))
    lines.append("")
    lines.append(iline("stats", stats_line(st, content, totals=True)))
    lines.append(iline("skills", "<b>Навыки</b> (уровень, опыт до следующего)"))
    lines.extend(skills_block(st, content))
    lines.append(style.SEP)
    wound = " · тяжёлое ранение" if derived.heavy_wound(st, content) else ""
    lines.append(style.meter("hp", st.hp, hp_max, w, "Здоровье", wound))
    lines.append(style.meter("humanity", st.humanity, hum_max, w, "Человечность"))
    lines.append(style.meter("mood", st.mood, bal.character.mood_max, w, "Настроение"))
    lines.append(iline("luck", f"Удача {st.luck_pool}/{derived.luck_max(st, content)}"))
    lines.append(iline("glitches", f"Глюки {st.glitches}"))
    lines.append(iline("heat", f"Розыск {st.heat}/{bal.character.heat_max}"))
    jail = st.flags.get(FLAG_JAIL_WEEKS)
    if jail:
        lines.append(iline("jail", f"За решёткой ещё {jail} нед."))
    lines.append(style.SEP)
    lines.append(cred_line(content, st))  # owner request 24.10: the bar and the title of the street cred
    lines.append(iline("status", f"Статус {st.status}/{bal.reputation.status_max}"))
    lines.append(iline("fame", f"Слава {st.fame}/{bal.reputation.fame_max}"))
    lines.append(style.SEP)
    lines.append(iline("eddies", f"<b>{money(st.eddies)}</b> эдди"))
    debts = debts_lines(st)
    if debts:
        lines.append(iline("debt", f"Долги: {money(st.debt_total)} эдди"))
        lines.extend(f"    {d}" for d in debts)
    else:
        lines.append(iline("debt", "Долгов нет."))
    lines.append("")
    if st.cyberware:
        lines.append(iline("chrome", "<b>Импланты</b>"))
        lines.extend(f"    • {implant_line(c, content, effects=True)}" for c in st.cyberware)
    elif st.chrome_free:
        lines.append(iline("chrome", "Имплантов нет. Чистое тело: киберпсихоз не грозит."))
    else:
        lines.append(iline("chrome", "Имплантов нет."))
    tol = tolerance_name(st, content)
    if tol:
        lines.append(iline("tolerance", f"Кибертолерантность: {tol}"))
    bf = derived.body_factor(st, content)  # owner request 23.10: the body holds chrome
    if abs(bf - 1) > 0.001:
        pct = round((1 - bf) * 100)
        lines.append(iline("tolerance", f"Тело и хром: потеря человечности и нагрузка хрома {'−' if pct > 0 else '+'}{abs(pct)}% "
                                        f"(от ТЕЛА и ТЕХ)"))
    fit = int(st.flags.get("fitness", 0) or 0)
    if fit:
        lines.append(iline("hp", f"Спортивная форма: {fit}/{content.balance.gym.cap}"))
    lines.extend(gear_lines(st, content))
    lines.extend(life_lines(st, content))
    roles = role_lines(st, content)
    lines.append(iline("roles", "Роли: " + ("; ".join(roles) if roles else "пока нет. Роль сложится из того, чем ты занимаешься.")))
    vows = vow_lines(st, content)
    if vows:
        lines.append(iline("vows", "Обеты: " + "; ".join(vows)))
    picks = picks_line(st, content)
    if picks:
        lines.append("")
        lines.append(picks)
    return "\n".join(lines)


# -- menu header ------------------------------------------------------------------------------------------------


BAR_WIDTH = 8  # cells of a scale bar in a window: a bar line stays under ~30 characters on a narrow phone


def status_line(content: Content, st: CharacterState) -> str:
    """The short status under the menu and the week report, three iconed lines:
    '❤️ ▰▰▰▰▰▰▱▱ 45/60', '💰 500 эдди · долг 3 800', '⚡ ОД: 3'."""
    money_line = f"<b>{money(st.eddies)}</b> эдди"
    if st.debt_total:
        money_line += f" · долг {money(st.debt_total)}"
    return "\n".join([
        style.meter("hp", st.hp, derived.max_hp(st, content), BAR_WIDTH),
        style.meter("humanity", st.humanity, derived.humanity_max(st, content), BAR_WIDTH),  # owner request 24.10
        iline("eddies", money_line),
        iline("ap", f"ОД: {st.ap}"),
        mood_line(content, st),
    ] + ([f"🏴 Банды {st.district_control[0]}% · 🏢 Корпорации {st.district_control[1]}%"] if st.district_control else [])
      + ([weather_line(content, st, detail=False)] if weather_line(content, st, detail=False) else []))


def weather_line(content: Content, st: CharacterState, detail: bool = True) -> str:
    """'🌧 Дождь · осень: тихие дела +2, побег −1' (owner request: weather and seasons)."""
    from app.engine import weather

    s = weather.season(st, content)
    k = weather.current(st, content)
    if s is None and k is None:
        return ""
    head = (f"{k.icon} {esc(k.name)}" if k else "🗓") + (f" · {esc(s.name.lower())}" if s else "")
    if not detail:
        return head
    mods: dict[str, int] = {}
    for src in ((s.mods if s else {}), (k.mods if k else {})):
        for key, v in src.items():
            mods[key] = mods.get(key, 0) + v
    phrases = effects_text.mods_phrases({key: v for key, v in mods.items() if v})
    if k is not None and k.weekly:
        phrases += [f"{t_label(key)} {signed(v)} в неделю" for key, v in k.weekly.items() if isinstance(v, int)]
    return head + (": " + ", ".join(phrases) if phrases else "")


def t_label(key: str) -> str:
    from app.bot import texts as t

    return t.EFFECT_LABELS.get(key, key)


def mood_line(content: Content, st: CharacterState) -> str:
    """'🎭 Настроение 72 · Подъём: проверки +1, опыт +10%' (owner request: the mood matters)."""
    tier = derived.mood_tier(st, content)
    if tier is None:
        return iline("mood", f"Настроение {st.mood}")
    bits = []
    if tier.checks:
        bits.append(f"проверки {style.signed(tier.checks)}")
    if tier.xp_pct:
        bits.append(f"опыт {style.signed(tier.xp_pct)}%")
    if tier.ap_week:
        bits.append(f"ОД {style.signed(tier.ap_week * content.balance.time.weeks_per_press)} за месяц")
    tail = f": {', '.join(bits)}" if bits else ""
    return iline("mood", f"Настроение {st.mood} · {tier.name}{tail}")


def cred_title(content: Content, n: int) -> str:
    """Owner request 24.10: the name of a street cred level."""
    titles = content.balance.reputation.street_cred_titles
    best = ""
    for at in sorted(titles):
        if n >= at:
            best = titles[at]
    return best


def cred_line(content: Content, st: CharacterState) -> str:
    mx = content.balance.reputation.street_cred_max
    title = cred_title(content, st.street_cred)
    return style.meter("street_cred", st.street_cred, mx, BAR_WIDTH, "Уличная репутация", f" · {title}" if title else "")


def menu_header(content: Content, st: CharacterState, year: int) -> str:
    lines = [
        iline("profile", f"<b>{full_name(st)}</b>, {years(st.age_years)}"),
        iline("district", f"{district_name(content, st.district)} · {year} год"),
        status_line(content, st),
        cred_line(content, st),  # owner request 24.10
    ]
    if st.heat:
        lines.append(iline("heat", f"Розыск {st.heat}/{content.balance.character.heat_max}"))
    return "\n".join(lines)


def gender_of(st: CharacterState) -> str:
    return str(st.flags.get(FLAG_GENDER, "m"))


# -- the week report ---------------------------------------------------------------------------------------


def week_report_text(
    content: Content, st: CharacterState, rep: WeekReport, *, notices: list[str] | None = None,
) -> str:
    """The closing report of a week: money, debts, body, time. Short lines, real numbers only."""
    from app.bot import texts as t  # local: texts imports nothing from views
    from app.bot import texts_world

    def change(n: int) -> str:
        return signed(n) if n else t.WEEK_NO_CHANGE

    from app.bot import texts_life

    if rep.span > 1:
        title = (texts_life.JAIL_WEEK_TITLE if rep.idle_week and not st.flags.get("fugitive_psycho") else t.WEEKS_TITLE).format(span=rep.span, n=rep.weeks_lived)
    else:
        title = t.WEEK_TITLE.format(n=rep.weeks_lived)
    lines = [f"{style.icon('week')} {title}", ""]
    wline = weather_line(content, st)
    if wline:
        lines.append(wline)
    if rep.idle_week:
        lines.append(iline("jail", t.WEEK_IDLE))
        lines.append(iline("eddies", f"Аренда −{money(rep.rent)}, еда −{money(rep.food)}."))
    else:
        lines.append(iline("eddies", t.WEEK_MONEY.format(salary=money(rep.salary), rent=money(rep.rent), food=money(rep.food))))
    if rep.trauma:
        lines.append(iline("trauma", t.WEEK_TRAUMA.format(trauma=money(rep.trauma))))
    lines.extend(_upkeep_lines(rep.upkeep))
    if rep.interest or rep.debt_paid:
        lines.append(iline("debt", t.WEEK_DEBT.format(interest=money(rep.interest), paid=money(rep.debt_paid))))
    if rep.missed:
        lines.append(iline("debt", t.WEEK_MISSED.format(creditors=", ".join(esc(c) for c in dict.fromkeys(rep.missed)))))
    for x in getattr(rep, "loan_lines", None) or []:
        lines.append(iline("debt", esc(x)))
    if rep.collector:
        lines.append(iline("danger", t.WEEK_COLLECTOR))
    dc = content.balance.death  # owner report 29.10: the collectors killed without a clear warning
    worst = max((int(getattr(d, "missed_weeks", 0) or 0) for d in getattr(st, "debts", []) or []), default=0)
    if worst >= dc.collector_kill_weeks:
        lines.append(iline("danger", f"Долг не платится {worst} нед. подряд: коллекторы могут прийти убивать — "
                                     f"{round(dc.collector_kill_chance * 100)}% каждую неделю, пока не заплатишь минимум."))
    elif worst >= dc.collector_kill_weeks - 2 and worst > 0:
        lines.append(iline("danger", f"Долг не платится {worst} нед. подряд. С {dc.collector_kill_weeks}-й недели "
                                     "коллекторы могут прийти убивать."))
    lines.extend(_week_money_lines(content, st, rep))
    lines.append(iline("hp", t.WEEK_HP.format(hp=change(rep.hp_delta))))
    lines.append(iline("mood", t.WEEK_MOOD.format(mood=change(rep.mood_delta))))
    if rep.infection_damage:
        lines.append(iline("infection", t.WEEK_INFECTION.format(dmg=rep.infection_damage)))
    if rep.heat_delta:
        lines.append(iline("heat", t.WEEK_HEAT.format(heat=signed(rep.heat_delta))))
    if rep.birthday:
        lines.append(iline("age", t.WEEK_BIRTHDAY.format(age=years(rep.age_years))))
    if rep.recharged:
        lines.append(iline("charge", t.WEEK_CHARGE))
    if rep.buffs_expired:
        lines.append(iline("time", t.WEEK_BUFFS.format(n=len(rep.buffs_expired))))
    if rep.vow_weeks:
        lines.append(iline("vows", t.WEEK_VOWS.format(vows=", ".join(
            f"{_name_of(content.vows, k)} ({st.vows.get(k, 0)} нед.)" for k in rep.vow_weeks))))
    lines.extend(notices or [])
    lines.append(style.SEP)
    lines.append(status_line(content, st))
    lines.append(iline("date", texts_world.WEEK_DATE.format(year=st.current_year, week=st.age_weeks + 1)))
    return "\n".join(lines)


def _upkeep_lines(up) -> list[str]:  # noqa: ANN001
    """Suppressants, staff, lifestyle and the drug habit of the press (owner request)."""
    if up is None:
        return []
    out: list[str] = []
    if up.immuno_paid:
        out.append(iline("ripper", f"Иммунодепрессанты ({esc(up.immuno_grade or '')}): −{money(up.immuno_paid)}."))
    if up.immuno_short:
        out.append(iline("danger", "Тело отторгает хром: подавителей не хватает. Здоровье, настроение и нервы "
                                   "уходят, пока не наладишь курс у риппера."))
    if up.immuno_rejected:
        out.append(iline("danger", f"Тело вытолкнуло имплант: {esc(up.immuno_rejected)}."))
    if up.empire_paid:
        net = up.empire_income - up.empire_paid
        out.append(iline("status", f"Империя: доход +{money(up.empire_income)}, содержание −{money(up.empire_paid)}, "
                                   f"итог {'+' if net >= 0 else '−'}{money(abs(net))}."))
    if up.empire_lost:
        out.append(iline("danger", f"Денег на содержание не хватило: «{esc(up.empire_lost)}» потеряно."))
    if up.staff_paid:
        out.append(iline("profile", f"Зарплата персоналу: −{money(up.staff_paid)}."))
    if up.staff_quit:
        out.append(iline("profile", "Ушли без зарплаты: " + ", ".join(esc(x) for x in up.staff_quit) + "."))
    if up.lifestyle_paid:
        out.append(iline("property", f"Уровень жизни: −{money(up.lifestyle_paid)}."))
    if up.lifestyle_dropped:
        out.append(iline("property", f"На уровень «{esc(up.lifestyle_dropped)}» денег не хватило: живёшь скромно."))
    if up.lifestyle_mood:
        out.append(iline("mood", f"Тоска по прежней жизни: настроение −{up.lifestyle_mood}."))
    if up.withdrawal:
        out.append(iline("danger", "Ломка: " + ", ".join(esc(x) for x in up.withdrawal) + ". Пока нет дозы, всё валится из рук."))
    return out


def _week_money_lines(content: Content, st: CharacterState, rep: WeekReport) -> list[str]:
    """Event money since the last report, the delayed outcomes that came due, and the total: no eddie moves silently."""
    out: list[str] = []
    if rep.events_eddies or rep.events_debt:
        tpl = tp.WEEK_EVENTS_MONEY_DEBT if rep.events_debt else tp.WEEK_EVENTS_MONEY
        out.append(iline("eddies", tpl.format(eddies=signed(rep.events_eddies), debt=signed(rep.events_debt))))
    if rep.later:
        out.append(tp.WEEK_LATER_TITLE)
        for r in rep.later:
            out.append(iline("ok" if r.ok else "fail", esc(r.text)))
            if r.applied is not None:
                out.extend(effects_lines(r.applied, content))
    out.append(iline("eddies", tp.WEEK_EDDIES_TOTAL.format(
        delta=signed(rep.eddies_delta + rep.events_eddies), eddies=money(st.eddies))))
    return out


# -- events -------------------------------------------------------------------------------------------------


def signed(n: int) -> str:
    return style.signed(n)


def effects_lines(applied: AppliedEffects, content: Content) -> list[str]:
    """The real changes as consequence lines, one icon each: '💰 эдди −200', '❤️ здоровье −6'."""
    from app.bot import texts as t

    out = [
        style.delta(style.EFFECT_ICONS.get(k, "note"), t.EFFECT_LABELS[k], v) for k, v in applied.deltas.items()
        if v and k in t.EFFECT_LABELS
    ]
    for skill, n in applied.skill_xp.items():
        if n:
            out.append(iline("xp", f"опыт ({SKILL_LABELS.get(skill, skill).lower()}) {signed(n)}"))
    if getattr(applied, "char_levels", 0):  # owner request 24.10
        out.append(iline("xp", f"⭐ <b>Новый уровень персонажа!</b> Очки атрибутов и перков: «Профиль» → «Прокачка»."))
    for skill, lvl in applied.level_ups:
        out.append(iline("xp", f"{SKILL_LABELS.get(skill, skill)}: уровень {lvl}"))
    for key in applied.cyberware_added:
        out.append(iline("chrome", tp.FX_IMPLANT.format(name=_name_of(content.cyberware, key))))
    for key in applied.cyberware_removed:
        out.append(iline("chrome", tp.FX_IMPLANT_LOST.format(name=_name_of(content.cyberware, key))))
    for npc in applied.npcs_added:
        out.append(iline("relations", f"новое знакомство: {esc(npc.name)}"))
    if applied.infection:
        out.append(iline("infection", "инфекция"))
    if applied.path_xp:
        out.append(iline("xp", f"опыт пути {signed(applied.path_xp)}"))
    for line in applied.biz_lines:  # owner request: what a story did to a business (and the Blackwall taint lines)
        out.append(esc(line) if line.startswith(("⚫", "🔥", "✝️", "💔", "🧠")) else iline("property", esc(line)))
    if applied.path_rank_up:
        out.append(iline("xp", f"новый ранг пути: {applied.path_rank_up}"))
    out.extend(payoff_lines(applied, content))
    return out


def payoff_lines(applied: AppliedEffects, content: Content) -> list[str]:
    """What an outcome gave besides the scales: items, delayed outcomes, a job, a scan, the overdraft into debt."""
    out: list[str] = []
    if applied.overflow_to_debt:
        out.append(iline("debt", tp.FX_OVERDRAFT.format(n=money(applied.overflow_to_debt))))
    for kind, key in applied.items_added:
        if kind == "weapon":
            out.append(iline("weapon", tp.FX_WEAPON.format(name=_name_of(content.weapons, key))))
        elif kind == "vehicle":
            out.append(iline("car", tp.FX_VEHICLE.format(name=_name_of(content.vehicles, key))))
            if applied.vehicle_sold:
                old, paid = applied.vehicle_sold
                out.append(iline("car", tp.FX_VEHICLE_SOLD.format(name=_name_of(content.vehicles, old), n=money(paid))))
        else:
            out.append(iline("armor", tp.FX_ARMOR.format(name=_name_of(content.armor, key))))
    for kind, key in applied.items_removed:  # owner request 26.10: the city director takes a thing away
        name = _name_of(content.weapons, key) if kind == "weapon" else _name_of(content.armor, key)
        out.append(iline("weapon" if kind == "weapon" else "armor", f"забрали: {name}"))
    if applied.career_change is not None:
        new = applied.career_change[1]
        out.append(iline("work", tp.FX_CAREER_NEW.format(name=_name_of(content.careers, new)) if new
                         else tp.FX_CAREER_GONE))
    if applied.employer:
        out.append(iline("work", tp.FX_EMPLOYER.format(name=_name_of(content.corps, applied.employer))))
    if applied.tolerance_revealed:
        out.append(iline("tolerance", tp.FX_TOLERANCE.format(
            name=esc(derived.tolerance_entry(content, applied.tolerance_revealed).name))))
    if applied.debt_grace:
        out.append(iline("debt", tp.FX_DEBT_GRACE))
    for rec in applied.later_added:
        n = int(rec["weeks"])
        weeks = f"{n} {effects_text.plural(n, tp.WEEKS)}" if n > 1 else tp.WEEKS[0]
        out.append(iline("time", tp.FX_LATER.format(weeks=weeks, hint=esc(rec.get("hint") or tp.FX_LATER_HINT))))
    return out


def effects_summary(applied: AppliedEffects, content: Content) -> str:
    """The consequences block: '<b>Итог</b>' and one iconed line per real change; '' when nothing changed."""
    from app.bot import texts as t

    lines = effects_lines(applied, content)
    return "\n".join([f"<b>{t.RESULT_LABEL}</b>", *lines]) if lines else ""


def check_line(check: CheckResult) -> str:
    """One line for a rolled check: 'Проверка ХЛАД + Убеждение: ХЛАД 5 + Убеждение 3 + кубик 4 = 12, нужно 13.'"""
    from app.bot import texts as t

    parts = [t.STAT_LABELS.get(check.stat or "", check.stat or "")]
    if check.skill:
        parts.append(t.SKILL_LABELS.get(check.skill, check.skill))
    crit = t.CRIT_WIN if check.crit == "success" else t.CRIT_FAIL if check.crit == "fail" else ""
    # owner report 27.10: a player saw «Скрытность 8» and did not get why the theft failed; the sum is spelled out:
    # stat + skill + die + modifiers = total against the DV
    terms: list[tuple[str, int]] = [(parts[0], check.stat_val)]
    if check.skill:
        terms.append((parts[1], check.skill_val))
    terms.append(("кубик", check.base_roll))
    if check.bonus:
        terms.append(("бонусы" if check.bonus > 0 else "штрафы", check.bonus))
    if check.luck:
        terms.append(("удача", check.luck))
    expr = f"{terms[0][0]} {terms[0][1]}"
    for name, val in terms[1:]:
        expr += f" {'−' if val < 0 else '+'} {name} {abs(val)}"
    crit = crit[2:].capitalize() + "." if crit else ""
    return iline("check", f"Проверка {' + '.join(parts)}: {expr} = {check.total}, нужно {check.dv}." + (f" {crit}" if crit else ""))


# -- modifiers and rewards ----------------------------------------------------------------------------------

def mod_line(key: str, val: int) -> str:
    """'Стрельба +2', 'зарплата корпората +15%' for one modifier key (see `effects_text`)."""
    return effects_text.mod_phrase(key, val)


def mods_text(mods: dict[str, int]) -> str:
    return effects_text.mods_line(mods)


def reward_parts(content: Content, period: str, rewards) -> list[str]:  # rewards: QuestRewards
    from app.bot import texts as t

    parts: list[str] = []
    if rewards.eddies:
        parts.append(t.REWARD_EDDIES.format(n=money(rewards.eddies)))
    if rewards.xp:
        parts.append(t.REWARD_XP.format(n=rewards.xp))
    if rewards.street_cred:
        parts.append(t.REWARD_STREET.format(n=rewards.street_cred))
    if rewards.status:
        parts.append(t.REWARD_STATUS.format(n=rewards.status))
    if rewards.fame:
        parts.append(t.REWARD_FAME.format(n=rewards.fame))
    tokens = content.balance.daily.quest_tokens[period] + rewards.tokens
    if tokens:
        parts.append(t.REWARD_TOKENS.format(n=tokens))
    return parts


def effects_cost(effects: dict) -> str:
    """'настроение −10, слава −1' for a dict of scale effects (the break price of a vow)."""
    from app.bot import texts as t

    bits = [f"{t.EFFECT_LABELS[k]} {signed(int(v))}" for k, v in effects.items() if k in t.EFFECT_LABELS and v]
    return ", ".join(bits) if bits else "без последствий"
