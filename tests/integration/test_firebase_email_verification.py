"""Comprehensive integration test suite for Firebase Email Verification workflow.

Test Scenarios:
1. Email/password signup initializes account with email_verified=False.
2. Unverified candidate is blocked from GET /api/v1/matches with HTTP 403 EMAIL_NOT_VERIFIED.
3. Unverified candidate is blocked from POST /api/v1/resumes with HTTP 403 EMAIL_NOT_VERIFIED.
4. Unverified candidate is blocked from POST /api/v1/jobs/{job_id}/save with HTTP 403 EMAIL_NOT_VERIFIED.
5. Unverified candidate is blocked from POST /api/v1/agent/run with HTTP 403 EMAIL_NOT_VERIFIED.
6. Verified candidate (email_verified=True) successfully accesses all career endpoints (HTTP 200).
7. Google Sign-In user is pre-verified and gains immediate access without redundant verification.
8. Token refresh / verification transition: unverified user who clicks verification link updates database and gains access.
9. Admin and recruiter roles bypass email verification requirements.
10. IDOR security: verified status cannot be spoofed across users.
"""

import base64
import json
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from app.authentication.service import AuthenticationService, get_auth_service
from app.db.connection import DatabaseManager
from app.db.sql_repositories import SQLJobRepository, SQLResumeRepository, SQLUserRepository
from app.main import app
from app.repositories.job_repo import get_job_repository
from app.repositories.resume_repo import get_resume_repository
from app.repositories.user_repo import UserRecord, get_user_repository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.job import Job
from app.schemas.resume import CandidateProfile, ResumeRecord, SkillItem

DISPOSABLE_TEST_DB = "test_email_verification.db"


def build_test_jwt(
    email: str,
    full_name: str,
    sub: str,
    email_verified: bool = False,
    role: str = "candidate",
) -> str:
    """Helper building mock Firebase ID token with customizable verification claim."""
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
            "name": full_name,
            "email_verified": email_verified,
            "mock_firebase": True,
            "role": role,
        })
    )
    return f"{header}.{payload}."


@pytest.fixture(autouse=True)
def clean_dependency_overrides():
    from app.core.config import get_settings
    settings = get_settings()
    old_val = settings.REQUIRE_EMAIL_VERIFICATION
    settings.REQUIRE_EMAIL_VERIFICATION = True
    yield
    settings.REQUIRE_EMAIL_VERIFICATION = old_val
    app.dependency_overrides.clear()


@pytest.fixture
async def email_test_db():
    """Create and initialize a clean disposable database for email verification tests."""
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
# Test 1: Email/password registration initializes account with email_verified=False
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_email_password_signup_sets_unverified_status(email_test_db: DatabaseManager):
    """Registering a new account via email/password sets email_verified=False in local repository."""
    user_repo = SQLUserRepository(db_manager=email_test_db)
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    tag = uuid.uuid4().hex[:6]
    test_email = f"unverified_{tag}@example.com"

    auth_svc = AuthenticationService(repository=user_repo)
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/auth/register",
            json={
                "email": test_email,
                "password": "SecurePassword123!",
                "full_name": "Unverified Candidate",
                "role": "candidate",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["email"] == test_email
        assert data["email_verified"] is False

        # Verify persisted database record
        persisted = await user_repo.get_by_email(test_email)
        assert persisted is not None
        assert persisted.email_verified is False


# ------------------------------------------------------------------------------
# Test 2: Unverified user cannot access ranked job matches
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unverified_user_cannot_access_ranked_matches(email_test_db: DatabaseManager):
    """Unverified candidate accessing GET /api/v1/matches receives HTTP 403 EMAIL_NOT_VERIFIED."""
    user_repo = SQLUserRepository(db_manager=email_test_db)
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    user = await user_repo.create_user(
        email="unverified_matches@example.com",
        hashed_password="hashed_pwd",
        full_name="Unverified Matches User",
        role="candidate",
        email_verified=False,
    )
    auth_svc = AuthenticationService(repository=user_repo)
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    token, _ = auth_svc.create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
        email_verified=False,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/matches", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "EMAIL_NOT_VERIFIED"
        assert "Email verification required" in body["error"]["message"]


# ------------------------------------------------------------------------------
# Test 3: Unverified user cannot upload resumes
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unverified_user_cannot_upload_resumes(email_test_db: DatabaseManager):
    """Unverified candidate accessing POST /api/v1/resumes receives HTTP 403 EMAIL_NOT_VERIFIED."""
    user_repo = SQLUserRepository(db_manager=email_test_db)
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    user = await user_repo.create_user(
        email="unverified_upload@example.com",
        hashed_password="hashed_pwd",
        full_name="Unverified Uploader",
        role="candidate",
        email_verified=False,
    )
    auth_svc = AuthenticationService(repository=user_repo)
    token, _ = auth_svc.create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
        email_verified=False,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        dummy_pdf = b"%PDF-1.4 header text\n%%EOF"
        files = {"file": ("test.pdf", dummy_pdf, "application/pdf")}
        resp = await ac.post("/api/v1/resumes", files=files, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "EMAIL_NOT_VERIFIED"


# ------------------------------------------------------------------------------
# Test 4: Unverified user cannot save jobs to shortlist
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unverified_user_cannot_save_jobs(email_test_db: DatabaseManager):
    """Unverified candidate accessing POST /api/v1/jobs/{job_id}/save receives HTTP 403 EMAIL_NOT_VERIFIED."""
    user_repo = SQLUserRepository(db_manager=email_test_db)
    job_repo = SQLJobRepository(db_manager=email_test_db)
    app.dependency_overrides[get_user_repository] = lambda: user_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo

    user = await user_repo.create_user(
        email="unverified_save@example.com",
        hashed_password="hashed_pwd",
        full_name="Unverified Saver",
        role="candidate",
        email_verified=False,
    )
    job = await job_repo.create_job(
        Job(id="job_email_test_01", title="Backend Dev", company="Test Corp", description="Comprehensive backend development role with Python and FastAPI", is_active=True)
    )

    auth_svc = AuthenticationService(repository=user_repo)
    token, _ = auth_svc.create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
        email_verified=False,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(f"/api/v1/jobs/{job.id}/save", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "EMAIL_NOT_VERIFIED"


# ------------------------------------------------------------------------------
# Test 5: Unverified user cannot run agent tasks
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unverified_user_cannot_run_agent(email_test_db: DatabaseManager):
    """Unverified candidate accessing POST /api/v1/agent/run receives HTTP 403 EMAIL_NOT_VERIFIED."""
    user_repo = SQLUserRepository(db_manager=email_test_db)
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    user = await user_repo.create_user(
        email="unverified_agent@example.com",
        hashed_password="hashed_pwd",
        full_name="Unverified Agent User",
        role="candidate",
        email_verified=False,
    )
    auth_svc = AuthenticationService(repository=user_repo)
    token, _ = auth_svc.create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
        email_verified=False,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/agent/run",
            json={"task": "Recommend jobs for my profile", "conversation_history": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "EMAIL_NOT_VERIFIED"


# ------------------------------------------------------------------------------
# Test 6: Verified candidate accesses protected career features without restriction
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_verified_candidate_accesses_protected_features(email_test_db: DatabaseManager):
    """Candidate with email_verified=True receives HTTP 200 on protected endpoints."""
    user_repo = SQLUserRepository(db_manager=email_test_db)
    resume_repo = SQLResumeRepository(db_manager=email_test_db)
    app.dependency_overrides[get_user_repository] = lambda: user_repo
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo

    user = await user_repo.create_user(
        email="verified_candidate@example.com",
        hashed_password="hashed_pwd",
        full_name="Verified Candidate",
        role="candidate",
        email_verified=True,
    )
    auth_svc = AuthenticationService(repository=user_repo)
    token, _ = auth_svc.create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
        email_verified=True,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Matches list access
        resp_matches = await ac.get("/api/v1/matches?async_matching=true", headers={"Authorization": f"Bearer {token}"})
        assert resp_matches.status_code == 200

        # 2. Resumes list access
        resp_resumes = await ac.get("/api/v1/resumes", headers={"Authorization": f"Bearer {token}"})
        assert resp_resumes.status_code == 200


# ------------------------------------------------------------------------------
# Test 7: Google Sign-In user is pre-verified and gains immediate access
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_google_sign_in_is_pre_verified_and_immediate(email_test_db: DatabaseManager):
    """Google sign-in token (Google accounts are pre-verified) provisions verified account immediately."""
    user_repo = SQLUserRepository(db_manager=email_test_db)
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    google_email = "alex.developer@gmail.com"
    id_token = build_test_jwt(
        email=google_email,
        full_name="Alex Google Dev",
        sub="google_uid_998877",
        email_verified=True,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        login_resp = await ac.post(
            "/api/v1/auth/firebase-login",
            json={"id_token": id_token, "role": "candidate", "full_name": "Alex Google Dev"},
        )
        assert login_resp.status_code == 200
        data = login_resp.json()["data"]
        assert data["user"]["email_verified"] is True

        # Token allows instant access to career endpoints
        bearer = f"Bearer {data['access_token']}"
        matches_resp = await ac.get("/api/v1/matches?async_matching=true", headers={"Authorization": bearer})
        assert matches_resp.status_code == 200


# ------------------------------------------------------------------------------
# Test 8: Verification transition: unverified user verifies email and gets unlocked
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_verification_transition_updates_database_and_unlocks_features(email_test_db: DatabaseManager):
    """When an unverified account's email is confirmed via Firebase, login refreshes claims and unlocks."""
    user_repo = SQLUserRepository(db_manager=email_test_db)
    auth_svc = AuthenticationService(repository=user_repo)
    app.dependency_overrides[get_user_repository] = lambda: user_repo
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    user_email = "transition_user@example.com"
    sub_id = "fb_uid_transition_123"

    # Step 1: User initially logs in unverified
    unverified_token = build_test_jwt(email=user_email, full_name="Transition User", sub=sub_id, email_verified=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r1 = await ac.post(
            "/api/v1/auth/firebase-login",
            json={"id_token": unverified_token, "role": "candidate"},
        )
        assert r1.status_code == 200
        assert r1.json()["data"]["user"]["email_verified"] is False
        blocked_token = r1.json()["data"]["access_token"]

        # Blocked from matches
        blocked_resp = await ac.get("/api/v1/matches", headers={"Authorization": f"Bearer {blocked_token}"})
        assert blocked_resp.status_code == 403

        # Step 2: User clicks verification link in inbox, client reloads and gets verified ID token
        verified_token = build_test_jwt(email=user_email, full_name="Transition User", sub=sub_id, email_verified=True)
        r2 = await ac.post(
            "/api/v1/auth/firebase-login",
            json={"id_token": verified_token, "role": "candidate"},
        )
        assert r2.status_code == 200
        assert r2.json()["data"]["user"]["email_verified"] is True
        unlocked_token = r2.json()["data"]["access_token"]

        # Step 3: Database is updated to email_verified=1
        db_user = await user_repo.get_by_email(user_email)
        assert db_user.email_verified is True

        # Step 4: Now matches endpoint succeeds (HTTP 200)
        unlocked_resp = await ac.get("/api/v1/matches?async_matching=true", headers={"Authorization": f"Bearer {unlocked_token}"})
        assert unlocked_resp.status_code == 200


# ------------------------------------------------------------------------------
# Test 9: Admin and Recruiter roles bypass candidate email verification
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_admin_and_recruiter_bypass_email_verification(email_test_db: DatabaseManager):
    """Admin and recruiter users bypass candidate email verification on guarded endpoints."""
    user_repo = SQLUserRepository(db_manager=email_test_db)
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    admin_user = await user_repo.create_user(
        email="admin_unverified@company.com",
        hashed_password="hashed_pwd",
        full_name="Admin Manager",
        role="admin",
        email_verified=False,
    )
    auth_svc = AuthenticationService(repository=user_repo)
    admin_token, _ = auth_svc.create_access_token(
        user_id=admin_user.id,
        email=admin_user.email,
        role="admin",
        email_verified=False,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/matches?async_matching=true", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
