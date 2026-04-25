from pydantic import AnyHttpUrl, SecretStr
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

    # ── S3 / MinIO ────────────────────────────────────────────────────────────────
    # Development: http://minio:9000 (container-to-container)
    # Production:  set to actual S3 or MinIO endpoint via env var
    S3_ENDPOINT_URL: SecretStr = SecretStr("http://minio:9000")  # type: ignore[assignment]
    AWS_ACCESS_KEY_ID: SecretStr = SecretStr("minioadmin")  # type: ignore[assignment]
    AWS_SECRET_ACCESS_KEY: SecretStr = SecretStr("minioadmin")  # type: ignore[assignment]
    S3_REGION: str = "us-east-1"
    S3_BUCKET_IMPORTS: str = "masjidkoi-imports"
    S3_BUCKET_PHOTOS: str = "masjidkoi-photos"
    S3_BUCKET_AVATARS: str = "masjidkoi-avatars"

    # ── SMTP ──────────────────────────────────────────────────────────────────────
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: SecretStr = SecretStr("")  # type: ignore[assignment]
    SMTP_FROM: str = "noreply@masjidkoi.com"
    SMTP_ENABLED: bool = False  # disabled by default; enable via .env in production

    # ── Redis ─────────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://redis:6379/0"

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

    @property
    def s3_endpoint(self) -> str:
        return self.S3_ENDPOINT_URL.get_secret_value()

    @property
    def aws_key(self) -> str:
        return self.AWS_ACCESS_KEY_ID.get_secret_value()

    @property
    def aws_secret(self) -> str:
        return self.AWS_SECRET_ACCESS_KEY.get_secret_value()


settings = Settings()
