"""Authentication and identity schemas conforming to docs/api-contracts.md."""

from datetime import datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRegisterRequest(BaseModel):
    """Payload for user registration."""

    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr = Field(description="Unique email address for the user")
    password: str = Field(min_length=8, max_length=128, description="User password (min 8 characters)")
    full_name: str = Field(min_length=1, max_length=128, description="Full name of user")
    role: Literal["candidate", "recruiter"] = Field(default="candidate", description="User system role")


class UserLoginRequest(BaseModel):
    """Payload for user login/token request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr = Field(description="Registered user email")
    password: str = Field(min_length=1, max_length=128, description="User password")


class UserResponse(BaseModel):
    """Public user profile information."""

    id: str = Field(description="Unique user identifier")
    email: EmailStr = Field(description="User email address")
    full_name: str = Field(description="User full name")
    role: str = Field(description="User system role")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TokenResponse(BaseModel):
    """JWT bearer token response."""

    access_token: str = Field(description="Signed JWT access token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(default=3600, description="Token lifetime in seconds")
    user: UserResponse = Field(description="Associated user profile")


class AuthenticatedPrincipal(BaseModel):
    """Validated caller principal passed to route handlers and authorization policies."""

    id: str = Field(description="Principal user identifier")
    email: str = Field(description="Principal email address")
    full_name: Optional[str] = None
    role: str = Field(default="candidate", description="Principal role")
    is_active: bool = Field(default=True, description="Account active status")


class FirebaseLoginRequest(BaseModel):
    """Payload to authenticate with Firebase ID token from client SDK."""

    id_token: str = Field(min_length=10, description="Raw Firebase ID token (JWT) from client SDK")
    role: Literal["candidate", "recruiter"] = Field(default="candidate", description="Assigned role if new user")


class FirebaseConfigResponse(BaseModel):
    """Public Firebase configuration needed by client Web SDK."""

    apiKey: str
    authDomain: str
    projectId: str
    storageBucket: str
    messagingSenderId: str
    appId: str
    measurementId: Optional[str] = None
