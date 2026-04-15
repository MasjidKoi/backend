from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "MasjidKoi API"
    VERSION: str = "0.1.0"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # ── Database ──────────────────────────────────────────────────────────────
    # FastAPI connects through PgBouncer (transaction pool mode)
    DATABASE_URL: str

    # ── GoTrue ────────────────────────────────────────────────────────────────
    # Shared JWT secret — must match GOTRUE_JWT_SECRET in GoTrue container.
    # FastAPI uses this to verify every inbound JWT without calling GoTrue.
    GOTRUE_JWT_SECRET: str

    # JWT audience expected in every token (GoTrue default: "authenticated")
    GOTRUE_JWT_AUD: str = "authenticated"

    # Internal GoTrue base URL (container-to-container, never exposed publicly)
    GOTRUE_URL: AnyHttpUrl = "http://gotrue:9999"  # type: ignore[assignment]

    # Service-role JWT — signed with GOTRUE_JWT_SECRET, role="service_role".
    # Used by FastAPI to call GoTrue admin endpoints (create/update/delete users).
    # Generate once with: uv run python scripts/gen_service_token.py
    GOTRUE_SERVICE_ROLE_KEY: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def gotrue_base_url(self) -> str:
        return str(self.GOTRUE_URL).rstrip("/")


settings = Settings()
