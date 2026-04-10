"""
backend/auth/jwt.py — Supabase JWT validation for Cassandra Voice Server.

Validates Supabase JWT tokens sent by authenticated clients.
Decodes the JWT, verifies the signature using the Supabase JWT secret,
and extracts org_id and user metadata.
"""

import time
from dataclasses import dataclass

import jwt
import httpx

from backend.config import get_settings
from backend.core.exceptions import InvalidJWTTokenError
from backend.utils.logging_config import get_logger

logger = get_logger("cassandra.auth")


@dataclass
class JWTClaims:
    """Claims extracted from a validated Supabase JWT."""

    sub: str               # User ID (UUID)
    org_id: str           # Organization ID (from custom claim)
    role: str             # User role
    email: str | None
    exp: int              # Expiration timestamp
    iat: int              # Issued at timestamp
    aud: str              # Audience
    iss: str              # Issuer (Supabase URL)


def decode_jwt(token: str) -> JWTClaims:
    """
    Decode and validate a Supabase JWT token.

    Args:
        token: The JWT string (without 'Bearer ' prefix).

    Returns:
        JWTClaims with extracted user and org information.

    Raises:
        InvalidJWTTokenError: If the token is invalid or expired.
    """
    settings = get_settings()

    if not settings.supabase_jwt_secret:
        logger.warning("jwt_validation_skipped", reason="jwt_secret_not_configured")
        raise InvalidJWTTokenError("JWT validation is not configured")

    try:
        # Decode without verification first to check claims
        unverified = jwt.decode(
            token,
            options={"verify_signature": False},
        )

        # Extract key claims for logging
        sub = unverified.get("sub", "")
        exp = unverified.get("exp", 0)

        # Check expiration
        if exp < time.time():
            raise InvalidJWTTokenError("JWT token has expired")

        # Verify signature
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience=unverified.get("aud", "authenticated"),
        )

        # Extract org_id from custom claims
        # Supabase stores this in app_metadata or as a custom claim
        org_id = (
            payload.get("org_id")
            or payload.get("organization_id")
            or payload.get("custom_claims", {}).get("org_id")
            or payload.get("app_meta", {}).get("org_id")
            or payload.get("sub")  # Fallback to user ID
        )

        role = payload.get("role", "tenant")
        email = payload.get("email")

        return JWTClaims(
            sub=sub,
            org_id=org_id,
            role=role,
            email=email,
            exp=exp,
            iat=payload.get("iat", 0),
            aud=payload.get("aud", ""),
            iss=payload.get("iss", ""),
        )

    except jwt.ExpiredSignatureError:
        logger.warning("jwt_expired", sub=sub)
        raise InvalidJWTTokenError("JWT token has expired")

    except jwt.InvalidTokenError as exc:
        logger.warning("jwt_invalid", error=str(exc))
        raise InvalidJWTTokenError(f"Invalid JWT token: {exc}")


async def refresh_jwt(
    refresh_token: str,
    supabase_url: str | None = None,
    supabase_key: str | None = None,
) -> dict:
    """
    Exchange a refresh token for a new access token.

    Args:
        refresh_token: The refresh token from Supabase Auth.
        supabase_url: Supabase project URL.
        supabase_key: Supabase anon key.

    Returns:
        Dict with access_token, refresh_token, expires_in, etc.
    """
    settings = get_settings()
    supabase_url = supabase_url or settings.supabase_url
    supabase_key = supabase_key or settings.supabase_key

    if not supabase_url:
        raise InvalidJWTTokenError("Supabase URL is not configured")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{supabase_url}/auth/v1/token?grant_type=refresh_token",
                headers={
                    "Content-Type": "application/json",
                    "apikey": supabase_key,
                },
                json={"refresh_token": refresh_token},
            )
            response.raise_for_status()
            return response.json()

    except httpx.HTTPError as exc:
        logger.error("jwt_refresh_failed", error=str(exc))
        raise InvalidJWTTokenError(f"JWT refresh failed: {exc}")
