"""
backend/schemas/messages.py — Pydantic schemas for WebSocket messages.

Defines the message schemas for both V1 (legacy) and V2 (smart) protocols.
Used for validation of incoming messages and serialization of outgoing messages.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal, NotRequired

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────

class SessionState(str, Enum):
    """Session state machine states."""

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    DISCONNECTED = "disconnected"


class ClientType(str, Enum):
    """Type of client connecting to the server."""

    WEB = "web"
    MOBILE = "mobile"
    UNKNOWN = "unknown"


class TranscriptSpeaker(str, Enum):
    """Speaker label for transcript messages."""

    USER = "user"
    AI = "ai"
    SYSTEM = "system"


class InsightCategory(str, Enum):
    """Category for detected insights."""

    DECISION = "decision"
    ACTION_ITEM = "action_item"
    RISK_FLAG = "risk_flag"
    CONTRADICTION = "contradiction"
    KEY_FACT = "key_fact"
    PATTERN = "pattern"
    BLIND_SPOT = "blind_spot"


class MessageDirection(str, Enum):
    """Direction of a message (server -> client or client -> server)."""

    SERVER_TO_CLIENT = "server_to_client"
    CLIENT_TO_SERVER = "client_to_server"


# ── V1 (Legacy) Incoming Messages ──────────────────────────────

class V1InputAudio(BaseModel):
    """Client -> Server: Audio chunk from microphone."""

    type: Literal["input_audio"] = "input_audio"
    audio: str = Field(..., description="base64-encoded PCM16 audio")


class V1Ping(BaseModel):
    """Client -> Server: Heartbeat ping."""

    type: Literal["ping"] = "ping"


class V1SwitchRole(BaseModel):
    """Client -> Server: Switch agent role."""

    type: Literal["switch_role"] = "switch_role"
    role: str = Field(..., description="Role identifier (e.g., 'GENERAL', 'MARKETING')")


# ── V2 Incoming Messages ───────────────────────────────────────

class V2SessionStart(BaseModel):
    """Client -> Server: Start a new session (V2 protocol)."""

    type: Literal["session_start"] = "session_start"
    api_key: NotRequired[str] = Field(default=None, description="API key for authentication")
    token: NotRequired[str] = Field(default=None, description="Supabase JWT token")
    client_type: NotRequired[ClientType] = Field(default=ClientType.WEB)
    session_id: NotRequired[str] = Field(default=None, description="Optional session ID (server generates if omitted)")
    meeting_id: NotRequired[str] = Field(default=None, description="Associated meeting ID")
    role: NotRequired[str] = Field(default="GENERAL", description="Initial agent role")


class V2SessionEnd(BaseModel):
    """Client -> Server: Explicitly end the session."""

    type: Literal["session_end"] = "session_end"
    reason: NotRequired[str] = None


class V2Interrupt(BaseModel):
    """Client -> Server: User interrupted the AI response."""

    type: Literal["interrupt"] = "interrupt"


class V2RoleUpdate(BaseModel):
    """Client -> Server: Update the agent role mid-session."""

    type: Literal["role_update"] = "role_update"
    role: str


class V2ContextInject(BaseModel):
    """Client -> Server: Request context injection from institutional memory."""

    type: Literal["context_inject"] = "context_inject"
    query: str = Field(..., description="Semantic search query")


# ── Outgoing Messages (Server -> Client) ──────────────────────

class OutConnected(BaseModel):
    """Server -> Client: Session established and ready."""

    type: Literal["connected"] = "connected"
    session_id: str
    protocol_version: str = "v2"


class OutStateChange(BaseModel):
    """Server -> Client: Session state changed."""

    type: Literal["state_change"] = "state_change"
    state: SessionState
    previous_state: SessionState | None = None


class OutAudio(BaseModel):
    """Server -> Client: AI speech audio chunk."""

    type: Literal["audio"] = "audio"
    audio: str = Field(..., description="base64-encoded PCM16 audio")
    is_final: bool = False


class OutTranscript(BaseModel):
    """Server -> Client: Transcript text (streaming or final)."""

    type: Literal["transcript"] = "transcript"
    speaker: TranscriptSpeaker
    text: str
    is_delta: bool = False


class OutInsight(BaseModel):
    """Server -> Client: Detected insight from the conversation."""

    type: Literal["insight"] = "insight"
    insight: str
    category: InsightCategory
    confidence: Literal["high", "medium", "low"] = "medium"
    owner: str = ""


class OutInterrupt(BaseModel):
    """Server -> Client: AI detected user speech (barge-in signal)."""

    type: Literal["interrupt"] = "interrupt"


class OutRoleSwitched(BaseModel):
    """Server -> Client: Role switch confirmed."""

    type: Literal["role_switched"] = "role_switched"
    role: str


class OutPong(BaseModel):
    """Server -> Client: Response to heartbeat."""

    type: Literal["pong"] = "pong"


class OutError(BaseModel):
    """Server -> Client: Error occurred."""

    type: Literal["error"] = "error"
    message: str
    code: str = "unknown"


class OutRateLimitExceeded(BaseModel):
    """Server -> Client: Monthly usage limit reached."""

    type: Literal["rate_limit_exceeded"] = "rate_limit_exceeded"
    message: str
    reset_at: str = Field(..., description="ISO timestamp when limit resets")


class OutMeetingEnded(BaseModel):
    """Server -> Client: Session has ended."""

    type: Literal["meeting_ended"] = "meeting_ended"
    reason: str = "normal"
    session_id: str


class OutMemoryMatch(BaseModel):
    """Server -> Client: Retrieved context from institutional memory."""

    type: Literal["memory_match"] = "memory_match"
    text: str
    source: str = "supermemory"
    relevance_score: float = 0.0


# ── Generic Message Wrappers ────────────────────────────────────

class AnyIncomingMessage(BaseModel):
    """
    Flexible schema that accepts any valid incoming message.
    Used for initial protocol detection before routing to specific schemas.
    """

    type: str
    # Preserve all other fields
    model_config: dict[str, Any] = {"extra": "allow"}


class AnyOutgoingMessage(BaseModel):
    """Generic outgoing message wrapper."""

    type: str
    model_config: dict[str, Any] = {"extra": "allow"}
