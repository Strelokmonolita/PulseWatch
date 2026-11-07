"""Asyncio-based monitoring worker."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.models.models import Check, Monitor
from app.notifications.notifiers import (
    NotificationDispatcher,
    build_default_dispatcher,
)
from app.repositories.check_repository import CheckRepository
from app.repositories.monitor_repository import MonitorRepository
from app.services.checker import CheckResult, perform_check
from app.services.incident_service import IncidentService

logger = logging.getLogger(__name__)


class MonitorWorker:
    """Schedules and executes health checks for every active monitor."""

    def __init__(
        self,
        dispatcher: NotificationDispatcher | None = None,
        redis: Redis[str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = get_settings()
        self.dispatcher = dispatcher or build_default_dispatcher()
        self.redis = redis
        self.client = client
        self._next_run: dict[int, float] = {}
        self._last_prune = 0.0
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        """Main loop: poll monitors and dispatch due checks."""
        logger.info("worker_started")
        while not self._stopping.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - keep the loop alive
                logger.exception("worker_tick_failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=1.0)
            except TimeoutError:
                continue
        logger.info("worker_stopped")

    async def tick(self, now: float | None = None) -> None:
        """Run all checks that are due at this moment."""
        moment = now if now is not None else time.monotonic()
        async with get_session_factory()() as session:
            monitors = await MonitorRepository(session).list_active()

        due: list[Monitor] = []
        for monitor in monitors:
            next_at = self._next_run.get(monitor.id, 0.0)
            if moment >= next_at:
                self._next_run[monitor.id] = moment + monitor.interval_seconds
                due.append(monitor)

        if not due:
            return
        await asyncio.gather(*(self._check_monitor(monitor) for monitor in due))

    async def _check_monitor(self, monitor: Monitor) -> None:
        if not await self._acquire_lock(monitor.id):
            return
        try:
            result = await perform_check(monitor, self.client)
            await self._persist(monitor, result)
        except Exception:  # noqa: BLE001 - one monitor must not kill the batch
            logger.exception("monitor_check_failed", extra={"monitor_id": monitor.id})
        finally:
            await self._release_lock(monitor.id)

    async def _acquire_lock(self, monitor_id: int) -> bool:
        if self.redis is None:
            return True
        lock_key = f"pulsewatch:lock:monitor:{monitor_id}"
        try:
            acquired = await self.redis.set(lock_key, "1", nx=True, ex=60)
        except Exception:  # noqa: BLE001 - degrade gracefully without Redis
            logger.warning("redis_lock_unavailable", extra={"monitor_id": monitor_id})
            return True
        return bool(acquired)

    async def _release_lock(self, monitor_id: int) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.delete(f"pulsewatch:lock:monitor:{monitor_id}")
        except Exception:  # noqa: BLE001
            logger.debug("redis_unlock_failed", extra={"monitor_id": monitor_id})

    async def _persist(self, monitor: Monitor, result: CheckResult) -> None:
        async with get_session_factory()() as session:
            checks = CheckRepository(session)
            check = await checks.create(
                Check(
                    monitor_id=monitor.id,
                    status_code=result.status_code,
                    response_time_ms=result.response_time_ms,
                    is_success=result.is_success,
                    error_message=result.error_message,
                )
            )
            incidents = IncidentService(
                session,
                dispatcher=self.dispatcher,
                threshold=self.settings.incident_threshold,
            )
            consecutive = await incidents.consecutive_failures(monitor.id)
            await incidents.evaluate(monitor, check, consecutive)
            await self._prune_history(session)

    async def _prune_history(self, session: AsyncSession) -> None:
        now = time.monotonic()
        if now - self._last_prune < self.settings.history_prune_interval_seconds:
            return
        self._last_prune = now
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.history_retention_days)
        removed = await CheckRepository(session).delete_older_than(cutoff)
        if removed:
            logger.info("history_pruned", extra={"removed": removed})


async def _close_redis(redis: Redis[str]) -> None:
    """Close a Redis connection regardless of client version."""
    close = getattr(redis, "aclose", None)
    if close is not None:
        await close()
    else:  # pragma: no cover - older redis-py releases
        await redis.close()


async def run_worker() -> None:
    """Start the worker until interrupted."""
    redis: Redis[str] | None = None
    try:
        candidate = Redis.from_url(get_settings().redis_url, decode_responses=True)
        await candidate.ping()
        redis = candidate
    except Exception:  # noqa: BLE001
        logger.warning("redis_unavailable_running_without_locks")
        redis = None

    worker = MonitorWorker(redis=redis)
    try:
        await worker.run()
    finally:
        if redis is not None:
            await _close_redis(redis)
