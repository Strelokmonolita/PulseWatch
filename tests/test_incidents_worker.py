"""Incident lifecycle and worker tests."""

from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.models import Check, Incident, IncidentStatus, Monitor, User
from app.notifications.notifiers import Notifier
from app.services.incident_service import IncidentService
from app.worker.scheduler import MonitorWorker


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
