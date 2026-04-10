"""
backend/auth/middleware.py — WebSocket authentication middleware.

Provides async context managers and helpers for authenticating WebSocket
connections using either API key or Supabase JWT.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.auth.api_key import validate_api_key, APIKeyInfo
from backend.auth.jwt import decode_jwt, JWTClaims
from backend.auth.rate_limiter import check_rate_limit, RateLimitStatus
from backend.core.exceptions import (
    AuthError,
    InvalidAPIKeyError,
    InvalidJWTTokenError,
    MissingAuthError,
    MonthlyLimitExceededError,
)
from backend.utils.logging_config import get_logger

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = get_logger("cassandra.auth")


@dataclass
class AuthContext:
    """
    Authentication context extracted from a WebSocket connection.

    This is attached to each session and used throughout the request lifecycle.
    """

    # Identity
    org_id: str
    user_id: str | None

    # Role & permissions
    role: str
    is_super_admin: bool = field(init=False)

    # Auth method used
    auth_method: str  # "api_key" or "jwt"

    # Rate limiting
    rate_limit_status: RateLimitStatus | None = None

    # Source key/token info
    api_key_info: APIKeyInfo | None = None
    jwt_claims: JWTClaims | None = None

    def __post_init__(self):
        # Convenience flag
        self.is_super_admin = self.role in (
            "master_admin", "org_super_admin", "owner"
        )


async def authenticate_session_start(
    message: dict,
    websocket: "WebSocket | None" = None,
) -> AuthContext:
    """
    Authenticate a V2 session_start message.

    Supports two auth methods:
    1. api_key: sk_cassandra_... key
    2. token: Supabase JWT

    Args:
        message: The V2 session_start message dict.
        websocket: Optional WebSocket for rejection.

    Returns:
        AuthContext with identity and permissions.

    Raises:
        MissingAuthError: No credentials provided.
        InvalidAPIKeyError: API key is invalid.
        InvalidJWTTokenError: JWT is invalid.
        MonthlyLimitExceededError: Usage limit exceeded.
    """
    api_key = message.get("api_key")
    jwt_token = message.get("token")

    if not api_key and not jwt_token:
        logger.warning("auth_missing_credentials", websocket_id=id(websocket) if websocket else None)
        raise MissingAuthError(
            "No authentication credentials provided. "
            "Include 'api_key' or 'token' in session_start message."
        )

    # Prefer API key over JWT
    if api_key:
        return await _authenticate_api_key(api_key)

    if jwt_token:
        return await _authenticate_jwt(jwt_token)

    raise MissingAuthError("No valid authentication credentials found.")


async def _authenticate_api_key(plain_key: str) -> AuthContext:
    """Authenticate using an API key."""
    logger.info("auth_attempt", method="api_key", key_prefix=plain_key[:18])

    key_info = await validate_api_key(plain_key)

    # Check rate limit
    rate_status = await check_rate_limit(
        org_id=key_info.org_id,
        user_id=key_info.user_id,
        role=key_info.role,
    )

    if not rate_status.can_proceed:
        raise MonthlyLimitExceededError(
            f"Monthly usage limit exceeded. "
            f"Resets at {rate_status.reset_at.isoformat()}",
            remaining_seconds=rate_status.remaining_seconds,
        )

    logger.info(
        "auth_success",
        method="api_key",
        org_id=key_info.org_id,
        user_id=key_info.user_id,
        role=key_info.role,
    )

    return AuthContext(
        org_id=key_info.org_id,
        user_id=key_info.user_id,
        role=key_info.role,
        auth_method="api_key",
        rate_limit_status=rate_status,
        api_key_info=key_info,
    )


async def _authenticate_jwt(token: str) -> AuthContext:
    """Authenticate using a Supabase JWT."""
    logger.info("auth_attempt", method="jwt")

    claims = decode_jwt(token)

    # Check rate limit
    rate_status = await check_rate_limit(
        org_id=claims.org_id,
        user_id=claims.sub,
        role=claims.role,
    )

    if not rate_status.can_proceed:
        raise MonthlyLimitExceededError(
            f"Monthly usage limit exceeded. "
            f"Resets at {rate_status.reset_at.isoformat()}",
            remaining_seconds=rate_status.remaining_seconds,
        )

    logger.info(
        "auth_success",
        method="jwt",
        org_id=claims.org_id,
        user_id=claims.sub,
        role=claims.role,
    )

    return AuthContext(
        org_id=claims.org_id,
        user_id=claims.sub,
        role=claims.role,
        auth_method="jwt",
        rate_limit_status=rate_status,
        jwt_claims=claims,
    )


async def authenticate_legacy_connection(
    websocket: "WebSocket | None" = None,
) -> AuthContext:
    """
    Authenticate a V1 legacy connection (OpenAI Realtime relay).

    Legacy connections have no auth — this is the existing behavior.
    Returns a minimal AuthContext with default values.

    In production, you may want to add IP-based or header-based auth here.
    """
    logger.info("auth_legacy_connection", websocket_id=id(websocket) if websocket else None)

    return AuthContext(
        org_id="legacy",
        user_id=None,
        role="tenant",
        auth_method="none",
    )
