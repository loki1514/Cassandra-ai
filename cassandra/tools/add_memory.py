"""
T14: Secure Tool Registry — add_memory (Atomic Map Write)

This module implements the add_memory tool for the Cassandra AI RAG system.
It provides atomic write operations that ensure consistency between:
- Supermemory (primary vector store)
- memory_archive (local backup)
- memory_ticket_map (association table)

Key Features:
- Atomic writes: all succeed or all fail
- Encryption before Supermemory write
- Automatic map table entry creation
- Organization-scoped memory isolation
- Idempotency support

Security:
- org_id from JWT context only
- Content encryption using per-org KMS keys
- Input validation and sanitization
- Audit logging
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union
from enum import Enum

from pydantic import BaseModel, Field, validator, field_validator

# Import from RAG module
from ..rag.memory_manager import (
    MemoryManager, 
    MemoryEntry, 
    MemoryType, 
    MemoryPriority,
    SupermemoryConfig
)
from ..rag.idempotency import generate_idempotency_key, EventType
from ..encryption import EncryptionService

# Configure logging
logger = logging.getLogger(__name__)


class AddMemoryInput(BaseModel):
    """
    Input model for add_memory tool.
    
    Validates and sanitizes input parameters for memory creation.
    Note: org_id is NOT included - it must come from JWT context.
    
    Attributes:
        content: Memory content (text or structured data, required)
        memory_type: Type of memory (default: ticket_context)
        ticket_id: Optional associated ticket ID
        priority: Memory priority (default: medium)
        tags: List of searchable tags
        metadata: Additional structured metadata
        encrypt: Whether to encrypt the content
        expires_in_hours: Optional expiration time in hours
        idempotency_key: Optional key for deduplication
        generate_embedding: Whether to generate vector embedding
    """
    content: Union[str, Dict[str, Any]] = Field(
        ...,
        description="Memory content (text or structured data)"
    )
    memory_type: MemoryType = Field(
        default=MemoryType.TICKET_CONTEXT,
        description="Type of memory entry"
    )
    ticket_id: Optional[str] = Field(
        default=None,
        description="Associated ticket ID"
    )
    priority: MemoryPriority = Field(
        default=MemoryPriority.MEDIUM,
        description="Memory priority level"
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Searchable tags"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata"
    )
    encrypt: bool = Field(
        default=True,
        description="Whether to encrypt content before storage"
    )
    expires_in_hours: Optional[int] = Field(
        default=None,
        ge=1,
        le=8760,  # Max 1 year
        description="Optional expiration time in hours"
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Idempotency key for deduplication"
    )
    generate_embedding: bool = Field(
        default=True,
        description="Whether to generate vector embedding"
    )
    
    @field_validator('content')
    @classmethod
    def validate_content(cls, v: Union[str, Dict[str, Any]]) -> Union[str, Dict[str, Any]]:
        """Validate and sanitize content."""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("Content cannot be empty or whitespace only")
            # Remove null bytes and other dangerous characters
            v = v.replace('\x00', '')
            # Limit size (10KB for text)
            if len(v) > 10240:
                v = v[:10240]
                logger.warning("Content truncated to 10KB")
        elif isinstance(v, dict):
            if not v:
                raise ValueError("Content dict cannot be empty")
            # Limit dict size by converting to JSON
            json_str = json.dumps(v)
            if len(json_str) > 10240:
                raise ValueError("Content dict exceeds 10KB when serialized")
        return v
    
    @field_validator('tags')
    @classmethod
    def validate_tags(cls, v: List[str]) -> List[str]:
        """Sanitize and deduplicate tags."""
        cleaned = []
        seen = set()
        for tag in v[:20]:  # Max 20 tags
            if isinstance(tag, str):
                tag = tag.strip().lower()[:50]  # Max 50 chars
                if tag and tag not in seen:
                    cleaned.append(tag)
                    seen.add(tag)
        return cleaned
    
    @field_validator('metadata')
    @classmethod
    def validate_metadata(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Validate metadata size."""
        json_str = json.dumps(v)
        if len(json_str) > 4096:  # 4KB limit
            raise ValueError("Metadata exceeds 4KB when serialized")
        return v
    
    def to_memory_entry(self, org_id: str, user_id: Optional[str] = None) -> MemoryEntry:
        """Convert input to MemoryEntry."""
        expires_at = None
        if self.expires_in_hours:
            expires_at = datetime.utcnow() + timedelta(hours=self.expires_in_hours)
        
        return MemoryEntry(
            content=self.content,
            memory_type=self.memory_type,
            org_id=org_id,
            ticket_id=self.ticket_id,
            user_id=user_id,
            priority=self.priority,
            metadata=self.metadata,
            tags=self.tags,
            expires_at=expires_at
        )
    
    class Config:
        json_schema_extra = {
            "example": {
                "content": "Customer reported login timeout issue at 3pm",
                "memory_type": "ticket_context",
                "ticket_id": "TICKET-1234",
                "priority": "high",
                "tags": ["login", "timeout", "customer-report"],
                "encrypt": True,
                "expires_in_hours": 168
            }
        }


class AddMemoryResult(BaseModel):
    """
    Result model for add_memory tool.
    
    Returns the created memory details with all generated fields.
    
    Attributes:
        memory_id: Unique memory identifier
        created_at: ISO timestamp of creation
        status: Operation status ('success' or 'duplicate')
        org_id: Organization ID (echoed from JWT)
        ticket_id: Associated ticket ID (if any)
        encrypted: Whether content was encrypted
        embedding_generated: Whether embedding was generated
        idempotency_key: Echoed idempotency key
    """
    memory_id: str = Field(..., description="Unique memory identifier")
    created_at: str = Field(..., description="ISO 8601 timestamp")
    status: str = Field(..., description="Operation status")
    org_id: str = Field(..., description="Organization ID from JWT")
    ticket_id: Optional[str] = Field(default=None, description="Associated ticket ID")
    encrypted: bool = Field(..., description="Whether content was encrypted")
    embedding_generated: bool = Field(..., description="Whether embedding was generated")
    idempotency_key: Optional[str] = Field(default=None, description="Idempotency key")
    map_entry_created: bool = Field(
        default=False,
        description="Whether map table entry was created"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "memory_id": "mem_abc123def456",
                "created_at": "2024-01-15T10:30:00Z",
                "status": "success",
                "org_id": "org_12345",
                "ticket_id": "TICKET-1234",
                "encrypted": True,
                "embedding_generated": True,
                "map_entry_created": True
            }
        }


class AddMemoryTool:
    """
    Secure tool for adding memories with atomic map writes.
    
    This tool implements atomic write operations:
    1. Encrypt content (if requested) before any writes
    2. Write to memory_archive (within transaction)
    3. Write to memory_ticket_map (within transaction, if ticket_id provided)
    4. Write to Supermemory
    5. Commit transaction only if Supermemory succeeds
    
    If any step fails, the entire operation is rolled back.
    
    Security:
    - org_id from JWT context only
    - Content encrypted using per-org KMS keys
    - All operations logged for audit
    
    Usage:
        tool = AddMemoryTool(memory_manager, db_pool, encryption_service)
        
        result = await tool.add(
            input_data=AddMemoryInput(content="..."),
            org_id="org_from_jwt",
            user_id="user_from_jwt"
        )
    """
    
    def __init__(
        self,
        memory_manager: MemoryManager,
        db_pool: Any,
        encryption_service: Optional[EncryptionService] = None
    ):
        """
        Initialize the add_memory tool.
        
        Args:
            memory_manager: MemoryManager instance for Supermemory operations
            db_pool: Database connection pool for archive and map table
            encryption_service: Optional EncryptionService for KMS encryption
        """
        self.memory_manager = memory_manager
        self.db_pool = db_pool
        self.encryption_service = encryption_service
        logger.info("AddMemoryTool initialized")
    
    async def _check_idempotency(
        self,
        idempotency_key: str,
        org_id: str
    ) -> Optional[Dict[str, Any]]:
        """Check if memory was already created with this idempotency key."""
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT memory_id, created_at, ticket_id, encrypted
                    FROM memory_archive
                    WHERE idempotency_key = $1 AND org_id = $2
                    """,
                    idempotency_key,
                    org_id
                )
                
                if row:
                    logger.info(f"Idempotency hit: memory exists for key {idempotency_key}")
                    return dict(row)
                
                return None
        except Exception as e:
            logger.error(f"Idempotency check failed: {e}")
            return None
    
    async def _encrypt_content(
        self,
        content: Union[str, Dict[str, Any]],
        org_id: str
    ) -> Dict[str, str]:
        """
        Encrypt content using per-organization KMS key.
        
        Args:
            content: Content to encrypt
            org_id: Organization ID for key selection
            
        Returns:
            Encrypted content dict with ciphertext and encrypted_data_key
        """
        if not self.encryption_service:
            logger.warning("No encryption service available, skipping encryption")
            return {"content": content}
        
        try:
            encrypted = self.encryption_service.encrypt(content, org_id)
            logger.debug(f"Content encrypted for org {org_id}")
            return encrypted
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise MemoryEncryptionError(f"Failed to encrypt content: {e}")
    
    async def add(
        self,
        input_data: AddMemoryInput,
        org_id: str,
        user_id: Optional[str] = None
    ) -> AddMemoryResult:
        """
        Add a memory with atomic map write.
        
        Args:
            input_data: Validated AddMemoryInput
            org_id: Organization ID from JWT (MUST NOT come from request body)
            user_id: User ID from JWT for audit trail
            
        Returns:
            AddMemoryResult with created memory details
            
        Raises:
            ValueError: If org_id is missing
            MemoryWriteError: If atomic write fails
        """
        # SECURITY: Verify org_id is provided (from JWT)
        if not org_id:
            raise ValueError("org_id is required and must come from JWT context")
        
        # Generate or use provided idempotency key
        idempotency_key = input_data.idempotency_key
        if not idempotency_key:
            idempotency_key = generate_idempotency_key(
                entity_id=input_data.ticket_id or "memory",
                event_type=EventType.MEMORY_CREATED,
                additional_data={"content_hash": hashlib.sha256(
                    json.dumps(input_data.content, default=str).encode()
                ).hexdigest()[:16]}
            )
        
        # Check idempotency
        existing = await self._check_idempotency(idempotency_key, org_id)
        if existing:
            logger.info(f"Returning existing memory {existing['memory_id']} due to idempotency")
            return AddMemoryResult(
                memory_id=existing['memory_id'],
                created_at=existing['created_at'].isoformat() if isinstance(existing['created_at'], datetime) else existing['created_at'],
                status="duplicate",
                org_id=org_id,
                ticket_id=existing.get('ticket_id'),
                encrypted=existing.get('encrypted', False),
                embedding_generated=True,
                idempotency_key=idempotency_key,
                map_entry_created=existing.get('ticket_id') is not None
            )
        
        # Create memory entry
        memory_entry = input_data.to_memory_entry(org_id, user_id)
        created_at = datetime.utcnow()
        
        logger.info(f"Adding memory for org {org_id}, ticket {input_data.ticket_id}")
        
        try:
            # Use atomic write with map
            memory_id = await self.memory_manager.add_memory_with_map(
                entry=memory_entry,
                ticket_id=input_data.ticket_id,
                encrypt=input_data.encrypt,
                generate_embedding=input_data.generate_embedding
            )
            
            # Store idempotency key
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE memory_archive
                    SET idempotency_key = $1
                    WHERE memory_id = $2 AND org_id = $3
                    """,
                    idempotency_key,
                    memory_id,
                    org_id
                )
            
            logger.info(f"Memory {memory_id} added successfully")
            
            return AddMemoryResult(
                memory_id=memory_id,
                created_at=created_at.isoformat(),
                status="success",
                org_id=org_id,
                ticket_id=input_data.ticket_id,
                encrypted=input_data.encrypt,
                embedding_generated=input_data.generate_embedding,
                idempotency_key=idempotency_key,
                map_entry_created=input_data.ticket_id is not None
            )
            
        except Exception as e:
            logger.error(f"Memory add failed: {e}")
            raise MemoryWriteError(f"Failed to add memory: {e}")
    
    async def add_batch(
        self,
        inputs: List[AddMemoryInput],
        org_id: str,
        user_id: Optional[str] = None
    ) -> List[AddMemoryResult]:
        """
        Add multiple memories in a batch.
        
        Each memory is added independently with its own atomic write.
        
        Args:
            inputs: List of AddMemoryInput
            org_id: Organization ID from JWT
            user_id: User ID from JWT for audit
            
        Returns:
            List of AddMemoryResult
        """
        results = []
        for input_data in inputs:
            try:
                result = await self.add(input_data, org_id, user_id)
                results.append(result)
            except Exception as e:
                logger.error(f"Batch memory add failed for item: {e}")
                raise
        return results


class MemoryWriteError(Exception):
    """Raised when memory write operation fails."""
    pass


class MemoryEncryptionError(Exception):
    """Raised when memory encryption fails."""
    pass


# Convenience function for direct usage
async def add_memory(
    memory_manager: MemoryManager,
    db_pool: Any,
    content: Union[str, Dict[str, Any]],
    org_id: str,
    memory_type: MemoryType = MemoryType.TICKET_CONTEXT,
    ticket_id: Optional[str] = None,
    priority: MemoryPriority = MemoryPriority.MEDIUM,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    encrypt: bool = True,
    expires_in_hours: Optional[int] = None,
    idempotency_key: Optional[str] = None,
    generate_embedding: bool = True,
    user_id: Optional[str] = None,
    encryption_service: Optional[EncryptionService] = None
) -> AddMemoryResult:
    """
    Convenience function to add a memory with minimal boilerplate.
    
    Args:
        memory_manager: MemoryManager instance
        db_pool: Database connection pool
        content: Memory content
        org_id: Organization ID from JWT (REQUIRED, from JWT only)
        memory_type: Type of memory
        ticket_id: Associated ticket ID
        priority: Memory priority
        tags: List of tags
        metadata: Additional metadata
        encrypt: Whether to encrypt
        expires_in_hours: Expiration time
        idempotency_key: Idempotency key
        generate_embedding: Whether to generate embedding
        user_id: User ID from JWT for audit
        encryption_service: Optional EncryptionService
        
    Returns:
        AddMemoryResult with created memory details
        
    Example:
        result = await add_memory(
            memory_manager=mm,
            db_pool=pool,
            content="Customer reported issue...",
            org_id="org_from_jwt",
            ticket_id="TICKET-1234",
            encrypt=True
        )
        print(result.memory_id)
    """
    input_data = AddMemoryInput(
        content=content,
        memory_type=memory_type,
        ticket_id=ticket_id,
        priority=priority,
        tags=tags or [],
        metadata=metadata or {},
        encrypt=encrypt,
        expires_in_hours=expires_in_hours,
        idempotency_key=idempotency_key,
        generate_embedding=generate_embedding
    )
    
    tool = AddMemoryTool(memory_manager, db_pool, encryption_service)
    return await tool.add(input_data, org_id, user_id)
