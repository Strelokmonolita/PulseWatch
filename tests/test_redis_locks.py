"""Redis integration checks for lock ownership and renewal."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError

from app.worker.scheduler import MonitorWorker

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis[str]]:
    url = os.environ.get("REDIS_TEST_URL")
    if not url:
        pytest.skip("set REDIS_TEST_URL to run Redis integration tests")
    client: Redis[str] = Redis.from_url(
        url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5
    )
    try:
        await client.ping()
        yield client
    finally:
        await client.aclose()


async def test_expired_owner_cannot_release_or_extend_new_lock(redis_client: Redis[str]) -> None:
    monitor_id = uuid4().int
    key = f"pulsewatch:lock:monitor:{monitor_id}"
    first = MonitorWorker(redis=redis_client)
    second = MonitorWorker(redis=redis_client)
    try:
        old_lock = await first._acquire_lock(monitor_id)
        assert old_lock is not None
        assert await redis_client.ttl(key) > 120
        assert await second._acquire_lock(monitor_id) is None

        # Expire immediately rather than waiting for the production TTL.
        await redis_client.pexpire(key, 0)
        new_lock = await second._acquire_lock(monitor_id)
        assert new_lock is not None
        with pytest.raises(LockNotOwnedError):
            await old_lock.reacquire()
        await first._release_lock(monitor_id, old_lock)
        assert await new_lock.owned()
        await second._release_lock(monitor_id, new_lock)
        assert await redis_client.get(key) is None
    finally:
        await redis_client.delete(key)


async def test_redis_lock_is_renewed_while_task_is_running(
    redis_client: Redis[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor_id = uuid4().int
    key = f"pulsewatch:lock:monitor:{monitor_id}"
    worker = MonitorWorker(redis=redis_client)
    lock = await worker._acquire_lock(monitor_id)
    assert lock is not None
    renewed = asyncio.Event()
    reacquire = lock.reacquire

    async def record_renewal() -> bool:
        result = await reacquire()
        renewed.set()
        return result

    monkeypatch.setattr(lock, "reacquire", record_renewal)
    monkeypatch.setattr("app.worker.scheduler._LOCK_RENEW_INTERVAL_SECONDS", 0.001)
    task: asyncio.Task[None] | None = None
    try:
        await redis_client.pexpire(key, 5000)
        task = asyncio.create_task(worker._renew_lock(lock))
        await asyncio.wait_for(renewed.wait(), timeout=2)
        assert await redis_client.ttl(key) > 120
        assert await MonitorWorker(redis=redis_client)._acquire_lock(monitor_id) is None
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await worker._release_lock(monitor_id, lock)
        await redis_client.delete(key)
