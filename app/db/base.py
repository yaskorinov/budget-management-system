from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import BASE_DIR, settings


def utcnow() -> dt.datetime:
    """Наивный UTC — единый формат хранения времени во всей базе."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


def _prepare_sqlite_path(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    _, _, tail = url.partition(":///")
    if not tail or tail == ":memory:":
        return
    path = Path(tail)
    if not path.is_absolute():
        path = BASE_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)


_prepare_sqlite_path(settings.database_url)

engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionMaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Сессия с автокоммитом на выходе и откатом при ошибке."""
    async with SessionMaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI."""
    async with session_scope() as session:
        yield session


async def init_db() -> None:
    from app.db import models  # noqa: F401  — регистрация моделей в метаданных

    async with engine.begin() as conn:
        if settings.database_url.startswith("sqlite"):
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(Base.metadata.create_all)
