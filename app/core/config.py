"""Application configuration and environment settings."""

from functools import lru_cache
from typing import List, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Core Application
    APP_NAME: str = "AI Job Matcher"
    APP_VERSION: str = "0.1.0"
    APP_ENV: str = "development"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # Security & Auth
    SECRET_KEY: str = "dev-secret-key-change-in-production-min-32-chars-long"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    ALGORITHM: str = "HS256"
    GITHUB_TOKEN: str = ""

    # Rate Limiting & Security (Developer A)
    RATE_LIMIT_LOGIN_MAX: int = 50
    RATE_LIMIT_LOGIN_WINDOW_SECONDS: int = 60
    MAX_PAYLOAD_SIZE_BYTES: int = 5 * 1024 * 1024  # 5 MB

    # Firebase Authentication
    FIREBASE_API_KEY: str = ""
    FIREBASE_AUTH_DOMAIN: str = "aihiring-3d070.firebaseapp.com"
    FIREBASE_PROJECT_ID: str = "aihiring-3d070"
    FIREBASE_STORAGE_BUCKET: str = "aihiring-3d070.firebasestorage.app"
    FIREBASE_MESSAGING_SENDER_ID: str = "14319993219"
    FIREBASE_APP_ID: str = ""
    FIREBASE_MEASUREMENT_ID: str = "G-EMPGKM4ZG6"
    FIREBASE_SERVICE_ACCOUNT_PATH: str = ""
    AUTH_PROVIDER: str = "hybrid"  # "local", "firebase", or "hybrid"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./app_dev.db"

    # CORS
    ALLOWED_ORIGINS: Union[str, List[str]] = [
        "http://localhost:3000",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]

    PRIMARY_LLM_PROVIDER: str = "gemini"
    PRIMARY_LLM_API_KEY: str = ""
    PRIMARY_LLM_MODEL: str = "gemini-3.8-flash"
    SECONDARY_LLM_PROVIDER: str = "gemini"
    SECONDARY_LLM_API_KEY: str = ""
    SECONDARY_LLM_MODEL: str = "gemini-3.7-flash"
    BACKUP_LLM_PROVIDER: str = "gemini"
    BACKUP_LLM_API_KEY: str = ""
    BACKUP_LLM_MODEL: str = "gemini-3.6-flash"

    # Vector Embeddings (Gemini Embedding 2 / Local)
    EMBEDDING_PROVIDER: str = "gemini"
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = "gemini-embedding-2"

    # Firecrawl & Job Ingestion (Phase 3)
    FIRECRAWL_API_KEY: str = ""
    FIRECRAWL_BASE_URL: str = "https://api.firecrawl.dev"
    FIRECRAWL_TIMEOUT_SECONDS: int = 15
    APPROVED_JOB_DOMAINS: Union[str, List[str]] = [
        "approved-board.example.com",
        "careers.techcompany.com",
    ]
    STALE_JOB_DAYS_THRESHOLD: int = 30

    # Resume Processing (Phase 3)
    ALLOWED_CV_MIME_TYPES: Union[str, List[str]] = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
    ]
    MAX_CV_SIZE_BYTES: int = 5 * 1024 * 1024  # 5 MB

    # Agent Limits (Phase 6)
    AGENT_MAX_STEPS: int = 8
    AGENT_MAX_TOOL_CALLS: int = 12
    AGENT_TIMEOUT_SECONDS: int = 90

    # Sandbox Limits (Phases 7 & 8)
    SANDBOX_PROVIDER: str = "mock"
    SANDBOX_TIMEOUT_SECONDS: int = 5
    SANDBOX_MEMORY_MB: int = 128

    @field_validator("ALLOWED_ORIGINS", "APPROVED_JOB_DOMAINS", "ALLOWED_CV_MIME_TYPES", mode="before")
    @classmethod
    def assemble_string_or_list(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
