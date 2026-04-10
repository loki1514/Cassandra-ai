from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum
from datetime import datetime


# ── Existing Enums ──────────────────────────────────────────────

class ArtifactType(str, Enum):
    DECISION = "decision"
    RISK = "risk"
    TOPIC = "topic"
    SUMMARY = "summary"


# ── Session Models ──────────────────────────────────────────────

class SessionState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    DISCONNECTED = "disconnected"


class ClientType(str, Enum):
    WEB = "web"
    MOBILE = "mobile"
    UNKNOWN = "unknown"


class ProtocolVersion(str, Enum):
    LEGACY = "v1"
    SMART = "v2"


# ── API Key Models ──────────────────────────────────────────────

class UserRole(str, Enum):
    TENANT = "tenant"
    SUPER_TENANT = "super_tenant"
    ADMIN = "admin"
    ORG_SUPER_ADMIN = "org_super_admin"
    OWNER = "owner"
    MASTER_ADMIN = "master_admin"


class APIKeyCreate(BaseModel):
    org_id: str
    user_id: Optional[str] = None
    role: UserRole = UserRole.TENANT
    name: Optional[str] = None
    expires_at: Optional[datetime] = None


class APIKeyResponse(BaseModel):
    id: str
    key_prefix: str
    org_id: str
    role: str
    name: Optional[str] = None
    is_active: bool
    expires_at: Optional[datetime] = None
    created_at: datetime


# ── Session Models ──────────────────────────────────────────────

class SessionCreate(BaseModel):
    session_id: Optional[str] = None
    meeting_id: Optional[str] = None
    client_type: ClientType = ClientType.WEB
    protocol_version: ProtocolVersion = ProtocolVersion.SMART


class SessionResponse(BaseModel):
    session_id: str
    meeting_id: Optional[str] = None
    org_id: str
    user_id: Optional[str] = None
    state: SessionState
    protocol_version: str
    created_at: datetime
    duration_seconds: int = 0
    transcript_turns: int = 0
    connected_clients: int = 0


# ── Usage Models ────────────────────────────────────────────────

class VoiceUsageResponse(BaseModel):
    year: int
    month: int
    audio_seconds: int
    monthly_limit_seconds: int
    remaining_seconds: int
    is_exceeded: bool
    reset_at: datetime


# ── Insight Models ──────────────────────────────────────────────

class InsightCategory(str, Enum):
    DECISION = "decision"
    ACTION_ITEM = "action_item"
    RISK_FLAG = "risk_flag"
    CONTRADICTION = "contradiction"
    KEY_FACT = "key_fact"
    PATTERN = "pattern"
    BLIND_SPOT = "blind_spot"


# ── Original Models (preserved) ─────────────────────────────────

class ArtifactBase(BaseModel):
    artifact_type: ArtifactType
    content: str
    confidence: float
    meeting_id: Optional[str] = None


class MeetingResponse(BaseModel):
    id: str
    title: Optional[str] = None


class ProcessingResponse(BaseModel):
    summary: str = ""
    decisions: List[str] = []
    risks: List[str] = []
    topics: List[str] = []
    confidence: float = 0.0


class ChatRequest(BaseModel):
    query: str


class ChatResponse(BaseModel):
    answer: str
