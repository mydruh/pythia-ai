"""Фабрика LLM-провайдеров анализа."""
from __future__ import annotations

import logging

from config import settings
from core.providers.base import AnalysisResult, AnalyzerProvider, NoLLMProvider
from core.providers.openai_compatible import OpenAICompatibleProvider

logger = logging.getLogger(__name__)

__all__ = [
    "AnalysisResult",
    "AnalyzerProvider",
    "get_provider",
    "get_filter_provider",
    "get_groq_decider",
    "describe_decision",
]


def get_filter_provider() -> AnalyzerProvider:
    """Дешёвый/быстрый слой фильтрации (Groq, открытые модели)."""
    if not settings.groq_api_key.strip():
        return NoLLMProvider("GROQ_API_KEY")
    return OpenAICompatibleProvider(
        name="groq",
        api_key=settings.groq_api_key,
        base_url="https://api.groq.com/openai/v1",
        model=settings.filter_model,
    )


def get_groq_decider() -> AnalyzerProvider:
    """Слой решений на Groq (бесплатный baseline / фоллбэк)."""
    if not settings.groq_api_key.strip():
        return NoLLMProvider("GROQ_API_KEY")
    return OpenAICompatibleProvider(
        name="groq",
        api_key=settings.groq_api_key,
        base_url="https://api.groq.com/openai/v1",
        model=settings.groq_decision_model,
    )


def get_provider(name: str | None = None) -> AnalyzerProvider:
    """Слой решений. По умолчанию — из конфига (Grok).

    Если у выбранного провайдера нет ключа — откатываемся на Groq, чтобы можно
    было проверить работоспособность пайплайна end-to-end бесплатно. Решение
    помечается реальной моделью Groq (см. AnalysisResult.model), так что в БД/UI
    видно, кто на самом деле принял решение.
    """
    name = name or settings.decision_provider

    if name == "grok":
        if settings.xai_api_key.strip():
            return OpenAICompatibleProvider(
                name="grok",
                api_key=settings.xai_api_key,
                base_url="https://api.x.ai/v1",
                model=settings.decision_model,
            )
        logger.warning(
            "XAI_API_KEY пуст — слой решений откатывается на Groq (%s). "
            "Это режим проверки работоспособности: НЕ делай выводов об edge по этим прогнозам.",
            settings.groq_decision_model,
        )
        return get_groq_decider()

    if name == "claude":
        if settings.anthropic_api_key.strip():
            # Отдельный SDK — реализуем при включении A/B (Фаза 4+).
            raise NotImplementedError("Claude-провайдер будет добавлен на этапе A/B")
        logger.warning(
            "ANTHROPIC_API_KEY пуст — слой решений откатывается на Groq (%s).",
            settings.groq_decision_model,
        )
        return get_groq_decider()

    if name == "groq":
        return get_groq_decider()

    raise ValueError(f"Неизвестный провайдер: {name}")


def describe_decision() -> dict:
    """Какой провайдер/модель реально обслуживает слой решений (для /health и UI).

    Логику разрешения держим в синхроне с get_provider().
    """
    name = settings.decision_provider
    if name == "grok" and settings.xai_api_key.strip():
        return {"provider": "grok", "model": settings.decision_model, "fallback": False}
    if name == "claude" and settings.anthropic_api_key.strip():
        return {"provider": "claude", "model": settings.decision_model, "fallback": False}
    return {
        "provider": "groq",
        "model": settings.groq_decision_model,
        "fallback": name != "groq",  # True = выбран grok/claude, но нет ключа
    }
