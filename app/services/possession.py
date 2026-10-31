"""An AI in the head, the service side (owner request 26.10; engine/possession.py has the rules).

Every lived month (press_hook): the sync grows (faster when she trusts), a line of her voice, sometimes one of her
wishes (a forced event), and at `final_at` the question «Одно тело на двоих». From the screen «В голове»: blockers
(−sync, she is quiet for the month) and a ripper who cuts her out.
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import possession as eng
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active


async def press_hook(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random,
                     year: int) -> list[str]:
    from app.engine import grammar
    from app.services.week import pending_events, set_pending

    st = ch.state
    r = eng.rec(st)
    if r is None:
        return []
    c = eng.cfg(content)
    lines: list[str] = []
    if r.get("quiet"):  # the blockers held her for a month
        r.pop("quiet", None)
        st.flags[eng.FLAG] = r
        return [f"🧠 {r['name']} снова здесь: блокаторы выветрились."]
    grow = c.sync_month + (c.trust_bonus if int(r.get("trust", 50)) >= 70 else 0)
    eng.shift(st, content, sync=grow)
    r = eng.rec(st) or r
    t = eng.tier(content, r)
    lines.append(f"🧠 {grammar.expand(content.balance.grammar, rng.choice(c.voice), rng).format(name=r['name'])}"
                 if c.voice else f"🧠 {r['name']} рядом.")
    lines.append(f"Синхронизация {r['sync']}%" + (f" · «{t.name}»" if t else "") + ".")
    q = pending_events(ch)
    mine = set(c.events) | {c.final_event}
    if any(e in mine for e in q):
        return lines
    if int(r["sync"]) >= c.final_at and c.final_event in content.events:
        set_pending(ch, q + [c.final_event])
        lines.append(f"⚠️ {r['name']}: «Нам надо поговорить. Серьёзно»." )
    elif rng.random() < c.event_chance:
        from app.engine import events as ev_eng

        pool = [e for e in c.events if e in content.events and e != r.get("last")
                and ev_eng.matches(content.events[e], st, content)]
        if pool:
            pick = rng.choice(pool)
            r = eng.rec(st) or r
            r["last"] = pick
            st.flags[eng.FLAG] = r
            set_pending(ch, q + [pick])
    return lines


async def suppress(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                   tz: str) -> str | None:
    c = eng.cfg(content)
    r = eng.rec(ch.state)
    if r is None:
        return "none"
    if ch.state.eddies < c.suppress_cost:
        return "eddies"
    apply_effects(ch.state, {"eddies": -c.suppress_cost}, content, rng, year)
    eng.shift(ch.state, content, sync=-c.suppress_sync, trust=-5)
    r = eng.rec(ch.state) or r
    r["quiet"] = True
    ch.state.flags[eng.FLAG] = r
    await save_active(session, ch, tz)
    return None


async def cut_out(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  tz: str) -> str | None:
    c = eng.cfg(content)
    if eng.rec(ch.state) is None:
        return "none"
    if ch.state.eddies < c.remove_cost:
        return "eddies"
    apply_effects(ch.state, {"eddies": -c.remove_cost, "possess_end:purge": True, "humanity": 5}, content, rng, year)
    ch.state.flags["pos_done"] = True
    await save_active(session, ch, tz)
    return None
