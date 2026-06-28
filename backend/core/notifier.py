"""Push-уведомления в Telegram (fire-and-forget).

Шлём напрямую через Telegram Bot API по httpx (он уже в зависимостях бэкенда) —
без aiogram, чтобы не тянуть фреймворк в торговое ядро.

Если TELEGRAM_BOT_TOKEN не задан — все вызовы молча игнорируются.
Падение Telegram API не влияет на торговую логику: отправка идёт фоновой
задачей и не блокирует caller."""
from __future__ import annotations

import asyncio
import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Сильные ссылки на висящие задачи — иначе event loop держит лишь слабые ссылки
# и задача может быть собрана GC до отправки (fire-and-forget gotcha).
_pending: set[asyncio.Task] = set()


async def _send(telegram_id: int, text: str) -> None:
    token = settings.telegram_bot_token
    if not token:
        logger.warning("Уведомление пропущено: TELEGRAM_BOT_TOKEN не задан в бэкенде")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"chat_id": telegram_id, "text": text})
        if resp.status_code != 200:
            logger.warning(
                "Telegram отклонил уведомление telegram_id=%s: %s %s",
                telegram_id, resp.status_code, resp.text[:200],
            )
    except Exception:
        logger.warning("Не удалось отправить уведомление telegram_id=%s", telegram_id, exc_info=True)


def notify(telegram_id: int | None, text: str) -> None:
    """Отправить уведомление не блокируя caller (fire-and-forget)."""
    if not telegram_id:
        return
    try:
        task = asyncio.create_task(_send(telegram_id, text))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except RuntimeError:
        pass  # нет event loop (тесты / CLI)
