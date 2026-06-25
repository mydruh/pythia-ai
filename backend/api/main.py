"""FastAPI — REST API поверх ядра.

Эндпоинты:
  /users/{tid}                         — кошелёк юзера (баланс, депозит, агрегат)
  /users/{tid}/sessions                — автоторговли (создать / список)
  /users/{tid}/sessions/{sid}/…        — данные конкретной автоторговли
  /markets                             — лоты из БД (фильтр/сорт/пагинация)
  /categories                          — список категорий
  /analyses                            — глобальные решения анализатора
  /cycle/run                           — ручной запуск цикла (дебаг)
  /health                              — статус
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_user
from config import settings
from core.fetcher import fetch_token_quote
from core.providers import describe_decision
from db.models import (
    Analysis, AppState, Market, MarketStatus, MarketToken, Position, PositionStatus,
    Resolution, SessionStatus, TradingSession, User,
)
from db.session import async_session_factory, init_db
from scheduler import settle_session, trading_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

NO_CATEGORY = "Прочее"   # бакет для рынков без категории


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Вычислить время следующего цикла на основе последнего запуска из БД.
    # Это гарантирует соблюдение интервала даже после перезапуска контейнера.
    async with async_session_factory() as s:
        state = await s.get(AppState, "last_cycle_at")

    now = datetime.now(timezone.utc)
    if state and state.value:
        last_run = datetime.fromisoformat(state.value)
        next_run = last_run + timedelta(minutes=settings.cycle_interval_minutes)
        if next_run <= now:
            next_run = now   # уже просрочен — запустить немедленно
            logger.info("Планировщик: прошлый цикл был %s, интервал истёк → запуск немедленно", last_run.isoformat())
        else:
            logger.info("Планировщик: прошлый цикл был %s → следующий в %s", last_run.isoformat(), next_run.isoformat())
    else:
        next_run = now   # первый запуск когда-либо
        logger.info("Планировщик: первый запуск → немедленно")

    scheduler.add_job(
        trading_cycle.run_once,
        "interval",
        minutes=settings.cycle_interval_minutes,
        id="trading_cycle",
        next_run_time=next_run,
    )
    # Частая лёгкая проверка стоп-лосса (без LLM) — между тяжёлыми циклами,
    # чтобы ловить просадку за минуту, а не за полчаса.
    scheduler.add_job(
        trading_cycle.run_stop_loss_check,
        "interval",
        seconds=settings.stop_loss_check_seconds,
        id="stop_loss_check",
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Pythia", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Telegram Mini App открывается с любого origin
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)


# ── health / debug ──────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mode": settings.trading_mode,
        "filter_model": settings.filter_model,
        "decision": describe_decision(),
        "max_active_sessions": settings.max_active_sessions,
    }


@app.post("/cycle/run")
async def run_cycle_now() -> dict:
    await trading_cycle.run_once()
    return {"status": "done"}


@app.get("/cycle/next")
async def cycle_next() -> dict:
    """Время следующего планового торгового цикла (для таймера в UI).
    Считаем от последнего запуска (AppState) + интервал; если данных нет — null."""
    async with async_session_factory() as session:
        state = await session.get(AppState, "last_cycle_at")
    next_at = None
    if state and state.value:
        next_at = datetime.fromisoformat(state.value) + timedelta(minutes=settings.cycle_interval_minutes)
    return {
        "next_cycle_at": next_at.isoformat() if next_at else None,
        "interval_minutes": settings.cycle_interval_minutes,
    }


# ── wallet (user) ────────────────────────────────────────────────

class StartWalletRequest(BaseModel):
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    deposit: float = 0.0        # первоначальный депозит (только при создании)


@app.post("/users/start")
async def start_wallet(req: StartWalletRequest) -> dict:
    """Создать кошелёк юзера (идемпотентно). Депозит применяется только при создании."""
    async with async_session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == req.telegram_id))
        if user is None:
            user = User(telegram_id=req.telegram_id)
            session.add(user)
            if req.deposit > 0:
                user.starting_balance = req.deposit
                user.virtual_balance = req.deposit
        user.username = req.username
        user.first_name = req.first_name
        await session.commit()
        await session.refresh(user)
    return _user_dict(user)


@app.get("/users/{telegram_id}")
async def get_user(telegram_id: int, _auth: int = Depends(require_user)) -> dict:
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)
        return _user_dict(user)


class DepositRequest(BaseModel):
    amount: float


@app.post("/users/{telegram_id}/deposit")
async def deposit(telegram_id: int, req: DepositRequest, _auth: int = Depends(require_user)) -> dict:
    """Пополнение кошелька. Это внесённый капитал (не прибыль) — растут и свободные
    средства, и общий внесённый депозит (чтобы доходность не искажалась)."""
    if req.amount <= 0:
        raise HTTPException(400, "amount должен быть > 0")
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)
        user.virtual_balance += req.amount
        user.starting_balance += req.amount
        await session.commit()
        await session.refresh(user)
        return _user_dict(user)


@app.get("/users/{telegram_id}/stats")
async def user_stats(telegram_id: int, _auth: int = Depends(require_user)) -> dict:
    """Агрегат кошелька: свободные средства + балансы ботов + рыночная стоимость позиций."""
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)

        # Балансы активных ботов.
        bots_balance = await session.scalar(
            select(func.coalesce(func.sum(TradingSession.balance), 0.0))
            .where(TradingSession.user_id == user.id,
                   TradingSession.status.in_([SessionStatus.active, SessionStatus.paused, SessionStatus.settling]))
        )
        active_sessions = await session.scalar(
            select(func.count()).select_from(TradingSession)
            .where(TradingSession.user_id == user.id, TradingSession.status == SessionStatus.active)
        )

        open_count, closed_count, realized, open_value, unrealized = await _positions_agg(
            session, Position.user_id == user.id
        )

        free = user.virtual_balance
        equity = free + float(bots_balance or 0.0) + open_value
        deposited = user.starting_balance
        total_return_pct = ((equity - deposited) / deposited * 100) if deposited > 0 else 0.0

        return {
            "telegram_id": telegram_id,
            "free_balance": round(free, 2),
            "bots_balance": round(float(bots_balance or 0.0), 2),
            "equity": round(equity, 2),
            "total_deposited": round(deposited, 2),
            "realized_pnl": round(realized, 4),
            "unrealized_pnl": round(unrealized, 4),
            "total_return_pct": round(total_return_pct, 2),
            "active_sessions": int(active_sessions or 0),
            "open_positions": open_count,
            "closed_positions": closed_count,
        }


@app.get("/users/{telegram_id}/positions")
async def user_positions(
    telegram_id: int, status: str | None = None, _auth: int = Depends(require_user)
) -> list[dict]:
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)
        return await _positions_list(session, Position.user_id == user.id, status)


@app.get("/users/{telegram_id}/pnl_history")
async def pnl_history(telegram_id: int, _auth: int = Depends(require_user)) -> list[dict]:
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)
        return await _pnl_history(session, Position.user_id == user.id)


# ── trading sessions (боты) ──────────────────────────────────────

class CreateSessionRequest(BaseModel):
    name: str | None = None
    category: str | None = None     # None = все категории
    budget: float
    days: int


@app.post("/users/{telegram_id}/sessions")
async def create_session(
    telegram_id: int, req: CreateSessionRequest, _auth: int = Depends(require_user)
) -> dict:
    """Запустить автоторговлю. Бюджет резервируется из свободных средств кошелька."""
    if req.budget <= 0 or req.days <= 0:
        raise HTTPException(400, "budget и days должны быть > 0")
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)

        active_count = await session.scalar(
            select(func.count()).select_from(TradingSession)
            .where(TradingSession.user_id == user.id, TradingSession.status == SessionStatus.active)
        )
        if int(active_count or 0) >= settings.max_active_sessions:
            raise HTTPException(400, f"Достигнут лимит активных автоторговель ({settings.max_active_sessions})")

        if user.virtual_balance < req.budget:
            raise HTTPException(400, f"Недостаточно свободных средств: {user.virtual_balance:.2f} < {req.budget:.2f}")

        now = datetime.now(timezone.utc)
        category = req.category or None   # None = торговать по всем категориям
        ts = TradingSession(
            user_id=user.id,
            name=req.name,
            category=category,
            starting_balance=req.budget,
            balance=req.budget,
            sim_start=now,
            sim_end=now + timedelta(days=req.days),
            status=SessionStatus.active,
        )
        user.virtual_balance -= req.budget
        session.add(ts)
        await session.commit()
        await session.refresh(ts)
        result = await _session_metrics(session, ts)

    # Запустить цикл досрочно — новый бот не должен ждать до следующего планового цикла.
    # Используем create_task напрямую: если цикл уже идёт, run_once() установит
    # _needs_extra_run=True и после завершения текущего цикла запустится ещё раз.
    asyncio.create_task(trading_cycle.run_once())
    logger.info("Новая сессия %d — цикл запущен (или запланирован после текущего)", ts.id)

    return result


@app.get("/users/{telegram_id}/sessions")
async def list_sessions(telegram_id: int, _auth: int = Depends(require_user)) -> list[dict]:
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)
        rows = await session.scalars(
            select(TradingSession)
            .where(TradingSession.user_id == user.id)
            .order_by(desc(TradingSession.created_at))
        )
        return [await _session_metrics(session, ts) for ts in rows.all()]


@app.get("/users/{telegram_id}/sessions/{session_id}")
async def session_stats(
    telegram_id: int, session_id: int, _auth: int = Depends(require_user)
) -> dict:
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)
        ts = await _get_session_or_404(session, user, session_id)
        return await _session_metrics(session, ts)


@app.get("/users/{telegram_id}/sessions/{session_id}/positions")
async def session_positions(
    telegram_id: int, session_id: int, status: str | None = None, _auth: int = Depends(require_user)
) -> list[dict]:
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)
        ts = await _get_session_or_404(session, user, session_id)
        return await _positions_list(session, Position.session_id == ts.id, status)


@app.get("/users/{telegram_id}/sessions/{session_id}/analyses")
async def session_analyses(
    telegram_id: int, session_id: int, response: Response,
    limit: int = 20, offset: int = 0, _auth: int = Depends(require_user)
) -> list[dict]:
    """Что бот рассматривает/рассматривал. Пагинация (limit/offset) + X-Total-Count
    для ленивой подгрузки старых записей. verdict=NEUTRAL — «рассмотрел, но не вошёл»."""
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)
        ts = await _get_session_or_404(session, user, session_id)
        total = await session.scalar(
            select(func.count()).select_from(Analysis).where(Analysis.session_id == ts.id)
        )
        response.headers["X-Total-Count"] = str(int(total or 0))
        rows = await session.execute(
            select(Analysis, Market, MarketToken)
            .join(Market, Market.id == Analysis.market_id)
            .join(MarketToken, MarketToken.token_id == Analysis.token_id)
            .where(Analysis.session_id == ts.id)
            .order_by(desc(Analysis.created_at))
            .limit(limit)
            .offset(offset)
        )
        analyses = rows.all()
        # Рынки, по которым реально открылась позиция в этой сессии.
        # Сопоставляем по market_id, а НЕ token_id: при BUY_NO (и нормализации O/U,
        # команд) позиция открывается на противоположном токене, чем записан в анализе.
        # В сессии на один рынок приходится максимум одна позиция.
        positioned = set(await session.scalars(
            select(Position.market_id).where(Position.session_id == ts.id)
        ))
        return [_analysis_dict(a, m, t, a.market_id in positioned) for a, m, t in analyses]


@app.get("/users/{telegram_id}/sessions/{session_id}/pnl_history")
async def session_pnl_history(
    telegram_id: int, session_id: int, _auth: int = Depends(require_user)
) -> list[dict]:
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)
        ts = await _get_session_or_404(session, user, session_id)
        return await _pnl_history(session, Position.session_id == ts.id)


@app.post("/users/{telegram_id}/sessions/{session_id}/pause")
async def session_pause(
    telegram_id: int, session_id: int, _auth: int = Depends(require_user)
) -> dict:
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)
        ts = await _get_session_or_404(session, user, session_id)
        if ts.status == SessionStatus.active:
            ts.status = SessionStatus.paused
            await session.commit()
        return await _session_metrics(session, ts)


@app.post("/users/{telegram_id}/sessions/{session_id}/resume")
async def session_resume(
    telegram_id: int, session_id: int, _auth: int = Depends(require_user)
) -> dict:
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)
        ts = await _get_session_or_404(session, user, session_id)
        if ts.status == SessionStatus.paused:
            ts.status = SessionStatus.active
            await session.commit()
        return await _session_metrics(session, ts)


@app.post("/users/{telegram_id}/sessions/{session_id}/stop")
async def session_stop(
    telegram_id: int, session_id: int, _auth: int = Depends(require_user)
) -> dict:
    """Остановить автоторговлю: закрыть позиции, вернуть средства в кошелёк."""
    async with async_session_factory() as session:
        user = await _get_user_or_404(session, telegram_id)
        ts = await _get_session_or_404(session, user, session_id)
        if ts.status in (SessionStatus.active, SessionStatus.paused, SessionStatus.settling):
            await settle_session(session, trading_cycle.executor, ts, status=SessionStatus.stopped)
        return await _session_metrics(session, ts)


# ── markets / categories / analyses (глобальные) ────────────────

@app.get("/markets")
async def list_markets(
    response: Response,
    category: str | None = None,
    sort: str = "volume",
    limit: int = 30,
    offset: int = 0,
) -> list[dict]:
    async with async_session_factory() as session:
        base = select(Market).where(Market.status == MarketStatus.open)
        count_q = select(func.count()).select_from(Market).where(Market.status == MarketStatus.open)
        if category and category != NO_CATEGORY:
            base = base.where(Market.category == category)
            count_q = count_q.where(Market.category == category)
        elif category == NO_CATEGORY:
            base = base.where(Market.category.is_(None))
            count_q = count_q.where(Market.category.is_(None))

        order = Market.close_time.asc() if sort == "closing" else desc(Market.volume)
        base = base.order_by(order).limit(limit).offset(offset)

        total = await session.scalar(count_q)
        response.headers["X-Total-Count"] = str(int(total or 0))

        rows = await session.scalars(base)
        return [
            {
                "id": m.id,
                "question": m.question,
                "category": m.category,
                "volume": m.volume,
                "close_time": m.close_time.isoformat() if m.close_time else None,
                "url": m.url,
            }
            for m in rows
        ]


@app.get("/categories")
async def list_categories() -> list[dict]:
    """Категории, присутствующие в БД, со счётчиками. null → «Прочее»."""
    async with async_session_factory() as session:
        rows = await session.execute(
            select(Market.category, func.count())
            .where(Market.status == MarketStatus.open)
            .group_by(Market.category)
        )
        result = []
        for cat, cnt in rows.all():
            result.append({"category": cat or NO_CATEGORY, "count": int(cnt)})
        result.sort(key=lambda x: -x["count"])
        return result


@app.get("/analyses")
async def list_analyses(limit: int = 50) -> list[dict]:
    async with async_session_factory() as session:
        rows = await session.scalars(select(Analysis).order_by(desc(Analysis.created_at)).limit(limit))
        return [_analysis_dict(a) for a in rows.all()]


@app.get("/accuracy")
async def accuracy() -> dict:
    """Метрики точности по моделям: бьёт ли ИИ рынок (Brier model vs market), hit rate.

    Считаем из analyses + resolutions. Каждый анализ — точка (model, my_prob, исход).
    Сравниваем калибровку модели с рыночной ценой на тот же момент:
    Brier_model < Brier_market → модель калибрована лучше рынка (есть edge)."""
    async with async_session_factory() as session:
        rows = (await session.execute(
            select(Analysis, Resolution.winning_outcome, MarketToken.outcome)
            .join(Resolution, Resolution.market_id == Analysis.market_id)
            .join(MarketToken, MarketToken.token_id == Analysis.token_id)
        )).all()

        # Аккумуляторы по моделям.
        acc: dict[str, dict] = {}
        for a, winning_outcome, analyzed_outcome in rows:
            # my_prob/market_prob теперь в координатах анализируемого исхода
            # (P(этот исход)). Истина — разрешился ли ИМЕННО этот исход.
            actual = 1.0 if analyzed_outcome == winning_outcome else 0.0

            s = acc.setdefault(a.model, {"n": 0, "sum_bm": 0.0, "sum_bk": 0.0, "hits": 0})
            s["n"] += 1
            s["sum_bm"] += (a.my_prob - actual) ** 2          # Brier модели
            s["sum_bk"] += (a.market_prob - actual) ** 2      # Brier рынка
            # Hit: направление модели совпало с исходом.
            if (a.my_prob >= 0.5) == (actual >= 0.5):
                s["hits"] += 1

        per_model = []
        for model, s in sorted(acc.items()):
            n = s["n"]
            brier_model = round(s["sum_bm"] / n, 4)
            brier_market = round(s["sum_bk"] / n, 4)
            per_model.append({
                "model": model,
                "n": n,
                "brier_model": brier_model,
                "brier_market": brier_market,
                "beats_market": brier_model < brier_market,
                "brier_edge": round(brier_market - brier_model, 4),  # >0 = модель лучше рынка
                "hit_rate": round(s["hits"] / n, 4),
            })

        total_scored = sum(s["n"] for s in acc.values())
        resolved_markets = await session.scalar(select(func.count()).select_from(Resolution))
        return {
            "scored_predictions": total_scored,
            "resolved_markets": int(resolved_markets or 0),
            "by_model": per_model,
        }


# ── helpers ──────────────────────────────────────────────────────

def _user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "starting_balance": round(user.starting_balance, 2),
        "virtual_balance": round(user.virtual_balance, 2),
    }


def _block_reason(a: Analysis, market: Market | None, has_position: bool) -> str | None:
    """Почему BUY-сигнал НЕ открыл позицию. Реконструкция ПЕРВОГО сработавшего гейта
    в том же порядке, что и scheduler: убеждённость → edge → объём → лимиты.
    None — если позиция открылась или вердикт NEUTRAL (модель сама отказалась)."""
    if a.verdict.value == "NEUTRAL" or has_position:
        return None
    if abs(a.my_prob - 0.5) < settings.risk_min_conviction:
        lo, hi = 0.5 - settings.risk_min_conviction, 0.5 + settings.risk_min_conviction
        return (f"Модель не уверена: её оценка {a.my_prob:.0%} близка к 50% "
                f"(для входа нужно ≤{lo:.0%} или ≥{hi:.0%})")
    if abs(a.edge) < settings.risk_min_edge:
        return f"Edge {a.edge:+.1%} меньше порога ±{settings.risk_min_edge:.0%}"
    if market is not None and market.volume is not None and market.volume < settings.risk_min_volume:
        return f"Объём рынка ниже порога {settings.risk_min_volume:.0f} USDC"
    return "Лимит риска: экспозиция бота или число ставок на это событие"


def _analysis_dict(
    a: Analysis,
    market: Market | None = None,
    token: MarketToken | None = None,
    has_position: bool = False,
) -> dict:
    return {
        "id": a.id,
        "session_id": a.session_id,
        "market_id": a.market_id,
        "model": a.model,
        "market_prob": a.market_prob,
        "my_prob": a.my_prob,
        "edge": round(a.edge, 4),
        "verdict": a.verdict.value,
        "has_position": has_position,
        "block_reason": _block_reason(a, market, has_position),
        "reasoning": a.reasoning,
        "created_at": a.created_at.isoformat(),
        "market_question": market.question if market else None,
        "market_url": market.url if market else None,
        "token_outcome": token.outcome if token else None,
    }


def _unrealized(p: Position) -> float | None:
    """Нереализованный P&L. Позиция ВСЕГДА лонг удерживаемого токена (и YES, и NO —
    просто держим разные токены), поэтому формула единая, как в executor.close_position:
    купили shares = size/entry, текущая стоимость по current_price. Убыток ≤ ставки."""
    if p.current_price is None:
        return None
    shares = p.size_usdc / p.entry_price
    return (p.current_price - p.entry_price) * shares


def _position_dict(p: Position, m: Market, token: MarketToken | None = None) -> dict:
    unrealized = None
    if p.status == PositionStatus.open:
        u = _unrealized(p)
        unrealized = round(u, 4) if u is not None else None
    # Показываем название исхода только если это не банальный Yes/No
    raw_outcome = token.outcome if token else None
    token_outcome = raw_outcome if raw_outcome and raw_outcome.lower() not in ("yes", "no") else None
    return {
        "id": p.id,
        "session_id": p.session_id,
        "market_question": m.question,
        "market_url": m.url,
        "side": p.side,
        "token_outcome": token_outcome,   # название команды/исхода для не-бинарных рынков
        "size_usdc": p.size_usdc,
        "entry_price": p.entry_price,
        "current_price": p.current_price,
        "unrealized_pnl": unrealized,
        "pnl": p.pnl,
        "status": p.status.value,
        "reasoning": p.reasoning,
        "opened_at": p.opened_at.isoformat(),
        "closed_at": p.closed_at.isoformat() if p.closed_at else None,
    }


async def _positions_agg(session: AsyncSession, where_clause):
    """Возвращает (open_count, closed_count, realized_pnl, open_value, unrealized).
    open_value = сколько вернётся при закрытии открытых позиций сейчас (size + unrealized)."""
    open_count = await session.scalar(
        select(func.count()).select_from(Position).where(where_clause, Position.status == PositionStatus.open)
    )
    closed_count = await session.scalar(
        select(func.count()).select_from(Position).where(where_clause, Position.status == PositionStatus.closed)
    )
    realized = await session.scalar(
        select(func.coalesce(func.sum(Position.pnl), 0.0)).where(where_clause, Position.status == PositionStatus.closed)
    )
    open_positions = await session.scalars(
        select(Position).where(where_clause, Position.status == PositionStatus.open)
    )
    open_value = 0.0
    unrealized = 0.0
    for p in open_positions.all():
        u = _unrealized(p) or 0.0
        unrealized += u
        open_value += p.size_usdc + u
    return int(open_count or 0), int(closed_count or 0), float(realized or 0.0), open_value, unrealized


# Кеш для лёгкого обновления цен на /positions: token_id -> monotonic-время
# последнего запроса к стакану. TTL гасит частый опрос UI, чтобы не долбить Polymarket.
_PRICE_REFRESH_TTL = 30.0
_price_refresh_at: dict[str, float] = {}


async def _refresh_open_prices(session: AsyncSession, positions: list[Position]) -> None:
    """Подтянуть живые цены открытых позиций из стакана при запросе /positions.

    Кешируем по токену (TTL 30с) и тянем параллельно — чтобы частый опрос UI не
    нагружал Polymarket и не тормозил ответ. Это лишь свежие цифры в интерфейсе;
    защита денег держится на ФОНОВОЙ задаче стоп-лосса (run_stop_loss_check)."""
    now = time.monotonic()
    stale = [
        p for p in positions
        if p.status == PositionStatus.open
        and (now - _price_refresh_at.get(p.token_id, 0.0)) >= _PRICE_REFRESH_TTL
    ]
    if not stale:
        return
    quotes = await asyncio.gather(
        *(fetch_token_quote(p.token_id) for p in stale), return_exceptions=True
    )
    changed = False
    for p, q in zip(stale, quotes):
        _price_refresh_at[p.token_id] = now
        if isinstance(q, BaseException) or q is None:
            continue
        bid, ask = q
        mid = (bid + ask) / 2.0 if (bid is not None and ask is not None) else (bid if bid is not None else ask)
        if mid is not None:
            p.current_price = mid
            changed = True
    if changed:
        await session.commit()


async def _positions_list(session: AsyncSession, where_clause, status: str | None) -> list[dict]:
    stmt = (
        select(Position, Market, MarketToken)
        .join(Market, Market.id == Position.market_id)
        .join(MarketToken, MarketToken.token_id == Position.token_id)
        .where(where_clause)
        .order_by(desc(Position.opened_at))
    )
    if status:
        stmt = stmt.where(Position.status == PositionStatus(status))
    rows = await session.execute(stmt)
    rows_all = rows.all()
    # Освежить цены открытых позиций из живого стакана (с кешем) — для актуального
    # P&L в UI. Падение запроса не должно ронять эндпоинт.
    try:
        await _refresh_open_prices(session, [p for p, _m, _t in rows_all])
    except Exception:
        logger.warning("Не удалось обновить цены на /positions", exc_info=True)
    return [_position_dict(p, m, t) for p, m, t in rows_all]


async def _pnl_history(session: AsyncSession, where_clause) -> list[dict]:
    rows = await session.scalars(
        select(Position).where(where_clause, Position.status == PositionStatus.closed).order_by(Position.closed_at)
    )
    cumulative = 0.0
    result = []
    for p in rows.all():
        cumulative += p.pnl or 0.0
        result.append({
            "closed_at": p.closed_at.isoformat() if p.closed_at else None,
            "pnl": round(p.pnl or 0.0, 4),
            "cumulative_pnl": round(cumulative, 4),
        })
    return result


async def _session_metrics(session: AsyncSession, ts: TradingSession) -> dict:
    open_count, closed_count, realized, open_value, unrealized = await _positions_agg(
        session, Position.session_id == ts.id
    )
    terminal = ts.status in (SessionStatus.completed, SessionStatus.stopped)
    if terminal:
        # Баланс уже возвращён в кошелёк (ts.balance = 0), восстанавливаем итог из P&L.
        equity = ts.starting_balance + realized
    else:
        equity = ts.balance + open_value
    total_return_pct = ((equity - ts.starting_balance) / ts.starting_balance * 100) if ts.starting_balance > 0 else 0.0
    return {
        "id": ts.id,
        "name": ts.name,
        "category": ts.category,   # None = все категории (фронт показывает «Все»)
        "status": ts.status.value,
        "starting_balance": round(ts.starting_balance, 2),
        "balance": round(ts.balance, 2),
        "equity": round(equity, 2),
        "realized_pnl": round(realized, 4),
        "unrealized_pnl": round(unrealized, 4),
        "total_return_pct": round(total_return_pct, 2),
        "open_positions": open_count,
        "closed_positions": closed_count,
        "sim_start": ts.sim_start.isoformat() if ts.sim_start else None,
        "sim_end": ts.sim_end.isoformat() if ts.sim_end else None,
    }


async def _get_user_or_404(session: AsyncSession, telegram_id: int) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if not user:
        raise HTTPException(404, "Пользователь не найден")
    return user


async def _get_session_or_404(session: AsyncSession, user: User, session_id: int) -> TradingSession:
    ts = await session.get(TradingSession, session_id)
    if not ts or ts.user_id != user.id:
        raise HTTPException(404, "Автоторговля не найдена")
    return ts
