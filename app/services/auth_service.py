"""Authentication service."""

from datetime import timedelta

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.models import User
from app.repositories.user_repository import UserRepository


class AuthError(Exception):
    """Authentication failure."""

    def __init__(self, message: str, code: str = "auth_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class AuthService:
    """Registration, login and token rotation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def register(self, username: str, email: str, password: str) -> User:
        if await self.users.get_by_username(username) is not None:
            raise AuthError("username is already taken", "username_taken")
        if await self.users.get_by_email(email) is not None:
            raise AuthError("email is already registered", "email_taken")

        user = User(
            username=username,
            email=str(email),
            hashed_password=hash_password(password),
        )
        return await self.users.create(user)

    async def authenticate(self, username: str, password: str) -> User:
        user = await self.users.get_by_username(username)
        if user is None:
            user = await self.users.get_by_email(username)
        if user is None or not verify_password(password, user.hashed_password):
            raise AuthError("invalid username or password", "invalid_credentials")
        if not user.is_active:
            raise AuthError("user is inactive", "inactive_user")
        return user

    def issue_tokens(self, user: User) -> tuple[str, str]:
        settings = get_settings()
        access = create_token(
            {"sub": str(user.id)},
            timedelta(minutes=settings.access_token_expire_minutes),
            token_type="access",
        )
        refresh = create_token(
            {"sub": str(user.id)},
            timedelta(days=settings.refresh_token_expire_days),
            token_type="refresh",
        )
        return access, refresh

    async def refresh(self, refresh_token: str) -> tuple[User, str, str]:
        try:
            payload = decode_token(refresh_token)
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("refresh token expired", "token_expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("invalid refresh token", "invalid_token") from exc

        if payload.get("type") != "refresh":
            raise AuthError("invalid token type", "invalid_token")

        subject = payload.get("sub")
        if subject is None:
            raise AuthError("invalid token payload", "invalid_token")
        try:
            user_id = int(str(subject))
        except ValueError as exc:
            raise AuthError("invalid token payload", "invalid_token") from exc

        user = await self.users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise AuthError("user not found", "invalid_token")

        access, rotated = self.issue_tokens(user)
        return user, access, rotated
