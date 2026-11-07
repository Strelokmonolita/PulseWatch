"""HTTP health check execution."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app.core.ssrf import UnsafeURLError, ensure_safe_url
from app.models.models import Monitor


@dataclass(slots=True)
class CheckResult:
    """Outcome of a single health check."""

    is_success: bool
    status_code: int | None
    response_time_ms: int | None
    error_message: str | None


async def perform_check(monitor: Monitor, client: httpx.AsyncClient | None = None) -> CheckResult:
    """Run one HTTP check against the monitored URL."""
    try:
        await ensure_safe_url(monitor.url)
    except UnsafeURLError as exc:
        return CheckResult(False, None, None, f"unsafe url: {exc}")

    owned_client = client is None
    http = client or httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(monitor.timeout_seconds),
    )
    started = time.perf_counter()
    try:
        response = await http.request(monitor.method, monitor.url)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        success = response.status_code == monitor.expected_status
        error = None if success else f"unexpected status {response.status_code}"
        return CheckResult(success, response.status_code, elapsed_ms, error)
    except httpx.TimeoutException:
        return CheckResult(False, None, None, "request timed out")
    except httpx.HTTPError as exc:
        return CheckResult(False, None, None, f"request failed: {type(exc).__name__}")
    finally:
        if owned_client:
            await http.aclose()
