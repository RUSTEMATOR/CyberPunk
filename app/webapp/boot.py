"""Service entry point: `python -m app.webapp.boot` (deploy/service.sh). Installs the Mini App hooks
(app/webapp/runtime.py) and runs the bot exactly as `python -m app.main` does. If the hooks cannot be installed the
bot still starts, without the app."""

from __future__ import annotations

import asyncio
import logging


def _install() -> None:
    try:
        from app.webapp import runtime

        runtime.install()
    except Exception:
        logging.getLogger("app.webapp.boot").exception("webapp: hooks not installed; the bot runs without the Mini App")


if __name__ == "__main__":
    _install()
    from app import main as bot_main

    asyncio.run(bot_main.main())
