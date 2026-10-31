"""Small games started by a word in the chat (owner request): the slot machine and the fishing. Pure functions."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from app.content.loader import Content
from app.content.schemas import FishCatch, FishingRod, SlotsBalance

# The Telegram 🎰 dice: value 1..64, value - 1 = reel0 + 4 * reel1 + 16 * reel2 with symbols in this order.
REEL_SYMBOLS = ("BAR", "🍇", "🍋", "7️⃣")
SEVEN = 3


def slot_reels(value: int) -> tuple[int, int, int]:
    v = max(1, min(64, int(value))) - 1
    return v & 3, (v >> 2) & 3, (v >> 4) & 3


def slot_mult(reels: tuple[int, int, int], cfg: SlotsBalance) -> int:
    """The payout multiplier of a spin (0 = the stake is lost)."""
    if reels == (SEVEN, SEVEN, SEVEN):
        return cfg.jackpot_mult
    if reels[0] == reels[1] == reels[2]:
        return cfg.triple_mult
    if sum(1 for r in reels if r == SEVEN) == 2:
        return cfg.two_sevens_mult
    return 0


def slot_line(reels: tuple[int, int, int]) -> str:
    return " | ".join(REEL_SYMBOLS[r] for r in reels)


def _word_re(words: list[str]) -> re.Pattern[str]:
    return re.compile(r"(?<![\w])(" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")(?![\w])",
                      re.IGNORECASE)


def match_word(text: str, words: list[str], max_chars: int | None = None) -> bool:
    if not text or (max_chars is not None and len(text) > max_chars):
        return False
    return _word_re(words).search(text) is not None


def stake_from(text: str, cfg: SlotsBalance) -> int:
    """The number in the message is the stake (clamped); none = the default stake."""
    m = re.search(r"\d[\d\s]*", text or "")
    if not m:
        return cfg.stake_default
    try:
        n = int(m.group(0).replace(" ", ""))
    except ValueError:
        return cfg.stake_default
    return max(cfg.stake_min, min(cfg.stake_max, n))


# -- fishing ------------------------------------------------------------------------------------------------------------


def rods_for(content: Content, year: int) -> list[FishingRod]:
    return sorted((r for r in content.balance.chat_games.fishing.rods
                   if (r.available_from is None or r.available_from <= year)
                   and (r.available_until is None or year <= r.available_until)), key=lambda r: r.tier)


def rod(content: Content, key: str | None) -> FishingRod | None:
    return next((r for r in content.balance.chat_games.fishing.rods if r.key == key), None)


def catch_weights(content: Content, tier: int, district: str) -> list[tuple[FishCatch, float]]:
    cfg = content.balance.chat_games.fishing
    out: list[tuple[FishCatch, float]] = []
    for fc in cfg.catches:
        if fc.min_tier > tier:
            continue
        w = fc.weight
        if fc.kind == "junk" and fc.rarity == 0:
            w /= 1 + cfg.junk_div_per_tier * (tier - 1)
        w *= 1 + cfg.rare_mult_per_tier * (tier - 1) * fc.rarity
        if district == cfg.harbor_district and fc.rarity >= 2:
            w *= cfg.harbor_rare_mult
        if district in cfg.dry_districts and fc.kind == "fish":
            w *= cfg.dry_fish_mult
        out.append((fc, w))
    return out


def roll_catch(content: Content, rng: random.Random, tier: int, district: str) -> FishCatch:
    pool = catch_weights(content, tier, district)
    return rng.choices([fc for fc, _ in pool], weights=[w for _, w in pool])[0]


@dataclass
class SaleLine:
    name: str
    count: int
    price: int


def sale(content: Content, keepnet: dict[str, int], district: str) -> tuple[int, list[SaleLine]]:
    """What the keepnet sells for (the harbour market pays more)."""
    cfg = content.balance.chat_games.fishing
    by_key = {fc.key: fc for fc in cfg.catches}
    pct = cfg.harbor_sell_pct if district == cfg.harbor_district else 0
    lines: list[SaleLine] = []
    total = 0
    for key, n in keepnet.items():
        fc = by_key.get(key)
        if fc is None or n <= 0:
            continue
        price = fc.price * (100 + pct) // 100
        lines.append(SaleLine(fc.name, int(n), price))
        total += price * int(n)
    return total, lines
