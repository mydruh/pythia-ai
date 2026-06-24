"""Провайдеро-независимый интерфейс анализа.

Каждый провайдер реализует analyze(market) -> AnalysisResult.
Это позволяет гонять несколько моделей на одних лотах и сравнивать точность.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AnalysisResult:
    """Единый формат ответа любого LLM-провайдера."""

    prob: float          # оценка моделью вероятности исхода (0..1)
    verdict: str         # BUY_YES | BUY_NO | NEUTRAL
    reasoning: str       # обоснование (для отчёта пользователю)
    model: str           # какая модель дала ответ


class AnalyzerProvider(abc.ABC):
    """Базовый класс LLM-провайдера анализа."""

    name: str

    @abc.abstractmethod
    async def analyze(self, market: dict[str, Any]) -> AnalysisResult:
        """Проанализировать лот и вернуть оценку вероятности + вердикт.

        Args:
            market: нормализованный словарь рынка
                {question, market_prob, outcome, close_time, volume, ...}
        """
        raise NotImplementedError


class NoLLMProvider(AnalyzerProvider):
    """Заглушка когда нет API-ключа. Всегда возвращает NEUTRAL — сделок не будет,
    цикл не падает. Логирует предупреждение один раз."""

    name = "no-llm"
    model = "no-llm"

    def __init__(self, missing_key_name: str) -> None:
        import logging
        logging.getLogger(__name__).warning(
            "⚠️  %s не задан — LLM-анализ отключён. Добавьте ключ в .env и перезапустите бэкенд.",
            missing_key_name,
        )

    async def analyze(self, market: dict[str, Any]) -> AnalysisResult:
        return AnalysisResult(
            prob=market.get("market_prob", 0.5),
            verdict="NEUTRAL",
            reasoning="LLM недоступен: добавьте GROQ_API_KEY или XAI_API_KEY в .env",
            model="no-llm",
        )
