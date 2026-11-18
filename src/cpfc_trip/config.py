from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["development", "test", "preview", "production"] = "development"
    frontend_origin: str = "http://localhost:3000"
    additional_frontend_origins: str = ""
    static_export_dir: str = ""
    preview_access_required: bool = True
    preview_username: str = ""
    preview_password: SecretStr = SecretStr("")
    database_url: str = "postgresql+asyncpg://cpfc:cpfc@localhost:5432/cpfc"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "cpfc-trip-v3"
    temporal_api_key: SecretStr = SecretStr("")
    temporal_tls: bool = False
    worker_build_id: str = ""
    planner_mode: Literal["live", "recorded"] = "live"
    searchapi_api_key: SecretStr = SecretStr("")
    searchapi_enabled: bool = True
    # How long a stored search result is reused; 0 always searches live.
    search_cache_ttl_minutes: int = Field(1440, ge=0)

    @model_validator(mode="after")
    def production(self) -> Self:
        if (
            self.app_env == "preview"
            and self.preview_access_required
            and (not self.preview_username or not self.preview_password.get_secret_value())
        ):
            raise ValueError("Preview requires access credentials")
        if self.app_env == "production":
            if self.planner_mode != "live":
                raise ValueError("Production requires live planning")
            if not self.temporal_tls or not self.worker_build_id:
                raise ValueError("Production requires Temporal TLS and an immutable build ID")
        return self
