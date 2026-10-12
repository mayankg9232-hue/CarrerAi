"""Phase 1 Backend Architecture Test Suite.

Verifies Developer A Phase 1 deliverables:
1. Health endpoint response and status (GET /api/v1/health)
2. Schema serialization and StandardResponse envelope
3. Request body validation rejection (422) with ErrorResponse envelope
4. Non-existent route handling (404) with ErrorResponse envelope
5. Unhandled exception isolation (500) without stack trace leakage
6. Configuration loader and environment variable parsing
7. Dev UI static file serving
8. Security headers and request ID tracing
"""

import pytest
from httpx import AsyncClient

from app.core.config import Settings, get_settings


@pytest.mark.asyncio
async def test_health_success(async_client: AsyncClient) -> None:
    """Test 1: Health endpoint returns 200 OK and valid health payload."""
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["status"] == "healthy"
    assert data["data"]["version"] == "0.1.0"
    assert data["data"]["environment"] == "development"
    assert data["data"]["services"]["api"] == "up"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_schema_serialization(async_client: AsyncClient) -> None:
    """Test 2: Valid payload correctly wrapped in StandardResponse envelope."""
    payload = {"message": "Valid test message", "tag": "unit_test_tag"}
    response = await async_client.post("/api/v1/test/echo", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["message"] == "Valid test message"
    assert data["data"]["tag"] == "unit_test_tag"
    assert data["message"] == "Payload successfully validated"


@pytest.mark.asyncio
async def test_invalid_body_rejected(async_client: AsyncClient) -> None:
    """Test 3: Malformed request rejected with 422 in standard ErrorResponse envelope."""
    # message is too short (min 3 chars required), tag has spaces (regex pattern rejected)
    invalid_payload = {"message": "x", "tag": "spaces are invalid"}
    response = await async_client.post("/api/v1/test/echo", json=invalid_payload)
    assert response.status_code == 422

    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert "Validation failed" in data["error"]["message"] or "validation constraints" in data["error"]["message"]
    assert len(data["error"]["details"]) > 0


@pytest.mark.asyncio
async def test_unknown_route_handled(async_client: AsyncClient) -> None:
    """Test 4: Non-existent route returns 404 in standard ErrorResponse envelope."""
    response = await async_client.get("/api/v1/non-existent-endpoint-12345")
    assert response.status_code == 404

    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "NOT_FOUND"
    assert "request_id" in data


@pytest.mark.asyncio
async def test_unhandled_exception_safe(async_client: AsyncClient) -> None:
    """Test 5: Unhandled exceptions return safe 500 without leaking stack traces."""
    response = await async_client.get("/api/v1/test/unhandled-error")
    assert response.status_code == 500

    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "INTERNAL_SERVER_ERROR"
    # Ensure internal exception details and tracebacks are never leaked
    assert "Deliberate unhandled test error" not in data["error"]["message"]
    assert "Traceback" not in response.text
    assert "File " not in response.text


def test_config_validation() -> None:
    """Test 6: Pydantic Settings safely loads defaults and parses types."""
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert settings.APP_NAME == "AI Job Matcher"
    assert settings.API_V1_PREFIX == "/api/v1"
    assert isinstance(settings.ALLOWED_ORIGINS, list)
    assert settings.AGENT_MAX_STEPS == 8


@pytest.mark.asyncio
async def test_dev_ui_served(async_client: AsyncClient) -> None:
    """Test 7: Local Dev UI index is served at root and /ui paths."""
    for path in ["/", "/ui"]:
        response = await async_client.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "AI Job Matcher" in response.text
        assert "Developer Testing Dashboard" in response.text


@pytest.mark.asyncio
async def test_security_headers_and_tracing(async_client: AsyncClient) -> None:
    """Test 8: Responses contain security headers and unique request tracing ID."""
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
