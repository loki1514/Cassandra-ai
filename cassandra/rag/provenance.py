"""
Provenance Module (T39)

Provides source metadata, attribution, and confidence display for UI responses.
This module enables transparent data lineage tracking for all RAG-generated
responses, showing users exactly where information came from.

Features:
- Source metadata for responses
- Meeting attribution
- Confidence display
- Ledger version history
- Full audit trail for UI
"""

import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SourceType(str, Enum):
    """Types of sources for provenance tracking."""
    MEMORY_ARCHIVE = "memory_archive"       # Vector search from memory
    DB1_TICKET = "db1_ticket"               # Authoritative ticket data
    DB1_USER = "db1_user"                   # User profile data
    TRANSCRIPT = "transcript"               # Meeting transcript
    TRUTH_LEDGER = "truth_ledger"           # Ground truth event
    KNOWLEDGE_BASE = "knowledge_base"       # Knowledge base article
    CONVERSATION = "conversation"           # Previous conversation
    DECISION = "decision"                   # Recorded decision


class ConfidenceLevel(str, Enum):
    """Confidence levels for UI display."""
    HIGH = "high"           # >= 0.9
    GOOD = "good"           # 0.7 - 0.89
    MEDIUM = "medium"       # 0.5 - 0.69
    LOW = "low"             # 0.3 - 0.49
    UNCERTAIN = "uncertain" # < 0.3


@dataclass
class SourceAttribution:
    """
    Attribution information for a single source.
    
    Tracks where a piece of information came from, including
    meeting details, speaker information, and timestamps.
    """
    source_type: SourceType
    source_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    
    # Meeting-specific attribution
    meeting_id: Optional[str] = None
    meeting_title: Optional[str] = None
    meeting_date: Optional[datetime] = None
    speaker_id: Optional[str] = None
    speaker_name: Optional[str] = None
    
    # Context
    excerpt: Optional[str] = None  # Relevant excerpt from source
    timestamp: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for UI."""
        return {
            "source_type": self.source_type.value,
            "source_id": self.source_id,
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "meeting": {
                "id": self.meeting_id,
                "title": self.meeting_title,
                "date": self.meeting_date.isoformat() if self.meeting_date else None
            } if self.meeting_id else None,
            "speaker": {
                "id": self.speaker_id,
                "name": self.speaker_name
            } if self.speaker_id else None,
            "excerpt": self.excerpt,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }


@dataclass
class ConfidenceDisplay:
    """
    Confidence information for UI display.
    
    Provides both numeric and human-readable confidence
    information with appropriate styling hints.
    """
    score: float  # 0.0 - 1.0
    level: ConfidenceLevel
    label: str
    description: str
    color: str  # CSS color for UI
    icon: Optional[str] = None  # Icon name for UI
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for UI."""
        return {
            "score": round(self.score, 2),
            "level": self.level.value,
            "label": self.label,
            "description": self.description,
            "color": self.color,
            "icon": self.icon
        }
    
    @classmethod
    def from_score(cls, score: float) -> 'ConfidenceDisplay':
        """Create confidence display from numeric score."""
        if score >= 0.9:
            return cls(
                score=score,
                level=ConfidenceLevel.HIGH,
                label="High Confidence",
                description="This information is highly reliable.",
                color="#22c55e",  # green-500
                icon="check-circle"
            )
        elif score >= 0.7:
            return cls(
                score=score,
                level=ConfidenceLevel.GOOD,
                label="Good Confidence",
                description="This information is reliable.",
                color="#84cc16",  # lime-500
                icon="check"
            )
        elif score >= 0.5:
            return cls(
                score=score,
                level=ConfidenceLevel.MEDIUM,
                label="Medium Confidence",
                description="This information may need verification.",
                color="#eab308",  # yellow-500
                icon="alert-circle"
            )
        elif score >= 0.3:
            return cls(
                score=score,
                level=ConfidenceLevel.LOW,
                label="Low Confidence",
                description="This information should be verified.",
                color="#f97316",  # orange-500
                icon="alert-triangle"
            )
        else:
            return cls(
                score=score,
                level=ConfidenceLevel.UNCERTAIN,
                label="Uncertain",
                description="This information is unreliable.",
                color="#ef4444",  # red-500
                icon="x-circle"
            )


@dataclass
class LedgerVersion:
    """
    A version entry from the Truth Ledger.
    
    Represents a single version of ground truth data
    with full audit information.
    """
    version_id: str
    event_id: str
    entity_type: str
    entity_id: str
    action: str
    timestamp: datetime
    confidence: float
    source: str
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    changes: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for UI."""
        return {
            "version_id": self.version_id,
            "event_id": self.event_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "action": self.action,
            "timestamp": self.timestamp.isoformat(),
            "confidence": round(self.confidence, 2),
            "source": self.source,
            "user": {
                "id": self.user_id,
                "name": self.user_name
            } if self.user_id else None,
            "changes": self.changes
        }


@dataclass
class ProvenanceInfo:
    """
    T39: Complete provenance information for a UI response.
    
    This is the main data structure returned to the UI,
    containing all metadata about the sources and confidence
    of the information presented.
    """
    response_id: str
    query: str
    generated_at: datetime
    
    # Sources
    sources: List[SourceAttribution] = field(default_factory=list)
    primary_source: Optional[SourceAttribution] = None
    
    # Confidence
    confidence: Optional[ConfidenceDisplay] = None
    overall_confidence: float = 0.0
    
    # Ledger history
    ledger_versions: List[LedgerVersion] = field(default_factory=list)
    
    # Metadata
    processing_time_ms: float = 0.0
    total_sources_consulted: int = 0
    sources_used: int = 0
    
    # Audit
    org_id: Optional[str] = None
    user_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for UI response."""
        return {
            "response_id": self.response_id,
            "query": self.query,
            "generated_at": self.generated_at.isoformat(),
            "sources": [s.to_dict() for s in self.sources],
            "primary_source": self.primary_source.to_dict() if self.primary_source else None,
            "confidence": self.confidence.to_dict() if self.confidence else None,
            "overall_confidence": round(self.overall_confidence, 2),
            "ledger_versions": [v.to_dict() for v in self.ledger_versions],
            "processing_time_ms": round(self.processing_time_ms, 2),
            "total_sources_consulted": self.total_sources_consulted,
            "sources_used": self.sources_used,
            "audit": {
                "org_id": self.org_id,
                "user_id": self.user_id
            }
        }
    
    def to_ui_format(self) -> Dict[str, Any]:
        """
        Convert to simplified UI format.
        
        Returns a format optimized for frontend display.
        """
        return {
            "confidence": self.confidence.to_dict() if self.confidence else None,
            "sources": [
                {
                    "type": s.source_type.value,
                    "title": s.title or s.source_id,
                    "meeting": s.meeting_title,
                    "speaker": s.speaker_name,
                    "excerpt": s.excerpt,
                    "date": s.timestamp.isoformat() if s.timestamp else None
                }
                for s in self.sources[:3]  # Top 3 sources
            ],
            "version_history": [
                {
                    "action": v.action,
                    "date": v.timestamp.isoformat(),
                    "user": v.user_name or v.user_id,
                    "confidence": round(v.confidence, 2)
                }
                for v in self.ledger_versions[:5]  # Last 5 versions
            ],
            "metadata": {
                "sources_count": self.sources_used,
                "processing_time_ms": round(self.processing_time_ms, 2)
            }
        }


class ProvenanceConfig(BaseModel):
    """Configuration for provenance tracking."""
    max_sources_display: int = Field(default=5, ge=1, le=20)
    max_ledger_versions: int = Field(default=10, ge=1, le=100)
    include_excerpts: bool = Field(default=True)
    excerpt_max_length: int = Field(default=200, ge=50, le=1000)
    enable_meeting_attribution: bool = Field(default=True)


class ProvenanceTracker:
    """
    T39: Provenance tracker for UI data.
    
    The ProvenanceTracker builds comprehensive provenance information
    for RAG responses, enabling transparent source attribution in the UI.
    
    Usage:
        tracker = ProvenanceTracker(config)
        
        # Build provenance for a response
        provenance = await tracker.build_provenance(
            response_id="resp_123",
            query="What was decided?",
            context_items=context_items,
            org_id="org_456"
        )
        
        # Send to UI
        ui_data = provenance.to_ui_format()
    """
    
    def __init__(
        self,
        db_pool: Any,
        config: Optional[ProvenanceConfig] = None
    ):
        """
        Initialize the provenance tracker.
        
        Args:
            db_pool: Database connection pool
            config: Provenance configuration
        """
        self.db_pool = db_pool
        self.config = config or ProvenanceConfig()
        logger.info("ProvenanceTracker initialized")
    
    async def build_provenance(
        self,
        response_id: str,
        query: str,
        context_items: List[Dict[str, Any]],
        org_id: str,
        user_id: Optional[str] = None,
        processing_time_ms: float = 0.0
    ) -> ProvenanceInfo:
        """
        T39: Build complete provenance information for a response.
        
        Args:
            response_id: Unique response identifier
            query: Original user query
            context_items: Context items used in the response
            org_id: Organization scope
            user_id: Optional user ID
            processing_time_ms: Time taken to generate response
            
        Returns:
            ProvenanceInfo with full source metadata
        """
        logger.debug(f"T39: Building provenance for response {response_id}")
        
        provenance = ProvenanceInfo(
            response_id=response_id,
            query=query,
            generated_at=datetime.utcnow(),
            org_id=org_id,
            user_id=user_id,
            processing_time_ms=processing_time_ms,
            total_sources_consulted=len(context_items)
        )
        
        # Build source attributions
        sources = []
        confidences = []
        
        for item in context_items:
            source = await self._build_source_attribution(item, org_id)
            if source:
                sources.append(source)
                
                # Track confidence
                item_confidence = item.get("confidence", 0.5)
                confidences.append(item_confidence)
        
        provenance.sources = sources[:self.config.max_sources_display]
        provenance.sources_used = len(sources)
        
        if sources:
            provenance.primary_source = sources[0]
        
        # Calculate overall confidence
        if confidences:
            provenance.overall_confidence = sum(confidences) / len(confidences)
            provenance.confidence = ConfidenceDisplay.from_score(
                provenance.overall_confidence
            )
        
        # Fetch ledger versions if applicable
        provenance.ledger_versions = await self._fetch_ledger_versions(
            context_items, org_id
        )
        
        logger.debug(
            f"T39: Provenance built for {response_id} | "
            f"Sources: {len(sources)}, Confidence: {provenance.overall_confidence:.2f}"
        )
        
        return provenance
    
    async def _build_source_attribution(
        self,
        context_item: Dict[str, Any],
        org_id: str
    ) -> Optional[SourceAttribution]:
        """Build source attribution from a context item."""
        source_type_str = context_item.get("source", "memory_archive")
        
        try:
            source_type = SourceType(source_type_str)
        except ValueError:
            source_type = SourceType.MEMORY_ARCHIVE
        
        attribution = SourceAttribution(
            source_type=source_type,
            source_id=context_item.get("memory_id") or context_item.get("ticket_id", "unknown"),
            timestamp=datetime.utcnow()
        )
        
        # Add excerpt if enabled
        if self.config.include_excerpts:
            content = context_item.get("content", "")
            if isinstance(content, dict):
                content = content.get("text", str(content))
            attribution.excerpt = self._truncate_excerpt(str(content))
        
        # Fetch meeting attribution if applicable
        if source_type == SourceType.TRANSCRIPT and self.config.enable_meeting_attribution:
            meeting_info = await self._fetch_meeting_info(
                context_item.get("transcript_id"),
                org_id
            )
            if meeting_info:
                attribution.meeting_id = meeting_info.get("meeting_id")
                attribution.meeting_title = meeting_info.get("title")
                attribution.meeting_date = meeting_info.get("date")
                attribution.speaker_id = meeting_info.get("speaker_id")
                attribution.speaker_name = meeting_info.get("speaker_name")
        
        # Fetch ticket info for DB1 sources
        if source_type == SourceType.DB1_TICKET:
            ticket_info = await self._fetch_ticket_info(
                context_item.get("ticket_id"),
                org_id
            )
            if ticket_info:
                attribution.title = ticket_info.get("title")
                attribution.description = ticket_info.get("description")
        
        return attribution
    
    async def _fetch_meeting_info(
        self,
        transcript_id: Optional[str],
        org_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch meeting information for a transcript."""
        if not transcript_id:
            return None
        
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT 
                        m.id as meeting_id,
                        m.title,
                        m.meeting_date,
                        t.speaker_id,
                        u.name as speaker_name
                    FROM transcripts t
                    JOIN meetings m ON t.meeting_id = m.id
                    LEFT JOIN users u ON t.speaker_id = u.id
                    WHERE t.id = $1 AND m.org_id = $2
                    """,
                    transcript_id, org_id
                )
                
                if row:
                    return {
                        "meeting_id": row["meeting_id"],
                        "title": row["title"],
                        "date": row["meeting_date"],
                        "speaker_id": row["speaker_id"],
                        "speaker_name": row["speaker_name"]
                    }
        except Exception as e:
            logger.warning(f"Failed to fetch meeting info: {e}")
        
        return None
    
    async def _fetch_ticket_info(
        self,
        ticket_id: Optional[str],
        org_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch ticket information."""
        if not ticket_id:
            return None
        
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT title, description
                    FROM tickets
                    WHERE id = $1 AND org_id = $2
                    """,
                    ticket_id, org_id
                )
                
                if row:
                    return {
                        "title": row["title"],
                        "description": row["description"]
                    }
        except Exception as e:
            logger.warning(f"Failed to fetch ticket info: {e}")
        
        return None
    
    async def _fetch_ledger_versions(
        self,
        context_items: List[Dict[str, Any]],
        org_id: str
    ) -> List[LedgerVersion]:
        """Fetch ledger version history for context items."""
        versions = []
        
        # Collect entity IDs from context
        entity_ids = set()
        for item in context_items:
            ticket_id = item.get("ticket_id")
            if ticket_id:
                entity_ids.add(ticket_id)
        
        if not entity_ids:
            return versions
        
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT 
                        event_id,
                        entity_type,
                        entity_id,
                        action,
                        timestamp,
                        confidence,
                        source,
                        user_id,
                        data
                    FROM truth_ledger
                    WHERE org_id = $1 AND entity_id = ANY($2)
                    ORDER BY timestamp DESC
                    LIMIT $3
                    """,
                    org_id, list(entity_ids), self.config.max_ledger_versions
                )
                
                for row in rows:
                    version = LedgerVersion(
                        version_id=f"ver_{row['event_id']}",
                        event_id=row["event_id"],
                        entity_type=row["entity_type"],
                        entity_id=row["entity_id"],
                        action=row["action"],
                        timestamp=row["timestamp"],
                        confidence=row["confidence"],
                        source=row["source"],
                        user_id=row["user_id"],
                        changes=row["data"]
                    )
                    versions.append(version)
                    
        except Exception as e:
            logger.warning(f"Failed to fetch ledger versions: {e}")
        
        return versions
    
    def _truncate_excerpt(self, text: str) -> str:
        """Truncate excerpt to configured length."""
        max_len = self.config.excerpt_max_length
        if len(text) <= max_len:
            return text
        return text[:max_len - 3] + "..."


# Convenience functions

def create_source_attribution(
    source_type: SourceType,
    source_id: str,
    title: Optional[str] = None,
    meeting_id: Optional[str] = None,
    meeting_title: Optional[str] = None,
    speaker_name: Optional[str] = None,
    excerpt: Optional[str] = None
) -> SourceAttribution:
    """
    Create a source attribution.
    
    Args:
        source_type: Type of source
        source_id: Source identifier
        title: Source title
        meeting_id: Meeting identifier
        meeting_title: Meeting title
        speaker_name: Speaker name
        excerpt: Relevant excerpt
        
    Returns:
        SourceAttribution
    """
    return SourceAttribution(
        source_type=source_type,
        source_id=source_id,
        title=title,
        meeting_id=meeting_id,
        meeting_title=meeting_title,
        speaker_name=speaker_name,
        excerpt=excerpt,
        timestamp=datetime.utcnow()
    )


def get_confidence_display(score: float) -> ConfidenceDisplay:
    """
    Get confidence display for a score.
    
    Args:
        score: Confidence score (0.0 - 1.0)
        
    Returns:
        ConfidenceDisplay for UI
    """
    return ConfidenceDisplay.from_score(score)


async def build_response_provenance(
    db_pool: Any,
    response_id: str,
    query: str,
    context_items: List[Dict[str, Any]],
    org_id: str,
    user_id: Optional[str] = None,
    processing_time_ms: float = 0.0
) -> ProvenanceInfo:
    """
    T39: Convenience function to build provenance for a response.
    
    Args:
        db_pool: Database connection pool
        response_id: Response identifier
        query: Original query
        context_items: Context items used
        org_id: Organization scope
        user_id: Optional user ID
        processing_time_ms: Processing time
        
    Returns:
        ProvenanceInfo
    """
    tracker = ProvenanceTracker(db_pool)
    return await tracker.build_provenance(
        response_id=response_id,
        query=query,
        context_items=context_items,
        org_id=org_id,
        user_id=user_id,
        processing_time_ms=processing_time_ms
    )
