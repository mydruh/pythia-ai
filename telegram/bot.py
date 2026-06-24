"""Telegram-бот Pythia (aiogram 3) — ТОНКАЯ обёртка над Mini App.

Вся логика (кошелёк, создание ботов, пауза/стоп, позиции, статистика) живёт в
React Mini App поверх FastAPI. Бот лишь:
  /start  — гарантирует кошелёк юзера и открывает дашборд
  /open   — кнопка запуска Mini App
  /status — краткая сводка по кошельку (read-only, детали — в Mini App)

Управление ботами (создать/пауза/возобновить/остановить) намеренно НЕ в боте:
оно в Mini App, чтобы не дублировать логику и не расходиться с API (см. CLAUDE.md).
"""
from __future__ import annotations

import logging
import os

import httpx
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "http://localhost:5173")

# Общий секрет для server-to-server вызовов API (бот -> бэкенд). В проде ОБЯЗАТЕЛЕН:
# при ALLOW_UNVERIFIED_AUTH=false без него /status получит 401.
INTERNAL_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")
API_HEADERS = {"X-Internal-Token": INTERNAL_TOKEN} if INTERNAL_TOKEN else {}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _webapp_kb(uid: int, text: str = "📊 Открыть дашборд") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, web_app=WebAppInfo(url=f"{WEBAPP_URL}?uid={uid}")),
    ]])


async def _ensure_wallet(msg: Message) -> bool:
    """Идемпотентно создать кошелёк юзера (без депозита — депозит задаётся в Mini App).
    Возвращает True при успехе. /users/start — открытый эндпоинт (без auth)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{API_BASE}/users/start", json={
                "telegram_id": msg.from_user.id,
                "username": msg.from_user.username,
                "first_name": msg.from_user.first_name,
            })
        return resp.status_code == 200
    except Exception:
        logger.exception("Не удалось создать кошелёк для %s", msg.from_user.id)
        return False


# ── /start ───────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    await _ensure_wallet(msg)
    await msg.answer(
        f"Привет, {msg.from_user.first_name}! 👋\n\n"
        "Я — *Pythia*, торговый агент на Polymarket.\n\n"
        "Открой дашборд, задай виртуальный депозит и запусти бота — он будет "
        "анализировать рынки через AI и делать ставки, а ты увидишь сколько он "
        "заработал (или потерял) и *почему* выбрал каждый лот.\n\n"
        "Всё виртуально — реальных денег нет.",
        parse_mode="Markdown",
        reply_markup=_webapp_kb(msg.from_user.id, "🚀 Открыть Pythia"),
    )


# ── /open ────────────────────────────────────────────────────────

@dp.message(Command("open"))
async def cmd_open(msg: Message) -> None:
    await _ensure_wallet(msg)
    await msg.answer("Открыть дашборд:", reply_markup=_webapp_kb(msg.from_user.id))


# ── /status (read-only сводка по кошельку) ───────────────────────

@dp.message(Command("status"))
async def cmd_status(msg: Message) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{API_BASE}/users/{msg.from_user.id}/stats", headers=API_HEADERS
            )
    except Exception:
        await msg.answer("⚠️ Бэкенд недоступен, попробуй позже.")
        return

    if resp.status_code == 404:
        await msg.answer(
            "Кошелёк ещё не создан. Нажми /start, затем открой дашборд и запусти бота.",
            reply_markup=_webapp_kb(msg.from_user.id, "🚀 Открыть Pythia"),
        )
        return
    if resp.status_code != 200:
        await msg.answer("⚠️ Не удалось получить статистику.")
        return

    s = resp.json()
    pnl_emoji = "📈" if s["total_return_pct"] >= 0 else "📉"
    await msg.answer(
        f"*Кошелёк Pythia*\n\n"
        f"💼 Капитал (equity): *{s['equity']:.2f} USDC*\n"
        f"💰 Свободно: {s['free_balance']:.2f} · в ботах: {s['bots_balance']:.2f}\n"
        f"📥 Внесено: {s['total_deposited']:.2f}\n"
        f"{pnl_emoji} Доходность: *{s['total_return_pct']:+.1f}%*\n"
        f"   реализованный: {s['realized_pnl']:+.2f} · "
        f"нереализованный: {s['unrealized_pnl']:+.2f}\n\n"
        f"🤖 Активных ботов: {s['active_sessions']}\n"
        f"📂 Открытых позиций: {s['open_positions']} · "
        f"✅ закрытых: {s['closed_positions']}",
        parse_mode="Markdown",
        reply_markup=_webapp_kb(msg.from_user.id, "📊 Подробный дашборд"),
    )


# ── entry point ──────────────────────────────────────────────────

async def main() -> None:
    logger.info("Запуск бота Pythia (тонкая обёртка над Mini App)...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
