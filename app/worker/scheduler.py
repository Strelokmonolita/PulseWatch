"""Asyncio-based monitoring worker."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
from redis.asyncio import Redis
from redis.asyncio.lock import Lock
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

_LOCK_TTL_SECONDS = 180
_LOCK_RENEW_INTERVAL_SECONDS = 60


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
        if not self.settings.worker_enabled:
            logger.info("worker_disabled")
            return
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
        if not self.settings.worker_enabled:
            return
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
        lock = await self._acquire_lock(monitor.id)
        if self.redis is not None and lock is None:
            return
        try:
            async with asyncio.TaskGroup() as tasks:
                renewal = tasks.create_task(self._renew_lock(lock)) if lock is not None else None
                try:
                    result = await perform_check(monitor, self.client)
                    await self._persist(monitor, result)
                finally:
                    if renewal is not None:
                        renewal.cancel()
        except Exception:  # noqa: BLE001 - one monitor must not kill the batch
            logger.exception("monitor_check_failed", extra={"monitor_id": monitor.id})
        finally:
            await self._release_lock(monitor.id, lock)

    async def _acquire_lock(self, monitor_id: int) -> Lock | None:
        if self.redis is None:
            return None
        lock_key = f"pulsewatch:lock:monitor:{monitor_id}"
        lock = Lock(
            self.redis, lock_key, timeout=_LOCK_TTL_SECONDS, blocking=False, thread_local=False
        )
        try:
            if await lock.acquire():
                return lock
        except Exception:  # noqa: BLE001 - skip this check rather than run without ownership
            logger.warning("redis_lock_unavailable", extra={"monitor_id": monitor_id})
        return None

    async def _renew_lock(self, lock: Lock) -> None:
        """Keep ownership through DNS, HTTP, persistence and notification work."""
        while True:
            await asyncio.sleep(_LOCK_RENEW_INTERVAL_SECONDS)
            # A failed renewal cancels the check through its TaskGroup.
            await lock.reacquire()

    async def _release_lock(self, monitor_id: int, lock: Lock | None) -> None:
        if lock is None:
            return
        try:
            # Redis Lock atomically compares the owner token before deleting.
            await lock.release()
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
    settings = get_settings()
    if not settings.worker_enabled:
        logger.info("worker_disabled")
        return

    redis: Redis[str] | None = None
    candidate: Redis[str] | None = None
    try:
        candidate = Redis.from_url(
            settings.redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5
        )
        await candidate.ping()
        redis = candidate
    except Exception:  # noqa: BLE001
        logger.warning("redis_unavailable_running_without_locks")
        if candidate is not None:
            await _close_redis(candidate)

    worker = MonitorWorker(redis=redis)
    try:
        await worker.run()
    finally:
        if redis is not None:
            await _close_redis(redis)
