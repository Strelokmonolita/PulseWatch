"""Incident data access."""

from datetime import UTC, datetime

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Incident, IncidentStatus


class IncidentRepository:
    """Incident persistence and state transitions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_open_for_monitor(self, monitor_id: int) -> Incident | None:
        result = await self.session.execute(
            select(Incident)
            .where(
                Incident.monitor_id == monitor_id,
                Incident.status == IncidentStatus.OPEN,
            )
            .order_by(Incident.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_for_monitors(
        self,
        monitor_ids: list[int],
        limit: int,
        offset: int,
        monitor_id: int | None = None,
        status: IncidentStatus | None = None,
    ) -> tuple[list[Incident], int]:
        conditions: list[ColumnElement[bool]] = (
            [Incident.monitor_id.in_(monitor_ids)] if monitor_ids else [Incident.id < 0]
        )
        if monitor_id is not None:
            conditions.append(Incident.monitor_id == monitor_id)
        if status is not None:
            conditions.append(Incident.status == status)

        total = await self.session.scalar(
            select(func.count()).select_from(Incident).where(*conditions)
        )
        result = await self.session.execute(
            select(Incident)
            .where(*conditions)
            .order_by(Incident.opened_at.desc().nullslast(), Incident.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all()), int(total or 0)

    async def open(self, monitor_id: int, message: str) -> Incident:
        incident = Incident(
            monitor_id=monitor_id,
            status=IncidentStatus.OPEN,
            opened_at=datetime.now(UTC),
            message=message,
        )
        self.session.add(incident)
        await self.session.commit()
        await self.session.refresh(incident)
        return incident

    async def close(self, incident: Incident, message: str | None = None) -> Incident:
        incident.status = IncidentStatus.CLOSED
        incident.closed_at = datetime.now(UTC)
        if message is not None:
            incident.message = message
        await self.session.commit()
        await self.session.refresh(incident)
        return incident

    async def count_open_for_monitors(self, monitor_ids: list[int]) -> int:
        if not monitor_ids:
            return 0
        total = await self.session.scalar(
            select(func.count())
            .select_from(Incident)
            .where(
                Incident.monitor_id.in_(monitor_ids),
                Incident.status == IncidentStatus.OPEN,
            )
        )
        return int(total or 0)
