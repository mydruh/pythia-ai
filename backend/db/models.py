"""ORM-модели (схема из CLAUDE.md п.6 + user management)."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


# ── enums ──────────────────────────────────────────────────────

class MarketStatus(str, enum.Enum):
    open = "open"
    closed = "closed"


class Verdict(str, enum.Enum):
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    NEUTRAL = "NEUTRAL"


class TradeMode(str, enum.Enum):
    paper = "paper"
    live = "live"


class PositionStatus(str, enum.Enum):
    open = "open"
    closed = "closed"


class SessionStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    settling = "settling"     # период истёк, ждём реальной резолюции открытых позиций
    completed = "completed"   # все позиции урегулированы, средства возвращены в кошелёк
    stopped = "stopped"       # остановлен пользователем вручную


# ── models ─────────────────────────────────────────────────────

class User(Base):
    """Пользователь Telegram. Кошелёк: virtual_balance — свободные средства,
    starting_balance — всего внесено. Сами автоторговли — в TradingSession."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Кошелёк
    virtual_balance: Mapped[float] = mapped_column(Float, default=0.0)   # свободные (нераспределённые) средства
    starting_balance: Mapped[float] = mapped_column(Float, default=0.0)  # всего внесено (для общей доходности)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sessions: Mapped[list["TradingSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    positions: Mapped[list["Position"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class TradingSession(Base):
    """Автоторговля («бот»): своя категория, бюджет, период, баланс и метрики.
    Бюджет резервируется из кошелька юзера; при остановке возвращается обратно."""

    __tablename__ = "trading_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)  # None = все категории

    starting_balance: Mapped[float] = mapped_column(Float)   # выделенный бюджет
    balance: Mapped[float] = mapped_column(Float)            # текущий баланс бота

    sim_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sim_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[SessionStatus] = mapped_column(Enum(SessionStatus), default=SessionStatus.active, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="sessions")
    positions: Mapped[list["Position"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class Market(Base):
    """Рынок (лот) с Gamma API."""

    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    question: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)  # заголовок события + описание
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[MarketStatus] = mapped_column(Enum(MarketStatus), default=MarketStatus.open)
    url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tokens: Mapped[list["MarketToken"]] = relationship(back_populates="market", cascade="all, delete-orphan")
    analyses: Mapped[list["Analysis"]] = relationship(back_populates="market")
    positions: Mapped[list["Position"]] = relationship(back_populates="market")


class MarketToken(Base):
    """Токен исхода (Yes/No). Торговля идёт по token_id."""

    __tablename__ = "market_tokens"

    token_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    outcome: Mapped[str] = mapped_column(String(120))
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    market: Mapped["Market"] = relationship(back_populates="tokens")


class Analysis(Base):
    """Решение анализатора по одному прогону модели (глобально, не привязано к юзеру)."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("trading_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    token_id: Mapped[str] = mapped_column(ForeignKey("market_tokens.token_id"))
    model: Mapped[str] = mapped_column(String(80))
    market_prob: Mapped[float] = mapped_column(Float)
    my_prob: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float)
    verdict: Mapped[Verdict] = mapped_column(Enum(Verdict))
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    market: Mapped["Market"] = relationship(back_populates="analyses")


class Position(Base):
    """Виртуальная позиция пользователя (привязана к user_id)."""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("trading_sessions.id", ondelete="CASCADE"), index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    token_id: Mapped[str] = mapped_column(ForeignKey("market_tokens.token_id"))
    side: Mapped[str] = mapped_column(String(20))        # YES / NO
    size_usdc: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    mode: Mapped[TradeMode] = mapped_column(Enum(TradeMode), default=TradeMode.paper)
    status: Mapped[PositionStatus] = mapped_column(Enum(PositionStatus), default=PositionStatus.open)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)  # AI reasoning для UI
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)

    user: Mapped["User"] = relationship(back_populates="positions")
    session: Mapped["TradingSession"] = relationship(back_populates="positions")
    market: Mapped["Market"] = relationship(back_populates="positions")


class Resolution(Base):
    """Исход закрытого рынка (для расчёта точности Brier/hit rate)."""

    __tablename__ = "resolutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), unique=True, index=True)
    winning_outcome: Mapped[String] = mapped_column(String(120))
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppState(Base):
    """Глобальное состояние приложения (key-value). Создаётся через create_all — дроп не нужен."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
