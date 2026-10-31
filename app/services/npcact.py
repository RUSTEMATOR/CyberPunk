"""What a person does in a talk (owner request 26.10 «действия с NPC … влияли на то, что происходит»).

In a talk (handlers/npcchat.py) Claude may end a reply with one action tag from the menu this module gives
(`menu`); the engine checks it again (`apply`) and changes the numbers. Claude never changes a number itself.
Cooldowns per NPC: `npc.data["act_cd"]` = {action: iso date it is free again}; one action per talk at most.
"""

from __future__ import annotations

import random
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.content.loader import Content
from app.engine.effects import apply_effects, apply_npc_relation
from app.engine.state import Buff, NpcState
from app.services.game import ActiveCharacter

# key: (who can, min relation, what Claude reads)
ACTS: dict[str, tuple[tuple[str, ...], int, str]] = {
    "warm": (("family", "partner", "friend", "contact", "protege", "child"), -100,
             "разговор тронул тебя, ты теплеешь к игроку"),
    "hurt": (("family", "partner", "friend", "contact", "protege", "child"), -100,
             "игрок тебя всерьёз задел или оскорбил, ты обижаешься"),
    "comfort": (("family", "partner", "friend", "child"), 20,
                "ты утешаешь и поддерживаешь игрока, ему становится легче"),
    "money": (("family", "partner", "friend"), 60,
              "ты сам решаешь помочь деньгами (без суммы в речи)"),
    "tip": (("friend", "contact"), 40,
            "ты даёшь наводку, где прячется банда или новая корпорация города"),
    "discount": (("contact",), 40, "ты договариваешься со знакомым риппером о скидке для игрока на сегодня"),
    "cover": (("contact", "friend"), 50, "ты подчищаешь следы игрока: знакомый в NCPD теряет бумаги"),
    "together": (("partner",), -100, "вы договорились провести вечер вдвоём"),
    # owner report 27.10: «Raven сдал адрес мастерской, а квеста никакого» — a lead turns into a scene at once
    "lead": (("family", "partner", "friend", "contact", "protege", "child"), 0,
             "ты даёшь игроку конкретную наводку на дело: адрес, место, имя, работу или чужую тайну; "
             "из неё сразу вырастет задание, поэтому говори конкретно"),
}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.npc_acts


def _free(npc: NpcState, key: str) -> bool:
    until = str((npc.data.get("act_cd") or {}).get(key, ""))
    return not until or until <= utcnow().date().isoformat()


def menu(ch: ActiveCharacter, npc: NpcState, *, has_dyn: bool) -> list[dict[str, str]]:
    out = []
    for key, (kinds, need, what) in ACTS.items():
        if npc.kind not in kinds or npc.relation < need or not _free(npc, key):
            continue
        if key == "tip" and not has_dyn:
            continue
        if key == "cover" and ch.state.heat <= 0:
            continue
        out.append({"key": key, "what": what})
    return out


async def apply(session: AsyncSession, content: Content, ch: ActiveCharacter, npc: NpcState, key: str,
                rng: random.Random, year: int) -> str:
    """Apply a tag Claude put; the line the window shows, or "" when the action is not allowed now."""
    from app.services import dyn, romance

    c = cfg(content)
    st = ch.state
    has_dyn = bool(dyn.alive(await dyn.all_factions(session)))
    if key not in {m["key"] for m in menu(ch, npc, has_dyn=has_dyn)}:
        return ""
    cd = dict(npc.data.get("act_cd") or {})
    days = int(c.cooldown_days.get(key, 1))
    cd[key] = (utcnow().date() + timedelta(days=days)).isoformat()
    npc.data["act_cd"] = cd
    name = npc.name
    if key == "warm":
        d = apply_npc_relation(st, content, npc, c.warm_rel)
        return f"{name} теплеет к тебе: отношение {d:+d}."
    if key == "hurt":
        d = apply_npc_relation(st, content, npc, -c.hurt_rel)
        return f"{name} обижается: отношение {d:+d}."
    if key == "comfort":
        apply_effects(st, {"mood": c.comfort_mood}, content, rng, year)
        return f"{name} умеет поддержать: настроение +{c.comfort_mood}."
    if key == "money":
        lo, hi = c.money
        n = rng.randint(lo, hi)
        apply_effects(st, {"eddies": n}, content, rng, year)
        apply_npc_relation(st, content, npc, -2)
        return f"{name} переводит тебе {n} эдди."
    if key == "tip":
        items = await dyn.all_factions(session)
        pool = dyn.alive(items)
        f = rng.choice(pool)
        clues = dict(st.flags.get(dyn.FLAG_CLUES) or {})
        clues[str(f["id"])] = min(dyn.cfg(content).search_need, int(clues.get(str(f["id"]), 0)) + c.tip_clues)
        st.flags[dyn.FLAG_CLUES] = clues
        found = clues[str(f["id"])] >= dyn.cfg(content).search_need
        return (f"{name} даёт наводку: {dyn.label(f)}. "
                + ("Логово найдено, можно идти на штурм («Улицы»)." if found else
                   f"Зацепок {clues[str(f['id'])]} из {dyn.cfg(content).search_need}."))
    if key == "discount":
        st.buffs = [b for b in st.buffs if b.source != "npc:discount"]
        st.buffs.append(Buff(source="npc:discount", mods={"ripper_discount_pct": c.discount_pct}))
        return f"{name} договаривается: сегодня у риппера скидка {c.discount_pct}%."
    if key == "cover":
        apply_effects(st, {"heat": -c.cover_heat}, content, rng, year)
        return f"{name} подчищает следы: розыск −{c.cover_heat}."
    if key == "lead":  # the scene itself is written right after the talk (services/uniqueevent.add_from_lead)
        return f"{name} даёт наводку. Из неё уже складывается дело: загляни в «События»."
    if key == "together":
        romance.mark_seen(st, npc)
        apply_npc_relation(st, content, npc, 2)
        return f"{name} ждёт тебя вечером: это засчитано как время вместе."
    return ""
