"""
backend/utils/circuit_breaker.py — Circuit breaker pattern for external service calls.

Prevents cascading failures when external services (OpenAI, ElevenLabs, Supabase)
are unavailable. Three states:
- CLOSED: Normal operation, failures are tracked
- OPEN: After N failures, calls fail immediately without making requests
- HALF_OPEN: After recovery timeout, one test call is allowed

Based on Martin Fowler's circuit breaker pattern.
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, TypeVar

from backend.config import get_settings

T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerStats:
    """Statistics for a circuit breaker."""

    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0  # Rejected due to open circuit
    state: CircuitState = CircuitState.CLOSED
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    consecutive_failures: int = 0


class CircuitBreakerOpen(Exception):
    """Raised when a call is rejected because the circuit is open."""

    def __init__(self, name: str, recovery_timeout: float):
        self.name = name
        self.recovery_timeout = recovery_timeout
        super().__init__(
            f"Circuit breaker '{name}' is open. "
            f"Retry after {recovery_timeout:.0f}s."
        )


@dataclass
class CircuitBreaker:
    """
    A circuit breaker that guards calls to external services.

    Attributes:
        name: Identifier for logging and debugging.
        failure_threshold: Number of consecutive failures to open the circuit.
        recovery_timeout: Seconds to wait before transitioning OPEN -> HALF_OPEN.
        half_open_max_calls: Max test calls allowed in HALF_OPEN state.
    """

    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 1

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _half_open_calls: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    stats: CircuitBreakerStats = field(default_factory=CircuitBreakerStats)

    def __post_init__(self) -> None:
        settings = get_settings()
        self.failure_threshold = (
            settings.circuit_breaker_failure_threshold or self.failure_threshold
        )
        self.recovery_timeout = (
            settings.circuit_breaker_recovery_timeout_seconds or self.recovery_timeout
        )

    @property
    def state(self) -> CircuitState:
        """Return current circuit state, checking for timeout transitions."""
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._transition_to(CircuitState.HALF_OPEN)
        return self._state

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state."""
        old_state = self._state
        self._state = new_state
        self.stats.state = new_state

        if new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0

        # Log transition (using standard logger to avoid circular import)
        import logging
        logging.getLogger("cassandra.circuit_breaker").info(
            f"Circuit '{self.name}': {old_state.value} -> {new_state.value}"
        )

    def _record_success(self) -> None:
        """Record a successful call."""
        self._consecutive_failures = 0
        self._last_success_time = time.time()
        self.stats.successful_calls += 1

        if self._state == CircuitState.HALF_OPEN:
            self._half_open_calls += 1
            if self._half_open_calls >= self.half_open_max_calls:
                self._transition_to(CircuitState.CLOSED)

    def _record_failure(self) -> None:
        """Record a failed call."""
        self._consecutive_failures += 1
        self._last_failure_time = time.time()
        self.stats.failed_calls += 1

        if self._state == CircuitState.HALF_OPEN:
            self._transition_to(CircuitState.OPEN)
        elif self._consecutive_failures >= self.failure_threshold:
            self._transition_to(CircuitState.OPEN)

    async def call(
        self,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """
        Execute an async function through the circuit breaker.

        Args:
            func: Async function to call.
            *args, **kwargs: Arguments to pass to func.

        Returns:
            The result of func.

        Raises:
            CircuitBreakerOpen: If the circuit is open.
            Exception: Any exception raised by func (after recording it).
        """
        if self.state == CircuitState.OPEN:
            self.stats.rejected_calls += 1
            raise CircuitBreakerOpen(self.name, self.recovery_timeout)

        try:
            result = await func(*args, **kwargs)
            self._record_success()
            self.stats.total_calls += 1
            return result
        except Exception as exc:
            self._record_failure()
            self.stats.total_calls += 1
            raise exc

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._half_open_calls = 0
        self.stats.state = CircuitState.CLOSED


class CircuitBreakerRegistry:
    """
    A registry for managing multiple circuit breakers by name.

    Usage:
        registry = CircuitBreakerRegistry()
        cb = registry.get("openai")
        result = await cb.call(openai_api_call)
    """

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ) -> CircuitBreaker:
        """Get or create a circuit breaker by name."""
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
            )
        return self._breakers[name]

    def all_stats(self) -> dict[str, CircuitBreakerStats]:
        """Return stats for all registered circuit breakers."""
        return {name: cb.stats for name, cb in self._breakers.items()}

    def reset_all(self) -> None:
        """Reset all circuit breakers to CLOSED."""
        for cb in self._breakers.values():
            cb.reset()


# Global registry instance
_global_registry: CircuitBreakerRegistry | None = None


def get_breaker_registry() -> CircuitBreakerRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = CircuitBreakerRegistry()
    return _global_registry
