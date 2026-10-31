from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

# Sonnet 5.5 and Haiku 5.5 (the default MODEL_NARRATIVE / MODEL_FAST), USD per 1M tokens
DEFAULT_PRICES: dict[str, float] = {
    "price_narrative_in": 2.0, "price_narrative_out": 10.0, "price_fast_in": 0.1, "price_fast_out": 0.5,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # keep ADMIN_IDS as a plain string, it is parsed below
        enable_decoding=False,
    )

    bot_token: str = ""
    anthropic_api_key: str = ""
    admin_ids: set[int] = set()
    group_chat_id: int | None = None
    db_path: str = "data/game.db"
    images_dir: str = "images"
    timezone: str = "Europe/Kyiv"
    monthly_budget_usd: float = 100.0
    model_narrative: str = "claude-sonnet-5-5"
    model_fast: str = "claude-haiku-5-5"
    price_narrative_in: float = DEFAULT_PRICES["price_narrative_in"]
    price_narrative_out: float = DEFAULT_PRICES["price_narrative_out"]
    price_fast_in: float = DEFAULT_PRICES["price_fast_in"]
    price_fast_out: float = DEFAULT_PRICES["price_fast_out"]
    # Cache prices as multiples of the input price (docs: a 5-minute write costs 1.25x; a read 0.1x, 0.05x on Sonnet 5.5).
    price_cache_write_mult: float = 1.25
    price_cache_read_mult: float = 0.1
    llm_enabled: bool = True

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v: object) -> set[int]:
        if v is None or v == "":
            return set()
        if isinstance(v, (set, list, tuple)):
            return {int(x) for x in v}
        return {int(x) for x in str(v).replace(";", ",").split(",") if x.strip()}

    @field_validator("group_chat_id", mode="before")
    @classmethod
    def _parse_group(cls, v: object) -> int | None:
        if v is None or str(v).strip() == "":
            return None
        return int(v)

    @field_validator(
        "price_narrative_in",
        "price_narrative_out",
        "price_fast_in",
        "price_fast_out",
        mode="before",
    )
    @classmethod
    def _empty_price(cls, v: object, info) -> float:  # noqa: ANN001
        """An empty price falls back to the published price of the default models (USD per 1M tokens, checked on
        the pricing page in October 2026), so the monthly budget guard always counts real money."""
        if v is None or str(v).strip() == "":
            return DEFAULT_PRICES[info.field_name]
        return float(v)

    @field_validator("price_cache_write_mult", "price_cache_read_mult", mode="before")
    @classmethod
    def _empty_mult(cls, v: object, info) -> float:  # noqa: ANN001
        if v is None or str(v).strip() == "":
            return 1.25 if info.field_name == "price_cache_write_mult" else 0.1
        return float(v)

    @property
    def llm_configured(self) -> bool:
        """The narrator can call Claude: enabled in .env, a key is present and the budget is positive."""
        return self.llm_enabled and bool(self.anthropic_api_key.strip()) and self.monthly_budget_usd > 0

    @property
    def images_path(self) -> Path:
        p = Path(self.images_dir)
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else BASE_DIR / p


def load_settings() -> Settings:
    return Settings()
