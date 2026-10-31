"""The city feed (PLAN 10.1, 10.1a, 10.3, 10.4, DESIGN.md 3): public `event_log` records become group posts.

- instant posts: obituary, cyberpsycho, MaxTac hunts, arrests (`engine/feed.py::INSTANT_KINDS`) go out right after the
  commit that wrote them (db commit hook) and in a periodic sweep as a safety net;
- the rest of the public records wait for the nightly news (services/tick.py asks `collect_news`);
- every post is queued for deletion: news `ui.news_ttl_h`, alerts `ui.alert_ttl_h`, obituaries stay (rule 10.1a);
- only what the public record carries is published (10.4): names, districts, the career or crime kind. Money, chrome
  and debts of `payload.effects` are never read here.
"""

from __future__ import annotations

from datetime import datetime

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts, texts_world
from app.bot.ui.cleanup import KIND_ALERT, KIND_NEWS
from app.bot.ui.kit import UiKit
from app.content.loader import Content
from app.db import repo
from app.db import session as db
from app.db.models import Character, EventLog
from app.engine import feed as feed_engine
from app.engine.character_gen import FLAG_GENDER
from app.engine.feed import CLASS_ALERT, CLASS_NEWS, CLASS_PERMANENT
from app.narrator import narrator
from app.services import legends

log = logging.getLogger(__name__)

_flush_lock = asyncio.Lock()
_last_batch = 0.0  # monotonic time of the last batched post (owner request 25.10: less spam)


def full_name(payload: dict, fallback: str = "") -> str:
    name = " ".join(x for x in (payload.get("name"), payload.get("surname")) if x)
    return name or fallback


def _char_name(row: Character | None, payload: dict) -> str:
    if row is not None:
        return " ".join(x for x in (row.name, row.surname) if x) or "безымянный"
    return full_name(payload, "безымянный")


def ttl_s(content: Content, klass: str) -> float | None:
    """Seconds until the post is deleted; None = never."""
    if klass == CLASS_PERMANENT:
        return None
    ui = content.balance.ui
    return (ui.alert_ttl_h if klass == CLASS_ALERT else ui.news_ttl_h) * 3600.0


def cleanup_kind(klass: str) -> str:
    return KIND_ALERT if klass == CLASS_ALERT else KIND_NEWS


async def death_text(content: Content, row: EventLog, char: Character | None) -> str:
    """The obituary: the whole text M7 stored in the record (it names the dead), else the narrator's."""
    p = row.payload or {}
    stored = p.get("obituary_text")
    name = _char_name(char, p)
    if stored:
        return texts_world.FEED_DEATH.format(text=texts.esc(str(stored)))  # M7 stores the whole text, the name in it
    weeks = int(p.get("weeks_lived") or (char.weeks_lived if char is not None else 0) or 0)
    achievement = (
        texts_world.OBIT_ACHIEVEMENT.format(weeks=weeks, weeks_word=texts_world.weeks_word(weeks))
        if weeks > 0 else texts_world.OBIT_ACHIEVEMENT_NONE
    )
    payload = {
        "name": name, "role": legends.roles_label(content, (char.role_affinity if char is not None else None) or {}),
        "age": int(p.get("age") or (char.age_years if char is not None else 0) or 0),
        "cause": texts.DEATH_CAUSE_NAMES.get(str(p.get("cause")), str(p.get("cause") or "")),
        "street_cred": int(p.get("street_cred") or 0),
        "achievement": achievement,
    }
    body = await narrator.narrate(
        "obituary", payload, year=(char.birth_year + char.age_years) if char is not None else None,
    )
    return texts_world.FEED_DEATH.format(text=texts.esc(body) if body else texts.esc(name))


async def render_instant(content: Content, session: AsyncSession, row: EventLog) -> str | None:
    """The post text of an instant record, or None when there is nothing publishable."""
    char = await session.get(Character, row.character_id) if row.character_id is not None else None
    p = row.payload or {}
    name = texts.esc(_char_name(char, p))
    kind = row.kind
    if kind == "death":
        return await death_text(content, row, char)
    if kind in ("loud", "legend", "borg"):
        return str(p.get("text") or "") or None
    if kind == "cyberpsychosis":
        district = content.districts.get(p.get("district") or (char.district if char else ""))
        if district is not None:
            return texts_world.FEED_PSYCHO.format(name=name, district=texts.esc(district.name))
        return texts_world.FEED_PSYCHO_NO_DISTRICT.format(name=name)
    if kind in ("psycho_hunt", "maxtac_hunt"):
        return texts_world.FEED_MAXTAC_HUNT.format(name=name)
    if kind == "psycho_caught":
        tpl = texts_world.FEED_PSYCHO_CAUGHT_RECRUITED if p.get("recruited") else texts_world.FEED_PSYCHO_CAUGHT
        return tpl.format(name=name)
    if kind == "psycho_ward":
        return texts_world.FEED_PSYCHO_WARD.format(name=name, weeks=int(p.get("weeks") or 0))
    if kind == "maxtac_recruit":
        return texts_world.FEED_MAXTAC_RECRUIT.format(name=name)
    if kind == "arrest":
        return texts_world.FEED_ARREST.format(name=name, weeks=int(p.get("weeks") or 0))
    if kind == "jail_escape":
        return texts_world.FEED_JAIL_ESCAPE.format(name=name)
    if kind == "wanted":
        return texts_world.FEED_WANTED.format(name=name, heat=int(p.get("heat") or 0))
    if kind == "canon_reached":  # a character's own date reached a canon event or a generated beat (DESIGN.md 9a)
        gender = str((char.flags or {}).get(FLAG_GENDER) or "m") if char is not None else "m"
        return texts_world.FEED_CANON_REACHED.get(gender, texts_world.FEED_CANON_REACHED["m"]).format(
            name=texts.esc((char.name if char is not None else None) or str(p.get("name") or "") or "безымянный"),
            year=int(p.get("year") or 0), title=texts.esc(str(p.get("title") or "")),
        )
    return None


async def instant_image(content: Content, session: AsyncSession, row: EventLog) -> feed_engine.ImageRequest | None:
    """The picture request of an instant record: for a cyberpsycho the district the record names (else the one the
    character lives in) is a wanted tag, so a picture made for that district is preferred."""
    district = None
    if row.kind == "cyberpsychosis":
        p = row.payload or {}
        char = await session.get(Character, row.character_id) if row.character_id is not None else None
        key = p.get("district") or (char.district if char is not None else None)
        district = key if key in content.districts else None
    return feed_engine.instant_image(row.kind, district, str((row.payload or {}).get("ref") or "") or None)


async def flush_instant(ui: UiKit) -> int:
    """Post every unpublished instant record. The records are claimed (marked published) before the send, so two
    overlapping flushes never post one record twice. Returns the number of posts."""
    import time

    global _last_batch
    sent = 0
    async with _flush_lock:
        batch_due = time.monotonic() - _last_batch >= ui.content.balance.ui.feed_batch_min * 60
        batch: list[str] = []
        async with db.session_scope() as s:
            res = await s.execute(
                select(EventLog).where(
                    EventLog.public.is_(True), EventLog.published.is_(False),
                    EventLog.kind.in_(list(feed_engine.INSTANT_KINDS)),
                ).order_by(EventLog.id)
            )
            rows = list(res.scalars())
            posts: list[tuple[str, str, feed_engine.ImageRequest | None]] = []
            for row in rows:
                batched = row.kind in feed_engine.BATCH_KINDS
                if batched and not batch_due:
                    continue  # waits for the next batch
                row.published = True
                try:
                    text = await render_instant(ui.content, s, row)
                except Exception:
                    log.exception("feed: could not render record %s (%s)", row.id, row.kind)
                    continue
                if text and batched:
                    batch.append(text)
                elif text:
                    avatar = (row.payload or {}).get("avatar") if row.kind in ("death", "legend") else None
                    if avatar:  # owner request 26.10: the character's own picture over the obituary
                        posts.append((text, feed_engine.post_class(row.kind), ("avatar", str(avatar))))
                        continue
                    posts.append((text, feed_engine.post_class(row.kind), await instant_image(ui.content, s, row)))
        if batch:
            _last_batch = time.monotonic()
            posts.append((batch_text(ui.content, batch), CLASS_ALERT, None))
        for text, klass, image in posts:
            if isinstance(image, tuple) and image[0] == "avatar":
                if await _avatar_post(ui, image[1], text, ttl_s(ui.content, klass), cleanup_kind(klass)):
                    sent += 1
                    continue
                image = None
            if await ui.send_feed(text, ttl_s(ui.content, klass), cleanup_kind(klass), image) is not None:
                sent += 1
    return sent


async def _avatar_post(ui, file_id: str, text: str, ttl: float | None, kind: str) -> bool:  # noqa: ANN001
    """The obituary under the character's own avatar: the photo with the text as caption, or the photo and then the
    text when it is too long. False when Telegram refused the picture (the caller posts the usual way)."""
    from app.services import images

    if ui.group_id is None:
        return False
    ttl = ui.cap_ttl(ttl, kind)
    fits = images.visible_length(text) <= images.CAPTION_MAX
    res = await ui.outbox.send_photo(ui.group_id, file_id, text if fits else None)
    if not res.ok or res.message_id is None:
        return False
    if ttl is not None:
        await ui.cleanup.schedule_delete(ui.group_id, res.message_id, ttl, kind)
    if not fits:
        ui._relay(text, ttl, kind)  # noqa: SLF001
        r2 = await ui.outbox.send(ui.group_id, text, None)
        if r2.ok and r2.message_id is not None and ttl is not None:
            await ui.cleanup.schedule_delete(ui.group_id, r2.message_id, ttl, kind)
    else:
        ui._relay(text, ttl, kind)  # noqa: SLF001
    return True


def batch_text(content: Content, lines: list[str]) -> str:
    """One post of many deeds: repeats of the same deed collapse into the last one, the rest is counted."""
    seen: dict[str, str] = {}
    for t in lines:
        t = t.strip()
        if t.startswith("📣"):
            t = t[1:].strip()
        seen[t[:45]] = t  # the same deed again (a pot raised five times): only the last stays
    items = list(seen.values())
    cap = content.balance.ui.feed_batch_max_lines
    body = "\n\n".join(f"• {t}" for t in items[-cap:])
    more = len(items) - cap
    return "📣 <b>Город говорит</b>\n\n" + body + (f"\n\n…и ещё {more}." if more > 0 else "")


def schedule_flush(ui: UiKit) -> None:
    """The commit hook: post the instant records in the background."""
    ui.spawn(flush_instant(ui))


def install(ui: UiKit) -> None:
    db.add_commit_hook(lambda: schedule_flush(ui))


# -- the nightly news ----------------------------------------------------------------------------------------------


@dataclass
class DistrictDigest:
    district: str  # content key, "" when unknown
    names: list[str] = field(default_factory=list)  # distinct, in order of appearance
    count: int = 0
    year: int = 0  # the own year of the latest named character: the era the paragraph is written in (personal time)
    facts: list[str] = field(default_factory=list)  # owner request 24.10: what exactly happened, for the narrator


_OUTCOME = {"clean": "чисто", "complication": "с осложнениями", "fail": "провален", "failed": "провален",
            "partial": "наполовину"}


def fact_line(content: Content | None, kind: str, p: dict, name: str) -> str | None:
    """One short, concrete line about a public record (no numbers the narrator must keep secret)."""
    from app.bot import texts as T

    if kind == "gig_result":
        out = _OUTCOME.get(str(p.get("outcome", "")), str(p.get("outcome", "")))
        pay = int(((p.get("effects") or {}).get("eddies", 0)) or 0)
        return f"{name}: заказ «{p.get('title', '?')}» — {out}" + (f", {pay} эдди" if pay > 0 else "")
    if kind == "event_resolved" and content is not None:
        ev = content.events.get(str(p.get("event", "")))
        if ev is None:
            return None
        idx = int(p.get("choice", 0) or 0)
        label = ev.choices[idx].label if 0 <= idx < len(ev.choices) else ""
        res = " (удачно)" if p.get("ok") is True else (" (не вышло)" if p.get("ok") is False else "")
        return f"{name}: «{ev.title}» — {label}{res}"
    if kind == "death":
        return f"{name}: гибель, {T.DEATH_CAUSE_NAMES.get(str(p.get('cause', '')), p.get('cause', ''))}"
    if kind == "arrest":
        return f"{name}: арест, срок {int(p.get('weeks', 0) or 0)} нед."
    if kind == "wanted":
        return f"{name}: розыск {int(p.get('heat', 0) or 0)}"
    if kind == "canon_reached":
        return f"{name}: застаёт событие «{p.get('title', '')}»"
    if kind == "maxtac_hunt":
        return f"{name}: за этим человеком выезжал МаксТак"
    if kind == "psycho_hunt":
        return f"{name}: охота на киберпсиха"
    if kind == "cyberpsychosis":
        return f"{name}: срыв, киберпсихоз"
    if kind == "jail_escape":
        return f"{name}: побег из тюрьмы"
    if kind in ("loud", "legend", "borg"):
        import re as _re

        t = _re.sub(r"<[^>]+>", "", str(p.get("text", ""))).replace("📣", "").strip()
        return t[:160] if t else None
    return None


async def collect_news(session: AsyncSession, limit: int = 1000,
                       content: Content | None = None, since: datetime | None = None) -> tuple[list[DistrictDigest], list[int]]:
    """Public records not yet published (the instant ones were flushed before), grouped by the district the
    character is in. Returns the digests, most active district first, and the ids to mark as published.
    `since`: owner request 24.10, a news preview of the records after that time, published or not."""
    q = select(EventLog).where(EventLog.public.is_(True))
    q = q.where(EventLog.created_at >= since) if since is not None else q.where(EventLog.published.is_(False))
    res = await session.execute(q.order_by(EventLog.id).limit(limit))
    rows = list(res.scalars())
    by_district: dict[str, DistrictDigest] = {}
    ids: list[int] = []
    for row in rows:
        ids.append(row.id)
        if feed_engine.is_instant(row.kind) and row.kind != "death":
            continue  # an instant record that failed to go out is not repeated in the digest (a death stays a fact)
        char = await session.get(Character, row.character_id) if row.character_id is not None else None
        key = char.district if char is not None else ""
        d = by_district.setdefault(key, DistrictDigest(key))
        d.count += 1
        if char is not None:
            d.year = int(char.birth_year) + int(char.age_years)
        nm = _char_name(char, row.payload or {})
        if nm not in d.names:
            d.names.append(nm)
        fact = fact_line(content, row.kind, row.payload or {}, nm)
        if fact and fact not in d.facts:
            d.facts.append(fact)
    digests = sorted(by_district.values(), key=lambda d: (-d.count, d.district))
    return digests, ids
