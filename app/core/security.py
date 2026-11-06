"""Security utilities."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import get_settings

ph = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash password using argon2."""
    return ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against hash."""
    try:
        ph.verify(hashed, password)
        return True
    except VerifyMismatchError:
        return False


def create_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
    token_type: str = "access",
) -> str:
    """Create JWT token."""
    settings = get_settings()
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        if token_type == "refresh":
            expire = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
        else:
            expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)

    to_encode.update({"exp": expire, "type": token_type})
    encoded = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded


def decode_token(token: str) -> dict[str, Any]:
    """Decode JWT token."""
    settings = get_settings()
    payload: dict[str, Any] = jwt.decode(
        token, settings.secret_key, algorithms=[settings.algorithm]
    )
    return payload
