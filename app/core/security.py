"""Password hashing and token issuing/verification.

Two token kinds, deliberately different in nature:

* **access** — a short-lived signed JWT. Stateless: nothing is stored, and it
  cannot be revoked before it expires.
* **refresh** — a long-lived opaque random string. Only its SHA-256 hash is
  stored, so a database leak does not hand out sessions, and because it *is*
  stored it can be revoked. That is what makes `POST /auth/logout` mean
  something (see docs/SPEC.md §11.1).
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings
from app.core.errors import UnauthorizedError

_hasher = PasswordHasher()

TokenType = Literal["access"]
ACCESS_TOKEN_TYPE: Final[TokenType] = "access"
REFRESH_TOKEN_BYTES: Final = 48


# --------------------------------------------------------------------------- #
# passwords
# --------------------------------------------------------------------------- #


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when argon2's parameters have moved on since this hash was made."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# access tokens
# --------------------------------------------------------------------------- #


def create_access_token(
    user_id: uuid.UUID,
    role: str,
    *,
    expires_delta: timedelta | None = None,
) -> str:
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an access token, or raise `UnauthorizedError`.

    Every failure — bad signature, expiry, a refresh token presented in the
    Authorization header — returns the same message, so the endpoint gives an
    attacker nothing to distinguish those cases by.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Access token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Could not validate credentials") from exc

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise UnauthorizedError("Could not validate credentials")
    return payload


# --------------------------------------------------------------------------- #
# refresh tokens
# --------------------------------------------------------------------------- #


def generate_refresh_token() -> tuple[str, str]:
    """Return `(plaintext, sha256_hash)`. Only the hash is ever persisted."""
    token = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    # Plain SHA-256, not argon2: the token is 48 bytes of CSPRNG output, so it
    # has nothing to brute-force, and lookups happen on every refresh.
    return hashlib.sha256(token.encode()).hexdigest()


def refresh_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
