"""Libria, the city where feeling is a crime (owner request 23.10: the «Equilibrium» crossover). The service side.

Prozium: a course of grey calm (no grief, no joy, chrome hurts the soul half as much) and a hard week after it.
The Grammaton Clerics: an exam of the gun kata, raids on the sense offenders, the art you burn or hide.
The Underground: smuggling art and music into the city. A cleric who skips a dose may turn against Tetragrammaton.
Boolean flags `libria_visit` / `libria_cleric` / `libria_rebel` serve the event conditions (content/events_libria.yaml).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.engine import checks
from app.engine import libria as le
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active


@dataclass
class LibriaResult:
    blocked: str | None = None
    ok: bool = True
    check: object | None = None
    text: str = ""
    lines: list[str] = field(default_factory=list)
    applied: AppliedEffects | None = None
    choice: bool = False  # a raid found art: burn or keep


def _jailed(ch: ActiveCharacter) -> bool:
    return int(ch.state.flags.get("jail_weeks", 0) or 0) > 0


def _visit(ch: ActiveCharacter) -> None:
    ch.state.flags["libria_visit"] = True


def _flatten(ch: ActiveCharacter, content: Content) -> dict:
    """The mood delta that pulls the mood to Prozium's grey middle."""
    d = le.cfg(content).flat_mood - ch.state.mood
    return {"mood": d} if d else {}


async def dose(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
               tz: str) -> LibriaResult:
    c = le.cfg(content)
    if ch.state.eddies < c.dose_price:
        return LibriaResult(blocked="eddies")
    start = max(ch.state.weeks_lived, int(le.rec(ch.state).get("dose_until", -1)) + 1)
    le.set_rec(ch.state, dose_until=start + c.course_weeks - 1, dosed=True)
    _visit(ch)
    res = LibriaResult(text="Щелчок инжектора в шею. Мир становится ровным и серым, как бетон. Ничего не болит. "
                            "Ничего не радует. Это и есть покой.")
    res.applied = apply_effects(ch.state, {"eddies": -c.dose_price, **_flatten(ch, content)}, content, rng, year)
    await save_active(session, ch, tz)
    return res


async def exam(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
               tz: str) -> LibriaResult:
    """The exam of the gun kata: REF + firearms. Success: a Grammaton Cleric."""
    c = le.cfg(content)
    if le.cleric(ch.state):
        return LibriaResult(blocked="have")
    if _jailed(ch):
        return LibriaResult(blocked="jail")
    if not le.on_dose(ch.state):
        return LibriaResult(blocked="nodose")
    chk = checks.roll_check(ch.state, content, rng, "REF", "firearms", c.cleric_dv, ("combat",))
    _visit(ch)
    res = LibriaResult(ok=chk.ok, check=chk)
    if chk.ok:
        le.set_rec(ch.state, cleric=True)
        ch.state.flags["libria_cleric"] = True
        res.text = ("Сорок секунд в тёмном зале, двадцать мишеней, ни одного лишнего движения. Экзаменатор кивает без "
                    "выражения. Тебе выдают чёрный плащ и два пистолета. Ты — клирик Тетраграмматона.")
        res.applied = apply_effects(ch.state, {"status": 1}, content, rng, year)
    else:
        res.text = "Мишень номер семь остаётся целой. Экзаменатор пишет одно слово: «эмоционален». Приходи в следующий раз."
    await save_active(session, ch, tz)
    return res


async def raid(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
               tz: str) -> LibriaResult:
    """A cleric's raid on the sense offenders: REF + firearms. Success: pay, and the art found waits for a choice."""
    c = le.cfg(content)
    if not le.cleric(ch.state):
        return LibriaResult(blocked="notcleric")
    if _jailed(ch):
        return LibriaResult(blocked="jail")
    if le.rec(ch.state).get("pending_art"):
        return LibriaResult(choice=True)
    if not ap_services.can_spend(ch, c.raid_ap):
        return LibriaResult(blocked="ap")
    ap_services.spend(ch, content, c.raid_ap)
    chk = checks.roll_check(ch.state, content, rng, "REF", "firearms", c.raid_dv, ("combat",))
    res = LibriaResult(ok=chk.ok, check=chk)
    if chk.ok:
        pay = rng.randint(*c.raid_pay)
        found = rng.choice(["картину маслом в раме", "виниловую пластинку и проигрыватель", "томик стихов с пометками",
                            "музыкальную шкатулку", "фарфоровую вазу", "детский рисунок из-под половицы"])
        le.set_rec(ch.state, pending_art=found)
        res.text = (f"Дверь вылетает с петель. Нарушители чувств — семья из трёх человек — стоят у стены. "
                    f"За фальшпанелью ты находишь {found}. Отряд ждёт приказа: сжечь.")
        res.applied = apply_effects(ch.state, {"eddies": pay}, content, rng, year)
        res.choice = True
    else:
        res.text = "Нарушители ушли через крышу за минуту до штурма. Кто-то их предупредил. Отряд смотрит на тебя."
        res.applied = apply_effects(ch.state, {"hp": -c.raid_fail_hp}, content, rng, year)
    await save_active(session, ch, tz)
    return res


async def art_choice(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, keep: bool) -> LibriaResult:
    c = le.cfg(content)
    found = le.rec(ch.state).get("pending_art")
    if not found:
        return LibriaResult(blocked="gone")
    le.set_rec(ch.state, pending_art=None)
    res = LibriaResult()
    if keep:
        le.set_rec(ch.state, art=le.art(ch.state) + 1)
        res.text = (f"Ты докладываешь, что всё сожжено, а находку уносишь под плащом: {found}. Дома прячешь за вентиляцией. "
                    "Ночью смотришь на это и не понимаешь, что с тобой происходит.")
        res.applied = apply_effects(ch.state, {"humanity": c.keep_humanity, "mood": 3}, content, rng, year)
    else:
        res.text = f"Ты отдаёшь приказ, и огнемёт сжигает {found}. Ровно, как всё остальное. Премия за образцовую чистку."
        res.applied = apply_effects(ch.state, {"eddies": c.burn_bonus, "humanity": c.burn_humanity}, content, rng, year)
    await save_active(session, ch, tz)
    return res


async def smuggle(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  tz: str) -> LibriaResult:
    """The Underground: carry art and music past the checkpoints. COOL + stealth."""
    c = le.cfg(content)
    if _jailed(ch):
        return LibriaResult(blocked="jail")
    if not ap_services.can_spend(ch, c.smuggle_ap):
        return LibriaResult(blocked="ap")
    ap_services.spend(ch, content, c.smuggle_ap)
    _visit(ch)
    chk = checks.roll_check(ch.state, content, rng, "COOL", "stealth", c.smuggle_dv, ("gig_quiet",))
    res = LibriaResult(ok=chk.ok, check=chk)
    if chk.ok:
        res.text = ("Ящик с пластинками проходит через КПП под видом фильтров для воды. Ночью в подвале Подполья "
                    "впервые за двадцать лет играет музыка. Люди плачут и не знают, что с этим делать.")
        res.applied = apply_effects(ch.state, {"eddies": rng.randint(*c.smuggle_pay), "humanity": 1,
                                               "street_cred": 1}, content, rng, year)
        ch.state.flags["libria_rebel"] = True
    else:
        res.text = "Сканер на КПП пищит. Ящик вскрывают, тебя бьют прикладом и вышвыривают в Пустоши за стеной."
        res.applied = apply_effects(ch.state, {"hp": -c.smuggle_fail_hp, "heat": c.smuggle_fail_heat},
                                    content, rng, year)
    await save_active(session, ch, tz)
    return res


async def rebel(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str) -> LibriaResult:
    """A cleric off Prozium turns on Tetragrammaton: the gun kata against the partner. Ends the cleric's service."""
    from app.services import loud

    c = le.cfg(content)
    if not le.cleric(ch.state):
        return LibriaResult(blocked="notcleric")
    if le.on_dose(ch.state):
        return LibriaResult(blocked="dosed")
    if _jailed(ch):
        return LibriaResult(blocked="jail")
    chk = checks.roll_check(ch.state, content, rng, "REF", "firearms", c.skip_dv, ("combat",))
    res = LibriaResult(ok=chk.ok, check=chk)
    le.set_rec(ch.state, cleric=False)
    ch.state.flags.pop("libria_cleric", None)
    if chk.ok:
        res.text = ("Напарник замечает пропущенную дозу и тянется к пистолету. Ган-ката длится три секунды. "
                    "Потом ты идёшь по коридорам Тетраграмматона, и каждый, кто встаёт на пути, падает. Трансляция "
                    "Отца гаснет на всех экранах Либрии. Город впервые слышит собственную тишину.")
        res.applied = apply_effects(ch.state, dict(c.skip_win), content, rng, year)
        ch.state.flags["libria_rebel"] = True
        await loud.post(session, ch.id, f"{loud.who(ch.state.name, ch.state.surname)} отказывается от Прозиума и "
                                        "гасит трансляцию Отца. В Либрии впервые за двадцать лет играет музыка.")
        await repo.log_event(session, "libria_rebel", {}, character_id=ch.id)
    else:
        res.text = ("Напарник быстрее. Пуля в плечо, падение с лестницы, побег через канализацию в Пустоши. "
                    "Плащ клирика остаётся в грязи. Обратной дороги нет.")
        hurt = max(0, min(c.skip_fail_hp, ch.state.hp - 1))
        res.applied = apply_effects(ch.state, {"hp": -hurt, "heat": 2}, content, rng, year)
    await save_active(session, ch, tz)
    return res


def press_lines(ch: ActiveCharacter, content: Content, rng: random.Random, year: int) -> list[str]:
    """After a press: Prozium keeps the mood grey or ends with a hard week; hidden art may be found."""
    c = le.cfg(content)
    st = ch.state
    out: list[str] = []
    r = le.rec(st)
    if not r:
        return out
    if le.on_dose(st):
        fx = _flatten(ch, content)
        if fx:
            apply_effects(st, fx, content, rng, year)
        out.append(f"💊 Прозиум: ещё {le.dose_left(st)} нед. ровного серого покоя.")
    elif r.get("dosed"):
        le.set_rec(st, dosed=False)
        apply_effects(st, {"mood": c.withdraw_mood, "humanity": c.withdraw_humanity}, content, rng, year)
        out.append("💊 Прозиум кончился. Чувства возвращаются все разом: цвет, музыка за стеной, старая боль. "
                   f"Настроение {str(c.withdraw_mood).replace('-', '−')}, человечность +{c.withdraw_humanity}.")
    n = le.art(st)
    if n and le.cleric(st) and rng.random() < min(0.6, n * c.found_chance_per_art):
        le.set_rec(st, art=0, cleric=False)
        st.flags.pop("libria_cleric", None)
        hurt = max(0, min(c.found_hp, st.hp - 1))
        apply_effects(st, {"heat": c.found_heat, "hp": -hurt}, content, rng, year)
        out.append("🔥 Обыск в твоей квартире. Находят всё спрятанное за вентиляцией. Плащ клирика срывают при "
                   "всех, тебя избивают и выбрасывают из Тетраграмматона.")
    return out
