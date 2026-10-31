"""The maniac mode (owner request), the service side: the registry other players see, the clues, the reveal.

world_state `maniacs` = {char_id: {nick, tg, name, clues, revealed}}
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.engine import maniac as me
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

KEY = "maniacs"
R_GONE, R_AP, R_DONE = "gone", "ap", "done"


async def registry(session: AsyncSession) -> dict:
    got = await repo.ws_get(session, KEY, {})
    return dict(got) if isinstance(got, dict) else {}


def _name(ch: ActiveCharacter) -> str:
    return f"{ch.state.name} {ch.state.surname}".strip()


async def after_kill(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random) -> list[str]:
    """A civilian died by the character's hand: the mode begins or grows. Returns lines for the player."""
    from app.services import loud

    became, grew = me.on_kill(ch.state, content, rng)
    r = me.rec(ch.state)
    if not r:
        return []
    reg = await registry(session)
    entry = dict(reg.get(str(ch.id), {}))
    entry.update({"nick": r["nick"], "tg": ch.player_tg_id, "name": _name(ch), "kills": r.get("kills", 0)})
    entry.setdefault("clues", 0)
    entry.setdefault("revealed", False)
    reg[str(ch.id)] = entry
    await repo.ws_set(session, KEY, reg)
    lines: list[str] = []
    if became:
        await loud.post(session, None, f"В городе орудует маньяк. Газеты уже придумали прозвище: «{r['nick']}».")
        lines.append(f"🔪 Ты больше не просто убийца. Город называет тебя «{r['nick']}». Никто не знает, кто ты. Пока.")
    if grew:
        await loud.post(session, None, f"«{r['nick']}» снова убивает. Теперь это {grew.lower()}.")
        lines.append(f"🔪 «{r['nick']}» — {grew.lower()}. Сила растёт, люди сторонятся.")
    return lines


async def press_hook(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     week0: int) -> list[str]:
    """After a press: the thirst, a clue of the NCPD, quitting. `week0` = weeks_lived at the start of the press."""
    r = me.rec(ch.state)
    if not r:
        return []
    c = me.cfg(content)
    lines: list[str] = []
    if me.quit_done(ch.state, content):
        ch.state.flags.pop(me.FLAG, None)
        reg = await registry(session)
        reg.pop(str(ch.id), None)
        await repo.ws_set(session, KEY, reg)
        return [f"«{r['nick']}» исчезает из новостей. Руки больше не дрожат по ночам. Почти."]
    if me.thirsty(ch.state, content):
        apply_effects(ch.state, {"mood": -c.thirst_mood}, content, rng, year)
        lines.append(f"🔪 Жажда. Давно никого. Настроение −{c.thirst_mood}." + (" Ты держишься." if r.get("quit") else ""))
    if int(r.get("last_kill", -1)) >= week0 and rng.random() < c.clue_chance:
        lines += await _clue(session, content, ch.id, "NCPD находит улику на месте последнего убийства.")
    return lines


async def _clue(session: AsyncSession, content: Content, char_id: int, why: str) -> list[str]:
    from app.services import loud

    reg = await registry(session)
    e = reg.get(str(char_id))
    if not e or e.get("revealed"):
        return []
    e["clues"] = int(e.get("clues", 0)) + 1
    out = [why + f" Улик: {e['clues']}/{me.cfg(content).reveal_clues}."]
    if e["clues"] >= me.cfg(content).reveal_clues:
        e["revealed"] = True
        await loud.post(session, None, f"NCPD раскрывает «{e['nick']}»: это {e['name']}. Награда за голову объявлена.")
        out.append(f"«{e['nick']}» раскрыт: это {e['name']}.")
        from app.db.models import PlayerMail

        session.add(PlayerMail(to_tg_id=int(e["tg"]), from_tg_id=0, from_name="NCPD", kind="city", to_char_id=char_id,
                               payload={"text": f"Твоё лицо на всех экранах: «{e['nick']}» раскрыт. Розыск на максимуме.",
                                        "heat": 5}))
    reg[str(char_id)] = e
    await repo.ws_set(session, KEY, reg)
    return out


@dataclass
class InvestigateResult:
    blocked: str | None = None
    ok: bool = False
    check: object | None = None
    lines: list[str] = field(default_factory=list)
    applied: AppliedEffects | None = None
    revealed: bool = False


async def investigate(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                      tz: str, char_id: int) -> InvestigateResult:
    from app.engine import checks

    c = me.cfg(content)
    reg = await registry(session)
    e = reg.get(str(char_id))
    if not e or e.get("revealed"):
        return InvestigateResult(blocked=R_GONE)
    if not ap_services.can_spend(ch, c.investigate_ap):
        return InvestigateResult(blocked=R_AP)
    ap_services.spend(ch, content, c.investigate_ap)
    res = InvestigateResult()
    if char_id == ch.id:  # the maniac "investigates" himself: a clue disappears
        e["clues"] = max(0, int(e.get("clues", 0)) - 1)
        reg[str(char_id)] = e
        await repo.ws_set(session, KEY, reg)
        await save_active(session, ch, tz)
        res.ok = True
        res.lines.append(f"Ты помогаешь следствию так, что одна улика пропадает. Улик: {e['clues']}/{c.reveal_clues}.")
        return res
    chk = checks.roll_check(ch.state, content, rng, "INT", "persuasion", c.investigate_dv, ("social",))
    res.check, res.ok = chk, chk.ok
    if chk.ok:
        res.lines += await _clue(session, content, char_id, "Ты находишь свидетеля, который кое-что видел.")
        reg = await registry(session)
        if reg.get(str(char_id), {}).get("revealed"):
            res.revealed = True
            res.applied = apply_effects(ch.state, {"eddies": c.investigate_reward, "fame": 1}, content, rng, year)
            res.lines.append(f"Награда за раскрытие: +{c.investigate_reward} эдди.")
    else:
        res.lines.append("Свидетели молчат. След остывает.")
    await save_active(session, ch, tz)
    return res


async def quit_mode(session: AsyncSession, ch: ActiveCharacter, tz: str) -> None:
    r = me.rec(ch.state)
    if r:
        r["quit"] = True
        ch.state.flags[me.FLAG] = r
        await save_active(session, ch, tz)
