"""Refresh rotation under concurrent requests and database failures."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema, DropSchema

from app.models import Base, RefreshToken, User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.services.auth_service import AuthError, AuthService


@pytest.fixture
async def token_sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Use independent connections, with an isolated PostgreSQL schema when configured."""
    url = os.environ.get("DATABASE_TEST_URL")
    admin: AsyncEngine | None = None
    schema = f"pulsewatch_tokens_{uuid4().hex}"
    if url:
        admin = create_async_engine(url)
        async with admin.begin() as connection:
            await connection.execute(CreateSchema(schema))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'tokens.db').as_posix()}")

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        if admin is not None:
            async with admin.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True))
            await admin.dispose()


async def test_concurrent_refresh_has_exactly_one_winner(
    token_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with token_sessions() as session:
        user = User(username="concurrent", email="concurrent@example.com", hashed_password="unused")
        session.add(user)
        await session.commit()
        _, original = await AuthService(session).issue_tokens(user)

    async def rotate() -> str | AuthError:
        async with token_sessions() as session:
            try:
                _, _, token = await AuthService(session).refresh(original)
            except AuthError as exc:
                return exc
            return token

    results = await asyncio.gather(*(rotate() for _ in range(4)))
    winners = [result for result in results if isinstance(result, str)]
    rejected = [result for result in results if isinstance(result, AuthError)]
    assert len(winners) == 1
    assert len(rejected) == 3
    assert all(error.code == "invalid_token" for error in rejected)
    assert winners[0] != original

    async with token_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(RefreshToken)) == 1
        _, _, next_token = await AuthService(session).refresh(winners[0])
        assert next_token not in {original, winners[0]}
        with pytest.raises(AuthError):
            await AuthService(session).refresh(original)


async def test_failed_rotation_preserves_original_token(
    token_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with token_sessions() as session:
        user = User(username="rollback", email="rollback@example.com", hashed_password="unused")
        session.add(user)
        await session.commit()
        service = AuthService(session)
        _, original = await service.issue_tokens(user)
        _, other_session = await service.issue_tokens(user)
        collision = RefreshToken(
            token_hash=sha256(other_session.encode()).hexdigest(),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        with pytest.raises(IntegrityError):
            await RefreshTokenRepository(session).rotate(
                sha256(original.encode()).hexdigest(), collision
            )

    async with token_sessions() as session:
        _, _, replacement = await AuthService(session).refresh(original)
        assert replacement != original
        _, _, other_replacement = await AuthService(session).refresh(other_session)
        assert other_replacement != other_session
