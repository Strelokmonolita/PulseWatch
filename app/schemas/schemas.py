"""Pydantic schemas for the API."""

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.ssrf import parse_http_url
from app.models.models import IncidentStatus

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


class ORMModel(BaseModel):
    """Base schema with ORM mode enabled."""

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- users


class UserCreate(BaseModel):
    """Registration payload."""

    username: str = Field(min_length=3, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        """Reject obviously malformed addresses."""
        email = value.strip().lower()
        if not _EMAIL_PATTERN.match(email):
            raise ValueError("must be a valid email address")
        return email


class UserRead(ORMModel):
    """Public user representation."""

    id: int
    username: str
    email: str
    is_active: bool
    created_at: datetime


# --------------------------------------------------------------------------- auth


class TokenPair(BaseModel):
    """Access and refresh tokens."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """Refresh token payload."""

    refresh_token: str


class LoginRequest(BaseModel):
    """Login payload."""

    username: str
    password: str


# --------------------------------------------------------------------------- monitors


class MonitorBase(BaseModel):
    """Shared monitor fields."""

    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=2048)
    method: str = "GET"
    expected_status: int = Field(default=200, ge=100, le=599)
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    interval_seconds: int = Field(default=60, ge=30, le=86400)
    is_active: bool = True
    webhook_url: str | None = Field(default=None, max_length=2048)

    @field_validator("url", "webhook_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        """Accept well-formed http(s) URLs for monitors and webhooks."""
        if value is None:
            return None
        value = value.strip()
        parse_http_url(value)
        return value

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        """Restrict the HTTP method to a safe read-only set."""
        method = value.upper()
        if method not in {"GET", "HEAD", "OPTIONS"}:
            raise ValueError("method must be one of GET, HEAD, OPTIONS")
        return method


class MonitorCreate(MonitorBase):
    """Monitor creation payload."""


class MonitorUpdate(BaseModel):
    """Partial monitor update payload."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    method: str | None = None
    expected_status: int | None = Field(default=None, ge=100, le=599)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    interval_seconds: int | None = Field(default=None, ge=30, le=86400)
    is_active: bool | None = None
    webhook_url: str | None = Field(default=None, max_length=2048)

    @field_validator(
        "name",
        "url",
        "method",
        "expected_status",
        "timeout_seconds",
        "interval_seconds",
        "is_active",
        mode="before",
    )
    @classmethod
    def reject_null(cls, value: Any) -> Any:
        """Omitted fields are allowed, but database-required fields cannot be cleared."""
        if value is None:
            raise ValueError("field must not be null")
        return value

    @field_validator("url", "webhook_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        """Apply the same URL validation to partial updates as to creation."""
        if value is None:
            return None
        value = value.strip()
        parse_http_url(value)
        return value

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str | None) -> str | None:
        """Restrict the HTTP method to a safe read-only set."""
        if value is None:
            return None
        method = value.upper()
        if method not in {"GET", "HEAD", "OPTIONS"}:
            raise ValueError("method must be one of GET, HEAD, OPTIONS")
        return method


class MonitorRead(ORMModel):
    """Monitor representation."""

    id: int
    user_id: int
    name: str
    url: str
    method: str
    expected_status: int
    timeout_seconds: int
    interval_seconds: int
    is_active: bool
    webhook_url: str | None
    created_at: datetime
    updated_at: datetime


class MonitorStatus(BaseModel):
    """Aggregated monitor status."""

    monitor_id: int
    is_up: bool
    last_checked_at: datetime | None
    last_status_code: int | None
    response_time_ms: int | None
    open_incident_id: int | None
    checks_24h: int
    failures_24h: int
    uptime_percent_24h: float | None


# --------------------------------------------------------------------------- checks


class CheckRead(ORMModel):
    """Check result representation."""

    id: int
    monitor_id: int
    status_code: int | None
    response_time_ms: int | None
    is_success: bool
    error_message: str | None
    created_at: datetime


# --------------------------------------------------------------------------- incidents


class IncidentRead(ORMModel):
    """Incident representation."""

    id: int
    monitor_id: int
    status: IncidentStatus
    opened_at: datetime | None
    closed_at: datetime | None
    message: str | None
    created_at: datetime


# --------------------------------------------------------------------------- pagination


class Page[T](BaseModel):
    """Generic paginated response."""

    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    """Uniform error payload."""

    detail: str
    code: str = "error"
