"""The deadly games of the rich (owner request 24.10). Not canon, not any show: an island, masked guards, children's games,
a prize that grows with every death.

The character gets in by an event (flag `dg_enter`: an invitation accepted or an abduction). From then on the window is the
game (handlers/dgame.py) until the character wins, the vote stops the game or the character dies. Rounds: «Замри»,
«Соты», «Канат», a night in the dormitory, «Шарики» (with a partner), «Стеклянный мост», the final.
Flag `dgame` = {round, alive, number, partner, shape, pending {opt, check}, log}.
A survivor gets the flag `dg_survivor`; the storm of the island (events_dgame.yaml) closes the games for a while
(world_state `dgame_closed_until`).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.engine import checks
from app.engine import combat as cb
from app.engine.effects import apply_effects
from app.bot.texts import esc
from app.services import loud
from app.services.game import ActiveCharacter, save_active

FLAG = "dgame"
FLAG_ENTER = "dg_enter"
FLAG_SURVIVOR = "dg_survivor"
SRC = "dgame"
CLOSED_KEY = "dgame_closed_until"
PLAYERS_KEY = "dgame_players"

ROUNDS = ("light", "honey", "rope", "night", "marbles", "glass", "final")
CUT = {"light": 0.45, "honey": 0.35, "rope": 0.5, "night": 0.15, "marbles": 0.5, "glass": 0.7}
SHAPES = (("круг", -2), ("треугольник", 0), ("звезда", 2), ("зонтик", 4))
PARTNERS = ("старик с кашлем", "девчонка-карманница из Кабуки", "бывший корпорат в рваном костюме",
            "кочевник с перевязанной рукой", "тихий парень с номером на шее", "медсестра без лицензии")

TITLES = {"light": "Раунд 1. «Замри»", "honey": "Раунд 2. «Соты»", "rope": "Раунд 3. «Канат»",
          "night": "Ночь в спальне", "marbles": "Раунд 4. «Шарики»", "glass": "Раунд 5. «Стеклянный мост»",
          "final": "Финал"}
INTRO = {
    "light": "Огромное поле, стена с нарисованным деревом, механическая кукла поворачивает голову. «Замри!» "
             "Кто двинется, когда она смотрит, получает пулю от охраны в масках. Дойти нужно за пять минут.",
    "honey": "Каждому дают жестяную коробку. Внутри пластина из жжёного сахара с фигурой: {shape}. Вырезать фигуру "
             "целой за десять минут. Треснет — выстрел.",
    "rope": "Две команды по десять человек, канат над пропастью. Проигравшая команда падает вниз вся.",
    "night": "Свет гаснет. В спальне кто-то решает, что игроков слишком много, и начинает резать спящих.",
    "marbles": "Разбиться на пары. Партнёр — {partner}. У каждого по десять шариков, игра любая. Кто останется без "
               "шариков — выбывает. Из пары выходит один.",
    "glass": "Мост из стеклянных плит над пропастью. Каждая пара плит: одна каленая, другая лопнет. Порядок — по номерам, "
             "которые нужно выбрать прямо сейчас.",
    "final": "Двое на песке под дождём. Напротив игрок, который прошёл всё то же, что и ты. Победитель забирает всё.",
}
# option: (label, stat, skill, dv shift, extra) ; extra: humanity cost on success, "die" = a sacrifice, "fight", "split"
OPTIONS: dict[str, list[tuple[str, str, str | None, int, dict[str, Any]]]] = {
    "light": [("Идти медленно", "COOL", None, 0, {}), ("Рывками между отсчётами", "REF", None, 1, {}),
              ("Прятаться за спинами", "INT", None, -1, {"humanity": -2})],
    "honey": [("Резать иглой", "TECH", "tech", 0, {}), ("Растопить языком", "INT", None, -1, {})],
    "rope": [("Тянуть изо всех сил", "BODY", "melee", 0, {}), ("Старый трюк: лечь назад", "INT", None, 0, {})],
    "night": [("Держаться у стены", "COOL", None, 0, {}), ("Вырубить зачинщика", "BODY", "melee", 1, {"street_cred": 1})],
    "marbles": [("Играть честно", "LUCK", None, 0, {}), ("Обмануть партнёра", "COOL", "persuasion", -1, {"humanity": -8}),
                ("Отдать шарики", "", None, 0, {"die": True})],
    "glass": [("Идти первым", "LUCK", None, 4, {}), ("Идти в середине", "LUCK", None, 0, {}),
              ("Идти последним", "REF", None, -1, {})],
    "final": [("Драться", "", None, 0, {"fight": True}), ("Разделить приз", "COOL", "persuasion", 4, {"split": True})],
}
OK_TEXT = {
    "light": "Ты пересекаешь линию. За спиной ещё стреляют. Табло: живых {alive}.",
    "honey": "Фигура выходит целой, до последнего лучика. Сладкий запах, пороховой дым. Живых {alive}.",
    "rope": "Твоя команда падает на спину, канат дёргается в вашу сторону, и чужие крики уходят вниз. Живых {alive}.",
    "night": "Свет загорается. На полу те, кому не повезло. Ты дышишь. Живых {alive}.",
    "marbles": "Последний шарик твой. {partner} отходит к стене и больше не оборачивается. Живых {alive}.",
    "glass": "Последний прыжок, и под ногами бетон. За спиной звенит стекло. Живых: двое, и один из них ты.",
}
FAIL_DEATH = {
    "light": "Ты дёргаешься на долю секунды позже. Кукла поворачивает голову, и грохот выстрела — последнее, что слышно.",
    "honey": "Сахар трескается по линии зонтика. Охранник в маске уже рядом.",
    "rope": "Канат рвёт ладони, команда скользит к краю, и пол уходит из-под ног.",
    "marbles": "Последний шарик катится не туда. {partner} опускает глаза.",
    "glass": "Плита под ногой лопается со звуком выстрела.",
}
WOUNDED = "Чудом, на последней секунде. Ты жив, но заплатил кровью: здоровье −{hp}. Живых {alive}."


def cfg(content: Content):  # noqa: ANN201
    return content.balance.dgame


def run_of(ch: ActiveCharacter) -> dict[str, Any] | None:
    r = ch.state.flags.get(FLAG)
    return r if isinstance(r, dict) else None


def round_key(run: dict[str, Any]) -> str:
    return ROUNDS[min(int(run["round"]), len(ROUNDS) - 1)]


def pool(content: Content, run: dict[str, Any]) -> int:
    return (cfg(content).players - int(run["alive"])) * cfg(content).per_head


async def closed(session: AsyncSession) -> bool:
    return float(await repo.ws_get(session, CLOSED_KEY, 0) or 0) > time.time()


async def _registry(session: AsyncSession, ch: ActiveCharacter, status: str) -> None:
    reg = dict(await repo.ws_get(session, PLAYERS_KEY, {}) or {})
    run = run_of(ch) or {}
    if status == "out":
        reg.pop(str(ch.player_tg_id), None)
    else:
        reg[str(ch.player_tg_id)] = {"name": f"{ch.state.name} {ch.state.surname}".strip(), "round": int(run.get("round", 0)),
                                     "number": int(run.get("number", 0))}
    await repo.ws_set(session, PLAYERS_KEY, reg)


async def others(session: AsyncSession, ch: ActiveCharacter) -> list[dict]:
    reg = dict(await repo.ws_get(session, PLAYERS_KEY, {}) or {})
    return [v for k, v in reg.items() if k != str(ch.player_tg_id)]


async def enter_if_due(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random,
                       tz: str) -> str | None:
    """An event set `dg_enter`: the game starts now. Returns a line when the games are closed."""
    st = ch.state
    how = st.flags.pop(FLAG_ENTER, None)
    if not how or run_of(ch) is not None:
        if how:
            await save_active(session, ch, tz)
        return None
    if await closed(session):
        await save_active(session, ch, tz)
        return "Визитка ведёт на пустой причал: остров разгромлен, игр пока нет."
    shape = rng.choice(SHAPES)
    st.flags[FLAG] = {"round": 0, "alive": cfg(content).players, "number": rng.randint(1, cfg(content).players),
                      "partner": rng.choice(PARTNERS), "shape": list(shape), "pending": None, "log": [],
                      "how": str(how)}
    h = await host_active(session)
    if h and str(h.get("tg")) != str(ch.player_tg_id):  # owner request 24.10: the game of a rich player
        st.flags[FLAG]["host"] = str(h["tg"])
        st.flags[FLAG]["host_title"] = str(h.get("title", ""))
    await _registry(session, ch, "in")
    await save_active(session, ch, tz)
    return None


@dataclass
class Result:
    blocked: str | None = None
    lines: list[str] = field(default_factory=list)
    check: object | None = None
    died: bool = False
    won: bool = False
    step: object | None = None
    quit: bool = False


def _intro(run: dict[str, Any], rk: str) -> str:
    return INTRO[rk].format(shape=run["shape"][0], partner=run["partner"])


def _advance(content: Content, run: dict[str, Any], rng: random.Random) -> None:
    rk = round_key(run)
    cut = CUT.get(rk, 0)
    alive = int(run["alive"])
    left = max(2, int(round(alive * (1 - cut)))) if rk != "glass" else 2
    run["alive"] = left
    run["round"] = int(run["round"]) + 1
    run["pending"] = None


async def choose(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                 tz: str, idx: int) -> Result:
    run = run_of(ch)
    if run is None:
        return Result("none")
    if run.get("pending"):
        return Result("pending")
    rk = round_key(run)
    opts = OPTIONS[rk]
    if not 0 <= idx < len(opts):
        return Result("none")
    label, stat, skill, shift, extra = opts[idx]
    c = cfg(content)
    st = ch.state
    res = Result()
    if extra.get("die"):
        res.lines.append(f"Ты отдаёшь {run['partner']} свои шарики. Он долго смотрит на тебя, потом плачет. "
                         "Охранник в маске подходит сзади.")
        return await _die(session, content, ch, tz, res, rng, year)
    if extra.get("fight"):
        return await _final_fight(session, content, ch, rng, year, tz, res)
    dv = c.dv.get(rk if rk != "final" else "marbles", 13) + shift + (int(run["shape"][1]) if rk == "honey" else 0)
    chk = checks.roll_check(st, content, rng, stat, skill, dv)
    res.check = chk
    if chk.ok:
        return await _passed(session, content, ch, rng, year, tz, run, rk, extra, res)
    run["pending"] = {"idx": idx, "margin": chk.margin, "dv": dv}
    st.flags[FLAG] = run
    await save_active(session, ch, tz)
    return res


async def _final_fight(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                       tz: str, res: Result) -> Result:
    from app.services import combat as combat_svc

    gangs = list(content.available("gangs", year))
    res.step = await combat_svc.begin(session, content, ch, rng, year, tz, faction=rng.choice(gangs) if gangs else "ncpd",
                                      danger=cfg(content).final_danger, src={"src": SRC, "solo": True,
                                                                         "foe": f"Финалист №{rng.randint(1, cfg(content).players)}"})
    return res


async def _passed(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                  run: dict[str, Any], rk: str, extra: dict[str, Any], res: Result) -> Result:
    st = ch.state
    if extra.get("split"):
        half = pool(content, run) // 2
        res.lines.append(f"Вы стоите под дождём и молчите. Потом оба бросаете ножи на песок. Охрана ждёт приказа сверху. "
                         f"Приказ приходит: разделить. {half} эдди на двоих — твоя половина.")
        return await _win(session, content, ch, rng, year, tz, run, half, res)
    fx = {k: v for k, v in extra.items() if k in ("humanity", "street_cred")}
    if fx:
        apply_effects(st, fx, content, rng, year)
    _advance(content, run, rng)
    res.lines.append(OK_TEXT[rk].format(alive=run["alive"], partner=run["partner"]))
    st.flags[FLAG] = run
    await _registry(session, ch, "in")
    await save_active(session, ch, tz)
    return res


async def take_fail(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                    tz: str) -> Result:
    """The failed roll stands: a near miss leaves the character wounded, else the end (the night only wounds)."""
    run = run_of(ch)
    if run is None or not run.get("pending"):
        return Result("none")
    c = cfg(content)
    rk = round_key(run)
    p = run["pending"]
    margin = int(p.get("margin", -99))
    res = Result()
    if rk == "final":  # the other finalist says no: the knives come out
        run["pending"] = None
        ch.state.flags[FLAG] = run
        res.lines.append("Финалист смеётся тебе в лицо и бросается первым.")
        return await _final_fight(session, content, ch, rng, year, tz, res)
    if rk == "night" or margin >= -c.near_miss:
        hp = c.near_miss_hp if rk != "night" else 15
        apply_effects(ch.state, {"hp": -hp}, content, rng, year)
        if ch.state.hp <= 0:
            ch.state.hp = 1
        _advance(content, run, rng)
        res.lines.append(WOUNDED.format(hp=hp, alive=run["alive"]))
        ch.state.flags[FLAG] = run
        await _registry(session, ch, "in")
        await save_active(session, ch, tz)
        return res
    res.lines.append(FAIL_DEATH.get(rk, "Охранник в маске поднимает пистолет.").format(partner=run["partner"]))
    return await _die(session, content, ch, tz, res, rng, year)


async def glitch(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                 tz: str) -> Result:
    run = run_of(ch)
    if run is None or not run.get("pending"):
        return Result("none")
    st = ch.state
    if st.glitches < 1 or run["pending"].get("rerolled"):
        return Result("glitch")
    rk = round_key(run)
    label, stat, skill, shift, extra = OPTIONS[rk][int(run["pending"]["idx"])]
    apply_effects(st, {"glitches": -1}, content, rng, year)
    chk = checks.roll_check(st, content, rng, stat, skill, int(run["pending"]["dv"]))
    res = Result(check=chk)
    res.lines.append("Глюк: мир на секунду мигает, и всё начинается заново.")
    if chk.ok:
        run["pending"] = None
        return await _passed(session, content, ch, rng, year, tz, run, rk, extra, res)
    run["pending"] = {**run["pending"], "margin": chk.margin, "rerolled": True}
    st.flags[FLAG] = run
    await save_active(session, ch, tz)
    return res


async def vote(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, tz: str) -> Result:
    run = run_of(ch)
    if (run is None or run.get("pending") or round_key(run) == "final" or int(run["round"]) == 0
            or run.get("voted_round") == run["round"]):
        return Result("none")
    run["voted_round"] = run["round"]
    res = Result()
    if rng.random() < cfg(content).vote_quit_chance:
        ch.state.flags.pop(FLAG, None)
        ch.state.flags[FLAG_SURVIVOR] = True
        await _registry(session, ch, "out")
        res.quit = True
        res.lines.append("Голосование: большинство за выход. Тебя усыпляют газом, и ты просыпаешься в Найт-Сити на "
                         "скамейке, без денег, с визиткой в кармане. На ней только телефон.")
    else:
        run["voted"] = int(run.get("voted", 0)) + 1
        ch.state.flags[FLAG] = run
        res.lines.append("Голосование: большинство за продолжение. Табло показывает копилку, и люди отводят глаза.")
    await save_active(session, ch, tz)
    return res


def _rescue(content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> list[str] | None:
    """Owner report 24.10: a Trauma Team contract (platinum always comes) or the Second Heart pulls the character off
    the island alive, without the prize. None: nobody comes."""
    from app.engine import death as death_eng
    from app.engine.effects import apply_remove_cyberware

    st = ch.state
    tier = death_eng.trauma_tier(st, content, year)
    if tier is not None:
        entry = content.balance.trauma_team[tier]
        if rng.random() < entry.rescue_chance:
            apply_effects(st, {"eddies": -entry.evac_price}, content, rng, year)
            st.hp = max(st.hp, death_eng.revive_hp(st, content))
            return [f"Над островом ревут винты. Броневертолёт Trauma Team ({entry.name}) садится прямо на поле: контракт "
                    f"не знает слов «частная территория». Охрана в масках опускает оружие перед пулемётами. Тебя грузят на "
                    f"носилки и увозят в Найт-Сити. Счёт за эвакуацию: {entry.evac_price} эдди. Здоровье: {st.hp}."]
    key = death_eng.heart_key(st, content)
    if key is not None:
        apply_remove_cyberware(st, content, key, rng, year, refund=False)
        st.hp = max(st.hp, death_eng.revive_hp(st, content))
        return ["Охрана сбрасывает тело в море. В холодной воде заводится Второе сердце. Тебя подбирает рыбацкая лодка у "
                f"берегов Найт-Сити. Импланта больше нет, зато ты дышишь. Здоровье: {st.hp}."]
    return None


async def _die(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, res: Result,
               rng: random.Random | None = None, year: int | None = None) -> Result:
    from app.services import death

    saved = _rescue(content, ch, rng or random.Random(), year if year is not None else ch.state.current_year)
    if saved is not None:
        ch.state.flags.pop(FLAG, None)
        ch.state.flags[FLAG_SURVIVOR] = True
        await _registry(session, ch, "out")
        await save_active(session, ch, tz)
        res.lines.extend(saved)
        res.quit = True
        return res
    run = run_of(ch) or {}
    ch.state.flags.pop(FLAG, None)
    await _registry(session, ch, "out")
    await _pay_host(session, content, run, pool(content, run) * cfg(content).host_dead_pct // 100 if run else 0,
                    f"Игрок №{run.get('number', 0):03d} выбыл в твоей игре. Твоя доля копилки")
    await death.kill(session, content, ch, "dgame", tz)
    res.died = True
    return res


async def _win(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               run: dict[str, Any], prize: int, res: Result) -> Result:
    st = ch.state
    apply_effects(st, {"eddies": prize, "humanity": -5, "fame": 3}, content, rng, year)
    st.flags.pop(FLAG, None)
    st.flags[FLAG_SURVIVOR] = True
    await _registry(session, ch, "out")
    await loud.post(session, ch.id, f"Ночью в порт Найт-Сити приходит катер без флага. На причале оставляют одного "
                                    f"человека с кредитной картой на {prize} эдди: {loud.who(st.name, st.surname)}. "
                                    "Где этот человек был, никто не говорит.")
    await save_active(session, ch, tz)
    res.won = True
    res.lines.append(f"Ты выигрываешь. {prize} эдди на карте. Человечность −5: такое не проходит даром. Катер увозит "
                     "тебя домой.")
    await _pay_host(session, content, run, prize * cfg(content).host_win_pct // 100,
                    f"Игрок №{run.get('number', 0):03d} выиграл в твоей игре. Доля со ставок гостей")
    return res


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict[str, Any]) -> list[str]:
    run = run_of(ch)
    if run is None:
        return []
    if result == cb.WON:
        res = await _win(session, content, ch, rng, year, tz, run, pool(content, run), Result())
        return res.lines
    if result == cb.DOWNED and ch.state.hp > 0:  # the fight already called the Trauma Team or the heart, or the save held
        ch.state.flags.pop(FLAG, None)
        ch.state.flags[FLAG_SURVIVOR] = True
        await _registry(session, ch, "out")
        await save_active(session, ch, tz)
        return ["Финал остаётся за другим. Тебя, полуживого, выносят с песка и выбрасывают на причале Найт-Сити без денег."]
    res = await _die(session, content, ch, tz, Result(), rng, year)
    return ["Охрана не даёт уйти с песка. Охранник в маске подходит к тебе и поднимает пистолет."] + res.lines


async def on_resolved(session: AsyncSession, content: Content, ch: ActiveCharacter, label: str, ok: bool) -> None:
    if label.startswith("Продать") or (label.startswith("Отдать") and not ok):
        return
    h = await host_active(session)
    if h:  # the storm unmasks the host
        await repo.ws_set(session, HOST_KEY, None)
        _mail(session, int(h["tg"]), "Остров взят штурмом. Твою игру закрыли, а имя хозяина теперь знает весь город.")
        await loud.post(session, None, f"В бумагах острова нашли имя хозяина последней игры: {esc(str(h.get('name', '')))}. "
                                       "Маска больше не поможет.")
    await repo.ws_set(session, CLOSED_KEY, time.time() + cfg(content).closed_days * 86400)
    await loud.post(session, ch.id, f"Остров, где богачи смотрели, как бедняки умирают в детских играх, взят этой ночью. "
                                    f"За штурмом стоит {loud.who(ch.state.name, ch.state.surname)}. Игры остановлены.")



# -- games of the players (owner request 24.10: a rich player or a corp owner runs their own game) ---------------------

HOST_KEY = "dgame_host"   # world_state: {tg, name, title, until, earned}
INVITE = "dginv"          # payload["offer"] of an invitation


def _mail(session: AsyncSession, tg: int, text: str, eddies: int = 0) -> None:
    from app.db.models import PlayerMail

    payload: dict[str, Any] = {"text": text}
    if eddies:
        payload["eddies"] = eddies
    session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name="Остров", kind="city", payload=payload))


async def host_active(session: AsyncSession) -> dict | None:
    h = await repo.ws_get(session, HOST_KEY, None)
    if not isinstance(h, dict) or float(h.get("until", 0) or 0) < time.time():
        return None
    return dict(h)


async def _pay_host(session: AsyncSession, content: Content, run: dict[str, Any], amount: int, why: str) -> None:
    tg = run.get("host")
    if not tg or amount <= 0:
        return
    h = await host_active(session)
    if h and str(h.get("tg")) == str(tg):
        h["earned"] = int(h.get("earned", 0) or 0) + amount
        await repo.ws_set(session, HOST_KEY, h)
    _mail(session, int(tg), f"{why}: {amount} эдди.", amount)


async def can_host(session: AsyncSession, content: Content, ch: ActiveCharacter) -> str | None:
    from app.services import bigmoney

    if await closed(session):
        return "closed"
    h = await host_active(session)
    if h:
        return "mine" if str(h.get("tg")) == str(ch.player_tg_id) else "busy"
    rich = ch.state.eddies >= cfg(content).host_min_eddies + cfg(content).host_price
    owner = str(ch.player_tg_id) in await bigmoney.corps(session)
    if not (rich or owner):
        return "poor"
    if ch.state.eddies < cfg(content).host_price:
        return "eddies"
    return None


async def host(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               title: str) -> str | None:
    why = await can_host(session, content, ch)
    if why:
        return why
    c = cfg(content)
    st = ch.state
    apply_effects(st, {"eddies": -c.host_price, "heat": c.host_heat}, content, rng, year)
    await repo.ws_set(session, HOST_KEY, {"tg": str(ch.player_tg_id), "name": f"{st.name} {st.surname}".strip(),
                                          "title": title[:40], "until": time.time() + c.host_days * 86400, "earned": 0})
    await save_active(session, ch, tz)
    await loud.post(session, None, f"По городу ходят люди в идеальных костюмах и раздают визитки с тремя знаками. "
                                   f"Говорят, на острове новая игра{': «' + esc(title[:40]) + '»' if title else ''}. "
                                   "Кто её хозяин, никто не знает.")
    return None


async def invite(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, to_tg: int) -> str | None:
    from app.db.models import PlayerMail
    from app.services import game

    h = await host_active(session)
    if not h or str(h.get("tg")) != str(ch.player_tg_id):
        return "nohost"
    other = await game.get_active_character(session, int(to_tg))
    if other is None or other.player_tg_id == ch.player_tg_id:
        return "gone"
    if run_of(other) is not None or other.state.flags.get(FLAG_ENTER):
        return "inside"
    session.add(PlayerMail(to_tg_id=int(to_tg), from_tg_id=ch.player_tg_id, from_char_id=ch.id, to_char_id=other.id,
                           from_name="Человек в костюме", from_year=year, kind="offer",
                           payload={"offer": INVITE, "advance": cfg(content).invite_advance, "title": h.get("title", "")}))
    await session.flush()
    return None


async def answer_invite(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                        tz: str, mail_id: int, accept: bool) -> str:
    from app.db.models import PlayerMail
    from app.services.players import _utcnow

    m = await session.get(PlayerMail, mail_id)
    if m is None or m.to_tg_id != ch.player_tg_id or (m.payload or {}).get("offer") != INVITE or m.delivered_at is not None:
        return "Визитка уже недействительна."
    m.delivered_at = _utcnow()
    if not accept:
        await session.flush()
        return "Ты рвёшь визитку. Человек в костюме вежливо кивает и уходит."
    if await host_active(session) is None or await closed(session):
        await session.flush()
        return "Номер на визитке больше не отвечает."
    adv = int((m.payload or {}).get("advance", 0) or 0)
    if adv:
        apply_effects(ch.state, {"eddies": adv}, content, rng, year)
    ch.state.flags[FLAG_ENTER] = "invite"
    await save_active(session, ch, tz)
    await session.flush()
    return f"Ты звонишь по номеру. Аванс {adv} эдди уже на счету. За тобой приедут."
