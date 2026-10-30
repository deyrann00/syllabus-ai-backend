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

    # stub: no Google account yet, files go to drive_stub_root. google: see app/storage/drive.py.
    drive_provider: Literal["stub", "google"] = "stub"
    drive_stub_root: Path = Path("./var/drive")
    # Used for versions whose folder has no drive_folder_id.
    drive_default_folder_id: str = ""

    # stub: no auth service yet, the bearer token is `stub:<email>`. http: see app/auth/client.py.
    auth_provider: Literal["stub", "http"] = "stub"
    auth_service_url: str = ""
    auth_service_verify_path: str = "/verify"
    auth_service_timeout_s: float = 5

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
    def _no_stubs_in_prod(self) -> "Settings":
        if self.env == "prod" and self.auth_provider == "stub":
            raise ValueError("AUTH_PROVIDER=stub accepts anyone, it cannot run in prod")
        if self.env == "prod" and self.drive_provider == "stub":
            raise ValueError("DRIVE_PROVIDER=stub keeps files out of Google Drive, it cannot run in prod")
        if self.auth_provider == "http" and not self.auth_service_url:
            raise ValueError("AUTH_PROVIDER=http needs AUTH_SERVICE_URL")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
