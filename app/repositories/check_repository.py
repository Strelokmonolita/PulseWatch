"""Check result data access."""

from datetime import datetime

from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Check


class CheckRepository:
    """Persistence and aggregation for health checks."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, check: Check) -> Check:
        self.session.add(check)
        await self.session.commit()
        await self.session.refresh(check)
        return check

    async def list_for_monitors(
        self,
        monitor_ids: list[int],
        limit: int,
        offset: int,
        monitor_id: int | None = None,
        since: datetime | None = None,
        success_only: bool | None = None,
    ) -> tuple[list[Check], int]:
        conditions: list[ColumnElement[bool]] = (
            [Check.monitor_id.in_(monitor_ids)] if monitor_ids else [Check.id < 0]
        )
        if monitor_id is not None:
            conditions.append(Check.monitor_id == monitor_id)
        if since is not None:
            conditions.append(Check.created_at >= since)
        if success_only is not None:
            conditions.append(Check.is_success.is_(success_only))

        total = await self.session.scalar(
            select(func.count()).select_from(Check).where(*conditions)
        )
        result = await self.session.execute(
            select(Check)
            .where(*conditions)
            .order_by(Check.created_at.desc(), Check.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all()), int(total or 0)

    async def latest_for_monitor(self, monitor_id: int) -> Check | None:
        result = await self.session.execute(
            select(Check)
            .where(Check.monitor_id == monitor_id)
            .order_by(Check.created_at.desc(), Check.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def stats_since(self, monitor_id: int, since: datetime) -> tuple[int, int]:
        """Return (total, failures) checks for a monitor since a moment."""
        result = await self.session.execute(
            select(
                func.count(Check.id),
                func.count(Check.id).filter(Check.is_success.is_(False)),
            ).where(Check.monitor_id == monitor_id, Check.created_at >= since)
        )
        total, failures = result.one()
        return int(total or 0), int(failures or 0)

    async def recent_for_monitor(self, monitor_id: int, limit: int) -> list[Check]:
        result = await self.session.execute(
            select(Check)
            .where(Check.monitor_id == monitor_id)
            .order_by(Check.created_at.desc(), Check.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def delete_older_than(self, cutoff: datetime) -> int:
        result = await self.session.execute(delete(Check).where(Check.created_at < cutoff))
        await self.session.commit()
        return int(result.rowcount or 0)
