"""
Cassandra AI Tools Module

This module provides the secure tool registry for the Cassandra AI system.
All tools implement:
- Input validation via Pydantic models
- org_id from JWT context (never from request body)
- Audit logging
- Idempotency support
- Organization-scoped data isolation

Available Tools:
- create_ticket: INSERT-only ticket creation
- add_memory: Atomic memory write with map table
- fetch_context: SELECT-only context retrieval

Usage:
    from cassandra.tools import get_tool_registry
    
    registry = get_tool_registry()
    
    # Execute a tool
    result = await registry.execute(
        tool_name="create_ticket",
        input_data={"title": "..."},
        org_id="org_from_jwt",
        user_id="user_from_jwt"
    )
"""

from typing import Optional, Dict, Any, List
import logging

# Import tool classes
from .create_ticket import (
    CreateTicketTool,
    CreateTicketInput,
    CreateTicketResult,
    create_ticket,
    TicketPriority,
    TicketStatus
)
from .add_memory import (
    AddMemoryTool,
    AddMemoryInput,
    AddMemoryResult,
    add_memory,
    MemoryWriteError,
    MemoryEncryptionError
)
from .fetch_context import (
    FetchContextTool,
    FetchContextInput,
    FetchContextResult,
    fetch_context,
    ContextResultItem,
    ContextFetchError
)
from .registry import ToolRegistry, ToolMetadata, ToolExecutionError

# Configure logging
logger = logging.getLogger(__name__)

# Module version
__version__ = "0.1.0"

# Tool registry singleton
_tool_registry: Optional[ToolRegistry] = None


def get_tool_registry(
    db_pool: Optional[Any] = None,
    memory_manager: Optional[Any] = None,
    cache_client: Optional[Any] = None,
    encryption_service: Optional[Any] = None
) -> ToolRegistry:
    """
    Get or create the singleton tool registry.
    
    Args:
        db_pool: Database connection pool
        memory_manager: MemoryManager instance
        cache_client: Optional cache client
        encryption_service: Optional EncryptionService
        
    Returns:
        ToolRegistry instance
    """
    global _tool_registry
    
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
        
        # Register tools if dependencies provided
        if db_pool:
            # Register create_ticket tool
            create_ticket_tool = CreateTicketTool(db_pool)
            _tool_registry.register(
                name="create_ticket",
                tool_instance=create_ticket_tool,
                metadata=ToolMetadata(
                    description="Create a new ticket (INSERT only)",
                    input_schema="CreateTicketInput",
                    output_schema="CreateTicketResult",
                    requires_org_id=True,
                    requires_auth=True,
                    operation_type="insert"
                )
            )
            
            # Register add_memory tool if memory_manager available
            if memory_manager:
                add_memory_tool = AddMemoryTool(
                    memory_manager=memory_manager,
                    db_pool=db_pool,
                    encryption_service=encryption_service
                )
                _tool_registry.register(
                    name="add_memory",
                    tool_instance=add_memory_tool,
                    metadata=ToolMetadata(
                        description="Add a memory with atomic map write",
                        input_schema="AddMemoryInput",
                        output_schema="AddMemoryResult",
                        requires_org_id=True,
                        requires_auth=True,
                        operation_type="insert"
                    )
                )
                
                # Register fetch_context tool
                fetch_context_tool = FetchContextTool(
                    memory_manager=memory_manager,
                    db_pool=db_pool,
                    cache_client=cache_client,
                    encryption_service=encryption_service
                )
                _tool_registry.register(
                    name="fetch_context",
                    tool_instance=fetch_context_tool,
                    metadata=ToolMetadata(
                        description="Fetch context (SELECT only)",
                        input_schema="FetchContextInput",
                        output_schema="FetchContextResult",
                        requires_org_id=True,
                        requires_auth=True,
                        operation_type="select"
                    )
                )
        
        logger.info("Tool registry initialized")
    
    return _tool_registry


def reset_tool_registry() -> None:
    """Reset the tool registry (useful for testing)."""
    global _tool_registry
    _tool_registry = None
    logger.debug("Tool registry reset")


# Export all public classes and functions
__all__ = [
    # Tool Registry
    "get_tool_registry",
    "reset_tool_registry",
    "ToolRegistry",
    "ToolMetadata",
    "ToolExecutionError",
    
    # Create Ticket
    "CreateTicketTool",
    "CreateTicketInput",
    "CreateTicketResult",
    "create_ticket",
    "TicketPriority",
    "TicketStatus",
    
    # Add Memory
    "AddMemoryTool",
    "AddMemoryInput",
    "AddMemoryResult",
    "add_memory",
    "MemoryWriteError",
    "MemoryEncryptionError",
    
    # Fetch Context
    "FetchContextTool",
    "FetchContextInput",
    "FetchContextResult",
    "fetch_context",
    "ContextResultItem",
    "ContextFetchError",
]
