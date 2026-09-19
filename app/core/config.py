"""Application settings, loaded from the environment / .env file."""

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, PostgresDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- app ---
    PROJECT_NAME: str = "BarberHub"
    ENVIRONMENT: Literal["local", "test", "staging", "production"] = "local"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: list[str] = Field(default_factory=list)

    # --- database ---
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5434
    POSTGRES_USER: str = "barberhub"
    POSTGRES_PASSWORD: str = "barberhub"
    POSTGRES_DB: str = "barberhub"
    DB_ECHO: bool = False

    # --- auth ---
    JWT_SECRET_KEY: str = "insecure-dev-key-override-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # --- rate limiting ---
    RATE_LIMIT_ENABLED: bool = True
    LOG_LEVEL: str = "INFO"

    # --- booking rules (see docs/SPEC.md §5) ---
    SLOT_STEP_MINUTES: int = 15
    MIN_BOOKING_LEAD_MINUTES: int = 30
    APPOINTMENT_CHANGE_CUTOFF_MINUTES: int = 120
    DEFAULT_TIMEZONE: str = "Asia/Almaty"

    # --- redis / celery ---
    REDIS_URL: str = "redis://localhost:6380/0"
    #: Queue notification tasks. Off under test so the suite does not need a
    #: running broker.
    NOTIFICATIONS_ENABLED: bool = True

    # --- smtp ---
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_TLS: bool = False
    EMAILS_FROM_EMAIL: str = "no-reply@barberhub.local"
    EMAILS_FROM_NAME: str = "BarberHub"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def async_database_url(self) -> str:
        """URL for the FastAPI request path (asyncpg)."""
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.POSTGRES_USER,
                password=self.POSTGRES_PASSWORD,
                host=self.POSTGRES_HOST,
                port=self.POSTGRES_PORT,
                path=self.POSTGRES_DB,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """URL for Alembic and Celery workers (psycopg), which are not async."""
        return str(
            PostgresDsn.build(
                scheme="postgresql+psycopg",
                username=self.POSTGRES_USER,
                password=self.POSTGRES_PASSWORD,
                host=self.POSTGRES_HOST,
                port=self.POSTGRES_PORT,
                path=self.POSTGRES_DB,
            )
        )

    @property
    def default_tz(self) -> ZoneInfo:
        return ZoneInfo(self.DEFAULT_TIMEZONE)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
