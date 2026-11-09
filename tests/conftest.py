"""Pytest configuration and fixtures."""

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def settings_override(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Provide deterministic settings for every test."""
    settings = Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        database_test_url=None,
        redis_url="redis://localhost:6379/0",
        secret_key="test-secret-key",
        telegram_token=None,
        telegram_chat_id=None,
        worker_enabled=True,
        ssrf_blocked_networks=None,
    )
    get_settings.cache_clear()
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.core.security.get_settings", lambda: settings)
    monkeypatch.setattr("app.core.ssrf.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.auth_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.notifications.notifiers.get_settings", lambda: settings)
    monkeypatch.setattr("app.worker.scheduler.get_settings", lambda: settings)
    return settings


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create an isolated in-memory SQLite engine with the schema applied."""
    database = create_async_engine(TEST_DATABASE_URL)
    async with database.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the test engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def test_db(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Raw session for repository and model tests."""
    async with session_factory() as session:
        yield session


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client against the application using the test database."""
    app = create_app()

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Register a user and return bearer auth headers."""
    await client.post(
        "/api/auth/register",
        json={
            "username": "tester",
            "email": "tester@example.com",
            "password": "supersecret1",
        },
    )
    response = await client.post(
        "/api/auth/login",
        json={"username": "tester", "password": "supersecret1"},
    )
    token: str = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def monitor_payload() -> dict[str, Any]:
    """Minimal valid monitor payload."""
    return {
        "name": "Example",
        "url": "https://example.com",
        "method": "GET",
        "expected_status": 200,
        "timeout_seconds": 5,
        "interval_seconds": 60,
    }
