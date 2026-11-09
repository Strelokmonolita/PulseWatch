"""Authentication routes."""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.schemas import (
    LoginRequest,
    RefreshRequest,
    TokenPair,
    UserCreate,
    UserRead,
)
from app.services.auth_service import AuthError, AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_token_pair(access: str, refresh: str) -> TokenPair:
    return TokenPair(access_token=access, refresh_token=refresh)


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, session: DbSession) -> UserRead:
    """Create a new account."""
    service = AuthService(session)
    try:
        user = await service.register(payload.username, payload.email, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return UserRead.model_validate(user)


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, session: DbSession) -> TokenPair:
    """Exchange credentials for an access and refresh token pair."""
    service = AuthService(session)
    try:
        user = await service.authenticate(payload.username, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc
    access, refresh = await service.issue_tokens(user)
    return _to_token_pair(access, refresh)


@router.post("/refresh", response_model=TokenPair)
async def refresh_token(payload: RefreshRequest, session: DbSession) -> TokenPair:
    """Rotate a refresh token into a fresh token pair."""
    service = AuthService(session)
    try:
        _, access, refresh = await service.refresh(payload.refresh_token)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc
    return _to_token_pair(access, refresh)


@router.get("/me", response_model=UserRead)
async def read_me(user: CurrentUser) -> UserRead:
    """Return the currently authenticated user."""
    return UserRead.model_validate(user)
