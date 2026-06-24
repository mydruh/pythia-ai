"""Анализатор — провайдеро-независимая оркестрация edge-логики.

Двухуровневая схема (тиринг):
  1. Фильтрация (дешёвый Groq) — прогнать все лоты, отсеять неинтересные.
  2. Решение (Grok) — глубокий разбор финалистов.

Возвращает AnalysisResult; запись в БД делает вызывающий код (scheduler).
"""
from __future__ import annotations

import logging
from typing import Any

from config import settings
from core.providers import (
    AnalysisResult,
    get_filter_provider,
    get_groq_decider,
    get_provider,
)

logger = logging.getLogger(__name__)


class Analyzer:
    def __init__(self) -> None:
        self._filter = get_filter_provider()
        self._decider = get_provider()
        # Резервный декейдер на Groq: если основной провайдер (напр. xAI) упадёт
        # в рантайме, откатимся на него, чтобы не валить весь торговый цикл.
        self._fallback = None if self._decider.name == "groq" else get_groq_decider()

        logger.info(
            "Analyzer готов: фильтр=%s(%s), решение=%s(%s)%s",
            self._filter.name, self._filter.model,
            self._decider.name, self._decider.model,
            f", фоллбэк=groq({self._fallback.model})" if self._fallback else "",
        )

    async def filter_market(self, market: dict[str, Any]) -> AnalysisResult:
        """Быстрая дешёвая оценка для отсева. При сбое провайдера — NEUTRAL, цикл не падает."""
        try:
            return await self._filter.analyze(market)
        except Exception as exc:
            logger.error(
                "Фильтр-провайдер (%s) недоступен: %s — добавьте GROQ_API_KEY в .env",
                self._filter.name, exc,
            )
            # Возвращаем prob = market_prob → edge = 0 → лот не пройдёт фильтр has_edge
            return AnalysisResult(
                prob=market.get("market_prob", 0.5),
                verdict="NEUTRAL",
                reasoning="LLM недоступен — нет API-ключа",
                model="no-llm",
            )

    async def decide(self, market: dict[str, Any]) -> AnalysisResult:
        """Глубокая оценка финалиста. При сбое основного провайдера — откат на Groq."""
        try:
            return await self._decider.analyze(market)
        except Exception:
            if self._fallback is None:
                raise
            logger.warning(
                "Основной декейдер %s(%s) упал — откат на Groq(%s).",
                self._decider.name, self._decider.model, self._fallback.model,
                exc_info=True,
            )
            return await self._fallback.analyze(market)

    @staticmethod
    def edge(result: AnalysisResult, market_prob: float) -> float:
        return result.prob - market_prob

    @staticmethod
    def has_edge(result: AnalysisResult, market_prob: float) -> bool:
        return abs(result.prob - market_prob) >= settings.risk_min_edge

    @staticmethod
    def has_conviction(result: AnalysisResult) -> bool:
        """Есть ли у модели реальное мнение: оценка достаточно далека от 0.5.
        Близко к 0.5 на бинаре = «не знаю» → edge относительно рынка иллюзорен
        (модель не видит того, что заложено в цену). См. risk_min_conviction."""
        return abs(result.prob - 0.5) >= settings.risk_min_conviction
