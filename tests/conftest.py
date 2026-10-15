import os
# Default test runs to memory repository backend unless explicitly testing SQL persistence
os.environ.setdefault("REPOSITORY_BACKEND", "memory")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("EMBEDDING_PROVIDER", "local")

from typing import AsyncGenerator
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.main import app


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Fixture providing cached settings instance."""
    return get_settings()


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Async test client bound to the FastAPI application."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
