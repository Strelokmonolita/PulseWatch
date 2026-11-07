"""Monitor lifecycle and status aggregation."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Monitor
from app.repositories.check_repository import CheckRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.monitor_repository import MonitorRepository
from app.schemas.schemas import MonitorStatus


class MonitorNotFoundError(Exception):
    """Monitor does not exist or is not owned by the caller."""


class MonitorService:
    """Business logic for monitors."""

    def __init__(self, session: AsyncSession) -> None:
        self.monitors = MonitorRepository(session)
        self.checks = CheckRepository(session)
        self.incidents = IncidentRepository(session)

    async def create(self, user_id: int, payload: dict[str, Any]) -> Monitor:
        monitor = Monitor(user_id=user_id, **payload)
        return await self.monitors.create(monitor)

    async def list(self, user_id: int, limit: int, offset: int) -> tuple[list[Monitor], int]:
        return await self.monitors.list_for_user(user_id, limit, offset)

    async def get(self, monitor_id: int, user_id: int) -> Monitor:
        monitor = await self.monitors.get_owned(monitor_id, user_id)
        if monitor is None:
            raise MonitorNotFoundError(monitor_id)
        return monitor

    async def update(self, monitor_id: int, user_id: int, values: dict[str, Any]) -> Monitor:
        monitor = await self.get(monitor_id, user_id)
        return await self.monitors.update(monitor, values)

    async def delete(self, monitor_id: int, user_id: int) -> None:
        monitor = await self.get(monitor_id, user_id)
        await self.monitors.delete(monitor)

    async def status(self, monitor_id: int, user_id: int) -> MonitorStatus:
        monitor = await self.get(monitor_id, user_id)
        latest = await self.checks.latest_for_monitor(monitor.id)
        open_incident = await self.incidents.get_open_for_monitor(monitor.id)
        since = datetime.now(UTC) - timedelta(hours=24)
        total, failures = await self.checks.stats_since(monitor.id, since)

        uptime = None
        if total:
            uptime = round((total - failures) / total * 100, 2)

        return MonitorStatus(
            monitor_id=monitor.id,
            is_up=bool(latest.is_success) if latest else monitor.is_active,
            last_checked_at=latest.created_at if latest else None,
            last_status_code=latest.status_code if latest else None,
            response_time_ms=latest.response_time_ms if latest else None,
            open_incident_id=open_incident.id if open_incident else None,
            checks_24h=total,
            failures_24h=failures,
            uptime_percent_24h=uptime,
        )
