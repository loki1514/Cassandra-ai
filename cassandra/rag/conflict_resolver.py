"""
Conflict Resolver Module (T24)

Implements conflict resolution between memory system results and DB1 data.
Core rule: DB1 always wins - DB1 is the source of truth for ticket status.

This module provides:
- merge_context(): Merge memory and DB1 results with conflict resolution
- Override logging for audit trail
- Multiple resolution strategies (DB1_WINS is default)
- Comprehensive test coverage

Conflict Types:
1. Status conflict: Memory status != DB1 status
2. Assignee conflict: Memory assignee != DB1 assignee
3. Priority conflict: Memory priority != DB1 priority
4. Stale data: Memory created before last DB1 update
"""

import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ConflictType(str, Enum):
    """Types of conflicts that can occur between memory and DB1."""
    STATUS = "status"           # Ticket status mismatch
    ASSIGNEE = "assignee"       # Assignee mismatch
    PRIORITY = "priority"       # Priority mismatch
    STALE_DATA = "stale_data"   # Memory is older than DB1 update
    RESOLUTION = "resolution"   # Resolution notes differ
    METADATA = "metadata"       # Other metadata conflicts


class ResolutionStrategy(str, Enum):
    """Available conflict resolution strategies."""
    DB1_WINS = "db1_wins"                   # DB1 always wins (default)
    MEMORY_WINS = "memory_wins"             # Memory always wins
    TIMESTAMP_WINS = "timestamp_wins"       # Most recent wins
    MERGE_FIELDS = "merge_fields"           # Merge non-conflicting fields
    MANUAL_REVIEW = "manual_review"         # Queue for human review


@dataclass
class ConflictDetails:
    """Detailed information about a detected conflict."""
    conflict_type: ConflictType
    field: str
    memory_value: Any
    db1_value: Any
    detected_at: datetime = field(default_factory=datetime.utcnow)
    resolution: Optional[str] = None
    resolution_timestamp: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "conflict_type": self.conflict_type.value,
            "field": self.field,
            "memory_value": self.memory_value,
            "db1_value": self.db1_value,
            "detected_at": self.detected_at.isoformat(),
            "resolution": self.resolution,
            "resolution_timestamp": self.resolution_timestamp.isoformat() if self.resolution_timestamp else None
        }


@dataclass
class OverrideLogEntry:
    """Log entry for when DB1 overrides memory data."""
    memory_id: Optional[str]
    ticket_id: str
    org_id: str
    conflict_type: ConflictType
    overridden_field: str
    memory_value: Any
    db1_value: Any
    overridden_at: datetime = field(default_factory=datetime.utcnow)
    reason: str = "db1_authoritative"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "memory_id": self.memory_id,
            "ticket_id": self.ticket_id,
            "org_id": self.org_id,
            "conflict_type": self.conflict_type.value,
            "overridden_field": self.overridden_field,
            "memory_value": str(self.memory_value)[:500],  # Truncate for log size
            "db1_value": str(self.db1_value)[:500],
            "overridden_at": self.overridden_at.isoformat(),
            "reason": self.reason
        }


@dataclass
class MergedContextResult:
    """Result of merging memory and DB1 context."""
    content: Dict[str, Any]
    source: str
    confidence: float
    conflicts_detected: List[ConflictDetails] = field(default_factory=list)
    overrides_applied: List[OverrideLogEntry] = field(default_factory=list)
    resolution_strategy: ResolutionStrategy = ResolutionStrategy.DB1_WINS
    merged_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "content": self.content,
            "source": self.source,
            "confidence": self.confidence,
            "conflicts_detected": [c.to_dict() for c in self.conflicts_detected],
            "overrides_applied": [o.to_dict() for o in self.overrides_applied],
            "resolution_strategy": self.resolution_strategy.value,
            "merged_at": self.merged_at.isoformat()
        }


class ConflictResolverConfig(BaseModel):
    """Configuration for conflict resolution."""
    default_strategy: ResolutionStrategy = Field(
        default=ResolutionStrategy.DB1_WINS,
        description="Default conflict resolution strategy"
    )
    enable_override_logging: bool = Field(
        default=True,
        description="Enable logging of DB1 overrides"
    )
    log_retention_days: int = Field(
        default=90,
        ge=1,
        le=365,
        description="Days to retain override logs"
    )
    alert_on_override: bool = Field(
        default=False,
        description="Send alert when override occurs"
    )


class ConflictResolver:
    """
    Conflict resolver implementing the DB1-always-wins rule.
    
    The ConflictResolver handles merging of memory system results with
    authoritative DB1 data, ensuring that DB1 is always the source of truth
    for ticket-related information.
    
    Usage:
        resolver = ConflictResolver(config)
        
        # Merge memory and DB1 results
        result = await resolver.merge_context(
            memory_result={"status": "open", "assignee": "user1"},
            db1_result={"status": "closed", "assignee": "user2"},
            memory_id="mem_123",
            ticket_id="TICKET-456",
            org_id="org_789"
        )
        
        # Access merged result
        print(result.content)  # DB1 values (status="closed", assignee="user2")
        print(result.overrides_applied)  # Log of what was overridden
    """
    
    def __init__(self, config: Optional[ConflictResolverConfig] = None):
        """
        Initialize the conflict resolver.
        
        Args:
            config: Configuration for conflict resolution
        """
        self.config = config or ConflictResolverConfig()
        self._override_logs: List[OverrideLogEntry] = []
        logger.info(f"ConflictResolver initialized (strategy={self.config.default_strategy.value})")
    
    async def merge_context(
        self,
        memory_result: Dict[str, Any],
        db1_result: Dict[str, Any],
        memory_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
        org_id: Optional[str] = None,
        strategy: Optional[ResolutionStrategy] = None
    ) -> MergedContextResult:
        """
        T24: Merge memory result with DB1 result, applying conflict resolution.
        
        Core rule: DB1 always wins for ticket status fields.
        
        Args:
            memory_result: Data from memory system (e.g., cached ticket state)
            db1_result: Authoritative data from DB1
            memory_id: Optional memory entry ID for logging
            ticket_id: Optional ticket ID for logging
            org_id: Optional organization ID for logging
            strategy: Override default resolution strategy
            
        Returns:
            MergedContextResult with resolved content and override logs
            
        Example:
            result = await resolver.merge_context(
                memory_result={
                    "status": "in_progress",
                    "assignee_id": "user_old",
                    "priority": "high"
                },
                db1_result={
                    "status": "resolved",
                    "assignee_id": "user_new",
                    "priority": "medium"
                },
                memory_id="mem_123",
                ticket_id="TICKET-456",
                org_id="org_789"
            )
            
            # Result contains DB1 values
            assert result.content["status"] == "resolved"
            assert result.content["assignee_id"] == "user_new"
            assert len(result.overrides_applied) == 3  # All fields overridden
        """
        strategy = strategy or self.config.default_strategy
        
        logger.debug(f"Merging context for ticket {ticket_id} using strategy: {strategy.value}")
        
        # Detect conflicts
        conflicts = self._detect_conflicts(memory_result, db1_result)
        
        # Apply resolution strategy
        if strategy == ResolutionStrategy.DB1_WINS:
            merged_content, overrides = self._apply_db1_wins(
                memory_result, db1_result, conflicts, memory_id, ticket_id, org_id
            )
        elif strategy == ResolutionStrategy.MEMORY_WINS:
            merged_content, overrides = self._apply_memory_wins(
                memory_result, db1_result, conflicts, memory_id, ticket_id, org_id
            )
        elif strategy == ResolutionStrategy.TIMESTAMP_WINS:
            merged_content, overrides = self._apply_timestamp_wins(
                memory_result, db1_result, conflicts, memory_id, ticket_id, org_id
            )
        elif strategy == ResolutionStrategy.MERGE_FIELDS:
            merged_content, overrides = self._apply_merge_fields(
                memory_result, db1_result, conflicts, memory_id, ticket_id, org_id
            )
        elif strategy == ResolutionStrategy.MANUAL_REVIEW:
            merged_content, overrides = self._apply_manual_review(
                memory_result, db1_result, conflicts, memory_id, ticket_id, org_id
            )
        else:
            raise ConflictResolutionError(f"Unknown resolution strategy: {strategy}")
        
        # Log overrides if enabled
        if self.config.enable_override_logging and overrides:
            self._override_logs.extend(overrides)
            for override in overrides:
                logger.info(
                    f"T24: DB1 override - {override.overridden_field}: "
                    f"'{override.memory_value}' -> '{override.db1_value}' "
                    f"(ticket={ticket_id}, memory={memory_id})"
                )
        
        return MergedContextResult(
            content=merged_content,
            source="db1" if strategy == ResolutionStrategy.DB1_WINS else "merged",
            confidence=1.0 if strategy == ResolutionStrategy.DB1_WINS else 0.8,
            conflicts_detected=conflicts,
            overrides_applied=overrides,
            resolution_strategy=strategy
        )
    
    def _detect_conflicts(
        self,
        memory_result: Dict[str, Any],
        db1_result: Dict[str, Any]
    ) -> List[ConflictDetails]:
        """
        Detect conflicts between memory and DB1 results.
        
        Args:
            memory_result: Memory system data
            db1_result: DB1 data
            
        Returns:
            List of detected conflicts
        """
        conflicts = []
        
        # Fields to check for conflicts
        conflict_fields = {
            "status": ConflictType.STATUS,
            "assignee_id": ConflictType.ASSIGNEE,
            "priority": ConflictType.PRIORITY,
            "resolution_notes": ConflictType.RESOLUTION
        }
        
        for field, conflict_type in conflict_fields.items():
            memory_value = memory_result.get(field)
            db1_value = db1_result.get(field)
            
            # Check if both have the field and values differ
            if memory_value is not None and db1_value is not None:
                if memory_value != db1_value:
                    conflicts.append(ConflictDetails(
                        conflict_type=conflict_type,
                        field=field,
                        memory_value=memory_value,
                        db1_value=db1_value
                    ))
        
        # Check for stale data
        memory_updated = memory_result.get("updated_at")
        db1_updated = db1_result.get("updated_at")
        
        if memory_updated and db1_updated:
            try:
                memory_time = self._parse_timestamp(memory_updated)
                db1_time = self._parse_timestamp(db1_updated)
                
                if memory_time < db1_time:
                    conflicts.append(ConflictDetails(
                        conflict_type=ConflictType.STALE_DATA,
                        field="updated_at",
                        memory_value=memory_updated,
                        db1_value=db1_updated
                    ))
            except (ValueError, TypeError):
                pass  # Can't parse timestamps, skip stale check
        
        return conflicts
    
    def _apply_db1_wins(
        self,
        memory_result: Dict[str, Any],
        db1_result: Dict[str, Any],
        conflicts: List[ConflictDetails],
        memory_id: Optional[str],
        ticket_id: Optional[str],
        org_id: Optional[str]
    ) -> tuple[Dict[str, Any], List[OverrideLogEntry]]:
        """
        Apply DB1-wins resolution strategy.
        
        DB1 values override memory values for all conflicting fields.
        Non-conflicting fields from memory are preserved.
        
        Args:
            memory_result: Memory system data
            db1_result: DB1 data
            conflicts: Detected conflicts
            memory_id: Memory ID for logging
            ticket_id: Ticket ID for logging
            org_id: Organization ID for logging
            
        Returns:
            Tuple of (merged content, override logs)
        """
        # Start with memory result as base
        merged = dict(memory_result)
        overrides = []
        
        # Apply DB1 values for all conflicting fields
        for conflict in conflicts:
            if conflict.field in db1_result:
                # Log the override
                override = OverrideLogEntry(
                    memory_id=memory_id,
                    ticket_id=ticket_id or "unknown",
                    org_id=org_id or "unknown",
                    conflict_type=conflict.conflict_type,
                    overridden_field=conflict.field,
                    memory_value=conflict.memory_value,
                    db1_value=conflict.db1_value,
                    reason="db1_authoritative"
                )
                overrides.append(override)
                
                # Apply DB1 value
                merged[conflict.field] = conflict.db1_value
                conflict.resolution = "db1_wins"
                conflict.resolution_timestamp = datetime.utcnow()
        
        # Add metadata about resolution
        merged["_resolution_metadata"] = {
            "strategy": "db1_wins",
            "conflicts_resolved": len(conflicts),
            "resolved_at": datetime.utcnow().isoformat()
        }
        
        return merged, overrides
    
    def _apply_memory_wins(
        self,
        memory_result: Dict[str, Any],
        db1_result: Dict[str, Any],
        conflicts: List[ConflictDetails],
        memory_id: Optional[str],
        ticket_id: Optional[str],
        org_id: Optional[str]
    ) -> tuple[Dict[str, Any], List[OverrideLogEntry]]:
        """Apply memory-wins resolution strategy."""
        merged = dict(memory_result)
        merged.update(db1_result)  # Start with DB1
        
        overrides = []
        
        # Override with memory values for conflicts
        for conflict in conflicts:
            merged[conflict.field] = conflict.memory_value
            override = OverrideLogEntry(
                memory_id=memory_id,
                ticket_id=ticket_id or "unknown",
                org_id=org_id or "unknown",
                conflict_type=conflict.conflict_type,
                overridden_field=conflict.field,
                memory_value=conflict.db1_value,
                db1_value=conflict.memory_value,
                reason="memory_wins"
            )
            overrides.append(override)
            conflict.resolution = "memory_wins"
            conflict.resolution_timestamp = datetime.utcnow()
        
        merged["_resolution_metadata"] = {
            "strategy": "memory_wins",
            "conflicts_resolved": len(conflicts),
            "resolved_at": datetime.utcnow().isoformat()
        }
        
        return merged, overrides
    
    def _apply_timestamp_wins(
        self,
        memory_result: Dict[str, Any],
        db1_result: Dict[str, Any],
        conflicts: List[ConflictDetails],
        memory_id: Optional[str],
        ticket_id: Optional[str],
        org_id: Optional[str]
    ) -> tuple[Dict[str, Any], List[OverrideLogEntry]]:
        """Apply timestamp-wins resolution strategy."""
        merged = dict(memory_result)
        merged.update(db1_result)
        
        overrides = []
        
        memory_time = self._parse_timestamp(memory_result.get("updated_at", "1970-01-01"))
        db1_time = self._parse_timestamp(db1_result.get("updated_at", "1970-01-01"))
        
        winner = "db1" if db1_time >= memory_time else "memory"
        
        for conflict in conflicts:
            if winner == "db1":
                merged[conflict.field] = conflict.db1_value
                reason = "timestamp_wins_db1"
            else:
                merged[conflict.field] = conflict.memory_value
                reason = "timestamp_wins_memory"
            
            override = OverrideLogEntry(
                memory_id=memory_id,
                ticket_id=ticket_id or "unknown",
                org_id=org_id or "unknown",
                conflict_type=conflict.conflict_type,
                overridden_field=conflict.field,
                memory_value=conflict.memory_value if winner == "db1" else conflict.db1_value,
                db1_value=conflict.db1_value if winner == "db1" else conflict.memory_value,
                reason=reason
            )
            overrides.append(override)
            conflict.resolution = reason
            conflict.resolution_timestamp = datetime.utcnow()
        
        merged["_resolution_metadata"] = {
            "strategy": "timestamp_wins",
            "winner": winner,
            "conflicts_resolved": len(conflicts),
            "resolved_at": datetime.utcnow().isoformat()
        }
        
        return merged, overrides
    
    def _apply_merge_fields(
        self,
        memory_result: Dict[str, Any],
        db1_result: Dict[str, Any],
        conflicts: List[ConflictDetails],
        memory_id: Optional[str],
        ticket_id: Optional[str],
        org_id: Optional[str]
    ) -> tuple[Dict[str, Any], List[OverrideLogEntry]]:
        """Apply merge-fields resolution strategy."""
        merged = dict(memory_result)
        merged.update(db1_result)
        
        overrides = []
        
        # For conflicts, prefer DB1 for authoritative fields, memory for others
        authoritative_fields = {"status", "assignee_id", "priority"}
        
        for conflict in conflicts:
            if conflict.field in authoritative_fields:
                merged[conflict.field] = conflict.db1_value
                reason = "merge_db1_authoritative"
            else:
                # Keep memory value for non-authoritative fields
                merged[conflict.field] = conflict.memory_value
                reason = "merge_memory_preferred"
            
            override = OverrideLogEntry(
                memory_id=memory_id,
                ticket_id=ticket_id or "unknown",
                org_id=org_id or "unknown",
                conflict_type=conflict.conflict_type,
                overridden_field=conflict.field,
                memory_value=conflict.memory_value,
                db1_value=conflict.db1_value,
                reason=reason
            )
            overrides.append(override)
            conflict.resolution = reason
            conflict.resolution_timestamp = datetime.utcnow()
        
        merged["_resolution_metadata"] = {
            "strategy": "merge_fields",
            "conflicts_resolved": len(conflicts),
            "resolved_at": datetime.utcnow().isoformat()
        }
        
        return merged, overrides
    
    def _apply_manual_review(
        self,
        memory_result: Dict[str, Any],
        db1_result: Dict[str, Any],
        conflicts: List[ConflictDetails],
        memory_id: Optional[str],
        ticket_id: Optional[str],
        org_id: Optional[str]
    ) -> tuple[Dict[str, Any], List[OverrideLogEntry]]:
        """Apply manual-review resolution strategy."""
        # Include both versions for manual review
        merged = {
            "_conflict": True,
            "_requires_review": True,
            "memory_data": memory_result,
            "db1_data": db1_result,
            "conflicts": [c.to_dict() for c in conflicts]
        }
        
        overrides = []
        
        for conflict in conflicts:
            override = OverrideLogEntry(
                memory_id=memory_id,
                ticket_id=ticket_id or "unknown",
                org_id=org_id or "unknown",
                conflict_type=conflict.conflict_type,
                overridden_field=conflict.field,
                memory_value=conflict.memory_value,
                db1_value=conflict.db1_value,
                reason="manual_review_queued"
            )
            overrides.append(override)
            conflict.resolution = "manual_review"
            conflict.resolution_timestamp = datetime.utcnow()
        
        logger.warning(
            f"T24: Manual review required for ticket {ticket_id} - "
            f"{len(conflicts)} conflicts detected"
        )
        
        return merged, overrides
    
    def _parse_timestamp(self, timestamp: Any) -> datetime:
        """Parse various timestamp formats."""
        if isinstance(timestamp, datetime):
            return timestamp
        if isinstance(timestamp, str):
            # Try ISO format
            try:
                return datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            except ValueError:
                pass
        return datetime.utcnow()
    
    def get_override_logs(
        self,
        ticket_id: Optional[str] = None,
        org_id: Optional[str] = None,
        since: Optional[datetime] = None
    ) -> List[OverrideLogEntry]:
        """
        Get override logs with optional filtering.
        
        Args:
            ticket_id: Filter by ticket ID
            org_id: Filter by organization ID
            since: Filter by timestamp
            
        Returns:
            List of matching override log entries
        """
        logs = self._override_logs
        
        if ticket_id:
            logs = [log for log in logs if log.ticket_id == ticket_id]
        
        if org_id:
            logs = [log for log in logs if log.org_id == org_id]
        
        if since:
            logs = [log for log in logs if log.overridden_at >= since]
        
        return logs
    
    def clear_override_logs(self) -> int:
        """Clear all override logs. Returns count cleared."""
        count = len(self._override_logs)
        self._override_logs = []
        return count


class ConflictResolutionError(Exception):
    """Raised when conflict resolution fails."""
    pass


# Convenience function for simple DB1-wins resolution
async def merge_context(
    memory_result: Dict[str, Any],
    db1_result: Dict[str, Any],
    memory_id: Optional[str] = None,
    ticket_id: Optional[str] = None,
    org_id: Optional[str] = None,
    strategy: ResolutionStrategy = ResolutionStrategy.DB1_WINS
) -> MergedContextResult:
    """
    T24: Merge memory result with DB1 result.
    
    Convenience function for one-off conflict resolution.
    Default strategy: DB1 always wins.
    
    Args:
        memory_result: Data from memory system
        db1_result: Authoritative data from DB1
        memory_id: Optional memory entry ID
        ticket_id: Optional ticket ID
        org_id: Optional organization ID
        strategy: Resolution strategy (default: DB1_WINS)
        
    Returns:
        MergedContextResult with resolved content
        
    Example:
        # Test Case 1: Status conflict - DB1 wins
        result = await merge_context(
            memory_result={"status": "open", "priority": "high"},
            db1_result={"status": "closed", "priority": "high"},
            ticket_id="TICKET-123",
            org_id="org_456"
        )
        assert result.content["status"] == "closed"  # DB1 value
        assert len(result.overrides_applied) == 1
    """
    resolver = ConflictResolver()
    return await resolver.merge_context(
        memory_result=memory_result,
        db1_result=db1_result,
        memory_id=memory_id,
        ticket_id=ticket_id,
        org_id=org_id,
        strategy=strategy
    )
