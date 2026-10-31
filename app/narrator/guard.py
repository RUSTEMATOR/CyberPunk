"""Checks on what Claude wrote (architecture rule 4: the narrator never changes the numbers).

- `clean` removes what a chat window must not show: markdown, emoji, angle brackets (the callers escape HTML anyway).
- `fit` makes a text fit its limit: it cuts at the last sentence end, and gives up when too little would be left.
- `foreign_numbers` finds digits that are not in the payload. A text with an invented figure is thrown away and the
  fallback text is used: the payload is the only source of numbers.
- `extract_json` takes the first JSON object out of an answer, with or without a code fence.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

# Emoji and pictographs (BMP symbols block, the astral pictograph planes, variation selectors, joiners).
_EMOJI = re.compile(
    "[←-⇿⌀-⏿①-⓿■-➿⤀-⥿⬀-⯿〰〽㊗㊙"
    "︎️‍\U0001f000-\U0001faff\U0001f1e6-\U0001f1ff]"
)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")
_MD_MARKS = re.compile(r"(\*\*|__|`+|~~)")
_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_BULLET = re.compile(r"(?m)^\s*[-*•]\s+")
_NUMBER = re.compile(r"(?<![A-Za-z\d])\d+(?:[ \u00a0\u202f]\d{3}(?!\d))*(?:[.,]\d+)?(?![A-Za-z])")
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_SENTENCE_END = re.compile(r"[.!?…»”)]\s|[.!?…»”)]$")


def clean(raw: str) -> str:
    """Plain text for a window: no markdown, no emoji, no angle brackets, single blank lines."""
    text = (raw or "").replace("\r", "").strip()
    text = _FENCE.sub("", text).strip()
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _MD_MARKS.sub("", text)
    text = _EMOJI.sub("", text)
    text = _TAG.sub("", text).replace("<", "").replace(">", "")
    if len(text) > 2 and text[0] == '"' and text[-1] == '"' and '"' not in text[1:-1]:
        text = text[1:-1].strip()
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def fit(text: str, limit: int, min_keep: float) -> str | None:
    """The text itself when it fits, else its beginning up to the last whole sentence. None when the cut would
    drop more than `1 - min_keep` of the text (a half sentence is worse than a ready-made fallback)."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    best = -1
    for m in _SENTENCE_END.finditer(head):
        best = m.start() + 1
    if best >= int(limit * min_keep) and best > 0:
        return head[:best].rstrip()
    cut = head.rsplit(" ", 1)[0].rstrip(" ,;:-")
    if len(cut) >= int(limit * min_keep) and cut:
        return cut + "…"
    return None


def _normalize(token: str) -> str:
    t = re.sub(r"[   ]", "", token).replace(",", ".")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t.lstrip("0") or "0"


def numbers_in(text: str) -> set[str]:
    """Every number written with digits in the text, normalised (spaces in thousands and trailing zeros dropped).
    A thousands group like '1 200' counts as the number 1200 and as 1 and 200 would not: it is read greedily."""
    return {_normalize(m.group(0)) for m in _NUMBER.finditer(text or "")}


def payload_numbers(*payloads: Any) -> set[str]:
    """Numbers the narrator may write: every number inside the payload(s), in numbers and in strings."""
    allowed: set[str] = set()
    for payload in payloads:
        raw = json.dumps(payload, ensure_ascii=False, default=str)
        allowed |= numbers_in(raw)
        for m in re.finditer(r"\d+", raw):  # a figure inside a longer token (H10, 2077-го) stays usable on its own
            allowed.add(_normalize(m.group(0)))
    return allowed


def foreign_numbers(text: str, allowed: Iterable[str]) -> list[str]:
    ok = set(allowed)
    return sorted(n for n in numbers_in(text) if n not in ok)


def extract_json(text: str) -> dict[str, Any] | None:
    """The first JSON object of an answer, or None."""
    body = _FENCE.sub("", (text or "").strip()).strip()
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None
