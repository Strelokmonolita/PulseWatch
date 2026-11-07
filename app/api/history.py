"""History routes for checks and incidents."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.models.models import IncidentStatus
from app.repositories.check_repository import CheckRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.monitor_repository import MonitorRepository
from app.schemas.schemas import CheckRead, IncidentRead, Page

router = APIRouter(tags=["history"])


async def _owned_monitor_ids(session: AsyncSession, user_id: int) -> list[int]:
    return await MonitorRepository(session).ids_for_user(user_id)


@router.get("/checks", response_model=Page[CheckRead])
async def list_checks(
    user: CurrentUser,
    session: DbSession,
    monitor_id: Annotated[int | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    success: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[CheckRead]:
    """List checks across the current user's monitors."""
    monitor_ids = await _owned_monitor_ids(session, user.id)
    if monitor_id is not None and monitor_id not in monitor_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Monitor not found")

    items, total = await CheckRepository(session).list_for_monitors(
        monitor_ids,
        limit,
        offset,
        monitor_id=monitor_id,
        since=since,
        success_only=success,
    )
    return Page[CheckRead](
        items=[CheckRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/incidents", response_model=Page[IncidentRead])
async def list_incidents(
    user: CurrentUser,
    session: DbSession,
    monitor_id: Annotated[int | None, Query()] = None,
    incident_status: Annotated[IncidentStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[IncidentRead]:
    """List incidents across the current user's monitors."""
    monitor_ids = await _owned_monitor_ids(session, user.id)
    if monitor_id is not None and monitor_id not in monitor_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Monitor not found")

    items, total = await IncidentRepository(session).list_for_monitors(
        monitor_ids,
        limit,
        offset,
        monitor_id=monitor_id,
        status=incident_status,
    )
    return Page[IncidentRead](
        items=[IncidentRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )
