"""Authentication endpoints."""

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser
from app.core.rate_limit import limit_auth
from app.db.session import DbSession
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
)
from app.schemas.user import UserRead
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_auth)],
    summary="Create an account",
    description="Self-service signup. Only CUSTOMER and SHOP_OWNER may be chosen; "
    "BARBER is granted by a shop owner and ADMIN is never granted over the API.",
)
async def register(payload: RegisterRequest, db: DbSession) -> AuthResponse:
    return await auth_service.register(db, payload)


@router.post(
    "/login",
    response_model=AuthResponse,
    dependencies=[Depends(limit_auth)],
    summary="Exchange credentials for tokens",
    description="Rate limited per client address.",
)
async def login(payload: LoginRequest, db: DbSession) -> AuthResponse:
    return await auth_service.login(db, payload.email, payload.password)


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Rotate a refresh token",
    description="Returns a new pair and revokes the presented token. Replaying an "
    "already-rotated token revokes every session for that user.",
)
async def refresh(payload: RefreshRequest, db: DbSession) -> TokenPair:
    return await auth_service.refresh(db, payload.refresh_token)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a refresh token",
)
async def logout(payload: LogoutRequest, db: DbSession) -> None:
    await auth_service.logout(db, payload.refresh_token)


@router.get("/me", response_model=UserRead, summary="The authenticated user")
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
