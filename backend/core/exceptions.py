"""
backend/core/exceptions.py — Custom exceptions for the Cassandra Voice Server.
"""


class CassandraError(Exception):
    """Base exception for all Cassandra errors."""

    def __init__(self, message: str, session_id: str | None = None, **kwargs):
        super().__init__(message)
        self.message = message
        self.session_id = session_id
        self.extra = kwargs


# ── Authentication Errors ────────────────────────────────────────

class AuthError(CassandraError):
    """Base class for authentication failures."""
    pass


class InvalidAPIKeyError(AuthError):
    """The provided API key is invalid, expired, or revoked."""
    pass


class InvalidJWTTokenError(AuthError):
    """The provided JWT token is invalid or expired."""
    pass


class MissingAuthError(AuthError):
    """No authentication credentials were provided."""
    pass


# ── Rate Limiting Errors ────────────────────────────────────────

class RateLimitError(CassandraError):
    """Base class for rate limiting errors."""

    def __init__(
        self,
        message: str,
        session_id: str | None = None,
        remaining_seconds: int = 0,
        **kwargs,
    ):
        super().__init__(message, session_id, **kwargs)
        self.remaining_seconds = remaining_seconds


class MonthlyLimitExceededError(RateLimitError):
    """The user has exceeded their monthly voice usage limit."""
    pass


class SessionLimitExceededError(RateLimitError):
    """The maximum number of concurrent sessions has been reached."""
    pass


# ── Session Errors ──────────────────────────────────────────────

class SessionError(CassandraError):
    """Base class for session-related errors."""
    pass


class SessionNotFoundError(SessionError):
    """The requested session does not exist."""
    pass


class SessionAlreadyExistsError(SessionError):
    """A session with this ID already exists."""
    pass


class SessionExpiredError(SessionError):
    """The session has expired due to inactivity."""
    pass


class InvalidSessionStateError(SessionError):
    """The requested operation is not valid in the current session state."""
    pass


# ── Audio Pipeline Errors ───────────────────────────────────────

class AudioPipelineError(CassandraError):
    """Base class for audio pipeline errors."""
    pass


class AudioDecodeError(AudioPipelineError):
    """Failed to decode base64 audio data."""
    pass


class AudioBufferOverflowError(AudioPipelineError):
    """The audio buffer has exceeded its maximum size."""
    pass


class VADError(AudioPipelineError):
    """Error during voice activity detection."""
    pass


class STTError(AudioPipelineError):
    """Error during speech-to-text conversion."""
    pass


class TTSError(AudioPipelineError):
    """Error during text-to-speech conversion."""
    pass


# ── LLM Errors ──────────────────────────────────────────────────

class LLMError(CassandraError):
    """Base class for LLM errors."""
    pass


class LLMConnectionError(LLMError):
    """Failed to connect to the LLM provider."""
    pass


class LLMResponseError(LLMError):
    """The LLM returned an error response."""
    pass


class ToolExecutionError(CassandraError):
    """Error during tool execution."""

    def __init__(
        self,
        message: str,
        tool_name: str,
        session_id: str | None = None,
        **kwargs,
    ):
        super().__init__(message, session_id, **kwargs)
        self.tool_name = tool_name


# ── Provider Errors ─────────────────────────────────────────────

class ProviderError(CassandraError):
    """Base class for provider-related errors."""
    pass


class ProviderUnavailableError(ProviderError):
    """The requested AI provider is unavailable."""
    pass


class ProviderCircuitOpenError(ProviderError):
    """Circuit breaker is open for this provider."""
    pass


# ── Protocol Errors ─────────────────────────────────────────────

class ProtocolError(CassandraError):
    """Error in the WebSocket protocol."""
    pass


class UnknownMessageTypeError(ProtocolError):
    """Received an unknown or malformed WebSocket message type."""
    pass


class ProtocolVersionMismatchError(ProtocolError):
    """Client and server protocol versions are incompatible."""
    pass
