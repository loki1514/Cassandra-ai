"""
Memory Manager Module

Core memory operations for the Cassandra AI RAG system.
Handles Supermemory API integration, encryption, and dual-write
operations to memory_archive for redundancy.

Key Features:
- Supermemory API integration for vector storage and retrieval
- Encryption layer for sensitive memory content
- Dual-write pattern: Supermemory + memory_archive
- Semantic search capabilities
- Organization-scoped memory isolation
"""

import hashlib
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field
from pydantic import BaseModel, Field, validator
import httpx
from cryptography.fernet import Fernet

# Configure logging
logger = logging.getLogger(__name__)


class MemoryType(str, Enum):
    """Types of memory entries supported by the system."""
    TICKET_CONTEXT = "ticket_context"
    DECISION = "decision"
    CONVERSATION = "conversation"
    KNOWLEDGE = "knowledge"
    USER_PREFERENCE = "user_preference"
    SYSTEM_STATE = "system_state"


class MemoryPriority(int, Enum):
    """Priority levels for memory entries."""
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4
    ARCHIVE = 5


@dataclass
class MemoryEntry:
    """
    Represents a single memory entry in the system.
    
    Attributes:
        content: The actual memory content (text or structured data)
        memory_type: Classification of the memory
        org_id: Organization identifier for isolation
        ticket_id: Optional associated ticket ID
        user_id: Optional user who created/owns this memory
        priority: Importance level for retention
        metadata: Additional structured data
        embedding: Optional pre-computed vector embedding
        created_at: Timestamp of creation
        expires_at: Optional expiration timestamp
        tags: List of searchable tags
    """
    content: Union[str, Dict[str, Any]]
    memory_type: MemoryType
    org_id: str
    ticket_id: Optional[str] = None
    user_id: Optional[str] = None
    priority: MemoryPriority = MemoryPriority.MEDIUM
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)
    memory_id: Optional[str] = None
    
    def to_dict(self, encrypt: bool = False, encryption_key: Optional[bytes] = None) -> Dict[str, Any]:
        """Convert memory entry to dictionary, optionally encrypting content."""
        content = self.content
        
        if encrypt and encryption_key:
            f = Fernet(encryption_key)
            content_bytes = json.dumps(content).encode() if isinstance(content, dict) else content.encode()
            content = f.encrypt(content_bytes).decode()
        
        return {
            "memory_id": self.memory_id,
            "content": content,
            "memory_type": self.memory_type.value,
            "org_id": self.org_id,
            "ticket_id": self.ticket_id,
            "user_id": self.user_id,
            "priority": self.priority.value,
            "metadata": self.metadata,
            "embedding": self.embedding,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "tags": self.tags,
            "encrypted": encrypt
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], decryption_key: Optional[bytes] = None) -> 'MemoryEntry':
        """Create memory entry from dictionary, optionally decrypting content."""
        content = data.get("content")
        
        if data.get("encrypted") and decryption_key:
            f = Fernet(decryption_key)
            decrypted = f.decrypt(content.encode())
            try:
                content = json.loads(decrypted.decode())
            except json.JSONDecodeError:
                content = decrypted.decode()
        
        return cls(
            content=content,
            memory_type=MemoryType(data["memory_type"]),
            org_id=data["org_id"],
            ticket_id=data.get("ticket_id"),
            user_id=data.get("user_id"),
            priority=MemoryPriority(data.get("priority", 3)),
            metadata=data.get("metadata", {}),
            embedding=data.get("embedding"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.utcnow(),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            tags=data.get("tags", []),
            memory_id=data.get("memory_id")
        )


@dataclass
class MemorySearchResult:
    """Result from a memory search operation."""
    memory: MemoryEntry
    similarity_score: float
    match_type: str  # 'semantic', 'exact', 'tag', 'metadata'
    matched_fields: List[str] = field(default_factory=list)


class SupermemoryConfig(BaseModel):
    """Configuration for Supermemory API integration."""
    api_key: str = Field(..., description="Supermemory API key")
    base_url: str = Field(default="https://api.supermemory.ai/v1", description="Supermemory API base URL")
    timeout: int = Field(default=30, description="Request timeout in seconds")
    max_retries: int = Field(default=3, description="Maximum retry attempts")
    
    class Config:
        env_prefix = "SUPERMEMORY_"


class MemoryManager:
    """
    Core memory manager for Cassandra AI RAG system.
    
    Handles all memory operations including:
    - Adding memories to Supermemory and local archive
    - Retrieving memories by ID or search
    - Semantic search with vector similarity
    - Organization-scoped memory isolation
    - Encryption for sensitive content
    
    Usage:
        manager = MemoryManager(supermemory_config, db_pool, encryption_key)
        
        # Add memory
        entry = MemoryEntry(content="User prefers email notifications", ...)
        memory_id = await manager.add_memory(entry)
        
        # Search memories
        results = await manager.search_memories("notification preferences", org_id="org_123")
        
        # Get specific memory
        memory = await manager.get_memory(memory_id, org_id="org_123")
    """
    
    def __init__(
        self,
        supermemory_config: SupermemoryConfig,
        db_pool: Any,  # asyncpg.Pool or similar
        encryption_key: Optional[bytes] = None,
        enable_dual_write: bool = True
    ):
        """
        Initialize the memory manager.
        
        Args:
            supermemory_config: Configuration for Supermemory API
            db_pool: Database connection pool for memory_archive
            encryption_key: Fernet key for content encryption (optional)
            enable_dual_write: Whether to write to both Supermemory and archive
        """
        self.config = supermemory_config
        self.db_pool = db_pool
        self.encryption_key = encryption_key
        self.enable_dual_write = enable_dual_write
        self._http_client: Optional[httpx.AsyncClient] = None
        
        logger.info(f"MemoryManager initialized (dual_write={enable_dual_write})")
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for Supermemory API."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=self.config.timeout,
                headers={"Authorization": f"Bearer {self.config.api_key}"}
            )
        return self._http_client
    
    def _generate_memory_id(self, content: str, org_id: str) -> str:
        """Generate deterministic memory ID from content and org."""
        hash_input = f"{org_id}:{content}:{datetime.utcnow().isoformat()}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:32]
    
    async def add_memory(
        self,
        entry: MemoryEntry,
        encrypt: bool = False,
        generate_embedding: bool = True
    ) -> str:
        """
        Add a memory entry to the system.
        
        Writes to Supermemory (primary) and optionally to memory_archive
        for redundancy. Generates embedding if not provided.
        
        Args:
            entry: The memory entry to add
            encrypt: Whether to encrypt the content
            generate_embedding: Whether to generate embedding if missing
            
        Returns:
            The memory ID of the created entry
            
        Raises:
            MemoryWriteError: If write to primary storage fails
        """
        # Generate memory ID if not provided
        if not entry.memory_id:
            content_str = entry.content if isinstance(entry.content, str) else json.dumps(entry.content)
            entry.memory_id = self._generate_memory_id(content_str, entry.org_id)
        
        logger.debug(f"Adding memory {entry.memory_id} for org {entry.org_id}")
        
        # Generate embedding if needed and requested
        if generate_embedding and entry.embedding is None:
            entry.embedding = await self._generate_embedding(entry.content)
        
        # Write to Supermemory (primary)
        await self._write_to_supermemory(entry, encrypt)
        
        # Dual-write to memory_archive if enabled
        if self.enable_dual_write:
            await self._write_to_archive(entry, encrypt)
        
        logger.info(f"Memory {entry.memory_id} added successfully")
        return entry.memory_id
    
    async def _write_to_supermemory(self, entry: MemoryEntry, encrypt: bool) -> None:
        """Write memory entry to Supermemory API."""
        client = await self._get_http_client()
        
        payload = {
            "id": entry.memory_id,
            "content": entry.content if isinstance(entry.content, str) else json.dumps(entry.content),
            "metadata": {
                "org_id": entry.org_id,
                "ticket_id": entry.ticket_id,
                "user_id": entry.user_id,
                "memory_type": entry.memory_type.value,
                "priority": entry.priority.value,
                "tags": entry.tags,
                **entry.metadata
            },
            "embedding": entry.embedding
        }
        
        for attempt in range(self.config.max_retries):
            try:
                response = await client.post("/memories", json=payload)
                response.raise_for_status()
                logger.debug(f"Supermemory write successful for {entry.memory_id}")
                return
            except httpx.HTTPError as e:
                logger.warning(f"Supermemory write attempt {attempt + 1} failed: {e}")
                if attempt == self.config.max_retries - 1:
                    raise MemoryWriteError(f"Failed to write to Supermemory: {e}")
    
    async def _write_to_archive(self, entry: MemoryEntry, encrypt: bool) -> None:
        """Write memory entry to local memory_archive table."""
        try:
            entry_dict = entry.to_dict(encrypt=encrypt and self.encryption_key is not None, 
                                      encryption_key=self.encryption_key)
            
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO memory_archive (
                        memory_id, content, memory_type, org_id, ticket_id, user_id,
                        priority, metadata, embedding, created_at, expires_at, tags, encrypted
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                    ON CONFLICT (memory_id) DO UPDATE SET
                        content = EXCLUDED.content,
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW()
                    """,
                    entry.memory_id,
                    json.dumps(entry_dict["content"]) if isinstance(entry_dict["content"], dict) else entry_dict["content"],
                    entry.memory_type.value,
                    entry.org_id,
                    entry.ticket_id,
                    entry.user_id,
                    entry.priority.value,
                    json.dumps(entry.metadata),
                    entry.embedding,
                    entry.created_at,
                    entry.expires_at,
                    entry.tags,
                    encrypt
                )
            logger.debug(f"Archive write successful for {entry.memory_id}")
        except Exception as e:
            logger.error(f"Archive write failed for {entry.memory_id}: {e}")
            # Don't raise - archive is secondary, Supermemory is primary
    
    async def _generate_embedding(self, content: Union[str, Dict[str, Any]]) -> List[float]:
        """Generate vector embedding for content using Supermemory API."""
        client = await self._get_http_client()
        
        content_str = content if isinstance(content, str) else json.dumps(content)
        
        try:
            response = await client.post(
                "/embeddings",
                json={"text": content_str}
            )
            response.raise_for_status()
            return response.json()["embedding"]
        except httpx.HTTPError as e:
            logger.error(f"Embedding generation failed: {e}")
            # Return empty embedding as fallback
            return []
    
    async def get_memory(
        self,
        memory_id: str,
        org_id: str,
        decrypt: bool = True
    ) -> Optional[MemoryEntry]:
        """
        Retrieve a memory by ID.
        
        Args:
            memory_id: The unique memory identifier
            org_id: Organization ID for access control
            decrypt: Whether to decrypt encrypted content
            
        Returns:
            MemoryEntry if found and accessible, None otherwise
        """
        logger.debug(f"Fetching memory {memory_id} for org {org_id}")
        
        # Try Supermemory first
        try:
            client = await self._get_http_client()
            response = await client.get(f"/memories/{memory_id}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get("metadata", {}).get("org_id") == org_id:
                    entry = MemoryEntry.from_dict(data, 
                        self.encryption_key if decrypt else None)
                    logger.debug(f"Memory {memory_id} retrieved from Supermemory")
                    return entry
        except httpx.HTTPError as e:
            logger.warning(f"Supermemory fetch failed: {e}")
        
        # Fallback to archive
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM memory_archive 
                    WHERE memory_id = $1 AND org_id = $2
                    """,
                    memory_id, org_id
                )
                
                if row:
                    entry = MemoryEntry.from_dict(dict(row),
                        self.encryption_key if decrypt else None)
                    logger.debug(f"Memory {memory_id} retrieved from archive")
                    return entry
        except Exception as e:
            logger.error(f"Archive fetch failed: {e}")
        
        logger.info(f"Memory {memory_id} not found for org {org_id}")
        return None
    
    async def search_memories(
        self,
        query: str,
        org_id: str,
        limit: int = 10,
        memory_types: Optional[List[MemoryType]] = None,
        ticket_id: Optional[str] = None,
        min_similarity: float = 0.7,
        include_expired: bool = False
    ) -> List[MemorySearchResult]:
        """
        Search memories using semantic similarity.
        
        Args:
            query: Search query text
            org_id: Organization ID for scoping
            limit: Maximum results to return
            memory_types: Filter by memory types
            ticket_id: Filter by associated ticket
            min_similarity: Minimum similarity threshold (0-1)
            include_expired: Whether to include expired memories
            
        Returns:
            List of memory search results sorted by relevance
        """
        logger.debug(f"Searching memories for org {org_id}: '{query[:50]}...'")
        
        # Generate query embedding
        query_embedding = await self._generate_embedding(query)
        
        if not query_embedding:
            logger.warning("Failed to generate query embedding, falling back to text search")
            return await self._text_search_memories(
                query, org_id, limit, memory_types, ticket_id, include_expired
            )
        
        # Search Supermemory
        try:
            client = await self._get_http_client()
            
            filters = {"org_id": org_id}
            if ticket_id:
                filters["ticket_id"] = ticket_id
            if memory_types:
                filters["memory_type"] = [mt.value for mt in memory_types]
            
            response = await client.post(
                "/memories/search",
                json={
                    "embedding": query_embedding,
                    "filters": filters,
                    "limit": limit,
                    "min_similarity": min_similarity
                }
            )
            response.raise_for_status()
            
            results = []
            for item in response.json().get("results", []):
                entry = MemoryEntry.from_dict(item, self.encryption_key)
                results.append(MemorySearchResult(
                    memory=entry,
                    similarity_score=item.get("similarity", 0),
                    match_type="semantic",
                    matched_fields=item.get("matched_fields", [])
                ))
            
            logger.info(f"Found {len(results)} memories via semantic search")
            return sorted(results, key=lambda r: r.similarity_score, reverse=True)
            
        except httpx.HTTPError as e:
            logger.error(f"Semantic search failed: {e}")
            # Fallback to archive search
            return await self._search_archive(
                query_embedding, org_id, limit, memory_types, 
                ticket_id, min_similarity, include_expired
            )
    
    async def _text_search_memories(
        self,
        query: str,
        org_id: str,
        limit: int,
        memory_types: Optional[List[MemoryType]],
        ticket_id: Optional[str],
        include_expired: bool
    ) -> List[MemorySearchResult]:
        """Fallback text-based search when embeddings fail."""
        try:
            async with self.db_pool.acquire() as conn:
                type_filter = ""
                params = [org_id, f"%{query}%", limit]
                param_idx = 4
                
                if memory_types:
                    type_filter = f"AND memory_type = ANY(${param_idx})"
                    params.append([mt.value for mt in memory_types])
                    param_idx += 1
                
                if ticket_id:
                    type_filter += f" AND ticket_id = ${param_idx}"
                    params.append(ticket_id)
                    param_idx += 1
                
                if not include_expired:
                    type_filter += " AND (expires_at IS NULL OR expires_at > NOW())"
                
                rows = await conn.fetch(
                    f"""
                    SELECT * FROM memory_archive 
                    WHERE org_id = $1 
                    AND content ILIKE $2
                    {type_filter}
                    ORDER BY created_at DESC
                    LIMIT $3
                    """,
                    *params
                )
                
                results = []
                for row in rows:
                    entry = MemoryEntry.from_dict(dict(row), self.encryption_key)
                    results.append(MemorySearchResult(
                        memory=entry,
                        similarity_score=0.5,  # Default for text match
                        match_type="exact",
                        matched_fields=["content"]
                    ))
                
                return results
        except Exception as e:
            logger.error(f"Text search failed: {e}")
            return []
    
    async def _search_archive(
        self,
        query_embedding: List[float],
        org_id: str,
        limit: int,
        memory_types: Optional[List[MemoryType]],
        ticket_id: Optional[str],
        min_similarity: float,
        include_expired: bool
    ) -> List[MemorySearchResult]:
        """Search memory_archive using vector similarity."""
        try:
            async with self.db_pool.acquire() as conn:
                # Build query with optional filters
                filters = ["org_id = $1"]
                params = [org_id, query_embedding, limit, min_similarity]
                param_idx = 5
                
                if memory_types:
                    filters.append(f"memory_type = ANY(${param_idx})")
                    params.append([mt.value for mt in memory_types])
                    param_idx += 1
                
                if ticket_id:
                    filters.append(f"ticket_id = ${param_idx}")
                    params.append(ticket_id)
                    param_idx += 1
                
                if not include_expired:
                    filters.append("(expires_at IS NULL OR expires_at > NOW())")
                
                where_clause = " AND ".join(filters)
                
                rows = await conn.fetch(
                    f"""
                    SELECT *, 
                        1 - (embedding <=> $2) as similarity
                    FROM memory_archive 
                    WHERE {where_clause}
                    AND embedding IS NOT NULL
                    AND 1 - (embedding <=> $2) >= $4
                    ORDER BY embedding <=> $2
                    LIMIT $3
                    """,
                    *params
                )
                
                results = []
                for row in rows:
                    entry = MemoryEntry.from_dict(dict(row), self.encryption_key)
                    results.append(MemorySearchResult(
                        memory=entry,
                        similarity_score=row["similarity"],
                        match_type="semantic",
                        matched_fields=["embedding"]
                    ))
                
                return results
        except Exception as e:
            logger.error(f"Archive search failed: {e}")
            return []
    
    async def delete_memory(self, memory_id: str, org_id: str) -> bool:
        """
        Delete a memory entry.
        
        Args:
            memory_id: Memory to delete
            org_id: Organization ID for verification
            
        Returns:
            True if deleted, False if not found or unauthorized
        """
        logger.debug(f"Deleting memory {memory_id} for org {org_id}")
        
        deleted = False
        
        # Delete from Supermemory
        try:
            client = await self._get_http_client()
            response = await client.delete(f"/memories/{memory_id}")
            if response.status_code in (200, 204):
                deleted = True
                logger.debug(f"Deleted from Supermemory: {memory_id}")
        except httpx.HTTPError as e:
            logger.warning(f"Supermemory delete failed: {e}")
        
        # Delete from archive
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute(
                    """
                    DELETE FROM memory_archive 
                    WHERE memory_id = $1 AND org_id = $2
                    """,
                    memory_id, org_id
                )
                if result != "DELETE 0":
                    deleted = True
                    logger.debug(f"Deleted from archive: {memory_id}")
        except Exception as e:
            logger.error(f"Archive delete failed: {e}")
        
        return deleted
    
    async def close(self):
        """Close HTTP client and cleanup resources."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            logger.debug("MemoryManager HTTP client closed")

    async def add_memory_atomic(
        self,
        entry: MemoryEntry,
        encrypt: bool = False,
        generate_embedding: bool = True
    ) -> str:
        """
        Atomically add a memory entry to both Supermemory and archive.
        
        This method ensures that either BOTH writes succeed, or NEITHER
        is persisted. This maintains consistency between Supermemory
        (primary) and memory_archive (backup).
        
        The atomic write process:
        1. Generate memory_id and embedding
        2. Encrypt content if requested (before any writes)
        3. Start database transaction
        4. Write to archive first (within transaction)
        5. Write to Supermemory
        6. If Supermemory succeeds, commit transaction
        7. If Supermemory fails, rollback transaction
        
        Args:
            entry: The memory entry to add
            encrypt: Whether to encrypt the content (before any writes)
            generate_embedding: Whether to generate embedding if missing
            
        Returns:
            The memory ID of the created entry
            
        Raises:
            MemoryWriteError: If either write fails (both are rolled back)
            AtomicWriteError: If atomicity cannot be guaranteed
        """
        # Generate memory ID if not provided
        if not entry.memory_id:
            content_str = entry.content if isinstance(entry.content, str) else json.dumps(entry.content)
            entry.memory_id = self._generate_memory_id(content_str, entry.org_id)
        
        logger.debug(f"Starting atomic write for memory {entry.memory_id}")
        
        # Generate embedding if needed and requested
        if generate_embedding and entry.embedding is None:
            entry.embedding = await self._generate_embedding(entry.content)
        
        # Encrypt content if requested (do this once, before any writes)
        encryption_key = self.encryption_key if encrypt else None
        if encrypt and encryption_key:
            entry_dict = entry.to_dict(encrypt=True, encryption_key=encryption_key)
            encrypted_content = entry_dict["content"]
        else:
            encrypted_content = None
        
        # Use a transaction to ensure atomicity
        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                try:
                    # Step 1: Write to archive FIRST (within transaction)
                    # This is the "prepare" phase - we can rollback if needed
                    entry_dict = entry.to_dict(
                        encrypt=encrypt and encryption_key is not None,
                        encryption_key=encryption_key
                    )
                    
                    await conn.execute(
                        """
                        INSERT INTO memory_archive (
                            memory_id, content, memory_type, org_id, ticket_id, user_id,
                            priority, metadata, embedding, created_at, expires_at, tags, encrypted
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                        ON CONFLICT (memory_id) DO UPDATE SET
                            content = EXCLUDED.content,
                            metadata = EXCLUDED.metadata,
                            updated_at = NOW()
                        """,
                        entry.memory_id,
                        json.dumps(entry_dict["content"]) if isinstance(entry_dict["content"], dict) else entry_dict["content"],
                        entry.memory_type.value,
                        entry.org_id,
                        entry.ticket_id,
                        entry.user_id,
                        entry.priority.value,
                        json.dumps(entry.metadata),
                        entry.embedding,
                        entry.created_at,
                        entry.expires_at,
                        entry.tags,
                        encrypt
                    )
                    
                    logger.debug(f"Archive write prepared for {entry.memory_id}")
                    
                    # Step 2: Write to Supermemory (the "commit" phase)
                    # If this fails, the transaction will be rolled back
                    try:
                        await self._write_to_supermemory(entry, encrypt)
                        logger.debug(f"Supermemory write successful for {entry.memory_id}")
                    except Exception as e:
                        # Supermemory write failed - transaction will rollback
                        logger.error(f"Supermemory write failed, rolling back: {e}")
                        raise AtomicWriteError(
                            f"Supermemory write failed, archive write rolled back: {e}"
                        )
                    
                    # Step 3: Both writes succeeded - transaction commits
                    logger.info(f"Atomic write completed for memory {entry.memory_id}")
                    
                except Exception as e:
                    # Any exception will cause rollback due to transaction context
                    logger.error(f"Atomic write failed for {entry.memory_id}: {e}")
                    raise
        
        return entry.memory_id

    async def add_memory_with_map(
        self,
        entry: MemoryEntry,
        ticket_id: Optional[str] = None,
        db1_ticket_id: Optional[str] = None,
        db1_table: str = "tickets",
        encrypt: bool = False,
        generate_embedding: bool = True
    ) -> str:
        """
        Add memory with automatic map table entry creation.
        
        This is a three-phase atomic write:
        1. Write to Supermemory
        2. Write to memory_archive
        3. Write to memory_ticket_map
        
        All three operations are wrapped in a transaction.
        
        Args:
            entry: The memory entry to add
            ticket_id: Optional ticket ID to associate with memory
            db1_ticket_id: DB1 ticket ID for map table (defaults to ticket_id)
            db1_table: DB1 table name for map table
            encrypt: Whether to encrypt content
            generate_embedding: Whether to generate embedding
            
        Returns:
            The memory ID of the created entry
        """
        # Use ticket_id from entry if not provided
        if ticket_id is None:
            ticket_id = entry.ticket_id
        
        # Use ticket_id as db1_ticket_id if not specified
        if db1_ticket_id is None and ticket_id:
            db1_ticket_id = ticket_id
        
        # Generate memory ID if not provided
        if not entry.memory_id:
            content_str = entry.content if isinstance(entry.content, str) else json.dumps(entry.content)
            entry.memory_id = self._generate_memory_id(content_str, entry.org_id)
        
        # Update entry with ticket_id
        entry.ticket_id = ticket_id
        
        logger.debug(f"Starting atomic write with map for memory {entry.memory_id}")
        
        # Generate embedding if needed
        if generate_embedding and entry.embedding is None:
            entry.embedding = await self._generate_embedding(entry.content)
        
        # Use transaction for atomicity
        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                try:
                    # Step 1: Write to archive
                    entry_dict = entry.to_dict(
                        encrypt=encrypt and self.encryption_key is not None,
                        encryption_key=self.encryption_key
                    )
                    
                    await conn.execute(
                        """
                        INSERT INTO memory_archive (
                            memory_id, content, memory_type, org_id, ticket_id, user_id,
                            priority, metadata, embedding, created_at, expires_at, tags, encrypted
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                        ON CONFLICT (memory_id) DO UPDATE SET
                            content = EXCLUDED.content,
                            metadata = EXCLUDED.metadata,
                            updated_at = NOW()
                        """,
                        entry.memory_id,
                        json.dumps(entry_dict["content"]) if isinstance(entry_dict["content"], dict) else entry_dict["content"],
                        entry.memory_type.value,
                        entry.org_id,
                        entry.ticket_id,
                        entry.user_id,
                        entry.priority.value,
                        json.dumps(entry.metadata),
                        entry.embedding,
                        entry.created_at,
                        entry.expires_at,
                        entry.tags,
                        encrypt
                    )
                    
                    # Step 2: Create map table entry if ticket association exists
                    if ticket_id and db1_ticket_id:
                        await conn.execute(
                            """
                            INSERT INTO memory_ticket_map (
                                memory_id, ticket_id, db1_ticket_id, db1_table,
                                org_id, created_at, resolution_status
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                            ON CONFLICT (memory_id, ticket_id) DO UPDATE SET
                                updated_at = NOW()
                            """,
                            entry.memory_id,
                            ticket_id,
                            db1_ticket_id,
                            db1_table,
                            entry.org_id,
                            entry.created_at,
                            'active'
                        )
                        logger.debug(f"Map entry created for memory {entry.memory_id} -> ticket {ticket_id}")
                    
                    # Step 3: Write to Supermemory (must succeed for commit)
                    try:
                        await self._write_to_supermemory(entry, encrypt)
                    except Exception as e:
                        logger.error(f"Supermemory write failed, rolling back: {e}")
                        raise AtomicWriteError(f"Supermemory write failed: {e}")
                    
                    logger.info(f"Atomic write with map completed for memory {entry.memory_id}")
                    
                except Exception as e:
                    logger.error(f"Atomic write with map failed: {e}")
                    raise
        
        return entry.memory_id


class MemoryWriteError(Exception):
    """Raised when memory write operation fails."""
    pass


class MemoryNotFoundError(Exception):
    """Raised when requested memory is not found."""
    pass


class AtomicWriteError(Exception):
    """Raised when atomic memory write fails and is rolled back."""
    pass
