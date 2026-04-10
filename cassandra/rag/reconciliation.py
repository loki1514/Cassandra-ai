"""
Reconciliation Module

Handles orphaned memory detection and cleanup for the Cassandra AI system.
Memories can become orphaned when their associated tickets are deleted
or when the mapping between memory and ticket is lost.

This module provides:
- Daily orphan detection jobs
- Re-linking logic for recoverable orphans
- Flagging of irrecoverable orphans for manual review
- Statistics and reporting on orphan rates

Orphan Types:
1. Soft orphan: Memory exists but map table entry is missing (recoverable)
2. Hard orphan: Memory exists but associated ticket is deleted (irrecoverable)
3. Dangling reference: Map entry exists but memory is missing (cleanup needed)
"""

import json
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from pydantic import BaseModel, Field

from .memory_manager import MemoryManager, MemoryEntry, MemoryType

logger = logging.getLogger(__name__)


class OrphanStatus(str, Enum):
    """Status of an orphaned memory in the reconciliation process."""
    DETECTED = "detected"           # Just detected, not yet processed
    RECOVERABLE = "recoverable"     # Can be re-linked
    IRRECOVERABLE = "irrecoverable" # Cannot be auto-recovered
    RE_LINKED = "re_linked"         # Successfully re-linked
    FLAGGED = "flagged"             # Flagged for manual review
    CLEANED = "cleaned"             # Removed from system
    IGNORED = "ignored"             # Marked to ignore


class OrphanType(str, Enum):
    """Types of orphaned memories."""
    SOFT = "soft"                   # Missing map entry (recoverable)
    HARD = "hard"                   # Ticket deleted (irrecoverable)
    DANGLING = "dangling"           # Map entry without memory (cleanup)
    EXPIRED = "expired"             # Memory past expiration


@dataclass
class OrphanedMemory:
    """
    Represents an orphaned memory entry.
    
    Attributes:
        memory_id: The orphaned memory ID
        ticket_id: Associated ticket ID (if known)
        org_id: Organization scope
        orphan_type: Type of orphan
        status: Current reconciliation status
        memory_content: Snapshot of memory content
        detected_at: When the orphan was detected
        resolved_at: When the orphan was resolved (if applicable)
        resolution_action: What action was taken
        resolution_notes: Notes about resolution
        retry_count: Number of recovery attempts
    """
    memory_id: str
    ticket_id: Optional[str]
    org_id: str
    orphan_type: OrphanType
    status: OrphanStatus = OrphanStatus.DETECTED
    memory_content: Optional[Dict[str, Any]] = None
    detected_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
    resolution_action: Optional[str] = None
    resolution_notes: Optional[str] = None
    retry_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "memory_id": self.memory_id,
            "ticket_id": self.ticket_id,
            "org_id": self.org_id,
            "orphan_type": self.orphan_type.value,
            "status": self.status.value,
            "memory_content": self.memory_content,
            "detected_at": self.detected_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolution_action": self.resolution_action,
            "resolution_notes": self.resolution_notes,
            "retry_count": self.retry_count
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OrphanedMemory':
        """Create from dictionary."""
        return cls(
            memory_id=data["memory_id"],
            ticket_id=data.get("ticket_id"),
            org_id=data["org_id"],
            orphan_type=OrphanType(data["orphan_type"]),
            status=OrphanStatus(data.get("status", "detected")),
            memory_content=data.get("memory_content"),
            detected_at=datetime.fromisoformat(data["detected_at"]),
            resolved_at=datetime.fromisoformat(data["resolved_at"]) if data.get("resolved_at") else None,
            resolution_action=data.get("resolution_action"),
            resolution_notes=data.get("resolution_notes"),
            retry_count=data.get("retry_count", 0)
        )


@dataclass
class ReconciliationResult:
    """Result of a reconciliation job run."""
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_memories_scanned: int = 0
    orphans_detected: int = 0
    soft_orphans: int = 0
    hard_orphans: int = 0
    dangling_refs: int = 0
    expired_memories: int = 0
    re_linked: int = 0
    flagged: int = 0
    cleaned: int = 0
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_memories_scanned": self.total_memories_scanned,
            "orphans_detected": self.orphans_detected,
            "soft_orphans": self.soft_orphans,
            "hard_orphans": self.hard_orphans,
            "dangling_refs": self.dangling_refs,
            "expired_memories": self.expired_memories,
            "re_linked": self.re_linked,
            "flagged": self.flagged,
            "cleaned": self.cleaned,
            "errors": self.errors
        }


class ReconciliationConfig(BaseModel):
    """Configuration for the orphan reconciler."""
    enable_auto_cleanup: bool = Field(default=False, description="Enable automatic cleanup")
    enable_auto_relink: bool = Field(default=True, description="Enable automatic re-linking")
    max_retries: int = Field(default=3, ge=1, le=10, description="Max recovery attempts")
    orphan_age_threshold_hours: int = Field(default=24, ge=1, description="Hours before orphan is processed")
    batch_size: int = Field(default=1000, ge=100, le=10000, description="Memories per batch")
    flag_threshold: int = Field(default=3, ge=1, description="Retries before flagging")
    
    class Config:
        env_prefix = "RECONCILIATION_"


class OrphanReconciler:
    """
    Orphaned memory reconciler for Cassandra AI.
    
    Handles detection and resolution of orphaned memories through
    a multi-stage process:
    1. Scan memory_archive for unmapped entries
    2. Check if associated tickets exist in DB1
    3. Attempt to re-link recoverable orphans
    4. Flag irrecoverable orphans for manual review
    5. Clean up dangling references
    
    Usage:
        reconciler = OrphanReconciler(memory_manager, db_pool)
        
        # Run full reconciliation
        result = await reconciler.run_reconciliation()
        
        # Or run specific phases
        orphans = await reconciler.detect_orphans()
        await reconciler.attempt_recovery(orphans)
        await reconciler.flag_irrecoverable(orphans)
    """
    
    def __init__(
        self,
        memory_manager: MemoryManager,
        db_pool: Any,
        config: Optional[ReconciliationConfig] = None,
        notification_client: Optional[Any] = None
    ):
        """
        Initialize the orphan reconciler.
        
        Args:
            memory_manager: MemoryManager instance
            db_pool: Database connection pool
            config: Reconciliation configuration
            notification_client: Optional notification client
        """
        self.memory_manager = memory_manager
        self.db_pool = db_pool
        self.config = config or ReconciliationConfig()
        self.notification_client = notification_client
        logger.info("OrphanReconciler initialized")
    
    async def run_reconciliation(self) -> ReconciliationResult:
        """
        Run the full reconciliation process.
        
        This is the main entry point for daily reconciliation jobs.
        
        Returns:
            ReconciliationResult with statistics
        """
        result = ReconciliationResult(started_at=datetime.utcnow())
        
        logger.info("Starting reconciliation job")
        
        try:
            # Phase 1: Detect orphans
            orphans = await self.detect_orphans(result)
            
            # Phase 2: Attempt recovery for soft orphans
            if self.config.enable_auto_relink:
                recoverable = [o for o in orphans if o.orphan_type == OrphanType.SOFT]
                re_linked = await self.attempt_recovery(recoverable)
                result.re_linked = len(re_linked)
            
            # Phase 3: Flag irrecoverable orphans
            irrecoverable = [o for o in orphans if o.orphan_type == OrphanType.HARD]
            flagged = await self.flag_irrecoverable(irrecoverable)
            result.flagged = len(flagged)
            
            # Phase 4: Clean up dangling references
            dangling = [o for o in orphans if o.orphan_type == OrphanType.DANGLING]
            cleaned = await self.cleanup_dangling(dangling)
            result.cleaned = len(cleaned)
            
            # Phase 5: Clean up expired memories
            if self.config.enable_auto_cleanup:
                expired_cleaned = await self.cleanup_expired()
                result.cleaned += expired_cleaned
            
        except Exception as e:
            logger.error(f"Reconciliation failed: {e}")
            result.errors.append(str(e))
        
        result.completed_at = datetime.utcnow()
        
        # Log summary
        duration = (result.completed_at - result.started_at).total_seconds()
        logger.info(
            f"Reconciliation completed in {duration:.2f}s: "
            f"scanned={result.total_memories_scanned}, "
            f"orphans={result.orphans_detected}, "
            f"re_linked={result.re_linked}, "
            f"flagged={result.flagged}, "
            f"cleaned={result.cleaned}"
        )
        
        # Send notification if issues found
        if result.orphans_detected > 0 and self.notification_client:
            await self._send_notification(result)
        
        return result
    
    async def detect_orphans(
        self, 
        result: Optional[ReconciliationResult] = None
    ) -> List[OrphanedMemory]:
        """
        Detect orphaned memories in the system.
        
        Scans memory_archive and identifies:
        - Soft orphans: Memories without map table entries
        - Hard orphans: Memories for deleted tickets
        - Dangling refs: Map entries without memories
        - Expired memories: Past their expiration date
        
        Args:
            result: Optional result object to update with statistics
            
        Returns:
            List of detected orphaned memories
        """
        orphans: List[OrphanedMemory] = []
        
        logger.info("Starting orphan detection")
        
        try:
            async with self.db_pool.acquire() as conn:
                # Count total memories
                count_row = await conn.fetchrow(
                    "SELECT COUNT(*) as count FROM memory_archive"
                )
                total_memories = count_row["count"] if count_row else 0
                
                if result:
                    result.total_memories_scanned = total_memories
                
                logger.info(f"Scanning {total_memories} memories for orphans")
                
                # Find soft orphans: memories without map entries
                soft_orphan_rows = await conn.fetch(
                    """
                    SELECT ma.* 
                    FROM memory_archive ma
                    LEFT JOIN memory_ticket_map mtm 
                        ON ma.memory_id = mtm.memory_id
                    WHERE mtm.memory_id IS NULL
                    AND ma.ticket_id IS NOT NULL
                    LIMIT $1
                    """,
                    self.config.batch_size
                )
                
                for row in soft_orphan_rows:
                    orphan = OrphanedMemory(
                        memory_id=row["memory_id"],
                        ticket_id=row["ticket_id"],
                        org_id=row["org_id"],
                        orphan_type=OrphanType.SOFT,
                        memory_content={
                            "content": row["content"],
                            "memory_type": row["memory_type"],
                            "created_at": row["created_at"].isoformat() if row["created_at"] else None
                        }
                    )
                    orphans.append(orphan)
                
                soft_count = len(soft_orphan_rows)
                if result:
                    result.soft_orphans = soft_count
                logger.info(f"Detected {soft_count} soft orphans")
                
                # Find hard orphans: memories for deleted tickets
                hard_orphan_rows = await conn.fetch(
                    """
                    SELECT ma.* 
                    FROM memory_archive ma
                    INNER JOIN memory_ticket_map mtm 
                        ON ma.memory_id = mtm.memory_id
                    LEFT JOIN tickets t 
                        ON mtm.db1_ticket_id = t.id
                    WHERE t.id IS NULL
                    LIMIT $1
                    """,
                    self.config.batch_size
                )
                
                for row in hard_orphan_rows:
                    orphan = OrphanedMemory(
                        memory_id=row["memory_id"],
                        ticket_id=row["ticket_id"],
                        org_id=row["org_id"],
                        orphan_type=OrphanType.HARD,
                        memory_content={
                            "content": row["content"],
                            "memory_type": row["memory_type"]
                        }
                    )
                    orphans.append(orphan)
                
                hard_count = len(hard_orphan_rows)
                if result:
                    result.hard_orphans = hard_count
                logger.info(f"Detected {hard_count} hard orphans")
                
                # Find dangling references: map entries without memories
                dangling_rows = await conn.fetch(
                    """
                    SELECT mtm.* 
                    FROM memory_ticket_map mtm
                    LEFT JOIN memory_archive ma 
                        ON mtm.memory_id = ma.memory_id
                    WHERE ma.memory_id IS NULL
                    LIMIT $1
                    """,
                    self.config.batch_size
                )
                
                for row in dangling_rows:
                    orphan = OrphanedMemory(
                        memory_id=row["memory_id"],
                        ticket_id=row["ticket_id"],
                        org_id=row["org_id"],
                        orphan_type=OrphanType.DANGLING
                    )
                    orphans.append(orphan)
                
                dangling_count = len(dangling_rows)
                if result:
                    result.dangling_refs = dangling_count
                logger.info(f"Detected {dangling_count} dangling references")
                
                # Find expired memories
                expired_rows = await conn.fetch(
                    """
                    SELECT * FROM memory_archive
                    WHERE expires_at IS NOT NULL 
                    AND expires_at < NOW()
                    LIMIT $1
                    """,
                    self.config.batch_size
                )
                
                for row in expired_rows:
                    orphan = OrphanedMemory(
                        memory_id=row["memory_id"],
                        ticket_id=row["ticket_id"],
                        org_id=row["org_id"],
                        orphan_type=OrphanType.EXPIRED,
                        memory_content={"expires_at": row["expires_at"].isoformat()}
                    )
                    orphans.append(orphan)
                
                expired_count = len(expired_rows)
                if result:
                    result.expired_memories = expired_count
                logger.info(f"Detected {expired_count} expired memories")
                
                # Update result totals
                if result:
                    result.orphans_detected = len(orphans)
                
                # Store detected orphans
                await self._store_orphans(orphans)
                
        except Exception as e:
            logger.error(f"Orphan detection failed: {e}")
            if result:
                result.errors.append(f"Detection failed: {e}")
        
        return orphans
    
    async def attempt_recovery(
        self, 
        orphans: List[OrphanedMemory]
    ) -> List[OrphanedMemory]:
        """
        Attempt to recover (re-link) soft orphans.
        
        For soft orphans, we try to:
        1. Find the ticket in DB1
        2. Create a new map table entry
        3. Update the memory with correct references
        
        Args:
            orphans: List of orphans to attempt recovery on
            
        Returns:
            List of successfully re-linked orphans
        """
        re_linked: List[OrphanedMemory] = []
        
        logger.info(f"Attempting recovery for {len(orphans)} soft orphans")
        
        for orphan in orphans:
            if orphan.retry_count >= self.config.max_retries:
                logger.warning(f"Max retries exceeded for {orphan.memory_id}")
                continue
            
            try:
                # Check if ticket exists in DB1
                async with self.db_pool.acquire() as conn:
                    ticket_row = await conn.fetchrow(
                        """
                        SELECT id FROM tickets
                        WHERE id = $1 AND org_id = $2
                        """,
                        orphan.ticket_id,
                        orphan.org_id
                    )
                    
                    if ticket_row:
                        # Ticket exists, create map entry
                        await conn.execute(
                            """
                            INSERT INTO memory_ticket_map (
                                memory_id, ticket_id, db1_ticket_id, org_id
                            ) VALUES ($1, $2, $3, $4)
                            ON CONFLICT (memory_id) DO NOTHING
                            """,
                            orphan.memory_id,
                            orphan.ticket_id,
                            ticket_row["id"],
                            orphan.org_id
                        )
                        
                        # Update orphan status
                        orphan.status = OrphanStatus.RE_LINKED
                        orphan.resolved_at = datetime.utcnow()
                        orphan.resolution_action = "created_map_entry"
                        orphan.retry_count += 1
                        
                        re_linked.append(orphan)
                        
                        logger.info(f"Re-linked orphan: {orphan.memory_id}")
                    else:
                        # Ticket doesn't exist, convert to hard orphan
                        orphan.orphan_type = OrphanType.HARD
                        orphan.retry_count += 1
                        logger.debug(f"Ticket not found for {orphan.memory_id}")
                
            except Exception as e:
                logger.error(f"Recovery failed for {orphan.memory_id}: {e}")
                orphan.retry_count += 1
        
        # Update stored orphan records
        await self._update_orphans(re_linked)
        
        logger.info(f"Successfully re-linked {len(re_linked)} orphans")
        return re_linked
    
    async def flag_irrecoverable(
        self, 
        orphans: List[OrphanedMemory]
    ) -> List[OrphanedMemory]:
        """
        Flag irrecoverable orphans for manual review.
        
        Hard orphans (tickets deleted) cannot be automatically recovered.
        They are flagged for manual review where an admin can:
        - Archive the memory
        - Reassign to a different ticket
        - Delete if no longer needed
        
        Args:
            orphans: List of hard orphans to flag
            
        Returns:
            List of flagged orphans
        """
        flagged: List[OrphanedMemory] = []
        
        logger.info(f"Flagging {len(orphans)} irrecoverable orphans")
        
        for orphan in orphans:
            # Check if already flagged
            if orphan.status == OrphanStatus.FLAGGED:
                continue
            
            # Check retry threshold
            if orphan.retry_count < self.config.flag_threshold:
                logger.debug(f"Skipping flag for {orphan.memory_id} (retries={orphan.retry_count})")
                continue
            
            try:
                # Update status
                orphan.status = OrphanStatus.FLAGGED
                orphan.resolved_at = None  # Not yet resolved
                orphan.resolution_notes = "Flagged for manual review"
                
                flagged.append(orphan)
                
                logger.info(f"Flagged orphan for review: {orphan.memory_id}")
                
            except Exception as e:
                logger.error(f"Flagging failed for {orphan.memory_id}: {e}")
        
        # Store flagged orphans
        await self._update_orphans(flagged)
        
        # Send notification
        if flagged and self.notification_client:
            await self._send_flag_notification(flagged)
        
        return flagged
    
    async def cleanup_dangling(self, orphans: List[OrphanedMemory]) -> List[OrphanedMemory]:
        """
        Clean up dangling map table references.
        
        Dangling references are map entries that point to non-existent
        memories. These can be safely deleted.
        
        Args:
            orphans: List of dangling references
            
        Returns:
            List of cleaned up references
        """
        cleaned: List[OrphanedMemory] = []
        
        logger.info(f"Cleaning up {len(orphans)} dangling references")
        
        for orphan in orphans:
            try:
                async with self.db_pool.acquire() as conn:
                    await conn.execute(
                        """
                        DELETE FROM memory_ticket_map
                        WHERE memory_id = $1
                        """,
                        orphan.memory_id
                    )
                
                orphan.status = OrphanStatus.CLEANED
                orphan.resolved_at = datetime.utcnow()
                orphan.resolution_action = "deleted_dangling_reference"
                
                cleaned.append(orphan)
                
                logger.debug(f"Cleaned dangling reference: {orphan.memory_id}")
                
            except Exception as e:
                logger.error(f"Cleanup failed for {orphan.memory_id}: {e}")
        
        await self._update_orphans(cleaned)
        
        return cleaned
    
    async def cleanup_expired(self) -> int:
        """
        Clean up expired memories.
        
        Returns:
            Number of expired memories cleaned up
        """
        cleaned_count = 0
        
        try:
            async with self.db_pool.acquire() as conn:
                # Archive before delete
                await conn.execute(
                    """
                    INSERT INTO memory_archive_expired
                    SELECT * FROM memory_archive
                    WHERE expires_at IS NOT NULL 
                    AND expires_at < NOW()
                    """
                )
                
                # Delete expired
                result = await conn.execute(
                    """
                    DELETE FROM memory_archive
                    WHERE expires_at IS NOT NULL 
                    AND expires_at < NOW()
                    """
                )
                
                cleaned_count = int(result.split()[1]) if result else 0
                
                logger.info(f"Cleaned up {cleaned_count} expired memories")
                
        except Exception as e:
            logger.error(f"Expired cleanup failed: {e}")
        
        return cleaned_count
    
    async def _store_orphans(self, orphans: List[OrphanedMemory]) -> None:
        """Store detected orphans in the orphan tracking table."""
        if not orphans:
            return
        
        try:
            async with self.db_pool.acquire() as conn:
                for orphan in orphans:
                    await conn.execute(
                        """
                        INSERT INTO orphaned_memories (
                            memory_id, ticket_id, org_id, orphan_type,
                            status, memory_content, detected_at, retry_count
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        ON CONFLICT (memory_id) DO UPDATE SET
                            status = EXCLUDED.status,
                            retry_count = orphaned_memories.retry_count + 1,
                            last_checked_at = NOW()
                        """,
                        orphan.memory_id,
                        orphan.ticket_id,
                        orphan.org_id,
                        orphan.orphan_type.value,
                        orphan.status.value,
                        json.dumps(orphan.memory_content) if orphan.memory_content else None,
                        orphan.detected_at,
                        orphan.retry_count
                    )
        except Exception as e:
            logger.error(f"Failed to store orphans: {e}")
    
    async def _update_orphans(self, orphans: List[OrphanedMemory]) -> None:
        """Update orphan records in the database."""
        if not orphans:
            return
        
        try:
            async with self.db_pool.acquire() as conn:
                for orphan in orphans:
                    await conn.execute(
                        """
                        UPDATE orphaned_memories
                        SET status = $1,
                            resolved_at = $2,
                            resolution_action = $3,
                            resolution_notes = $4,
                            retry_count = $5
                        WHERE memory_id = $6
                        """,
                        orphan.status.value,
                        orphan.resolved_at,
                        orphan.resolution_action,
                        orphan.resolution_notes,
                        orphan.retry_count,
                        orphan.memory_id
                    )
        except Exception as e:
            logger.error(f"Failed to update orphans: {e}")
    
    async def get_orphan_stats(
        self, 
        org_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get statistics about orphaned memories.
        
        Args:
            org_id: Optional organization filter
            
        Returns:
            Statistics dictionary
        """
        try:
            async with self.db_pool.acquire() as conn:
                if org_id:
                    row = await conn.fetchrow(
                        """
                        SELECT 
                            COUNT(*) as total,
                            COUNT(*) FILTER (WHERE status = 'detected') as detected,
                            COUNT(*) FILTER (WHERE status = 'flagged') as flagged,
                            COUNT(*) FILTER (WHERE orphan_type = 'soft') as soft,
                            COUNT(*) FILTER (WHERE orphan_type = 'hard') as hard
                        FROM orphaned_memories
                        WHERE org_id = $1
                        """,
                        org_id
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT 
                            COUNT(*) as total,
                            COUNT(*) FILTER (WHERE status = 'detected') as detected,
                            COUNT(*) FILTER (WHERE status = 'flagged') as flagged,
                            COUNT(*) FILTER (WHERE orphan_type = 'soft') as soft,
                            COUNT(*) FILTER (WHERE orphan_type = 'hard') as hard
                        FROM orphaned_memories
                        """
                    )
                
                return {
                    "total_orphans": row["total"] if row else 0,
                    "detected": row["detected"] if row else 0,
                    "flagged_for_review": row["flagged"] if row else 0,
                    "soft_orphans": row["soft"] if row else 0,
                    "hard_orphans": row["hard"] if row else 0
                }
        except Exception as e:
            logger.error(f"Failed to get orphan stats: {e}")
            return {"error": str(e)}
    
    async def resolve_flagged_orphan(
        self,
        memory_id: str,
        action: str,  # "archive", "delete", "reassign"
        notes: Optional[str] = None,
        new_ticket_id: Optional[str] = None
    ) -> bool:
        """
        Manually resolve a flagged orphan.
        
        Args:
            memory_id: The orphan to resolve
            action: Resolution action
            notes: Optional resolution notes
            new_ticket_id: Optional new ticket for reassignment
            
        Returns:
            True if successful
        """
        try:
            async with self.db_pool.acquire() as conn:
                if action == "delete":
                    # Delete the memory
                    await conn.execute(
                        "DELETE FROM memory_archive WHERE memory_id = $1",
                        memory_id
                    )
                elif action == "archive":
                    # Archive the memory
                    await conn.execute(
                        """
                        INSERT INTO memory_archive_orphaned
                        SELECT * FROM memory_archive WHERE memory_id = $1
                        """,
                        memory_id
                    )
                    await conn.execute(
                        "DELETE FROM memory_archive WHERE memory_id = $1",
                        memory_id
                    )
                elif action == "reassign" and new_ticket_id:
                    # Reassign to new ticket
                    await conn.execute(
                        """
                        UPDATE memory_archive
                        SET ticket_id = $1
                        WHERE memory_id = $2
                        """,
                        new_ticket_id,
                        memory_id
                    )
                    
                    # Create new map entry
                    await conn.execute(
                        """
                        INSERT INTO memory_ticket_map (memory_id, ticket_id, org_id)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (memory_id) DO UPDATE SET
                            ticket_id = EXCLUDED.ticket_id
                        """,
                        memory_id,
                        new_ticket_id,
                        (await conn.fetchrow(
                            "SELECT org_id FROM memory_archive WHERE memory_id = $1",
                            memory_id
                        ))["org_id"]
                    )
                
                # Update orphan record
                await conn.execute(
                    """
                    UPDATE orphaned_memories
                    SET status = 'resolved',
                        resolved_at = NOW(),
                        resolution_action = $1,
                        resolution_notes = $2
                    WHERE memory_id = $3
                    """,
                    action,
                    notes,
                    memory_id
                )
                
                logger.info(f"Resolved flagged orphan {memory_id}: {action}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to resolve orphan {memory_id}: {e}")
            return False
    
    async def _send_notification(self, result: ReconciliationResult) -> None:
        """Send notification about reconciliation results."""
        if not self.notification_client:
            return
        
        try:
            message = {
                "type": "reconciliation_complete",
                "timestamp": datetime.utcnow().isoformat(),
                "stats": result.to_dict()
            }
            
            await self.notification_client.send(
                channel="reconciliation",
                message=message
            )
        except Exception as e:
            logger.warning(f"Failed to send notification: {e}")
    
    async def _send_flag_notification(self, orphans: List[OrphanedMemory]) -> None:
        """Send notification about flagged orphans."""
        if not self.notification_client:
            return
        
        try:
            message = {
                "type": "orphans_flagged",
                "timestamp": datetime.utcnow().isoformat(),
                "count": len(orphans),
                "orphans": [o.to_dict() for o in orphans[:10]]  # Limit to first 10
            }
            
            await self.notification_client.send(
                channel="orphan_review",
                message=message
            )
        except Exception as e:
            logger.warning(f"Failed to send flag notification: {e}")


class ReconciliationError(Exception):
    """Raised when reconciliation operation fails."""
    pass


class DailyReconciliationJob:
    """
    T30: Daily cron job for orphaned memory cleanup.
    
    This class implements a scheduled daily job that:
    1. Detects orphaned memories (soft, hard, dangling, expired)
    2. Attempts auto re-linking for soft orphans
    3. Flags irrecoverable orphans for manual review
    4. Generates daily metrics and reports
    
    The job is designed to run once per day during low-traffic hours.
    
    Usage:
        # Initialize the job
        job = DailyReconciliationJob(reconciler, metrics_client)
        
        # Run manually
        result = await job.run()
        
        # Or schedule with APScheduler
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()
        scheduler.add_job(job.run, 'cron', hour=2, minute=0)
        scheduler.start()
    """
    
    def __init__(
        self,
        reconciler: OrphanReconciler,
        metrics_client: Optional[Any] = None,
        notification_client: Optional[Any] = None,
        run_hour: int = 2,  # 2 AM
        run_minute: int = 0
    ):
        """
        Initialize the daily reconciliation job.
        
        Args:
            reconciler: OrphanReconciler instance
            metrics_client: Optional metrics client for reporting
            notification_client: Optional notification client for alerts
            run_hour: Hour to run the job (0-23)
            run_minute: Minute to run the job (0-59)
        """
        self.reconciler = reconciler
        self.metrics_client = metrics_client
        self.notification_client = notification_client
        self.run_hour = run_hour
        self.run_minute = run_minute
        self._last_run: Optional[datetime] = None
        self._last_result: Optional[ReconciliationResult] = None
        self._daily_metrics: List[Dict[str, Any]] = []
        
        logger.info(
            f"DailyReconciliationJob initialized (scheduled: {run_hour:02d}:{run_minute:02d})"
        )
    
    async def run(self) -> ReconciliationResult:
        """
        T30: Execute the daily reconciliation job.
        
        This method runs the full reconciliation pipeline:
        1. Detect all orphan types
        2. Auto re-link soft orphans
        3. Flag irrecoverable orphans
        4. Clean up dangling references
        5. Generate and store metrics
        
        Returns:
            ReconciliationResult with full statistics
            
        Example:
            job = DailyReconciliationJob(reconciler)
            result = await job.run()
            
            print(f"Scanned: {result.total_memories_scanned}")
            print(f"Orphans detected: {result.orphans_detected}")
            print(f"Re-linked: {result.re_linked}")
            print(f"Flagged: {result.flagged}")
        """
        logger.info("T30: Starting daily reconciliation job")
        
        start_time = datetime.utcnow()
        
        try:
            # Run the full reconciliation
            result = await self.reconciler.run_reconciliation()
            
            # Store result
            self._last_result = result
            self._last_run = datetime.utcnow()
            
            # Generate daily metrics
            metrics = self._generate_daily_metrics(result, start_time)
            self._daily_metrics.append(metrics)
            
            # Send metrics if client available
            if self.metrics_client:
                await self._send_metrics(metrics)
            
            # Send notification if issues found
            if result.orphans_detected > 0 and self.notification_client:
                await self._send_daily_report(result, metrics)
            
            duration = (datetime.utcnow() - start_time).total_seconds()
            logger.info(
                f"T30: Daily reconciliation completed in {duration:.2f}s | "
                f"Orphans: {result.orphans_detected}, "
                f"Re-linked: {result.re_linked}, "
                f"Flagged: {result.flagged}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"T30: Daily reconciliation failed: {e}")
            
            # Send error notification
            if self.notification_client:
                await self._send_error_notification(e)
            
            raise ReconciliationError(f"Daily reconciliation failed: {e}")
    
    def _generate_daily_metrics(
        self,
        result: ReconciliationResult,
        start_time: datetime
    ) -> Dict[str, Any]:
        """
        T30: Generate daily metrics from reconciliation result.
        
        Args:
            result: Reconciliation result
            start_time: When the job started
            
        Returns:
            Dictionary of metrics
        """
        duration = (result.completed_at - start_time).total_seconds() if result.completed_at else 0
        
        # Calculate orphan rate
        orphan_rate = (
            result.orphans_detected / result.total_memories_scanned * 100
            if result.total_memories_scanned > 0 else 0
        )
        
        # Calculate recovery rate
        soft_orphans = result.soft_orphans
        recovery_rate = (
            result.re_linked / soft_orphans * 100
            if soft_orphans > 0 else 0
        )
        
        metrics = {
            "date": start_time.strftime("%Y-%m-%d"),
            "timestamp": start_time.isoformat(),
            "duration_seconds": round(duration, 2),
            "memories_scanned": result.total_memories_scanned,
            "orphans_detected": result.orphans_detected,
            "orphan_rate_percent": round(orphan_rate, 2),
            "by_type": {
                "soft": result.soft_orphans,
                "hard": result.hard_orphans,
                "dangling": result.dangling_refs,
                "expired": result.expired_memories
            },
            "actions_taken": {
                "re_linked": result.re_linked,
                "flagged": result.flagged,
                "cleaned": result.cleaned
            },
            "recovery_rate_percent": round(recovery_rate, 2),
            "errors": len(result.errors),
            "error_details": result.errors[:5]  # First 5 errors
        }
        
        return metrics
    
    async def _send_metrics(self, metrics: Dict[str, Any]) -> None:
        """Send metrics to metrics client."""
        if not self.metrics_client:
            return
        
        try:
            await self.metrics_client.gauge(
                "reconciliation.orphans_detected",
                metrics["orphans_detected"],
                tags={"date": metrics["date"]}
            )
            await self.metrics_client.gauge(
                "reconciliation.orphan_rate",
                metrics["orphan_rate_percent"],
                tags={"date": metrics["date"]}
            )
            await self.metrics_client.gauge(
                "reconciliation.recovery_rate",
                metrics["recovery_rate_percent"],
                tags={"date": metrics["date"]}
            )
            logger.debug("T30: Metrics sent successfully")
        except Exception as e:
            logger.warning(f"T30: Failed to send metrics: {e}")
    
    async def _send_daily_report(
        self,
        result: ReconciliationResult,
        metrics: Dict[str, Any]
    ) -> None:
        """Send daily reconciliation report."""
        if not self.notification_client:
            return
        
        try:
            message = {
                "type": "daily_reconciliation_report",
                "date": metrics["date"],
                "summary": {
                    "memories_scanned": result.total_memories_scanned,
                    "orphans_detected": result.orphans_detected,
                    "orphan_rate": f"{metrics['orphan_rate_percent']:.2f}%",
                    "re_linked": result.re_linked,
                    "flagged": result.flagged,
                    "cleaned": result.cleaned,
                    "recovery_rate": f"{metrics['recovery_rate_percent']:.2f}%"
                },
                "by_type": metrics["by_type"],
                "duration_seconds": metrics["duration_seconds"],
                "requires_attention": result.flagged > 0
            }
            
            await self.notification_client.send(
                channel="reconciliation_daily",
                message=message
            )
            
            logger.info("T30: Daily report sent")
        except Exception as e:
            logger.warning(f"T30: Failed to send daily report: {e}")
    
    async def _send_error_notification(self, error: Exception) -> None:
        """Send error notification."""
        if not self.notification_client:
            return
        
        try:
            await self.notification_client.send(
                channel="reconciliation_errors",
                message={
                    "type": "reconciliation_failed",
                    "error": str(error),
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
        except Exception as e:
            logger.warning(f"T30: Failed to send error notification: {e}")
    
    def get_daily_metrics(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Get daily metrics within a date range.
        
        Args:
            start_date: Start of date range
            end_date: End of date range
            
        Returns:
            List of daily metrics
        """
        metrics = self._daily_metrics
        
        if start_date:
            metrics = [m for m in metrics 
                      if datetime.fromisoformat(m["timestamp"]) >= start_date]
        
        if end_date:
            metrics = [m for m in metrics 
                      if datetime.fromisoformat(m["timestamp"]) <= end_date]
        
        return metrics
    
    def get_orphan_detection_query(self) -> str:
        """
        T30: Get the orphan detection SQL query.
        
        Returns:
            SQL query string for detecting orphans
        """
        return """
        -- T30: Orphan Detection Query
        -- Detects soft orphans, hard orphans, and dangling references
        
        WITH soft_orphans AS (
            -- Memories without map entries but with ticket references
            SELECT 
                ma.memory_id,
                ma.ticket_id,
                ma.org_id,
                'soft' as orphan_type,
                ma.created_at,
                ma.content
            FROM memory_archive ma
            LEFT JOIN memory_ticket_map mtm ON ma.memory_id = mtm.memory_id
            WHERE mtm.memory_id IS NULL
            AND ma.ticket_id IS NOT NULL
        ),
        hard_orphans AS (
            -- Memories for deleted tickets
            SELECT 
                ma.memory_id,
                ma.ticket_id,
                ma.org_id,
                'hard' as orphan_type,
                ma.created_at,
                ma.content
            FROM memory_archive ma
            INNER JOIN memory_ticket_map mtm ON ma.memory_id = mtm.memory_id
            LEFT JOIN tickets t ON mtm.db1_ticket_id = t.id
            WHERE t.id IS NULL
        ),
        dangling_refs AS (
            -- Map entries without memories
            SELECT 
                mtm.memory_id,
                mtm.ticket_id,
                mtm.org_id,
                'dangling' as orphan_type,
                mtm.created_at,
                NULL as content
            FROM memory_ticket_map mtm
            LEFT JOIN memory_archive ma ON mtm.memory_id = ma.memory_id
            WHERE ma.memory_id IS NULL
        ),
        expired_memories AS (
            -- Memories past expiration
            SELECT 
                memory_id,
                ticket_id,
                org_id,
                'expired' as orphan_type,
                created_at,
                content
            FROM memory_archive
            WHERE expires_at IS NOT NULL 
            AND expires_at < NOW()
        )
        SELECT * FROM soft_orphans
        UNION ALL
        SELECT * FROM hard_orphans
        UNION ALL
        SELECT * FROM dangling_refs
        UNION ALL
        SELECT * FROM expired_memories
        ORDER BY orphan_type, created_at;
        """
    
    def get_scheduler_config(self) -> Dict[str, Any]:
        """
        Get APScheduler configuration for the daily job.
        
        Returns:
            Dictionary with scheduler configuration
        """
        return {
            "trigger": "cron",
            "hour": self.run_hour,
            "minute": self.run_minute,
            "id": "daily_reconciliation",
            "name": "Daily Orphaned Memory Reconciliation",
            "replace_existing": True,
            "misfire_grace_time": 3600  # 1 hour grace period
        }
    
    @property
    def last_run(self) -> Optional[datetime]:
        """Get the last run timestamp."""
        return self._last_run
    
    @property
    def last_result(self) -> Optional[ReconciliationResult]:
        """Get the last reconciliation result."""
        return self._last_result


async def run_daily_reconciliation(
    reconciler: OrphanReconciler,
    metrics_client: Optional[Any] = None,
    notification_client: Optional[Any] = None
) -> ReconciliationResult:
    """
    T30: Convenience function to run daily reconciliation.
    
    Args:
        reconciler: OrphanReconciler instance
        metrics_client: Optional metrics client
        notification_client: Optional notification client
        
    Returns:
        ReconciliationResult
        
    Example:
        result = await run_daily_reconciliation(
            reconciler=reconciler,
            metrics_client=datadog_client,
            notification_client=slack_client
        )
    """
    job = DailyReconciliationJob(
        reconciler=reconciler,
        metrics_client=metrics_client,
        notification_client=notification_client
    )
    return await job.run()
