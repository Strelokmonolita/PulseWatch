"""History endpoints: checks and incidents."""

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.models import Check, Incident, IncidentStatus, Monitor


async def _seed(
    session_factory: async_sessionmaker[AsyncSession], user_monitor_id: int, failing: bool
) -> None:
    async with session_factory() as session:
        session.add(
            Check(
                monitor_id=user_monitor_id,
                status_code=500 if failing else 200,
                response_time_ms=42,
                is_success=not failing,
                error_message="boom" if failing else None,
            )
        )
        await session.commit()


async def test_checks_and_incidents_history(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monitor_payload: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await client.post("/api/monitors", json=monitor_payload, headers=auth_headers)
    monitor_id = created.json()["id"]

    await _seed(session_factory, monitor_id, failing=False)
    await _seed(session_factory, monitor_id, failing=True)

    async with session_factory() as session:
        monitor = await session.get(Monitor, monitor_id)
        assert monitor is not None
        session.add(
            Incident(
                monitor_id=monitor_id,
                status=IncidentStatus.OPEN,
                opened_at=datetime.now(UTC),
                message="down",
            )
        )
        await session.commit()

    checks = await client.get("/api/checks", headers=auth_headers)
    assert checks.status_code == 200
    body = checks.json()
    assert body["total"] == 2
    assert {item["is_success"] for item in body["items"]} == {True, False}

    failed = await client.get("/api/checks", params={"success": False}, headers=auth_headers)
    assert failed.json()["total"] == 1

    recent = await client.get(
        "/api/checks",
        params={"since": (datetime.now(UTC) - timedelta(hours=1)).isoformat()},
        headers=auth_headers,
    )
    assert recent.json()["total"] == 2

    incidents = await client.get("/api/incidents", headers=auth_headers)
    assert incidents.status_code == 200
    assert incidents.json()["total"] == 1
    assert incidents.json()["items"][0]["status"] == "open"

    open_only = await client.get(
        "/api/incidents", params={"status": "closed"}, headers=auth_headers
    )
    assert open_only.json()["total"] == 0


async def test_checks_are_scoped_to_owner(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monitor_payload: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await client.post("/api/monitors", json=monitor_payload, headers=auth_headers)
    monitor_id = created.json()["id"]
    await _seed(session_factory, monitor_id, failing=False)

    await client.post(
        "/api/auth/register",
        json={"username": "other", "email": "other@example.com", "password": "password123"},
    )
    login = await client.post(
        "/api/auth/login", json={"username": "other", "password": "password123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert (await client.get("/api/checks", headers=headers)).json()["total"] == 0
    forbidden = await client.get("/api/checks", params={"monitor_id": monitor_id}, headers=headers)
    assert forbidden.status_code == 404


async def test_monitor_checks_endpoint(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monitor_payload: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await client.post("/api/monitors", json=monitor_payload, headers=auth_headers)
    monitor_id = created.json()["id"]
    await _seed(session_factory, monitor_id, failing=True)

    response = await client.get(f"/api/monitors/{monitor_id}/checks", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1

    status_response = await client.get(f"/api/monitors/{monitor_id}/status", headers=auth_headers)
    status_body = status_response.json()
    assert status_body["is_up"] is False
    assert status_body["last_status_code"] == 500
    assert status_body["checks_24h"] == 1
    assert status_body["failures_24h"] == 1
    assert status_body["uptime_percent_24h"] == 0.0


async def test_health_endpoints(client: AsyncClient) -> None:
    live = await client.get("/health")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
