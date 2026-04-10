"""
backend/tests/test_rate_limiter.py — Tests for rate limiting
"""

import pytest
from backend.auth.rate_limiter import get_month_reset, RateLimitStatus


class TestRateLimiter:
    """Tests for the rate limiter."""

    def test_get_month_reset(self):
        reset = get_month_reset()
        assert reset.day == 1
        # Should be next month
        from datetime import datetime
        now = datetime.utcnow()
        expected_month = now.month + 1 if now.month < 12 else 1
        assert reset.month == expected_month

    def test_rate_limit_status_unlimited(self):
        status = RateLimitStatus(
            can_proceed=True,
            current_usage_seconds=0,
            monthly_limit_seconds=0,
            remaining_seconds=0,
            is_exceeded=False,
            reset_at=get_month_reset(),
        )
        assert status.can_proceed is True
        assert status.is_exceeded is False
