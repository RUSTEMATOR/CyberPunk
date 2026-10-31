"""A text grammar in the spirit of Tracery (owner request 26.10, docs/PLAN_LIVING.md stage 3). Pure.

Rules live in balance.grammar: {rule: [variants]}. A variant may hold `#other_rule#` (expanded recursively, up to a
depth) and `{name}` fields filled from the call. So the same news reads differently every time.
"""

from __future__ import annotations

import random
import re
from typing import Any

from app.content.loader import Content

_REF = re.compile(r"#([a-z_0-9]+)#")
MAX_DEPTH = 6


def expand(rules: dict[str, list[str]], text: str, rng: random.Random, depth: int = 0) -> str:
    if depth >= MAX_DEPTH:
        return _REF.sub("", text)

    def one(m: re.Match[str]) -> str:
        pool = rules.get(m.group(1))
        return expand(rules, rng.choice(pool), rng, depth + 1) if pool else ""

    return _REF.sub(one, text)


def say(content: Content, rng: random.Random, rule: str, **fields: Any) -> str:
    """Expand a root rule and fill its fields; a missing rule gives ""."""
    rules = content.balance.grammar
    if rule not in rules:
        return ""
    text = expand(rules, f"#{rule}#", rng)
    try:
        return text.format(**fields)
    except (KeyError, IndexError, ValueError):
        return text
