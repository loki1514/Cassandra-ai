"""
backend/utils/logging_config.py — Structured logging configuration for Cassandra Voice Server.

Provides structured JSON logging with session context (session_id, org_id, event_type).
Uses structlog for consistent, queryable log output in production.
"""

import logging
import sys
from datetime import datetime
from typing import Any

from backend.config import get_settings


def get_log_level() -> str:
    return get_settings().log_level


def setup_logging() -> None:
    """
    Configure the root logger with structured output.

    - In production (log_format=json): JSON lines with structured fields
    - In development (log_format=text): Human-readable format
    """
    settings = get_settings()

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    if settings.log_format == "json":
        _setup_json_logging(root_logger)
    else:
        _setup_text_logging(root_logger)


def _setup_json_logging(logger: logging.Logger) -> None:
    """Set up JSON structured logging."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    # Format: standard JSON — let structlog handle it
    formatter = logging.Formatter(
        fmt="%(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)


def _setup_text_logging(logger: logging.Logger) -> None:
    """Set up human-readable text logging."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class SessionLogger:
    """
    A logger wrapper that includes session context in every log message.

    Usage:
        logger = SessionLogger(session_id="abc", org_id="org1")
        logger.info("audio_chunk_received", bytes=1024)
    """

    def __init__(
        self,
        session_id: str | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
        logger_name: str = "cassandra",
    ):
        self._session_id = session_id
        self._org_id = org_id
        self._user_id = user_id
        self._logger = logging.getLogger(logger_name)

    def _build_ctx(self, **kwargs: Any) -> dict[str, Any]:
        """Build context dict for structured logging."""
        ctx = dict(kwargs)
        if self._session_id:
            ctx["session_id"] = self._session_id
        if self._org_id:
            ctx["org_id"] = self._org_id
        if self._user_id:
            ctx["user_id"] = self._user_id
        ctx["timestamp"] = datetime.utcnow().isoformat() + "Z"
        return ctx

    def debug(self, event: str, **kwargs: Any) -> None:
        self._logger.debug(event, extra=self._build_ctx(event=event, **kwargs))

    def info(self, event: str, **kwargs: Any) -> None:
        self._logger.info(event, extra=self._build_ctx(event=event, **kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self._logger.warning(event, extra=self._build_ctx(event=event, **kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self._logger.error(event, extra=self._build_ctx(event=event, **kwargs))

    def critical(self, event: str, **kwargs: Any) -> None:
        self._logger.critical(event, extra=self._build_ctx(event=event, **kwargs))

    def exception(self, event: str, **kwargs: Any) -> None:
        self._logger.exception(event, extra=self._build_ctx(event=event, **kwargs))

    def with_context(self, **kwargs: Any) -> "SessionLogger":
        """Return a new SessionLogger with additional context."""
        new = SessionLogger(
            session_id=self._session_id,
            org_id=self._org_id,
            user_id=self._user_id,
            logger_name=self._logger.name,
        )
        new._session_id = self._session_id
        new._org_id = self._org_id
        new._user_id = self._user_id
        return new


def get_logger(name: str = "cassandra") -> logging.Logger:
    """Get a named logger instance."""
    return logging.getLogger(name)
