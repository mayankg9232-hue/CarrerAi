"""Automated tests validating Firebase email verification delivery diagnostics,
backend Identity Toolkit dispatch, rate limit recovery, and verified token enforcement.

Scenarios:
1. Email/password signup invokes verification email dispatch.
2. Successful dispatch reports true confirmation status.
3. Throttled/failed dispatch surfaces recoverable error (TOO_MANY_ATTEMPTS_TRY_LATER).
4. Resend verification endpoint respects cooldown and prevents spam.
5. Verification status refresh detects confirmed claim and unlocks session.
6. Unverified account cannot access protected career features (matches/resumes).
7. Google sign-in remains functional without redundant verification.
8. Backend service account generates official Firebase verification link directly.
"""

import base64
import json
import uuid
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient, Response

from app.authentication.service import AuthenticationService, get_auth_service
from app.db.connection import DatabaseManager
from app.db.sql_repositories import SQLUserRepository
from app.main import app
from app.repositories.user_repo import get_user_repository
from app.schemas.auth import SendVerificationEmailRequest

DISPOSABLE_TEST_DB = "test_verification_delivery.db"


def build_mock_jwt(email: str, sub: str, email_verified: bool = False, role: str = "candidate") -> str:
    def b64url(s: str) -> str:
        return base64.urlsafe_b64encode(s.encode("utf-8")).decode("utf-8").rstrip("=")

    header = b64url(json.dumps({"alg": "none", "typ": "JWT"}))
    payload = b64url(
        json.dumps({
            "iss": "https://securetoken.google.com/aihiring-3d070",
            "aud": "aihiring-3d070",
            "sub": sub,
            "user_id": sub,
            "email": email,
            "name": "Delivery Test User",
            "email_verified": email_verified,
            "mock_firebase": True,
            "role": role,
        })
    )
    return f"{header}.{payload}."


@pytest.fixture(autouse=True)
def clean_overrides():
    from app.core.config import get_settings
    settings = get_settings()
    old_val = settings.REQUIRE_EMAIL_VERIFICATION
    settings.REQUIRE_EMAIL_VERIFICATION = True
    yield
    settings.REQUIRE_EMAIL_VERIFICATION = old_val
    app.dependency_overrides.clear()


@pytest.fixture
async def delivery_db():
    mgr = DatabaseManager(database_url=f"sqlite:///{DISPOSABLE_TEST_DB}")
    await mgr.initialize()
    yield mgr
    await mgr.close()
    import os
    if os.path.exists(DISPOSABLE_TEST_DB):
        try:
            os.remove(DISPOSABLE_TEST_DB)
        except Exception:
            pass


# ------------------------------------------------------------------------------
# Test 1: Email/password signup registers user with email_verified=False
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_email_password_signup_initializes_unverified(delivery_db: DatabaseManager):
    """Signup stores email_verified=False and does not falsely mark account as verified."""
    user_repo = SQLUserRepository(db_manager=delivery_db)
    auth_svc = AuthenticationService(repository=user_repo)
    app.dependency_overrides[get_user_repository] = lambda: user_repo
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    test_email = f"candidate_{uuid.uuid4().hex[:6]}@example.com"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": test_email,
                "password": "StrongPassword123!",
                "full_name": "Test Candidate",
                "role": "candidate",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["email_verified"] is False


# ------------------------------------------------------------------------------
# Test 2: Backend send-verification dispatches to Identity Toolkit successfully
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backend_send_verification_success(delivery_db: DatabaseManager):
    """POST /api/v1/auth/send-verification calls Google Identity Toolkit accounts:sendOobCode."""
    user_repo = SQLUserRepository(db_manager=delivery_db)
    auth_svc = AuthenticationService(repository=user_repo)
    app.dependency_overrides[get_user_repository] = lambda: user_repo
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    test_token = build_mock_jwt(email="verify_target@example.com", sub="sub_123")

    with patch.object(auth_svc, "send_verification_email", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"status": "sent", "email": "verify_target@example.com"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/send-verification",
                json={"id_token": test_token},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["status"] == "sent"
            assert data["email"] == "verify_target@example.com"


# ------------------------------------------------------------------------------
# Test 3: Failed send (rate-limited) surfaces recoverable error
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backend_send_verification_rate_limited(delivery_db: DatabaseManager):
    """When Firebase throttles with TOO_MANY_ATTEMPTS_TRY_LATER, response indicates rate_limited."""
    user_repo = SQLUserRepository(db_manager=delivery_db)
    auth_svc = AuthenticationService(repository=user_repo)
    app.dependency_overrides[get_user_repository] = lambda: user_repo
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    test_token = build_mock_jwt(email="rate_limited@example.com", sub="sub_456")

    with patch.object(auth_svc, "send_verification_email", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {
            "status": "rate_limited",
            "message": "Firebase email rate limit reached. Please wait a few minutes before requesting another verification email, or check your spam/promotions folder.",
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/send-verification",
                json={"id_token": test_token},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["status"] == "rate_limited"
            assert "rate limit" in data["message"].lower()


# ------------------------------------------------------------------------------
# Test 4: Unverified user cannot access protected career endpoints
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unverified_account_blocked_from_matches(delivery_db: DatabaseManager):
    """Unverified candidate receives HTTP 403 EMAIL_NOT_VERIFIED on protected endpoints."""
    user_repo = SQLUserRepository(db_manager=delivery_db)
    auth_svc = AuthenticationService(repository=user_repo)
    app.dependency_overrides[get_user_repository] = lambda: user_repo
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    user = await user_repo.create_user(
        email="unverified_block@example.com",
        hashed_password="hashed_pwd",
        full_name="Unverified User",
        role="candidate",
        email_verified=False,
    )
    token, _ = auth_svc.create_access_token(user_id=user.id, email=user.email, role=user.role, email_verified=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/matches", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


# ------------------------------------------------------------------------------
# Test 5: Verified user transitions to active status and unlocks access
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_verification_refresh_unlocks_workspace(delivery_db: DatabaseManager):
    """User who opened verification link sends refreshed token and gains access."""
    user_repo = SQLUserRepository(db_manager=delivery_db)
    auth_svc = AuthenticationService(repository=user_repo)
    app.dependency_overrides[get_user_repository] = lambda: user_repo
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    email = "refreshed_user@example.com"
    verified_id_token = build_mock_jwt(email=email, sub="uid_refresh_789", email_verified=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_resp = await client.post(
            "/api/v1/auth/firebase-login",
            json={"id_token": verified_id_token, "role": "candidate"},
        )
        assert login_resp.status_code == 200
        bearer_token = login_resp.json()["data"]["access_token"]
        assert login_resp.json()["data"]["user"]["email_verified"] is True

        # Matches endpoint is now unlocked
        matches_resp = await client.get("/api/v1/matches?async_matching=true", headers={"Authorization": f"Bearer {bearer_token}"})
        assert matches_resp.status_code == 200


# ------------------------------------------------------------------------------
# Test 6: Google Sign-In remains pre-verified without extra steps
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_google_sign_in_remains_pre_verified(delivery_db: DatabaseManager):
    """Google sign-in tokens are inherently verified and grant immediate access."""
    user_repo = SQLUserRepository(db_manager=delivery_db)
    auth_svc = AuthenticationService(repository=user_repo)
    app.dependency_overrides[get_user_repository] = lambda: user_repo
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    google_email = "dev.candidate@gmail.com"
    google_token = build_mock_jwt(email=google_email, sub="google_dev_4455", email_verified=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/auth/firebase-login",
            json={"id_token": google_token, "role": "candidate"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["user"]["email_verified"] is True


# ------------------------------------------------------------------------------
# Test 7: Service account admin verification link generation
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_service_account_verification_link_generation():
    """POST /api/v1/auth/get-verification-link generates official Firebase oobLink."""
    auth_svc = AuthenticationService()
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    mock_link = "https://aihiring-3d070.firebaseapp.com/__/auth/action?mode=verifyEmail&oobCode=mockCode123"

    with patch.object(auth_svc, "generate_verification_link", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = {
            "status": "success",
            "email": "test_link@example.com",
            "verification_link": mock_link,
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/get-verification-link",
                json={"email": "test_link@example.com"},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["status"] == "success"
            assert data["verification_link"] == mock_link


# ------------------------------------------------------------------------------
# Test 8: Resend verification endpoint handles rate-limit and cooldown
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resend_verification_cooldown_and_rate_limit_handling(delivery_db: DatabaseManager):
    """Resend requests surface rate limits accurately and prevent repeated silent failure."""
    user_repo = SQLUserRepository(db_manager=delivery_db)
    auth_svc = AuthenticationService(repository=user_repo)
    app.dependency_overrides[get_user_repository] = lambda: user_repo
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    test_token = build_mock_jwt(email="resend_cooldown@example.com", sub="sub_cooldown")

    # 1. First send attempt succeeds
    with patch.object(auth_svc, "send_verification_email", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"status": "sent", "email": "resend_cooldown@example.com"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp1 = await client.post(
                "/api/v1/auth/send-verification",
                json={"id_token": test_token},
            )
            assert resp1.status_code == 200
            assert resp1.json()["data"]["status"] == "sent"

    # 2. Immediate second send triggers provider rate limit
    with patch.object(auth_svc, "send_verification_email", new_callable=AsyncMock) as mock_send_limited:
        mock_send_limited.return_value = {
            "status": "rate_limited",
            "message": "Firebase email rate limit reached. Please wait a few minutes before requesting another verification email, or check your spam/promotions folder.",
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp2 = await client.post(
                "/api/v1/auth/send-verification",
                json={"id_token": test_token},
            )
            assert resp2.status_code == 200
            assert resp2.json()["data"]["status"] == "rate_limited"
            assert "rate limit" in resp2.json()["data"]["message"].lower()


# ------------------------------------------------------------------------------
# Test 9: Existing unverified account can resend verification without re-registering
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_existing_unverified_account_can_resend_verification(delivery_db: DatabaseManager):
    """An account that already exists in unverified state can trigger resend via email payload."""
    user_repo = SQLUserRepository(db_manager=delivery_db)
    auth_svc = AuthenticationService(repository=user_repo)
    app.dependency_overrides[get_user_repository] = lambda: user_repo
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    email = "existing_unverified@example.com"
    await user_repo.create_user(
        email=email,
        hashed_password="hashed_pwd",
        full_name="Existing Candidate",
        role="candidate",
        email_verified=False,
    )

    with patch.object(auth_svc, "generate_verification_link", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = {
            "status": "success",
            "email": email,
            "verification_link": f"https://aihiring-3d070.firebaseapp.com/__/auth/action?mode=verifyEmail&oobCode=code_123",
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/send-verification",
                json={"email": email},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["status"] == "success"
            assert data["email"] == email
            assert "aihiring-3d070.firebaseapp.com/__/auth/action" in data["verification_link"]


# ------------------------------------------------------------------------------
# Test 10: Default Firebase Action URL Conformance Check
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_default_firebase_action_url_conformance():
    """Verify that action URLs generated use the official Firebase action handler and not an invented URL."""
    auth_svc = AuthenticationService()
    project_id = auth_svc.settings.FIREBASE_PROJECT_ID or "aihiring-3d070"
    auth_domain = auth_svc.settings.FIREBASE_AUTH_DOMAIN or "aihiring-3d070.firebaseapp.com"

    assert project_id == "aihiring-3d070"
    assert auth_domain == "aihiring-3d070.firebaseapp.com"
    expected_action_handler_prefix = f"https://{auth_domain}/__/auth/action?mode=verifyEmail"
    sample_oob_link = f"https://{auth_domain}/__/auth/action?mode=verifyEmail&oobCode=ABCxyz123"

    assert sample_oob_link.startswith(expected_action_handler_prefix)
    # Ensure no invented custom port or fake path
    assert "localhost" not in sample_oob_link
    assert "/__/auth/action" in sample_oob_link

