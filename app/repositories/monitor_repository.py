"""Monitor data access."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Monitor


class MonitorRepository:
    """CRUD operations for monitors, always scoped to an owner."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(
        self, user_id: int, limit: int, offset: int, active_only: bool = False
    ) -> tuple[list[Monitor], int]:
        conditions = [Monitor.user_id == user_id]
        if active_only:
            conditions.append(Monitor.is_active.is_(True))

        total = await self.session.scalar(
            select(func.count()).select_from(Monitor).where(*conditions)
        )
        result = await self.session.execute(
            select(Monitor).where(*conditions).order_by(Monitor.id).limit(limit).offset(offset)
        )
        return list(result.scalars().all()), int(total or 0)

    async def list_active(self) -> list[Monitor]:
        result = await self.session.execute(
            select(Monitor).where(Monitor.is_active.is_(True)).order_by(Monitor.id)
        )
        return list(result.scalars().all())

    async def ids_for_user(self, user_id: int) -> list[int]:
        """Return every monitor id owned by a user."""
        result = await self.session.execute(
            select(Monitor.id).where(Monitor.user_id == user_id).order_by(Monitor.id)
        )
        return list(result.scalars().all())

    async def get_owned(self, monitor_id: int, user_id: int) -> Monitor | None:
        result = await self.session.execute(
            select(Monitor).where(Monitor.id == monitor_id, Monitor.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get(self, monitor_id: int) -> Monitor | None:
        return await self.session.get(Monitor, monitor_id)

    async def create(self, monitor: Monitor) -> Monitor:
        self.session.add(monitor)
        await self.session.commit()
        await self.session.refresh(monitor)
        return monitor

    async def update(self, monitor: Monitor, values: dict[str, Any]) -> Monitor:
        for field, value in values.items():
            setattr(monitor, field, value)
        monitor.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(monitor)
        return monitor

    async def delete(self, monitor: Monitor) -> None:
        await self.session.delete(monitor)
        await self.session.commit()
