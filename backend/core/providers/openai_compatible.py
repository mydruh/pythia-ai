"""Провайдер на базе OpenAI SDK — общий для Groq и Grok (xAI).

Оба провайдера OpenAI-совместимы: отличается только base_url + ключ.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openai import AsyncOpenAI, RateLimitError

from core.providers.base import AnalysisResult, AnalyzerProvider
from core.providers.prompt import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)
_MAX_RETRIES = 3
_RETRY_DELAYS = (15, 30, 60)


class OpenAICompatibleProvider(AnalyzerProvider):
    def __init__(self, *, name: str, api_key: str, base_url: str, model: str) -> None:
        self.name = name
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def analyze(self, market: dict[str, Any]) -> AnalysisResult:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_user_prompt(market)},
                    ],
                    temperature=0.2,
                    response_format={"type": "json_object"},
                )
                break
            except RateLimitError:
                if attempt >= _MAX_RETRIES:
                    raise
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "429 от %s (попытка %d/%d) — ждём %ds",
                    self.name, attempt + 1, _MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
        raw = resp.choices[0].message.content or "{}"
        data = _safe_json(raw)

        prob = _clamp(float(data.get("prob", market.get("market_prob", 0.5))))
        verdict = str(data.get("verdict", "NEUTRAL")).upper()
        if verdict not in ("BUY_YES", "BUY_NO", "NEUTRAL"):
            verdict = "NEUTRAL"

        return AnalysisResult(
            prob=prob,
            verdict=verdict,
            reasoning=str(data.get("reasoning", "")).strip(),
            model=self.model,
        )


def _safe_json(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Модель иногда оборачивает в ```json ... ```
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
