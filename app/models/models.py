"""SQLAlchemy models."""

import enum

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """User model."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    monitors: Mapped[list["Monitor"]] = relationship(
        "Monitor", back_populates="user", cascade="all, delete-orphan"
    )


class Monitor(Base, TimestampMixin):
    """Website monitor model."""

    __tablename__ = "monitors"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    method: Mapped[str] = mapped_column(String(10), default="GET", nullable=False)
    expected_status: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="monitors")
    checks: Mapped[list["Check"]] = relationship(
        "Check", back_populates="monitor", cascade="all, delete-orphan"
    )
    incidents: Mapped[list["Incident"]] = relationship(
        "Incident", back_populates="monitor", cascade="all, delete-orphan"
    )


class Check(Base, TimestampMixin):
    """Health check result model."""

    __tablename__ = "checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_id: Mapped[int] = mapped_column(ForeignKey("monitors.id"), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    monitor: Mapped["Monitor"] = relationship("Monitor", back_populates="checks")


class IncidentStatus(enum.StrEnum):
    """Incident status enum."""

    OPEN = "open"
    CLOSED = "closed"


class Incident(Base, TimestampMixin):
    """Incident model."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_id: Mapped[int] = mapped_column(ForeignKey("monitors.id"), nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(
        SQLEnum(IncidentStatus), default=IncidentStatus.OPEN, nullable=False
    )
    opened_at: Mapped[str | None] = mapped_column(String(255), nullable=True)
    closed_at: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    monitor: Mapped["Monitor"] = relationship("Monitor", back_populates="incidents")
