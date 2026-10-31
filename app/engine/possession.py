"""An AI in the head (owner request 26.10 «одержимость ИИшкой», like Raven Tearz and «она», like the engram of the
2077 story). Pure.

flag `possessed` = {"src" (raven | shard), "name", "sync" 0..100, "trust" 0..100, "since" (weeks lived)}.
- The AI gives power by the sync tier (balance.possession.tiers: mods), talks in the head every lived month, asks
  for things through forced events (services/possession.press_hook).
- The sync grows every month; at `final_at` the question «одно тело на двоих» comes: merge (the character steps
  beyond the Blackwall as an AI, services/rogue_ai), give her the body (the life ends), or cut her out.
- She is a person in «Связи» too (an NPC contact with `data.ai`): the talk, the memory, the actions of a talk.
Effects: `possess:<src>: true` starts, `possess_sync: N` / `possess_trust: N` shift, `possess_end:<how>: true` ends.
"""

from __future__ import annotations

from typing import Any, Iterator

from app.content.loader import Content
from app.engine.state import CharacterState, NpcState

FLAG = "possessed"
ENDS = ("purge", "merge", "takeover", "silence")


def cfg(content: Content):  # noqa: ANN201
    return content.balance.possession


def rec(st: CharacterState) -> dict[str, Any] | None:
    r = st.flags.get(FLAG)
    return dict(r) if isinstance(r, dict) and r.get("name") else None


def tier(content: Content, r: dict[str, Any]) -> Any:
    out = None
    for t in cfg(content).tiers:
        if int(r.get("sync", 0)) >= t.at:
            out = t
    return out


def mod_sources(st: CharacterState, content: Content) -> Iterator[tuple[str, dict[str, int]]]:
    r = rec(st)
    if r is None or r.get("quiet"):
        return
    t = tier(content, r)
    if t is not None and t.mods:
        yield f"possessed:{t.name}", dict(t.mods)


def npc_of(st: CharacterState) -> NpcState | None:
    return next((n for n in st.npcs if n.data.get("ai") and n.alive), None)


def start(st: CharacterState, content: Content, src: str) -> str:
    c = cfg(content)
    s = c.sources.get(src) or next(iter(c.sources.values()))
    st.flags[FLAG] = {"src": src, "name": s.name, "sync": c.start_sync, "trust": 50, "since": st.weeks_lived}
    if npc_of(st) is None:
        st.npcs.append(NpcState(name=s.name, kind="contact", relation=50,
                                data={"ai": True, "bio": s.bio, "district": st.district}))
    return s.start_line


def shift(st: CharacterState, content: Content, sync: int = 0, trust: int = 0) -> None:
    r = rec(st)
    if r is None:
        return
    r["sync"] = max(0, min(100, int(r.get("sync", 0)) + sync))
    r["trust"] = max(0, min(100, int(r.get("trust", 50)) + trust))
    st.flags[FLAG] = r
    n = npc_of(st)
    if n is not None and trust:
        n.relation = max(0, min(100, n.relation + trust))


def end(st: CharacterState, content: Content, how: str) -> str:
    r = rec(st)
    if r is None:
        return ""
    st.flags.pop(FLAG, None)
    st.flags["pos_done"] = True  # one AI a life: the ways in close
    n = npc_of(st)
    if n is not None:
        st.npcs.remove(n)
    if how == "merge":
        st.flags["ascend_ai"] = True  # the AI path (services/rogue_ai.ascend through the menu)
        return f"{r['name']} и ты становитесь одним. Тело больше не нужно: Сеть ждёт."
    if how == "purge":
        return f"{r['name']} вырезана. В голове тихо, слишком тихо."
    if how == "takeover":
        return f"{r['name']} открывает твои глаза и делает первый шаг твоими ногами."
    return f"{r['name']} уходит сама. На прощание на экране мигает: «Спасибо»."
