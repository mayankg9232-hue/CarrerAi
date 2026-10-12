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

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./app_dev.db"

    # CORS
    ALLOWED_ORIGINS: Union[str, List[str]] = [
        "http://localhost:3000",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]

    # External LLM Providers (Introduced Phase 4)
    PRIMARY_LLM_PROVIDER: str = "openai"
    PRIMARY_LLM_API_KEY: str = ""
    PRIMARY_LLM_MODEL: str = "gpt-4o-mini"
    SECONDARY_LLM_PROVIDER: str = "gemini"
    SECONDARY_LLM_API_KEY: str = ""
    SECONDARY_LLM_MODEL: str = "gemini-1.5-flash"

    # Firecrawl (Introduced Phase 3)
    FIRECRAWL_API_KEY: str = ""
    FIRECRAWL_BASE_URL: str = "https://api.firecrawl.dev"

    # Agent Limits (Phase 6)
    AGENT_MAX_STEPS: int = 8
    AGENT_MAX_TOOL_CALLS: int = 12
    AGENT_TIMEOUT_SECONDS: int = 90

    # Sandbox Limits (Phases 7 & 8)
    SANDBOX_PROVIDER: str = "mock"
    SANDBOX_TIMEOUT_SECONDS: int = 5
    SANDBOX_MEMORY_MB: int = 128

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
