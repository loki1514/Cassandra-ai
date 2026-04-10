"""
T29: Error Handling

This module provides comprehensive error handling:
- Graceful degradation
- Partial failure handling
- Circuit breaker pattern
- Error classification

Features:
- Fallback strategies
- Retry policies
- Error reporting
- User-friendly messages
"""

import functools
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type, Union
from contextlib import asynccontextmanager

import structlog

logger = structlog.get_logger("cassandra.errors")


class ErrorCategory(str, Enum):
    """Categories of errors."""
    TRANSIENT = "transient"  # May succeed on retry
    PERMANENT = "permanent"  # Will not succeed on retry
    AUTHENTICATION = "authentication"  # Auth-related
    AUTHORIZATION = "authorization"  # Permission-related
    VALIDATION = "validation"  # Input validation
    NOT_FOUND = "not_found"  # Resource not found
    TIMEOUT = "timeout"  # Operation timed out
    RATE_LIMIT = "rate_limit"  # Rate limited
    DEPENDENCY = "dependency"  # External dependency failure
    UNKNOWN = "unknown"  # Unclassified


class ErrorSeverity(str, Enum):
    """Error severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ErrorInfo:
    """Structured error information."""
    
    message: str
    category: ErrorCategory
    severity: ErrorSeverity
    error_code: str
    details: Optional[Dict[str, Any]] = None
    retryable: bool = False
    user_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "message": self.message,
            "category": self.category.value,
            "severity": self.severity.value,
            "error_code": self.error_code,
            "details": self.details,
            "retryable": self.retryable,
            "user_message": self.user_message
        }


class CassandraError(Exception):
    """Base exception for Cassandra AI errors."""
    
    def __init__(
        self,
        message: str,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        error_code: str = "UNKNOWN_ERROR",
        details: Optional[Dict[str, Any]] = None,
        retryable: bool = False,
        user_message: Optional[str] = None
    ):
        super().__init__(message)
        self.error_info = ErrorInfo(
            message=message,
            category=category,
            severity=severity,
            error_code=error_code,
            details=details,
            retryable=retryable,
            user_message=user_message or message
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return self.error_info.to_dict()


class TransientError(CassandraError):
    """Transient error that may succeed on retry."""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message=message,
            category=ErrorCategory.TRANSIENT,
            severity=ErrorSeverity.MEDIUM,
            error_code="TRANSIENT_ERROR",
            retryable=True,
            **kwargs
        )


class PermanentError(CassandraError):
    """Permanent error that will not succeed on retry."""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message=message,
            category=ErrorCategory.PERMANENT,
            severity=ErrorSeverity.HIGH,
            error_code="PERMANENT_ERROR",
            retryable=False,
            **kwargs
        )


class DependencyError(CassandraError):
    """External dependency failure."""
    
    def __init__(self, message: str, dependency: str, **kwargs):
        super().__init__(
            message=message,
            category=ErrorCategory.DEPENDENCY,
            severity=ErrorSeverity.HIGH,
            error_code="DEPENDENCY_ERROR",
            retryable=True,
            details={"dependency": dependency, **(kwargs.get("details", {}))},
            **kwargs
        )


class CircuitBreaker:
    """
    Circuit breaker pattern for fault tolerance.
    
    States:
    - CLOSED: Normal operation
    - OPEN: Failing, reject requests
    - HALF_OPEN: Testing if service recovered
    
    Usage:
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
        
        @breaker
        async def call_external_service():
            ...
    """
    
    class State(Enum):
        CLOSED = "closed"
        OPEN = "open"
        HALF_OPEN = "half_open"
    
    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3
    ):
        """
        Initialize circuit breaker.
        
        Args:
            name: Circuit breaker name
            failure_threshold: Failures before opening
            recovery_timeout: Seconds before half-open
            half_open_max_calls: Calls in half-open state
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        
        self._state = self.State.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0
        
        logger.info(
            "circuit_breaker_initialized",
            name=name,
            failure_threshold=failure_threshold
        )
    
    @property
    def state(self) -> State:
        """Get current state."""
        return self._state
    
    @property
    def is_open(self) -> bool:
        """Check if circuit is open."""
        return self._state == self.State.OPEN
    
    def _can_attempt_reset(self) -> bool:
        """Check if enough time has passed to try reset."""
        if self._last_failure_time is None:
            return True
        return time.time() - self._last_failure_time >= self.recovery_timeout
    
    def _on_success(self):
        """Handle successful call."""
        if self._state == self.State.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.half_open_max_calls:
                # Service recovered, close circuit
                self._state = self.State.CLOSED
                self._failure_count = 0
                self._success_count = 0
                self._half_open_calls = 0
                logger.info("circuit_breaker_closed", name=self.name)
        else:
            self._failure_count = 0
    
    def _on_failure(self):
        """Handle failed call."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        
        if self._state == self.State.HALF_OPEN:
            # Still failing, reopen
            self._state = self.State.OPEN
            self._half_open_calls = 0
            logger.warning("circuit_breaker_reopened", name=self.name)
        elif self._failure_count >= self.failure_threshold:
            # Open circuit
            self._state = self.State.OPEN
            logger.warning(
                "circuit_breaker_opened",
                name=self.name,
                failure_count=self._failure_count
            )
    
    def __call__(self, func: Callable) -> Callable:
        """Decorator to wrap function with circuit breaker."""
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Check if circuit is open
            if self._state == self.State.OPEN:
                if self._can_attempt_reset():
                    self._state = self.State.HALF_OPEN
                    self._half_open_calls = 0
                    self._success_count = 0
                    logger.info("circuit_breaker_half_open", name=self.name)
                else:
                    raise CircuitBreakerOpenError(self.name)
            
            # In half-open, limit concurrent calls
            if self._state == self.State.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitBreakerOpenError(self.name)
                self._half_open_calls += 1
            
            try:
                result = await func(*args, **kwargs)
                self._on_success()
                return result
            except Exception as e:
                self._on_failure()
                raise
        
        return async_wrapper


class CircuitBreakerOpenError(CassandraError):
    """Raised when circuit breaker is open."""
    
    def __init__(self, circuit_name: str):
        super().__init__(
            message=f"Circuit breaker '{circuit_name}' is open",
            category=ErrorCategory.DEPENDENCY,
            severity=ErrorSeverity.HIGH,
            error_code="CIRCUIT_BREAKER_OPEN",
            user_message="Service temporarily unavailable. Please try again later."
        )
        self.circuit_name = circuit_name


class FallbackStrategy:
    """
    Fallback strategy for graceful degradation.
    
    Usage:
        strategy = FallbackStrategy(default_value=[])
        
        @strategy
        async def fetch_data():
            # If this fails, returns default_value
            ...
    """
    
    def __init__(
        self,
        default_value: Any = None,
        fallback_func: Optional[Callable] = None,
        log_fallback: bool = True
    ):
        """
        Initialize fallback strategy.
        
        Args:
            default_value: Value to return on failure
            fallback_func: Optional function to call on failure
            log_fallback: Whether to log fallback events
        """
        self.default_value = default_value
        self.fallback_func = fallback_func
        self.log_fallback = log_fallback
    
    def __call__(self, func: Callable) -> Callable:
        """Decorator to add fallback."""
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if self.log_fallback:
                    logger.warning(
                        "fallback_activated",
                        function=func.__name__,
                        error=str(e)[:100]
                    )
                
                if self.fallback_func:
                    try:
                        return await self.fallback_func(*args, **kwargs)
                    except Exception as fallback_error:
                        logger.error(
                            "fallback_failed",
                            error=str(fallback_error)[:100]
                        )
                
                return self.default_value
        
        return async_wrapper


class PartialResultHandler:
    """
    Handler for partial failure scenarios.
    
    Returns successful results even if some operations fail.
    
    Usage:
        handler = PartialResultHandler()
        
        results = await handler.process_batch(
            items,
            process_func=process_item
        )
        # Returns both successful results and failures
    """
    
    def __init__(self, continue_on_error: bool = True):
        """
        Initialize handler.
        
        Args:
            continue_on_error: Continue processing on individual failures
        """
        self.continue_on_error = continue_on_error
    
    async def process_batch(
        self,
        items: List[Any],
        process_func: Callable[[Any], Any],
        item_key_func: Optional[Callable[[Any], str]] = None
    ) -> Dict[str, Any]:
        """
        Process batch with partial failure handling.
        
        Args:
            items: Items to process
            process_func: Function to process each item
            item_key_func: Function to get item key
            
        Returns:
            Dict with 'successes', 'failures', and 'summary'
        """
        successes = []
        failures = []
        
        for item in items:
            item_key = item_key_func(item) if item_key_func else str(item)
            
            try:
                result = await process_func(item)
                successes.append({
                    "item": item_key,
                    "result": result
                })
            except Exception as e:
                failures.append({
                    "item": item_key,
                    "error": str(e),
                    "error_type": type(e).__name__
                })
                
                if not self.continue_on_error:
                    break
        
        return {
            "successes": successes,
            "failures": failures,
            "summary": {
                "total": len(items),
                "successful": len(successes),
                "failed": len(failures),
                "success_rate": len(successes) / len(items) if items else 1.0
            }
        }


@asynccontextmanager
async def error_boundary(
    operation_name: str,
    fallback_value: Any = None,
    reraise: bool = False
):
    """
    Context manager for error boundaries.
    
    Usage:
        async with error_boundary("database_query", fallback_value=[]):
            results = await db.query(...)
    """
    try:
        yield
    except Exception as e:
        logger.error(
            "error_boundary_caught",
            operation=operation_name,
            error_type=type(e).__name__,
            error=str(e)[:200]
        )
        
        if reraise:
            raise
        
        return fallback_value


def classify_error(error: Exception) -> ErrorInfo:
    """
    Classify an exception into ErrorInfo.
    
    Args:
        error: Exception to classify
        
    Returns:
        ErrorInfo classification
    """
    # Handle known error types
    if isinstance(error, CassandraError):
        return error.error_info
    
    # Classify by exception type
    error_type = type(error).__name__
    
    classification_map = {
        "ConnectionError": (ErrorCategory.DEPENDENCY, ErrorSeverity.HIGH, True),
        "TimeoutError": (ErrorCategory.TIMEOUT, ErrorSeverity.HIGH, True),
        "AuthenticationError": (ErrorCategory.AUTHENTICATION, ErrorSeverity.HIGH, False),
        "PermissionError": (ErrorCategory.AUTHORIZATION, ErrorSeverity.HIGH, False),
        "ValueError": (ErrorCategory.VALIDATION, ErrorSeverity.MEDIUM, False),
        "KeyError": (ErrorCategory.NOT_FOUND, ErrorSeverity.MEDIUM, False),
        "FileNotFoundError": (ErrorCategory.NOT_FOUND, ErrorSeverity.MEDIUM, False),
    }
    
    category, severity, retryable = classification_map.get(
        error_type,
        (ErrorCategory.UNKNOWN, ErrorSeverity.MEDIUM, False)
    )
    
    return ErrorInfo(
        message=str(error),
        category=category,
        severity=severity,
        error_code=f"{error_type.upper()}_ERROR",
        retryable=retryable,
        user_message="An error occurred. Please try again."
    )


# =============================================================================
# FastAPI Error Handlers
# =============================================================================

from fastapi import FastAPI, Request, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

def setup_error_handlers(app: FastAPI):
    """Setup error handlers for FastAPI app."""
    
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """Handle HTTP exceptions."""
        error_info = ErrorInfo(
            message=exc.detail,
            category=ErrorCategory.PERMANENT,
            severity=ErrorSeverity.MEDIUM,
            error_code=f"HTTP_{exc.status_code}",
            user_message=exc.detail
        )
        
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": error_info.to_dict()
            }
        )
    
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handle validation errors."""
        error_info = ErrorInfo(
            message="Validation error",
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.LOW,
            error_code="VALIDATION_ERROR",
            details={"errors": exc.errors()},
            user_message="Invalid input. Please check your request."
        )
        
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": error_info.to_dict()
            }
        )
    
    @app.exception_handler(CassandraError)
    async def cassandra_error_handler(request: Request, exc: CassandraError):
        """Handle Cassandra errors."""
        status_code = 500
        
        if exc.error_info.category == ErrorCategory.AUTHENTICATION:
            status_code = 401
        elif exc.error_info.category == ErrorCategory.AUTHORIZATION:
            status_code = 403
        elif exc.error_info.category == ErrorCategory.NOT_FOUND:
            status_code = 404
        elif exc.error_info.category == ErrorCategory.VALIDATION:
            status_code = 422
        elif exc.error_info.category == ErrorCategory.RATE_LIMIT:
            status_code = 429
        
        return JSONResponse(
            status_code=status_code,
            content={
                "success": False,
                "error": exc.to_dict()
            }
        )
    
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle all other exceptions."""
        error_info = classify_error(exc)
        
        logger.error(
            "unhandled_exception",
            error_type=type(exc).__name__,
            error=str(exc)[:200],
            path=request.url.path
        )
        
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": error_info.to_dict()
            }
        )
