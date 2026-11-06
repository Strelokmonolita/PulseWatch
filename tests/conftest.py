"""Pytest configuration and fixtures."""

import asyncio
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest  # type: ignore[import-not-found]
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.models import Base


@pytest.fixture  # type: ignore[misc]
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture  # type: ignore[misc]
async def test_db() -> AsyncGenerator[AsyncSession, None]:
    """Create test database engine and session."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        database_test_url=None,
        redis_url="redis://localhost:6379/0",
        secret_key="test-secret-key",
    )

    engine = create_async_engine(
        settings.database_url,
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()
