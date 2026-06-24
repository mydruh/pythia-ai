"""Async-движок и фабрика сессий SQLAlchemy."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings
from db.base import Base

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Зависимость FastAPI / контекст для ядра."""
    async with async_session_factory() as session:
        yield session


async def init_db() -> None:
    """Создать таблицы (быстрый старт без Alembic; для prod — миграции)."""
    # Импорт моделей регистрирует их в metadata.
    import db.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
