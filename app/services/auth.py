"""Registration, login, and the refresh-token lifecycle."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, UnauthorizedError
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    refresh_token_expiry,
    verify_password,
)
from app.models import RefreshToken, User
from app.schemas.auth import AuthResponse, RegisterRequest, TokenPair
from app.schemas.user import UserRead


async def register(db: AsyncSession, payload: RegisterRequest) -> AuthResponse:
    user = User(
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        phone=payload.phone,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("An account with this email already exists") from exc

    tokens = await _issue_token_pair(db, user)
    await db.commit()
    return AuthResponse(**tokens.model_dump(), user=UserRead.model_validate(user))


async def login(db: AsyncSession, email: str, password: str) -> AuthResponse:
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()

    # Same error whether the address is unknown or the password is wrong: telling
    # them apart would turn this endpoint into an account-enumeration oracle.
    if user is None or not verify_password(password, user.password_hash):
        raise UnauthorizedError("Incorrect email or password")
    if not user.is_active:
        raise UnauthorizedError("This account has been deactivated")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    tokens = await _issue_token_pair(db, user)
    await db.commit()
    return AuthResponse(**tokens.model_dump(), user=UserRead.model_validate(user))


async def refresh(db: AsyncSession, raw_token: str) -> TokenPair:
    """Exchange a refresh token for a new pair, invalidating the old one.

    Rotation means a stolen token is usable at most once. If a token that was
    already rotated away comes back, we cannot tell the thief from the victim,
    so every session for that user is revoked and both must log in again.
    """
    stored = await _load_refresh_token(db, raw_token)

    if stored.revoked_at is not None:
        await _revoke_all_for_user(db, stored.user_id)
        await db.commit()
        raise UnauthorizedError("Refresh token has already been used; all sessions were revoked")

    if stored.expires_at <= datetime.now(UTC):
        raise UnauthorizedError("Refresh token has expired")

    user = await db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("This account has been deactivated")

    stored.revoked_at = datetime.now(UTC)
    tokens = await _issue_token_pair(db, user)
    await db.commit()
    return tokens


async def logout(db: AsyncSession, raw_token: str) -> None:
    """Revoke one refresh token. Idempotent: logging out twice is not an error."""
    stored = await _load_refresh_token(db, raw_token)
    if stored.revoked_at is None:
        stored.revoked_at = datetime.now(UTC)
    await db.commit()


async def _issue_token_pair(db: AsyncSession, user: User) -> TokenPair:
    raw_refresh, token_hash = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=refresh_token_expiry(),
        )
    )
    await db.flush()
    return TokenPair(
        access_token=create_access_token(user.id, user.role.value),
        refresh_token=raw_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


async def _load_refresh_token(db: AsyncSession, raw_token: str) -> RefreshToken:
    stored = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
        )
    ).scalar_one_or_none()
    if stored is None:
        raise UnauthorizedError("Invalid refresh token")
    return stored


async def _revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
