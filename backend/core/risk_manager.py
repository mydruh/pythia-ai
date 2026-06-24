"""Risk manager — ХАРДКОД-предохранители от слива банка (CLAUDE.md п.7).

Эти лимиты — не «по желанию», а защита капитала. Любая сделка проходит
через approve(); просадка проверяется отдельно и ОСТАНАВЛИВАЕТ бота.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import settings


@dataclass(slots=True)
class RiskDecision:
    allowed: bool
    size_usdc: float          # рекомендованный размер позиции (0 если запрет)
    reason: str


class DrawdownHalt(Exception):
    """Бот достиг лимита просадки и должен остановиться, позвав человека."""


class RiskManager:
    def __init__(self, starting_bankroll: float | None = None) -> None:
        self.starting_bankroll = starting_bankroll or settings.starting_bankroll

    # ── проверка просадки ───────────────────────────────────────
    def check_drawdown(self, current_equity: float) -> None:
        """Бросает DrawdownHalt при просадке >= лимита."""
        threshold = self.starting_bankroll * (1 - settings.risk_max_drawdown_pct)
        if current_equity <= threshold:
            raise DrawdownHalt(
                f"Просадка достигла лимита: equity={current_equity:.2f} "
                f"<= {threshold:.2f} (-{settings.risk_max_drawdown_pct:.0%}). "
                f"Бот остановлен, требуется вмешательство человека."
            )

    # ── одобрение сделки ────────────────────────────────────────
    def approve(
        self,
        *,
        bankroll: float,
        current_exposure: float,
        edge: float,
        market_volume: float | None,
    ) -> RiskDecision:
        """Решить, можно ли открывать позицию и какого размера."""
        if abs(edge) < settings.risk_min_edge:
            return RiskDecision(False, 0.0, f"edge {edge:+.3f} ниже порога {settings.risk_min_edge}")

        if market_volume is not None and market_volume < settings.risk_min_volume:
            return RiskDecision(False, 0.0, f"объём {market_volume:.0f} ниже порога {settings.risk_min_volume:.0f}")

        # Размер по фикс-проценту от банка (Kelly — позже, осторожно).
        size = bankroll * settings.risk_max_position_pct

        max_exposure = bankroll * settings.risk_max_exposure_pct
        room = max_exposure - current_exposure
        if room <= 0:
            return RiskDecision(False, 0.0, "достигнут лимит суммарной экспозиции")

        size = min(size, room)
        if size <= 0:
            return RiskDecision(False, 0.0, "нет свободного лимита под позицию")

        return RiskDecision(True, round(size, 2), f"ok: edge {edge:+.3f}, size {size:.2f}")
