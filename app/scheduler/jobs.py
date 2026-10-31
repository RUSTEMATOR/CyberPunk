"""Scheduled jobs of the group (PLAN 4.7, DESIGN.md 12a), Kyiv time:

- 12:00 "Ежедневка обновилась" (deleted after 3 hours);
- 21:00 mentions of the players whose streak is about to burn;
- Sunday 20:00 "Легенды недели";
- the global tick (scheduler/tick.py) at the global tick hour: the real-time city only (news, heat, gang wars and the
  coin rate step; the rate also catches up lazily on /coin); a watchdog runs a missed tick. There is no world clock:
  canon events, eras and the gig boards follow each character's own date (DESIGN.md 9a);
- a sweep that posts instant feed records that did not go out right after their commit;
- the daily database backup (`services/backup.py`) at `backup.hour`, newest `backup.keep` copies stay.

Every job is a plain coroutine over the UiKit, so a scratch script can call it directly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.bot import mention, texts
from app.bot.ui.cleanup import KIND_NEWS, KIND_TEMP
from app.bot.ui.kit import UiKit
from app.clock import kyiv_date
from app.db import repo
from app.db import session as db
from app.db.models import Player
from app.engine import daily as engine_daily
from app.scheduler import tick
from app.services import backup, feed, legends, minigames

log = logging.getLogger(__name__)


async def noon_post(ui: UiKit) -> None:
    ui.post(texts.REMIND_NOON, ui.content.balance.reminders.noon_ttl_h * 3600, KIND_TEMP)


async def evening_mentions(ui: UiKit) -> int:
    """Mention everyone whose streak burns if they skip today. Returns how many were named."""
    today = kyiv_date(ui.settings.timezone)
    async with db.session_scope() as s:
        res = await s.execute(select(Player))
        names = []
        for p in res.scalars():
            if engine_daily.streak_at_risk(p.last_daily, today, int(p.streak or 0), int(p.freeze_count or 0)):
                char = await repo.display_name(s, p.tg_id, "")
                names.append(mention.ping(p.username, char or None, p.tg_id))
    if names:
        ui.post(texts.REMIND_EVENING.format(names=", ".join(names)), ui.content.balance.reminders.evening_ttl_h * 3600, KIND_TEMP)
    return len(names)


def weekly_text(ui: UiKit, summary: legends.WeeklySummary) -> str:
    lines = [texts.LEGENDS_WEEK_TITLE]
    tpl = {"street": texts.LEGENDS_WEEK_STREET, "status": texts.LEGENDS_WEEK_STATUS, "fame": texts.LEGENDS_WEEK_FAME}
    for g in summary.gains:
        lines.append(tpl[g.scale].format(who=texts.esc(g.name), n=g.gain))
    if summary.caliber:
        _, name, mm, title = summary.caliber
        lines.append(texts.LEGENDS_WEEK_CALIBER.format(who=texts.esc(name), mm=mm, title=title))
    if summary.dump:
        _, a, _, b = summary.dump
        lines.append(texts.LEGENDS_WEEK_DUMP.format(a=texts.esc(a), b=texts.esc(b)))
    if summary.quiet:
        lines.append(texts.LEGENDS_WEEK_QUIET)
    return "\n".join(lines)


async def weekly_legends(ui: UiKit) -> str:
    today = kyiv_date(ui.settings.timezone)
    async with db.session_scope() as s:
        summary = await legends.weekly_summary(s, ui.content, today)
    text = weekly_text(ui, summary)
    ui.post(text, ui.content.balance.reminders.legends_ttl_h * 3600, KIND_NEWS)
    from app.services import leaderboard  # owner request: titles of the week

    async with db.session_scope() as s:
        board = await leaderboard.award(s)
    ui.post(board, ui.content.balance.reminders.legends_ttl_h * 3600, KIND_NEWS)
    return text


async def feed_sweep(ui: UiKit) -> int:
    return await feed.flush_instant(ui)


async def backup_database(ui: UiKit) -> str | None:
    path = await backup.backup_job(ui.settings.db_file, ui.content.balance.backup.keep)
    return path.name if path else None



def _post_all(ui: UiKit, posts: list[str], ttl: float, kind: str) -> None:
    """Owner request 25.10 «меньше спама»: the posts of one job go out as one message (split only when too long)."""
    chunk: list[str] = []
    size = 0
    for text in [t for t in posts if t]:
        if chunk and size + len(text) > 3500:
            ui.post("\n\n".join(chunk), ttl, kind)
            chunk, size = [], 0
        chunk.append(text)
        size += len(text) + 2
    if chunk:
        ui.post("\n\n".join(chunk), ttl, kind)

def _safe(fn, ui: UiKit):  # noqa: ANN001
    async def run() -> None:
        try:
            await fn(ui)
        except Exception:
            log.exception("scheduled job %s failed", fn.__name__)

    run.__name__ = fn.__name__
    return run


async def retention_job(ui: UiKit) -> None:
    """Pings for players with letters, the shared goal of the day (app/services/retention.py)."""
    from app.services import retention

    async with db.session_scope() as s:
        posts = await retention.pings(s, ui.content, ui.settings.timezone)
        posts += await retention.goal_posts(s, ui.content, ui.settings.timezone)
        from app.services import crews  # owner request: crew heists close on time

        posts += await crews.heist_tick(s, ui.content)
        from app.services import bigmoney  # owner request: the auction of the group

        posts += await bigmoney.auction_tick(s, ui.content)
        import random as _random

        from app.services import hariton  # owner request 25.10: the event of the week with the cat Hariton

        cat = await hariton.tick(s, ui.content, ui.settings.timezone, _random.Random())
    ttl = ui.content.balance.retention.ping_ttl_h * 3600
    _post_all(ui, posts, ttl, KIND_TEMP)
    _post_all(ui, cat, 7 * 24 * 3600, KIND_NEWS)


async def purge_job(ui: UiKit) -> None:
    """«Судная ночь» (owner request): the warning, the siren and the dawn on time (app/services/purge.py)."""
    from app.services import purge

    async with db.session_scope() as s:
        posts = await purge.tick(s, ui.content, ui.settings.timezone)
    _post_all(ui, posts, 24 * 3600, KIND_NEWS)


async def crew_biz_job(ui: UiKit) -> None:
    """Owner report 23.10: a crew business pays into the bank every hour."""
    import random as _random

    from app.services import crews

    async with db.session_scope() as s:
        await crews.biz_tick(s, ui.content, _random.Random())


async def dyn_job(ui: UiKit) -> None:
    """Owner request 23.10: the non-canon gangs and corps live a day (app/services/dyn.py)."""
    import random as _random

    from app.services import dyn

    now = datetime.now(ZoneInfo(ui.settings.timezone))
    async with db.session_scope() as s:
        last = await repo.ws_get(s, "dyn_day", None)
        if last == now.date().isoformat() or (last is not None and now.hour < ui.content.balance.dyn.hour):
            return  # once a real day, after `dyn.hour`; the very first day starts at once
        await repo.ws_set(s, "dyn_day", now.date().isoformat())
        posts = await dyn.daily(s, ui.content, _random.Random())
        from app.services import blackwall  # owner request 24.10: NetWatch cleans the city, tainted districts break through

        breaches = await blackwall.daily(s, ui.content, _random.Random())
        from app.services import control  # owner request 25.10: gangs against NCPD shift once a day

        breaches += await control.daily(s, ui.content, _random.Random())
        from app.services import game, pmc  # owner request 25.10: the conflict of two megacorps moves every day

        breaches += await pmc.daily(s, ui.content, _random.Random(), await game.current_year(s, ui.content))
        from app.services import corpstand  # owner request 25.10: the corps heal, the fallen rise

        breaches += await corpstand.daily(s, ui.content)
    _post_all(ui, ["🔫 " + texts.esc(t) for t in posts] + [texts.esc(t) for t in breaches], 24 * 3600, KIND_NEWS)


async def crisis_job(ui: UiKit) -> None:
    """Owner request 24.10: the city crises start, report, end and pay (app/services/crisis.py)."""
    import random as _random

    from app.services import crisis, retention

    async with db.session_scope() as s:
        posts = await crisis.tick(s, ui.content, _random.Random(), retention._quiet(ui.content, ui.settings.timezone))  # noqa: SLF001
    _post_all(ui, posts, 48 * 3600, KIND_NEWS)


async def challenge_job(ui: UiKit) -> None:
    """Owner request 25.10: once a day every build is measured; outgrown fighters get a challenge twice as strong."""
    import random as _random

    from app.services import challenge

    tz = ui.settings.timezone
    async with db.session_scope() as s:  # owner request 25.10: the meters of the districts exist from the start
        from app.services import control

        if not await repo.ws_get(s, control.KEY, None):
            await control.save(s, await control.load(s, ui.content))
    async with db.session_scope() as s:  # owner request 25.10: the season closes on time
        from app.services import season

        season_posts = await season.tick(s, ui.content)
    _post_all(ui, season_posts, 72 * 3600, KIND_NEWS)
    async with db.session_scope() as s:
        first = not await challenge.book(s)
        if not first and datetime.now(ZoneInfo(tz)).hour < ui.content.balance.challenge.hour:
            return
        posts = await challenge.daily(s, ui.content, tz, _random.Random())
    _post_all(ui, posts, 24 * 3600, KIND_NEWS)


async def names_job(ui: UiKit) -> None:
    """Owner request 24.10: the map of character names to player handles the outbox decorates texts with."""
    from app.bot.ui import names

    async with db.session_scope() as s:
        await names.refresh(s)


async def raid_job(ui: UiKit) -> None:
    """The city raid: close an escaped boss, start a new one when due (app/bot/handlers/raid.py)."""
    from app.bot.handlers.raid import raid_tick  # local: the handler module imports the kit

    await raid_tick(ui)


async def pboss_job(ui: UiKit) -> None:
    """Owner request 27.10: a player as the raid boss with a bounty (services/pboss.py)."""
    from app.services import pboss

    async with db.session_scope() as s:
        posts = await pboss.tick(s, ui.content, ui.settings.timezone)
    _post_all(ui, posts, 24 * 3600, KIND_NEWS)
    async with db.session_scope() as s:  # the fallen boss's legend scene (Claude, up to a minute)
        await pboss.write_scenes(s)


async def borgs_job(ui: UiKit) -> None:
    """Owner request 27.10: a body with so much chrome that it is more machine than meat is a borg; the city learns."""
    from pathlib import Path

    from app.services import ripper

    # the borgs already in the city are announced once the picture is in (images/borg.png) or by the balance switch
    if not (ui.content.balance.cyber.borg_sweep or (Path(ui.settings.images_path) / "borg.png").exists()):
        return
    async with db.session_scope() as s:
        await ripper.sweep_borgs(s, ui.content, ui.settings.timezone, force=True)


async def director_job(ui: UiKit) -> None:
    """Owner request 26.10: the city director: the day's input for the scheduled task, then its story (services/director.py)."""
    from app.services import director

    c = ui.content.balance.director
    if not c.enabled:
        return
    now = datetime.now(ZoneInfo(ui.settings.timezone))
    async with db.session_scope() as s:
        if now.hour >= c.hour:
            await director.dump(s, ui.content, ui.settings.db_path, now.date())
        posts = await director.collect(s, ui.content, ui.settings.db_path, now.date(), ui.settings.timezone)
    _post_all(ui, ["🎬 <b>Город. Сюжет дня</b>\n" + texts.esc(p) for p in posts], 24 * 3600, KIND_NEWS)


async def dialogs_job(ui: UiKit) -> None:
    """Owner request 26.10: the talks with NPCs for the scheduled task, then its outcomes (services/dialogs.py)."""
    from app.services import dialogs

    if not ui.content.balance.dialogs.enabled:
        return
    async with db.session_scope() as s:
        await dialogs.collect(s, ui.content, ui, ui.settings.db_path)
        await dialogs.harvest(s, ui.content, ui.settings.timezone)  # owner request 27.10: the deeds too
        if ui.content.balance.dialogs.file_dump:
            await dialogs.dump(s, ui.content, ui.settings.db_path)
        items = await dialogs.prepare(s, ui.content)  # owner request 27.10 «не scheduled Claude»
    if not items:
        return
    judged = await dialogs.judge(items)  # Claude, outside the database session
    async with db.session_scope() as s:
        await dialogs.apply_judged(s, ui.content, ui, ui.settings.db_path, judged)


async def pally_job(ui: UiKit) -> None:
    """Owner request 26.10: the hired players are called when their employer's fight starts (services/pally.py)."""
    from app.services import pally

    async with db.session_scope() as s:
        await pally.send_pings(s, ui)


async def capri_job(ui: UiKit) -> None:
    """Owner request 26.10: project «Каприкорн» (services/capri.py)."""
    from app.services import capri

    async with db.session_scope() as s:
        await capri.job(s, ui.content, ui)


async def clones_job(ui: UiKit) -> None:
    """Owner request 26.10: a new clone corp comes after the old one falls (services/clones.py)."""
    from app.services import clones

    async with db.session_scope() as s:
        await clones.job(s, ui.content, ui)


async def equilib_job(ui: UiKit) -> None:
    """Owner request 26.10: the balance of the gangs, the Order and the Midnight Agency (services/equilib.py)."""
    from app.services import equilib

    async with db.session_scope() as s:
        await equilib.tick(s, ui.content, ui, ui.settings.timezone)


async def blacktears_job(ui: UiKit) -> None:
    """Owner request 26.10: Raven Tearz comes in the daytime; the consequences of the contracts (services/blacktears.py)."""
    from app.services import blacktears

    async with db.session_scope() as s:
        await blacktears.job(s, ui.content, ui)


async def sticker_job(ui: UiKit) -> None:
    """Owner request 26.10: the legends' stickers go into the group's set (services/stickers.py)."""
    from app.services import stickers

    admin = min(ui.settings.admin_ids) if ui.settings.admin_ids else 0
    async with db.session_scope() as s:
        await stickers.process(s, ui.content, ui.bot, admin, int(ui.settings.group_chat_id or 0))


def register(scheduler: AsyncIOScheduler, ui: UiKit) -> None:
    """Put the jobs on the scheduler (cron in the scheduler's time zone, Kyiv by default)."""
    rem = ui.content.balance.reminders
    tz = ui.settings.timezone
    common = {"misfire_grace_time": 3600, "coalesce": True, "replace_existing": True}
    scheduler.add_job(_safe(noon_post, ui), CronTrigger(hour=rem.noon_hour, minute=0, timezone=tz), id="noon_post", **common)
    scheduler.add_job(_safe(evening_mentions, ui), CronTrigger(hour=rem.evening_hour, minute=0, timezone=tz), id="evening_mentions", **common)
    scheduler.add_job(
        _safe(weekly_legends, ui),
        CronTrigger(day_of_week=rem.legends_weekday, hour=rem.legends_hour, minute=0, timezone=tz),
        id="weekly_legends", **common,
    )
    scheduler.add_job(
        _safe(tick.tick_job, ui), CronTrigger(hour=ui.content.balance.time.global_tick_hour, minute=0, timezone=tz),
        id="global_tick", **common,
    )
    scheduler.add_job(
        _safe(tick.tick_watchdog, ui), IntervalTrigger(minutes=10, timezone=tz), id="tick_watchdog",
        next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=20), **common,
    )
    scheduler.add_job(
        _safe(backup_database, ui), CronTrigger(hour=ui.content.balance.backup.hour, minute=0, timezone=tz),
        id="db_backup", **common,
    )
    scheduler.add_job(_safe(feed_sweep, ui), IntervalTrigger(seconds=60, timezone=tz), id="feed_sweep", **common)
    scheduler.add_job(_safe(retention_job, ui), IntervalTrigger(minutes=2, timezone=tz), id="retention",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=50), **common)
    scheduler.add_job(_safe(names_job, ui), IntervalTrigger(minutes=2, timezone=tz), id="names",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=5), **common)
    scheduler.add_job(_safe(challenge_job, ui), IntervalTrigger(minutes=30, timezone=tz), id="challenge_tick",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=90), **common)
    scheduler.add_job(_safe(crisis_job, ui), IntervalTrigger(minutes=5, timezone=tz), id="crisis_tick",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=40), **common)
    scheduler.add_job(_safe(purge_job, ui), IntervalTrigger(minutes=1, timezone=tz), id="purge_tick",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=30), **common)
    scheduler.add_job(_safe(crew_biz_job, ui), IntervalTrigger(minutes=15, timezone=tz), id="crew_biz",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=45), **common)
    scheduler.add_job(_safe(dyn_job, ui), IntervalTrigger(minutes=20, timezone=tz), id="dyn_tick",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=55), **common)
    scheduler.add_job(_safe(sticker_job, ui), IntervalTrigger(minutes=3, timezone=tz), id="stickers",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=80), **common)
    scheduler.add_job(_safe(capri_job, ui), IntervalTrigger(minutes=10, timezone=tz), id="capri",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=35), **common)
    scheduler.add_job(_safe(clones_job, ui), IntervalTrigger(minutes=30, timezone=tz), id="clones",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=25), **common)
    scheduler.add_job(_safe(equilib_job, ui), IntervalTrigger(minutes=30, timezone=tz), id="equilib",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=15), **common)
    scheduler.add_job(_safe(blacktears_job, ui), IntervalTrigger(minutes=ui.content.balance.blacktears.tick_min, timezone=tz),
                      id="blacktears", next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=45), **common)
    scheduler.add_job(_safe(pally_job, ui), IntervalTrigger(minutes=1, timezone=tz), id="pally_ping",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=30), **common)
    scheduler.add_job(_safe(dialogs_job, ui), IntervalTrigger(minutes=5, timezone=tz), id="dialogs",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=90), **common)
    scheduler.add_job(_safe(director_job, ui), IntervalTrigger(minutes=5, timezone=tz), id="director",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=70), **common)
    scheduler.add_job(_safe(raid_job, ui), IntervalTrigger(minutes=10, timezone=tz), id="raid_tick",
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=40), **common)
    scheduler.add_job(_safe(pboss_job, ui), IntervalTrigger(minutes=5, timezone=tz), id="pboss",  # owner request 27.10
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=25), **common)
    scheduler.add_job(_safe(borgs_job, ui), IntervalTrigger(minutes=30, timezone=tz), id="borgs",  # owner request 27.10
                      next_run_time=datetime.now(ZoneInfo(tz)) + timedelta(seconds=90), **common)
