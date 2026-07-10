from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "MasjidKoi API"
    VERSION: str = "0.1.0"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    # Run the in-process APScheduler in this instance. The bundled jobs (stale
    # sweep, recurring nudges, digest, announcement publish) must fan out from
    # exactly ONE runner. Leave true for a single process; set false on extra
    # uvicorn workers / replicas (or run a dedicated scheduler worker). A Redis
    # per-job lock guards against accidental duplication — see core/scheduler.py.
    SCHEDULER_ENABLED: bool = True

    # PRD 09 #1 — days after a DELETE /users/me soft-delete before the purge job
    # anonymises the account. The 202 response and settings copy promise 30.
    ACCOUNT_PURGE_WINDOW_DAYS: int = 30

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
    # Host that CLIENTS (browser/mobile) use to fetch objects. The boto3 client
    # keeps using S3_ENDPOINT_URL (container-internal); only the public URLs we
    # hand back are built from this. Defaults to the internal endpoint. Local dev:
    # set to the published MinIO host (http://localhost:9090) so the simulator can
    # load photos. Production: the CDN / public S3 host.
    S3_PUBLIC_URL: str | None = None

    # ── SMTP ──────────────────────────────────────────────────────────────────────
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: SecretStr = SecretStr("")  # type: ignore[assignment]
    SMTP_FROM: str = "noreply@masjidkoi.com"
    SMTP_ENABLED: bool = False  # disabled by default; enable via .env in production

    # ── Redis ─────────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://redis:6379/0"

    # ── Expo Push (PRD 03 push delivery) ────────────────────────────────────────────
    # PUSH_ENABLED=false keeps the no-op LoggingTransport (dev/CI); true swaps in the
    # real Expo transport for every push-firing path. The access token is optional —
    # only required once "Enhanced Security for Push Notifications" is enabled on the
    # Expo project (expo.dev → Settings → Access Tokens). Never hard-code it.
    PUSH_ENABLED: bool = False
    EXPO_ACCESS_TOKEN: SecretStr = SecretStr("")  # type: ignore[assignment]

    # ── SSLCommerz payment gateway (PRD 05) ─────────────────────────────────────────
    # The NGO is the single merchant of record — one pooled account, per-masjid
    # attribution in our ledger. Sandbox by default; production overrides the
    # store credentials and base URL via .env. Never hard-code these.
    SSLCOMMERZ_STORE_ID: SecretStr = SecretStr("")  # type: ignore[assignment]
    SSLCOMMERZ_STORE_PASSWORD: SecretStr = SecretStr("")  # type: ignore[assignment]
    SSLCOMMERZ_BASE_URL: AnyHttpUrl = "https://sandbox.sslcommerz.com"  # type: ignore[assignment]
    # Pre-confirm "masjid receives ~৳X" estimate only. The ledger stores the
    # actual fee from the validated IPN (store_amount); the two may differ by a
    # taka or two, so donor-facing copy says "approx".
    SSLCOMMERZ_FEE_RATE: float = 0.025
    # Public URL FastAPI is reachable at — used to build the success/fail/cancel/IPN
    # callback URLs handed to the gateway. Must be publicly reachable in production.
    PUBLIC_API_BASE_URL: AnyHttpUrl = "http://localhost:8000"  # type: ignore[assignment]
    # Mobile deep-link scheme for post-payment redirects:
    # {scheme}://donation/{donation_id}?status={success|fail|cancel}
    APP_DEEP_LINK_SCHEME: str = "masjidkoi"

    # ── NGO identity (PRD 05 receipts) ──────────────────────────────────────────
    # Printed on every acknowledgment PDF. Registration number is blank until the
    # NGO supplies it; tax-deductibility wording is separately gated by the
    # platform-settings flag.
    NGO_NAME: str = "MasjidKoi Foundation"
    NGO_REGISTRATION_NUMBER: str = ""
    # Optional issuer contact, printed on the receipt header/footer. Each line
    # renders only when set, so receipts show real details and never placeholders.
    NGO_CONTACT_EMAIL: str = ""
    NGO_WEBSITE: str = ""
    NGO_ADDRESS: str = ""

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
    def cors_origins(self) -> list[str]:
        """Browser origins allowed to make credentialed cross-origin calls.

        localhost dev origins are trusted only outside production so a page
        served from localhost on a victim's machine cannot make credentialed
        requests against the live API (CODEBASE_AUDIT #21). The production origin
        value itself is tracked separately by #4.
        """
        prod_origins = ["https://admin.masjidkoi.me"]
        if self.is_production:
            return prod_origins
        return [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3000",
            *prod_origins,
        ]

    @property
    def gotrue_base_url(self) -> str:
        return str(self.GOTRUE_URL).rstrip("/")

    @property
    def s3_endpoint(self) -> str:
        return self.S3_ENDPOINT_URL.get_secret_value()

    @property
    def s3_public_url(self) -> str:
        """Public base URL for object links handed to clients (see S3_PUBLIC_URL)."""
        return (self.S3_PUBLIC_URL or self.s3_endpoint).rstrip("/")

    @property
    def aws_key(self) -> str:
        return self.AWS_ACCESS_KEY_ID.get_secret_value()

    @property
    def aws_secret(self) -> str:
        return self.AWS_SECRET_ACCESS_KEY.get_secret_value()

    @property
    def sslcommerz_base_url(self) -> str:
        return str(self.SSLCOMMERZ_BASE_URL).rstrip("/")

    @property
    def sslcommerz_store_id(self) -> str:
        return self.SSLCOMMERZ_STORE_ID.get_secret_value()

    @property
    def sslcommerz_store_password(self) -> str:
        return self.SSLCOMMERZ_STORE_PASSWORD.get_secret_value()

    @property
    def public_api_base_url(self) -> str:
        return str(self.PUBLIC_API_BASE_URL).rstrip("/")

    @property
    def expo_access_token(self) -> str:
        return self.EXPO_ACCESS_TOKEN.get_secret_value()


settings = Settings()
