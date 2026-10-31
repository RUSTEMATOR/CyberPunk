"""The city (owner request: freedom of action, docs/PLAN_FREEDOM.md, stage 2).

menu -> city (places of the district) -> cplace (people) -> cnpc (one person: deeds) -> cnchat (a talk typed in the chat)
"""

from __future__ import annotations

import asyncio
import contextvars
import random

from app.bot import texts, views
from app.bot import texts_city as T
from app.bot.handlers.menu import menu_screen
from app.bot.ui import late, style
from app.bot.ui.callbacks import Ctx, goto, has_screen, on_screen, on_text
from app.bot.ui.screen import Button, Screen, pack
from app.content.loader import Content
from app.db import session as db
from app.db.models import CityNpc
from app.engine import city as ce
from app.engine import cyberpsycho as cp
from app.engine import maniac as maniac_engine
from app.services import city as svc
from app.services import game
from app.services.game import ActiveCharacter

_locks: dict[int, asyncio.Lock] = {}
_VIEW: contextvars.ContextVar[str] = contextvars.ContextVar("city_view_place", default="")  # a guest is met away from home


async def _load(ctx: Ctx) -> tuple[ActiveCharacter | None, int]:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        year = await game.current_year(s, ctx.content)
    return ch, year


def _local(dt, tz: str) -> str:  # noqa: ANN001
    from datetime import timezone
    from zoneinfo import ZoneInfo

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz)).strftime("%d.%m %H:%M")


def _status(ch: ActiveCharacter) -> str:
    st = ch.state
    return f"Эдди: {views.money(st.eddies)} · ОД: {st.ap} · розыск {st.heat}/5"


def _voice(content: Content, ch: ActiveCharacter) -> list[str]:
    """A fugitive psycho hears the voices: one line, glitched (owner request)."""
    if not cp.active(ch.state):
        return []
    rng = random.Random()
    lv = cp.level(ch.state, content)
    head = T.PSY_LEVEL.format(name=texts.esc(lv.name)) if lv is not None else ""
    return [head, style.quote(texts.esc(cp.glitch(cp.voice(content, rng), rng, cp.glitch_rate(ch.state, content) * 2))), ""]


def _glitched(content: Content, ch: ActiveCharacter, text: str) -> str:
    """Plain text of a psycho's window breaks a little."""
    return cp.glitch(text, random.Random(), cp.glitch_rate(ch.state, content)) if cp.active(ch.state) else text


def city_screen(content: Content, ch: ActiveCharacter, note: str | None = None) -> Screen:
    district = views.district_name(content, ch.state.district)
    lines = [style.header("district", T.TITLE_CITY.format(district=texts.esc(district))), _status(ch), "",
             *_voice(content, ch), texts.esc(_glitched(content, ch, T.CITY_TEXT)), ""]
    if note:
        lines += [note, ""]
    places = ce.cfg(content).places
    for i, p in enumerate(places, 1):
        lines.append(T.PLACE_LINE.format(n=i, venue=texts.esc(ce.venue_name(ch.state.district, p, content, ch.state.current_year)), kind=texts.esc(ce.kind_name(content, ch.state.district, p).lower())))
    rows = pack([Button(p.name, "p", p.key) for p in places], content.balance.ui.button_chars, max_per_row=2)
    from app.engine import abroad

    away = abroad.country(ch.state, content)
    if away is not None:  # owner request 25.10: the places of the city abroad work like those of Night City
        lines += ["", f"✈️ Ты в городе {texts.esc(away.city)} ({texts.esc(away.name)}). Местные люди, местные порядки."]
    if ch.state.district_control and away is None:  # owner request 25.10: gangs against NCPD
        from app.engine import control as ctl_eng

        g, o = ch.state.district_control
        lines += ["", f"🏴 Банды {g}% · 🏢 Корпорации {o}% — {ctl_eng.word(g, o)}"]
    mr = maniac_engine.rec(ch.state)
    if mr:
        lines += ["", T.MANIAC_SELF.format(nick=texts.esc(str(mr["nick"])))]
    from app.services import pboss  # owner request 27.10: a player as the raid boss

    boss = pboss.cached()
    if boss and away is None:
        lines += ["", f"🎯 Рейд-босс: <b>{texts.esc(boss['name'])}</b>, за голову {pboss.money(boss['bounty'])} эдди."]
        rows.append([Button(f"🎯 Рейд-босс: {str(boss['name']).split()[0]}"[:24], "pboss", style="danger")])
    if away is None:
        rows.append([Button("Улицы: банды и корпы", "ctrl", style="danger"), Button("🏁 Сезон", "season")])
    rows.append([Button("🗺 Карта города", "map"), Button("⚖️ Равновесие", "equil")])  # owner request 26.10
    rows.append([Button("🧩 Каприкорн", "capri")])  # owner request 26.10: project «Каприкорн»
    rows.append([Button("👑 Зал легенд", "lghall"), Button("🚀 Экспедиции", "exped")])
    rows.append([Button("💎 Реликвии", "relics"), Button("🐱 Харитон недели", "hariton")])
    rows.append([Button("🕹 Аркада", "arcade"), Button("🥊 Арена", "arena")])  # owner request 30.10: the fighting arena
    rows.append([Button("🏙 Район в 3D", "wplay", "city3d")])  # owner request 30.10: the district in 3D
    rows.append([Button("Судная ночь", "purge"), Button("Маньяки", "maniacs")])
    rows.append([Button("Эон: город времени", "aeon"), Button("Элизиум", "elysium")])
    rows.append([Button("Либрия", "libria"), Button("Мир", "world")])
    rows.append([Button("Банды и корпорации", "dyn"), Button("Сеть", "net")])
    rows.append([Button("Услуги игроков", "psvc"), Button("Вылазка", "sortie")])
    from app.engine import property as prop_engine  # owner request 25.10: a trip without moving

    home = prop_engine.visiting(ch.state)
    if home:
        lines += ["", f"🚕 Ты в гостях в районе «{texts.esc(views.district_name(content, ch.state.district))}». "
                      f"Дом — «{texts.esc(views.district_name(content, home))}». Вернёшься сам в начале месяца."]
        rows.append([Button("Поехать в район", "trip"), Button("Домой", "triphome", style="success")])
    else:
        rows.append([Button("Поехать в район", "trip")])
    from app.engine import blackwall as bw_engine  # owner request 24.10: the Blackwall path

    if bw_engine.touched(ch.state):
        rows.append([Button("⚫ Чёрный заслон", "bw", style="danger")])
    rows.append([Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="city")


async def place_screen(ctx: Ctx, ch: ActiveCharacter, place_key: str, note: str | None = None) -> Screen:
    c = ctx.content
    p = ce.place_type(c, place_key)
    if p is None:
        return city_screen(c, ch)
    from app.services import city_life

    async with db.session_scope() as s:
        closed = await city_life.closed_until(s, ch.state.district, place_key)
        crowd = await city_life.present(s, c, ch.state.district, place_key, random.Random())
        rows_data = [(n.id, n.name, n.archetype, n.alive, n.gender, svc.hurt(n), svc.att(n, ch.id)) for n, _ in crowd]
        guests = {n.id: home for n, home in crowd if home}
    if closed is not None:
        lines = [style.header("district", texts.esc(ce.venue_name(ch.state.district, p, c, ch.state.current_year))), "",
                 T.CLOSED.format(until=_local(closed, ctx.ui.settings.timezone))]
        if note:
            lines += ["", note]
        return Screen("\n".join(lines), [[Button("Другие места", "city"), Button(texts.MENU, "menu")]], name="cplace",
                      state={"place": place_key})
    lines = [style.header("district", f"{texts.esc(ce.venue_name(ch.state.district, p, c, ch.state.current_year))}"),
             f"<i>{texts.esc(_glitched(c, ch, p.text))}</i>", _status(ch), "", *_voice(c, ch)]
    if note:
        lines += [note, ""]
    buttons: list[Button] = []
    for i, (nid, name, arch_key, alive, gender, is_hurt, att) in enumerate(rows_data, 1):
        arch = ce.archetype(c, arch_key)
        state = (T.DEAD_F if gender == "f" else T.DEAD) if not alive else T.HURT if is_hurt else ""
        hp = ce.place_type(c, guests[nid]) if nid in guests else None
        state += T.GUEST.format(place=texts.esc(hp.name.lower())) if hp is not None else ""
        lines.append(T.PERSON_LINE.format(n=i, name=texts.esc(name), role=texts.esc(arch.name if arch else "?"),
                                          att=ce.attitude_word(att), state=state))
        if alive and not is_hurt:
            buttons.append(Button(str(i), "n", str(nid)))
    rows = pack(buttons, c.balance.ui.button_chars, max_per_row=5)
    if place_key == "bar":  # owner request 26.10: Raven Tearz and Life in Tears sit in the bar
        f = ch.state.flags  # owner report 26.10: strangers stay strangers until they introduce themselves
        rk = bool(f.get("raven_known") or f.get("raven_trust"))
        lk = bool(f.get("lit_known") or f.get("lit_trust"))
        raven = ("<b>Raven Tearz</b>: худи, кепка, нетронутый стакан" if rk else
                 "парень из того переулка: худи, кепка, нетронутый стакан" if f.get("raven_met") else
                 "худой парень в чёрном худи над нетронутым стаканом")
        lit = ("<b>Life in Tears</b>" if lk else "рокербой с подведёнными глазами")
        lines += ["", f"🎸 За угловым столиком — {raven}. На маленькой сцене настраивает бас {lit}."]
        rows.append([Button("Подсесть к Raven" if rk else "Угловой столик", "tz", "raven"),
                     Button("К сцене: Life in Tears" if lk else "К сцене", "tz", "lit")])
    if cp.active(ch.state) and any(alive and not is_hurt for _, _, _, alive, _, is_hurt, _ in rows_data):
        rows.append([Button(T.PSY_RAMPAGE_BTN.format(ap=cp.cfg(c).rampage_ap), "rampage", style="danger")])
    rows.append([Button("Другие места", "city"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="cplace", state={"place": place_key})


def npc_screen(content: Content, ch: ActiveCharacter, npc: CityNpc, note: str | None = None) -> Screen:
    arch = ce.archetype(content, npc.archetype)
    a = svc.att(npc, ch.id)
    rng = random.Random(npc.id * 7919 + ch.state.weeks_lived)
    lines = [style.header("relations", f"{texts.esc(npc.name)}, {texts.esc(arch.name if arch else '')}"),
             f"<i>{texts.esc(npc.name)} {texts.esc(rng.choice(arch.lines) if arch else '')}.</i>",
             f"Отношение к тебе: {ce.attitude_word(a)} ({a:+d}). {_status(ch)}"]
    prev = (npc.data or {}).get("prev")
    if prev and prev.get("name"):
        lines.append(T.PREV.format(name=texts.esc(prev["name"])))
    notes = svc.bond(npc, ch.id).get("notes") or []
    if notes:
        lines += ["", "<b>Что было</b>"] + [f"· {texts.esc(x)}" for x in notes[-3:]]
    paid = svc.tribute_of(ch.state, npc.id)  # owner request 27.10
    if paid:
        lines.append(f"💸 Платит тебе дань: {views.money(paid)} эдди в месяц.")
    if note:
        lines += ["", note]
    lines += ["", "<i>Насилие против вооружённых сразу начинает драку. Свидетели поднимают розыск.</i>"]
    buttons: list[Button] = []
    for v in ce.cfg(content).verbs:
        if arch is None or not ce.allowed(v, arch):
            continue
        if v.key == "service":
            sv = ce.cfg(content).services.get(arch.service or "")
            if arch.service == "dealer" and has_screen("dealer"):
                buttons.append(Button("Товар", "dealer"))
            elif sv is not None:
                buttons.append(Button(f"{sv.name} · {sv.price}"[:22], "service"))
            continue
        label = v.name + (f" · {v.price}" if v.price else "")
        buttons.append(Button(label[:22], "v", v.key))
    rows = pack(buttons, content.balance.ui.button_chars, max_per_row=2)
    from app.engine import companion as comp
    from app.services import city_life

    if comp.has(ch.state, npc.id):
        lines.append(T.HIRED_NOW)
    elif city_life.can_hire(content, ch, npc):
        price = comp.price(content, arch.danger if arch else 1)
        lines.append(T.HIRE_NOTE.format(n=len(comp.active_members(ch.state)), max=content.balance.freedom.life.squad_max))
        rows.append([Button(T.HIRE_BTN.format(price=views.money(price)), "hire")])
    if city_life.can_hire(content, ch, npc):  # owner request 25.10: take for good into the own fighters
        from app.engine import army as army_eng
        from app.services import army as army_svc

        if not any(int(x.get("npc", 0) or 0) == npc.id for x in army_eng.soldiers(ch.state)):
            rows.append([Button(f"Завербовать · {views.money(army_svc.npc_price(content, arch.danger if arch else 1))}"[:24],
                                "recruit")])
    rows.append([Button("Свой вариант (текстом)", "free")])
    rows.append([Button(texts.BACK, "place"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="cnpc", state={"npc": npc.id, "place": _VIEW.get() or npc.place})


def season_screen(content: Content, ch: ActiveCharacter, sv: dict) -> Screen:
    """Owner request 25.10: the season table."""
    from app.services import season

    c = content.balance.season
    pts = c.pts
    lines = [style.header("legends", f"Сезон {sv.get('n', 1)}"), f"До конца: {season.left_text(sv)}",
             f"Призы: " + ", ".join(f"{i + 1}-е — {views.money(p)}" for i, p in enumerate(c.prizes)) + " эдди и титул. "
             "В конце сезона карта районов сбрасывается.",
             "Очки: вызов " + str(pts.get("challenge", 0)) + "×ступень, амбиция " + str(pts.get("ambition", 0))
             + ", уйти легендой " + str(pts.get("legend", 0)) + ", война банд " + str(pts.get("war", 0))
             + ", налёт на корпорацию " + f"{pts.get('craid_depot', 0)}/{pts.get('craid_lab', 0)}/{pts.get('craid_tower', 0)}"
             + ", налёт на банду " + str(pts.get("graid", 0)) + ", зачистка или удар " + str(pts.get("street", 0))
             + ", логово " + str(pts.get("lair", 0)) + ".", ""]
    top = season.table(sv)
    if not top:
        lines.append("Пока ни одного очка.")
    for i, (tg, rec) in enumerate(top[:10]):
        mark = ("🥇", "🥈", "🥉")[i] if i < 3 else f"{i + 1}."
        parts = ", ".join(f"{season.KIND_RU.get(k, k)} {v}" for k, v in (rec.get("parts") or {}).items())
        me = " ← ты" if str(tg) == str(ch.player_tg_id) else ""
        lines.append(f"{mark} {texts.esc(str(rec.get('name')))} — <b>{rec.get('pts')}</b>{me}\n   <i>{texts.esc(parts)}</i>")
    hist = sv.get("history") or []
    if hist:
        last = hist[-1]
        lines += ["", f"Прошлый сезон {last.get('n')}: " + ", ".join(f"{n} ({p})" for n, p in last.get("top") or [])]
    return Screen("\n".join(lines), [[Button(texts.BACK, "city"), Button(texts.MENU, "menu")]], name="city")


def prop_engine_home(ch: ActiveCharacter) -> str | None:
    from app.engine import property as prop_engine

    return prop_engine.visiting(ch.state)


TRIP_WHY = {"gone": "Туда отсюда не доехать.", "jail": "Из камеры не уехать.", "no_money": "Не хватает на такси.",
            "busy": "Сначала разберись с тем, что уже ждёт.",
            "no_ap": "ОД кончились."}


async def trip_screen(ctx: Ctx, ch: ActiveCharacter, go: str = "", note: str | None = None) -> Screen:
    """Owner request 25.10: go to another district for a while: its fixer, ripper, places and sortie, the home stays."""
    from app.engine import property as prop_engine
    from app.services import game as game_service
    from app.services import property as pr_service

    c = ctx.content
    went = False
    async with db.session_scope() as s:
        ch2 = await game.get_active_character(s, ctx.user_id)
        if ch2 is None:
            return await menu_screen(ctx)
        ch = ch2
        year = await game.current_year(s, c)
        if go:
            r = await pr_service.trip(s, c, ch, year, ctx.ui.settings.timezone, go)
            if r.ok and not prop_engine.visiting(ch.state):
                return city_screen(c, ch, f"🚕 Ты дома: «{texts.esc(views.district_name(c, ch.state.district))}».")
            if not r.ok:
                note = TRIP_WHY.get(r.reason or "", r.reason)
            went = r.ok
        from app.services import worldfx

        keys = prop_engine.districts_to_move(ch.state, c, year, await worldfx.load_fx(s, c, ch.player_tg_id))
    if go and went:  # the road event plays first (after the commit)
        from app.bot.ui.callbacks import goto as _goto

        return await _goto(ctx, "evchoice", "resume")
    home = prop_engine.visiting(ch.state)
    lines = ["🚕 <b>Поехать в район</b>",
             f"Такси {pr_service.TRIP_FARE} эдди и {pr_service.TRIP_AP} ОД. Жильё остаётся за тобой, переезжать не нужно. "
             "На месте работают фиксер, риппер, места и вылазка того района. В начале месяца ты сам вернёшься домой.", ""]
    rows: list[list[Button]] = []
    for k in keys:
        d = c.districts[k]
        fixer = game_service.district_fixer(c, k, year)
        rip = [r.name for r in c.ripperdocs.values() if r.district == k]
        lines.append(f"• <b>{texts.esc(d.name)}</b>{' · дом' if k == home else ''} (опасность {d.danger}): фиксер "
                     f"{texts.esc(fixer.name) if fixer else '—'}" + (f", риппер {texts.esc(', '.join(rip))}" if rip else ""))
        rows.append([Button(("🏠 " if k == home else "") + d.name[:22], "go", k)])
    if note:
        lines += ["", note]
    rows.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), rows, name="trip")


async def map_screen(ctx: Ctx, ch: ActiveCharacter, note: str | None = None) -> Screen:
    """Owner request 26.10: the live map of Night City. A district button is a trip there (the same as «Поехать в
    район»); the state carries the whole map for the Mini App (services/citymap.city, docs/PLAN_LIVING.md stage 6)."""
    from app.services import citymap

    c = ctx.content
    async with db.session_scope() as s:
        m = await citymap.city(s, c)
    here = ch.state.district
    lines = ["🗺 <b>Карта Найт-Сити</b>", "Кто держит районы: банды (Б) и корпорации (К), от 0 до 100. "
             "Нажми на район, чтобы поехать туда.", ""]
    btns: list[Button] = []
    for d in m["districts"]:
        bits = [f"Б {d['gang']} · К {d['corp']}", d["word"]]
        if d["holder"]:
            bits.append(f"держит {texts.esc(str(d['holder'].get('name') or ''))}")
        if d["crews"]:
            bits.append("новые: " + ", ".join(f"«{texts.esc(x['name'])}»" for x in d["crews"]))
        mark = "📍 " if d["key"] == here else ""
        lines.append(f"{mark}<b>{texts.esc(d['name'])}</b>: " + " · ".join(bits))
        btns.append(Button((mark + d["name"])[:24], "go", d["key"]))
    down = [x["name"] for x in m["corps"] if x["down"]]
    weak = sorted((x for x in m["corps"] if not x["down"]), key=lambda x: x["hp"])[:2]
    lines += ["", "<b>Корпорации</b>: " + ", ".join(f"{texts.esc(x['name'])} {x['hp']}/100" for x in weak)
              + (f" · лежат: {texts.esc(', '.join(down))}" if down else "")]
    if m["crisis"]:
        lines.append("<b>Кризис</b>: идёт, окно в меню.")
    if note:
        lines += ["", note]
    packed = pack(btns, c.balance.ui.button_chars, max_per_row=2)
    packed.append([Button(texts.BACK, "back")])
    return Screen("\n".join(lines), packed, name="citymap", state={"map": m})


async def tearz_screen(ctx: Ctx, ch: ActiveCharacter, who: str = "raven", note: str | None = None) -> Screen:
    """Owner request 26.10: Raven Tearz and Life in Tears sit in the bar (services/tearz.py)."""
    from app.services import tearz

    st = ch.state
    tz = ctx.ui.settings.timezone
    met = bool(st.flags.get(tearz.MET[who]))
    nxt = tearz.next_event(ctx.content, st, who)
    today = tearz.done_today(st, who, tz)
    known = bool(st.flags.get(f"{who}_known") or st.flags.get(f"{who}_trust"))
    if who == "raven":
        lines = ["🎸 <b>Raven Tearz</b>" if known else "🎸 <b>Парень в чёрном худи</b>",
                 "<i>Угловой столик, сигарета, наушники на шее. Kiroshi иногда вспыхивают красным, будто он слушает "
                 "кого-то ещё.</i>" if met else
                 "<i>Худой парень в чёрном худи сидит один над нетронутым стаканом. Говорят, лучше его не трогать.</i>"]
    else:
        lines = ["🎸 <b>Life in Tears</b>" if known else "🎸 <b>Рокербой на сцене</b>",
                 "<i>Бас, подведённые глаза, колонка на плече. Между песнями он машет тебе со сцены.</i>" if met else
                 "<i>Рокербой на маленькой сцене настраивает бас. Сорок человек ждут, когда начнётся.</i>"]
    lines.append(f"История: {tearz.status(st, who)}.")
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    if not met:
        rows.append([Button("Подойти · 1 ОД" if who == "raven" else "Послушать · 1 ОД", "next", who)])
    else:
        rows.append([Button("Поговорить", "talk", who)])
        if not known and nxt and not today:
            lines.append("Он, кажется, не против познакомиться ближе.")
        if nxt and not today:
            rows.append([Button("Что нового? · 1 ОД", "next", who)])
        elif today:
            lines.append("Сегодня вы уже виделись: новое будет завтра.")
    if who == "raven" and (st.flags.get("raven_known") or st.flags.get("raven_trust")):  # owner request 26.10
        rows.append([Button("🖤 Чёрные слёзы", "btears")])
    rows.append([Button("Назад в бар", "bar")])
    return Screen("\n".join(lines), rows, name="tearz", state={"who": who})


@on_screen("tearz")
async def tearz_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    from app.services import ap as ap_services
    from app.services import tearz
    from app.services.week import pending_events, set_pending

    tz = ctx.ui.settings.timezone
    who = arg if arg in tearz.NAMES else str(ctx.state.get("who") or "raven")
    play = False
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        tearz.ensure_contacts(ch.state)
        await game.save_active(s, ch, tz)
        if action == "talk":
            n = next((x for x in ch.state.npcs if x.data.get("story") == who and x.alive), None)
            if n is not None:
                from app.bot.handlers.npcchat import open_chat

                return await open_chat(ctx, ch.state.npcs.index(n))
        if action == "next":
            if int(ch.state.flags.get("jail_weeks", 0) or 0) > 0:
                return await tearz_screen(ctx, ch, who, T.BLOCKED["jail"])
            ev = tearz.next_event(ctx.content, ch.state, who)
            if ev is None or tearz.done_today(ch.state, who, tz):
                return await tearz_screen(ctx, ch, who, "Сегодня ничего нового.")
            if not ap_services.can_spend(ch, 1):
                return await tearz_screen(ctx, ch, who, "Не хватает ОД.")
            ap_services.spend(ch, ctx.content, 1)
            tearz.mark(ch, who, tz)
            set_pending(ch, [ev] + [e for e in pending_events(ch) if e != ev])
            await game.save_active(s, ch, tz)
            play = True
    if play:  # after the commit: the scene plays at once
        from app.bot.ui.callbacks import goto as _goto

        return await _goto(ctx, "evchoice", "resume")
    if action == "bar":
        return await place_screen(ctx, ch, "bar")
    if action == "btears":  # owner request 26.10
        from app.bot.ui.callbacks import goto as _goto2

        return await _goto2(ctx, "btears", "open")
    return await tearz_screen(ctx, ch, who)


@on_screen("citymap")
async def citymap_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action == "go" and arg:
        if arg == ch.state.district:
            return await map_screen(ctx, ch, "📍 Ты уже здесь.")
        return await trip_screen(ctx, ch, arg)
    return city_screen(ctx.content, ch)


@on_screen("trip")
async def trip_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action == "go" and arg:
        return await trip_screen(ctx, ch, arg)
    return city_screen(ctx.content, ch)


@on_screen("city")
async def city_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    scr = await _city_handler(ctx, action, arg)
    if scr is not None and ctx.ui.is_admin(ctx.user_id):  # owner request 31.10: the Unity client, admins first
        for row in scr.keyboard:
            if any(isinstance(b, Button) and b.action == "wplay" and b.arg == "city3d" for b in row):
                row.append(Button("🧪 Unity-клиент", "wplay", "unity"))
                break
    return scr


async def _city_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if int(ch.state.flags.get("jail_weeks", 0) or 0) > 0:
        return city_screen(ctx.content, ch, T.BLOCKED["jail"])
    from app.engine import abroad as abroad_engine

    if action == "p" and arg and ch.state.flags.get("abroad") and not abroad_engine.in_city(ch.state, ctx.content):
        return city_screen(ctx.content, ch, "✈️ Ты за границей. Места Найт-Сити откроются, когда вернёшься: «Мир» → «Домой».")
    if action == "p" and arg:
        return await place_screen(ctx, ch, arg)
    if action in ("trip", "triphome"):
        return await trip_screen(ctx, ch, "" if action == "trip" else (prop_engine_home(ch) or ""))
    if action == "map":
        return await map_screen(ctx, ch)
    if action == "tearz":  # the old button: they sit in the bar now
        return await place_screen(ctx, ch, "bar")
    if action == "purge":
        from app.bot.handlers.purge import purge_screen

        return await purge_screen(ctx)
    if action == "maniacs":
        return await maniacs_screen(ctx, ch)
    if action == "ctrl":
        return await goto(ctx, "ctrl", "open")
    if action == "pboss":  # owner request 27.10: a player as the raid boss
        return await goto(ctx, "pboss", "open")
    if action == "capri":  # owner request 26.10: project «Каприкорн»
        return await goto(ctx, "capri", "open")
    if action == "equil":  # owner request 26.10: the balance of the three forces
        return await goto(ctx, "equil", "open")
    if action == "lghall":  # owner request 25.10: legends in the city
        return await goto(ctx, "lghall", "open")
    if action == "exped":  # owner request 25.10: expeditions to other worlds
        return await goto(ctx, "exped", "open")
    if action == "relics":  # owner request 25.10: relics, one of each in the city
        return await goto(ctx, "relics", "open")
    if action == "hariton":  # owner request 25.10: the event of the week with the cat Hariton
        return await goto(ctx, "hariton", "open")
    if action == "arcade":  # owner request 29.10: the popular HTML5 games
        return await goto(ctx, "arcade", "open")
    if action == "arena":  # owner request 30.10: the fighting arena
        return await goto(ctx, "arena", "open")
    if action == "wplay" and arg == "city3d":  # owner request 30.10: the district in 3D (docs/PLAN_CITY3D.md)
        from app.services import city3d

        link = city3d.url(ctx.content, ctx.ui.settings.bot_token, ctx.user_id)
        return city_screen(ctx.content, ch, f'🏙 <a href="{link}">Открыть район в 3D</a>. Двери и люди там ведут сюда же.')
    if action == "wplay" and arg == "unity":  # owner request 31.10: the Unity client (docs/PLAN_UNITY.md), admins only
        from app.services import unity3d

        if not ctx.ui.is_admin(ctx.user_id):
            return city_screen(ctx.content, ch)
        link = unity3d.url(ctx.content, ctx.ui.settings.bot_token, ctx.user_id)
        if link is None:
            return city_screen(ctx.content, ch, "🧪 Unity-клиент ещё не собран. Сборка идёт на маке, загляни позже.")
        return city_screen(ctx.content, ch, f'🧪 <a href="{link}">Открыть Unity-клиент</a>. Перед загрузкой покажет размер.')
    if action == "d3" and arg:  # a door or a person pressed in the 3D district
        return await _door3d(ctx, ch, arg)
    if action == "season":  # owner request 25.10: the season of the city
        from app.services import season

        async with db.session_scope() as s:
            sv = await season.state(s, ctx.content)
        return season_screen(ctx.content, ch, sv)
    if action == "dyn":
        return await dyn_screen(ctx, ch)
    if action == "bw":
        return await goto(ctx, "bw", "open")
    if action == "net":
        from app.bot.handlers.net import net_screen, run_screen

        year = await _load(ctx)
        return run_screen(ctx.content, ch) or net_screen(ctx.content, ch, year[1])
    if action in ("elysium", "libria", "aeon"):  # owner request 30.10: real cities, their windows for residents
        from app.bot.handlers.world import gate_screen, resident

        if not resident(ctx.content, ch, action):
            return gate_screen(ctx.content, ch, action)
    if action == "elysium":
        from app.bot.handlers.elysium import elysium_screen

        return elysium_screen(ctx.content, ch)
    if action == "sortie":  # owner request 24.10
        from app.bot.handlers.sortie import sortie_screen

        return sortie_screen(ctx.content, ch)
    if action == "psvc":
        from app.bot.handlers.pservices import psvc_screen

        return await psvc_screen(ctx, ch)
    if action == "world":
        from app.bot.handlers.world import world_screen

        return world_screen(ctx.content, ch, (await _load(ctx))[1])
    if action == "libria":
        from app.bot.handlers.libria import libria_screen

        return libria_screen(ctx.content, ch)
    if action == "aeon":
        from app.bot.handlers.aeon import aeon_screen

        return aeon_screen(ctx.content, ch)
    return city_screen(ctx.content, ch)


# -- the maniacs (owner request) -------------------------------------------------------------------------------------


async def maniacs_screen(ctx: Ctx, ch: ActiveCharacter, note: str | None = None) -> Screen:
    from app.services import maniac as msvc

    c = ctx.content
    mc = maniac_engine.cfg(c)
    async with db.session_scope() as s:
        reg = await msvc.registry(s)
    lines = [style.header("crime", T.MANIAC_TITLE), _status(ch), "", T.MANIAC_TEXT.format(
        ap=mc.investigate_ap, dv=mc.investigate_dv, reward=views.money(mc.investigate_reward), n=mc.reveal_clues), ""]
    if note:
        lines += [note, ""]
    rows: list[list[Button]] = []
    live = [(k, e) for k, e in reg.items() if not e.get("revealed")]
    shown = [(k, e) for k, e in reg.items() if e.get("revealed")]
    if not live and not shown:
        lines.append(T.MANIAC_NONE)
    for i, (k, e) in enumerate(live, 1):
        lines.append(T.MANIAC_LINE.format(i=i, nick=texts.esc(str(e.get("nick", "?"))), clues=int(e.get("clues", 0)),
                                          n=mc.reveal_clues, kills=int(e.get("kills", 0))))
        rows.append([Button(f"Расследовать {i}", "inv", str(k))])
    for k, e in shown:
        lines.append(T.MANIAC_SHOWN.format(nick=texts.esc(str(e.get("nick", "?"))), name=texts.esc(str(e.get("name", "?")))))
    mr = maniac_engine.rec(ch.state)
    if mr and not mr.get("quit"):
        rows.append([Button("Завязать", "quit", style="danger")])
    elif mr:
        lines += ["", T.MANIAC_QUITTING.format(n=mc.quit_weeks)]
    rows.append([Button(texts.BACK, "city"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="cmaniac")


@on_screen("cmaniac")
async def cmaniac_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if action == "city":
        return city_screen(c, ch)
    from app.services import maniac as msvc

    if action == "inv" and arg and arg.isdigit():
        if int(ch.state.flags.get("jail_weeks", 0) or 0) > 0:
            return await maniacs_screen(ctx, ch, T.BLOCKED["jail"])
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            r = await msvc.investigate(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, int(arg))
        if r.blocked == msvc.R_AP:
            return await maniacs_screen(ctx, ch, T.BLOCKED["ap"])
        if r.blocked is not None:
            return await maniacs_screen(ctx, ch, T.MANIAC_GONE)
        parts = [views.check_line(r.check)] if r.check is not None else []  # type: ignore[arg-type]
        parts += [texts.esc(x) for x in r.lines]
        if r.applied is not None:
            sm = views.effects_summary(r.applied, c)
            if sm:
                parts.append(sm)
        if r.revealed:
            ctx.ui.post(f"🔪 {texts.esc(ch.state.name)} раскрывает маньяка. Подробности в ленте города.", 6 * 3600)
        return await maniacs_screen(ctx, ch, "\n".join(parts))
    if action == "quit":
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            await msvc.quit_mode(s, ch, ctx.ui.settings.timezone)
        return await maniacs_screen(ctx, ch, T.MANIAC_QUIT.format(n=maniac_engine.cfg(c).quit_weeks))
    return await maniacs_screen(ctx, ch)


_D3_SECTIONS = ("gigs", "ripper", "work", "property")


async def _door3d(ctx: Ctx, ch: ActiveCharacter, door: str) -> Screen | None:
    """The doors of the 3D district (app/services/city3d.py): a place, a person at a place, or a section of the game."""
    if door.startswith("p.") and ce.place_type(ctx.content, door[2:]) is not None:
        from app.engine import abroad as abroad_engine

        if ch.state.flags.get("abroad") and not abroad_engine.in_city(ch.state, ctx.content):
            return city_screen(ctx.content, ch, "✈️ Ты за границей. Места Найт-Сити откроются, когда вернёшься: «Мир» → «Домой».")
        return await place_screen(ctx, ch, door[2:])
    if door.startswith("n.") and door[2:].isdigit():
        async with db.session_scope() as s:
            npc = await s.get(CityNpc, int(door[2:]))
        if npc is not None and npc.alive and npc.district == ch.state.district and not svc.hurt(npc):
            _VIEW.set(npc.place)
            return npc_screen(ctx.content, ch, npc)
        return city_screen(ctx.content, ch, "Человек уже ушёл.")
    if door.startswith("pl.") and door[3:].isdigit():  # another player on the street (docs/PLAN_CITY3D_2.md, phase 1)
        from app.bot.handlers.players import card_screen

        return await card_screen(ctx, int(door[3:]))
    if door == "cops":  # a patrol caught the player (phase 2): the police wave comes now
        from app.services import wanted as wanted_svc

        async with db.session_scope() as s:
            ch2 = await game.get_active_character(s, ctx.user_id)
            line = wanted_svc.tick(ctx.content, ch2, force=True) if ch2 is not None else None
            if line:
                await game.save_active(s, ch2, ctx.ui.settings.timezone)
        if line:
            return await goto(ctx, "evchoice", "resume")
        return city_screen(ctx.content, ch, "🚨 Патруль проехал мимо: тебя уже не ищут.")
    if door == "gang":  # a gang on its turf (phase 3): the cleanse of «Улицы: банды и корпы»
        return await goto(ctx, "ctrl", "cleanse")
    if door.startswith("gig.") and door[4:].isdigit():  # a gig marker on the street (phase 4)
        return await goto(ctx, "gigs", "pick", door[4:])
    if door.startswith("trip.") and door[5:] in ctx.content.districts:  # the way out of the district (phase 11)
        return await trip_screen(ctx, ch, door[5:])
    if door in ("arena", "arcade"):
        return await goto(ctx, door, "open")
    if door in _D3_SECTIONS:
        return await goto(ctx, "menu", "go", door)
    return city_screen(ctx.content, ch)


@on_screen("cplace")
async def cplace_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    if action == "city":
        return city_screen(ctx.content, ch)
    place = str(ctx.state.get("place", ""))
    if action == "n" and arg and arg.isdigit():
        async with db.session_scope() as s:
            npc = await s.get(CityNpc, int(arg))
        if npc is not None and npc.alive and npc.district == ch.state.district:
            _VIEW.set(place)
            return npc_screen(ctx.content, ch, npc)
    if action == "rampage":
        return await _rampage(ctx, ch, place)
    if action == "tz" and arg in ("raven", "lit"):
        return await tearz_screen(ctx, ch, arg)
    return await place_screen(ctx, ch, place)


async def _rampage(ctx: Ctx, ch: ActiveCharacter, place: str) -> Screen:
    c = ctx.content
    rng = random.Random()
    async with db.session_scope() as s:
        ch1 = await game.get_active_character(s, ctx.user_id)
        if ch1 is None:
            return await menu_screen(ctx)
        ch = ch1
        year = await game.current_year(s, c)
        r = await svc.rampage(s, c, ch, rng, year, ctx.ui.settings.timezone, place)
    if r.blocked is not None:
        return await place_screen(ctx, ch, place, T.BLOCKED.get(r.blocked, r.blocked))
    parts = [style.quote(texts.esc(cp.glitch(rng.choice(T.PSY_RAMPAGE), rng, cp.glitch_rate(ch.state, c))))]
    if r.killed:
        parts.append(T.PSY_RAMPAGE_DEAD.format(names=texts.esc(", ".join(r.killed))))
    if r.applied is not None:
        sm = views.effects_summary(r.applied, c)
        if sm:
            parts.append(sm)
    if r.level_up:
        parts.append(T.PSY_LEVEL_UP.format(name=texts.esc(r.level_up)))
    ctx.ui.post(T.PSY_RAMPAGE_POST.format(name=texts.esc(ch.state.name), n=len(r.killed)), 6 * 3600)
    if r.fight is not None:
        from app.bot.handlers.combat import step_screen

        return step_screen(c, ch, r.fight)  # type: ignore[arg-type]
    return await place_screen(ctx, ch, place, "\n".join(parts))


@on_screen("cnpc")
async def cnpc_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    c = ctx.content
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    npc_id = int(ctx.state.get("npc", 0) or 0)
    place = str(ctx.state.get("place", ""))
    _VIEW.set(place)
    if action == "place":
        return await place_screen(ctx, ch, place)
    if action == "dealer":
        return await goto(ctx, "dealer")
    if action == "v" and arg == "talk":
        return await chat_open(ctx, ch, npc_id)
    if action == "free":
        from app.narrator import narrator

        async with db.session_scope() as s:
            npc1 = await s.get(CityNpc, npc_id)
        if npc1 is None:
            return city_screen(c, ch)
        if not narrator.is_live():
            return npc_screen(c, ch, npc1, T.CHAT_OFF)
        scr = npc_screen(c, ch, npc1, FREE_PROMPT)
        scr.awaiting_text = "city_free"
        return scr
    rng = random.Random()
    if action == "recruit":  # owner request 25.10: into the own fighters for good
        from app.bot.handlers.army import BLOCK as ARMY_BLOCK
        from app.services import army as army_svc

        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            why, line = await army_svc.recruit_npc(s, c, ch, rng, year, ctx.ui.settings.timezone, npc_id)
            npc2 = await s.get(CityNpc, npc_id)
        if npc2 is None:
            return city_screen(c, ch)
        return npc_screen(c, ch, npc2, ARMY_BLOCK.get(why, why) if why else texts.esc(line))
    if action == "hire":
        from app.services import city_life

        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            hr = await city_life.hire(s, c, ch, rng, year, ctx.ui.settings.timezone, npc_id)
            npc2 = await s.get(CityNpc, npc_id)
        if npc2 is None:
            return city_screen(c, ch)
        if hr.blocked is not None:
            return npc_screen(c, ch, npc2, T.HIRE_BLOCKED.get(hr.blocked, hr.blocked))
        return npc_screen(c, ch, npc2, T.HIRE_DONE.format(name=texts.esc(npc2.name), price=views.money(hr.price), weeks=hr.weeks))
    if action == "service":
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            sr = await svc.service(s, c, ch, rng, year, ctx.ui.settings.timezone, npc_id)
        if sr.blocked is not None or sr.npc is None:
            return await _back(ctx, ch, sr.npc, T.BLOCKED.get(sr.blocked or "gone", sr.blocked))
        note = texts.esc(sr.service.text)  # type: ignore[union-attr]
        if sr.applied is not None:
            summary = views.effects_summary(sr.applied, c)
            if summary:
                note += "\n" + summary
        return npc_screen(c, ch, sr.npc, note)
    if action == "v" and arg:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            took = _voices_pick(c, ch, await s.get(CityNpc, npc_id), arg, rng)
            r = await svc.do(s, c, ch, rng, year, ctx.ui.settings.timezone, npc_id, took or arg)
        if took:
            r.lines.insert(0, T.PSY_VOICES_TOOK.format(verb=(ce.verb(c, took).name.lower() if ce.verb(c, took) else took)))
        return await _render_deed(ctx, ch, r, took or arg, rng, place)
    async with db.session_scope() as s:
        npc0 = await s.get(CityNpc, npc_id)
    return await _back(ctx, ch, npc0, None)


def _voices_pick(content: Content, ch: ActiveCharacter, npc: CityNpc | None, verb_key: str, rng: random.Random) -> str | None:
    """A psycho's deed may be chosen by the voices: a violent one instead of the player's (owner request)."""
    v = ce.verb(content, verb_key)
    if npc is None or v is None or v.violent or not cp.active(ch.state) or not cp.voices_take(ch.state, content, rng):
        return None
    arch = ce.archetype(content, npc.archetype)
    if arch is None:
        return None
    pool = [x.key for x in ce.cfg(content).verbs if x.violent and ce.allowed(x, arch)]
    return rng.choice(pool) if pool else None


def _said(scr: Screen, line: str) -> Screen:
    """The player's own line on top of the window (the message itself is deleted from the chat)."""
    text = f"🗣 <i>«{texts.esc(line)}»</i>\n\n" + scr.text
    return Screen(text, scr.keyboard, name=scr.name, state=dict(scr.state or {}), awaiting_text=scr.awaiting_text)


def _with_scene(scr: Screen, replace: str | None, story: str | None) -> Screen | None:
    """The same screen with Claude's scene: in place of `replace` (a ready line), else at the end."""
    if not story:
        return None
    quote = style.quote(texts.esc(story))
    text = scr.text.replace(replace, quote, 1) if replace and replace in scr.text else scr.text + "\n\n" + quote
    return Screen(text, scr.keyboard, name=scr.name, state=dict(scr.state or {}), awaiting_text=scr.awaiting_text)


async def _render_deed(ctx: Ctx, ch: ActiveCharacter, r: svc.DeedResult, verb_key: str, rng: random.Random, place: str,
                       story: str | None = None) -> Screen:
    """What a deed did: the check, the scene (Claude's `story` or a ready line), the numbers, a fight or a death."""
    c = ctx.content
    npc = r.npc
    if r.blocked is not None or npc is None or r.deed is None:
        return await _back(ctx, ch, npc, T.BLOCKED.get(r.blocked or "gone", r.blocked))
    d = r.deed
    parts: list[str] = []
    if d.check is not None:
        parts.append(views.check_line(d.check))  # type: ignore[arg-type]
    if story:
        parts.append(style.quote(texts.esc(story)))
    if d.fight:
        parts.append(T.FIGHT.format(npc=texts.esc(npc.name)))
    elif not story:
        pool = (T.OK if d.ok else T.FAIL).get(verb_key) or []
        if pool:
            parts.append(texts.esc(rng.choice(pool).format(npc=npc.name)))
    if r.applied is not None:
        summary = views.effects_summary(r.applied, c)
        if summary:
            parts.append(summary)
    parts.extend(r.lines)
    if r.died:
        from app.bot.handlers.week import death_screen

        return death_screen(ch.state.name, r.death_cause, parts)
    if r.fight is not None:
        from app.bot.handlers.combat import step_screen

        return step_screen(c, ch, r.fight)  # type: ignore[arg-type]
    if d.killed:
        who = f"«{texts.esc(r.anon)}»" if r.anon else texts.esc(ch.state.name)  # an unrevealed maniac has no face
        ctx.ui.post(f"🩸 {who} убивает человека: <b>{texts.esc(npc.name)}</b>. Район запомнит.", 6 * 3600)
        return await place_screen(ctx, ch, place, "\n".join(parts))
    if not npc.alive or svc.hurt(npc):
        return await place_screen(ctx, ch, place, "\n".join(parts))
    return npc_screen(c, ch, npc, "\n".join(parts))


async def _back(ctx: Ctx, ch: ActiveCharacter, npc: CityNpc | None, note: str | None) -> Screen:
    if npc is None or not npc.alive or svc.hurt(npc):
        return await place_screen(ctx, ch, _VIEW.get() or (npc.place if npc is not None else ""), note)
    return npc_screen(ctx.content, ch, npc, note)


# -- talk ------------------------------------------------------------------------------------------------------------------


def chat_screen(ctx: Ctx, npc_id: int, name: str, role: str, history: list[list[str]], note: str | None = None,
                waiting: str | None = None, did: str | None = None) -> Screen:
    limit = ce.cfg(ctx.content).chat_lines
    lines = [style.header("relations", f"Разговор: {texts.esc(name)}, {texts.esc(role)}"), ""]
    for said, answer in history[-4:]:
        lines += [f"— {texts.esc(said)}", f"<b>{texts.esc(name)}:</b> {texts.esc(answer)}", ""]
    if waiting:
        lines += [f"— {texts.esc(waiting)}", f"<i>{texts.esc(name)} думает…</i>", ""]
    left = limit - len(history)
    state = {"npc": npc_id, "name": name, "role": role, "history": history}
    if did or ctx.state.get("did"):  # owner report 27.10: the talk is judged later like any other (services/dialogs.py)
        state["did"] = did or ctx.state.get("did")
    if left <= 0:
        lines.append(T.CHAT_DONE.format(npc=texts.esc(name)))
        return Screen("\n".join(lines).strip(), [[Button("Закончить", "end")]], name="cnchat", state=state)
    if note:
        lines.append(note)
    lines.append(T.CHAT_PROMPT.format(left=left))
    return Screen("\n".join(lines).strip(), [[Button("Закончить", "end")]], awaiting_text="city_chat", name="cnchat",
                  state=state)


async def chat_open(ctx: Ctx, ch: ActiveCharacter, npc_id: int) -> Screen:
    from app.narrator import narrator

    async with db.session_scope() as s:
        npc = await s.get(CityNpc, npc_id)
    if npc is None or not npc.alive:
        return await place_screen(ctx, ch, npc.place if npc else "")
    if not narrator.is_live():
        return npc_screen(ctx.content, ch, npc, T.CHAT_OFF)
    arch = ce.archetype(ctx.content, npc.archetype)
    return chat_screen(ctx, npc.id, npc.name, arch.name if arch else "", [])


@on_screen("cnchat")
async def cnchat_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    ch, _ = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    async with db.session_scope() as s:
        npc = await s.get(CityNpc, int(ctx.state.get("npc", 0) or 0))
    if npc is None:
        return city_screen(ctx.content, ch)
    return npc_screen(ctx.content, ch, npc)


@on_text("city_chat")  # owner report 24.10: the typed lines are deleted like any other input
async def city_chat_text(ctx: Ctx, text: str) -> Screen | None:
    from app.narrator import narrator
    from app.services import narration

    lock = _locks.setdefault(ctx.user_id, asyncio.Lock())
    if lock.locked():
        return None
    async with lock:
        c = ctx.content
        npc_id = int(ctx.state.get("npc", 0) or 0)
        name = str(ctx.state.get("name", ""))
        role = str(ctx.state.get("role", ""))
        history = [list(x) for x in (ctx.state.get("history") or [])]
        line = " ".join(text.split())[: c.balance.narrator.npc_chat_player_max_chars]
        if not line or len(history) >= ce.cfg(c).chat_lines:
            return chat_screen(ctx, npc_id, name, role, history)
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            npc = await s.get(CityNpc, npc_id)
            if ch is None or npc is None or not npc.alive:
                return None
            a = svc.att(npc, ch.id)
            notes = list(svc.bond(npc, ch.id).get("notes") or [])
            note = await narration.legacy_note(s, c, ch) if narrator.is_live() else ""
        if not narrator.is_live():
            return chat_screen(ctx, npc_id, name, role, history, T.CHAT_OFF)
        await ctx.show(chat_screen(ctx, npc_id, name, role, history, waiting=line))
        card = narration.npc_card(c, ch.state, name=name, who=role, relation=max(0, min(100, 50 + a // 2)), age=None,
                                  enemy=a <= -50, lineage_note=note)
        if notes:
            card["npc"]["memory"] = notes  # type: ignore[index]
        reply = await narrator.npc_reply(card, [(x, y) for x, y in history], line, year=ch.state.current_year)
        if not reply:
            return chat_screen(ctx, npc_id, name, role, history, T.CHAT_SILENT.format(npc=texts.esc(name)))
        history.append([line, reply])
        import time
        from types import SimpleNamespace

        did = str(ctx.state.get("did") or "") if len(history) > 1 else ""
        did = did or f"city{npc_id}-{ch.id}-{int(time.time())}"
        async with db.session_scope() as s:
            await svc.note_talk(s, c, npc_id, ch.id, f"{ch.state.name}: «{line[:80]}» — {name}: «{reply[:100]}»")
            from app.services import dialogs  # owner report 27.10: street talks have consequences too

            ch2 = await game.get_active_character(s, ctx.user_id)
            if ch2 is not None:
                who = SimpleNamespace(name=name, kind="street", data={"bio": role}, relation=max(0, min(100, 50 + a // 2)))
                await dialogs.record(s, c, ch2, who, history, did)
        return chat_screen(ctx, npc_id, name, role, history, did=did)


# -- a free action (stage 3): the player writes what the character does ----------------------------------------------------

FREE_PROMPT = ("<b>Свой вариант.</b> Напиши в чат, что делаешь: «плескаю ему в лицо пиво и забираю ключи», "
               "«предлагаю работу в моей банде», «молча кладу на стойку жетон и жду». Игра поймёт, что это за действие, "
               "бросит проверку и опишет, что вышло. Твоё сообщение исчезнет из беседы, а в окне останется.")


@on_text("city_free")  # owner report 24.10: deleted; the window quotes the line instead
async def city_free_text(ctx: Ctx, text: str) -> Screen | None:
    from app.clock import utcnow
    from app.narrator import narrator

    lock = _locks.setdefault(ctx.user_id, asyncio.Lock())
    if lock.locked():
        return None
    async with lock:
        c = ctx.content
        npc_id = int(ctx.state.get("npc", 0) or 0)
        place = str(ctx.state.get("place", ""))
        line = " ".join(text.split())[: c.balance.narrator.npc_chat_player_max_chars]
        ch, year = await _load(ctx)
        if ch is None:
            return None
        async with db.session_scope() as s:
            npc = await s.get(CityNpc, npc_id)
        if npc is None or not npc.alive:
            return city_screen(c, ch)
        arch = ce.archetype(c, npc.archetype)
        today = utcnow().date().isoformat()
        day = ch.state.flags.get("free_day") or {}
        used = int(day.get("n", 0)) if day.get("d") == today else 0
        if used >= ce.cfg(c).free_per_day:
            return npc_screen(c, ch, npc, "На сегодня свободных действий хватит. Кнопки работают как обычно.")
        verbs = {v.key: v.name for v in ce.cfg(c).verbs
                 if v.key not in ("talk", "service") and arch is not None and ce.allowed(v, arch)}
        card = {"name": npc.name, "role": arch.name if arch else "", "attitude": ce.attitude_word(svc.att(npc, ch.id))}
        got = await narrator.classify_free(line, verbs, card, year=ch.state.current_year)
        if got is None:
            return npc_screen(c, ch, npc, "Город не понял, что ты делаешь. Попробуй проще или выбери кнопку.")
        async with db.session_scope() as s:
            ch2 = await game.get_active_character(s, ctx.user_id)
            if ch2 is not None:
                ch2.state.flags["free_day"] = {"d": today, "n": used + 1}
                await game.save_active(s, ch2, ctx.ui.settings.timezone)
        if got.get("refuse"):
            return npc_screen(c, ch, npc, "Такое в этой истории не случится. Попробуй что-то другое.")
        verb = got.get("verb")
        rng = random.Random()
        if not verb:  # a harmless deed: no numbers, the NPC reacts and remembers
            async with db.session_scope() as s:
                await svc.note_talk(s, c, npc.id, ch.id, f"{ch.state.name}: {got.get('summary') or line[:80]}")
                from app.db import repo as _repo  # owner request 27.10: the scheduled task sees it too

                await _repo.log_event(s, "city_free", {"npc_name": npc.name, "role": card["role"], "said": line[:300],
                                                       "place": place, "district": npc.district}, character_id=ch.id)
            async with db.session_scope() as s:
                npc = await s.get(CityNpc, npc_id)
            plain = texts.esc(f"{npc.name} смотрит на тебя и ничего не говорит.")
            scr = _said(npc_screen(c, ch, npc, plain), line)
            free_payload = {"player_text": line, "npc": card, "no_effect": True}
            yr = ch.state.current_year

            async def job_plain() -> Screen | None:  # owner request: the scene comes after the window
                story = await narrator.free_outcome(free_payload, year=yr)
                return _with_scene(scr, plain, story)

            return late.attach(ctx, scr, job_plain)
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return None
            r = await svc.do(s, c, ch, rng, year, ctx.ui.settings.timezone, npc_id, verb, said=line)
        scr = _said(await _render_deed(ctx, ch, r, verb, rng, place, None), line)
        if r.blocked is not None or r.deed is None or r.fight is not None or r.died:
            return scr
        payload = {"player_text": line, "npc": card, "verb": verbs.get(verb, verb), "ok": r.deed.ok,
                   "fight": r.deed.fight, "killed": r.deed.killed,
                   "effects": r.applied.narrator_effects() if r.applied is not None else {}}
        yr = ch.state.current_year
        async def job() -> Screen | None:  # owner request: the scene comes after the numbers
            story = await narrator.free_outcome(payload, year=yr)
            return _with_scene(scr, None, story)

        return late.attach(ctx, scr, job)


# -- the non-canon gangs and corps (owner request 23.10) -----------------------------------------------------------------


async def dyn_screen(ctx: Ctx, ch: ActiveCharacter, note: str | None = None) -> Screen:
    from app.services import dyn

    c = ctx.content
    dc = dyn.cfg(c)
    async with db.session_scope() as s:
        items = await dyn.all_factions(s)
    live = dyn.alive(items)
    lines = [style.header("crime", "Новые банды и корпорации"), _status(ch), "",
             "Их нет в учебниках истории. Они появляются, грызутся между собой, разваливаются. "
             "Нападут на тебя в их районе — запомнишь лица. "
             f"Собери {dc.search_need} улики, и логово найдено: штурм · {dc.assault_ap} ОД, опасность {dc.assault_danger}, "
             "в логове сидит главарь.", ""]
    if note:
        lines += [note, ""]
    rows: list[list[Button]] = []
    if not live:
        lines.append("Сейчас новых группировок не слышно. Город затишье не любит: подожди.")
    for i, f in enumerate(live, 1):
        d = c.districts.get(f["district"])
        clues = dyn.clues_of(ch, int(f["id"]))
        here = " · твой район" if f["district"] == ch.state.district else ""
        lines.append(f"{i}. {texts.esc(dyn.cap(dyn.label(f)))} — {texts.esc(d.name if d else f['district'])}{here}. "
                     f"Сила {f['power']}/100. Улики: {min(clues, dc.search_need)}/{dc.search_need}")
        if clues >= dc.search_need:
            rows.append([Button(f"Штурм логова {i} · {dc.assault_ap} ОД", "go", str(f["id"]), style="danger")])
        else:
            rows.append([Button(f"Искать логово {i} · {dc.search_ap} ОД", "find", str(f["id"]))])
    dead = [f for f in items if not f.get("alive")][-4:]
    if dead:
        lines += ["", "Уже в прошлом:"]
        for f in reversed(dead):
            how = "уничтожена игроком" if f.get("how") == "player" else "развалилась сама"
            lines.append(f"· {texts.esc(dyn.label(f))} — {how}")
    rows.append([Button(texts.BACK, "city"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="cdyn")


@on_screen("cdyn")
async def cdyn_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    ch, year = await _load(ctx)
    if ch is None:
        return await menu_screen(ctx)
    c = ctx.content
    if action == "city":
        return city_screen(c, ch)
    if action not in ("find", "go") or not arg or not arg.isdigit():
        return await dyn_screen(ctx, ch)
    from app.services import dyn

    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return await menu_screen(ctx)
        if ch.state.flags.get("pending_combat") or ch.state.flags.get("combat"):
            return await dyn_screen(ctx, ch, "Сначала разберись с текущим боем.")
        fn = dyn.search if action == "find" else dyn.assault
        r = await fn(s, c, ch, random.Random(), year, ctx.ui.settings.timezone, int(arg))
    blocked = {"gone": "Этой группировки больше нет.", "jail": T.BLOCKED["jail"], "ap": T.BLOCKED["ap"],
               "found": "Логово уже найдено.", "notfound": "Логово ещё не найдено."}
    if r.blocked is not None:
        return await dyn_screen(ctx, ch, blocked.get(r.blocked, r.blocked))
    if r.fight is not None:
        from app.bot.handlers.combat import step_screen

        return step_screen(c, ch, r.fight)  # type: ignore[arg-type]
    parts = [views.check_line(r.check)] if r.check is not None else []  # type: ignore[arg-type]
    parts += [texts.esc(x) for x in r.lines]
    if r.applied is not None:
        sm = views.effects_summary(r.applied, c)
        if sm:
            parts.append(sm)
    return await dyn_screen(ctx, ch, "\n".join(parts))
