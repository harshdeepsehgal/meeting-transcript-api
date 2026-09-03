from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration from environment variables and local environment files."""

    model_config = SettingsConfigDict(
        env_file=(".env", "local/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Meeting Transcript API"
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/meeting_transcripts"
    openai_api_key: SecretStr | None = Field(default=None, repr=False)
    openai_model: str = "gpt-5.6-terra"
    openai_timeout_seconds: float = 60.0
    openai_max_retries: int = 2


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""
    return Settings()
