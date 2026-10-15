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
    FIREBASE_API_KEY: str = "AIzaSyBcuGpHGfq2dO0-3o8j1qtc2ctWbgxK9Uo"
    FIREBASE_AUTH_DOMAIN: str = "aihiring-3d070.firebaseapp.com"
    FIREBASE_PROJECT_ID: str = "aihiring-3d070"
    FIREBASE_STORAGE_BUCKET: str = "aihiring-3d070.firebasestorage.app"
    FIREBASE_MESSAGING_SENDER_ID: str = "14319993219"
    FIREBASE_APP_ID: str = "1:14319993219:web:39f05a13de221b4bfb36b6"
    FIREBASE_MEASUREMENT_ID: str = "G-EMPGKM4ZG6"
    FIREBASE_SERVICE_ACCOUNT_PATH: str = ""
    AUTH_PROVIDER: str = "hybrid"  # "local", "firebase", or "hybrid"

    # Database & PostgreSQL
    DATABASE_URL: str = "sqlite:///./app_dev.db"
    POSTGRES_ENDPOINT: str = ""
    REPOSITORY_BACKEND: str = "sql"  # "sql" or "memory"

    # Redis Cache (Upstash TLS or Local)
    REDIS_URL: str = ""
    REDIS_ENABLED: bool = True
    REDIS_DEFAULT_TTL_SECONDS: int = 300
    REDIS_CONNECT_TIMEOUT_SECONDS: float = 2.0

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
        "boards.greenhouse.io",
        "greenhouse.io",
        "jobs.lever.co",
        "lever.co",
        "jobs.ashbyhq.com",
        "ashbyhq.com",
        "apply.workable.com",
        "workable.com",
        "remoteok.com",
        "weworkremotely.com",
        "news.ycombinator.com",
    ]
    STALE_JOB_DAYS_THRESHOLD: int = 30

    # Resume Processing (Phase 3)
    ALLOWED_CV_MIME_TYPES: Union[str, List[str]] = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
    ]
    MAX_CV_SIZE_BYTES: int = 5 * 1024 * 1024  # 5 MB

    # Automated Weekly Scraping (Firecrawl & Neon / ATS Platforms)
    SCRAPING_ENABLED: bool = True
    SCRAPING_SCHEDULE_CRON: str = "0 0 * * 0"  # Midnight every Sunday
    SCRAPING_TIMEZONE: str = "Asia/Kolkata"    # Default timezone as required
    SCRAPING_MAX_RETRIES: int = 3
    SCRAPING_TIMEOUT_SECONDS: float = 20.0
    SCRAPING_CONCURRENCY_LIMIT: int = 5
    SCRAPING_BATCH_SIZE: int = 50
    SCRAPING_MAX_JOBS_PER_SOURCE: int = 100
    SCRAPING_SEED_SOURCES: str = "https://boards.greenhouse.io/gitlab,https://jobs.lever.co/leverdemo,https://jobs.ashbyhq.com/ashby"
    PERMITTED_JOB_PLATFORMS: Union[str, List[str]] = ["greenhouse", "lever", "ashby"]

    # Agent Limits (Phase 6)
    AGENT_MAX_STEPS: int = 8
    AGENT_MAX_TOOL_CALLS: int = 12
    AGENT_TIMEOUT_SECONDS: int = 90

    # Sandbox Limits (Phases 7 & 8)
    SANDBOX_PROVIDER: str = "mock"
    SANDBOX_TIMEOUT_SECONDS: int = 5
    SANDBOX_MEMORY_MB: int = 128
    SANDBOX_DOCKER_PYTHON_IMAGE: str = "ai-job-matcher-sandbox-python:3.11"
    SANDBOX_DOCKER_CPUS: float = 1.0
    SANDBOX_DOCKER_PIDS_LIMIT: int = 32

    @field_validator("ALLOWED_ORIGINS", "APPROVED_JOB_DOMAINS", "ALLOWED_CV_MIME_TYPES", "PERMITTED_JOB_PLATFORMS", mode="before")
    @classmethod
    def assemble_string_or_list(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("DATABASE_URL must not be empty.")
        clean = v.strip()
        scheme = clean.split("://")[0].lower() if "://" in clean else ""
        if scheme not in ("postgresql", "postgres", "sqlite", "sqlite+aiosqlite", "postgresql+asyncpg"):
            raise ValueError(f"Unsupported database scheme: '{scheme}'. Supported schemes: 'postgresql', 'postgres', 'sqlite'.")
        return clean


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
