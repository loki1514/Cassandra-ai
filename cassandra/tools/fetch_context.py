"""
T15: Secure Tool Registry — fetch_context (SELECT Only)

This module implements the fetch_context tool for the Cassandra AI RAG system.
It provides secure, read-only access to context with:
- Semantic search → map table → DB1 resolution
- Decryption of encrypted payloads
- Merging memory + live DB1 status
- Latency optimization (<300ms p95)

Security Features:
- SELECT-only operations (no writes)
- org_id from JWT context only
- Organization-scoped data isolation
- Input validation and sanitization

Performance Features:
- Parallel processing for multiple memories
- Timeout-controlled operations
- Intelligent caching
- Batch processing
"""

import hashlib
import json
import logging
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from enum import Enum

from pydantic import BaseModel, Field, field_validator

# Import from RAG module
from ..rag.context_fetcher import (
    ContextFetcher,
    FetchContextInput as BaseFetchContextInput,
    FetchContextResult as BaseFetchContextResult,
    ContextItem,
    ContextSource,
    ConflictResolution
)
from ..rag.memory_manager import MemoryManager, MemoryType
from ..encryption import EncryptionService

# Configure logging
logger = logging.getLogger(__name__)


class FetchContextInput(BaseModel):
    """
    Input model for fetch_context tool.
    
    Validates and sanitizes input parameters for context fetching.
    Note: org_id is NOT included - it must come from JWT context.
    
    Attributes:
        query_text: Natural language query for semantic search
        ticket_id: Optional specific ticket to scope search
        user_id: Optional user context for personalization
        max_results: Maximum context items to return (1-50)
        min_confidence: Minimum relevance threshold (0-1)
        include_history: Include conversation history
        include_decisions: Include past decisions
        include_knowledge: Include knowledge base articles
        memory_types: Specific memory types to include
        timeout_ms: Maximum time for fetch (100-5000ms)
        use_cache: Whether to use caching
    """
    query_text: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Natural language query for semantic search"
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
        le=50,
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
        description="Include conversation history"
    )
    include_decisions: bool = Field(
        default=True,
        description="Include past decisions"
    )
    include_knowledge: bool = Field(
        default=True,
        description="Include knowledge base articles"
    )
    memory_types: Optional[List[str]] = Field(
        default=None,
        description="Specific memory types to include"
    )
    timeout_ms: int = Field(
        default=300,
        ge=100,
        le=5000,
        description="Maximum time for fetch in milliseconds"
    )
    use_cache: bool = Field(
        default=True,
        description="Whether to use caching"
    )
    
    @field_validator('query_text')
    @classmethod
    def validate_query(cls, v: str) -> str:
        """Sanitize and validate query text."""
        v = v.strip()
        if not v:
            raise ValueError("Query text cannot be empty")
        # Remove null bytes
        v = v.replace('\x00', '')
        # Limit length
        if len(v) > 1000:
            v = v[:1000]
            logger.warning("Query text truncated to 1000 chars")
        return v
    
    def to_base_input(self, org_id: str) -> BaseFetchContextInput:
        """Convert to base FetchContextInput."""
        # Build memory types list
        memory_types = []
        if self.memory_types:
            memory_types = [MemoryType(mt) for mt in self.memory_types]
        else:
            if self.include_history:
                memory_types.append(MemoryType.CONVERSATION)
            if self.include_decisions:
                memory_types.append(MemoryType.DECISION)
            if self.include_knowledge:
                memory_types.append(MemoryType.KNOWLEDGE)
        
        if not memory_types:
            memory_types = None
        
        return BaseFetchContextInput(
            query_text=self.query_text,
            org_id=org_id,
            ticket_id=self.ticket_id,
            user_id=self.user_id,
            max_results=self.max_results,
            min_confidence=self.min_confidence,
            include_history=self.include_history,
            include_decisions=self.include_decisions,
            include_knowledge=self.include_knowledge,
            conflict_resolution=ConflictResolution.DB1_WINS,
            timeout_ms=self.timeout_ms
        )
    
    class Config:
        json_schema_extra = {
            "example": {
                "query_text": "What was the resolution for the login timeout issue?",
                "ticket_id": "TICKET-1234",
                "max_results": 5,
                "min_confidence": 0.75,
                "timeout_ms": 300
            }
        }


class ContextResultItem(BaseModel):
    """
    Single context item in the result.
    
    Simplified representation for API responses.
    """
    content: Union[str, Dict[str, Any]] = Field(..., description="Context content")
    source: str = Field(..., description="Source of context")
    memory_id: Optional[str] = Field(default=None, description="Memory ID if applicable")
    ticket_id: Optional[str] = Field(default=None, description="Ticket ID if applicable")
    confidence: float = Field(default=1.0, description="Relevance confidence")
    timestamp: str = Field(..., description="ISO timestamp")
    
    class Config:
        json_schema_extra = {
            "example": {
                "content": "Customer reported login timeout at 3pm",
                "source": "memory_archive",
                "memory_id": "mem_abc123",
                "ticket_id": "TICKET-1234",
                "confidence": 0.92,
                "timestamp": "2024-01-15T10:30:00Z"
            }
        }


class FetchContextResult(BaseModel):
    """
    Result model for fetch_context tool.
    
    Returns assembled context with performance metrics.
    
    Attributes:
        items: List of context items
        query: Original query
        org_id: Organization ID from JWT
        total_found: Total memories found
        items_returned: Number of items in result
        fetch_time_ms: Actual fetch time in milliseconds
        cache_hit: Whether result was from cache
        sources_used: List of data sources used
        latency_status: Whether latency target was met
    """
    items: List[ContextResultItem] = Field(
        default_factory=list,
        description="Context items"
    )
    query: str = Field(..., description="Original query")
    org_id: str = Field(..., description="Organization ID from JWT")
    total_found: int = Field(default=0, description="Total memories found")
    items_returned: int = Field(default=0, description="Number of items returned")
    fetch_time_ms: float = Field(default=0.0, description="Fetch time in ms")
    cache_hit: bool = Field(default=False, description="Whether from cache")
    sources_used: List[str] = Field(
        default_factory=list,
        description="Data sources used"
    )
    latency_status: str = Field(
        default="unknown",
        description="Latency status: 'optimal', 'acceptable', 'slow'"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "items": [],
                "query": "What was the resolution?",
                "org_id": "org_12345",
                "total_found": 10,
                "items_returned": 5,
                "fetch_time_ms": 150.5,
                "cache_hit": False,
                "sources_used": ["memory_archive", "db1_ticket"],
                "latency_status": "optimal"
            }
        }


class FetchContextTool:
    """
    Secure tool for fetching context (SELECT only).
    
    This tool implements read-only context retrieval:
    1. Semantic search on memory_archive
    2. Map table lookup for ticket associations
    3. DB1 resolution for authoritative data
    4. Decryption of encrypted payloads
    5. Merging memory + live DB1 status
    
    Performance Target: <300ms p95
    
    Security:
    - SELECT-only operations (no writes)
    - org_id from JWT context only
    - Organization-scoped data isolation
    
    Usage:
        tool = FetchContextTool(memory_manager, db_pool, cache_client)
        
        result = await tool.fetch(
            input_data=FetchContextInput(query_text="..."),
            org_id="org_from_jwt"
        )
    """
    
    # Latency thresholds
    LATENCY_OPTIMAL_MS = 150   # <150ms is optimal
    LATENCY_ACCEPTABLE_MS = 300  # <300ms is acceptable
    # >300ms is slow (exceeds p95 target)
    
    def __init__(
        self,
        memory_manager: MemoryManager,
        db_pool: Any,
        cache_client: Optional[Any] = None,
        encryption_service: Optional[EncryptionService] = None
    ):
        """
        Initialize the fetch_context tool.
        
        Args:
            memory_manager: MemoryManager for semantic search
            db_pool: Database connection pool for map table and DB1
            cache_client: Optional cache client (Redis)
            encryption_service: Optional EncryptionService for decryption
        """
        self.memory_manager = memory_manager
        self.db_pool = db_pool
        self.cache_client = cache_client
        self.encryption_service = encryption_service
        
        # Initialize context fetcher
        self.context_fetcher = ContextFetcher(
            memory_manager=memory_manager,
            db_pool=db_pool,
            cache_client=cache_client,
            encryption_service=encryption_service
        )
        
        logger.info("FetchContextTool initialized")
    
    def _get_latency_status(self, fetch_time_ms: float) -> str:
        """Determine latency status based on fetch time."""
        if fetch_time_ms < self.LATENCY_OPTIMAL_MS:
            return "optimal"
        elif fetch_time_ms < self.LATENCY_ACCEPTABLE_MS:
            return "acceptable"
        else:
            return "slow"
    
    async def fetch(
        self,
        input_data: FetchContextInput,
        org_id: str
    ) -> FetchContextResult:
        """
        Fetch context for the given query.
        
        Args:
            input_data: Validated FetchContextInput
            org_id: Organization ID from JWT (MUST NOT come from request body)
            
        Returns:
            FetchContextResult with assembled context
            
        Raises:
            ValueError: If org_id is missing
        """
        # SECURITY: Verify org_id is provided (from JWT)
        if not org_id:
            raise ValueError("org_id is required and must come from JWT context")
        
        logger.info(f"Fetching context for org {org_id}: '{input_data.query_text[:50]}...'")
        
        start_time = datetime.utcnow()
        
        try:
            # Convert to base input with org_id
            base_input = input_data.to_base_input(org_id)
            
            # Fetch context using context fetcher
            base_result = await self.context_fetcher.fetch_context(base_input)
            
            # Convert items to simplified format
            items = []
            for item in base_result.items:
                items.append(ContextResultItem(
                    content=item.content,
                    source=item.source.value,
                    memory_id=item.memory_id,
                    ticket_id=item.ticket_id,
                    confidence=item.confidence,
                    timestamp=item.timestamp.isoformat()
                ))
            
            fetch_time_ms = base_result.fetch_time_ms
            
            return FetchContextResult(
                items=items,
                query=input_data.query_text,
                org_id=org_id,
                total_found=base_result.total_found,
                items_returned=len(items),
                fetch_time_ms=fetch_time_ms,
                cache_hit=base_result.cache_hit,
                sources_used=[s.value for s in base_result.sources_used],
                latency_status=self._get_latency_status(fetch_time_ms)
            )
            
        except Exception as e:
            logger.error(f"Context fetch failed: {e}")
            
            # Calculate elapsed time even on error
            elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            
            return FetchContextResult(
                items=[],
                query=input_data.query_text,
                org_id=org_id,
                total_found=0,
                items_returned=0,
                fetch_time_ms=elapsed_ms,
                cache_hit=False,
                sources_used=[],
                latency_status="error"
            )
    
    async def fetch_stream(
        self,
        input_data: FetchContextInput,
        org_id: str
    ):
        """
        Fetch context as a stream (for large results).
        
        Yields context items one at a time as they are retrieved.
        
        Args:
            input_data: FetchContextInput
            org_id: Organization ID from JWT
            
        Yields:
            ContextResultItem objects
        """
        if not org_id:
            raise ValueError("org_id is required and must come from JWT context")
        
        logger.info(f"Streaming context for org {org_id}")
        
        base_input = input_data.to_base_input(org_id)
        
        # Get memory results
        memory_types = []
        if input_data.include_history:
            memory_types.append(MemoryType.CONVERSATION)
        if input_data.include_decisions:
            memory_types.append(MemoryType.DECISION)
        if input_data.include_knowledge:
            memory_types.append(MemoryType.KNOWLEDGE)
        
        try:
            memory_results = await self.memory_manager.search_memories(
                query=input_data.query_text,
                org_id=org_id,
                limit=input_data.max_results,
                memory_types=memory_types or None,
                ticket_id=input_data.ticket_id,
                min_similarity=input_data.min_confidence
            )
            
            for memory_result in memory_results:
                memory = memory_result.memory
                
                # Decrypt if needed
                if self.encryption_service and memory.metadata.get('encrypted'):
                    try:
                        memory = await self.context_fetcher._decrypt_memory(memory)
                    except Exception as e:
                        logger.warning(f"Decryption failed: {e}")
                        continue
                
                yield ContextResultItem(
                    content=memory.content,
                    source=ContextSource.MEMORY_ARCHIVE.value,
                    memory_id=memory.memory_id,
                    ticket_id=memory.ticket_id,
                    confidence=memory_result.similarity_score,
                    timestamp=memory.created_at.isoformat() if memory.created_at else datetime.utcnow().isoformat()
                )
                
        except Exception as e:
            logger.error(f"Stream fetch failed: {e}")
            raise


class ContextFetchError(Exception):
    """Raised when context fetch fails."""
    pass


# Convenience function for direct usage
async def fetch_context(
    memory_manager: MemoryManager,
    db_pool: Any,
    query_text: str,
    org_id: str,
    ticket_id: Optional[str] = None,
    max_results: int = 10,
    min_confidence: float = 0.7,
    timeout_ms: int = 300,
    cache_client: Optional[Any] = None,
    encryption_service: Optional[EncryptionService] = None
) -> FetchContextResult:
    """
    Convenience function to fetch context with minimal boilerplate.
    
    Args:
        memory_manager: MemoryManager instance
        db_pool: Database connection pool
        query_text: Natural language query
        org_id: Organization ID from JWT (REQUIRED, from JWT only)
        ticket_id: Optional ticket ID to scope search
        max_results: Maximum results to return
        min_confidence: Minimum confidence threshold
        timeout_ms: Timeout in milliseconds
        cache_client: Optional cache client
        encryption_service: Optional EncryptionService
        
    Returns:
        FetchContextResult with assembled context
        
    Example:
        result = await fetch_context(
            memory_manager=mm,
            db_pool=pool,
            query_text="What was the resolution?",
            org_id="org_from_jwt",
            max_results=5
        )
        print(result.items)
    """
    input_data = FetchContextInput(
        query_text=query_text,
        ticket_id=ticket_id,
        max_results=max_results,
        min_confidence=min_confidence,
        timeout_ms=timeout_ms
    )
    
    tool = FetchContextTool(memory_manager, db_pool, cache_client, encryption_service)
    return await tool.fetch(input_data, org_id)
