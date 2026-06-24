"""Executor — исполнение виртуальных сделок (paper).

Каждая позиция привязана к автоторговле (TradingSession). Баланс бота
обновляется сразу. Live-режим (Фаза 6) — NotImplementedError до подключения CLOB.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import Position, PositionStatus, TradeMode, TradingSession

logger = logging.getLogger(__name__)


class Executor:
    def __init__(self) -> None:
        self.mode = settings.trading_mode

    async def open_position(
        self,
        session: AsyncSession,
        *,
        trading_session: TradingSession,
        market_id: int,
        token_id: str,
        side: str,
        size_usdc: float,
        entry_price: float,
        reasoning: str = "",
    ) -> Position | None:
        """Открыть позицию. Списывает size_usdc с баланса бота."""
        if trading_session.balance < size_usdc:
            logger.info(
                "session %d: недостаточно баланса (%.2f < %.2f)",
                trading_session.id, trading_session.balance, size_usdc,
            )
            return None

        if self.mode == "live":
            await self._submit_live_order(token_id=token_id, side=side, size_usdc=size_usdc, price=entry_price)

        trading_session.balance -= size_usdc

        position = Position(
            user_id=trading_session.user_id,
            session_id=trading_session.id,
            market_id=market_id,
            token_id=token_id,
            side=side,
            size_usdc=size_usdc,
            entry_price=entry_price,
            current_price=entry_price,
            mode=TradeMode(self.mode),
            status=PositionStatus.open,
            reasoning=reasoning,
        )
        session.add(position)
        await session.commit()
        await session.refresh(position)
        logger.info(
            "[%s] session=%d OPEN %s token=%s size=%.2f @ %.3f",
            self.mode, trading_session.id, side, token_id, size_usdc, entry_price,
        )
        return position

    async def close_position(
        self,
        session: AsyncSession,
        position: Position,
        exit_price: float,
    ) -> Position:
        """Закрыть позицию, рассчитать P&L, вернуть средства на баланс бота.

        Позиция ВСЕГДА лонг удерживаемого токена (и для YES, и для NO — просто
        держим разные токены). Поэтому формула единая: купили shares = size/entry,
        продали по exit. Убыток не может превысить ставку (exit >= 0)."""
        shares = position.size_usdc / position.entry_price if position.entry_price else 0.0
        pnl = (exit_price - position.entry_price) * shares

        position.status = PositionStatus.closed
        position.closed_at = datetime.now(timezone.utc)
        position.current_price = exit_price
        position.pnl = round(pnl, 4)

        trading_session = await session.get(TradingSession, position.session_id)
        if trading_session:
            trading_session.balance += position.size_usdc + pnl

        await session.commit()
        logger.info("CLOSE pos=%d pnl=%.4f", position.id, pnl)
        return position

    async def _submit_live_order(self, *, token_id: str, side: str, size_usdc: float, price: float) -> None:
        raise NotImplementedError("Live-исполнение: подключить py-clob-client и POLYMARKET_PRIVATE_KEY.")
