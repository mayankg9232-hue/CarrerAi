"""Phase 2 Security & Authorization Test Suite (Developer A Lead).

Verifies Developer A authorization and security responsibilities:
1. Role-Based Access Control (RBAC): Candidate blocked from recruiter and admin routes (403 Forbidden).
2. Admin Privilege Elevation: Admin role authorized to access recruiter and admin resources.
3. Inactive/Suspended Account Defense: Token from inactive user rejected with 403 Forbidden.
4. Direct Object Reference (IDOR) Defense: Cross-user resource access blocked (403 Forbidden); admin override allowed.
5. Brute-Force & Rate Limiting: Rapid successive requests exceed sliding window limit and return 429 Too Many Requests.
6. Denial of Service (Payload Capping): Oversized request bodies rejected with 413 Payload Too Large.
7. Security Header Enforcement: Security headers present on all responses.
"""

import pytest
from httpx import AsyncClient

from app.authentication.service import get_auth_service
from app.core.rate_limiter import InMemoryRateLimiter
from app.core.security import DEFAULT_MAX_PAYLOAD_SIZE
from app.repositories.user_repo import get_user_repository
from app.schemas.auth import UserRegisterRequest


@pytest.fixture(autouse=True)
async def reset_state() -> None:
    """Clear repositories before each test."""
    repo = get_user_repository()
    await repo.clear()


@pytest.mark.asyncio
async def test_rbac_candidate_denied_recruiter_and_admin(async_client: AsyncClient) -> None:
    """Test 1: Candidate role is blocked from accessing recruiter-only and admin-only endpoints."""
    auth_svc = get_auth_service()

    # Create candidate
    candidate = await auth_svc.register_user(
        UserRegisterRequest(
            email="candidate_rbac@example.com",
            password="StrongPassword123!",
            full_name="Candidate RBAC",
            role="candidate",
        )
    )
    token, _ = auth_svc.create_access_token(user_id=candidate.id, email=candidate.email, role=candidate.role)
    headers = {"Authorization": f"Bearer {token}"}

    # Attempt recruiter-only route
    res_recruiter = await async_client.get("/api/v1/auth/recruiter-only", headers=headers)
    assert res_recruiter.status_code == 403
    assert res_recruiter.json()["error"]["code"] == "FORBIDDEN"
    assert "Insufficient role" in res_recruiter.json()["error"]["message"]

    # Attempt admin-only route
    res_admin = await async_client.get("/api/v1/auth/admin-only", headers=headers)
    assert res_admin.status_code == 403
    assert res_admin.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_rbac_admin_has_elevated_access(async_client: AsyncClient) -> None:
    """Test 2: Admin role is authorized for both recruiter and admin endpoints."""
    auth_svc = get_auth_service()
    repo = get_user_repository()

    # Register as recruiter, then promote to admin
    user = await auth_svc.register_user(
        UserRegisterRequest(
            email="admin_user@example.com",
            password="AdminPassword123!",
            full_name="System Admin",
            role="recruiter",
        )
    )
    await repo.update_user(user.id, role="admin")

    token, _ = auth_svc.create_access_token(user_id=user.id, email=user.email, role="admin")
    headers = {"Authorization": f"Bearer {token}"}

    res_recruiter = await async_client.get("/api/v1/auth/recruiter-only", headers=headers)
    assert res_recruiter.status_code == 200
    assert res_recruiter.json()["data"]["access"] == "granted"

    res_admin = await async_client.get("/api/v1/auth/admin-only", headers=headers)
    assert res_admin.status_code == 200
    assert res_admin.json()["data"]["access"] == "granted"


@pytest.mark.asyncio
async def test_deactivated_account_denied_access(async_client: AsyncClient) -> None:
    """Test 3: Inactive/disabled account is rejected with 403 Forbidden even with valid token."""
    auth_svc = get_auth_service()
    repo = get_user_repository()

    user = await auth_svc.register_user(
        UserRegisterRequest(
            email="deactivated@example.com",
            password="StrongPassword123!",
            full_name="Disabled User",
            role="candidate",
        )
    )
    # Generate token before deactivation
    token, _ = auth_svc.create_access_token(user_id=user.id, email=user.email, role=user.role)

    # Deactivate account in repository
    await repo.update_user(user.id, is_active=False)

    headers = {"Authorization": f"Bearer {token}"}
    response = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert "inactive" in response.json()["error"]["message"] or "disabled" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_idor_cross_tenant_resource_isolation(async_client: AsyncClient) -> None:
    """Test 4: User A cannot access User B's resource; Admin is allowed access."""
    auth_svc = get_auth_service()
    repo = get_user_repository()

    user_a = await auth_svc.register_user(
        UserRegisterRequest(email="user_a@example.com", password="Password123!", full_name="User A", role="candidate")
    )
    user_b = await auth_svc.register_user(
        UserRegisterRequest(email="user_b@example.com", password="Password123!", full_name="User B", role="candidate")
    )
    admin_user = await auth_svc.register_user(
        UserRegisterRequest(email="admin_idor@example.com", password="Password123!", full_name="Admin", role="recruiter")
    )
    await repo.update_user(admin_user.id, role="admin")

    token_a, _ = auth_svc.create_access_token(user_id=user_a.id, email=user_a.email, role=user_a.role)
    token_admin, _ = auth_svc.create_access_token(user_id=admin_user.id, email=admin_user.email, role="admin")

    # User A accesses User A's data -> 200 OK
    res_self = await async_client.get(f"/api/v1/auth/users/{user_a.id}/data", headers={"Authorization": f"Bearer {token_a}"})
    assert res_self.status_code == 200

    # User A attempts to access User B's data -> 403 Forbidden (IDOR defense)
    res_idor = await async_client.get(f"/api/v1/auth/users/{user_b.id}/data", headers={"Authorization": f"Bearer {token_a}"})
    assert res_idor.status_code == 403
    assert res_idor.json()["error"]["code"] == "FORBIDDEN"

    # Admin accesses User B's data -> 200 OK (authorized administrative audit)
    res_admin = await async_client.get(f"/api/v1/auth/users/{user_b.id}/data", headers={"Authorization": f"Bearer {token_admin}"})
    assert res_admin.status_code == 200


@pytest.mark.asyncio
async def test_sliding_window_rate_limiter_throttles(async_client: AsyncClient) -> None:
    """Test 5: Successive rapid calls trigger sliding-window 429 Rate Limit Exceeded."""
    endpoint = "/api/v1/auth/rate-limit-test"

    # First 3 requests are permitted (threshold = 3)
    for _ in range(3):
        res = await async_client.get(endpoint)
        assert res.status_code == 200

    # 4th request must be rejected with 429
    res_throttled = await async_client.get(endpoint)
    assert res_throttled.status_code == 429
    assert res_throttled.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert "Rate limit exceeded" in res_throttled.json()["error"]["message"]


@pytest.mark.asyncio
async def test_payload_size_limiter_blocks_oversized_requests(async_client: AsyncClient) -> None:
    """Test 6: Requests exceeding maximum payload body size are rejected with 413 Payload Too Large."""
    # Send Content-Length header exceeding default 5 MB limit
    oversized_headers = {
        "Content-Length": str(DEFAULT_MAX_PAYLOAD_SIZE + 1024),
        "Content-Type": "application/json",
    }
    response = await async_client.post("/api/v1/auth/login", headers=oversized_headers, content=b"{}")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert "exceeds maximum allowed limit" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_security_headers_on_all_auth_responses(async_client: AsyncClient) -> None:
    """Test 7: Security headers and request ID tracing are present on all auth endpoints."""
    response = await async_client.get("/api/v1/auth/firebase-config")
    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-XSS-Protection") == "1; mode=block"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
