"""Authentication API tests."""

import pytest
from httpx import AsyncClient

from app.core.security import create_token, decode_token


async def test_register_and_login(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"username": "alice", "email": "alice@example.com", "password": "password123"},
    )
    assert response.status_code == 201
    assert response.json()["username"] == "alice"
    assert "hashed_password" not in response.json()

    login = await client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "password123"},
    )
    assert login.status_code == 200
    body = login.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


async def test_register_duplicate_username(client: AsyncClient) -> None:
    payload = {"username": "bob", "email": "bob@example.com", "password": "password123"}
    assert (await client.post("/api/auth/register", json=payload)).status_code == 201
    duplicate = await client.post(
        "/api/auth/register",
        json={**payload, "email": "other@example.com"},
    )
    assert duplicate.status_code == 409


async def test_login_with_wrong_password(client: AsyncClient) -> None:
    await client.post(
        "/api/auth/register",
        json={"username": "carol", "email": "carol@example.com", "password": "password123"},
    )
    response = await client.post(
        "/api/auth/login",
        json={"username": "carol", "password": "wrong-password"},
    )
    assert response.status_code == 401


async def test_me_requires_token(client: AsyncClient) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_me_returns_user(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    response = await client.get("/api/auth/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["username"] == "tester"


async def test_refresh_token_rotates(client: AsyncClient) -> None:
    await client.post(
        "/api/auth/register",
        json={"username": "dave", "email": "dave@example.com", "password": "password123"},
    )
    login = await client.post(
        "/api/auth/login",
        json={"username": "dave", "password": "password123"},
    )
    refresh = login.json()["refresh_token"]

    response = await client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert response.status_code == 200
    assert response.json()["access_token"]
    rotated = response.json()["refresh_token"]
    assert rotated != refresh
    assert decode_token(rotated)["jti"] != decode_token(refresh)["jti"]

    replay = await client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert replay.status_code == 401
    me = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}
    )
    assert me.status_code == 200

    next_rotation = await client.post("/api/auth/refresh", json={"refresh_token": rotated})
    assert next_rotation.status_code == 200
    assert next_rotation.json()["refresh_token"] not in {refresh, rotated}
    assert (
        await client.post("/api/auth/refresh", json={"refresh_token": rotated})
    ).status_code == 401


async def test_logins_issue_independent_refresh_tokens(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    payload = {"username": "tester", "password": "supersecret1"}
    first = (await client.post("/api/auth/login", json=payload)).json()["refresh_token"]
    second = (await client.post("/api/auth/login", json=payload)).json()["refresh_token"]
    assert first != second
    for token in (first, second):
        response = await client.post("/api/auth/refresh", json={"refresh_token": token})
        assert response.status_code == 200
        assert response.json()["refresh_token"] not in {first, second}


async def test_unregistered_refresh_token_rejected(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    user = (await client.get("/api/auth/me", headers=auth_headers)).json()
    token = create_token({"sub": str(user["id"])}, token_type="refresh")
    response = await client.post("/api/auth/refresh", json={"refresh_token": token})
    assert response.status_code == 401


async def test_access_token_cannot_refresh(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    login = await client.post(
        "/api/auth/login",
        json={"username": "tester", "password": "supersecret1"},
    )
    access = login.json()["access_token"]
    response = await client.post("/api/auth/refresh", json={"refresh_token": access})
    assert response.status_code == 401


async def test_refresh_token_cannot_authorize(client: AsyncClient) -> None:
    await client.post(
        "/api/auth/register",
        json={"username": "erin", "email": "erin@example.com", "password": "password123"},
    )
    login = await client.post(
        "/api/auth/login",
        json={"username": "erin", "password": "password123"},
    )
    refresh = login.json()["refresh_token"]
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {refresh}"})
    assert response.status_code == 401


@pytest.mark.parametrize("password", ["short", ""])
async def test_weak_password_rejected(client: AsyncClient, password: str) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"username": "frank", "email": "frank@example.com", "password": password},
    )
    assert response.status_code == 422
