"""Mini App settings, read from the same .env as the bot (prefix WEBAPP_). Kept apart from app/config.py so the app
package stands on its own (docs/WEBAPP.md, "Self-contained package")."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import BASE_DIR


class WebappSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", env_prefix="WEBAPP_",
                                      extra="ignore")

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8787
    url: str = "https://nightcity.qa-temple-of-serenity.cc"
    direct_link: str = ""  # t.me/<bot>/<app> from BotFather /newapp: opens the app straight from the group


@lru_cache(maxsize=1)
def get() -> WebappSettings:
    return WebappSettings()
