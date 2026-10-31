from __future__ import annotations

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.utils.backoff import BackoffConfig
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.commands import register_commands
from app.bot.handlers import build_routers
from app.bot.middlewares import GameMiddleware
from app.bot.ui.kit import UiKit
from app.config import BASE_DIR, load_settings
from app.content.loader import load_content_or_exit
from app.db import session as db
from app.scheduler import jobs
from app.narrator import narrator
from app.services import debtfix, feed, futurebeats, listeners, uniqueevent
from app.services import world as world_service

log = logging.getLogger("nightcity")

system_router = Router(name="system")


@system_router.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    await message.answer("pong")


def setup_logging() -> None:
    log_dir = BASE_DIR / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    file = RotatingFileHandler(log_dir / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [stream, file]


def _admin_notifier(ui: UiKit):  # noqa: ANN202
    """Sends the budget notices to every admin in private. An admin who never opened the bot cannot be written to;
    that is logged, and the notice stays in the log."""

    async def notify(text: str) -> None:
        for admin_id in sorted(ui.settings.admin_ids | ui.bootstrap_admins):
            try:
                await ui.bot.send_message(admin_id, text)
            except Exception as exc:
                log.warning("budget notice: could not write to admin %s: %s", admin_id, exc)

    return notify


class AdminErrorHandler(logging.Handler):
    """Sends ERROR log records to the admins in private (owner request), so a crash does not live only in the log.
    The same message (first line) goes out at most once per `quiet` seconds, and at most `per_hour` messages an hour."""

    def __init__(self, notify, quiet: int = 600, per_hour: int = 12) -> None:  # noqa: ANN001
        super().__init__(level=logging.ERROR)
        self.notify, self.quiet, self.per_hour = notify, quiet, per_hour
        self._last: dict[str, float] = {}
        self._sent: list[float] = []

    def emit(self, record: logging.LogRecord) -> None:
        import time

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        now = time.monotonic()
        head = record.getMessage().splitlines()[0][:200]
        if record.name == "aiogram.dispatcher" and "Failed to fetch updates" in head:
            return  # owner report 24.10: a network hiccup retries by itself; it is not worth a message to the admins
        if now - self._last.get(head, -1e9) < self.quiet:
            return
        self._sent = [t for t in self._sent if now - t < 3600]
        if len(self._sent) >= self.per_hour:
            return
        self._last[head] = now
        self._sent.append(now)
        tail = ""
        if record.exc_info:
            import traceback

            tail = "".join(traceback.format_exception(*record.exc_info))[-1500:]
        text = f"Ошибка бота ({record.name}):\n{head}" + (f"\n\n{tail}" if tail else "")
        from html import escape

        loop.create_task(self.notify(f"<pre>{escape(text)}</pre>"))


RESTART_FLAG = BASE_DIR / "data" / "restart.request"


async def _restart_watch(dp: Dispatcher) -> None:
    """A file data/restart.request asks for a clean restart (used to deploy updates without a terminal).
    Polling stops, the process exits and the service manager (launchd KeepAlive) starts it again."""
    if not RESTART_FLAG.exists():
        return
    from app.services import unity_jobs

    if unity_jobs.busy():  # owner request 31.10: a Unity build is running, the restart waits for it
        return
    try:
        RESTART_FLAG.unlink()
    except OSError as exc:
        log.warning("restart request: cannot remove the flag, ignoring it: %s", exc)
        return
    log.info("restart requested via %s, stopping polling", RESTART_FLAG.name)
    await dp.stop_polling()


async def _revive_once(ui: UiKit) -> None:
    from app.bot.handlers.admin import revive_and_post
    from app.db import repo

    key, tg_id = "revive_356096615_v1", 356096615
    async with db.session_scope() as s:
        if await repo.ws_get(s, key):
            return
    try:
        await revive_and_post(ui, tg_id)
    except Exception:
        log.exception("one-time revive failed")
        return
    async with db.session_scope() as s:
        await repo.ws_set(s, key, True)


async def _news_once(ui: UiKit) -> None:
    from app.db import repo
    from app.scheduler import tick

    key = "news_preview_2410_v1"
    async with db.session_scope() as s:
        if await repo.ws_get(s, key):
            return
        await repo.ws_set(s, key, True)
    ui.spawn(tick.post_news_now(ui))


NET_POLL_S = 10       # long-poll wait of getUpdates
NET_TIMEOUT_S = 25    # request timeout of every Telegram call (getUpdates gets this + NET_POLL_S)


def _net_session() -> AiohttpSession:
    """Owner report 24.10 (the bot went deaf for minutes on the Mac): Telegram over IPv4 only (a broken IPv6 route
    makes every new connection hang first), pooled connections closed after 20 s idle (a router or a VPN drops
    them silently, and reusing a dead one hangs the request)."""
    import socket

    s = AiohttpSession(timeout=NET_TIMEOUT_S)
    s._connector_init.update(family=socket.AF_INET, keepalive_timeout=20)  # noqa: SLF001
    return s


async def main() -> None:
    setup_logging()
    settings = load_settings()
    if not settings.bot_token:
        log.error("BOT_TOKEN is empty. Fill it in .env")
        sys.exit(1)

    db.init_engine(settings.db_file)
    await db.create_all()
    log.info("Database ready at %s", settings.db_file)

    content = load_content_or_exit()
    async with db.session_scope() as s:  # one-time move from the shared world clock to personal time (idempotent)
        await world_service.migrate_personal_time(s, content)
    async with db.session_scope() as s:  # debts of the old 5%-a-week model move to the new one (idempotent)
        await debtfix.migrate_debt_rates(s, content)
    async with db.session_scope() as s:  # owner request: a press lives a month; top up the turn granted before
        from app.services import migrations

        await migrations.ap_month_topup(s, content)
    async with db.session_scope() as s:  # owner request 30.10: the starting debts of the living are written off once
        from app.services import migrations

        await migrations.drop_start_debts(s, content)
    async with db.session_scope() as s:  # owner request 24.10: the live modifiers of the city crisis
        from app.services import crisis as crisis_service

        await crisis_service.refresh(s)
    async with db.session_scope() as s:  # owner request 24.10: player handles after character names in every text
        from app.bot.ui import names

        await names.refresh(s)

    bot = Bot(token=settings.bot_token, session=_net_session(), default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    ui = UiKit(bot, content, settings)
    from app.services import pally as pally_svc  # owner request 26.10: invitations into the employer's fight

    pally_svc.UI = ui
    gate = GameMiddleware(ui)
    dp.message.outer_middleware(gate)
    dp.callback_query.outer_middleware(gate)
    dp.include_router(system_router)
    for router in build_routers():
        dp.include_router(router)
    dp["settings"] = settings
    dp["content"] = content
    dp["ui"] = ui

    listeners.register(content, settings.timezone)
    narrator.configure(content, settings=settings)
    await narrator.start()
    await uniqueevent.restore_all(content)  # generated events that waited for a player across the restart
    from app.services import saga

    await saga.restore(content)  # every saga step Claude ever wrote
    from app.services import director

    await director.restore(content)  # owner request 26.10: the scenes the city director wrote
    narrator.budget.set_notifier(_admin_notifier(ui))
    logging.getLogger().addHandler(AdminErrorHandler(_admin_notifier(ui)))
    feed.install(ui)
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    jobs.register(scheduler, ui)
    scheduler.add_job(_restart_watch, "interval", seconds=20, args=[dp], id="restart_watch")
    from app.services import unity_jobs  # owner request 31.10: the Unity client's builds (docs/PLAN_UNITY.md)

    scheduler.add_job(unity_jobs.watch, "interval", seconds=15, id="unity_watch", max_instances=1)
    scheduler.start()

    await ui.start()  # group id, cleanup pickup, idle-window sweeper
    # never raises: a failure only costs the "/" menu; admins get their commands in the group and in private
    await register_commands(bot, ui.group_id, set(ui.settings.admin_ids) | set(ui.bootstrap_admins))
    await _revive_once(ui)  # owner request 24.10: Kenji Torres, killed by the games of the rich, back by the Trauma Team
    await _news_once(ui)  # owner request 24.10: one post of the new, detailed news right after this deploy

    try:
        # owner report 24.10: the Mac's link drops long idle requests. A short long-poll (10 s) and a short request
        # timeout (25 + 10 s) notice a dead request in seconds, not in a minute and a half; a failed poll retries fast.
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), polling_timeout=NET_POLL_S,
                               backoff_config=BackoffConfig(min_delay=0.5, max_delay=5.0, factor=1.5, jitter=0.1))
    finally:
        await ui.stop()
        scheduler.shutdown(wait=False)
        await futurebeats.drain()
        await narrator.stop()
        await db.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
