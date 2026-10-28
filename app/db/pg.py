"""PostgreSQL engine and session handling.

The active sessionmaker is process-global and swappable: the API configures a pooled engine at startup,
each Celery task configures a NullPool engine bound to its own event loop, and tests point it at a container.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def make_engine(url: str | None = None, *, nullpool: bool = False) -> AsyncEngine:
    kwargs: dict = {"poolclass": NullPool} if nullpool else {"pool_pre_ping": True}
    return create_async_engine(url or get_settings().database_url, **kwargs)


def configure(engine: AsyncEngine) -> None:
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)


def get_engine() -> AsyncEngine:
    if _engine is None:
        configure(make_engine())
    assert _engine is not None
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        configure(make_engine())
    assert _sessionmaker is not None
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Session for background work: commits on success, rolls back on error."""
    async with sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Endpoints commit explicitly before dispatching background work."""
    async with sessionmaker()() as session:
        yield session
