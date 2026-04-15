from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "MasjidKoi API"
    VERSION: str = "0.1.0"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # FastAPI connects through PgBouncer (transaction pool mode)
    DATABASE_URL: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


settings = Settings()
