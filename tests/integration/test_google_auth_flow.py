"""Integration tests for Google Sign-In and Firebase Authentication flow."""

import pytest
from httpx import AsyncClient

from app.authentication.service import AuthenticationService
from app.db.connection import DatabaseManager
from app.db.sql_repositories import SQLUserRepository
from app.repositories.user_repo import get_user_repository


def build_test_firebase_jwt(email: str, full_name: str, sub: str = "google_test_12345") -> str:
    """Helper creating a test Firebase ID token accepted by the test environment."""
    import base64
    import json

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
            "mock_firebase": True,
            "role": "candidate",
        })
    )
    return f"{header}.{payload}."


@pytest.mark.asyncio
async def test_get_firebase_config_endpoint(async_client: AsyncClient):
    """1. GET /api/v1/auth/firebase-config returns complete public Web SDK config."""
    resp = await async_client.get("/api/v1/auth/firebase-config")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["apiKey"].startswith("AIza")
    assert data["authDomain"] == "aihiring-3d070.firebaseapp.com"
    assert data["projectId"] == "aihiring-3d070"
    assert "web:" in data["appId"]


@pytest.mark.asyncio
async def test_firebase_login_provisions_new_candidate(async_client: AsyncClient):
    """2. Real/mock Firebase ID token provisions candidate and returns application JWT."""
    test_email = "google_candidate_new@gmail.com"
    test_name = "Jordan Google"
    id_token = build_test_firebase_jwt(email=test_email, full_name=test_name, sub="google_uid_98765")

    resp = await async_client.post(
        "/api/v1/auth/firebase-login",
        json={"id_token": id_token, "role": "candidate", "full_name": test_name},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == test_email
    assert data["user"]["full_name"] == test_name

    # Verify protected endpoint can be called with the returned application JWT
    bearer = f"Bearer {data['access_token']}"
    me_resp = await async_client.get("/api/v1/resumes", headers={"Authorization": bearer})
    assert me_resp.status_code == 200


@pytest.mark.asyncio
async def test_firebase_login_reuses_existing_account(async_client: AsyncClient):
    """3. Subsequent Firebase logins for the same email reuse the existing database record."""
    test_email = "returning_google_user@gmail.com"
    id_token = build_test_firebase_jwt(email=test_email, full_name="Returning User", sub="google_uid_555")

    # First login
    r1 = await async_client.post(
        "/api/v1/auth/firebase-login",
        json={"id_token": id_token, "role": "candidate"},
    )
    assert r1.status_code == 200
    u1_id = r1.json()["data"]["user"]["id"]

    # Second login
    r2 = await async_client.post(
        "/api/v1/auth/firebase-login",
        json={"id_token": id_token, "role": "candidate"},
    )
    assert r2.status_code == 200
    u2_id = r2.json()["data"]["user"]["id"]

    assert u1_id == u2_id, "User ID must be preserved across logins"


@pytest.mark.asyncio
async def test_firebase_login_rejects_malformed_token(async_client: AsyncClient):
    """4. Malformed or invalid ID token is rejected with 401 Unauthorized."""
    resp = await async_client.post(
        "/api/v1/auth/firebase-login",
        json={"id_token": "not.a.valid.jwt.token", "role": "candidate"},
    )
    assert resp.status_code in (401, 400)

