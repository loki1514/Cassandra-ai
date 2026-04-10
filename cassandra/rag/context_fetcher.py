"""
Context Fetcher Module

Implements the fetch_context function for retrieving relevant context
from the memory system based on semantic search queries.

Architecture:
1. Semantic search on memory_archive (vector similarity)
2. Map table lookup for memory-to-ticket associations
3. DB1 resolution for authoritative ticket data
4. Conflict resolution: DB1 always wins

This module is critical for the RAG pipeline, providing context
that grounds LLM responses in factual ticket data.
"""

import hashlib
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union
from dataclasses import dataclass, field
from pydantic import BaseModel, Field, validator, root_validator

from .memory_manager import MemoryManager, MemoryEntry, MemorySearchResult, MemoryType
from ..encryption import EncryptionService

logger = logging.getLogger(__name__)


class ContextSource(str, Enum):
    """Sources of context data in the resolution flow."""
    MEMORY_ARCHIVE = "memory_archive"      # Vector search results
    MAP_TABLE = "map_table"                # Memory-to-ticket mappings
    DB1_TICKET = "db1_ticket"              # Authoritative ticket data
    DB1_USER = "db1_user"                  # User profile data
    CACHE = "cache"                        # Cached context


class ConflictResolution(str, Enum):
    """Conflict resolution strategies."""
    DB1_WINS = "db1_wins"                  # DB1 always wins (default)
    TIMESTAMP_WINS = "timestamp_wins"      # Most recent wins
    MANUAL_REVIEW = "manual_review"        # Queue for human review


class FetchContextInput(BaseModel):
    """
    Input model for fetch_context operation.
    
    Validates and normalizes context fetch requests with support for
    various query types, filtering options, and resolution preferences.
    
    Attributes:
        query_text: Natural language query for semantic search
        org_id: Organization identifier for data isolation
        ticket_id: Optional specific ticket to scope search
        user_id: Optional user context for personalization
        max_results: Maximum context items to return
        min_confidence: Minimum relevance threshold (0-1)
        include_history: Whether to include conversation history
        include_decisions: Whether to include past decisions
        conflict_resolution: How to handle data conflicts
        timeout_ms: Maximum time to spend fetching context
    """
    query_text: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Natural language query for semantic search"
    )
    org_id: str = Field(
        ...,
        min_length=1,
        description="Organization identifier for data isolation"
    )
    ticket_id: Optional[str] = Field(
        default=None,
        description="Optional specific ticket to scope search"
    )
    user_id: Optional[str] = Field(
        default=None,
        description="Optional user context for personalization"
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum context items to return"
    )
    min_confidence: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum relevance threshold"
    )
    include_history: bool = Field(
        default=True,
        description="Include conversation history in context"
    )
    include_decisions: bool = Field(
        default=True,
        description="Include past decisions in context"
    )
    include_knowledge: bool = Field(
        default=True,
        description="Include knowledge base articles"
    )
    conflict_resolution: ConflictResolution = Field(
        default=ConflictResolution.DB1_WINS,
        description="Strategy for resolving data conflicts"
    )
    timeout_ms: int = Field(
        default=5000,
        ge=100,
        le=30000,
        description="Maximum time to spend fetching context"
    )
    
    @validator('query_text')
    def validate_query(cls, v):
        """Ensure query is not empty or whitespace only."""
        if not v.strip():
            raise ValueError("Query text cannot be empty or whitespace")
        return v.strip()
    
    class Config:
        schema_extra = {
            "example": {
                "query_text": "What was the resolution for the login timeout issue?",
                "org_id": "org_12345",
                "ticket_id": "TICKET-789",
                "max_results": 5,
                "min_confidence": 0.75
            }
        }


@dataclass
class ContextItem:
    """
    Single item of context with provenance information.
    
    Tracks the source, confidence, and resolution path for each
    piece of context to enable auditability and conflict detection.
    """
    content: Union[str, Dict[str, Any]]
    source: ContextSource
    memory_id: Optional[str] = None
    ticket_id: Optional[str] = None
    confidence: float = 1.0
    timestamp: datetime = field(default_factory=datetime.utcnow)
    resolution_path: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    conflict_detected: bool = False
    conflict_resolution: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "content": self.content,
            "source": self.source.value,
            "memory_id": self.memory_id,
            "ticket_id": self.ticket_id,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
            "resolution_path": self.resolution_path,
            "metadata": self.metadata,
            "conflict_detected": self.conflict_detected,
            "conflict_resolution": self.conflict_resolution
        }


@dataclass
class FetchContextResult:
    """
    Result of a context fetch operation.
    
    Contains the assembled context items along with metadata about
    the fetch operation including timing, sources used, and any
    conflicts detected.
    """
    items: List[ContextItem] = field(default_factory=list)
    query: str = ""
    org_id: str = ""
    total_found: int = 0
    filtered_out: int = 0
    sources_used: Set[ContextSource] = field(default_factory=set)
    conflicts_detected: int = 0
    fetch_time_ms: float = 0.0
    cache_hit: bool = False
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "items": [item.to_dict() for item in self.items],
            "query": self.query,
            "org_id": self.org_id,
            "total_found": self.total_found,
            "filtered_out": self.filtered_out,
            "sources_used": [s.value for s in self.sources_used],
            "conflicts_detected": self.conflicts_detected,
            "fetch_time_ms": self.fetch_time_ms,
            "cache_hit": self.cache_hit,
            "errors": self.errors
        }
    
    def get_combined_context(self, max_length: int = 8000) -> str:
        """
        Combine all context items into a single text string.
        
        Args:
            max_length: Maximum length of combined context
            
        Returns:
            Combined context string suitable for LLM prompt
        """
        parts = []
        current_length = 0
        
        for item in self.items:
            content = item.content if isinstance(item.content, str) else json.dumps(item.content)
            
            # Add source attribution
            header = f"[{item.source.value.upper()}]"
            if item.ticket_id:
                header += f" Ticket: {item.ticket_id}"
            header += "\n"
            
            part = header + content + "\n\n"
            
            if current_length + len(part) > max_length:
                # Truncate if exceeding limit
                remaining = max_length - current_length - len(header) - 4
                if remaining > 50:  # Only add if meaningful
                    parts.append(header + content[:remaining] + "...\n\n")
                break
            
            parts.append(part)
            current_length += len(part)
        
        return "".join(parts)


class ContextFetcher:
    """
    Context fetcher implementing the semantic search → map → resolution flow.
    
    The fetcher retrieves relevant context through a multi-stage pipeline:
    1. Semantic search on memory_archive for relevant memories
    2. Map table lookup to find associated tickets
    3. DB1 resolution for authoritative ticket/user data
    4. Conflict resolution with DB1 as source of truth
    5. Decryption of encrypted payloads
    6. Merging memory data with live DB1 status
    
    Performance Target: <300ms p95 latency
    
    Usage:
        fetcher = ContextFetcher(memory_manager, db_pool)
        
        input_data = FetchContextInput(
            query_text="What was the resolution?",
            org_id="org_123"
        )
        result = await fetcher.fetch_context(input_data)
        context_text = result.get_combined_context()
    """
    
    # Performance target: 300ms p95
    TARGET_P95_LATENCY_MS = 300
    
    def __init__(
        self,
        memory_manager: MemoryManager,
        db_pool: Any,
        cache_client: Optional[Any] = None,
        encryption_service: Optional[EncryptionService] = None,
        default_resolution: ConflictResolution = ConflictResolution.DB1_WINS
    ):
        """
        Initialize the context fetcher.
        
        Args:
            memory_manager: MemoryManager instance for memory operations
            db_pool: Database connection pool for map table and DB1 queries
            cache_client: Optional cache client (Redis, etc.) for context caching
            encryption_service: Optional EncryptionService for decrypting payloads
            default_resolution: Default conflict resolution strategy
        """
        self.memory_manager = memory_manager
        self.db_pool = db_pool
        self.cache_client = cache_client
        self.encryption_service = encryption_service
        self.default_resolution = default_resolution
        logger.info("ContextFetcher initialized")
    
    async def fetch_context(self, input_data: FetchContextInput) -> FetchContextResult:
        """
        Fetch context based on the provided input.
        
        Implements the full context retrieval pipeline:
        1. Check cache for existing context
        2. Semantic search on memory_archive
        3. Map table lookups for ticket associations
        4. DB1 resolution for authoritative data
        5. Decrypt encrypted payloads
        6. Merge memory + live DB1 status
        7. Conflict resolution
        8. Assemble and return context
        
        Performance: Target <300ms p95
        
        Args:
            input_data: Validated fetch context input
            
        Returns:
            FetchContextResult with assembled context items
        """
        start_time = datetime.utcnow()
        result = FetchContextResult(
            query=input_data.query_text,
            org_id=input_data.org_id
        )
        
        logger.info(f"Fetching context for org {input_data.org_id}: '{input_data.query_text[:50]}...'")
        
        try:
            # Step 1: Check cache (fast path)
            if self.cache_client:
                cached = await self._check_cache(input_data)
                if cached:
                    result.cache_hit = True
                    result.fetch_time_ms = self._elapsed_ms(start_time)
                    logger.debug("Cache hit for context fetch")
                    return cached
            
            # Step 2: Semantic search on memory_archive (with timeout)
            memory_results = await self._search_memories_with_timeout(input_data)
            result.total_found = len(memory_results)
            result.sources_used.add(ContextSource.MEMORY_ARCHIVE)
            
            # Step 3-6: Parallel map lookup, DB1 resolution, and decryption
            context_items = await self._resolve_context_items_optimized(
                memory_results, input_data
            )
            
            # Step 7: Apply filters and confidence threshold
            filtered_items = self._apply_filters(context_items, input_data)
            result.filtered_out = len(context_items) - len(filtered_items)
            
            # Assemble result
            result.items = filtered_items[:input_data.max_results]
            
            # Cache result if not empty
            if self.cache_client and result.items:
                await self._cache_result(input_data, result)
            
        except Exception as e:
            logger.error(f"Context fetch failed: {e}")
            result.errors.append(str(e))
        
        result.fetch_time_ms = self._elapsed_ms(start_time)
        
        # Log performance metrics
        if result.fetch_time_ms > self.TARGET_P95_LATENCY_MS:
            logger.warning(
                f"Context fetch exceeded target latency: {result.fetch_time_ms}ms "
                f"(target: {self.TARGET_P95_LATENCY_MS}ms)"
            )
        
        logger.info(f"Context fetch completed in {result.fetch_time_ms}ms, "
                   f"found {len(result.items)} items")
        
        return result
    
    async def _search_memories_with_timeout(
        self,
        input_data: FetchContextInput
    ) -> List[MemorySearchResult]:
        """
        Search memories with timeout for latency control.
        
        Args:
            input_data: Fetch context input
            
        Returns:
            List of memory search results
        """
        import asyncio
        
        # Calculate timeout (leave 100ms for other operations)
        timeout_seconds = max(0.1, (input_data.timeout_ms - 100) / 1000)
        
        try:
            return await asyncio.wait_for(
                self._search_memories(input_data),
                timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            logger.warning(f"Memory search timed out after {timeout_seconds}s")
            return []
    
    async def _resolve_context_items_optimized(
        self,
        memory_results: List[MemorySearchResult],
        input_data: FetchContextInput
    ) -> List[ContextItem]:
        """
        Optimized context resolution with parallel processing.
        
        Processes multiple memories in parallel for better performance.
        
        Args:
            memory_results: Memory search results
            input_data: Fetch context input
            
        Returns:
            List of resolved context items
        """
        import asyncio
        
        # Process memories in parallel batches
        batch_size = 5  # Process 5 at a time
        all_items = []
        
        for i in range(0, len(memory_results), batch_size):
            batch = memory_results[i:i + batch_size]
            tasks = [
                self._resolve_single_memory(m, input_data)
                for m in batch
            ]
            batch_items = await asyncio.gather(*tasks, return_exceptions=True)
            
            for item in batch_items:
                if isinstance(item, Exception):
                    logger.error(f"Failed to resolve memory: {item}")
                else:
                    all_items.append(item)
        
        return all_items
    
    async def _resolve_single_memory(
        self,
        memory_result: MemorySearchResult,
        input_data: FetchContextInput
    ) -> ContextItem:
        """
        Resolve a single memory to a context item.
        
        Args:
            memory_result: Single memory search result
            input_data: Fetch context input
            
        Returns:
            Resolved context item
        """
        memory = memory_result.memory
        
        # Decrypt if needed
        if self.encryption_service and memory.metadata.get('encrypted'):
            try:
                memory = await self._decrypt_memory(memory)
            except Exception as e:
                logger.warning(f"Failed to decrypt memory {memory.memory_id}: {e}")
        
        # Create base context item
        item = ContextItem(
            content=memory.content,
            source=ContextSource.MEMORY_ARCHIVE,
            memory_id=memory.memory_id,
            ticket_id=memory.ticket_id,
            confidence=memory_result.similarity_score,
            resolution_path=["memory_archive"]
        )
        
        # If memory has ticket_id, resolve through map table and merge with DB1
        if memory.ticket_id:
            db1_data = await self._resolve_via_map_table(
                memory.ticket_id, input_data.org_id
            )
            
            if db1_data:
                item.sources_used.add(ContextSource.MAP_TABLE)
                item.sources_used.add(ContextSource.DB1_TICKET)
                
                # Merge memory + live DB1 status
                merged_content = await self._merge_memory_db1(
                    memory, db1_data
                )
                item.content = merged_content
                item.metadata["db1_enhanced"] = True
                item.metadata["ticket_data"] = db1_data
                item.resolution_path.extend(["map_table", "db1_ticket", "merged"])
                
                # Detect conflicts
                conflict = self._detect_conflict(memory, db1_data)
                
                if conflict:
                    item.conflict_detected = True
                    item = self._resolve_conflict(
                        item, db1_data, input_data.conflict_resolution
                    )
        
        return item
    
    async def _decrypt_memory(self, memory: MemoryEntry) -> MemoryEntry:
        """
        Decrypt an encrypted memory entry.
        
        Args:
            memory: Potentially encrypted memory entry
            
        Returns:
            Decrypted memory entry
        """
        if not self.encryption_service:
            return memory
        
        # Check if content is encrypted
        content = memory.content
        if isinstance(content, dict) and 'ciphertext' in content:
            try:
                decrypted = self.encryption_service.decrypt(
                    ciphertext=content['ciphertext'],
                    encrypted_data_key=content['encrypted_data_key'],
                    org_id=memory.org_id
                )
                
                # Create new memory entry with decrypted content
                return MemoryEntry(
                    content=decrypted,
                    memory_type=memory.memory_type,
                    org_id=memory.org_id,
                    ticket_id=memory.ticket_id,
                    user_id=memory.user_id,
                    priority=memory.priority,
                    metadata={**memory.metadata, 'decrypted': True},
                    embedding=memory.embedding,
                    created_at=memory.created_at,
                    expires_at=memory.expires_at,
                    tags=memory.tags,
                    memory_id=memory.memory_id
                )
            except Exception as e:
                logger.error(f"Decryption failed for memory {memory.memory_id}: {e}")
                raise
        
        return memory
    
    async def _merge_memory_db1(
        self,
        memory: MemoryEntry,
        db1_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Merge memory content with live DB1 ticket status.
        
        Args:
            memory: Memory entry
            db1_data: DB1 ticket data
            
        Returns:
            Merged content dictionary
        """
        memory_content = memory.content
        if isinstance(memory_content, str):
            memory_content = {"text": memory_content}
        elif not isinstance(memory_content, dict):
            memory_content = {"data": memory_content}
        
        return {
            "memory_content": memory_content,
            "live_status": {
                "ticket_id": db1_data.get('id'),
                "status": db1_data.get('status'),
                "priority": db1_data.get('priority'),
                "assignee_id": db1_data.get('assignee_id'),
                "updated_at": db1_data.get('updated_at'),
                "resolution_notes": db1_data.get('resolution_notes')
            },
            "merged_at": datetime.utcnow().isoformat()
        }
    
    async def _check_cache(self, input_data: FetchContextInput) -> Optional[FetchContextResult]:
        """Check if context is available in cache."""
        if not self.cache_client:
            return None
        
        cache_key = self._generate_cache_key(input_data)
        
        try:
            cached_data = await self.cache_client.get(cache_key)
            if cached_data:
                data = json.loads(cached_data)
                return FetchContextResult(
                    items=[ContextItem(**item) for item in data.get("items", [])],
                    query=input_data.query_text,
                    org_id=input_data.org_id,
                    cache_hit=True,
                    fetch_time_ms=0
                )
        except Exception as e:
            logger.warning(f"Cache check failed: {e}")
        
        return None
    
    async def _cache_result(
        self, 
        input_data: FetchContextInput, 
        result: FetchContextResult
    ) -> None:
        """Cache the context result for future requests."""
        if not self.cache_client:
            return
        
        cache_key = self._generate_cache_key(input_data)
        cache_data = json.dumps(result.to_dict())
        
        try:
            # Cache for 5 minutes (context can change frequently)
            await self.cache_client.setex(cache_key, 300, cache_data)
            logger.debug(f"Cached context result: {cache_key}")
        except Exception as e:
            logger.warning(f"Cache write failed: {e}")
    
    def _generate_cache_key(self, input_data: FetchContextInput) -> str:
        """Generate cache key from input parameters."""
        key_data = {
            "query": input_data.query_text.lower().strip(),
            "org_id": input_data.org_id,
            "ticket_id": input_data.ticket_id,
            "user_id": input_data.user_id,
            "max_results": input_data.max_results
        }
        key_str = json.dumps(key_data, sort_keys=True)
        return f"context:{hashlib.sha256(key_str.encode()).hexdigest()[:16]}"
    
    async def _search_memories(
        self, 
        input_data: FetchContextInput
    ) -> List[MemorySearchResult]:
        """Perform semantic search on memory archive."""
        # Build memory type filter
        memory_types = []
        if input_data.include_history:
            memory_types.append(MemoryType.CONVERSATION)
        if input_data.include_decisions:
            memory_types.append(MemoryType.DECISION)
        if input_data.include_knowledge:
            memory_types.append(MemoryType.KNOWLEDGE)
        
        if not memory_types:
            memory_types = None  # Search all types
        
        try:
            results = await self.memory_manager.search_memories(
                query=input_data.query_text,
                org_id=input_data.org_id,
                limit=input_data.max_results * 2,  # Get extra for filtering
                memory_types=memory_types,
                ticket_id=input_data.ticket_id,
                min_similarity=input_data.min_confidence
            )
            return results
        except Exception as e:
            logger.error(f"Memory search failed: {e}")
            return []
    
    async def _resolve_context_items(
        self,
        memory_results: List[MemorySearchResult],
        input_data: FetchContextInput
    ) -> List[ContextItem]:
        """
        Resolve memories to context items via map table and DB1.
        
        For each memory:
        1. Check map table for ticket associations
        2. Query DB1 for authoritative ticket data
        3. Detect and resolve conflicts
        """
        context_items = []
        
        for memory_result in memory_results:
            memory = memory_result.memory
            
            # Create base context item from memory
            item = ContextItem(
                content=memory.content,
                source=ContextSource.MEMORY_ARCHIVE,
                memory_id=memory.memory_id,
                ticket_id=memory.ticket_id,
                confidence=memory_result.similarity_score,
                resolution_path=["memory_archive"]
            )
            
            # If memory has ticket_id, resolve through map table
            if memory.ticket_id:
                db1_data = await self._resolve_via_map_table(
                    memory.ticket_id, input_data.org_id
                )
                
                if db1_data:
                    result.sources_used.add(ContextSource.MAP_TABLE)
                    result.sources_used.add(ContextSource.DB1_TICKET)
                    
                    # Detect conflicts
                    conflict = self._detect_conflict(memory, db1_data)
                    
                    if conflict:
                        item.conflict_detected = True
                        result.conflicts_detected += 1
                        
                        # Apply conflict resolution
                        resolved_item = self._resolve_conflict(
                            item, db1_data, input_data.conflict_resolution
                        )
                        item = resolved_item
                    else:
                        # No conflict, enhance with DB1 data
                        item.metadata["db1_enhanced"] = True
                        item.metadata["ticket_data"] = db1_data
                        item.resolution_path.extend(["map_table", "db1_ticket"])
            
            context_items.append(item)
        
        return context_items
    
    async def _resolve_via_map_table(
        self, 
        ticket_id: str, 
        org_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Query map table and resolve to DB1 ticket data.
        
        The map table links memories to their authoritative source
        in DB1. This function:
        1. Looks up the ticket in the map table
        2. Queries DB1 for the authoritative ticket record
        3. Returns the resolved data
        """
        try:
            async with self.db_pool.acquire() as conn:
                # Query map table for ticket mapping
                map_row = await conn.fetchrow(
                    """
                    SELECT db1_ticket_id, db1_table, resolution_status
                    FROM memory_ticket_map
                    WHERE ticket_id = $1 AND org_id = $2
                    """,
                    ticket_id, org_id
                )
                
                if not map_row:
                    logger.debug(f"No map entry found for ticket {ticket_id}")
                    return None
                
                db1_ticket_id = map_row["db1_ticket_id"]
                db1_table = map_row["db1_table"]
                
                # Query DB1 for authoritative ticket data
                # Note: DB1 schema may vary, this is a generic implementation
                ticket_row = await conn.fetchrow(
                    f"""
                    SELECT 
                        t.id, t.title, t.description, t.status,
                        t.priority, t.assignee_id, t.requester_id,
                        t.created_at, t.updated_at, t.resolved_at,
                        t.resolution_notes, t.tags
                    FROM {db1_table} t
                    WHERE t.id = $1 AND t.org_id = $2
                    """,
                    db1_ticket_id, org_id
                )
                
                if ticket_row:
                    return dict(ticket_row)
                else:
                    logger.warning(f"DB1 ticket not found: {db1_ticket_id}")
                    return None
                    
        except Exception as e:
            logger.error(f"Map table resolution failed for {ticket_id}: {e}")
            return None
    
    def _detect_conflict(
        self, 
        memory: MemoryEntry, 
        db1_data: Dict[str, Any]
    ) -> bool:
        """
        Detect if there's a conflict between memory and DB1 data.
        
        Conflicts can occur when:
        - Memory status differs from DB1 status
        - Memory assignee differs from DB1 assignee
        - Memory was created after DB1 last update (stale memory)
        """
        # Check status conflict
        memory_status = memory.metadata.get("ticket_status")
        db1_status = db1_data.get("status")
        
        if memory_status and db1_status and memory_status != db1_status:
            return True
        
        # Check assignee conflict
        memory_assignee = memory.metadata.get("assignee_id")
        db1_assignee = db1_data.get("assignee_id")
        
        if memory_assignee and db1_assignee and memory_assignee != db1_assignee:
            return True
        
        # Check if memory is stale (created before last DB1 update)
        db1_updated = db1_data.get("updated_at")
        if db1_updated and memory.created_at:
            if isinstance(db1_updated, str):
                db1_updated = datetime.fromisoformat(db1_updated.replace('Z', '+00:00'))
            if memory.created_at < db1_updated:
                return True
        
        return False
    
    def _resolve_conflict(
        self,
        item: ContextItem,
        db1_data: Dict[str, Any],
        resolution_strategy: ConflictResolution
    ) -> ContextItem:
        """
        Resolve conflict between memory and DB1 data.
        
        Default strategy: DB1_WINS - DB1 is always the source of truth.
        """
        if resolution_strategy == ConflictResolution.DB1_WINS:
            # Replace content with DB1 data
            item.content = {
                "ticket_data": db1_data,
                "original_memory": item.content
            }
            item.source = ContextSource.DB1_TICKET
            item.conflict_resolution = "db1_wins"
            item.resolution_path.extend(["conflict_detected", "db1_wins"])
            item.confidence = 1.0  # DB1 is authoritative
            
        elif resolution_strategy == ConflictResolution.TIMESTAMP_WINS:
            # Compare timestamps and keep most recent
            db1_updated = db1_data.get("updated_at")
            memory_time = item.timestamp
            
            if db1_updated:
                if isinstance(db1_updated, str):
                    db1_updated = datetime.fromisoformat(db1_updated.replace('Z', '+00:00'))
                
                if db1_updated > memory_time:
                    item.content = db1_data
                    item.source = ContextSource.DB1_TICKET
                    item.conflict_resolution = "timestamp_wins_db1"
                else:
                    item.conflict_resolution = "timestamp_wins_memory"
            
            item.resolution_path.extend(["conflict_detected", "timestamp_wins"])
            
        elif resolution_strategy == ConflictResolution.MANUAL_REVIEW:
            # Flag for human review, keep both
            item.content = {
                "conflict": True,
                "memory_data": item.content,
                "db1_data": db1_data,
                "review_required": True
            }
            item.conflict_resolution = "manual_review_queued"
            item.resolution_path.extend(["conflict_detected", "manual_review"])
        
        return item
    
    def _apply_filters(
        self,
        items: List[ContextItem],
        input_data: FetchContextInput
    ) -> List[ContextItem]:
        """Apply confidence threshold and other filters."""
        filtered = []
        
        for item in items:
            # Apply confidence threshold (unless from DB1 which is authoritative)
            if item.source != ContextSource.DB1_TICKET and \
               item.confidence < input_data.min_confidence:
                continue
            
            filtered.append(item)
        
        # Sort by confidence (highest first)
        filtered.sort(key=lambda x: x.confidence, reverse=True)
        
        return filtered
    
    def _elapsed_ms(self, start_time: datetime) -> float:
        """Calculate elapsed time in milliseconds."""
        return (datetime.utcnow() - start_time).total_seconds() * 1000


async def fetch_context(
    query_text: str,
    org_id: str,
    memory_manager: MemoryManager,
    db_pool: Any,
    **kwargs
) -> FetchContextResult:
    """
    Convenience function for fetching context.
    
    Creates a ContextFetcher and executes fetch_context in one call.
    
    Args:
        query_text: Natural language query
        org_id: Organization identifier
        memory_manager: MemoryManager instance
        db_pool: Database connection pool
        **kwargs: Additional FetchContextInput parameters
        
    Returns:
        FetchContextResult with assembled context
        
    Example:
        result = await fetch_context(
            query_text="What was the resolution?",
            org_id="org_123",
            memory_manager=mm,
            db_pool=pool,
            max_results=5
        )
        context = result.get_combined_context()
    """
    input_data = FetchContextInput(
        query_text=query_text,
        org_id=org_id,
        **kwargs
    )
    
    fetcher = ContextFetcher(memory_manager, db_pool)
    return await fetcher.fetch_context(input_data)


async def resolve_query(
    query_text: str,
    org_id: str,
    memory_manager: MemoryManager,
    db_pool: Any,
    max_results: int = 10,
    min_confidence: float = 0.7,
    timeout_ms: int = 400
) -> FetchContextResult:
    """
    T21: Query Resolution — Semantic → Map → DB1 Flow
    
    Resolves a user query through the complete RAG pipeline:
    1. Semantic search on memory_archive (vector similarity)
    2. Map table lookup for memory-to-ticket associations
    3. DB1 resolution for authoritative ticket data
    4. Conflict resolution: DB1 always wins
    
    Performance Target: <400ms latency
    
    Args:
        query_text: Natural language query from user
        org_id: Organization identifier for data isolation
        memory_manager: MemoryManager instance for semantic search
        db_pool: Database connection pool for map table and DB1 queries
        max_results: Maximum context items to return (default: 10)
        min_confidence: Minimum relevance threshold (default: 0.7)
        timeout_ms: Maximum query time in milliseconds (default: 400)
        
    Returns:
        FetchContextResult with resolved context items
        
    Raises:
        QueryResolutionError: If resolution fails critically
        
    Example:
        result = await resolve_query(
            query_text="What was decided about the API migration?",
            org_id="org_12345",
            memory_manager=memory_mgr,
            db_pool=db_pool,
            max_results=5
        )
        
        # Access resolved context
        for item in result.items:
            print(f"Source: {item.source}, Confidence: {item.confidence}")
            if item.conflict_detected:
                print(f"Conflict resolved: {item.conflict_resolution}")
    """
    start_time = datetime.utcnow()
    
    logger.info(f"T21: Resolving query for org {org_id}: '{query_text[:50]}...'")
    
    try:
        # Create fetcher with DB1_WINS as default conflict resolution
        fetcher = ContextFetcher(
            memory_manager=memory_manager,
            db_pool=db_pool,
            default_resolution=ConflictResolution.DB1_WINS
        )
        
        # Build input with T21-specific requirements
        input_data = FetchContextInput(
            query_text=query_text,
            org_id=org_id,
            max_results=max_results,
            min_confidence=min_confidence,
            conflict_resolution=ConflictResolution.DB1_WINS,
            timeout_ms=timeout_ms,
            include_history=True,
            include_decisions=True,
            include_knowledge=True
        )
        
        # Execute the full resolution flow
        result = await fetcher.fetch_context(input_data)
        
        # T21: Enforce latency requirement
        elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
        if elapsed_ms > timeout_ms:
            logger.warning(
                f"T21: Query resolution exceeded latency target: {elapsed_ms:.2f}ms "
                f"(target: {timeout_ms}ms)"
            )
        
        # T21: Log resolution statistics
        db1_wins_count = sum(
            1 for item in result.items 
            if item.conflict_resolution == "db1_wins"
        )
        
        logger.info(
            f"T21: Query resolved in {elapsed_ms:.2f}ms | "
            f"Items: {len(result.items)}, "
            f"Conflicts resolved (DB1 wins): {db1_wins_count}, "
            f"Sources: {[s.value for s in result.sources_used]}"
        )
        
        return result
        
    except Exception as e:
        logger.error(f"T21: Query resolution failed: {e}")
        raise QueryResolutionError(f"Failed to resolve query: {e}")


class QueryResolutionError(Exception):
    """Raised when query resolution fails critically."""
    pass
