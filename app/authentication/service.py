"""Authentication service handling password hashing, JWT lifecycle, and user verification."""

from datetime import datetime, timedelta, timezone
from typing import Optional
import json
import os
import uuid
import bcrypt
import httpx
import jwt
from cryptography.x509 import load_pem_x509_certificate

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError, ConflictError
from app.core.logging import logger
from app.repositories.user_repo import IUserRepository, get_user_repository
from app.schemas.auth import (
    AuthenticatedPrincipal,
    FirebaseLoginRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)


class AuthenticationService:
    """Service encapsulating identity verification and JWT operations (Developer B)."""

    def __init__(
        self,
        repository: Optional[IUserRepository] = None,
        settings: Optional[Settings] = None,
        bcrypt_rounds: int = 12,
    ) -> None:
        self._repository = repository
        self.settings = settings or get_settings()
        self.bcrypt_rounds = bcrypt_rounds
        self._google_certs_cache: dict[str, str] = {}
        self._google_certs_last_fetch: float = 0.0

    @property
    def repository(self) -> IUserRepository:
        if self._repository is not None:
            return self._repository
        return get_user_repository()

    @repository.setter
    def repository(self, repo: Optional[IUserRepository]) -> None:
        self._repository = repo

    def get_service_account_credentials(self) -> Optional[dict]:
        """Load service account JSON credentials if path is configured and exists."""
        path = self.settings.FIREBASE_SERVICE_ACCOUNT_PATH
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read Firebase service account credentials: {e}")
        return None

    async def get_google_service_account_access_token(self) -> Optional[str]:
        """Mint a Google OAuth2 access token using the service account credentials."""
        sa = self.get_service_account_credentials()
        if not sa or not sa.get("client_email") or not sa.get("private_key"):
            return None
        now = int(datetime.now(timezone.utc).timestamp())
        payload = {
            "iss": sa["client_email"],
            "sub": sa["client_email"],
            "aud": sa.get("token_uri", "https://oauth2.googleapis.com/token"),
            "iat": now,
            "exp": now + 3600,
            "scope": "https://www.googleapis.com/auth/identitytoolkit https://www.googleapis.com/auth/firebase",
        }
        try:
            signed_jwt = jwt.encode(payload, sa["private_key"], algorithm="RS256")
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    sa.get("token_uri", "https://oauth2.googleapis.com/token"),
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": signed_jwt,
                    },
                )
                if resp.status_code == 200:
                    return resp.json().get("access_token")
        except Exception as e:
            logger.warning(f"Failed to mint service account access token: {e}")
        return None

    async def send_verification_email(self, id_token: str) -> dict:
        """Trigger a Firebase verification email via Identity Toolkit API."""
        api_key = self.settings.FIREBASE_API_KEY
        if not api_key:
            return {"status": "error", "message": "Firebase API key not configured"}

        url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={api_key}"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    url,
                    json={"requestType": "VERIFY_EMAIL", "idToken": id_token},
                )
                if resp.status_code == 200:
                    return {"status": "sent", "email": resp.json().get("email")}
                err_data = resp.json().get("error", {})
                msg = err_data.get("message", "FAILED")
                if "TOO_MANY_ATTEMPTS_TRY_LATER" in msg:
                    return {
                        "status": "rate_limited",
                        "message": "Firebase email rate limit reached. Please wait a few minutes before requesting another verification email, or check your spam/promotions folder.",
                    }
                return {"status": "error", "message": msg}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def generate_verification_link(self, email: str) -> dict:
        """Generate official Firebase email verification link via Google Identity Toolkit admin endpoint."""
        token = await self.get_google_service_account_access_token()
        project_id = self.settings.FIREBASE_PROJECT_ID or "aihiring-3d070"
        if not token:
            return {"status": "error", "message": "Service account credentials unavailable"}

        admin_url = f"https://identitytoolkit.googleapis.com/v1/projects/{project_id}/accounts:sendOobCode"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    admin_url,
                    headers={"Authorization": f"Bearer {token}"},
                    json={"requestType": "VERIFY_EMAIL", "email": email, "returnOobLink": True},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "status": "success",
                        "email": email,
                        "verification_link": data.get("oobLink"),
                    }
                err_data = resp.json().get("error", {})
                return {"status": "error", "message": err_data.get("message", "Link generation failed")}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def hash_password(self, plain_password: str) -> str:
        """Hash a plain text password using bcrypt."""
        salt = bcrypt.gensalt(rounds=self.bcrypt_rounds)
        hashed_bytes = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
        return hashed_bytes.decode("utf-8")

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify plain password against stored bcrypt hash."""
        try:
            return bcrypt.checkpw(
                plain_password.encode("utf-8"),
                hashed_password.encode("utf-8"),
            )
        except Exception:
            return False

    def create_access_token(
        self,
        user_id: str,
        email: str,
        role: str,
        full_name: Optional[str] = None,
        email_verified: bool = False,
        expires_delta: Optional[timedelta] = None,
    ) -> tuple[str, int]:
        """Generate a signed JWT access token and return (token, expires_in_seconds)."""
        expire_minutes = self.settings.ACCESS_TOKEN_EXPIRE_MINUTES
        if expires_delta:
            expire_at = datetime.now(timezone.utc) + expires_delta
            expires_in = int(expires_delta.total_seconds())
        else:
            expire_at = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
            expires_in = expire_minutes * 60

        now = datetime.now(timezone.utc)
        claims = {
            "sub": user_id,
            "email": email,
            "role": role,
            "email_verified": email_verified,
            "exp": expire_at,
            "iat": now,
            "jti": str(uuid.uuid4()),
        }
        if full_name:
            claims["name"] = full_name

        token = jwt.encode(
            claims,
            self.settings.SECRET_KEY,
            algorithm=self.settings.ALGORITHM,
        )
        return token, expires_in

    async def get_google_public_keys(self) -> dict[str, str]:
        """Fetch and cache Google's public x509 certs for Firebase ID token verification."""
        now = datetime.now(timezone.utc).timestamp()
        if self._google_certs_cache and (now - self._google_certs_last_fetch < 3600):
            return self._google_certs_cache

        url = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    self._google_certs_cache = resp.json()
                    self._google_certs_last_fetch = now
                    return self._google_certs_cache
        except Exception as e:
            logger.warning(f"Failed to fetch Google public certs: {e}")

        return self._google_certs_cache or {}

    async def verify_firebase_token(self, id_token: str) -> AuthenticatedPrincipal:
        """Validate Firebase ID token (RS256) issued by Google Firebase Auth."""
        project_id = self.settings.FIREBASE_PROJECT_ID or "aihiring-3d070"
        expected_issuer = f"https://securetoken.google.com/{project_id}"

        try:
            unverified_header = jwt.get_unverified_header(id_token)
            unverified_claims = jwt.decode(id_token, options={"verify_signature": False})
        except Exception as e:
            raise AuthenticationError("Invalid token format") from e

        # Mock / Test verification support for offline test suites
        if unverified_claims.get("mock_firebase") is True:
            if not getattr(self.settings, "ALLOW_MOCK_FIREBASE", False) or getattr(self.settings, "APP_ENV", "") == "production":
                raise AuthenticationError("Mock Firebase tokens are disabled in this environment")
            sub = unverified_claims.get("sub") or unverified_claims.get("user_id")
            email = unverified_claims.get("email")
            role = unverified_claims.get("role", "candidate")
            name = unverified_claims.get("name") or unverified_claims.get("full_name")
            if not sub or not email:
                raise AuthenticationError("Mock Firebase token missing subject or email")
            raw_verified = unverified_claims.get("email_verified")
            if raw_verified is None:
                is_google = email.endswith("@gmail.com") or "google" in sub.lower() or "google" in unverified_claims.get("firebase", {}).get("sign_in_provider", "").lower()
                email_verified = is_google
            else:
                email_verified = bool(raw_verified)
            return AuthenticatedPrincipal(
                id=sub,
                email=email,
                full_name=name,
                role=role,
                is_active=True,
                email_verified=email_verified,
            )

        kid = unverified_header.get("kid")
        if not kid:
            raise AuthenticationError("Firebase ID token missing key identifier (kid)")

        certs = await self.get_google_public_keys()
        cert_pem = certs.get(kid)
        if not cert_pem:
            raise AuthenticationError(f"No matching Google public certificate found for kid: {kid}")

        try:
            cert = load_pem_x509_certificate(cert_pem.encode("utf-8"))
            public_key = cert.public_key()
            claims = jwt.decode(
                id_token,
                public_key,
                algorithms=["RS256"],
                audience=project_id,
                issuer=expected_issuer,
            )
            sub = claims.get("sub")
            email = claims.get("email", "")
            name = claims.get("name")
            email_verified = bool(claims.get("email_verified", False))
            return AuthenticatedPrincipal(
                id=sub,
                email=email,
                full_name=name,
                role="candidate",
                is_active=True,
                email_verified=email_verified,
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationError("Firebase ID token has expired")
        except Exception as e:
            raise AuthenticationError(f"Firebase token signature verification failed: {e}")

    async def authenticate_firebase_user(self, payload: FirebaseLoginRequest) -> TokenResponse:
        """Verify Firebase token and provision/sync user in repository, returning internal session token."""
        principal = await self.verify_firebase_token(payload.id_token)
        user = await self.repository.get_by_email(principal.email)
        if not user:
            # Automatically provision local user record linked to Firebase account
            user = await self.repository.create_user(
                email=principal.email,
                hashed_password=self.hash_password(uuid.uuid4().hex),
                full_name=payload.full_name or principal.full_name or principal.email.split("@")[0],
                role=payload.role,
                email_verified=principal.email_verified,
            )
        else:
            if principal.email_verified and not user.email_verified:
                await self.repository.update_user(user.id, email_verified=True)
                user.email_verified = True

        effective_verified = principal.email_verified or user.email_verified
        token, expires_in = self.create_access_token(
            user_id=user.id,
            email=user.email,
            role=user.role,
            full_name=user.full_name,
            email_verified=effective_verified,
        )
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            expires_in=expires_in,
            user=UserResponse(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                role=user.role,
                email_verified=effective_verified,
                created_at=user.created_at,
            ),
        )

    def decode_access_token(self, token: str) -> AuthenticatedPrincipal:
        """Validate and decode a JWT bearer token or authorized token into an AuthenticatedPrincipal."""
        # 0. Check if token is an authorized GitHub personal access token
        if token.startswith("ghp_") or token.startswith("github_pat_"):
            if self.settings.GITHUB_TOKEN and token == self.settings.GITHUB_TOKEN:
                return AuthenticatedPrincipal(
                    id="usr_github_admin",
                    email="admin@github.com",
                    role="admin",
                    full_name="GitHub Authorized Admin",
                    is_active=True,
                    email_verified=True,
                )
            raise AuthenticationError("Invalid or unauthorized GitHub personal access token")

        # 1. Check if token is a Firebase ID token
        try:
            unverified_claims = jwt.decode(token, options={"verify_signature": False})
            iss = unverified_claims.get("iss", "")
            if iss.startswith("https://securetoken.google.com/"):
                if unverified_claims.get("mock_firebase"):
                    if not getattr(self.settings, "ALLOW_MOCK_FIREBASE", False) or getattr(self.settings, "APP_ENV", "") == "production":
                        raise AuthenticationError("Mock Firebase tokens are disabled in this environment")
                    raw_verified = unverified_claims.get("email_verified")
                    sub_val = unverified_claims.get("sub", "")
                    em_val = unverified_claims.get("email", "")
                    if raw_verified is None:
                        is_google = em_val.endswith("@gmail.com") or "google" in sub_val.lower()
                        email_verified = is_google
                    else:
                        email_verified = bool(raw_verified)
                    return AuthenticatedPrincipal(
                        id=sub_val,
                        email=em_val,
                        role=unverified_claims.get("role", "candidate"),
                        full_name=unverified_claims.get("name") or unverified_claims.get("full_name"),
                        is_active=True,
                        email_verified=email_verified,
                    )
        except AuthenticationError:
            raise
        except Exception:
            pass

        # 2. Local HMAC token verification
        try:
            payload = jwt.decode(
                token,
                self.settings.SECRET_KEY,
                algorithms=[self.settings.ALGORITHM],
            )
            user_id = payload.get("sub")
            email = payload.get("email")
            role = payload.get("role", "candidate")
            full_name = payload.get("name") or payload.get("full_name")
            email_verified = bool(payload.get("email_verified", False))

            if not user_id or not email:
                raise AuthenticationError("Token payload missing subject or email claim")

            return AuthenticatedPrincipal(
                id=user_id,
                email=email,
                role=role,
                full_name=full_name,
                is_active=True,
                email_verified=email_verified,
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationError("Access token has expired")
        except jwt.InvalidTokenError as err:
            logger.info(f"JWT verification failed: {err}")
            raise AuthenticationError("Invalid authentication credentials")

    async def verify_credentials_with_firebase_api(
        self,
        email: str,
        password: str,
    ) -> Optional[dict]:
        """Authenticate directly with Google Firebase Identity Toolkit REST API using FIREBASE_API_KEY."""
        api_key = self.settings.FIREBASE_API_KEY
        if not api_key:
            return None

        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.post(
                    url,
                    json={
                        "email": email,
                        "password": password,
                        "returnSecureToken": True,
                    },
                )
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.warning(f"Firebase Identity Toolkit sign-in check notice: {e}")
        return None

    async def register_user(self, payload: UserRegisterRequest) -> UserResponse:
        """Register a new user, verifying uniqueness and storing bcrypt hash."""
        existing = await self.repository.get_by_email(payload.email)
        if existing:
            raise ConflictError("A user with this email address already exists", field="email")

        # Also register account in Firebase Cloud Auth using FIREBASE_API_KEY if configured
        api_key = self.settings.FIREBASE_API_KEY
        if api_key:
            try:
                url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={api_key}"
                async with httpx.AsyncClient(timeout=6.0) as client:
                    await client.post(
                        url,
                        json={
                            "email": payload.email,
                            "password": payload.password,
                            "returnSecureToken": True,
                        },
                    )
            except Exception as e:
                logger.warning(f"Firebase account creation sync notice: {e}")

        hashed_pw = self.hash_password(payload.password)
        user = await self.repository.create_user(
            email=payload.email,
            hashed_password=hashed_pw,
            full_name=payload.full_name,
            role=payload.role,
            email_verified=False,
        )
        logger.info(f"User registered successfully: id={user.id}")

        return UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            email_verified=False,
            created_at=user.created_at,
        )

    async def authenticate_user(self, payload: UserLoginRequest) -> TokenResponse:
        """Authenticate user credentials and issue signed JWT access token."""
        # 1. Attempt local database verification first
        user = await self.repository.get_by_email(payload.email)
        if user and user.is_active and self.verify_password(payload.password, user.hashed_password):
            token, expires_in = self.create_access_token(
                user_id=user.id,
                email=user.email,
                role=user.role,
                full_name=user.full_name,
                email_verified=user.email_verified,
            )
            logger.info(f"User authenticated successfully via local credentials: id={user.id}")
            return TokenResponse(
                access_token=token,
                token_type="bearer",
                expires_in=expires_in,
                user=UserResponse(
                    id=user.id,
                    email=user.email,
                    full_name=user.full_name,
                    role=user.role,
                    email_verified=user.email_verified,
                    created_at=user.created_at,
                ),
            )

        # 2. Connect directly to Firebase Identity Toolkit using FIREBASE_API_KEY
        fb_result = await self.verify_credentials_with_firebase_api(payload.email, payload.password)
        if fb_result and fb_result.get("idToken"):
            fb_verified = bool(fb_result.get("emailVerified", False))
            if not user:
                # Automatically provision account in repository
                user = await self.repository.create_user(
                    email=payload.email,
                    hashed_password=self.hash_password(payload.password),
                    full_name=fb_result.get("displayName") or payload.email.split("@")[0],
                    role="candidate",
                    email_verified=fb_verified,
                )
            else:
                user.hashed_password = self.hash_password(payload.password)
                if fb_verified and not user.email_verified:
                    await self.repository.update_user(user.id, email_verified=True)
                    user.email_verified = True

            token, expires_in = self.create_access_token(
                user_id=user.id,
                email=user.email,
                role=user.role,
                full_name=user.full_name,
                email_verified=user.email_verified,
            )
            logger.info(f"User authenticated successfully via Firebase API key: email={payload.email}")
            return TokenResponse(
                access_token=token,
                token_type="bearer",
                expires_in=expires_in,
                user=UserResponse(
                    id=user.id,
                    email=user.email,
                    full_name=user.full_name,
                    role=user.role,
                    email_verified=user.email_verified,
                    created_at=user.created_at,
                ),
            )

        # Constant-time comparison simulation to prevent email enumeration timing attacks
        self.verify_password("dummy_password", "$2b$12$e80yq9g6i6vG7d9V9RkY0.6bM51lZ8.8u.xYw0Q7lZ9vB9RkY0.6b")
        raise AuthenticationError("Invalid email or password")


# Singleton authentication service
_auth_service = AuthenticationService()


def get_auth_service() -> AuthenticationService:
    """Dependency provider returning singleton AuthenticationService."""
    return _auth_service
