"""Raven Tearz in the daytime and «Чёрные слёзы» (owner request 26.10, docs/PLAN_BLACKTEARS.md).

world_state:
- `bt_offers` = {tg: {id, day, at}} — the contract waiting in the window «🖤 Шёпот Raven»;
- `bt_echo` = [{tg, cid, due, title, text, fx}] — the consequences that come later by letter (PlayerMail `btears`);
- `bt_day` = {tg: {d, n}} — how many times Raven came today;
- `bt_test_sent` — the owner's test contract went out.
The character: flag `bt_rep` (0..100), `bt_hist` (the last contracts), `bt_sweep_day`, `bt_share_day`.
Every Raven visit is a post with his picture (images/<image>) tagging the player: in the group, outside it in private.
"""

from __future__ import annotations

import functools
import html
import logging
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date, kyiv_now
from app.content.loader import Content
from app.db import repo
from app.engine import checks
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

log = logging.getLogger(__name__)

FILE = Path(__file__).resolve().parent.parent.parent / "content" / "blacktears.yaml"
OFFERS, ECHO, DAY, TEST = "bt_offers", "bt_echo", "bt_day", "bt_test_sent"
FLAG_REP, FLAG_HIST, FLAG_SWEEP, FLAG_SHARE = "bt_rep", "bt_hist", "bt_sweep_day", "bt_share_day"
MAIL_KIND = "btears"
SCENES = ("raven_day_auction", "raven_day_bridge", "raven_day_debt", "raven_day_rain", "raven_day_mirror",
          "raven_day_list")
TIERS = ("Чужой", "Слышащий шёпот", "Тень", "Чёрная слеза", "Голос Raven")

# owner request 26.10: his colour — dry, tired, and «корпоратское дерьмо»
QUIPS = (
    "«Корпоратское дерьмо. Везде одно и то же корпоратское дерьмо»",
    "«Давай закончим это быстро»",
    "«Корпы жрут город, город жрёт нас. Мы хотя бы кусаемся»",
    "«Люси говорит, ты справишься. Я бы не ставил, но она редко ошибается»",
    "«Хорошие люди в этом городе долго не живут. Плохие тоже. Живут те, кто не задаёт вопросов»",
    "«Не люблю героев. Они слишком громко умирают»",
    "«Арасака, Милитех — одно и то же дерьмо в разных костюмах»",
    "«Деньги — это просто способ сказать „я пришёл“. Иногда лучше молча»",
    "«Люси скучно. Когда ей скучно, город горит. Давай не будем её злить»",
    "«Ебучие корпы. Все до одного, от уборщика до совета директоров. Хотя нет, уборщика жалко»",
    "«Богатые думают, что их нельзя достать. Охуенно смешные люди»",
    "«Не ссы. Люси говорит, сегодня никто из наших не умрёт. Про ваших она не говорила»",
    "«Корпоратское дерьмо в дорогом костюме всё равно дерьмо»",
)

# owner answers 26.10: «можно сделать ивенты, что он убил того-то» — the city news with his picture, once a day
KILL_NEWS = (
    "Утром в Чартер-Хилл нашли совладельца сети элитных клиник. Охрана ничего не видела, камеры писали помехи. На "
    "зеркале в ванной кто-то нарисовал чёрную слезу.",
    "Топ-менеджер Militech не доехал до аэропорта. Лимузин стоит на Северной окружной, двигатель тёплый, салон пуст. "
    "На стекле — чёрная капля.",
    "Застройщик, выселивший два квартала в Кабуки, найден в своём пентхаусе. Его личный ИИ-ассистент твердит одно "
    "слово: «услышано».",
    "Наследник медиахолдинга пропал после вечеринки на крыше. Гости помнят парня в кепке у бара. Он ничего не пил.",
    "В Норт-Оке сгорела вилла банкира. Пожарные говорят — проводка. Соседи говорят, что перед пожаром все экраны в "
    "доме показывали красный глаз.",
    "Ростовщик из Уотсона, державший полрайона в долгах, найден живым, но без единого эдди на счетах. Деньги ушли на "
    "сотню анонимных переводов жителям его квартала.",
    "Совет директоров одной биотех-компании собрался в полном составе — кроме председателя. Председателя больше нет. "
    "В переговорной стоит раскрытый чёрный зонт.",
)
FLAG_GRUDGE, FLAG_HUNT, FLAG_HUNT_AT = "raven_grudge", "raven_hunt", "raven_hunt_at"
SPITE = {"raven_spite_deck": 2, "raven_spite_port": 2}  # scene choices that cross him


def cfg(content: Content):  # noqa: ANN201
    return content.balance.blacktears


@functools.cache
def _data() -> dict[str, Any]:
    raw = yaml.safe_load(FILE.read_text(encoding="utf-8")) or {}
    raw["by_id"] = {str(c["id"]): c for c in raw.get("contracts") or []}
    return raw


def contract(cid: str) -> dict[str, Any] | None:
    return _data()["by_id"].get(cid)


def rep(ch: ActiveCharacter) -> int:
    return int(ch.state.flags.get(FLAG_REP, 0) or 0)


def tier(content: Content, r: int) -> int:
    return sum(1 for t in cfg(content).tiers if r >= t)


def tier_name(content: Content, r: int) -> str:
    return TIERS[min(tier(content, r), len(TIERS) - 1)]


def quip(rng: random.Random) -> str:
    return rng.choice(QUIPS)


# -- the visits -----------------------------------------------------------------------------------------------------------


async def offer_of(session: AsyncSession, tg: int, tz: str) -> dict | None:
    o = (await repo.ws_get(session, OFFERS, {}) or {}).get(str(tg))
    if not o or o.get("day") != kyiv_date(tz) or contract(str(o.get("id"))) is None:
        return None
    return dict(o)


async def _send_post(ui: "UiKit", tg: int, caption: str) -> None:
    """The picture of Raven with the caption: in the group for a member, in private for the rest."""
    from app.services import dm_relay, images

    try:
        member = ui.group_id is not None and (not dm_relay.cfg(ui).enabled or await dm_relay.in_group(ui, tg))
    except Exception:  # noqa: BLE001
        member = False
    chat = ui.group_id if member else tg
    ttl = 6 * 3600
    path = ui.settings.images_path / cfg(ui.content).image
    if path.is_file():
        pic = images.Picture(cfg(ui.content).image, frozenset({"raven"}), path)
        if await images.send_photo_post(ui, chat, pic, caption, ttl, "news") is not None:
            return
    await ui._send_temp(chat, caption, ttl, "news")  # noqa: SLF001


def _tag(tg: int, name: str) -> str:
    return f'<a href="tg://user?id={int(tg)}">{html.escape(name)}</a>'


async def visit(session: AsyncSession, content: Content, ui: "UiKit | None", ch: ActiveCharacter, rng: random.Random,
                tz: str, kind: str | None = None) -> str | None:
    """Raven comes to the character: a contract or a scene of his day. Returns the kind, or None."""
    from app.services.game import save_active
    from app.services.week import pending_events, set_pending

    st = ch.state
    tg = int(ch.player_tg_id)
    has_offer = await offer_of(session, tg, tz) is not None
    if kind is None:
        kind = "contract" if (not has_offer and rng.random() < cfg(content).contract_share) else "scene"
    name = f"{st.name}".strip()
    if kind == "contract":
        if has_offer:
            return None
        hist = {str(h.get("id")) for h in st.flags.get(FLAG_HIST) or []}
        pool = [c for c in _data()["contracts"] if str(c["id"]) not in hist] or list(_data()["contracts"])
        c = rng.choice(pool)
        offers = dict(await repo.ws_get(session, OFFERS, {}) or {})
        offers[str(tg)] = {"id": c["id"], "day": kyiv_date(tz), "at": time.time()}
        await repo.ws_set(session, OFFERS, offers)
        caption = (f"🖤 {_tag(tg, name)}, Raven Tearz находит тебя сам. {quip(rng)}.\nУ «Чёрных слёз» заказ. "
                   f"Что он изменит, знает только Люси. /play → «🖤 Шёпот Raven».")
    else:
        pool = [e for e in SCENES if e in content.events and e not in pending_events(ch)]
        seen = set(st.flags.get("bt_scenes") or [])
        fresh = [e for e in pool if e not in seen] or pool
        if not fresh:
            return None
        ev = rng.choice(fresh)
        set_pending(ch, pending_events(ch) + [ev])
        st.flags["bt_scenes"] = (list(seen - {ev}) + [ev])[-4:]
        await save_active(session, ch, tz)
        caption = (f"🖤 {_tag(tg, name)}, Raven Tearz рядом. {quip(rng)}.\n"
                   f"«{content.events[ev].title}» ждёт в игре: /play.")
    if ui is not None:
        ui.spawn(_send_post(ui, tg, caption))
    return kind


async def job(session: AsyncSession, content: Content, ui: "UiKit", rng: random.Random | None = None) -> int:
    """Every few minutes: the consequences that came due, the owner's test, Raven's visits in the daytime."""
    from app.db.models import PlayerMail
    from app.services import game

    c = cfg(content)
    if not c.enabled:
        return 0
    rng = rng or random.Random()
    tz = ui.settings.timezone
    now = time.time()
    echo = list(await repo.ws_get(session, ECHO, []) or [])
    keep = []
    for e in echo:
        if float(e.get("due", 0)) > now:
            keep.append(e)
            continue
        session.add(PlayerMail(to_tg_id=int(e["tg"]), from_tg_id=0, from_char_id=None, to_char_id=int(e["cid"]),
                               from_name="Чёрные слёзы", from_year=0, kind=MAIL_KIND,
                               payload={"title": e.get("title", ""), "text": e.get("text", ""), "fx": e.get("fx") or {}}))
    if len(keep) != len(echo):
        await repo.ws_set(session, ECHO, keep)
    sent = 0
    if c.test_tg and not await repo.ws_get(session, TEST, False):
        ch = await game.get_active_character(session, int(c.test_tg))
        await repo.ws_set(session, TEST, True)
        if ch is not None:
            await visit(session, content, ui, ch, rng, tz, "contract")
            sent += 1
    hour = kyiv_now(tz).hour
    if not c.hour_from <= hour < c.hour_to:
        return sent
    sent += await _news(session, content, ui, rng, tz)
    days = dict(await repo.ws_get(session, DAY, {}) or {})
    today = kyiv_date(tz)
    ticks = max(1, (c.hour_to - c.hour_from) * 60 // max(1, c.tick_min))
    chance = min(1.0, c.per_day / ticks * 1.6)
    for row in await repo.living_characters(session):
        tg = int(row.player_tg_id)
        if await _hunt(session, content, ui, row, rng, tz, now):
            sent += 1
            continue
        rec = days.get(str(tg)) or {}
        n = int(rec.get("n", 0)) if rec.get("d") == today else 0
        if n >= c.per_day or rng.random() >= chance:
            continue
        ch = await game.get_active_character(session, tg)
        if ch is None or ch.id != row.id:
            continue
        st = ch.state
        d = content.districts.get(st.district)
        knows = st.flags.get("raven_her_known") or st.flags.get("raven_trust")  # his day is about her
        if (not knows or st.flags.get("abroad") or d is None or d.city != "nc"
                or int(st.flags.get("jail_weeks", 0) or 0) > 0):
            continue
        if await visit(session, content, ui, ch, rng, tz):
            days[str(tg)] = {"d": today, "n": n + 1}
            sent += 1
    await repo.ws_set(session, DAY, days)
    return sent


# -- the answer ------------------------------------------------------------------------------------------------------------


def good_chance(content: Content, r: int, accepted: bool, check_ok: bool | None) -> float:
    c = cfg(content)
    p = c.accept_good if accepted else c.refuse_good
    if accepted and check_ok is True:
        p += c.check_ok_bonus
    elif accepted and check_ok is False:
        p -= c.check_fail_malus
    t = tier(content, r)
    p += c.tier_good[min(t, len(c.tier_good) - 1)]
    if not accepted and t >= 2:
        p = min(p, c.refuse_high_good)
    return max(0.05, min(0.95, p))


async def answer(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                 accept: bool) -> list[str]:
    """Do the contract or refuse it. Now: the reputation and one line; later: the consequence by letter."""
    from app.services import ap as ap_services
    from app.services.game import save_active

    c = cfg(content)
    tg = int(ch.player_tg_id)
    o = await offer_of(session, tg, tz)
    if o is None:
        return ["Заказа больше нет. Шёпот стих."]
    k = contract(str(o["id"]))
    assert k is not None
    st = ch.state
    r0 = rep(ch)
    t = tier(content, r0)
    lines: list[str] = []
    check_ok: bool | None = None
    fx_now: dict[str, int] = {}
    if accept:
        if not ap_services.can_spend(ch, c.accept_ap):
            return ["Не хватает ОД."]
        ap_services.spend(ch, content, c.accept_ap)
        chk = k.get("check")
        if chk:
            res = checks.roll_check(st, content, rng, str(chk["stat"]), chk.get("skill"), int(chk["dv"]))
            check_ok = res.ok
            from app.bot import views

            lines.append(views.check_line(res))
        dr = c.rep_accept + (c.rep_check_bonus if check_ok else 0)
        lines.append("Сделано. Что это было, ты не знаешь. Пока." if check_ok is not False else
                     "Сделано, но не чисто. Кто-то мог видеть. Что это было, ты не знаешь. Пока.")
        if t >= 2 and rng.random() < c.humanity_chance:
            fx_now["humanity"] = -1
            lines.append("Ночью тебе снится Люси. Человечность −1: она слишком близко.")
        if t >= 3:
            fx_now["heat"] = 1
            lines.append("NCPD начинает связывать странные совпадения. Розыск +1.")
    else:
        dr = -(c.rep_refuse_high if t >= 2 else c.rep_refuse)
        st.flags[FLAG_GRUDGE] = int(st.flags.get(FLAG_GRUDGE, 0) or 0) + (2 if t >= 2 else 1)  # he remembers
        lines.append("Ты отказываешься. Raven пожимает плечами: «Люси запомнит». Что изменит отказ, ты тоже не знаешь.")
    new = max(0, min(100, r0 + dr))
    st.flags[FLAG_REP] = new
    lines.append(f"Репутация у Слёз: {new} ({'+' if dr >= 0 else ''}{dr}) · {tier_name(content, new)}.")
    if tier(content, new) > t:
        lines.append(f"🖤 Новый круг: «{tier_name(content, new)}».")
    good = rng.random() < good_chance(content, r0, accept, check_ok)
    out = k[("accept_" if accept else "refuse_") + ("good" if good else "bad")]
    fx = {str(a): int(b) for a, b in (out.get("fx") or {}).items()}
    if good and tier(content, new) >= 3 and fx.get("eddies", 0) > 0:
        fx["eddies"] = round(fx["eddies"] * c.tier3_pay_mult)
    if fx_now:
        apply_effects(st, fx_now, content, rng, year)
    hist = list(st.flags.get(FLAG_HIST) or [])
    hist.append({"id": k["id"], "a": accept, "g": good, "at": time.time()})
    st.flags[FLAG_HIST] = hist[-8:]
    await save_active(session, ch, tz)
    offers = dict(await repo.ws_get(session, OFFERS, {}) or {})
    offers.pop(str(tg), None)
    await repo.ws_set(session, OFFERS, offers)
    echo = list(await repo.ws_get(session, ECHO, []) or [])
    echo.append({"tg": tg, "cid": ch.id, "due": time.time() + rng.randint(c.echo_min, c.echo_max) * 60,
                 "title": ("заказ" if accept else "отказ") + f" {k['text'][:50].rstrip()}", "text": out["text"], "fx": fx})
    await repo.ws_set(session, ECHO, echo)
    return lines


def sweep(content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str) -> str:
    """«Тень» and above: once a Kyiv day the Tears clean the trail."""
    c = cfg(content)
    st = ch.state
    if tier(content, rep(ch)) < 2:
        return "Слёзы заметают следы только за своими. Нужен круг «Тень»."
    if st.flags.get(FLAG_SWEEP) == kyiv_date(tz):
        return "Сегодня следы уже заметали."
    if st.heat <= 0:
        return "Заметать нечего: тебя никто не ищет."
    apply_effects(st, {"heat": -c.sweep_heat}, content, rng, year)
    st.flags[FLAG_SWEEP] = kyiv_date(tz)
    return f"Пара свидетелей вдруг ничего не помнит, запись камеры повреждена. Розыск {st.heat}."


def share(content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str) -> str:
    """«Чёрная слеза» and above: once a Kyiv day the share of the Tears."""
    c = cfg(content)
    st = ch.state
    t = tier(content, rep(ch))
    if t < 3:
        return "Доля только для «Чёрной слезы» и выше."
    if st.flags.get(FLAG_SHARE) == kyiv_date(tz):
        return "Доля за сегодня уже у тебя."
    n = c.share_eddies * (t - 2)
    apply_effects(st, {"eddies": n}, content, rng, year)
    st.flags[FLAG_SHARE] = kyiv_date(tz)
    return f"В почтовом ящике конверт с чёрной каплей на печати: {n} эдди."


# -- his kills in the news, his anger at the ones who make him trouble (owner answers 26.10) -----------------------------


async def _news(session: AsyncSession, content: Content, ui: "UiKit", rng: random.Random, tz: str) -> int:
    """Once a Kyiv day, at a random tick of the day window: one of the rich is gone, with a black tear."""
    c = cfg(content)
    today = kyiv_date(tz)
    if await repo.ws_get(session, "bt_news_day", "") == today or ui.group_id is None:
        return 0
    ticks = max(1, (c.hour_to - c.hour_from) * 60 // max(1, c.tick_min))
    if rng.random() >= 1.5 / ticks:
        return 0
    await repo.ws_set(session, "bt_news_day", today)
    used = list(await repo.ws_get(session, "bt_news_used", []) or [])
    pool = [i for i in range(len(KILL_NEWS)) if i not in used] or list(range(len(KILL_NEWS)))
    i = rng.choice(pool)
    await repo.ws_set(session, "bt_news_used", (used + [i])[-(len(KILL_NEWS) - 1):])
    text = f"🖤 <b>Хроника Найт-Сити</b>\n{KILL_NEWS[i]}\n<i>Говорят, это «Чёрные слёзы». Говорят тихо.</i>"
    ui.spawn(_group_post(ui, text))
    return 1


async def _group_post(ui: "UiKit", text: str) -> None:
    from app.services import images

    path = ui.settings.images_path / cfg(ui.content).image
    if path.is_file() and ui.group_id is not None:
        pic = images.Picture(cfg(ui.content).image, frozenset({"raven"}), path)
        if await images.send_photo_post(ui, ui.group_id, pic, text, 12 * 3600, "news") is not None:
            return
    ui.post(text, 12 * 3600, "news")


async def _hunt(session: AsyncSession, content: Content, ui: "UiKit", row, rng: random.Random, tz: str,  # noqa: ANN001
                now: float) -> bool:
    """Raven comes for the one who crossed him too often: the hunt waits in the menu (handlers/blacktears.py)."""
    from app.services import game
    from app.services.game import save_active

    c = cfg(content)
    ch = await game.get_active_character(session, int(row.player_tg_id))
    if ch is None or ch.id != row.id:
        return False
    st = ch.state
    changed = False
    for key, n in SPITE.items():
        if st.flags.pop(key, None):
            st.flags[FLAG_GRUDGE] = int(st.flags.get(FLAG_GRUDGE, 0) or 0) + n
            changed = True
    grudge = int(st.flags.get(FLAG_GRUDGE, 0) or 0)
    d = content.districts.get(st.district)
    ready = (grudge >= c.hunt_grudge and not st.flags.get(FLAG_HUNT) and d is not None and d.city == "nc"
             and not st.flags.get("abroad") and int(st.flags.get("jail_weeks", 0) or 0) <= 0
             and now - float(st.flags.get(FLAG_HUNT_AT, 0) or 0) > c.hunt_cooldown_h * 3600)
    if ready:
        st.flags[FLAG_HUNT] = True
        st.flags[FLAG_HUNT_AT] = now
        changed = True
    if changed:
        await save_active(session, ch, tz)
    if ready:
        tg = int(ch.player_tg_id)
        ui.spawn(_send_post(ui, tg, f"🖤 {_tag(tg, st.name)}, Raven Tearz идёт за тобой. «Ты делаешь мне слишком много "
                                    f"проблем, чумба. Давай закончим это быстро». /play"))
    return ready


def boss_faction(content: Content, year: int) -> str:
    from app.engine import combat as cb

    for f in ("maelstrom", "animals", "tyger_claws", "scavengers", "valentinos"):
        if f in content.gangs and cb._faction_pool(content, f, year):  # noqa: SLF001
            return f
    return "maelstrom"


async def boss_fight(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str):  # noqa: ANN201 - a combat Step
    from app.services import combat

    c = cfg(content)
    ch.state.flags.pop(FLAG_HUNT, None)
    return await combat.begin(session, content, ch, rng, year, tz, faction=boss_faction(content, year),
                              danger=c.boss_danger, foes=[build_boss(content, ch)],
                              src={"src": "ravenboss", "nonlethal": True, "no_rep": True, "long": True,
                                   "title": "Raven Tearz"})


def build_boss(content: Content, ch: ActiveCharacter):  # noqa: ANN201 - a Combatant
    """Raven as one very strong enemy, measured against the character: the strongest chrome archetype of any year,
    his health a multiple of the character's, his blade always close to hitting."""
    from app.engine import combat as cb
    from app.engine import derived

    c = cfg(content)
    pool = [e for e in content.enemies.values() if "cyberpsycho" in e.factions] or list(content.enemies.values())
    arch = max(pool, key=lambda e: (e.danger, e.attack))
    b = cb.build_enemy(arch, 0, content, "Raven Tearz")
    p = cb.build_player(ch.state, content)
    b.max_hp = b.hp = max(c.boss_hp_min, int(derived.max_hp(ch.state, content) * c.boss_hp_mult))
    b.defense = max(b.defense, max((a.base for a in p.attacks.values()), default=10) + 4)
    from app.engine import dice

    want = max(4, round(p.max_hp * c.boss_hit_pct))  # one hit takes about this much of the character's health
    for a in list(b.attacks.values()):
        a.base = max(a.base, p.defense + c.boss_hit_edge)
        bonus = max(0, want - round(dice.dice_average(a.dice)) - p.armor // 2)
        if a.kind == cb.RANGED:
            b.ranged_damage = max(b.ranged_damage, bonus)
        else:
            b.melee_damage = max(b.melee_damage, bonus)
    b.armor = max(b.armor, p.armor)
    b.leader = True
    return b


def boss_talk(content: Content, ch: ActiveCharacter, rng: random.Random) -> tuple[bool, str]:
    c = cfg(content)
    res = checks.roll_check(ch.state, content, rng, "COOL", "persuasion", c.boss_talk_dv)
    from app.bot import views

    line = views.check_line(res)
    if res.ok:
        ch.state.flags.pop(FLAG_HUNT, None)
        ch.state.flags[FLAG_GRUDGE] = max(0, int(ch.state.flags.get(FLAG_GRUDGE, 0) or 0) - 2)
        return True, (f"{line}\nRaven долго смотрит на тебя, потом убирает катану. «Ладно. Люси говорит, ты ещё "
                      f"пригодишься. Не заставляй её ошибаться, блядь».")
    return False, f"{line}\n«Слова. Все вы, суки, мастера слов». Он уже рядом."


def boss_pay(content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> str | None:
    c = cfg(content)
    if ch.state.eddies < c.boss_price:
        return None
    apply_effects(ch.state, {"eddies": -c.boss_price}, content, rng, year)
    ch.state.flags.pop(FLAG_HUNT, None)
    ch.state.flags[FLAG_GRUDGE] = 1
    return ("Raven пересчитывает эдди и хмыкает: «Видишь? Иногда живой богач полезнее мёртвого. Даже такой мелкий». "
            "Он уходит, не оборачиваясь.")


async def boss_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                    tz: str, result: str, ctx: dict) -> list[str]:
    """He never dies: beaten, he is gone in a flash of the Sandevistan; winning, he leaves the player alive."""
    from app.engine import combat as cb
    from app.services import loud
    from app.services.game import save_active

    c = cfg(content)
    st = ch.state
    if result == cb.WON:
        st.flags[FLAG_GRUDGE] = 0
        st.flags["raven_beaten"] = int(st.flags.get("raven_beaten", 0) or 0) + 1
        st.flags[FLAG_REP] = min(100, rep(ch) + c.boss_won_rep)
        apply_effects(st, {"street_cred": 3, "fame": 2}, content, rng, year)
        await save_active(session, ch, tz)
        await loud.post(session, ch.id, f"{st.name} {st.surname}: бой с Raven Tearz выигран, он отступил.")
        return ["Последний удар — и воздух дрожит красным: Сандевистан уносит его прочь. Убить Raven нельзя, он всегда "
                "уходит. Из темноты доносится: «Ладно, чумба. Твоя взяла. Пока». Уважение Слёз +"
                f"{c.boss_won_rep}, уличная репутация +3, слава +2."]
    lost = min(st.eddies, max(0, st.eddies * c.boss_take_pct // 100))
    if lost:
        apply_effects(st, {"eddies": -lost}, content, rng, year)
    st.flags[FLAG_GRUDGE] = max(0, int(st.flags.get(FLAG_GRUDGE, 0) or 0) - 2)
    await save_active(session, ch, tz)
    return [f"Ты лежишь на мокром асфальте. Raven вытирает катану о твою куртку и забирает {lost} эдди. «Это "
            f"предупреждение, сука. Люси просила тебя не убивать. Следующего раза она может не попросить»."]
