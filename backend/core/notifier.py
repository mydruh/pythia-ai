"""Push-уведомления в Telegram (fire-and-forget).

Если TELEGRAM_BOT_TOKEN не задан — все вызовы молча игнорируются.
Падение Telegram API не влияет на торговую логику: отправка идёт через
asyncio.create_task и не блокирует caller."""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_bot = None


def _get_bot():
    global _bot
    if _bot is None:
        try:
            from config import settings
            if settings.telegram_bot_token:
                from aiogram import Bot
                _bot = Bot(token=settings.telegram_bot_token)
        except Exception:
            logger.warning("Не удалось инициализировать Telegram Bot для уведомлений")
    return _bot


async def _send(telegram_id: int, text: str) -> None:
    bot = _get_bot()
    if bot is None:
        return
    try:
        # parse_mode=None — plain text, чтобы спецсимволы в вопросах лотов
        # (_, (, ., *) не ломали парсер и сообщение доходило в любом случае.
        await bot.send_message(chat_id=telegram_id, text=text, parse_mode=None)
    except Exception:
        logger.warning("Не удалось отправить уведомление telegram_id=%d", telegram_id, exc_info=True)


def notify(telegram_id: int | None, text: str) -> None:
    """Отправить уведомление не блокируя caller (fire-and-forget)."""
    if not telegram_id:
        return
    try:
        asyncio.create_task(_send(telegram_id, text))
    except RuntimeError:
        pass  # нет event loop (тесты / CLI)
