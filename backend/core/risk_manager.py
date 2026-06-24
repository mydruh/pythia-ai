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

    # ── размер позиции ──────────────────────────────────────────
    def _position_size(
        self,
        bankroll: float,
        win_prob: float | None,
        price: float | None,
    ) -> float:
        """Базовый размер позиции (до лимитов экспозиции).

        fixed — фикс-процент банка. kelly — дробный Келли по перевесу/цене контракта.

        Kelly для бинарного контракта по цене `price` (выплата 1 при выигрыше):
            f* = (win_prob − price) / (1 − price)
        где win_prob — вероятность выигрыша ИМЕННО покупаемого токена (модель),
        price — цена контракта (доля банка по полному Келли). Берём kelly_fraction
        от f*. Размер НИКОГДА не превышает фикс-потолок risk_max_position_pct —
        Келли может только уменьшить ставку (защита от переразмера на неточных оценках).
        """
        fixed = bankroll * settings.risk_max_position_pct
        if settings.sizing_mode != "kelly":
            return fixed
        # Нет данных для Келли или вырожденная цена — безопасный фолбэк на фикс-процент.
        if win_prob is None or price is None or not (0.0 < price < 1.0):
            return fixed
        kelly_full = (win_prob - price) / (1.0 - price)
        if kelly_full <= 0.0:
            return 0.0
        size = bankroll * settings.kelly_fraction * kelly_full
        return min(size, fixed)   # Келли только УМЕНЬШАЕТ относительно потолка

    # ── одобрение сделки ────────────────────────────────────────
    def approve(
        self,
        *,
        bankroll: float,
        current_exposure: float,
        edge: float,
        market_volume: float | None,
        win_prob: float | None = None,
        price: float | None = None,
    ) -> RiskDecision:
        """Решить, можно ли открывать позицию и какого размера.

        win_prob/price нужны только для sizing_mode="kelly" (вероятность выигрыша
        покупаемого токена и цена контракта). В режиме "fixed" игнорируются."""
        if abs(edge) < settings.risk_min_edge:
            return RiskDecision(False, 0.0, f"edge {edge:+.3f} ниже порога {settings.risk_min_edge}")

        if market_volume is not None and market_volume < settings.risk_min_volume:
            return RiskDecision(False, 0.0, f"объём {market_volume:.0f} ниже порога {settings.risk_min_volume:.0f}")

        size = self._position_size(bankroll, win_prob, price)
        if size <= 0:
            return RiskDecision(False, 0.0, "размер по сайзингу = 0 (нет перевеса для Kelly)")

        max_exposure = bankroll * settings.risk_max_exposure_pct
        room = max_exposure - current_exposure
        if room <= 0:
            return RiskDecision(False, 0.0, "достигнут лимит суммарной экспозиции")

        size = min(size, room)
        if size <= 0:
            return RiskDecision(False, 0.0, "нет свободного лимита под позицию")

        return RiskDecision(True, round(size, 2),
                            f"ok ({settings.sizing_mode}): edge {edge:+.3f}, size {size:.2f}")
