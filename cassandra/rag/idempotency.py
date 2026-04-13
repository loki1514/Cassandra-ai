"""
Idempotency Module

Handles idempotency key generation and deduplication for the Cassandra AI system.
Ensures that events are processed exactly once, even if received multiple times
due to retries, network issues, or duplicate submissions.

Key Features:
- Deterministic idempotency key generation
- 5-minute time bucketing for deduplication windows
- Check-before-write pattern
- Automatic cleanup of expired keys
- Support for multiple entity types

Architecture:
- Keys are generated from entity_id + event_type + time_bucket
- Keys are stored in idempotency_store table
- 5-minute buckets allow for natural deduplication windows
- Keys expire after 24 hours to prevent unbounded growth
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Types of events that can be deduplicated."""
    TICKET_CREATED = "ticket_created"
    TICKET_UPDATED = "ticket_updated"
    TICKET_RESOLVED = "ticket_resolved"
    TICKET_ASSIGNED = "ticket_assigned"
    COMMENT_ADDED = "comment_added"
    DECISION_RECORDED = "decision_recorded"
    MEMORY_CREATED = "memory_created"
    USER_ACTION = "user_action"
    SYSTEM_EVENT = "system_event"
    WEBHOOK_RECEIVED = "webhook_received"
    API_CALL = "api_call"


class IdempotencyStatus(str, Enum):
    """Status of an idempotency check."""
    NEW = "new"                    # Never seen before
    PROCESSING = "processing"      # Currently being processed
    PROCESSED = "processed"        # Successfully processed
    FAILED = "failed"              # Processing failed
    DUPLICATE = "duplicate"        # Already processed (exact duplicate)


@dataclass
class IdempotencyKey:
    """
    Represents an idempotency key with its metadata.
    
    Attributes:
        key: The unique idempotency key string
        entity_id: The entity this key refers to
        event_type: Type of event
        time_bucket: The 5-minute time bucket
        created_at: When the key was first created
        expires_at: When the key expires (24h default)
        status: Current processing status
        result_data: Optional stored result for replay
    """
    key: str
    entity_id: str
    event_type: EventType
    time_bucket: datetime
    created_at: datetime
    expires_at: datetime
    status: IdempotencyStatus = IdempotencyStatus.NEW
    result_data: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "key": self.key,
            "entity_id": self.entity_id,
            "event_type": self.event_type.value,
            "time_bucket": self.time_bucket.isoformat(),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status.value,
            "result_data": self.result_data
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IdempotencyKey':
        """Create from dictionary."""
        return cls(
            key=data["key"],
            entity_id=data["entity_id"],
            event_type=EventType(data["event_type"]),
            time_bucket=datetime.fromisoformat(data["time_bucket"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            status=IdempotencyStatus(data.get("status", "new")),
            result_data=data.get("result_data")
        )


class IdempotencyConfig(BaseModel):
    """Configuration for idempotency handling."""
    bucket_minutes: int = Field(default=5, ge=1, le=60)
    key_expiry_hours: int = Field(default=24, ge=1, le=168)
    max_retries: int = Field(default=3, ge=1, le=10)
    enable_result_caching: bool = Field(default=True)
    cleanup_interval_hours: int = Field(default=6)
    
    class Config:
        env_prefix = "IDEMPOTENCY_"


def generate_idempotency_key(
    entity_id: str,
    event_type: Union[EventType, str],
    timestamp: Optional[datetime] = None,
    bucket_minutes: int = 5,
    additional_data: Optional[Dict[str, Any]] = None,
    use_simple_formula: bool = False
) -> str:
    """
    Generate a deterministic idempotency key.
    
    The key is generated from:
    - entity_id: The unique entity identifier
    - event_type: Type of event (for namespacing)
    - time_bucket: 5-minute bucket of the timestamp
    - additional_data: Optional extra data for uniqueness
    
    This ensures that events within the same 5-minute window
    for the same entity and type will generate identical keys,
    enabling deduplication.
    
    T16 Implementation: Supports the simple SHA256 formula:
    SHA256(entity_id + event_type + floor(timestamp / 300))
    
    Args:
        entity_id: Unique entity identifier (e.g., ticket ID)
        event_type: Type of event being processed
        timestamp: Event timestamp (defaults to now)
        bucket_minutes: Time bucket size in minutes (default 5)
        additional_data: Optional additional data to include in key
        use_simple_formula: Use T16 simple formula instead of full formula
        
    Returns:
        Deterministic idempotency key string
        
    Example:
        >>> key = generate_idempotency_key(
        ...     entity_id="TICKET-123",
        ...     event_type=EventType.TICKET_UPDATED,
        ...     timestamp=datetime(2024, 1, 15, 10, 23, 45)
        ... )
        >>> print(key)
        'idemp:ticket_updated:a1b2c3d4...'
    """
    # Normalize event type
    if isinstance(event_type, EventType):
        event_type_str = event_type.value
    else:
        event_type_str = str(event_type)
    
    # Use current time if not provided
    if timestamp is None:
        timestamp = datetime.utcnow()
    
    # T16: Simple formula mode - SHA256(entity_id + event_type + floor(timestamp / 300))
    if use_simple_formula:
        # Calculate 5-minute bucket (300 seconds)
        timestamp_seconds = int(timestamp.timestamp())
        time_bucket = timestamp_seconds // (bucket_minutes * 60)
        
        # Build simple key input
        key_input = f"{entity_id}:{event_type_str}:{time_bucket}"
        key_hash = hashlib.sha256(key_input.encode()).hexdigest()
        
        idempotency_key = f"idemp:{event_type_str}:{key_hash[:32]}"
        logger.debug(f"Generated idempotency key (simple): {idempotency_key}")
        return idempotency_key
    
    # Standard formula mode
    # Calculate time bucket (floor to bucket_minutes)
    # e.g., 10:23:45 with 5-min bucket -> 10:20:00
    bucket_number = (
        timestamp.hour * 60 + timestamp.minute
    ) // bucket_minutes
    
    time_bucket = timestamp.replace(
        minute=(bucket_number * bucket_minutes) % 60,
        second=0,
        microsecond=0
    )
    
    if bucket_number * bucket_minutes >= 60:
        time_bucket = time_bucket.replace(hour=timestamp.hour)
    
    # Build key components
    key_components = [
        entity_id,
        event_type_str,
        time_bucket.strftime("%Y%m%d%H%M")
    ]
    
    # Add additional data if provided (sorted for consistency)
    if additional_data:
        additional_str = json.dumps(additional_data, sort_keys=True)
        key_components.append(additional_str)
    
    # Generate hash
    key_input = "|".join(key_components)
    key_hash = hashlib.sha256(key_input.encode()).hexdigest()[:32]
    
    # Format: idemp:<event_type>:<hash>
    idempotency_key = f"idemp:{event_type_str}:{key_hash}"
    
    logger.debug(f"Generated idempotency key: {idempotency_key} "
                f"(bucket: {time_bucket.isoformat()})")
    
    return idempotency_key


def get_time_bucket(
    timestamp: Optional[datetime] = None,
    bucket_minutes: int = 5
) -> datetime:
    """
    Get the time bucket for a given timestamp.
    
    Args:
        timestamp: The timestamp to bucket (defaults to now)
        bucket_minutes: Bucket size in minutes
        
    Returns:
        The floor time bucket
    """
    if timestamp is None:
        timestamp = datetime.utcnow()
    
    bucket_number = (
        timestamp.hour * 60 + timestamp.minute
    ) // bucket_minutes
    
    time_bucket = timestamp.replace(
        minute=(bucket_number * bucket_minutes) % 60,
        second=0,
        microsecond=0
    )
    
    if bucket_number * bucket_minutes >= 60:
        time_bucket = time_bucket.replace(hour=timestamp.hour)
    
    return time_bucket


class IdempotencyStore:
    """
    Store for managing idempotency keys.
    
    Provides:
    - Check-before-write pattern
    - Status tracking (new/processing/processed/failed)
    - Result caching for replay
    - Automatic cleanup of expired keys
    
    Usage:
        store = IdempotencyStore(db_pool)
        
        # Check if event should be processed
        should_process, key = await store.check_idempotency(
            entity_id="TICKET-123",
            event_type=EventType.TICKET_UPDATED
        )
        
        if should_process:
            # Process the event
            result = await process_event(data)
            
            # Mark as processed with result
            await store.mark_processed(key, result)
        else:
            # Get cached result if available
            result = await store.get_result(key)
    """
    
    def __init__(
        self,
        db_pool: Any,
        config: Optional[IdempotencyConfig] = None,
        cache_client: Optional[Any] = None
    ):
        """
        Initialize the idempotency store.
        
        Args:
            db_pool: Database connection pool
            config: Idempotency configuration
            cache_client: Optional cache client for fast lookups
        """
        self.db_pool = db_pool
        self.config = config or IdempotencyConfig()
        self.cache_client = cache_client
        logger.info("IdempotencyStore initialized")
    
    async def check_idempotency(
        self,
        entity_id: str,
        event_type: Union[EventType, str],
        timestamp: Optional[datetime] = None,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        """
        Check if an event should be processed (check-before-write).
        
        Returns a tuple of (should_process, idempotency_key):
        - should_process: True if event should be processed, False if duplicate
        - idempotency_key: The generated/retrieved key
        
        Args:
            entity_id: Unique entity identifier
            event_type: Type of event
            timestamp: Event timestamp
            additional_data: Additional uniqueness data
            
        Returns:
            Tuple of (should_process, idempotency_key)
            
        Example:
            should_process, key = await store.check_idempotency(
                entity_id="TICKET-123",
                event_type=EventType.TICKET_UPDATED
            )
            
            if should_process:
                # Process and mark complete
                result = await process()
                await store.mark_processed(key, result)
            else:
                # Skip - already processed
                cached_result = await store.get_result(key)
        """
        # Generate the idempotency key
        idempotency_key = generate_idempotency_key(
            entity_id=entity_id,
            event_type=event_type,
            timestamp=timestamp,
            bucket_minutes=self.config.bucket_minutes,
            additional_data=additional_data
        )
        
        # Check cache first (fast path)
        if self.cache_client:
            cached_status = await self._check_cache(idempotency_key)
            if cached_status == IdempotencyStatus.PROCESSED:
                logger.debug(f"Cache hit: {idempotency_key} already processed")
                return False, idempotency_key
            elif cached_status == IdempotencyStatus.PROCESSING:
                logger.warning(f"Event {idempotency_key} is already being processed")
                return False, idempotency_key
        
        # Check database
        try:
            async with self.db_pool.acquire() as conn:
                # Try to insert the key (will fail if exists)
                time_bucket = get_time_bucket(timestamp, self.config.bucket_minutes)
                expires_at = datetime.utcnow() + timedelta(
                    hours=self.config.key_expiry_hours
                )
                
                row = await conn.fetchrow(
                    """
                    INSERT INTO idempotency_store (
                        key, entity_id, event_type, time_bucket,
                        created_at, expires_at, status
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (key) DO UPDATE SET
                        status = CASE 
                            WHEN idempotency_store.status = 'failed' THEN 'processing'
                            ELSE idempotency_store.status 
                        END,
                        updated_at = NOW()
                    RETURNING status, created_at
                    """,
                    idempotency_key,
                    entity_id,
                    event_type.value if isinstance(event_type, EventType) else event_type,
                    time_bucket,
                    datetime.utcnow(),
                    expires_at,
                    IdempotencyStatus.PROCESSING.value
                )
                
                status = IdempotencyStatus(row["status"])
                
                if status == IdempotencyStatus.NEW or status == IdempotencyStatus.PROCESSING:
                    # This is a new event or retry of failed event
                    logger.debug(f"New event: {idempotency_key}")
                    
                    # Update cache
                    if self.cache_client:
                        await self._update_cache(idempotency_key, status)
                    
                    return True, idempotency_key
                else:
                    # Already processed or duplicate
                    logger.debug(f"Duplicate event: {idempotency_key} (status: {status.value})")
                    return False, idempotency_key
                    
        except Exception as e:
            logger.error(f"Idempotency check failed: {e}")
            # SECURITY: Fail closed - reject processing on DB errors
            # This prevents duplicate ticket creation when idempotency check fails
            return False, idempotency_key
    
    async def mark_processing(self, idempotency_key: str) -> bool:
        """
        Mark an idempotency key as currently processing.
        
        Args:
            idempotency_key: The key to mark
            
        Returns:
            True if successful, False otherwise
        """
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE idempotency_store
                    SET status = $1, updated_at = NOW()
                    WHERE key = $2
                    """,
                    IdempotencyStatus.PROCESSING.value,
                    idempotency_key
                )
                
                if self.cache_client:
                    await self._update_cache(
                        idempotency_key, 
                        IdempotencyStatus.PROCESSING
                    )
                
                return True
        except Exception as e:
            logger.error(f"Failed to mark processing: {e}")
            return False
    
    async def mark_processed(
        self,
        idempotency_key: str,
        result_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Mark an idempotency key as successfully processed.
        
        Args:
            idempotency_key: The key to mark
            result_data: Optional result data to cache
            
        Returns:
            True if successful, False otherwise
        """
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE idempotency_store
                    SET status = $1, 
                        result_data = $2,
                        processed_at = NOW(),
                        updated_at = NOW()
                    WHERE key = $3
                    """,
                    IdempotencyStatus.PROCESSED.value,
                    json.dumps(result_data) if result_data else None,
                    idempotency_key
                )
                
                if self.cache_client:
                    await self._update_cache(
                        idempotency_key,
                        IdempotencyStatus.PROCESSED,
                        result_data
                    )
                
                logger.debug(f"Marked processed: {idempotency_key}")
                return True
        except Exception as e:
            logger.error(f"Failed to mark processed: {e}")
            return False
    
    async def mark_failed(
        self,
        idempotency_key: str,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Mark an idempotency key as failed.
        
        Failed events can be retried later.
        
        Args:
            idempotency_key: The key to mark
            error_message: Optional error message
            
        Returns:
            True if successful, False otherwise
        """
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE idempotency_store
                    SET status = $1,
                        error_message = $2,
                        updated_at = NOW()
                    WHERE key = $3
                    """,
                    IdempotencyStatus.FAILED.value,
                    error_message,
                    idempotency_key
                )
                
                if self.cache_client:
                    await self._update_cache(
                        idempotency_key,
                        IdempotencyStatus.FAILED
                    )
                
                logger.debug(f"Marked failed: {idempotency_key}")
                return True
        except Exception as e:
            logger.error(f"Failed to mark failed: {e}")
            return False
    
    async def get_result(
        self, 
        idempotency_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached result for a processed idempotency key.
        
        Args:
            idempotency_key: The key to lookup
            
        Returns:
            Cached result data or None
        """
        # Check cache first
        if self.cache_client:
            cached = await self.cache_client.get(f"idemp:result:{idempotency_key}")
            if cached:
                return json.loads(cached)
        
        # Check database
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT result_data 
                    FROM idempotency_store 
                    WHERE key = $1 AND status = $2
                    """,
                    idempotency_key,
                    IdempotencyStatus.PROCESSED.value
                )
                
                if row and row["result_data"]:
                    return json.loads(row["result_data"])
        except Exception as e:
            logger.error(f"Failed to get result: {e}")
        
        return None
    
    async def get_status(
        self, 
        idempotency_key: str
    ) -> Optional[IdempotencyStatus]:
        """
        Get the current status of an idempotency key.
        
        Args:
            idempotency_key: The key to lookup
            
        Returns:
            Current status or None if not found
        """
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT status FROM idempotency_store WHERE key = $1",
                    idempotency_key
                )
                
                if row:
                    return IdempotencyStatus(row["status"])
        except Exception as e:
            logger.error(f"Failed to get status: {e}")
        
        return None
    
    async def cleanup_expired(self) -> int:
        """
        Clean up expired idempotency keys.
        
        Returns:
            Number of keys deleted
        """
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute(
                    """
                    DELETE FROM idempotency_store 
                    WHERE expires_at < NOW()
                    """
                )
                
                # Parse "DELETE N" format
                deleted = int(result.split()[1]) if result else 0
                
                logger.info(f"Cleaned up {deleted} expired idempotency keys")
                return deleted
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
            return 0
    
    async def _check_cache(self, idempotency_key: str) -> Optional[IdempotencyStatus]:
        """Check cache for idempotency key status."""
        if not self.cache_client:
            return None
        
        try:
            cached = await self.cache_client.get(f"idemp:status:{idempotency_key}")
            if cached:
                return IdempotencyStatus(cached.decode())
        except Exception as e:
            logger.warning(f"Cache check failed: {e}")
        
        return None
    
    async def _update_cache(
        self,
        idempotency_key: str,
        status: IdempotencyStatus,
        result_data: Optional[Dict[str, Any]] = None
    ) -> None:
        """Update cache with idempotency key status."""
        if not self.cache_client:
            return
        
        try:
            # Cache status
            await self.cache_client.setex(
                f"idemp:status:{idempotency_key}",
                self.config.key_expiry_hours * 3600,
                status.value
            )
            
            # Cache result if provided
            if result_data and self.config.enable_result_caching:
                await self.cache_client.setex(
                    f"idemp:result:{idempotency_key}",
                    self.config.key_expiry_hours * 3600,
                    json.dumps(result_data)
                )
        except Exception as e:
            logger.warning(f"Cache update failed: {e}")
    
    # T16: Memory Archive Idempotency Methods
    
    async def check_memory_archive_idempotency(
        self,
        idempotency_key: str,
        org_id: str
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Check idempotency in memory_archive table with UNIQUE constraint.
        
        T16 Implementation:
        - Uses memory_archive.idempotency_key UNIQUE constraint
        - Returns (should_process, existing_memory_id, cached_result)
        - Check-before-write pattern
        
        Args:
            idempotency_key: The idempotency key to check
            org_id: Organization ID for scoping
            
        Returns:
            Tuple of (should_process, existing_memory_id, cached_result)
            - should_process: True if should proceed, False if duplicate
            - existing_memory_id: Memory ID if duplicate, None otherwise
            - cached_result: Cached result data if available
        """
        try:
            async with self.db_pool.acquire() as conn:
                # Check if idempotency key exists in memory_archive
                row = await conn.fetchrow(
                    """
                    SELECT memory_id, content, memory_type, created_at, metadata
                    FROM memory_archive
                    WHERE idempotency_key = $1 AND org_id = $2
                    """,
                    idempotency_key,
                    org_id
                )
                
                if row:
                    # Duplicate found
                    logger.info(
                        f"Idempotency hit in memory_archive: key={idempotency_key}, "
                        f"memory_id={row['memory_id']}"
                    )
                    
                    # Build cached result
                    cached_result = {
                        "memory_id": row["memory_id"],
                        "content": row["content"],
                        "memory_type": row["memory_type"],
                        "created_at": row["created_at"].isoformat() if isinstance(row["created_at"], datetime) else row["created_at"],
                        "metadata": row["metadata"]
                    }
                    
                    return False, row["memory_id"], cached_result
                
                # No duplicate found - should proceed
                return True, None, None
                
        except Exception as e:
            logger.error(f"Memory archive idempotency check failed: {e}")
            # SECURITY: Fail closed - reject processing on DB errors
            # This prevents duplicate memory writes when idempotency check fails
            return False, None, None
    
    async def write_memory_with_idempotency(
        self,
        idempotency_key: str,
        org_id: str,
        memory_id: str,
        content: Any,
        memory_type: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Write memory to archive with idempotency check.
        
        T16 Implementation:
        - Attempts INSERT with UNIQUE constraint on idempotency_key
        - If conflict, returns existing memory_id
        - If success, returns new memory_id
        
        Args:
            idempotency_key: Idempotency key for deduplication
            org_id: Organization ID
            memory_id: New memory ID to use
            content: Memory content
            memory_type: Type of memory
            metadata: Optional metadata
            
        Returns:
            Tuple of (is_new, memory_id)
            - is_new: True if new insert, False if duplicate
            - memory_id: The memory ID (new or existing)
        """
        try:
            async with self.db_pool.acquire() as conn:
                # Attempt INSERT with ON CONFLICT to handle duplicates
                row = await conn.fetchrow(
                    """
                    INSERT INTO memory_archive (
                        memory_id, content, memory_type, org_id,
                        metadata, idempotency_key, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (idempotency_key) DO UPDATE SET
                        -- This shouldn't happen due to UNIQUE, but handle gracefully
                        updated_at = NOW()
                    RETURNING memory_id, 
                        CASE WHEN xmax = 0 THEN 'new' ELSE 'existing' END as insert_status
                    """,
                    memory_id,
                    json.dumps(content) if isinstance(content, (dict, list)) else content,
                    memory_type,
                    org_id,
                    json.dumps(metadata) if metadata else None,
                    idempotency_key,
                    datetime.utcnow()
                )
                
                is_new = row["insert_status"] == "new"
                returned_memory_id = row["memory_id"]
                
                if is_new:
                    logger.debug(f"New memory written: {returned_memory_id}")
                else:
                    logger.info(f"Duplicate detected, existing memory: {returned_memory_id}")
                
                return is_new, returned_memory_id
                
        except Exception as e:
            # Check if it's a unique constraint violation
            if "unique constraint" in str(e).lower() or "duplicate" in str(e).lower():
                # Try to get the existing memory_id
                try:
                    async with self.db_pool.acquire() as conn:
                        row = await conn.fetchrow(
                            """
                            SELECT memory_id FROM memory_archive
                            WHERE idempotency_key = $1 AND org_id = $2
                            """,
                            idempotency_key,
                            org_id
                        )
                        if row:
                            logger.info(f"Duplicate detected via exception: {row['memory_id']}")
                            return False, row["memory_id"]
                except Exception as inner_e:
                    logger.error(f"Failed to retrieve existing memory: {inner_e}")
            
            logger.error(f"Memory write with idempotency failed: {e}")
            raise
    
    async def get_memory_by_idempotency_key(
        self,
        idempotency_key: str,
        org_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve memory by idempotency key.
        
        T16 Test: Duplicate call returns existing memory_id
        
        Args:
            idempotency_key: The idempotency key
            org_id: Organization ID for scoping
            
        Returns:
            Memory data if found, None otherwise
        """
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT memory_id, content, memory_type, created_at, 
                           metadata, ticket_id, encrypted
                    FROM memory_archive
                    WHERE idempotency_key = $1 AND org_id = $2
                    """,
                    idempotency_key,
                    org_id
                )
                
                if row:
                    return {
                        "memory_id": row["memory_id"],
                        "content": row["content"],
                        "memory_type": row["memory_type"],
                        "created_at": row["created_at"].isoformat() if isinstance(row["created_at"], datetime) else row["created_at"],
                        "metadata": row["metadata"],
                        "ticket_id": row["ticket_id"],
                        "encrypted": row["encrypted"]
                    }
                
                return None
                
        except Exception as e:
            logger.error(f"Failed to get memory by idempotency key: {e}")
            return None


async def check_idempotency(
    entity_id: str,
    event_type: Union[EventType, str],
    db_pool: Any,
    timestamp: Optional[datetime] = None,
    additional_data: Optional[Dict[str, Any]] = None,
    config: Optional[IdempotencyConfig] = None
) -> Tuple[bool, str]:
    """
    Convenience function for quick idempotency checks.
    
    Creates an IdempotencyStore and performs check in one call.
    
    Args:
        entity_id: Unique entity identifier
        event_type: Type of event
        db_pool: Database connection pool
        timestamp: Event timestamp
        additional_data: Additional uniqueness data
        config: Optional configuration
        
    Returns:
        Tuple of (should_process, idempotency_key)
        
    Example:
        should_process, key = await check_idempotency(
            entity_id="TICKET-123",
            event_type=EventType.TICKET_UPDATED,
            db_pool=pool
        )
        
        if should_process:
            await process_ticket_update(data)
            await store.mark_processed(key)
    """
    store = IdempotencyStore(db_pool, config)
    return await store.check_idempotency(
        entity_id=entity_id,
        event_type=event_type,
        timestamp=timestamp,
        additional_data=additional_data
    )


class IdempotencyMiddleware:
    """
    Middleware for automatic idempotency handling.
    
    Can be used as a decorator or context manager to automatically
    handle idempotency checks around function calls.
    
    Usage:
        middleware = IdempotencyMiddleware(store)
        
        @middleware.idempotent(entity_id_arg="ticket_id", event_type=EventType.TICKET_UPDATED)
        async def update_ticket(ticket_id: str, data: dict):
            # This will only execute if not a duplicate
            return await process_update(ticket_id, data)
    """
    
    def __init__(self, store: IdempotencyStore):
        self.store = store
    
    def idempotent(
        self,
        entity_id_arg: str,
        event_type: EventType,
        timestamp_arg: Optional[str] = None,
        result_cache: bool = True
    ):
        """
        Decorator for making functions idempotent.
        
        Args:
            entity_id_arg: Name of argument containing entity ID
            event_type: Type of event for deduplication
            timestamp_arg: Optional name of timestamp argument
            result_cache: Whether to cache function results
        """
        def decorator(func):
            async def wrapper(*args, **kwargs):
                # Extract entity_id from args/kwargs
                entity_id = kwargs.get(entity_id_arg)
                if entity_id is None and args:
                    # Try to get from positional args
                    import inspect
                    sig = inspect.signature(func)
                    params = list(sig.parameters.keys())
                    if entity_id_arg in params:
                        idx = params.index(entity_id_arg)
                        if idx < len(args):
                            entity_id = args[idx]
                
                if entity_id is None:
                    raise ValueError(f"Could not find entity_id in arguments")
                
                # Extract timestamp if specified
                timestamp = None
                if timestamp_arg:
                    timestamp = kwargs.get(timestamp_arg)
                
                # Check idempotency
                should_process, key = await self.store.check_idempotency(
                    entity_id=entity_id,
                    event_type=event_type,
                    timestamp=timestamp
                )
                
                if not should_process:
                    # Return cached result if available
                    if result_cache:
                        cached = await self.store.get_result(key)
                        if cached:
                            return cached
                    return None
                
                # Execute function
                try:
                    result = await func(*args, **kwargs)
                    
                    # Mark as processed
                    if result_cache:
                        await self.store.mark_processed(key, result)
                    else:
                        await self.store.mark_processed(key)
                    
                    return result
                    
                except Exception as e:
                    # Mark as failed
                    await self.store.mark_failed(key, str(e))
                    raise
            
            return wrapper
        return decorator
