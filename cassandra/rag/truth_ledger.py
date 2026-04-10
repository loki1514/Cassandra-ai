"""
Truth Ledger Module

Implements the ground truth event tracking system for Cassandra AI.
The Truth Ledger maintains a verifiable, auditable record of all
critical decisions and entity changes with confidence scoring.

Key Features:
- Immutable event logging for ground truth entities
- Confidence scoring (0.0 - 1.0)
- Human review queue for uncertain events (0.5-0.7 confidence)
- Entity type classification (DECISION, OWNER, BUDGET)
- Cryptographic verification support
- Full audit trail

Architecture:
- Events are append-only (immutable)
- Each event has a unique hash for verification
- Confidence scores determine review requirements
- Events link to previous events for chain integrity
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field, asdict
from pydantic import BaseModel, Field, validator, root_validator

logger = logging.getLogger(__name__)


class EntityType(str, Enum):
    """
    Types of entities tracked in the Truth Ledger.
    
    These represent the core ground truth entities that require
    high confidence and potential human verification.
    """
    DECISION = "decision"           # AI or human decisions
    OWNER = "owner"                 # Ownership assignments
    BUDGET = "budget"               # Budget allocations/changes
    PRIORITY = "priority"           # Priority changes
    STATUS = "status"               # Status transitions
    ASSIGNMENT = "assignment"       # Task/assignment changes
    CONFIGURATION = "configuration" # System configuration changes
    POLICY = "policy"               # Policy/rule changes


class ConfidenceLevel(float, Enum):
    """
    Confidence level thresholds for truth events.
    
    These thresholds determine routing and review requirements.
    """
    CERTAIN = 0.9           # No review needed
    HIGH = 0.8              # Automatic processing
    MEDIUM = 0.7            # Borderline - may need review
    UNCERTAIN = 0.5         # Requires human review
    LOW = 0.3               # Likely incorrect
    REJECT = 0.0            # Automatic rejection


class ReviewStatus(str, Enum):
    """Status of human review for uncertain events."""
    NOT_REQUIRED = "not_required"   # Confidence >= 0.7
    PENDING = "pending"             # In review queue
    APPROVED = "approved"           # Reviewer approved
    REJECTED = "rejected"           # Reviewer rejected
    ESCALATED = "escalated"         # Escalated to senior reviewer
    AUTO_APPROVED = "auto_approved" # Auto-approved after timeout


class VerificationStatus(str, Enum):
    """Cryptographic verification status."""
    UNVERIFIED = "unverified"       # Not yet verified
    VERIFIED = "verified"           # Hash chain verified
    CORRUPTED = "corrupted"         # Hash mismatch detected
    REPAIRED = "repaired"           # Corruption repaired


@dataclass
class TruthEvent:
    """
    A single ground truth event in the ledger.
    
    TruthEvents are immutable records of critical system events
    with confidence scoring and verification support.
    
    Attributes:
        event_id: Unique event identifier (UUID)
        entity_type: Type of entity being tracked
        entity_id: Identifier for the specific entity
        org_id: Organization scope
        action: The action that occurred (e.g., "created", "updated")
        data: Event payload/data
        confidence: Confidence score (0.0 - 1.0)
        source: Source of the event (e.g., "ai", "user", "system")
        user_id: Optional user who triggered the event
        previous_hash: Hash of previous event (for chain integrity)
        event_hash: Hash of this event (for verification)
        timestamp: When the event occurred
        review_status: Human review status
        reviewed_by: User who reviewed (if applicable)
        reviewed_at: When review occurred
        review_notes: Notes from reviewer
        verification_status: Cryptographic verification status
        metadata: Additional structured data
    """
    event_id: str
    entity_type: EntityType
    entity_id: str
    org_id: str
    action: str
    data: Dict[str, Any]
    confidence: float
    source: str
    user_id: Optional[str] = None
    previous_hash: Optional[str] = None
    event_hash: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    review_status: ReviewStatus = ReviewStatus.NOT_REQUIRED
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Generate event hash if not provided."""
        if self.event_hash is None:
            self.event_hash = self._calculate_hash()
    
    def _calculate_hash(self) -> str:
        """Calculate cryptographic hash of event data."""
        # Create deterministic representation
        hash_data = {
            "event_id": self.event_id,
            "entity_type": self.entity_type.value,
            "entity_id": self.entity_id,
            "org_id": self.org_id,
            "action": self.action,
            "data": self._normalize_data(self.data),
            "confidence": float(self.confidence),
            "source": self.source,
            "user_id": self.user_id,
            "previous_hash": self.previous_hash,
            "timestamp": self.timestamp.isoformat()
        }
        
        # Generate hash
        hash_input = json.dumps(hash_data, sort_keys=True, default=str)
        return hashlib.sha256(hash_input.encode()).hexdigest()
    
    def _normalize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize data for consistent hashing."""
        normalized = {}
        for key, value in sorted(data.items()):
            if isinstance(value, Decimal):
                normalized[key] = float(value)
            elif isinstance(value, datetime):
                normalized[key] = value.isoformat()
            elif isinstance(value, dict):
                normalized[key] = self._normalize_data(value)
            elif isinstance(value, list):
                normalized[key] = [self._normalize_value(v) for v in value]
            else:
                normalized[key] = value
        return normalized
    
    def _normalize_value(self, value: Any) -> Any:
        """Normalize a single value."""
        if isinstance(value, Decimal):
            return float(value)
        elif isinstance(value, datetime):
            return value.isoformat()
        elif isinstance(value, dict):
            return self._normalize_data(value)
        return value
    
    def verify(self) -> bool:
        """Verify event integrity by recalculating hash."""
        calculated = self._calculate_hash()
        return calculated == self.event_hash
    
    def requires_review(self) -> bool:
        """Check if this event requires human review."""
        return 0.5 <= self.confidence < 0.7
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "event_id": self.event_id,
            "entity_type": self.entity_type.value,
            "entity_id": self.entity_id,
            "org_id": self.org_id,
            "action": self.action,
            "data": self.data,
            "confidence": self.confidence,
            "source": self.source,
            "user_id": self.user_id,
            "previous_hash": self.previous_hash,
            "event_hash": self.event_hash,
            "timestamp": self.timestamp.isoformat(),
            "review_status": self.review_status.value,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "review_notes": self.review_notes,
            "verification_status": self.verification_status.value,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TruthEvent':
        """Create from dictionary."""
        return cls(
            event_id=data["event_id"],
            entity_type=EntityType(data["entity_type"]),
            entity_id=data["entity_id"],
            org_id=data["org_id"],
            action=data["action"],
            data=data["data"],
            confidence=data["confidence"],
            source=data["source"],
            user_id=data.get("user_id"),
            previous_hash=data.get("previous_hash"),
            event_hash=data.get("event_hash"),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            review_status=ReviewStatus(data.get("review_status", "not_required")),
            reviewed_by=data.get("reviewed_by"),
            reviewed_at=datetime.fromisoformat(data["reviewed_at"]) if data.get("reviewed_at") else None,
            review_notes=data.get("review_notes"),
            verification_status=VerificationStatus(data.get("verification_status", "unverified")),
            metadata=data.get("metadata", {})
        )


class TruthLedgerConfig(BaseModel):
    """Configuration for the Truth Ledger."""
    review_queue_threshold_low: float = Field(default=0.5, ge=0.0, le=1.0)
    review_queue_threshold_high: float = Field(default=0.7, ge=0.0, le=1.0)
    auto_approve_timeout_hours: int = Field(default=24, ge=1, le=168)
    enable_verification: bool = Field(default=True)
    retention_days: int = Field(default=365, ge=30)
    
    @root_validator
    def validate_thresholds(cls, values):
        """Ensure threshold_low < threshold_high."""
        low = values.get('review_queue_threshold_low')
        high = values.get('review_queue_threshold_high')
        if low >= high:
            raise ValueError("review_queue_threshold_low must be less than review_queue_threshold_high")
        return values
    
    class Config:
        env_prefix = "TRUTH_LEDGER_"


class ReviewQueueEntry(BaseModel):
    """Entry in the human review queue."""
    event_id: str
    entity_type: EntityType
    entity_id: str
    org_id: str
    confidence: float
    submitted_at: datetime
    priority: int = Field(default=5, ge=1, le=10)
    status: ReviewStatus = ReviewStatus.PENDING
    
    class Config:
        schema_extra = {
            "example": {
                "event_id": "evt_12345",
                "entity_type": "decision",
                "entity_id": "DEC-789",
                "org_id": "org_123",
                "confidence": 0.65,
                "submitted_at": "2024-01-15T10:30:00Z",
                "priority": 5
            }
        }


class TruthLedger:
    """
    Ground truth event ledger for Cassandra AI.
    
    The Truth Ledger maintains an immutable, verifiable record of
critical system events with confidence scoring and human review
    capabilities.
    
    Key Responsibilities:
    - Record ground truth events with confidence scores
    - Route uncertain events (0.5-0.7 confidence) to human review
    - Maintain hash chain for verification
    - Provide audit trail for compliance
    
    Usage:
        ledger = TruthLedger(db_pool, config)
        
        # Record an event
        event = await ledger.record_event(
            entity_type=EntityType.DECISION,
            entity_id="DEC-123",
            org_id="org_456",
            action="created",
            data={"decision": "assign_to_team_b"},
            confidence=0.85,
            source="ai"
        )
        
        # Check if review needed
        if event.requires_review():
            await ledger.submit_for_review(event)
        
        # Review an event
        await ledger.review_event(
            event_id=event.event_id,
            reviewer_id="user_123",
            decision="approved",
            notes="Looks correct"
        )
    """
    
    def __init__(
        self,
        db_pool: Any,
        config: Optional[TruthLedgerConfig] = None,
        notification_client: Optional[Any] = None
    ):
        """
        Initialize the Truth Ledger.
        
        Args:
            db_pool: Database connection pool
            config: Ledger configuration
            notification_client: Optional client for review notifications
        """
        self.db_pool = db_pool
        self.config = config or TruthLedgerConfig()
        self.notification_client = notification_client
        self._last_hash_cache: Dict[str, str] = {}  # org_id -> last_hash
        logger.info("TruthLedger initialized")
    
    async def record_event(
        self,
        entity_type: EntityType,
        entity_id: str,
        org_id: str,
        action: str,
        data: Dict[str, Any],
        confidence: float,
        source: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> TruthEvent:
        """
        Record a ground truth event.
        
        Args:
            entity_type: Type of entity
            entity_id: Entity identifier
            org_id: Organization scope
            action: Action that occurred
            data: Event payload
            confidence: Confidence score (0.0 - 1.0)
            source: Event source ("ai", "user", "system")
            user_id: Optional triggering user
            metadata: Additional data
            
        Returns:
            The recorded TruthEvent
        """
        # Get previous hash for chain integrity
        previous_hash = await self._get_last_hash(org_id)
        
        # Create event
        event = TruthEvent(
            event_id=str(uuid.uuid4()),
            entity_type=entity_type,
            entity_id=entity_id,
            org_id=org_id,
            action=action,
            data=data,
            confidence=confidence,
            source=source,
            user_id=user_id,
            previous_hash=previous_hash,
            timestamp=datetime.utcnow(),
            metadata=metadata or {}
        )
        
        # Determine review status
        if self._requires_review(confidence):
            event.review_status = ReviewStatus.PENDING
        
        # Store event
        await self._store_event(event)
        
        # Update cache
        self._last_hash_cache[org_id] = event.event_hash
        
        # Submit for review if needed
        if event.requires_review():
            await self.submit_for_review(event)
        
        logger.info(f"Recorded truth event: {event.event_id} "
                   f"(type={entity_type.value}, confidence={confidence:.2f})")
        
        return event
    
    async def _store_event(self, event: TruthEvent) -> None:
        """Store event in the database."""
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO truth_ledger (
                        event_id, entity_type, entity_id, org_id,
                        action, data, confidence, source, user_id,
                        previous_hash, event_hash, timestamp,
                        review_status, metadata
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    """,
                    event.event_id,
                    event.entity_type.value,
                    event.entity_id,
                    event.org_id,
                    event.action,
                    json.dumps(event.data),
                    event.confidence,
                    event.source,
                    event.user_id,
                    event.previous_hash,
                    event.event_hash,
                    event.timestamp,
                    event.review_status.value,
                    json.dumps(event.metadata)
                )
        except Exception as e:
            logger.error(f"Failed to store truth event: {e}")
            raise TruthLedgerError(f"Failed to store event: {e}")
    
    async def _get_last_hash(self, org_id: str) -> Optional[str]:
        """Get the hash of the last event for an organization."""
        # Check cache first
        if org_id in self._last_hash_cache:
            return self._last_hash_cache[org_id]
        
        # Query database
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT event_hash 
                    FROM truth_ledger 
                    WHERE org_id = $1 
                    ORDER BY timestamp DESC 
                    LIMIT 1
                    """,
                    org_id
                )
                
                if row:
                    return row["event_hash"]
        except Exception as e:
            logger.error(f"Failed to get last hash: {e}")
        
        return None
    
    def _requires_review(self, confidence: float) -> bool:
        """Check if confidence level requires human review."""
        return (self.config.review_queue_threshold_low <= confidence < 
                self.config.review_queue_threshold_high)
    
    async def submit_for_review(self, event: TruthEvent) -> None:
        """
        Submit an event for human review.
        
        Adds the event to the review queue and sends notifications.
        """
        try:
            async with self.db_pool.acquire() as conn:
                # Calculate priority (lower confidence = higher priority)
                priority = int(10 - (event.confidence * 10))
                
                await conn.execute(
                    """
                    INSERT INTO review_queue (
                        event_id, entity_type, entity_id, org_id,
                        confidence, submitted_at, priority, status
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    event.event_id,
                    event.entity_type.value,
                    event.entity_id,
                    event.org_id,
                    event.confidence,
                    datetime.utcnow(),
                    priority,
                    ReviewStatus.PENDING.value
                )
            
            # Send notification if configured
            if self.notification_client:
                await self._send_review_notification(event)
            
            logger.info(f"Submitted event {event.event_id} for review "
                       f"(confidence={event.confidence:.2f})")
            
        except Exception as e:
            logger.error(f"Failed to submit for review: {e}")
    
    async def review_event(
        self,
        event_id: str,
        reviewer_id: str,
        decision: str,  # "approved" or "rejected"
        notes: Optional[str] = None
    ) -> TruthEvent:
        """
        Review an event in the review queue.
        
        Args:
            event_id: Event to review
            reviewer_id: User performing the review
            decision: "approved" or "rejected"
            notes: Optional review notes
            
        Returns:
            Updated TruthEvent
        """
        review_status = (
            ReviewStatus.APPROVED if decision == "approved" 
            else ReviewStatus.REJECTED
        )
        
        try:
            async with self.db_pool.acquire() as conn:
                # Update truth_ledger
                await conn.execute(
                    """
                    UPDATE truth_ledger
                    SET review_status = $1,
                        reviewed_by = $2,
                        reviewed_at = $3,
                        review_notes = $4
                    WHERE event_id = $5
                    """,
                    review_status.value,
                    reviewer_id,
                    datetime.utcnow(),
                    notes,
                    event_id
                )
                
                # Update review queue
                await conn.execute(
                    """
                    UPDATE review_queue
                    SET status = $1,
                        reviewed_by = $2,
                        reviewed_at = $3
                    WHERE event_id = $4
                    """,
                    review_status.value,
                    reviewer_id,
                    datetime.utcnow(),
                    event_id
                )
                
                # Fetch updated event
                row = await conn.fetchrow(
                    "SELECT * FROM truth_ledger WHERE event_id = $1",
                    event_id
                )
                
                if row:
                    event = TruthEvent.from_dict(dict(row))
                    logger.info(f"Event {event_id} reviewed by {reviewer_id}: {decision}")
                    return event
                else:
                    raise TruthLedgerError(f"Event {event_id} not found")
                    
        except Exception as e:
            logger.error(f"Failed to review event: {e}")
            raise TruthLedgerError(f"Review failed: {e}")
    
    async def get_review_queue(
        self,
        org_id: Optional[str] = None,
        status: ReviewStatus = ReviewStatus.PENDING,
        limit: int = 50
    ) -> List[ReviewQueueEntry]:
        """
        Get events in the review queue.
        
        Args:
            org_id: Optional organization filter
            status: Review status filter
            limit: Maximum results
            
        Returns:
            List of review queue entries
        """
        try:
            async with self.db_pool.acquire() as conn:
                if org_id:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM review_queue
                        WHERE org_id = $1 AND status = $2
                        ORDER BY priority ASC, submitted_at ASC
                        LIMIT $3
                        """,
                        org_id, status.value, limit
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM review_queue
                        WHERE status = $1
                        ORDER BY priority ASC, submitted_at ASC
                        LIMIT $2
                        """,
                        status.value, limit
                    )
                
                return [
                    ReviewQueueEntry(
                        event_id=row["event_id"],
                        entity_type=EntityType(row["entity_type"]),
                        entity_id=row["entity_id"],
                        org_id=row["org_id"],
                        confidence=row["confidence"],
                        submitted_at=row["submitted_at"],
                        priority=row["priority"],
                        status=ReviewStatus(row["status"])
                    )
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"Failed to get review queue: {e}")
            return []
    
    async def verify_chain(self, org_id: str) -> List[Dict[str, Any]]:
        """
        Verify the integrity of the event chain for an organization.
        
        Checks that:
        1. Each event's hash is correct
        2. Each event's previous_hash matches the previous event
        
        Returns:
            List of any integrity issues found
        """
        issues = []
        
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM truth_ledger
                    WHERE org_id = $1
                    ORDER BY timestamp ASC
                    """,
                    org_id
                )
                
                previous_hash = None
                
                for row in rows:
                    event = TruthEvent.from_dict(dict(row))
                    
                    # Verify event hash
                    if not event.verify():
                        issues.append({
                            "event_id": event.event_id,
                            "issue": "hash_mismatch",
                            "message": "Event hash does not match calculated hash"
                        })
                        
                        # Update verification status
                        await conn.execute(
                            """
                            UPDATE truth_ledger
                            SET verification_status = $1
                            WHERE event_id = $2
                            """,
                            VerificationStatus.CORRUPTED.value,
                            event.event_id
                        )
                    else:
                        # Check chain integrity
                        if previous_hash and event.previous_hash != previous_hash:
                            issues.append({
                                "event_id": event.event_id,
                                "issue": "chain_break",
                                "message": f"Previous hash mismatch: expected {previous_hash}, got {event.previous_hash}"
                            })
                        
                        # Update verification status
                        await conn.execute(
                            """
                            UPDATE truth_ledger
                            SET verification_status = $1
                            WHERE event_id = $2
                            """,
                            VerificationStatus.VERIFIED.value,
                            event.event_id
                        )
                    
                    previous_hash = event.event_hash
        except Exception as e:
            logger.error(f"Chain verification failed: {e}")
            issues.append({"issue": "verification_error", "message": str(e)})
        
        return issues
    
    async def get_events(
        self,
        org_id: str,
        entity_type: Optional[EntityType] = None,
        entity_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100
    ) -> List[TruthEvent]:
        """
        Query events from the ledger.
        
        Args:
            org_id: Organization scope
            entity_type: Optional entity type filter
            entity_id: Optional entity ID filter
            start_time: Optional start time filter
            end_time: Optional end time filter
            limit: Maximum results
            
        Returns:
            List of matching TruthEvents
        """
        try:
            async with self.db_pool.acquire() as conn:
                query = "SELECT * FROM truth_ledger WHERE org_id = $1"
                params = [org_id]
                param_idx = 2
                
                if entity_type:
                    query += f" AND entity_type = ${param_idx}"
                    params.append(entity_type.value)
                    param_idx += 1
                
                if entity_id:
                    query += f" AND entity_id = ${param_idx}"
                    params.append(entity_id)
                    param_idx += 1
                
                if start_time:
                    query += f" AND timestamp >= ${param_idx}"
                    params.append(start_time)
                    param_idx += 1
                
                if end_time:
                    query += f" AND timestamp <= ${param_idx}"
                    params.append(end_time)
                    param_idx += 1
                
                query += f" ORDER BY timestamp DESC LIMIT ${param_idx}"
                params.append(limit)
                
                rows = await conn.fetch(query, *params)
                
                return [TruthEvent.from_dict(dict(row)) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get events: {e}")
            return []
    
    async def _send_review_notification(self, event: TruthEvent) -> None:
        """Send notification about pending review."""
        if not self.notification_client:
            return
        
        try:
            message = {
                "type": "review_required",
                "event_id": event.event_id,
                "entity_type": event.entity_type.value,
                "entity_id": event.entity_id,
                "org_id": event.org_id,
                "confidence": event.confidence,
                "action": event.action,
                "data": event.data
            }
            
            await self.notification_client.send(
                channel=f"review_queue:{event.org_id}",
                message=message
            )
        except Exception as e:
            logger.warning(f"Failed to send review notification: {e}")
    
    async def cleanup_old_events(self) -> int:
        """
        Clean up events older than retention period.
        
        Returns:
            Number of events archived/deleted
        """
        cutoff = datetime.utcnow() - timedelta(days=self.config.retention_days)
        
        try:
            async with self.db_pool.acquire() as conn:
                # Archive old events before deleting
                await conn.execute(
                    """
                    INSERT INTO truth_ledger_archive
                    SELECT * FROM truth_ledger
                    WHERE timestamp < $1
                    """,
                    cutoff
                )
                
                # Delete old events
                result = await conn.execute(
                    "DELETE FROM truth_ledger WHERE timestamp < $1",
                    cutoff
                )
                
                deleted = int(result.split()[1]) if result else 0
                logger.info(f"Cleaned up {deleted} old truth ledger events")
                return deleted
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
            return 0


class TruthLedgerError(Exception):
    """Raised when truth ledger operation fails."""
    pass


class ReviewQueueError(Exception):
    """Raised when review queue operation fails."""
    pass


@dataclass
class ExtractedFact:
    """A fact extracted from a transcript."""
    fact_id: str
    entity_type: EntityType
    entity_id: str
    fact_text: str
    confidence: float
    source_transcript_id: str
    timestamp: datetime
    speaker_id: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "fact_id": self.fact_id,
            "entity_type": self.entity_type.value,
            "entity_id": self.entity_id,
            "fact_text": self.fact_text,
            "confidence": self.confidence,
            "source_transcript_id": self.source_transcript_id,
            "timestamp": self.timestamp.isoformat(),
            "speaker_id": self.speaker_id,
            "context": self.context
        }


@dataclass
class TranscriptSegment:
    """A segment of a meeting transcript."""
    segment_id: str
    transcript_id: str
    speaker_id: str
    text: str
    start_time: float
    end_time: float
    timestamp: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "segment_id": self.segment_id,
            "transcript_id": self.transcript_id,
            "speaker_id": self.speaker_id,
            "text": self.text,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "timestamp": self.timestamp.isoformat()
        }


class DeepHistorianConfig(BaseModel):
    """Configuration for the DeepHistorian async worker."""
    enable_fact_extraction: bool = Field(default=True)
    extraction_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    review_queue_threshold_low: float = Field(default=0.5, ge=0.0, le=1.0)
    review_queue_threshold_high: float = Field(default=0.7, ge=0.0, le=1.0)
    batch_size: int = Field(default=10, ge=1, le=100)
    max_workers: int = Field(default=3, ge=1, le=10)
    entity_types: List[EntityType] = Field(
        default=[EntityType.DECISION, EntityType.OWNER, EntityType.BUDGET]
    )


class DeepHistorian:
    """
    T38: DeepHistorian async worker for fact extraction from transcripts.
    
    The DeepHistorian processes meeting transcripts asynchronously to:
    1. Extract facts related to ground truth entities (DECISION, OWNER, BUDGET)
    2. Score confidence for each extracted fact
    3. Route uncertain facts (0.5-0.7 confidence) to human review queue
    4. Record verified facts to the Truth Ledger
    
    Usage:
        historian = DeepHistorian(ledger, config)
        
        # Process a transcript
        await historian.process_transcript(transcript_id, segments)
        
        # Or run as background worker
        await historian.start_worker()
    """
    
    def __init__(
        self,
        ledger: TruthLedger,
        config: Optional[DeepHistorianConfig] = None,
        llm_client: Optional[Any] = None
    ):
        """
        Initialize the DeepHistorian worker.
        
        Args:
            ledger: TruthLedger instance for recording facts
            config: DeepHistorian configuration
            llm_client: Optional LLM client for fact extraction
        """
        self.ledger = ledger
        self.config = config or DeepHistorianConfig()
        self.llm_client = llm_client
        self._processing_queue: List[str] = []
        self._is_running = False
        
        logger.info(
            f"DeepHistorian initialized (entities={[e.value for e in self.config.entity_types]})"
        )
    
    async def process_transcript(
        self,
        transcript_id: str,
        segments: List[TranscriptSegment],
        org_id: str,
        meeting_id: Optional[str] = None
    ) -> List[ExtractedFact]:
        """
        T38: Process a meeting transcript and extract facts.
        
        Args:
            transcript_id: Unique transcript identifier
            segments: List of transcript segments
            org_id: Organization scope
            meeting_id: Optional meeting identifier
            
        Returns:
            List of extracted facts
            
        Example:
            segments = [
                TranscriptSegment(
                    segment_id="seg_1",
                    transcript_id="trans_123",
                    speaker_id="user_1",
                    text="We decided to migrate to the new API",
                    start_time=0.0,
                    end_time=5.0,
                    timestamp=datetime.utcnow()
                )
            ]
            
            facts = await historian.process_transcript(
                transcript_id="trans_123",
                segments=segments,
                org_id="org_456"
            )
        """
        logger.info(f"T38: Processing transcript {transcript_id} ({len(segments)} segments)")
        
        extracted_facts = []
        
        for segment in segments:
            # Extract facts from segment
            facts = await self._extract_facts_from_segment(segment, org_id)
            extracted_facts.extend(facts)
        
        # Process extracted facts
        for fact in extracted_facts:
            await self._process_extracted_fact(fact, org_id, transcript_id, meeting_id)
        
        logger.info(
            f"T38: Transcript {transcript_id} processed | "
            f"Facts extracted: {len(extracted_facts)}"
        )
        
        return extracted_facts
    
    async def _extract_facts_from_segment(
        self,
        segment: TranscriptSegment,
        org_id: str
    ) -> List[ExtractedFact]:
        """
        Extract facts from a single transcript segment.
        
        Args:
            segment: Transcript segment
            org_id: Organization scope
            
        Returns:
            List of extracted facts
        """
        facts = []
        
        # Use LLM for fact extraction if available
        if self.llm_client:
            facts = await self._extract_with_llm(segment, org_id)
        else:
            # Fallback: rule-based extraction
            facts = self._extract_with_rules(segment, org_id)
        
        return facts
    
    async def _extract_with_llm(
        self,
        segment: TranscriptSegment,
        org_id: str
    ) -> List[ExtractedFact]:
        """Extract facts using LLM."""
        facts = []
        
        if not self.llm_client:
            return facts
        
        try:
            # Build prompt for fact extraction
            prompt = self._build_extraction_prompt(segment.text)
            
            response = await self.llm_client.complete(prompt)
            
            # Parse LLM response
            extracted = self._parse_llm_extraction(response, segment)
            facts.extend(extracted)
            
        except Exception as e:
            logger.warning(f"LLM fact extraction failed: {e}")
        
        return facts
    
    def _extract_with_rules(
        self,
        segment: TranscriptSegment,
        org_id: str
    ) -> List[ExtractedFact]:
        """Extract facts using rule-based patterns."""
        facts = []
        text = segment.text.lower()
        
        # Decision patterns
        decision_patterns = [
            (r"we decided to\s+(.+)", EntityType.DECISION),
            (r"the decision is to\s+(.+)", EntityType.DECISION),
            (r"let's go with\s+(.+)", EntityType.DECISION),
        ]
        
        # Owner patterns
        owner_patterns = [
            (r"(\w+) will (?:own|handle|manage|lead)\s+(.+)", EntityType.OWNER),
            (r"assigned to\s+(\w+)", EntityType.OWNER),
        ]
        
        # Budget patterns
        budget_patterns = [
            (r"budget of\s+\$?([\d,]+)", EntityType.BUDGET),
            (r"allocate\s+\$?([\d,]+)", EntityType.BUDGET),
            (r"costs?\s+\$?([\d,]+)", EntityType.BUDGET),
        ]
        
        import re
        
        # Check decision patterns
        for pattern, entity_type in decision_patterns:
            match = re.search(pattern, text)
            if match:
                fact = ExtractedFact(
                    fact_id=f"fact_{segment.segment_id}_{len(facts)}",
                    entity_type=entity_type,
                    entity_id=f"dec_{segment.transcript_id}_{len(facts)}",
                    fact_text=match.group(1).strip(),
                    confidence=0.75,  # Rule-based confidence
                    source_transcript_id=segment.transcript_id,
                    timestamp=segment.timestamp,
                    speaker_id=segment.speaker_id,
                    context={"segment_text": segment.text}
                )
                facts.append(fact)
        
        # Check owner patterns
        for pattern, entity_type in owner_patterns:
            match = re.search(pattern, text)
            if match:
                fact = ExtractedFact(
                    fact_id=f"fact_{segment.segment_id}_{len(facts)}",
                    entity_type=entity_type,
                    entity_id=f"owner_{match.group(1)}_{len(facts)}",
                    fact_text=f"{match.group(1)} assigned to {match.group(2) if len(match.groups()) > 1 else 'task'}",
                    confidence=0.70,
                    source_transcript_id=segment.transcript_id,
                    timestamp=segment.timestamp,
                    speaker_id=segment.speaker_id,
                    context={"segment_text": segment.text}
                )
                facts.append(fact)
        
        # Check budget patterns
        for pattern, entity_type in budget_patterns:
            match = re.search(pattern, text)
            if match:
                fact = ExtractedFact(
                    fact_id=f"fact_{segment.segment_id}_{len(facts)}",
                    entity_type=entity_type,
                    entity_id=f"budget_{segment.transcript_id}_{len(facts)}",
                    fact_text=f"Budget: ${match.group(1)}",
                    confidence=0.80,
                    source_transcript_id=segment.transcript_id,
                    timestamp=segment.timestamp,
                    speaker_id=segment.speaker_id,
                    context={"segment_text": segment.text}
                )
                facts.append(fact)
        
        return facts
    
    def _build_extraction_prompt(self, text: str) -> str:
        """Build LLM prompt for fact extraction."""
        return f"""Extract facts from the following meeting transcript segment.
Focus on: decisions made, owners assigned, and budget mentions.

Transcript: "{text}"

For each fact found, provide:
1. Entity type (DECISION, OWNER, or BUDGET)
2. The fact text
3. Confidence score (0.0-1.0)

Format: ENTITY_TYPE|FACT_TEXT|CONFIDENCE"""
    
    def _parse_llm_extraction(
        self,
        response: str,
        segment: TranscriptSegment
    ) -> List[ExtractedFact]:
        """Parse LLM extraction response."""
        facts = []
        
        for line in response.strip().split('\n'):
            parts = line.split('|')
            if len(parts) >= 3:
                try:
                    entity_type = EntityType(parts[0].strip().lower())
                    fact_text = parts[1].strip()
                    confidence = float(parts[2].strip())
                    
                    fact = ExtractedFact(
                        fact_id=f"fact_{segment.segment_id}_{len(facts)}",
                        entity_type=entity_type,
                        entity_id=f"{entity_type.value}_{segment.transcript_id}_{len(facts)}",
                        fact_text=fact_text,
                        confidence=confidence,
                        source_transcript_id=segment.transcript_id,
                        timestamp=segment.timestamp,
                        speaker_id=segment.speaker_id
                    )
                    facts.append(fact)
                except (ValueError, KeyError):
                    continue
        
        return facts
    
    async def _process_extracted_fact(
        self,
        fact: ExtractedFact,
        org_id: str,
        transcript_id: str,
        meeting_id: Optional[str] = None
    ) -> None:
        """
        Process an extracted fact - route to review queue or ledger.
        
        Args:
            fact: Extracted fact
            org_id: Organization scope
            transcript_id: Source transcript ID
            meeting_id: Optional meeting ID
        """
        # Check if confidence requires review
        if self._requires_review(fact.confidence):
            # Submit to review queue
            await self._submit_fact_for_review(fact, org_id, transcript_id, meeting_id)
            logger.debug(
                f"T38: Fact {fact.fact_id} queued for review "
                f"(confidence={fact.confidence:.2f})"
            )
        else:
            # Record directly to ledger
            await self._record_fact_to_ledger(fact, org_id, transcript_id, meeting_id)
            logger.debug(
                f"T38: Fact {fact.fact_id} recorded to ledger "
                f"(confidence={fact.confidence:.2f})"
            )
    
    def _requires_review(self, confidence: float) -> bool:
        """Check if confidence level requires human review."""
        return (
            self.config.review_queue_threshold_low <= confidence < 
            self.config.review_queue_threshold_high
        )
    
    async def _submit_fact_for_review(
        self,
        fact: ExtractedFact,
        org_id: str,
        transcript_id: str,
        meeting_id: Optional[str] = None
    ) -> None:
        """Submit a fact for human review."""
        try:
            async with self.ledger.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO fact_review_queue (
                        fact_id, entity_type, entity_id, org_id,
                        fact_text, confidence, source_transcript_id,
                        meeting_id, submitted_at, status
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    ON CONFLICT (fact_id) DO NOTHING
                    """,
                    fact.fact_id,
                    fact.entity_type.value,
                    fact.entity_id,
                    org_id,
                    fact.fact_text,
                    fact.confidence,
                    transcript_id,
                    meeting_id,
                    datetime.utcnow(),
                    "pending"
                )
        except Exception as e:
            logger.error(f"Failed to submit fact for review: {e}")
    
    async def _record_fact_to_ledger(
        self,
        fact: ExtractedFact,
        org_id: str,
        transcript_id: str,
        meeting_id: Optional[str] = None
    ) -> None:
        """Record a fact to the Truth Ledger."""
        await self.ledger.record_event(
            entity_type=fact.entity_type,
            entity_id=fact.entity_id,
            org_id=org_id,
            action="extracted_from_transcript",
            data={
                "fact_text": fact.fact_text,
                "source_transcript_id": transcript_id,
                "meeting_id": meeting_id,
                "speaker_id": fact.speaker_id,
                "context": fact.context
            },
            confidence=fact.confidence,
            source="deep_historian",
            metadata={
                "fact_id": fact.fact_id,
                "extraction_method": "llm" if self.llm_client else "rule_based"
            }
        )
    
    async def get_review_queue(
        self,
        org_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get facts in the review queue.
        
        Args:
            org_id: Optional organization filter
            limit: Maximum results
            
        Returns:
            List of facts awaiting review
        """
        try:
            async with self.ledger.db_pool.acquire() as conn:
                if org_id:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM fact_review_queue
                        WHERE org_id = $1 AND status = 'pending'
                        ORDER BY confidence ASC, submitted_at ASC
                        LIMIT $2
                        """,
                        org_id, limit
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM fact_review_queue
                        WHERE status = 'pending'
                        ORDER BY confidence ASC, submitted_at ASC
                        LIMIT $1
                        """,
                        limit
                    )
                
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get review queue: {e}")
            return []
    
    async def review_fact(
        self,
        fact_id: str,
        reviewer_id: str,
        decision: str,  # "approved" or "rejected"
        notes: Optional[str] = None
    ) -> bool:
        """
        Review a fact in the queue.
        
        Args:
            fact_id: Fact to review
            reviewer_id: User performing review
            decision: "approved" or "rejected"
            notes: Optional review notes
            
        Returns:
            True if successful
        """
        try:
            async with self.ledger.db_pool.acquire() as conn:
                # Update review queue
                await conn.execute(
                    """
                    UPDATE fact_review_queue
                    SET status = $1,
                        reviewer_id = $2,
                        reviewed_at = $3,
                        review_notes = $4
                    WHERE fact_id = $5
                    """,
                    decision,
                    reviewer_id,
                    datetime.utcnow(),
                    notes,
                    fact_id
                )
                
                # If approved, record to ledger
                if decision == "approved":
                    row = await conn.fetchrow(
                        "SELECT * FROM fact_review_queue WHERE fact_id = $1",
                        fact_id
                    )
                    if row:
                        await self.ledger.record_event(
                            entity_type=EntityType(row["entity_type"]),
                            entity_id=row["entity_id"],
                            org_id=row["org_id"],
                            action="reviewed_and_approved",
                            data={
                                "fact_text": row["fact_text"],
                                "source_transcript_id": row["source_transcript_id"],
                                "reviewer_id": reviewer_id,
                                "review_notes": notes
                            },
                            confidence=row["confidence"],
                            source="human_review",
                            user_id=reviewer_id
                        )
                
                logger.info(f"T38: Fact {fact_id} reviewed by {reviewer_id}: {decision}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to review fact: {e}")
            return False


class TruthLedgerController:
    """
    T38: Controller for Truth Ledger conflict resolution.
    
    The controller provides a unified interface for:
    1. Recording events from various sources
    2. Managing the review queue
    3. Resolving conflicts between different event sources
    4. Coordinating between DeepHistorian and TruthLedger
    
    Usage:
        controller = TruthLedgerController(ledger, historian)
        
        # Record an event
        await controller.record_event(...)
        
        # Get review queue
        queue = await controller.get_review_queue()
        
        # Resolve conflict
        await controller.resolve_conflict(event_id, resolution)
    """
    
    def __init__(
        self,
        ledger: TruthLedger,
        historian: Optional[DeepHistorian] = None
    ):
        """
        Initialize the controller.
        
        Args:
            ledger: TruthLedger instance
            historian: Optional DeepHistorian instance
        """
        self.ledger = ledger
        self.historian = historian
        logger.info("TruthLedgerController initialized")
    
    async def record_event(
        self,
        entity_type: EntityType,
        entity_id: str,
        org_id: str,
        action: str,
        data: Dict[str, Any],
        confidence: float,
        source: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> TruthEvent:
        """
        Record an event through the controller.
        
        Args:
            entity_type: Type of entity
            entity_id: Entity identifier
            org_id: Organization scope
            action: Action that occurred
            data: Event payload
            confidence: Confidence score
            source: Event source
            user_id: Optional triggering user
            metadata: Additional metadata
            
        Returns:
            Recorded TruthEvent
        """
        return await self.ledger.record_event(
            entity_type=entity_type,
            entity_id=entity_id,
            org_id=org_id,
            action=action,
            data=data,
            confidence=confidence,
            source=source,
            user_id=user_id,
            metadata=metadata
        )
    
    async def resolve_conflict(
        self,
        event_id: str,
        resolution: str,  # "accept", "reject", "merge"
        resolver_id: str,
        merged_data: Optional[Dict[str, Any]] = None,
        notes: Optional[str] = None
    ) -> bool:
        """
        Resolve a conflict between events.
        
        Args:
            event_id: Event with conflict
            resolution: Resolution action
            resolver_id: User resolving the conflict
            merged_data: Optional merged data
            notes: Optional resolution notes
            
        Returns:
            True if resolved successfully
        """
        try:
            async with self.ledger.db_pool.acquire() as conn:
                # Get the conflicting event
                row = await conn.fetchrow(
                    "SELECT * FROM truth_ledger WHERE event_id = $1",
                    event_id
                )
                
                if not row:
                    logger.warning(f"Event {event_id} not found for conflict resolution")
                    return False
                
                # Record resolution
                await conn.execute(
                    """
                    INSERT INTO conflict_resolutions (
                        event_id, resolution, resolver_id,
                        merged_data, notes, resolved_at
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    event_id,
                    resolution,
                    resolver_id,
                    json.dumps(merged_data) if merged_data else None,
                    notes,
                    datetime.utcnow()
                )
                
                # Update event status
                await conn.execute(
                    """
                    UPDATE truth_ledger
                    SET review_status = $1,
                        reviewed_by = $2,
                        reviewed_at = $3,
                        review_notes = $4
                    WHERE event_id = $5
                    """,
                    "resolved",
                    resolver_id,
                    datetime.utcnow(),
                    f"Conflict resolved: {resolution}. {notes or ''}",
                    event_id
                )
                
                logger.info(
                    f"T38: Conflict resolved for event {event_id} "
                    f"by {resolver_id}: {resolution}"
                )
                return True
                
        except Exception as e:
            logger.error(f"Failed to resolve conflict: {e}")
            return False
    
    async def get_review_queue(
        self,
        org_id: Optional[str] = None,
        limit: int = 50
    ) -> List[ReviewQueueEntry]:
        """Get events in the review queue."""
        return await self.ledger.get_review_queue(org_id=org_id, limit=limit)
    
    async def get_fact_review_queue(
        self,
        org_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get facts in the review queue (from DeepHistorian)."""
        if self.historian:
            return await self.historian.get_review_queue(org_id=org_id, limit=limit)
        return []
    
    async def review_event(
        self,
        event_id: str,
        reviewer_id: str,
        decision: str,
        notes: Optional[str] = None
    ) -> TruthEvent:
        """Review an event in the queue."""
        return await self.ledger.review_event(
            event_id=event_id,
            reviewer_id=reviewer_id,
            decision=decision,
            notes=notes
        )
    
    async def review_fact(
        self,
        fact_id: str,
        reviewer_id: str,
        decision: str,
        notes: Optional[str] = None
    ) -> bool:
        """Review a fact in the queue."""
        if self.historian:
            return await self.historian.review_fact(
                fact_id=fact_id,
                reviewer_id=reviewer_id,
                decision=decision,
                notes=notes
            )
        return False
    
    async def get_entity_history(
        self,
        org_id: str,
        entity_type: EntityType,
        entity_id: str,
        limit: int = 100
    ) -> List[TruthEvent]:
        """
        Get the full history for an entity.
        
        Args:
            org_id: Organization scope
            entity_type: Type of entity
            entity_id: Entity identifier
            limit: Maximum events
            
        Returns:
            List of events for the entity
        """
        return await self.ledger.get_events(
            org_id=org_id,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit
        )
    
    async def verify_chain(self, org_id: str) -> List[Dict[str, Any]]:
        """Verify the integrity of the event chain."""
        return await self.ledger.verify_chain(org_id=org_id)
