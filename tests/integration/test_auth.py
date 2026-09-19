"""Auth endpoints and the RBAC guard (docs/SPEC.md §2, §6)."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models import RefreshToken, User, UserRole
from tests.conftest import REGISTER_PASSWORD, unique_email

PayloadFactory = Callable[..., dict[str, str]]
UserFactory = Callable[..., Awaitable[User]]
HeaderFactory = Callable[[User], dict[str, str]]


class TestRegister:
    async def test_creates_a_customer_and_returns_tokens(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        response = await client.post("/api/v1/auth/register", json=register_payload())

        assert response.status_code == 201
        body = response.json()
        assert body["user"]["role"] == "CUSTOMER"
        assert body["token_type"] == "bearer"
        assert body["access_token"] and body["refresh_token"]
        assert "password" not in body["user"]
        assert "password_hash" not in body["user"]

    async def test_shop_owner_may_self_register(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        response = await client.post(
            "/api/v1/auth/register", json=register_payload(role="SHOP_OWNER")
        )

        assert response.status_code == 201
        assert response.json()["user"]["role"] == "SHOP_OWNER"

    async def test_admin_cannot_be_self_assigned(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        """Privilege escalation via the signup body must be impossible."""
        response = await client.post("/api/v1/auth/register", json=register_payload(role="ADMIN"))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_barber_cannot_be_self_assigned(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        response = await client.post("/api/v1/auth/register", json=register_payload(role="BARBER"))

        assert response.status_code == 422

    async def test_duplicate_email_conflicts(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        payload = register_payload()
        assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201

        response = await client.post("/api/v1/auth/register", json=payload)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"

    async def test_duplicate_email_conflicts_across_letter_case(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        payload = register_payload(email="Dana.Test@Example.com")
        assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201

        response = await client.post(
            "/api/v1/auth/register", json=payload | {"email": "dana.test@example.com"}
        )

        assert response.status_code == 409

    async def test_password_is_stored_hashed(
        self, client: AsyncClient, db: AsyncSession, register_payload: PayloadFactory
    ) -> None:
        payload = register_payload()
        await client.post("/api/v1/auth/register", json=payload)

        user = (await db.execute(select(User).where(User.email == payload["email"]))).scalar_one()

        assert user.password_hash != payload["password"]
        assert user.password_hash.startswith("$argon2")

    async def test_short_password_is_rejected(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        response = await client.post(
            "/api/v1/auth/register", json=register_payload(password="Ab1!")
        )

        assert response.status_code == 422

    async def test_all_digit_password_is_rejected(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        response = await client.post(
            "/api/v1/auth/register", json=register_payload(password="1234567890")
        )

        assert response.status_code == 422

    async def test_malformed_email_is_rejected(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        response = await client.post(
            "/api/v1/auth/register", json=register_payload(email="not-an-email")
        )

        assert response.status_code == 422


class TestLogin:
    async def test_valid_credentials_return_tokens(
        self, client: AsyncClient, make_user: UserFactory
    ) -> None:
        user = await make_user()

        response = await client.post(
            "/api/v1/auth/login", json={"email": user.email, "password": REGISTER_PASSWORD}
        )

        assert response.status_code == 200
        assert response.json()["user"]["email"] == user.email

    async def test_email_is_case_insensitive(
        self, client: AsyncClient, make_user: UserFactory
    ) -> None:
        user = await make_user()

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": user.email.upper(), "password": REGISTER_PASSWORD},
        )

        assert response.status_code == 200

    async def test_wrong_password_is_rejected(
        self, client: AsyncClient, make_user: UserFactory
    ) -> None:
        user = await make_user()

        response = await client.post(
            "/api/v1/auth/login", json={"email": user.email, "password": "WrongPass1!"}
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    async def test_unknown_email_gives_the_same_error_as_a_wrong_password(
        self, client: AsyncClient, make_user: UserFactory
    ) -> None:
        """Distinguishable errors would make this an account-enumeration oracle."""
        user = await make_user()

        wrong_password = await client.post(
            "/api/v1/auth/login", json={"email": user.email, "password": "WrongPass1!"}
        )
        unknown_email = await client.post(
            "/api/v1/auth/login", json={"email": unique_email(), "password": REGISTER_PASSWORD}
        )

        assert wrong_password.status_code == unknown_email.status_code == 401
        assert wrong_password.json() == unknown_email.json()

    async def test_deactivated_account_cannot_log_in(
        self, client: AsyncClient, db: AsyncSession, make_user: UserFactory
    ) -> None:
        user = await make_user()
        user.is_active = False
        await db.flush()

        response = await client.post(
            "/api/v1/auth/login", json={"email": user.email, "password": REGISTER_PASSWORD}
        )

        assert response.status_code == 401


class TestRefresh:
    async def test_returns_a_new_pair(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        registered = (await client.post("/api/v1/auth/register", json=register_payload())).json()

        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": registered["refresh_token"]}
        )

        assert response.status_code == 200
        assert response.json()["refresh_token"] != registered["refresh_token"]

    async def test_rotated_token_stops_working(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        registered = (await client.post("/api/v1/auth/register", json=register_payload())).json()
        first = registered["refresh_token"]
        await client.post("/api/v1/auth/refresh", json={"refresh_token": first})

        replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": first})

        assert replay.status_code == 401

    async def test_replay_revokes_every_session(
        self, client: AsyncClient, db: AsyncSession, register_payload: PayloadFactory
    ) -> None:
        """A replayed token means one of the two holders is a thief, and we
        cannot tell which — so both are logged out."""
        registered = (await client.post("/api/v1/auth/register", json=register_payload())).json()
        first = registered["refresh_token"]
        second = (await client.post("/api/v1/auth/refresh", json={"refresh_token": first})).json()[
            "refresh_token"
        ]

        await client.post("/api/v1/auth/refresh", json={"refresh_token": first})
        response = await client.post("/api/v1/auth/refresh", json={"refresh_token": second})

        assert response.status_code == 401
        user_id = uuid.UUID(registered["user"]["id"])
        tokens = (
            (await db.execute(select(RefreshToken).where(RefreshToken.user_id == user_id)))
            .scalars()
            .all()
        )
        assert tokens and all(t.revoked_at is not None for t in tokens)

    async def test_unknown_token_is_rejected(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "nothing-like-a-real-token"}
        )

        assert response.status_code == 401

    async def test_expired_token_is_rejected(
        self, client: AsyncClient, db: AsyncSession, register_payload: PayloadFactory
    ) -> None:
        registered = (await client.post("/api/v1/auth/register", json=register_payload())).json()
        stored = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == uuid.UUID(registered["user"]["id"])
                )
            )
        ).scalar_one()
        stored.expires_at = stored.created_at - timedelta(days=1)
        await db.flush()

        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": registered["refresh_token"]}
        )

        assert response.status_code == 401
        assert "expired" in response.json()["error"]["message"].lower()


class TestLogout:
    async def test_revokes_the_refresh_token(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        registered = (await client.post("/api/v1/auth/register", json=register_payload())).json()
        token = registered["refresh_token"]

        logout = await client.post("/api/v1/auth/logout", json={"refresh_token": token})

        assert logout.status_code == 204
        replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
        assert replay.status_code == 401

    async def test_is_idempotent(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        registered = (await client.post("/api/v1/auth/register", json=register_payload())).json()
        token = registered["refresh_token"]

        first = await client.post("/api/v1/auth/logout", json={"refresh_token": token})
        second = await client.post("/api/v1/auth/logout", json={"refresh_token": token})

        assert first.status_code == second.status_code == 204


class TestMe:
    async def test_returns_the_authenticated_user(
        self, client: AsyncClient, make_user: UserFactory, auth_headers: HeaderFactory
    ) -> None:
        user = await make_user(role=UserRole.SHOP_OWNER)

        response = await client.get("/api/v1/auth/me", headers=auth_headers(user))

        assert response.status_code == 200
        assert response.json()["id"] == str(user.id)
        assert response.json()["role"] == "SHOP_OWNER"

    async def test_missing_header_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/auth/me")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    async def test_garbage_token_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"}
        )

        assert response.status_code == 401

    async def test_expired_token_is_rejected(
        self, client: AsyncClient, make_user: UserFactory
    ) -> None:
        user = await make_user()
        token = create_access_token(user.id, user.role.value, expires_delta=timedelta(minutes=-1))

        response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401
        assert "expired" in response.json()["error"]["message"].lower()

    async def test_token_for_a_deleted_user_is_rejected(self, client: AsyncClient) -> None:
        token = create_access_token(uuid.uuid4(), "CUSTOMER")

        response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401

    async def test_token_survives_but_deactivation_takes_effect_immediately(
        self,
        client: AsyncClient,
        db: AsyncSession,
        make_user: UserFactory,
        auth_headers: HeaderFactory,
    ) -> None:
        user = await make_user()
        headers = auth_headers(user)
        assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

        user.is_active = False
        await db.flush()

        assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401

    async def test_refresh_token_is_not_accepted_as_a_bearer_token(
        self, client: AsyncClient, register_payload: PayloadFactory
    ) -> None:
        registered = (await client.post("/api/v1/auth/register", json=register_payload())).json()

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {registered['refresh_token']}"},
        )

        assert response.status_code == 401


class TestRoleGuard:
    """`require_roles` gates on role; admins pass every gate."""

    async def test_role_claim_in_the_token_is_not_trusted(
        self, client: AsyncClient, make_user: UserFactory
    ) -> None:
        """A forged role claim must not grant anything: the role is re-read from
        the database on every request."""
        user = await make_user(role=UserRole.CUSTOMER)
        token = create_access_token(user.id, UserRole.ADMIN.value)

        response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json()["role"] == "CUSTOMER"
