from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    api_prefix: str = "/api/v1"
    frontend_url: str = "http://localhost:3000"
    cors_origins: list[str] = ["http://localhost:3000"]

    database_url: str = "postgresql+asyncpg://syllabus:syllabus@localhost:5432/syllabus"
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "syllabus"
    redis_url: str = "redis://localhost:6379/0"

    # Celery is skipped and jobs are awaited in-process; meant for tests and quick local runs only.
    tasks_eager: bool = False

    files_root: Path = Path("./var/files")
    tmp_root: Path = Path("./var/tmp")
    max_upload_mb: int = 20

    jwt_secret: str = "change-me"
    jwt_ttl_minutes: int = 60 * 12
    session_secret: str = "change-me-too"
    cookie_name: str = "syllabus_token"
    cookie_secure: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    google_allowed_domain: str = ""
    dev_login_enabled: bool = True

    llm_provider: Literal["vllm", "fake"] = "fake"
    llm_base_url: str = "http://localhost:8000/v1"
    llm_api_key: str = "EMPTY"
    llm_model: str = "Qwen/Qwen2.5-32B-Instruct"
    llm_model_version: str = "vllm-qwen2.5-32b"
    llm_max_concurrency: int = 4
    llm_timeout_s: float = 180
    llm_max_retries: int = 3
    llm_output_language: str = "English"
    checker_version: str = "2026.10"
    evidence_match_threshold: int = 85

    precheck_ttl_s: int = 3600

    email_backend: Literal["smtp", "console"] = "console"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    email_from: str = "syllabus-ai@narxoz.kz"

    @model_validator(mode="after")
    def _no_placeholder_secrets_in_prod(self) -> "Settings":
        if self.env == "prod":
            weak = [n for n in ("jwt_secret", "session_secret") if getattr(self, n).startswith("change-me")]
            if weak or len(self.jwt_secret) < 32:
                raise ValueError(
                    f"set strong values for {', '.join(weak) or 'jwt_secret (>= 32 chars)'} in prod"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
