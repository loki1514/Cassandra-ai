"""
backend/tests/test_circuit_breaker.py — Tests for circuit breaker
"""

import pytest
import asyncio
from backend.utils.circuit_breaker import CircuitBreaker, CircuitState, CircuitBreakerOpen


class TestCircuitBreaker:
    """Tests for the CircuitBreaker class."""

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(name="test", failure_threshold=3, recovery_timeout=1.0)
        assert cb.state == CircuitState.CLOSED

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=0.1)

        async def failing_func():
            raise ValueError("test error")

        async def run():
            for _ in range(3):
                try:
                    await cb.call(failing_func)
                except ValueError:
                    pass

        asyncio.run(run())
        assert cb.state == CircuitState.OPEN

    def test_raises_when_open(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0.1)
        cb._state = CircuitState.OPEN

        async def any_func():
            return "success"

        async def run():
            try:
                await cb.call(any_func)
                assert False, "Should have raised CircuitBreakerOpen"
            except CircuitBreakerOpen:
                pass

        asyncio.run(run())

    def test_reset(self):
        cb = CircuitBreaker(name="test")
        cb._state = CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb._consecutive_failures == 0
