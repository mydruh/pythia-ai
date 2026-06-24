"""Fetcher — тянет открытые лоты с публичного Gamma API и нормализует их.

Gamma API не требует авторизации. Берём активные нерешённые рынки,
парсим исходы (outcomes) и токены (clobTokenIds), сохраняем в БД.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import Market, MarketStatus, MarketToken

logger = logging.getLogger(__name__)


# Категория выводится из тегов события. Порядок важен (приоритет сверху вниз):
# политика раньше «world», т.к. выборные события часто имеют оба тега.
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("Politics", ["politic", "election", "trump", "biden", "geopolit", "senate", "congress", "government"]),
    ("Crypto", ["crypto", "bitcoin", "ethereum", "btc", "eth", "solana", "memecoin"]),
    ("Sports", ["sport", "soccer", "football", "nfl", "nba", "mlb", "nhl", "tennis", "ufc", "f1", "cricket", "world cup", "olympic"]),
    ("Economy", ["econ", "business", "fed", "inflation", "interest rate", "gdp", "stocks", "finance", "recession"]),
    ("Tech", ["tech", " ai", "artificial intelligence", "openai", "spacex", "elon"]),
    ("Culture", ["culture", "entertainment", "movie", "music", "celebrity", "awards", "oscar", "grammy"]),
    ("Science", ["science", "climate", "space", "health", "covid", "weather"]),
    ("World", ["world", "global", "geo", "war", "ukraine", "israel", "china"]),
]


def pick_category(tags: list[dict[str, Any]] | None) -> str | None:
    """Подобрать топ-категорию по тегам события. None — если не распознано (→ «Прочее»)."""
    if not tags:
        return None
    labels = " ".join((t.get("label") or "").lower() for t in tags)
    labels = f" {labels} "  # рамки для совпадений вроде ' ai '
    for canonical, keys in CATEGORY_RULES:
        if any(k in labels for k in keys):
            return canonical
    return None


async def fetch_raw_events(page_size: int = 100) -> list[dict[str, Any]]:
    """Получить ВСЕ активные события с Gamma API через пагинацию offset.
    API ограничен offset <= 2000, при выходе за предел возвращает ошибку — это сигнал стопа."""
    all_events: list[dict[str, Any]] = []
    offset = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            params: dict[str, Any] = {
                "active": "true",
                "closed": "false",
                "archived": "false",
                "limit": page_size,
                "offset": offset,
                "order": "volume",
                "ascending": "false",
            }
            resp = await client.get(f"{settings.gamma_api_url}/events", params=params)
            if not resp.is_success:
                break  # API вернул ошибку — исчерпали доступный диапазон
            data = resp.json()
            if isinstance(data, dict) and "error" in data:
                break
            page = data["data"] if isinstance(data, dict) and "data" in data else data
            if not isinstance(page, list) or len(page) == 0:
                break
            all_events.extend(page)
            if len(page) < page_size:
                break  # последняя страница
            offset += page_size

    logger.info("Gamma вернул %d событий (страниц: %d)", len(all_events), offset // page_size + 1)
    return all_events


def normalize_market(
    raw: dict[str, Any],
    category: str | None,
    event_title: str | None = None,
    event_description: str | None = None,
) -> dict[str, Any] | None:
    """Привести сырой рынок к внутреннему виду. None — если данных не хватает.
    category берётся из тегов родительского события."""
    condition_id = raw.get("conditionId")
    question = raw.get("question")
    if not condition_id or not question:
        return None

    outcomes = _maybe_json_list(raw.get("outcomes"))
    prices = _maybe_json_list(raw.get("outcomePrices"))
    token_ids = _maybe_json_list(raw.get("clobTokenIds"))
    if not outcomes or not token_ids or len(outcomes) != len(token_ids):
        return None

    tokens = []
    for i, outcome in enumerate(outcomes):
        price = _safe_float(prices[i]) if i < len(prices) else None
        tokens.append({"token_id": str(token_ids[i]), "outcome": str(outcome), "last_price": price})

    # Собираем контекст события для AI: заголовок + описание события.
    # Это то, чего нет в голом question ("Will Morocco win on 2026-06-19?") —
    # здесь есть соперник, турнир, правила разрешения.
    description_parts = []
    if event_title:
        description_parts.append(event_title)
    if event_description:
        # Обрезаем до ~500 символов — правила резолюции AI не нужны
        description_parts.append(event_description[:500])
    description = "\n".join(description_parts) if description_parts else None

    return {
        "condition_id": str(condition_id),
        "question": str(question),
        "description": description,
        "category": category,
        "close_time": _parse_dt(raw.get("endDate")),
        "volume": _safe_float(raw.get("volume") or raw.get("volumeNum")),
        "url": f"https://polymarket.com/market/{raw.get('slug')}" if raw.get("slug") else None,
        "tokens": tokens,
    }


def normalize_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Развернуть событие в список нормализованных рынков с категорией из тегов."""
    category = pick_category(event.get("tags"))
    event_title = event.get("title") or None
    event_description = event.get("description") or None
    out = []
    for raw_market in event.get("markets") or []:
        norm = normalize_market(raw_market, category, event_title, event_description)
        if norm is not None:
            out.append(norm)
    return out


async def save_markets(session: AsyncSession, normalized: list[dict[str, Any]]) -> int:
    """Upsert рынков и их токенов. Возвращает число сохранённых рынков."""
    saved = 0
    for n in normalized:
        existing = await session.scalar(
            select(Market).where(Market.condition_id == n["condition_id"])
        )
        if existing is None:
            market = Market(
                condition_id=n["condition_id"],
                question=n["question"],
                description=n.get("description"),
                category=n["category"],
                close_time=n["close_time"],
                volume=n["volume"],
                status=MarketStatus.open,
                url=n["url"],
            )
            session.add(market)
            await session.flush()  # получить market.id
        else:
            market = existing
            market.volume = n["volume"]
            market.close_time = n["close_time"]
            market.category = n["category"]
            if n.get("description"):
                market.description = n["description"]
            market.fetched_at = datetime.utcnow()
            # Рынок снова в активном листинге (Polymarket отдаёт только closed=false)
            # → он торгуется. Если ранее ошибочно пометили closed по дате — вернуть open.
            if market.status == MarketStatus.closed:
                market.status = MarketStatus.open

        for t in n["tokens"]:
            tok = await session.get(MarketToken, t["token_id"])
            if tok is None:
                session.add(
                    MarketToken(
                        token_id=t["token_id"],
                        market_id=market.id,
                        outcome=t["outcome"],
                        last_price=t["last_price"],
                    )
                )
            else:
                tok.last_price = t["last_price"]
        saved += 1

    await session.commit()
    logger.info("Сохранено %d рынков", saved)
    return saved


async def run_fetch(session: AsyncSession) -> set[str]:
    """Полный цикл: скачать ВСЕ события -> развернуть в рынки -> сохранить.

    Возвращает множество condition_id, реально присутствующих в активном листинге
    Polymarket в этом прогоне. Рынки НЕ из этого множества (с истёкшей датой) —
    кандидаты на пометку closed (они пропали из листинга = вероятно зарезолвились)."""
    events = await fetch_raw_events()
    normalized: list[dict[str, Any]] = []
    for event in events:
        normalized.extend(normalize_event(event))
    await save_markets(session, normalized)
    return {n["condition_id"] for n in normalized}


async def fetch_token_quote(token_id: str) -> tuple[float | None, float | None] | None:
    """Получить (best_bid, best_ask) токена из CLOB-стакана.

    best_bid — цена, по которой можно ПРОДАТЬ (выход из лонга). None если нет покупателей.
    best_ask — цена, по которой можно КУПИТЬ (вход / закрытие шорта). None если нет продавцов.

    Сам результат None — стакан целиком пуст / ошибка сети. Каждая сторона может
    быть None по отдельности: тогда соответствующая операция недоступна."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.clob_api_url}/book",
                params={"token_id": token_id},
            )
            if not resp.is_success:
                return None
            book = resp.json()
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            if not bids and not asks:
                return None  # стакан пуст — рынок не торгуется
            # best_bid = максимальная цена покупателя, best_ask = минимальная цена продавца.
            bid_prices = [p for b in bids if (p := _safe_float(b.get("price")))]
            ask_prices = [p for a in asks if (p := _safe_float(a.get("price")))]
            best_bid = max(bid_prices) if bid_prices else None
            best_ask = min(ask_prices) if ask_prices else None
            return best_bid, best_ask
    except Exception:
        return None


async def fetch_token_resolution(condition_id: str, token_id: str) -> float | None:
    """Запросить финальную цену резолюции токена с Gamma API.

    closed=true ОБЯЗАТЕЛЕН: зарезолвившиеся/архивированные рынки по умолчанию из
    /markets не отдаются (вернётся пусто). С closed=true приходит финальный исход
    (outcomePrices = ["1","0"] и umaResolutionStatus=resolved). Если рынок ещё не
    закрыт реально — вернётся пусто → None → позиция держится до настоящей резолюции."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.gamma_api_url}/markets",
                params={"condition_ids": condition_id, "closed": "true"},
            )
            if not resp.is_success:
                return None
            data = resp.json()
            markets: list[Any] = data if isinstance(data, list) else data.get("data", [data] if isinstance(data, dict) else [])
            for market in markets:
                token_ids = _maybe_json_list(market.get("clobTokenIds"))
                prices = _maybe_json_list(market.get("outcomePrices"))
                for i, tid in enumerate(token_ids):
                    if str(tid) == str(token_id) and i < len(prices):
                        return _safe_float(prices[i])
    except Exception:
        pass
    return None


async def fetch_market_winner(condition_id: str) -> str | None:
    """Победивший исход зарезолвившегося рынка (строка outcome, напр. 'Yes'/'No'/'Spirit').

    closed=true — чтобы получить архивированные/закрытые рынки. Возвращает outcome,
    у которого финальная цена ≥ 0.98 (токен-победитель → 1). None, если рынок ещё не
    зарезолвился (ни один токен не у 1) или при ошибке — тогда резолюцию не фиксируем."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.gamma_api_url}/markets",
                params={"condition_ids": condition_id, "closed": "true"},
            )
            if not resp.is_success:
                return None
            data = resp.json()
            markets: list[Any] = data if isinstance(data, list) else data.get("data", [data] if isinstance(data, dict) else [])
            for market in markets:
                outcomes = _maybe_json_list(market.get("outcomes"))
                prices = _maybe_json_list(market.get("outcomePrices"))
                for i, outcome in enumerate(outcomes):
                    price = _safe_float(prices[i]) if i < len(prices) else None
                    if price is not None and price >= 0.98:
                        return str(outcome)
    except Exception:
        pass
    return None


# ── helpers ──────────────────────────────────────────────────────
def _maybe_json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
