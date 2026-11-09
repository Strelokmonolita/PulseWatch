"""Incident lifecycle and worker tests."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from redis.asyncio import Redis
from redis.asyncio.lock import Lock
from redis.exceptions import LockNotOwnedError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.models import Check, Incident, IncidentStatus, Monitor, User
from app.notifications.notifiers import Notifier
from app.services.checker import CheckResult
from app.services.incident_service import IncidentService
from app.worker.scheduler import MonitorWorker, run_worker


class RecordingNotifier(Notifier):
    """Collects notifications for assertions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def send(self, subject: str, message: str, **meta: Any) -> bool:
        self.events.append((subject, message))
        return True


class FakeDispatcher:
    """Dispatcher stub that records calls."""

    def __init__(self) -> None:
        self.notifier = RecordingNotifier()

    async def dispatch(
        self, subject: str, message: str, webhook_url: str | None = None, **meta: Any
    ) -> None:
        await self.notifier.send(subject, message, **meta)


async def _make_monitor(session: AsyncSession) -> Monitor:
    user = User(username="owner", email="owner@example.com", hashed_password="x")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    monitor = Monitor(
        user_id=user.id,
        name="svc",
        url="http://93.184.216.34/",
        method="GET",
        expected_status=200,
        timeout_seconds=5,
        interval_seconds=60,
        is_active=True,
    )
    session.add(monitor)
    await session.commit()
    await session.refresh(monitor)
    return monitor


async def test_incident_opens_after_threshold(test_db: AsyncSession) -> None:
    monitor = await _make_monitor(test_db)
    dispatcher = FakeDispatcher()
    service = IncidentService(test_db, dispatcher=dispatcher, threshold=3)  # type: ignore[arg-type]

    for _ in range(2):
        check = await _record_failure(test_db, monitor.id)
        assert (
            await service.evaluate(monitor, check, await service.consecutive_failures(monitor.id))
            is None
        )

    check = await _record_failure(test_db, monitor.id)
    incident = await service.evaluate(
        monitor, check, await service.consecutive_failures(monitor.id)
    )
    assert incident is not None
    assert incident.status is IncidentStatus.OPEN
    assert len(dispatcher.notifier.events) == 1
    assert "DOWN" in dispatcher.notifier.events[0][0]


async def test_incident_closes_on_success(test_db: AsyncSession) -> None:
    monitor = await _make_monitor(test_db)
    dispatcher = FakeDispatcher()
    service = IncidentService(test_db, dispatcher=dispatcher, threshold=1)  # type: ignore[arg-type]

    failing = await _record_failure(test_db, monitor.id)
    opened = await service.evaluate(monitor, failing, 1)
    assert opened is not None

    success = Check(monitor_id=monitor.id, status_code=200, response_time_ms=10, is_success=True)
    await test_db.commit()
    test_db.add(success)
    await test_db.commit()
    await test_db.refresh(success)

    closed = await service.evaluate(monitor, success, 0)
    assert closed is not None
    assert closed.status is IncidentStatus.CLOSED
    assert closed.closed_at is not None
    assert any("RECOVERED" in subject for subject, _ in dispatcher.notifier.events)


async def test_no_duplicate_incidents(test_db: AsyncSession) -> None:
    monitor = await _make_monitor(test_db)
    service = IncidentService(test_db, threshold=1)

    first = await _record_failure(test_db, monitor.id)
    assert await service.evaluate(monitor, first, 1) is not None

    second = await _record_failure(test_db, monitor.id)
    assert await service.evaluate(monitor, second, 2) is None

    rows = await test_db.execute(
        select(Incident).where(
            Incident.monitor_id == monitor.id,
            Incident.status == IncidentStatus.OPEN,
        )
    )
    assert len(list(rows.scalars())) == 1


async def _record_failure(session: AsyncSession, monitor_id: int) -> Check:
    check = Check(
        monitor_id=monitor_id,
        status_code=503,
        response_time_ms=12,
        is_success=False,
        error_message="unexpected status 503",
    )
    session.add(check)
    await session.commit()
    await session.refresh(check)
    return check


async def test_worker_records_check_and_opens_incident(
    test_db: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
) -> None:
    monitor = await _make_monitor(test_db)
    monkeypatch.setattr("app.worker.scheduler.get_session_factory", lambda: session_factory)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        worker = MonitorWorker(dispatcher=FakeDispatcher(), client=client)  # type: ignore[arg-type]
        worker.settings.incident_threshold = 1
        await worker.tick()

    checks = list(
        (await test_db.execute(select(Check).where(Check.monitor_id == monitor.id))).scalars()
    )
    assert len(checks) == 1
    assert checks[0].is_success is False


async def test_worker_skips_checks_until_interval(
    test_db: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
) -> None:
    await _make_monitor(test_db)
    monkeypatch.setattr("app.worker.scheduler.get_session_factory", lambda: session_factory)

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        worker = MonitorWorker(dispatcher=FakeDispatcher(), client=client)  # type: ignore[arg-type]
        await worker.tick(now=100.0)
        await worker.tick(now=101.0)
        await worker.tick(now=161.0)

    assert len(calls) == 2


async def test_disabled_worker_does_not_connect_or_start(
    settings_override: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_override.worker_enabled = False
    redis_factory = Mock()
    worker_factory = Mock()
    monkeypatch.setattr("app.worker.scheduler.Redis.from_url", redis_factory)
    monkeypatch.setattr("app.worker.scheduler.MonitorWorker", worker_factory)
    await run_worker()
    redis_factory.assert_not_called()
    worker_factory.assert_not_called()


async def test_disabled_worker_cannot_run_or_tick_directly(
    settings_override: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_override.worker_enabled = False
    database = Mock()
    monkeypatch.setattr("app.worker.scheduler.get_session_factory", database)
    worker = MonitorWorker()
    await asyncio.wait_for(worker.run(), timeout=1)
    await worker.tick()
    database.assert_not_called()


@pytest.fixture
def worker_lock(monkeypatch: pytest.MonkeyPatch) -> Mock:
    lock = Mock(spec=Lock)
    lock.acquire = AsyncMock(return_value=True)
    lock.reacquire = AsyncMock(return_value=True)
    lock.release = AsyncMock()
    monkeypatch.setattr("app.worker.scheduler.Lock", Mock(return_value=lock))
    monkeypatch.setattr("app.worker.scheduler._LOCK_RENEW_INTERVAL_SECONDS", 0.001)
    return lock


async def test_worker_renews_lock_until_check_finishes(
    worker_lock: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renewed = asyncio.Event()

    async def renew() -> bool:
        renewed.set()
        return True

    async def check(*args: Any) -> CheckResult:
        await renewed.wait()
        return CheckResult(True, 200, 1, None)

    worker_lock.reacquire.side_effect = renew
    monkeypatch.setattr("app.worker.scheduler.perform_check", check)
    worker = MonitorWorker(redis=Mock(spec=Redis))
    persist = AsyncMock()
    monkeypatch.setattr(worker, "_persist", persist)
    await asyncio.wait_for(worker._check_monitor(Monitor(id=1)), timeout=1)
    assert renewed.is_set()
    persist.assert_awaited_once()
    worker_lock.release.assert_awaited_once()


async def test_lost_lock_cancels_check_before_persistence(
    worker_lock: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def check(*args: Any) -> CheckResult:
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
        raise AssertionError("check must be cancelled")

    async def lose_lock() -> bool:
        await started.wait()
        raise LockNotOwnedError("lock expired")

    worker_lock.reacquire.side_effect = lose_lock
    monkeypatch.setattr("app.worker.scheduler.perform_check", check)
    worker = MonitorWorker(redis=Mock(spec=Redis))
    persist = AsyncMock()
    monkeypatch.setattr(worker, "_persist", persist)
    await asyncio.wait_for(worker._check_monitor(Monitor(id=1)), timeout=1)
    assert cancelled.is_set()
    persist.assert_not_awaited()
    worker_lock.release.assert_awaited_once()


@pytest.mark.parametrize("unavailable", [False, True])
async def test_worker_skips_check_without_lock_ownership(
    worker_lock: Mock,
    monkeypatch: pytest.MonkeyPatch,
    unavailable: bool,
) -> None:
    if unavailable:
        worker_lock.acquire.side_effect = ConnectionError("Redis unavailable")
    else:
        worker_lock.acquire.return_value = False
    check = AsyncMock()
    monkeypatch.setattr("app.worker.scheduler.perform_check", check)
    worker = MonitorWorker(redis=Mock(spec=Redis))
    await worker._check_monitor(Monitor(id=1))
    check.assert_not_awaited()
    worker_lock.release.assert_not_awaited()
