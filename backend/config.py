"""Конфигурация проекта (pydantic settings). Источник — переменные окружения / .env."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM providers ───────────────────────────────────────────
    groq_api_key: str = ""
    xai_api_key: str = ""
    anthropic_api_key: str = ""

    # ── Auth ────────────────────────────────────────────────────
    telegram_bot_token: str = ""        # для проверки подписи initData Mini App
    internal_api_token: str = ""        # общий секрет для server-to-server (бот -> API)
    # true — разрешить запросы без проверки initData (локальная разработка/браузер).
    # В проде ОБЯЗАТЕЛЬНО false.
    allow_unverified_auth: bool = False

    # ── Polymarket / Polygon ────────────────────────────────────
    polymarket_private_key: str = ""
    polymarket_funder: str = ""

    # ── Database ────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://pythia:pythia@localhost:5432/pythia"

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        """Привести URL managed-Postgres к async-драйверу asyncpg.

        PaaS (Railway/Render/Heroku) отдают DATABASE_URL вида `postgres://…`
        или `postgresql://…` (psycopg/libpq). Нашему async-движку нужен
        `postgresql+asyncpg://…` — иначе create_async_engine падает на старте.
        Также срезаем libpq-параметр sslmode из query: asyncpg его не понимает
        (SSL он согласует сам). Если драйвер уже указан — не трогаем."""
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            v = "postgresql+asyncpg://" + v[len("postgresql://"):]

        parts = urlsplit(v)
        if parts.query:
            kept = [(k, val) for k, val in parse_qsl(parts.query) if k != "sslmode"]
            v = urlunsplit(parts._replace(query=urlencode(kept)))
        return v

    # ── Trading mode & risk limits ──────────────────────────────
    trading_mode: Literal["paper", "live"] = "paper"
    risk_max_position_pct: float = 0.05
    risk_max_exposure_pct: float = 0.90
    risk_max_drawdown_pct: float = 0.20
    # Автоматически возобновить бота из паузы, если equity восстановился выше
    # порога просадки (открытые позиции сыграли в плюс). Если false — пауза
    # снимается только вручную через UI.
    risk_auto_resume: bool = True
    risk_min_edge: float = 0.10
    # Минимальная УБЕЖДЁННОСТЬ модели: |my_prob − 0.5|. Если оценка близка к 0.5,
    # у модели НЕТ реального мнения (на бинаре 0.5 = «монетка / не знаю»). Большой
    # edge относительно крайней цены рынка тогда иллюзорен: модель не видит того,
    # что знает рынок (свежие данные, knowledge cutoff). Такие лоты пропускаем,
    # даже если формальный edge велик. Спасает от ставок на шум (часовой Bitcoin
    # Up/Down и т.п., где модель сама пишет «вероятность роста/падения равна»).
    risk_min_conviction: float = 0.10
    risk_min_volume: float = 1000.0
    # Диапазон цены анализируемого токена (market_prob) для входа.
    # Ниже минимума — малая ликвидность и огромный спред.
    # Выше максимума — рынок уже почти решён, реального upside нет.
    risk_min_market_prob: float = 0.30
    risk_max_market_prob: float = 0.85
    # Минимум времени до закрытия рынка для ВХОДА. Рынок, резолвящийся раньше,
    # стоп-лосс/переоценка не успеют отработать (он закроется между 30-мин циклами
    # → возможен гэп цены в ноль без шанса выйти). Отсекаем почасовые/событийные
    # рынки у самой развязки.
    risk_min_hours_to_close: float = 2.0
    # Лимит на ОДНО событие (матч, город-день). Рынки одного события коррелированы:
    # несколько ставок на него — концентрация, а не диверсификация. Группируем по
    # заголовку события (первая строка описания рынка). База % — стартовый банк бота.
    risk_max_positions_per_event: int = 2
    risk_max_event_exposure_pct: float = 0.10   # ≤10% капитала бота на одно событие

    # ── Досрочный выход (early exit) ──────────────────────────────
    # Гибрид: жёсткий стоп-лосс (страховка) + AI-переоценка тезиса.
    # Выход всегда по bid (реальная цена продажи), только пока стакан жив.
    early_exit_enabled: bool = True
    # Стоп-лосс: режем позицию, если нереализованный убыток достиг доли от вложенного.
    early_exit_stop_loss_pct: float = 0.50   # -50% от размера позиции → выход
    # AI-переоценка: выходим, если ВСТРЕЧНЫЙ edge превысил этот порог (симметрично входу).
    early_exit_ai_edge_threshold: float = 0.10
    # Re-eval тезиса делаем ТОЛЬКО если P(Yes) сдвинулся ≥ этого с прошлой проверки.
    # Цена не двигалась → модель на тех же данных даст тот же ответ: экономим LLM-вызов
    # и не ловим шумовые флипы стохастичной модели.
    early_exit_reeval_price_move: float = 0.03

    # ── Analyzer ────────────────────────────────────────────────
    filter_model: str = "llama-3.1-8b-instant"
    decision_model: str = "grok-4.3"
    decision_provider: Literal["groq", "grok", "claude"] = "grok"
    # Модель Groq для слоя решений: используется и при DECISION_PROVIDER=groq,
    # и как фоллбэк, когда у выбранного провайдера нет ключа / он недоступен.
    groq_decision_model: str = "llama-3.3-70b-versatile"

    # ── Сайзинг позиции ─────────────────────────────────────────
    # КАК размер ставки зависит от сигнала:
    #   "fixed" — фикс-процент банка (risk_max_position_pct). Безопасно, текущий режим.
    #   "kelly" — дробный критерий Келли: размер ∝ величине перевеса (edge) и цене
    #             контракта. Слабый сигнал → меньше ставка, сильный → до потолка.
    #
    # ⚠️ ВКЛЮЧАТЬ "kelly" ТОЛЬКО после ПОДТВЕРЖДЁННОЙ калибровки (Brier модели < рынка
    #    на дистанции 50–100+ закрытых прогнозов, желательно на Grok+поиске). На
    #    некалиброванной модели Келли увеличивает ставку там, где модель увереннее
    #    ОШИБАЕТСЯ (сайзит в шум). Поэтому по умолчанию "fixed".
    #
    # Свойство безопасности: даже в режиме "kelly" размер НИКОГДА не превышает
    # risk_max_position_pct (Келли может только УМЕНЬШИТЬ ставку относительно потолка).
    # Сверху так же действуют лимиты экспозиции и на событие.
    sizing_mode: Literal["fixed", "kelly"] = "fixed"
    # Доля от ПОЛНОГО Келли. Полный Келли слишком агрессивен (риск разорения при любой
    # неточности оценок) → берём дробный. 0.25 = «четверть-Келли», стандартный
    # консервативный выбор.
    kelly_fraction: float = 0.25

    # ── Capital / cycle ─────────────────────────────────────────
    starting_bankroll: float = 1000.0
    cycle_interval_minutes: int = 30
    # Частота лёгкой ФОНОВОЙ проверки стоп-лосса по открытым позициям (без LLM):
    # тянет живой стакан и режет позицию при убытке ≥ порога между тяжёлыми
    # 30-мин циклами — чтобы ловить просадку за минуту, а не за полчаса.
    stop_loss_check_seconds: int = 60
    max_active_sessions: int = 3        # лимит активных автоторговель на юзера

    # ── Rate limiting (LLM API) ──────────────────────────────────
    # Пауза между LLM-вызовами (секунды). Groq free tier: ~30 RPM формально,
    # но реально 7-8 RPM при TPM-ограничениях 70b-модели.
    # При 3 сек: 10 фильтров × 3 + 5 решений × 3 ≈ 45 сек/сессия — вкладывается в лимит.
    llm_filter_delay: float = 3.0    # пауза после каждого вызова фильтра (8b)
    llm_decision_delay: float = 8.0  # пауза после каждого вызова решения (70b жёстче)

    # ── Polymarket Gamma API ────────────────────────────────────
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    clob_api_url: str = "https://clob.polymarket.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
