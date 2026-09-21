"""Password hashing and token primitives."""

import uuid
from datetime import timedelta

import jwt
import pytest

from app.core.config import settings
from app.core.errors import UnauthorizedError
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)


class TestPasswords:
    def test_hash_is_not_the_password(self) -> None:
        assert hash_password("Sup3rSecret!") != "Sup3rSecret!"

    def test_hashes_are_salted(self) -> None:
        """Two identical passwords must not produce identical hashes."""
        assert hash_password("Sup3rSecret!") != hash_password("Sup3rSecret!")

    def test_verify_accepts_the_right_password(self) -> None:
        assert verify_password("Sup3rSecret!", hash_password("Sup3rSecret!"))

    def test_verify_rejects_the_wrong_password(self) -> None:
        assert not verify_password("nope", hash_password("Sup3rSecret!"))

    def test_verify_rejects_garbage_instead_of_raising(self) -> None:
        assert not verify_password("anything", "not-a-hash")


class TestAccessTokens:
    def test_round_trips_subject_and_role(self) -> None:
        user_id = uuid.uuid4()

        payload = decode_access_token(create_access_token(user_id, "BARBER"))

        assert payload["sub"] == str(user_id)
        assert payload["role"] == "BARBER"
        assert payload["type"] == "access"

    def test_expired_token_is_rejected(self) -> None:
        token = create_access_token(uuid.uuid4(), "CUSTOMER", expires_delta=timedelta(seconds=-1))

        with pytest.raises(UnauthorizedError, match="expired"):
            decode_access_token(token)

    def test_token_signed_with_another_key_is_rejected(self) -> None:
        forged = jwt.encode(
            {"sub": str(uuid.uuid4()), "type": "access", "exp": 9999999999},
            "another-secret-of-at-least-32-bytes-length",
            algorithm=settings.JWT_ALGORITHM,
        )

        with pytest.raises(UnauthorizedError):
            decode_access_token(forged)

    def test_unsigned_token_is_rejected(self) -> None:
        """`alg: none` must never be honoured."""
        forged = jwt.encode(
            {"sub": str(uuid.uuid4()), "type": "access", "exp": 9999999999},
            key="",
            algorithm="none",
        )

        with pytest.raises(UnauthorizedError):
            decode_access_token(forged)

    def test_token_without_a_type_claim_is_rejected(self) -> None:
        forged = jwt.encode(
            {"sub": str(uuid.uuid4()), "exp": 9999999999},
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        with pytest.raises(UnauthorizedError):
            decode_access_token(forged)

    def test_each_token_has_a_distinct_id(self) -> None:
        user_id = uuid.uuid4()

        first = decode_access_token(create_access_token(user_id, "CUSTOMER"))
        second = decode_access_token(create_access_token(user_id, "CUSTOMER"))

        assert first["jti"] != second["jti"]


class TestRefreshTokens:
    def test_plaintext_is_never_the_stored_value(self) -> None:
        token, stored = generate_refresh_token()

        assert token != stored
        assert stored == hash_refresh_token(token)

    def test_tokens_are_unique(self) -> None:
        assert len({generate_refresh_token()[0] for _ in range(100)}) == 100

    def test_hash_is_deterministic(self) -> None:
        token, _ = generate_refresh_token()

        assert hash_refresh_token(token) == hash_refresh_token(token)
