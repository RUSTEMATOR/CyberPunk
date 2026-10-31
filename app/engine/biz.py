"""Owned businesses (owner request: several of them, each with its own name). Pure helpers over the flags.

flag `businesses` = [{id, kind, name, level, invested, ads_until, guard_until}]. The old single flag `business`
({kind, level, ...}) is read as a list of one and converted on the first save.
"""

from __future__ import annotations

from app.engine.state import CharacterState

FLAG, OLD = "businesses", "business"


def list_of(state: CharacterState) -> list[dict]:
    got = state.flags.get(FLAG)
    if isinstance(got, list):
        return [dict(b) for b in got if isinstance(b, dict)]
    old = state.flags.get(OLD)
    if isinstance(old, dict) and old.get("kind"):
        return [{**old, "id": 1, "name": old.get("name") or ""}]
    return []


def save(state: CharacterState, items: list[dict]) -> None:
    """Store the list; `business` mirrors the first one, so old readers (event conditions) keep working."""
    state.flags.pop(OLD, None)
    if items:
        state.flags[FLAG] = items
        state.flags[OLD] = dict(items[0])
    else:
        state.flags.pop(FLAG, None)


def find(state: CharacterState, biz_id: int) -> dict | None:
    return next((b for b in list_of(state) if int(b.get("id", 0)) == biz_id), None)


def next_id(items: list[dict]) -> int:
    return max((int(b.get("id", 0)) for b in items), default=0) + 1


def clean_name(text: str, limit: int = 32) -> str:
    """A name typed by the player: one line, no quotes or brackets that would break the layout."""
    name = " ".join(text.replace("«", "").replace("»", "").replace('"', "").replace("<", "").replace(">", "").split())
    return name[:limit].strip()


NOW = "_biz_now"      # the business an event's effects act on (set while a business story resolves)
FOCUS = "biz_focus"   # event id -> business id: whose story is queued


def focus_of(state: CharacterState, event_id: str) -> dict | None:
    fid = (state.flags.get(FOCUS) or {}).get(event_id)
    return find(state, int(fid)) if fid is not None else None


OPS = ("stock", "staff", "cond")
OPS_NAMES = {"stock": "запасы", "staff": "персонал", "cond": "состояние"}


def ops(b: dict, start: dict[str, int]) -> dict[str, int]:
    """Owner request 24.10: the three resources of a business (old businesses get the start values)."""
    return {k: max(0, min(100, int(b.get(k, start.get(k, 70)) if b.get(k) is not None else start.get(k, 70)))) for k in OPS}


def apply_effect(state: CharacterState, base: str, val: int | bool, max_level: int, max_owned: int) -> str | None:
    """`biz_level`, `biz_boost`, `biz_slump`, `biz_lost`, `biz_chain` on the business of the story (else the first).
    Returns a line for the player or None."""
    items = list_of(state)
    if not items:
        return None
    bid = state.flags.get(NOW)
    b = next((x for x in items if int(x.get("id", 0)) == int(bid)), None) if bid is not None else None
    b = b or items[0]
    nm = f"«{b['name']}»" if b.get("name") else "заведение"
    now = state.weeks_lived
    out = None
    if base == "biz_level":
        old = int(b.get("level", 1))
        b["level"] = max(1, min(max_level, old + int(val)))
        if b["level"] != old:
            out = f"{nm}: уровень {old} → {b['level']}."
    elif base == "biz_boost":
        b["boost_until"] = max(int(b.get("boost_until", 0)), now) + int(val)
        out = f"{nm}: полоса прибыли, {int(val)} нед."
    elif base == "biz_slump":
        b["slump_until"] = max(int(b.get("slump_until", 0)), now) + int(val)
        out = f"{nm}: спад, {int(val)} нед."
    elif base == "biz_lost":
        items = [x for x in items if x is not b]
        out = f"{nm} больше не твоё."
    elif base in ("biz_stock", "biz_staff", "biz_cond"):
        key = base[4:]
        old = int(b.get(key, 70) if b.get(key) is not None else 70)
        b[key] = max(0, min(100, old + int(val)))
        out = f"{nm}: {OPS_NAMES[key]} {old} → {b[key]}."
    elif base == "biz_chain":
        if len(items) < max_owned:
            copy = {k: v for k, v in b.items() if k not in ("id", "scheme", "visit", "fame")}
            copy.update({"id": next_id(items), "level": 1, "invested": int(b.get("invested", 0)) // max(1, int(b.get("level", 1))),
                         "name": (str(b.get("name") or "") + " 2").strip() if b.get("name") else ""})
            items.append(copy)
            out = f"Сеть растёт: второе заведение {nm}."
        else:
            b["level"] = min(max_level, int(b.get("level", 1)) + 1)
            out = f"{nm}: больше заведений не потянуть, зато уровень {b['level']}."
    save(state, items)
    return out
