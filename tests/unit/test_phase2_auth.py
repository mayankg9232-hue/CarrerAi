"""Phase 2 Authentication and Authorization Test Suite (Developer B & Developer A).

Verifies Phase 2 deliverables in accordance with docs/AI_Job_Matcher_Phases_and_Structure.md:
1. Valid user registration (POST /api/v1/auth/register) -> 201 Created
2. Duplicate email registration rejected -> 409 Conflict in ErrorResponse envelope
3. Valid authentication & JWT token issuance (POST /api/v1/auth/login) -> 200 OK
4. Invalid credentials rejected (wrong password / unknown email) -> 401 Unauthorized
5. Protected route access with valid Bearer token (GET /api/v1/auth/me) -> 200 OK
6. Missing authentication token rejected -> 401 Unauthorized
7. Tampered / malformed token rejected -> 401 Unauthorized
8. Role-based access control enforcement (Candidate denied recruiter-only route) -> 403 Forbidden
9. Per-resource ownership enforcement (User A denied User B's record) -> 403 Forbidden (IDOR defense)
10. Password hashing security (bcrypt rounds, salt verification, no raw password exposure)
"""

import pytest
from httpx import AsyncClient

from app.authentication.service import AuthenticationService
from app.repositories.user_repo import get_user_repository


@pytest.fixture(autouse=True)
async def clear_repository() -> None:
    """Ensure clean user repository before each test."""
    repo = get_user_repository()
    await repo.clear()


@pytest.mark.asyncio
async def test_registration_success(async_client: AsyncClient) -> None:
    """Test 1: User registration succeeds and returns 201 Created with UserResponse."""
    payload = {
        "email": "candidate1@example.com",
        "password": "SecurePassword123!",
        "full_name": "Jordan Lee",
        "role": "candidate",
    }
    response = await async_client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201

    data = response.json()
    assert data["success"] is True
    assert data["data"]["email"] == "candidate1@example.com"
    assert data["data"]["full_name"] == "Jordan Lee"
    assert data["data"]["role"] == "candidate"
    assert "id" in data["data"]
    # Ensure sensitive credentials are never leaked
    assert "password" not in data["data"]
    assert "hashed_password" not in data["data"]


@pytest.mark.asyncio
async def test_duplicate_email_rejected(async_client: AsyncClient) -> None:
    """Test 2: Registering an already existing email returns 409 Conflict."""
    payload = {
        "email": "duplicate@example.com",
        "password": "SecurePassword123!",
        "full_name": "Jordan Lee",
        "role": "candidate",
    }
    res1 = await async_client.post("/api/v1/auth/register", json=payload)
    assert res1.status_code == 201

    res2 = await async_client.post("/api/v1/auth/register", json=payload)
    assert res2.status_code == 409

    data = res2.json()
    assert data["success"] is False
    assert data["error"]["code"] == "CONFLICT"
    assert "already exists" in data["error"]["message"]


@pytest.mark.asyncio
async def test_login_success_and_jwt_issuance(async_client: AsyncClient) -> None:
    """Test 3: Valid credentials return 200 OK with signed JWT token."""
    # Register user first
    reg_payload = {
        "email": "login_test@example.com",
        "password": "Password123!",
        "full_name": "Alex Smith",
        "role": "candidate",
    }
    await async_client.post("/api/v1/auth/register", json=reg_payload)

    # Login
    login_payload = {
        "email": "login_test@example.com",
        "password": "Password123!",
    }
    response = await async_client.post("/api/v1/auth/login", json=login_payload)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert "access_token" in data["data"]
    assert data["data"]["token_type"] == "bearer"
    assert data["data"]["expires_in"] > 0
    assert data["data"]["user"]["email"] == "login_test@example.com"


@pytest.mark.asyncio
async def test_login_invalid_password_rejected(async_client: AsyncClient) -> None:
    """Test 4: Incorrect password returns 401 Unauthorized in standard ErrorResponse."""
    reg_payload = {
        "email": "wrong_pwd@example.com",
        "password": "CorrectPassword123!",
        "full_name": "Alex Smith",
    }
    await async_client.post("/api/v1/auth/register", json=reg_payload)

    login_payload = {
        "email": "wrong_pwd@example.com",
        "password": "WrongPassword456!",
    }
    response = await async_client.post("/api/v1/auth/login", json=login_payload)
    assert response.status_code == 401

    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "UNAUTHORIZED"
    assert "Invalid email or password" in data["error"]["message"]


@pytest.mark.asyncio
async def test_login_unknown_email_rejected(async_client: AsyncClient) -> None:
    """Test 5: Non-existent email returns 401 Unauthorized."""
    login_payload = {
        "email": "nonexistent@example.com",
        "password": "AnyPassword123!",
    }
    response = await async_client.post("/api/v1/auth/login", json=login_payload)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_protected_route_with_valid_token(async_client: AsyncClient) -> None:
    """Test 6: Valid Bearer token successfully grants access to GET /api/v1/auth/me."""
    reg = await async_client.post(
        "/api/v1/auth/register",
        json={"email": "me_test@example.com", "password": "Password123!", "full_name": "Me User"},
    )
    user_id = reg.json()["data"]["id"]

    login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "me_test@example.com", "password": "Password123!"},
    )
    token = login.json()["data"]["access_token"]

    response = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert data["data"]["id"] == user_id
    assert data["data"]["email"] == "me_test@example.com"
    assert data["data"]["is_active"] is True


@pytest.mark.asyncio
async def test_protected_route_missing_token_rejected(async_client: AsyncClient) -> None:
    """Test 7: Missing token on protected route returns 401 Unauthorized."""
    response = await async_client.get("/api/v1/auth/me")
    assert response.status_code == 401

    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_protected_route_tampered_token_rejected(async_client: AsyncClient) -> None:
    """Test 8: Tampered or invalid JWT signature returns 401 Unauthorized."""
    invalid_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalidpayload.tampered_signature"
    response = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {invalid_token}"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_role_based_access_control(async_client: AsyncClient) -> None:
    """Test 9: Role enforcement ensures candidate cannot access recruiter-only endpoint."""
    # Register candidate
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": "candidate_role@example.com", "password": "Password123!", "full_name": "Cand User", "role": "candidate"},
    )
    login_cand = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "candidate_role@example.com", "password": "Password123!"},
    )
    cand_token = login_cand.json()["data"]["access_token"]

    # Candidate denied recruiter route (403)
    cand_resp = await async_client.get(
        "/api/v1/auth/recruiter-only",
        headers={"Authorization": f"Bearer {cand_token}"},
    )
    assert cand_resp.status_code == 403
    assert cand_resp.json()["error"]["code"] == "FORBIDDEN"

    # Register recruiter
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": "recruiter_role@example.com", "password": "Password123!", "full_name": "Rec User", "role": "recruiter"},
    )
    login_rec = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "recruiter_role@example.com", "password": "Password123!"},
    )
    rec_token = login_rec.json()["data"]["access_token"]

    # Recruiter granted access (200)
    rec_resp = await async_client.get(
        "/api/v1/auth/recruiter-only",
        headers={"Authorization": f"Bearer {rec_token}"},
    )
    assert rec_resp.status_code == 200
    assert rec_resp.json()["data"]["role"] == "recruiter"


@pytest.mark.asyncio
async def test_resource_ownership_idor_defense(async_client: AsyncClient) -> None:
    """Test 10: Per-resource ownership verification blocks User A from accessing User B's record."""
    # Register User A
    u1 = await async_client.post(
        "/api/v1/auth/register",
        json={"email": "usera@example.com", "password": "Password123!", "full_name": "User A"},
    )
    u1_id = u1.json()["data"]["id"]
    l1 = await async_client.post("/api/v1/auth/login", json={"email": "usera@example.com", "password": "Password123!"})
    t1 = l1.json()["data"]["access_token"]

    # Register User B
    u2 = await async_client.post(
        "/api/v1/auth/register",
        json={"email": "userb@example.com", "password": "Password123!", "full_name": "User B"},
    )
    u2_id = u2.json()["data"]["id"]

    # User A accesses own record -> 200 OK
    own_resp = await async_client.get(
        f"/api/v1/auth/users/{u1_id}/data",
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert own_resp.status_code == 200
    assert own_resp.json()["data"]["user_id"] == u1_id

    # User A accesses User B's record -> 403 Forbidden (IDOR blocked)
    other_resp = await async_client.get(
        f"/api/v1/auth/users/{u2_id}/data",
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert other_resp.status_code == 403
    assert other_resp.json()["error"]["code"] == "FORBIDDEN"


def test_password_hashing_security() -> None:
    """Test 11: Direct verification of bcrypt salt and password hashing security."""
    auth_service = AuthenticationService(bcrypt_rounds=4)  # 4 rounds for fast test
    plain = "SuperSecretPassword99!"
    hashed = auth_service.hash_password(plain)

    # Hash should start with standard bcrypt prefix
    assert hashed.startswith("$2b$") or hashed.startswith("$2a$")
    assert hashed != plain

    # Matching verification
    assert auth_service.verify_password(plain, hashed) is True
    assert auth_service.verify_password("WrongPassword", hashed) is False


@pytest.mark.asyncio
async def test_expired_token_rejected(async_client: AsyncClient) -> None:
    """Test 12: An expired JWT token returns 401 Unauthorized with descriptive error."""
    from datetime import timedelta
    auth_service = AuthenticationService()
    # Create token that expired 10 minutes ago
    expired_token, _ = auth_service.create_access_token(
        user_id="usr_expired",
        email="expired@example.com",
        role="candidate",
        expires_delta=timedelta(minutes=-10),
    )

    response = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "UNAUTHORIZED"
    assert "expired" in data["error"]["message"].lower()


@pytest.mark.asyncio
async def test_oversized_and_malformed_auth_input_rejected(async_client: AsyncClient) -> None:
    """Test 13: Oversized passwords (>128 chars) and malformed payloads are safely rejected (422)."""
    # Password too long (>128 characters)
    oversized_payload = {
        "email": "valid@example.com",
        "password": "A" * 150,
        "full_name": "Test User",
        "role": "candidate",
    }
    response = await async_client.post("/api/v1/auth/register", json=oversized_payload)
    assert response.status_code == 422
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "VALIDATION_ERROR"

    # Missing required password field
    missing_fields = {"email": "valid@example.com"}
    res2 = await async_client.post("/api/v1/auth/login", json=missing_fields)
    assert res2.status_code == 422
    assert res2.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_firebase_config_endpoint(async_client: AsyncClient) -> None:
    """Test 14: GET /api/v1/auth/firebase-config returns public Firebase configuration."""
    response = await async_client.get("/api/v1/auth/firebase-config")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["projectId"] == "aihiring-3d070"
    assert "apiKey" in data["data"]
    assert "authDomain" in data["data"]


@pytest.mark.asyncio
async def test_firebase_login_and_user_provisioning(async_client: AsyncClient) -> None:
    """Test 15: Valid Firebase ID token provisions candidate and issues session JWT."""
    import jwt
    # Generate mock Firebase ID token
    mock_payload = {
        "sub": "firebase_uid_12345",
        "email": "firebase_candidate@example.com",
        "name": "Firebase User",
        "iss": "https://securetoken.google.com/aihiring-3d070",
        "aud": "aihiring-3d070",
        "mock_firebase": True,
    }
    mock_token = jwt.encode(mock_payload, "dummy_secret_key_at_least_32_bytes_long", algorithm="HS256")

    response = await async_client.post(
        "/api/v1/auth/firebase-login",
        json={"id_token": mock_token, "role": "candidate"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "access_token" in data["data"]
    assert data["data"]["user"]["email"] == "firebase_candidate@example.com"

    # Use the acquired access token to access protected /api/v1/auth/me
    token = data["data"]["access_token"]
    me_resp = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["data"]["email"] == "firebase_candidate@example.com"


@pytest.mark.asyncio
async def test_github_token_authorisation(async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 16: Configured GitHub PAT grants admin authorization; unauthorized PAT returns 401."""
    test_pat = "ghp_test_valid_github_token_12345"
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "GITHUB_TOKEN", test_pat)

    # 1. Valid GitHub PAT
    resp = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {test_pat}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["role"] == "admin"
    assert data["data"]["id"] == "usr_github_admin"

    # 2. Unauthorized / invalid PAT
    bad_resp = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer ghp_unauthorized_token_999"},
    )
    assert bad_resp.status_code == 401
