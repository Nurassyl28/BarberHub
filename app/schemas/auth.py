"""Auth request/response bodies."""

from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import UserRole
from app.schemas.user import UserRead

#: Roles a stranger may self-assign. `BARBER` is granted by a shop owner adding
#: them to a shop; `ADMIN` is never granted over the API.
SelfAssignableRole = Literal[UserRole.CUSTOMER, UserRole.SHOP_OWNER]

Password = Annotated[str, Field(min_length=8, max_length=128)]


class RegisterRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=32)
    password: Password
    role: SelfAssignableRole = UserRole.CUSTOMER

    @field_validator("password")
    @classmethod
    def password_is_not_trivial(cls, value: str) -> str:
        if value.isdigit() or value.isalpha():
            raise ValueError("Password must mix letters and digits")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(RefreshRequest):
    pass


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")


class AuthResponse(TokenPair):
    user: UserRead
