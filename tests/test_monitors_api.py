"""Monitor CRUD and status API tests."""

from typing import Any

import pytest
from httpx import AsyncClient


async def test_create_and_list_monitors(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monitor_payload: dict[str, Any],
) -> None:
    created = await client.post("/api/monitors", json=monitor_payload, headers=auth_headers)
    assert created.status_code == 201
    monitor_id = created.json()["id"]

    listing = await client.get("/api/monitors", headers=auth_headers)
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == monitor_id


async def test_invalid_url_rejected(
    client: AsyncClient, auth_headers: dict[str, str], monitor_payload: dict[str, Any]
) -> None:
    response = await client.post(
        "/api/monitors",
        json={**monitor_payload, "url": "ftp://example.com"},
        headers=auth_headers,
    )
    assert response.status_code == 422


async def test_interval_below_minimum_rejected(
    client: AsyncClient, auth_headers: dict[str, str], monitor_payload: dict[str, Any]
) -> None:
    response = await client.post(
        "/api/monitors",
        json={**monitor_payload, "interval_seconds": 5},
        headers=auth_headers,
    )
    assert response.status_code == 422


async def test_update_monitor(
    client: AsyncClient, auth_headers: dict[str, str], monitor_payload: dict[str, Any]
) -> None:
    created = await client.post("/api/monitors", json=monitor_payload, headers=auth_headers)
    monitor_id = created.json()["id"]

    updated = await client.patch(
        f"/api/monitors/{monitor_id}",
        json={"name": "Renamed", "interval_seconds": 120},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"
    assert updated.json()["interval_seconds"] == 120
    assert updated.json()["url"] == monitor_payload["url"]


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "url",
        "method",
        "expected_status",
        "timeout_seconds",
        "interval_seconds",
        "is_active",
    ],
)
async def test_update_rejects_null_for_required_fields(
    client: AsyncClient, auth_headers: dict[str, str], monitor_payload: dict[str, Any], field: str
) -> None:
    created = await client.post("/api/monitors", json=monitor_payload, headers=auth_headers)
    monitor_id = created.json()["id"]

    response = await client.patch(
        f"/api/monitors/{monitor_id}", json={field: None}, headers=auth_headers
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.json()["errors"][0]["field"] == f"body.{field}"
    current = await client.get(f"/api/monitors/{monitor_id}", headers=auth_headers)
    assert current.json()[field] == created.json()[field]


@pytest.mark.parametrize("field", ["url", "webhook_url"])
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com",
        "gopher://example.com",
        "javascript:alert(1)",
        "http://example.com:abc",
        "https://example.com:65536",
        "http://example.com:0",
        "http://93.184.216.34:abc",
        "http://[::1",
        "http:///missing-host",
        "http://example.com:",
        "http://exa mple.com/",
        "http://example.com/\r\nheader",
        "http://example.com\\path",
    ],
)
async def test_invalid_urls_rejected_on_create_and_update(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monitor_payload: dict[str, Any],
    field: str,
    url: str,
) -> None:
    response = await client.post(
        "/api/monitors", json={**monitor_payload, field: url}, headers=auth_headers
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"

    created = await client.post("/api/monitors", json=monitor_payload, headers=auth_headers)
    monitor_id = created.json()["id"]
    response = await client.patch(
        f"/api/monitors/{monitor_id}", json={field: url}, headers=auth_headers
    )
    assert response.status_code == 422
    current = await client.get(f"/api/monitors/{monitor_id}", headers=auth_headers)
    assert current.json()[field] == created.json()[field]


async def test_webhook_can_be_changed_and_cleared(
    client: AsyncClient, auth_headers: dict[str, str], monitor_payload: dict[str, Any]
) -> None:
    created = await client.post(
        "/api/monitors",
        json={**monitor_payload, "webhook_url": "http://example.com:8080/hook"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    path = f"/api/monitors/{created.json()['id']}"
    changed = await client.patch(
        path, json={"webhook_url": "https://example.com/hook"}, headers=auth_headers
    )
    assert changed.status_code == 200
    assert changed.json()["webhook_url"] == "https://example.com/hook"
    empty_patch = await client.patch(path, json={}, headers=auth_headers)
    assert empty_patch.status_code == 200
    assert empty_patch.json()["webhook_url"] == changed.json()["webhook_url"]
    cleared = await client.patch(path, json={"webhook_url": None}, headers=auth_headers)
    assert cleared.status_code == 200
    assert cleared.json()["webhook_url"] is None
    assert cleared.json()["name"] == monitor_payload["name"]


async def test_delete_monitor(
    client: AsyncClient, auth_headers: dict[str, str], monitor_payload: dict[str, Any]
) -> None:
    created = await client.post("/api/monitors", json=monitor_payload, headers=auth_headers)
    monitor_id = created.json()["id"]

    deleted = await client.delete(f"/api/monitors/{monitor_id}", headers=auth_headers)
    assert deleted.status_code == 204

    missing = await client.get(f"/api/monitors/{monitor_id}", headers=auth_headers)
    assert missing.status_code == 404


async def test_monitor_isolation_between_users(
    client: AsyncClient, auth_headers: dict[str, str], monitor_payload: dict[str, Any]
) -> None:
    created = await client.post("/api/monitors", json=monitor_payload, headers=auth_headers)
    monitor_id = created.json()["id"]

    await client.post(
        "/api/auth/register",
        json={"username": "intruder", "email": "intruder@example.com", "password": "password123"},
    )
    login = await client.post(
        "/api/auth/login",
        json={"username": "intruder", "password": "password123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert (await client.get(f"/api/monitors/{monitor_id}", headers=headers)).status_code == 404
    assert (await client.delete(f"/api/monitors/{monitor_id}", headers=headers)).status_code == 404
    listing = await client.get("/api/monitors", headers=headers)
    assert listing.json()["total"] == 0


async def test_monitor_status_without_checks(
    client: AsyncClient, auth_headers: dict[str, str], monitor_payload: dict[str, Any]
) -> None:
    created = await client.post("/api/monitors", json=monitor_payload, headers=auth_headers)
    monitor_id = created.json()["id"]

    status_response = await client.get(f"/api/monitors/{monitor_id}/status", headers=auth_headers)
    assert status_response.status_code == 200
    body = status_response.json()
    assert body["checks_24h"] == 0
    assert body["uptime_percent_24h"] is None
    assert body["open_incident_id"] is None


async def test_requires_authentication(
    client: AsyncClient, monitor_payload: dict[str, Any]
) -> None:
    assert (await client.post("/api/monitors", json=monitor_payload)).status_code == 401
    assert (await client.get("/api/monitors")).status_code == 401
