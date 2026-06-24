# Pythia — Polymarket AI Trading Bot

Автономный торговый агент для Polymarket. Архитектура и правила — в [`CLAUDE.md`](./CLAUDE.md).
Старт всегда в **paper-режиме**; переход на live — один флаг `TRADING_MODE`.

## Статус

- **Фаза 0 — Каркас** ✅ структура, конфиг, модели БД, заглушки ядра, docker-compose.
- **Фаза 1 — Данные** ✅ `fetcher` тянет реальные лоты с Gamma API в БД (проверено).
- **Фаза 2 — Анализ** 🟡 каркас провайдеров (Groq/Grok) и `analyzer` готовы; нужны ключи.
- **Фаза 3 — Paper** 🟡 `risk_manager` + `executor(paper)` + `scheduler` собраны.
- Фазы 4–6 — впереди (учёт точности, UI, live).

## Структура

```
backend/
├── config.py            # pydantic settings (.env)
├── core/
│   ├── fetcher.py        # лоты с Gamma API → БД
│   ├── analyzer.py       # оркестрация фильтр→решение
│   ├── providers/        # base + openai_compatible (Groq/Grok), фабрика
│   ├── risk_manager.py   # хардкод-лимиты, drawdown halt
│   └── executor.py       # paper | live
├── db/                   # base, session, models (SQLAlchemy async)
├── scheduler.py          # торговый цикл (APScheduler)
└── api/main.py           # FastAPI (REST + старт планировщика)
```

## Запуск (локально)

```bash
cp .env.example .env          # заполнить GROQ_API_KEY, XAI_API_KEY
docker compose up -d db       # Postgres
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
uvicorn api.main:app --reload # http://localhost:8000/docs
```

Полностью в Docker: `docker compose up --build`.

## Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| GET  | `/health`     | статус, режим, halt |
| POST | `/cycle/run`  | прогнать торговый цикл вручную |
| GET  | `/markets`    | лоты из БД |
| GET  | `/analyses`   | решения анализатора |
| GET  | `/positions`  | позиции (paper/live) |
| GET  | `/stats`      | экспозиция, PnL, число позиций |

## Дальше

1. Заполнить `.env` ключами Groq/xAI → запустить `POST /cycle/run`, проверить запись в `analyses`.
2. Трекинг исходов (`resolutions`) + Brier/hit rate (Фаза 4).
3. React-дашборд (Фаза 5).

> Миграции: для быстрого старта используется `init_db()` (create_all). Для prod — Alembic
> (зависимость уже в `requirements.txt`).
