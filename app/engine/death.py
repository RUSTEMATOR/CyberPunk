"""Zero hit points (PLAN 7.1, DESIGN.md 6 "Здоровье 0"): what stands between a character and death.

Order, as in the design: the Trauma Team subscription (a chance by tier, the evacuation costs money, a missing sum
becomes a debt) -> the Second Heart (one revival, the implant burns out) -> the death save `d10 < BODY - penalty`
-> the glitch offer (a failed save may be rolled again by spending a glitch, once per life) -> death.

`resolve_zero_hp` changes the state in place through `apply_effects` (architecture rule 3) and returns what happened;
the service writes the texts, the log and the graveyard. Randomness only through `rng`. The glitch offer is a decision
of the player, so the function stops at it (`OFFER`) and `accept_glitch` plays the second roll.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.content.loader import Content
from app.engine import derived
from app.engine.dice import half_up
from app.engine.effects import apply_effects, apply_remove_cyberware
from app.engine.state import FLAG_JAIL_WEEKS, FLAG_TRAUMA_TEAM, CharacterState

# Ways a zero-hp moment ends.
EVACUATED = "evacuated"  # Trauma Team came
SECOND_HEART = "second_heart"  # the spare heart started
SAVED = "saved"  # the death save passed
OFFER = "offer"  # the save failed, a glitch may roll it again: the player decides
DEAD = "dead"

# `ZeroHpResult.how` of a save that passed after the glitch.
SAVED_BY_GLITCH = "saved_glitch"

FLAG_DEATH_SAVES = "death_saves"  # [weeks_lived, n]: the saves already rolled in this week (the "encounter")


@dataclass
class SaveRoll:
    roll: int
    need: int  # the save passes when roll < need
    ok: bool
    penalty: int


@dataclass
class ZeroHpResult:
    outcome: str  # EVACUATED | SECOND_HEART | SAVED | OFFER | DEAD
    how: str = ""  # SAVED_BY_GLITCH when the glitch saved it
    tier: str | None = None  # the Trauma Team tier that was tried (rescued or not)
    tier_failed: bool = False  # the subscription was there, the crew did not make it
    evac_price: int = 0
    heart: str | None = None  # the implant that burned out (Second Heart)
    save: SaveRoll | None = None  # the last death save
    first_save: SaveRoll | None = None  # the save before the glitch (when the glitch was spent)
    glitch_spent: bool = False
    repeats: int = 0  # saves already rolled in this encounter before this one

    @property
    def survived(self) -> bool:
        return self.outcome in (EVACUATED, SECOND_HEART, SAVED)


# -- pieces ---------------------------------------------------------------------------------------------------


def saves_this_week(state: CharacterState) -> int:
    """Death saves already rolled in the current lived week (the "same encounter": each next one is harder)."""
    raw = state.flags.get(FLAG_DEATH_SAVES)
    if isinstance(raw, list) and len(raw) == 2 and int(raw[0]) == state.weeks_lived:
        return int(raw[1])
    return 0


def _count_save(state: CharacterState) -> None:
    state.flags[FLAG_DEATH_SAVES] = [state.weeks_lived, saves_this_week(state) + 1]


def trauma_tier(state: CharacterState, content: Content, year: int | None = None) -> str | None:
    """The subscription tier that can come now: the flag, the company exists in `year`, not in a cell unless allowed."""
    from app.engine.property import trauma_offered  # local: property imports derived, as this module does

    tier = state.flags.get(FLAG_TRAUMA_TEAM)
    if not isinstance(tier, str) or tier not in content.balance.trauma_team:
        return None
    if year is not None and not trauma_offered(content, year):
        return None
    if int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0 and not content.balance.death.evac_in_jail:
        return None
    return tier


def heart_key(state: CharacterState, content: Content) -> str | None:
    """An installed implant that revives its owner once (content flag `resurrection`: the Second Heart)."""
    for item in state.cyberware:
        cw = content.cyberware.get(item.key)
        if cw is not None and cw.resurrection:
            return item.key
    return None


def revive_hp(state: CharacterState, content: Content) -> int:
    """hp after an evacuation or the Second Heart: `death.evac_hp_pct` of the maximum, at least the survive hp."""
    top = derived.max_hp(state, content)
    return max(content.balance.death.save_survive_hp, half_up(top * content.balance.death.evac_hp_pct))


def roll_save(state: CharacterState, content: Content, rng: random.Random, repeats: int) -> SaveRoll:
    """The death save: `d(save_die) < BODY - repeats * save_penalty_per_repeat`."""
    cfg = content.balance.death
    penalty = max(0, repeats) * cfg.save_penalty_per_repeat
    need = derived.stat_total(state, content, "BODY") - penalty
    roll = rng.randint(1, cfg.save_die)
    return SaveRoll(roll, need, roll < need, penalty)


def glitch_offer_available(state: CharacterState, content: Content) -> bool:
    """A glitch may roll the failed save again: once per life (`luck.death_glitch_uses_per_life`)."""
    uses = 1 if state.death_glitch_used else 0
    return state.glitches >= 1 and uses < content.balance.luck.death_glitch_uses_per_life


def _heal_to(state: CharacterState, content: Content, rng: random.Random, year: int, hp: int) -> None:
    if state.hp < hp:
        apply_effects(state, {"hp": hp - state.hp}, content, rng, year)


# -- the order --------------------------------------------------------------------------------------------------


def resolve_zero_hp(
    state: CharacterState, rng: random.Random, content: Content, encounter_penalty: int | None = None,
    *, year: int | None = None, allow_evac: bool = True,
) -> ZeroHpResult:
    """The character is at 0 hp. `encounter_penalty` = death saves already rolled in this encounter (None: read the
    count of the current week from the state). `year` is the world year (the Trauma Team may not exist yet or any more);
    `allow_evac` False skips the crew and the heart (a death sentence that no medic can stop).
    Returns the outcome; the state already holds the survivor's hp, the paid evacuation or the burnt heart.
    """
    cfg = content.balance.death
    year_for_effects = year if year is not None else 0
    repeats = saves_this_week(state) if encounter_penalty is None else encounter_penalty
    res = ZeroHpResult(DEAD, repeats=repeats)

    # 1. Trauma Team
    tier = trauma_tier(state, content, year) if allow_evac else None
    if tier is not None:
        entry = content.balance.trauma_team[tier]
        res.tier = tier
        if rng.random() < entry.rescue_chance:
            res.outcome, res.evac_price = EVACUATED, entry.evac_price
            apply_effects(state, {"eddies": -entry.evac_price}, content, rng, year_for_effects)  # a missing sum is a debt
            _heal_to(state, content, rng, year_for_effects, revive_hp(state, content))
            return res
        res.tier_failed = True

    # 2. Second Heart: one revival, then the implant is gone (no humanity comes back)
    key = heart_key(state, content) if allow_evac else None
    if key is not None:
        item = state.cyber_item(key)
        assert item is not None
        apply_remove_cyberware(state, content, key, rng, year_for_effects, refund=False)
        res.outcome, res.heart = SECOND_HEART, key
        _heal_to(state, content, rng, year_for_effects, revive_hp(state, content))
        return res

    # 3. the death save
    return _save_or_offer(state, rng, content, res, year_for_effects)


def _save_or_offer(
    state: CharacterState, rng: random.Random, content: Content, res: ZeroHpResult, year: int,
) -> ZeroHpResult:
    cfg = content.balance.death
    save = roll_save(state, content, rng, res.repeats)
    _count_save(state)
    res.save = save
    if save.ok:
        res.outcome = SAVED
        _heal_to(state, content, rng, year, cfg.save_survive_hp)
        return res
    # 4. a glitch may roll it again (the player decides), otherwise the end
    res.outcome = OFFER if glitch_offer_available(state, content) else DEAD
    return res


def accept_glitch(
    state: CharacterState, rng: random.Random, content: Content, repeats: int, *, year: int | None = None,
) -> ZeroHpResult:
    """The player spends a glitch on the failed save: the glitch is gone, the glitch of this life is used up, the
    save is rolled again with the same penalty. A second failure is death (there is no second offer)."""
    year_for_effects = year if year is not None else 0
    apply_effects(state, {"glitches": -1}, content, random.Random(0), year_for_effects)
    state.death_glitch_used = True
    res = ZeroHpResult(DEAD, repeats=repeats, glitch_spent=True)
    save = roll_save(state, content, rng, repeats)
    res.save = save
    if save.ok:
        res.outcome, res.how = SAVED, SAVED_BY_GLITCH
        _heal_to(state, content, rng, year_for_effects, content.balance.death.save_survive_hp)
    return res
