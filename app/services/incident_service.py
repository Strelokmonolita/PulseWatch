"""Incident detection based on consecutive failures."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Check, Incident, Monitor
from app.notifications.notifiers import NotificationDispatcher
from app.repositories.check_repository import CheckRepository
from app.repositories.incident_repository import IncidentRepository


class IncidentService:
    """Opens and closes incidents around monitor failures."""

    def __init__(
        self,
        session: AsyncSession,
        dispatcher: NotificationDispatcher | None = None,
        threshold: int = 3,
    ) -> None:
        self.checks = CheckRepository(session)
        self.incidents = IncidentRepository(session)
        self.dispatcher = dispatcher
        self.threshold = max(1, threshold)

    async def evaluate(
        self, monitor: Monitor, result: Check, consecutive_failures: int
    ) -> Incident | None:
        """Open or close an incident for the monitor; returns a changed incident."""
        open_incident = await self.incidents.get_open_for_monitor(monitor.id)

        if not result.is_success:
            if open_incident is None and consecutive_failures >= self.threshold:
                reason = result.error_message or "monitor is failing"
                incident = await self.incidents.open(
                    monitor.id,
                    f"{monitor.name} is DOWN after {consecutive_failures} failed checks: {reason}",
                )
                await self._notify_open(monitor, incident)
                return incident
            return None

        if open_incident is not None:
            incident = await self.incidents.close(open_incident, f"{monitor.name} recovered")
            await self._notify_close(monitor, incident)
            return incident
        return None

    async def consecutive_failures(self, monitor_id: int) -> int:
        """Count trailing failed checks until the first success."""
        recent = await self.checks.recent_for_monitor(monitor_id, self.threshold + 1)
        count = 0
        for check in recent:
            if check.is_success:
                break
            count += 1
        return count

    async def _notify_open(self, monitor: Monitor, incident: Incident) -> None:
        if self.dispatcher is None:
            return
        await self.dispatcher.dispatch(
            subject=f"[DOWN] {monitor.name}",
            message=incident.message or "monitor is down",
            webhook_url=monitor.webhook_url,
            monitor_id=monitor.id,
            incident_id=incident.id,
            event="incident_opened",
            url=monitor.url,
        )

    async def _notify_close(self, monitor: Monitor, incident: Incident) -> None:
        if self.dispatcher is None:
            return
        await self.dispatcher.dispatch(
            subject=f"[RECOVERED] {monitor.name}",
            message=incident.message or "monitor recovered",
            webhook_url=monitor.webhook_url,
            monitor_id=monitor.id,
            incident_id=incident.id,
            event="incident_closed",
            url=monitor.url,
        )
