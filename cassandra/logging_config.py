"""
T28: Structured Logging

This module provides structured logging configuration:
- structlog JSON logging
- trace_id propagation
- No PII in logs
- Context-aware logging

Features:
- JSON output for production
- Pretty printing for development
- Automatic context binding
- Request tracing
"""

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any, Dict, Optional

import structlog
from structlog.types import EventDict, WrappedLogger

# Context variable for trace ID
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
org_id_var: ContextVar[str] = ContextVar("org_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")

# PII fields to redact
PII_FIELDS = {
    "password", "token", "secret", "api_key", "credit_card",
    "ssn", "social_security", "email", "phone", "address",
    "authorization", "cookie", "session_id"
}


def add_trace_id(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict
) -> EventDict:
    """Add trace_id to log entry."""
    event_dict["trace_id"] = trace_id_var.get() or ""
    return event_dict


def add_request_context(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict
) -> EventDict:
    """Add request context to log entry."""
    event_dict["request_id"] = request_id_var.get() or ""
    event_dict["org_id"] = org_id_var.get() or ""
    event_dict["user_id"] = user_id_var.get() or ""
    return event_dict


def redact_pii(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict
) -> EventDict:
    """Redact PII from log entry."""
    def _redact_value(key: str, value: Any) -> Any:
        key_lower = key.lower()
        
        # Check if key contains PII indicators
        if any(pii in key_lower for pii in PII_FIELDS):
            return "[REDACTED]"
        
        # Recursively redact nested dicts
        if isinstance(value, dict):
            return {k: _redact_value(k, v) for k, v in value.items()}
        
        # Redact lists of dicts
        if isinstance(value, list):
            return [
                _redact_value(f"{key}[{i}]", item) if isinstance(item, dict) else item
                for i, item in enumerate(value)
            ]
        
        return value
    
    # Redact event dict values
    for key in list(event_dict.keys()):
        if key not in ("event", "timestamp", "level", "logger"):
            event_dict[key] = _redact_value(key, event_dict[key])
    
    return event_dict


def setup_logging(
    environment: str = "development",
    log_level: str = "INFO",
    json_format: Optional[bool] = None
):
    """
    Setup structured logging.
    
    Args:
        environment: Environment name (development/production)
        log_level: Logging level
        json_format: Force JSON format (auto-detected if None)
    """
    # Auto-detect format
    if json_format is None:
        json_format = environment == "production"
    
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper())
    )
    
    # Shared processors
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        add_trace_id,
        add_request_context,
        redact_pii,
    ]
    
    if json_format:
        # Production: JSON output
        structlog.configure(
            processors=shared_processors + [
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.PositionalArgumentsFormatter(),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),
                structlog.processors.JSONRenderer()
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
    else:
        # Development: Pretty console output
        structlog.configure(
            processors=shared_processors + [
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.PositionalArgumentsFormatter(),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),
                structlog.dev.ConsoleRenderer(
                    colors=True,
                    sort_keys=True
                )
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
    
    # Suppress noisy loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    
    logger = structlog.get_logger("cassandra.logging")
    logger.info(
        "logging_configured",
        environment=environment,
        log_level=log_level,
        json_format=json_format
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structured logger."""
    return structlog.get_logger(name)


class LogContext:
    """
    Context manager for log context.
    
    Usage:
        with LogContext(trace_id="abc", org_id="org_123"):
            logger.info("message")  # Will include trace_id and org_id
    """
    
    def __init__(
        self,
        trace_id: Optional[str] = None,
        request_id: Optional[str] = None,
        org_id: Optional[str] = None,
        user_id: Optional[str] = None
    ):
        self.trace_id = trace_id or generate_trace_id()
        self.request_id = request_id or self.trace_id
        self.org_id = org_id or ""
        self.user_id = user_id or ""
        
        self._tokens = []
    
    def __enter__(self):
        """Enter context."""
        self._tokens = [
            trace_id_var.set(self.trace_id),
            request_id_var.set(self.request_id),
            org_id_var.set(self.org_id),
            user_id_var.set(self.user_id)
        ]
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context."""
        for token in self._tokens:
            token.var.reset(token)


def generate_trace_id() -> str:
    """Generate unique trace ID."""
    return str(uuid.uuid4())[:16]


def get_current_trace_id() -> str:
    """Get current trace ID from context."""
    return trace_id_var.get()


def set_trace_id(trace_id: str):
    """Set trace ID in context."""
    trace_id_var.set(trace_id)


def set_request_context(
    request_id: Optional[str] = None,
    org_id: Optional[str] = None,
    user_id: Optional[str] = None
):
    """Set request context variables."""
    if request_id:
        request_id_var.set(request_id)
    if org_id:
        org_id_var.set(org_id)
    if user_id:
        user_id_var.set(user_id)


# =============================================================================
# FastAPI Integration
# =============================================================================

from fastapi import Request

async def logging_middleware(request: Request, call_next):
    """
    FastAPI middleware for request logging.
    
    Adds trace_id and request context to all logs.
    """
    # Generate trace ID
    trace_id = generate_trace_id()
    
    # Extract context from request
    request_id = request.headers.get("X-Request-ID", trace_id)
    org_id = request.headers.get("X-Organization-ID", "")
    
    # Get user from auth (if available)
    user_id = ""
    if hasattr(request.state, "user"):
        user_id = request.state.user.get("user_id", "")
    
    # Set context
    with LogContext(
        trace_id=trace_id,
        request_id=request_id,
        org_id=org_id,
        user_id=user_id
    ):
        logger = get_logger("cassandra.api")
        
        # Log request
        logger.info(
            "request_started",
            method=request.method,
            path=request.url.path,
            client_ip=request.client.host if request.client else None
        )
        
        # Process request
        response = await call_next(request)
        
        # Log response
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code
        )
        
        # Add trace ID to response headers
        response.headers["X-Trace-ID"] = trace_id
        
        return response
