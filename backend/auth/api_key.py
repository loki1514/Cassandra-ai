"""
backend/auth/api_key.py — API key validation for Cassandra Voice Server.

API keys are:
- Generated with format: sk_cassandra_{base64url_token}
- Stored as SHA-256 hash in the database (plain key never stored)
- Associated with an org_id and user_id
- Support role-based access control
- Optional expiration (expires_at)
"""

import hashlib
import secrets
import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import httpx

from backend.config import get_settings
from backend.core.exceptions import InvalidAPIKeyError
from backend.utils.logging_config import get_logger

logger = get_logger("cassandra.auth")


@dataclass
class APIKeyInfo:
    """Information extracted from a validated API key."""

    key_id: str              # UUID of the api_keys record
    org_id: str              # Organization UUID
    user_id: str | None      # User UUID (optional)
    role: Literal[
        "tenant", "super_tenant", "admin",
        "org_super_admin", "owner", "master_admin"
    ]
    name: str | None         # Friendly key name
    is_active: bool
    expires_at: datetime | None


def generate_api_key() -> tuple[str, str]:
    """
    Generate a new API key.

    Returns:
        Tuple of (plain_key, key_hash).
        The plain_key should be returned to the user once (never stored).
        Only the key_hash is stored in the database.

    The plain key format is: sk_cassandra_{32 random base64url chars}
    """
    random_bytes = secrets.token_bytes(24)
    random_part = base64.urlsafe_b64encode(random_bytes).decode().rstrip("=")
    plain_key = f"sk_cassandra_{random_part}"

    key_hash = hash_sha256(plain_key)
    key_prefix = plain_key[:18]  # "sk_cassandra_" + first 8 chars of random

    return plain_key, key_hash


def hash_sha256(key: str) -> str:
    """Compute SHA-256 hash of an API key."""
    return hashlib.sha256(key.encode()).hexdigest()


async def validate_api_key(
    plain_key: str,
    supabase_url: str | None = None,
    supabase_service_key: str | None = None,
) -> APIKeyInfo:
    """
    Validate an API key against the database.

    Args:
        plain_key: The plain-text API key (e.g., 'sk_cassandra_...').
        supabase_url: Supabase project URL (from config if not provided).
        supabase_service_key: Supabase service role key (from config if not provided).

    Returns:
        APIKeyInfo with the key's associated metadata.

    Raises:
        InvalidAPIKeyError: If the key is invalid, expired, or revoked.
    """
    settings = get_settings()
    supabase_url = supabase_url or settings.supabase_url
    supabase_service_key = supabase_service_key or settings.supabase_service_role_key

    if not supabase_url or not supabase_service_key:
        logger.warning("api_key_validation_skipped reason=supabase_not_configured")
        # In development, allow a special dev key
        if plain_key == "sk_cassandra_dev":
            return APIKeyInfo(
                key_id="dev-key",
                org_id="dev-org",
                user_id=None,
                role="master_admin",
                name="Development Key",
                is_active=True,
                expires_at=None,
            )
        raise InvalidAPIKeyError("API key validation is not configured")

    # Validate format
    if not plain_key.startswith("sk_cassandra_"):
        raise InvalidAPIKeyError("Invalid API key format")

    # Hash and look up
    key_hash = hash_sha256(plain_key)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{supabase_url}/rest/v1/rpc/validate_api_key",
                headers={
                    "apikey": supabase_service_key,
                    "Authorization": f"Bearer {supabase_service_key}",
                    "Content-Type": "application/json",
                },
                json={"key_hash": key_hash},
            )

            if response.status_code != 200:
                raise InvalidAPIKeyError(f"Database error: {response.status_code}")

            result = response.json()
            if not result:
                raise InvalidAPIKeyError("API key not found")

            record = result[0] if isinstance(result, list) else result

            # Check if active
            if not record.get("is_active", False):
                raise InvalidAPIKeyError("API key has been revoked")

            # Check expiration
            expires_at_str = record.get("expires_at")
            if expires_at_str:
                expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                if datetime.now(expires_at.tzinfo) > expires_at:
                    raise InvalidAPIKeyError("API key has expired")

            return APIKeyInfo(
                key_id=record["id"],
                org_id=record["org_id"],
                user_id=record.get("user_id"),
                role=record.get("role", "tenant"),
                name=record.get("name"),
                is_active=record.get("is_active", True),
                expires_at=expires_at if expires_at_str else None,
            )

    except httpx.TimeoutException:
        logger.error("api_key_validation_timeout key_hash_prefix=%s", plain_key[:18])
        raise InvalidAPIKeyError("API key validation timed out")

    except httpx.HTTPError as exc:
        logger.error(
            "api_key_validation_http_error error=%s key_hash_prefix=%s",
            str(exc),
            plain_key[:18],
        )
        raise InvalidAPIKeyError(f"API key validation failed: {exc}")


async def create_api_key(
    org_id: str,
    user_id: str | None = None,
    role: str = "tenant",
    name: str | None = None,
    expires_at: datetime | None = None,
    supabase_url: str | None = None,
    supabase_service_key: str | None = None,
) -> tuple[str, str]:
    """
    Create and store a new API key.

    Args:
        org_id: Organization UUID.
        user_id: User UUID (optional).
        role: Access role.
        name: Friendly name for the key.
        expires_at: Expiration datetime (None = never).
        supabase_url: Supabase URL.
        supabase_service_key: Service role key.

    Returns:
        Tuple of (plain_key, key_id).
    """
    settings = get_settings()
    supabase_url = supabase_url or settings.supabase_url
    supabase_service_key = supabase_service_key or settings.supabase_service_role_key

    plain_key, key_hash = generate_api_key()
    key_prefix = plain_key[:18]

    record = {
        "key_hash": key_hash,
        "key_prefix": key_prefix,
        "org_id": org_id,
        "user_id": user_id,
        "role": role,
        "name": name,
        "is_active": True,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{supabase_url}/rest/v1/api_keys",
                headers={
                    "apikey": supabase_service_key,
                    "Authorization": f"Bearer {supabase_service_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation",
                },
                json=record,
            )
            response.raise_for_status()
            result = response.json()
            key_id = result[0]["id"] if isinstance(result, list) else result["id"]
            return plain_key, key_id

    except httpx.HTTPError as exc:
        logger.error("api_key_creation_failed error=%s", str(exc))
        raise
