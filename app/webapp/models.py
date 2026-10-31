"""Tables of the Mini App, on the game's declarative Base: `db.create_all()` at startup creates them as soon as this
module is imported (app/webapp/boot.py imports it before app.main runs)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class WebappOp(Base):
    """One action sent by the Mini App: the op id makes a retried request return the stored answer instead of
    running the action twice."""

    __tablename__ = "webapp_ops"
    __table_args__ = {"extend_existing": True}

    op_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    player_tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(16))
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class WebappScreen(Base):
    """The last rendered screen of a player's window, so the Mini App can show it after a bot restart."""

    __tablename__ = "webapp_screens"
    __table_args__ = {"extend_existing": True}

    player_tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    screen: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
