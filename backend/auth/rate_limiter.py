"""
backend/auth/rate_limiter.py — Per-role rate limit enforcement.

Enforces monthly voice usage limits based on user role:
- Tenant: 5400 seconds (90 minutes)
- Super Tenant: 5400 seconds
- Admin: 5400 seconds
- Org Super Admin: unlimited
- Owner: unlimited
- Master Admin: unlimited

Usage is tracked in the voice_usage_monthly table.
"""

from dataclasses import dataclass
from datetime import datetime

import httpx

from backend.config import get_settings
from backend.core.exceptions import MonthlyLimitExceededError
from backend.utils.logging_config import get_logger

logger = get_logger("cassandra.auth")


# WebSocket close code for rate limiting
WS_CLOSE_RATE_LIMITED = 4001


@dataclass
class RateLimitStatus:
    """Result of a rate limit check."""

    can_proceed: bool
    current_usage_seconds: int
    monthly_limit_seconds: int
    remaining_seconds: int
    is_exceeded: bool
    reset_at: datetime  # First day of next month


def get_month_reset() -> datetime:
    """Return the datetime when the current monthly period resets."""
    now = datetime.utcnow()
    # First day of next month
    if now.month == 12:
        return datetime(now.year + 1, 1, 1, tzinfo=now.tzinfo)
    return datetime(now.year, now.month + 1, 1, tzinfo=now.tzinfo)


async def check_rate_limit(
    org_id: str,
    user_id: str | None,
    role: str,
    supabase_url: str | None = None,
    supabase_key: str | None = None,
) -> RateLimitStatus:
    """
    Check if the user can proceed with a voice session.

    Args:
        org_id: Organization UUID.
        user_id: User UUID (optional).
        role: User's access role.
        supabase_url: Supabase URL.
        supabase_key: Supabase anon/service key.

    Returns:
        RateLimitStatus indicating whether the request can proceed.

    Raises:
        MonthlyLimitExceededError: If the limit has been exceeded.
    """
    settings = get_settings()
    supabase_url = supabase_url or settings.supabase_url
    supabase_key = supabase_key or settings.supabase_service_role_key

    monthly_limit = settings.get_role_limit(role)
    reset_at = get_month_reset()

    # Unlimited roles
    if settings.is_role_unlimited(role):
        logger.info(
            "rate_limit_unlimited_role",
            org_id=org_id,
            user_id=user_id,
            role=role,
        )
        return RateLimitStatus(
            can_proceed=True,
            current_usage_seconds=0,
            monthly_limit_seconds=0,
            remaining_seconds=0,
            is_exceeded=False,
            reset_at=reset_at,
        )

    if not supabase_url or not supabase_key:
        logger.warning(
            "rate_limit_check_skipped",
            reason="supabase_not_configured",
            role=role,
        )
        # Allow in development with dev key
        return RateLimitStatus(
            can_proceed=True,
            current_usage_seconds=0,
            monthly_limit_seconds=monthly_limit,
            remaining_seconds=monthly_limit,
            is_exceeded=False,
            reset_at=reset_at,
        )

    try:
        # Query current month's usage
        now = datetime.utcnow()
        year = now.year
        month = now.month

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{supabase_url}/rest/v1/voice_usage_monthly",
                params={
                    "org_id": f"eq.{org_id}",
                    "user_id": f"{'eq.' + user_id if user_id else 'is.null'}",
                    "year": f"eq.{year}",
                    "month": f"eq.{month}",
                    "select": "audio_seconds",
                },
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                },
            )
            response.raise_for_status()
            result = response.json()

        current_usage = 0
        if result:
            current_usage = result[0].get("audio_seconds", 0)

        remaining = max(0, monthly_limit - current_usage)
        is_exceeded = current_usage >= monthly_limit

        logger.info(
            "rate_limit_checked",
            org_id=org_id,
            user_id=user_id,
            role=role,
            current_usage=current_usage,
            monthly_limit=monthly_limit,
            remaining=remaining,
            is_exceeded=is_exceeded,
        )

        return RateLimitStatus(
            can_proceed=not is_exceeded,
            current_usage_seconds=current_usage,
            monthly_limit_seconds=monthly_limit,
            remaining_seconds=remaining,
            is_exceeded=is_exceeded,
            reset_at=reset_at,
        )

    except httpx.HTTPError as exc:
        logger.error("rate_limit_check_failed", error=str(exc))
        # Fail open — allow the session if we can't check
        return RateLimitStatus(
            can_proceed=True,
            current_usage_seconds=0,
            monthly_limit_seconds=monthly_limit,
            remaining_seconds=monthly_limit,
            is_exceeded=False,
            reset_at=reset_at,
        )


async def enforce_rate_limit(
    org_id: str,
    user_id: str | None,
    role: str,
    session_audio_seconds: int = 0,
    stt_calls: int = 0,
    tts_tokens: int = 0,
    llm_tokens: int = 0,
    tool_calls: int = 0,
    supabase_url: str | None = None,
    supabase_key: str | None = None,
) -> RateLimitStatus:
    """
    Enforce rate limits by checking AND recording usage atomically.

    This calls the increment_voice_usage RPC function which atomically
    updates the usage counters.

    Args:
        org_id: Organization UUID.
        user_id: User UUID (optional).
        role: User's access role.
        session_audio_seconds: Seconds of audio in this session.
        stt_calls: Number of STT calls.
        tts_tokens: Number of TTS tokens.
        llm_tokens: Number of LLM tokens.
        tool_calls: Number of tool calls.
        supabase_url: Supabase URL.
        supabase_key: Service role key.

    Returns:
        RateLimitStatus after recording usage.
    """
    settings = get_settings()
    supabase_url = supabase_url or settings.supabase_url
    supabase_key = supabase_key or settings.supabase_service_role_key

    monthly_limit = settings.get_role_limit(role)
    reset_at = get_month_reset()

    if settings.is_role_unlimited(role):
        return RateLimitStatus(
            can_proceed=True,
            current_usage_seconds=0,
            monthly_limit_seconds=0,
            remaining_seconds=0,
            is_exceeded=False,
            reset_at=reset_at,
        )

    if not supabase_url or not supabase_key:
        return RateLimitStatus(
            can_proceed=True,
            current_usage_seconds=session_audio_seconds,
            monthly_limit_seconds=monthly_limit,
            remaining_seconds=max(0, monthly_limit - session_audio_seconds),
            is_exceeded=False,
            reset_at=reset_at,
        )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{supabase_url}/rest/v1/rpc/increment_voice_usage",
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "representation",
                },
                json={
                    "p_org_id": org_id,
                    "p_user_id": user_id,
                    "p_audio_seconds": session_audio_seconds,
                    "p_stt_calls": stt_calls,
                    "p_tts_tokens": tts_tokens,
                    "p_llm_tokens": llm_tokens,
                    "p_tool_calls": tool_calls,
                    "p_role": role,
                    "p_limit_seconds": monthly_limit,
                },
            )
            response.raise_for_status()
            result = response.json()

        if result and isinstance(result, list):
            record = result[0]
            audio_total = record.get("audio_seconds_total", 0)
            limit = record.get("monthly_limit_seconds", monthly_limit)
            is_exceeded = record.get("is_exceeded", False)
            remaining = max(0, limit - audio_total)

            return RateLimitStatus(
                can_proceed=not is_exceeded,
                current_usage_seconds=audio_total,
                monthly_limit_seconds=limit,
                remaining_seconds=remaining,
                is_exceeded=is_exceeded,
                reset_at=reset_at,
            )

        # Fallback
        return RateLimitStatus(
            can_proceed=True,
            current_usage_seconds=session_audio_seconds,
            monthly_limit_seconds=monthly_limit,
            remaining_seconds=max(0, monthly_limit - session_audio_seconds),
            is_exceeded=False,
            reset_at=reset_at,
        )

    except httpx.HTTPError as exc:
        logger.error("rate_limit_enforcement_failed", error=str(exc))
        return RateLimitStatus(
            can_proceed=True,
            current_usage_seconds=session_audio_seconds,
            monthly_limit_seconds=monthly_limit,
            remaining_seconds=max(0, monthly_limit - session_audio_seconds),
            is_exceeded=False,
            reset_at=reset_at,
        )
