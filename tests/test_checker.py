"""Checker and SSRF protection tests."""

import httpx
import pytest

from app.core.ssrf import UnsafeURLError, ensure_safe_url
from app.models.models import Monitor
from app.services.checker import perform_check


def _monitor(url: str, expected_status: int = 200) -> Monitor:
    return Monitor(
        id=1,
        user_id=1,
        name="probe",
        url=url,
        method="GET",
        expected_status=expected_status,
        timeout_seconds=5,
        interval_seconds=60,
        is_active=True,
    )


async def test_private_ipv4_rejected() -> None:
    for url in (
        "http://127.0.0.1/admin",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
    ):
        with pytest.raises(UnsafeURLError):
            await ensure_safe_url(url)


async def test_private_ipv6_rejected() -> None:
    for url in ("http://[::1]/", "http://[fd00::1]/"):
        with pytest.raises(UnsafeURLError):
            await ensure_safe_url(url)


async def test_localhost_name_rejected() -> None:
    with pytest.raises(UnsafeURLError):
        await ensure_safe_url("http://localhost:8000/")


async def test_non_http_scheme_rejected() -> None:
    with pytest.raises(UnsafeURLError):
        await ensure_safe_url("file:///etc/passwd")


async def test_public_benchmark_network_not_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """198.18.0.0/15 is used by Docker/VPN DNS interception, not by the host."""
    from app.core import ssrf

    monkeypatch.setattr(
        ssrf, "get_settings", lambda: type("S", (), {"ssrf_blocked_networks": None})()
    )
    assert ssrf._is_forbidden_ip("198.18.0.136") is False


def test_extra_blocked_networks_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import ssrf

    fake = type("S", (), {"ssrf_blocked_networks": "198.18.0.0/15, not-a-cidr"})()
    monkeypatch.setattr(ssrf, "get_settings", lambda: fake)
    assert ssrf._is_forbidden_ip("198.18.0.136") is True


async def test_check_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await perform_check(_monitor("http://93.184.216.34/"), client)
    assert result.is_success is True
    assert result.status_code == 200
    assert result.error_message is None
    assert result.response_time_ms is not None


async def test_check_unexpected_status() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await perform_check(_monitor("http://93.184.216.34/"), client)
    assert result.is_success is False
    assert result.status_code == 503
    assert result.error_message is not None


async def test_check_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await perform_check(_monitor("http://93.184.216.34/"), client)
    assert result.is_success is False
    assert result.error_message == "request timed out"


async def test_check_rejects_unsafe_target() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await perform_check(_monitor("http://127.0.0.1:8080/"), client)
    assert result.is_success is False
    assert result.error_message is not None
    assert result.error_message.startswith("unsafe url")
