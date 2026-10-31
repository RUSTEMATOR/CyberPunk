"""Data for the Mini App screens (owner request 26.10, docs/PLAN_LIVING.md stage 6). The app draws, the bot decides:
these functions only read. Contract: docs/PLAN_LIVING.md «Контракт данных».

- `city(session, content)`: the live map of Night City: every district with the hold of the gangs and the corps, the
  player who holds it, the crews that came and go, the last deeds there; the megacorps' standing; the conflict; the crisis.
- `body(content, st)`: the body scheme: the implants by slot, the humanity.
- `last_fight(session, tg)`: the last fight for the replay (services/combat.replay).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo


async def city(session: AsyncSession, content: Content) -> dict[str, Any]:
    from app.engine import control as ce
    from app.services import control, corpstand, crisis, deeds, dyn, pmc, turf

    ctrl = await control.load(session, content)
    tb = await repo.ws_get(session, turf.KEY, {}) or {}
    crews = dyn.alive(await dyn.all_factions(session))
    dd = await deeds.all_deeds(session)
    districts = []
    for k, d in content.districts.items():
        if d.city != "nc":
            continue
        v = ctrl.get(k, {})
        g, c = int(v.get("gang", 0)), int(v.get("corp", 0))
        holder = tb.get(k) if isinstance(tb, dict) else None
        districts.append({
            "key": k, "name": d.name, "gang": g, "corp": c, "word": ce.word(g, c),
            "holder": {"name": holder.get("name"), "army": holder.get("army"), "tax": holder.get("tax"),
                       "law": holder.get("law")} if isinstance(holder, dict) else None,
            "crews": [{"id": f["id"], "kind": f["kind"], "name": f["name"], "power": f["power"]}
                      for f in crews if f.get("district") == k],
            "deeds": [x["text"] for x in dd if x.get("district") == k][-3:],
        })
    sb = await corpstand.book(session)
    corps = [{"key": k, "name": c.name, "hp": int(corpstand.rec_of(content, sb, k)["hp"]),
              "down": corpstand.down(corpstand.rec_of(content, sb, k)),
              "bar": corpstand.bar(content, corpstand.rec_of(content, sb, k))}
             for k, c in content.corps.items() if c.kind == "megacorp"]
    cr = await crisis.current(session)
    return {"districts": districts, "corps": corps, "conflict": await repo.ws_get(session, pmc.KEY, None),
            "crisis": {"key": cr.get("key"), "score": cr.get("score"), "ends": cr.get("ends")} if cr else None}


def body(content: Content, st: Any) -> dict[str, Any]:
    from app.bot.views_chrome import SLOT_NAMES
    from app.content.schemas import CYBER_SLOTS

    slots: dict[str, list[dict[str, Any]]] = {s: [] for s in CYBER_SLOTS}
    for it in st.cyberware:
        cw = content.cyberware.get(it.key)
        if cw is None:
            continue
        slots.setdefault(cw.slot, []).append({"key": it.key, "name": cw.name, "charge": it.charge,
                                              "complication": it.complication, "humanity_paid": it.humanity_paid})
    return {"slots": [{"slot": s, "name": SLOT_NAMES.get(s, s), "items": items} for s, items in slots.items()],
            "humanity": st.humanity, "borg": bool(st.flags.get("borg_announced")),
            "avatar": bool(st.flags.get("avatar_announced"))}


async def last_fight(session: AsyncSession, tg: int) -> dict[str, Any] | None:
    from app.services import playerdata

    got = await playerdata.get(session, tg, "last_fight", None)
    return got if isinstance(got, dict) else None
