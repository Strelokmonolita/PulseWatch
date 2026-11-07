"""Monitor routes."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbSession
from app.repositories.check_repository import CheckRepository
from app.schemas.schemas import (
    CheckRead,
    MonitorCreate,
    MonitorRead,
    MonitorStatus,
    MonitorUpdate,
    Page,
)
from app.services.monitor_service import MonitorNotFoundError, MonitorService

router = APIRouter(prefix="/monitors", tags=["monitors"])


@router.post("", response_model=MonitorRead, status_code=status.HTTP_201_CREATED)
async def create_monitor(
    payload: MonitorCreate, user: CurrentUser, session: DbSession
) -> MonitorRead:
    """Create a monitor owned by the current user."""
    service = MonitorService(session)
    monitor = await service.create(user.id, payload.model_dump())
    return MonitorRead.model_validate(monitor)


@router.get("", response_model=Page[MonitorRead])
async def list_monitors(
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[MonitorRead]:
    """List the current user's monitors with pagination."""
    service = MonitorService(session)
    monitors, total = await service.list(user.id, limit, offset)
    return Page[MonitorRead](
        items=[MonitorRead.model_validate(item) for item in monitors],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{monitor_id}", response_model=MonitorRead)
async def get_monitor(monitor_id: int, user: CurrentUser, session: DbSession) -> MonitorRead:
    """Fetch a single monitor."""
    try:
        monitor = await MonitorService(session).get(monitor_id, user.id)
    except MonitorNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Monitor not found"
        ) from exc
    return MonitorRead.model_validate(monitor)


@router.patch("/{monitor_id}", response_model=MonitorRead)
async def update_monitor(
    monitor_id: int, payload: MonitorUpdate, user: CurrentUser, session: DbSession
) -> MonitorRead:
    """Update monitor fields; only provided fields are changed."""
    values = payload.model_dump(exclude_unset=True)
    try:
        monitor = await MonitorService(session).update(monitor_id, user.id, values)
    except MonitorNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Monitor not found"
        ) from exc
    return MonitorRead.model_validate(monitor)


@router.delete("/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_monitor(monitor_id: int, user: CurrentUser, session: DbSession) -> Response:
    """Delete a monitor and its history."""
    try:
        await MonitorService(session).delete(monitor_id, user.id)
    except MonitorNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Monitor not found"
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{monitor_id}/status", response_model=MonitorStatus)
async def monitor_status(monitor_id: int, user: CurrentUser, session: DbSession) -> MonitorStatus:
    """Aggregated current state and 24h statistics for a monitor."""
    try:
        return await MonitorService(session).status(monitor_id, user.id)
    except MonitorNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Monitor not found"
        ) from exc


@router.get("/{monitor_id}/checks", response_model=Page[CheckRead])
async def monitor_checks(
    monitor_id: int,
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[CheckRead]:
    """List recent checks of a monitor."""
    try:
        await MonitorService(session).get(monitor_id, user.id)
    except MonitorNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Monitor not found"
        ) from exc

    items, total = await CheckRepository(session).list_for_monitors(
        [monitor_id], limit, offset, monitor_id=monitor_id
    )
    return Page[CheckRead](
        items=[CheckRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )
