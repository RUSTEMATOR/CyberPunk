"""The global daily tick (PLAN 10.2, 10.5, DESIGN.md 4 and 9a): 03:00 Kyiv. It runs the real-time city only.

There is no shared world clock (personal time, DESIGN.md 9a): canon events, eras, world effects and the gig boards
follow each character's own date and are handled in the week flow. One real day, in one unit of work:

1. district heat cools and gang wars run down; a new random war may start (of gangs that exist in the year of one of
   the living characters, picked at random);
2. the Eddy-crypto rate takes its step;
3. the public records since the last tick become the night news.

After the commit the news goes to the city feed. The tick is idempotent per day: a second call the same day does
nothing unless it is forced (`/admin_tick`).
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts, texts_world
from app.bot.ui import style
from app.bot.ui.cleanup import KIND_NEWS
from app.bot.ui.kit import UiKit
from app.clock import kyiv_date
from app.content.loader import Content
from app.db import repo
from app.db import session as db
from app.engine import feed as feed_engine, world as world_engine
from app.narrator import narrator
from app.services import feed, minigames, world as world_service, worldfx

log = logging.getLogger(__name__)

_lock = asyncio.Lock()


@dataclass
class TickReport:
    ran: bool = False
    days: int = 0
    wars_started: list[str] = field(default_factory=list)
    wars_ended: list[str] = field(default_factory=list)
    coin_rate: float = 0.0
    news: str = ""  # the night news text
    posts: list[tuple[str, float | None, str, feed_engine.ImageRequest | None]] = field(default_factory=list)  # (text, ttl_s, cleanup kind, picture)


async def build_news(
    content: Content, digests: list[feed.DistrictDigest], extra: list[str], history: list[str] | None = None,
) -> str:
    """The night news (PLAN 10.2): one narrator paragraph per active district, war lines. Quiet night: the ready-made
    quiet digest. Never longer than `narrator.news_max_chars`. There is no dateline: the players of a district live in
    their own years, so each paragraph is written in the era of the year its district's players stand in."""
    limit = content.balance.narrator.news_max_chars
    lines: list[str] = [texts_world.NEWS_TITLE]
    if not digests:
        body = await narrator.narrate("news_quiet", {})
        room = limit - len(texts_world.NEWS_TITLE) - len("<blockquote></blockquote>") - 2
        lines.append(style.quote(texts.esc(body if len(texts.esc(body)) <= room else body[: max(0, room // 2)].rstrip() + "…")))
    payloads: list[dict[str, object]] = []
    for d in digests:
        dist = content.districts.get(d.district)
        payload: dict[str, object] = {
            "district": dist.name if dist else texts_world.NEWS_DISTRICT_FALLBACK,
            "count": d.count, "names": ", ".join(d.names[:5]),
            "facts": d.facts[-10:],  # owner request 24.10: what exactly happened
        }  # the era and its tone are in the cached lore block of the narrator
        if history:
            payload["history"] = history  # recent world events (titles), so the news can refer to them
        payloads.append(payload)
    paragraphs = await asyncio.gather(*(narrator.narrate("news", p, year=d.year or None) for p, d in zip(payloads, digests)))
    shown = 0
    for d, text in zip(digests, paragraphs):
        para = style.quote(texts.esc(text))
        if len("\n".join(lines + [para])) > limit:
            rest = len(digests) - shown
            lines.append(texts_world.NEWS_MORE_DISTRICTS.format(n=rest, word=texts_world.districts_word(rest)))
            break
        lines.append(para)
        shown += 1
    for line in extra:
        if len("\n".join(lines + [line])) <= limit:
            lines.append(line)
    text = "\n".join(lines)
    return text if len(text) <= limit else "\n".join(lines[:-1])  # every line is whole HTML: drop, never cut


async def _war_year(s: AsyncSession, content: Content, rng: random.Random) -> int:
    """The year in which a random gang war is rolled: the own year of a random living character (so the war is between
    gangs that exist for somebody), else the start of the calendar."""
    rows = await repo.living_characters(s)
    if not rows:
        return content.balance.world.start_year
    return world_service.year_of_week(content, world_service.row_week(rng.choice(rows), content))


async def run_tick(
    ui: UiKit, *, force: bool = False, rng: random.Random | None = None, post: bool = True,
) -> TickReport:
    """Run the tick for the real days that passed. `force` runs it now even when today's tick is done (admin)."""
    content = ui.content
    tz = ui.settings.timezone
    rng = rng or random.Random()
    rep = TickReport()
    await feed.flush_instant(ui)  # whatever instant record is still waiting goes out before the digest
    async with _lock:
        async with db.session_scope() as s:
            today = world_service.today_world_day(content, tz)
            last = await repo.ws_get(s, world_service.LAST_WORLD_DAY_KEY)
            if last is None and not force:  # the very first run only starts the count of days
                await repo.ws_set(s, world_service.LAST_WORLD_DAY_KEY, today)
                await repo.ws_set(s, world_service.LAST_TICK_KEY, today)
                return rep
            last = str(last or today)
            days = _ordinal(today) - _ordinal(last)
            if days <= 0 and not force:
                return rep
            days = max(1, days)
            rep.ran, rep.days = True, days
            await repo.ws_set(s, world_service.LAST_WORLD_DAY_KEY, max(today, last))
            await repo.ws_set(s, world_service.LAST_TICK_KEY, max(today, last))

            # district heat and gang wars (real-time city)
            heat = await repo.ws_get(s, worldfx.HEAT_KEY, {}) or {}
            await repo.ws_set(s, worldfx.HEAT_KEY, world_engine.decay_district_heat(heat, content, days))
            wars = await worldfx.gang_wars(s)
            wars, ended = world_engine.tick_gang_wars(wars, days)
            extra: list[str] = []
            for gang in ended:
                rep.wars_ended.append(gang)
                extra.append(texts_world.NEWS_WAR_END.format(gang=texts.esc(content.gangs[gang].name)))
            for _ in range(min(days, 3)):  # a long sleep does not start a dozen wars at once
                new = world_engine.roll_gang_war(rng, content, await _war_year(s, content, rng), wars)
                if new is not None:
                    district, gang, length = new
                    wars[gang] = length
                    rep.wars_started.append(gang)
                    extra.append(texts_world.NEWS_WAR_START.format(
                        gang=texts.esc(content.gangs[gang].name), district=texts.esc(content.districts[district].name),
                    ))
            await repo.ws_set(s, worldfx.WARS_KEY, wars)

            # the Eddy-crypto step
            rep.coin_rate = await minigames.ensure_coin_rate(s, content, kyiv_date(tz))

            # the night news
            digests, ids = await feed.collect_news(s, content=content)
            await repo.mark_published(s, ids)
            recent = [r.title for r in reversed(await repo.history_rows(s, content.balance.narrator.history_recent_rows, active_only=True))]
        # the news are written after the commit: a Claude call must not hold the database write lock
        rep.news = await build_news(content, digests, extra, recent)
        async with db.session_scope() as s:  # owner request: the night news tags every player, with a day's recap
            from app.services import retention

            recap = await retention.recap_lines(s)
            dead = await retention.deaths_lines(s, content)
            from app.services import crisis as crisis_svc  # owner request 24.10: the city crisis in the news

            cr_line = await crisis_svc.menu_line(s)
            from app.services import mortality  # owner request 24.10: the death count of all the city

            mort = await mortality.news_block(s, content, tz, rng)
            from app.services import bounties, crews

            await bounties.drop_dead(s)
            await crews.biz_daily(s, content, rng)
        if cr_line:
            rep.news += "\n\n" + cr_line
        if mort:
            rep.news += "\n\n" + mort
        if dead:  # owner request 24.10: the deaths of the day with their causes
            rep.news += "\n\n<b>Погибли за сутки:</b>\n" + "\n".join(dead)
        if recap:
            rep.news += "\n\n" + texts_world.NEWS_RECAP + "\n" + "\n".join(recap) + "\n" + texts_world.NEWS_NEW_DAY
        if post:
            await ui.send_feed(rep.news, feed.ttl_s(content, "news"), KIND_NEWS, feed_engine.news_image())
    return rep


def _ordinal(day: str) -> int:
    from datetime import date

    return date.fromisoformat(day).toordinal()


async def tick_job(ui: UiKit) -> None:
    """The 03:00 cron job."""
    rep = await run_tick(ui)
    if rep.ran:
        log.info("global tick: %d day(s), %d war(s) started, news %d chars", rep.days, len(rep.wars_started), len(rep.news))


async def tick_watchdog(ui: UiKit) -> None:
    """Every few minutes (and at start): a tick that was missed (the bot was down at 03:00) is run now."""
    async with db.session_scope() as s:
        last = await repo.ws_get(s, world_service.LAST_WORLD_DAY_KEY)
        today = world_service.today_world_day(ui.content, ui.settings.timezone)
    if last is not None and str(last) < today:
        await tick_job(ui)


async def post_news_now(ui: UiKit, hours: int = 24) -> str:
    """Owner request 24.10: the news of the last `hours` now, without the tick (no heat, wars, coin; nothing is marked
    published, the morning news stays whole)."""
    from datetime import datetime, timedelta

    content = ui.content
    async with db.session_scope() as s:
        digests, _ = await feed.collect_news(s, content=content, since=datetime.utcnow() - timedelta(hours=hours))
        recent = [r.title for r in reversed(await repo.history_rows(s, content.balance.narrator.history_recent_rows, active_only=True))]
    text = await build_news(content, digests, [], recent)
    async with db.session_scope() as s:
        from app.services import retention

        recap = await retention.recap_lines(s)
        dead = await retention.deaths_lines(s, content)
        from app.services import crisis as crisis_svc

        cr_line = await crisis_svc.menu_line(s)
        from app.services import mortality

        mort = await mortality.news_block(s, content, ui.settings.timezone, store=False)
    if cr_line:
        text += "\n\n" + cr_line
    if mort:
        text += "\n\n" + mort
    if dead:
        text += "\n\n<b>Погибли за сутки:</b>\n" + "\n".join(dead)
    if recap:
        text += "\n\n" + texts_world.NEWS_RECAP + "\n" + "\n".join(recap) + "\n" + texts_world.NEWS_NEW_DAY
    await ui.send_feed(text, feed.ttl_s(content, "news"), KIND_NEWS, feed_engine.news_image())
    return text
