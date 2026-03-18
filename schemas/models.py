from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum

class ArtifactType(str, Enum):
    DECISION = "decision"
    RISK = "risk"
    TOPIC = "topic"
    SUMMARY = "summary"

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
    # tts_audio_url or base64 could be added if returning stream or string
