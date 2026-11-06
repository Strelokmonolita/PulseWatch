"""Test models and database setup."""

import pytest  # type: ignore[import-not-found]
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Monitor, User


@pytest.mark.asyncio  # type: ignore[misc]
async def test_user_creation(test_db: AsyncSession) -> None:
    """Test creating a user."""
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_password",
    )
    test_db.add(user)
    await test_db.commit()

    assert user.id is not None
    assert user.username == "testuser"
    assert user.email == "test@example.com"


@pytest.mark.asyncio  # type: ignore[misc]
async def test_monitor_creation(test_db: AsyncSession) -> None:
    """Test creating a monitor."""
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_password",
    )
    test_db.add(user)
    await test_db.commit()

    monitor = Monitor(
        user_id=user.id,
        name="Test Monitor",
        url="https://example.com",
        method="GET",
        expected_status=200,
        timeout_seconds=10,
        interval_seconds=60,
    )
    test_db.add(monitor)
    await test_db.commit()

    assert monitor.id is not None
    assert monitor.name == "Test Monitor"
    assert monitor.url == "https://example.com"
