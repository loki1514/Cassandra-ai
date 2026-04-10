"""
T13: Secure Tool Registry — create_ticket

This module implements the create_ticket tool for the Cassandra AI system.
It provides a secure, INSERT-only interface for creating new tickets with:
- org_id extracted from JWT (never from request body)
- Input validation via Pydantic
- Audit logging for all ticket creations
- Idempotency support via idempotency keys

Security Features:
- Organization isolation enforced at the tool level
- No UPDATE or DELETE operations (INSERT only)
- All operations logged for audit trail
- Rate limiting support via idempotency keys
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, List
from enum import Enum

from pydantic import BaseModel, Field, validator, field_validator

# Configure logging
logger = logging.getLogger(__name__)


class TicketPriority(str, Enum):
    """Ticket priority levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"
    CRITICAL = "critical"


class TicketStatus(str, Enum):
    """Ticket status values."""
    ACTIVE = "active"
    PENDING = "pending"
    ON_HOLD = "on_hold"


class CreateTicketInput(BaseModel):
    """
    Input model for create_ticket tool.
    
    Validates and sanitizes all input parameters for ticket creation.
    Note: org_id is NOT included here - it must come from JWT context.
    
    Attributes:
        title: Ticket title (required, 1-500 chars)
        description: Ticket description (optional, max 10000 chars)
        priority: Ticket priority level (default: medium)
        requester_email: Email of the requester
        tags: List of tags for categorization
        custom_fields: Organization-specific custom fields
        idempotency_key: Optional key for deduplication
    """
    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Ticket title (required)"
    )
    description: Optional[str] = Field(
        default=None,
        max_length=10000,
        description="Ticket description"
    )
    priority: TicketPriority = Field(
        default=TicketPriority.MEDIUM,
        description="Ticket priority level"
    )
    requester_email: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Requester email address"
    )
    assignee_id: Optional[str] = Field(
        default=None,
        description="ID of assigned agent"
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Tags for categorization"
    )
    custom_fields: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Organization-specific custom fields"
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Idempotency key for deduplication"
    )
    source: Optional[str] = Field(
        default="api",
        description="Source of the ticket (api, email, web, etc.)"
    )
    
    @field_validator('title')
    @classmethod
    def validate_title(cls, v: str) -> str:
        """Sanitize and validate title."""
        v = v.strip()
        if not v:
            raise ValueError("Title cannot be empty or whitespace only")
        # Remove potentially dangerous characters
        v = v.replace('\x00', '')  # Null bytes
        return v
    
    @field_validator('description')
    @classmethod
    def validate_description(cls, v: Optional[str]) -> Optional[str]:
        """Sanitize description if provided."""
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        # Remove null bytes
        v = v.replace('\x00', '')
        return v
    
    @field_validator('requester_email')
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        """Basic email validation."""
        if v is None:
            return None
        v = v.strip().lower()
        if '@' not in v or '.' not in v.split('@')[-1]:
            raise ValueError("Invalid email format")
        return v
    
    @field_validator('tags')
    @classmethod
    def validate_tags(cls, v: List[str]) -> List[str]:
        """Sanitize and deduplicate tags."""
        # Clean and limit tags
        cleaned = []
        seen = set()
        for tag in v[:20]:  # Max 20 tags
            tag = tag.strip().lower()[:50]  # Max 50 chars per tag
            if tag and tag not in seen:
                cleaned.append(tag)
                seen.add(tag)
        return cleaned
    
    class Config:
        json_schema_extra = {
            "example": {
                "title": "Login issue with SSO",
                "description": "Users unable to login via SSO after update",
                "priority": "high",
                "requester_email": "user@example.com",
                "tags": ["sso", "login", "urgent"],
                "source": "api"
            }
        }


class CreateTicketResult(BaseModel):
    """
    Result model for create_ticket tool.
    
    Returns the created ticket details with all generated fields.
    
    Attributes:
        ticket_id: Unique ticket identifier
        created_at: ISO timestamp of creation
        status: Ticket status (always 'active' for new tickets)
        org_id: Organization ID (echoed from JWT)
        title: Ticket title (echoed)
        priority: Ticket priority (echoed)
    """
    ticket_id: str = Field(..., description="Unique ticket identifier")
    created_at: str = Field(..., description="ISO 8601 timestamp of creation")
    status: TicketStatus = Field(..., description="Ticket status")
    org_id: str = Field(..., description="Organization ID from JWT")
    title: str = Field(..., description="Ticket title")
    priority: TicketPriority = Field(..., description="Ticket priority")
    ticket_number: Optional[str] = Field(
        default=None,
        description="Human-readable ticket number (e.g., TICKET-1234)"
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Echoed idempotency key if provided"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "ticket_id": "550e8400-e29b-41d4-a716-446655440000",
                "created_at": "2024-01-15T10:30:00Z",
                "status": "active",
                "org_id": "org_12345",
                "title": "Login issue with SSO",
                "priority": "high",
                "ticket_number": "TICKET-1234"
            }
        }


class CreateTicketTool:
    """
    Secure tool for creating tickets.
    
    This tool implements INSERT-only operations with strict security:
    - org_id is extracted from JWT context, never from request body
    - All inputs are validated and sanitized
    - Operations are logged for audit
    - Idempotency keys prevent duplicate creation
    
    Usage:
        tool = CreateTicketTool(db_pool)
        
        result = await tool.create(
            input_data=CreateTicketInput(title="..."),
            org_id="org_from_jwt",  # From JWT, never from user input
            user_id="user_from_jwt"  # From JWT for audit
        )
    """
    
    def __init__(self, db_pool: Any):
        """
        Initialize the create_ticket tool.
        
        Args:
            db_pool: Database connection pool for ticket table
        """
        self.db_pool = db_pool
        logger.info("CreateTicketTool initialized")
    
    def _generate_ticket_id(self) -> str:
        """Generate a unique ticket ID."""
        return str(uuid.uuid4())
    
    def _generate_ticket_number(self, org_id: str) -> str:
        """Generate a human-readable ticket number."""
        # Use timestamp and hash for uniqueness
        timestamp = datetime.utcnow().strftime("%Y%m%d")
        hash_input = f"{org_id}:{timestamp}:{uuid.uuid4().hex[:8]}"
        hash_suffix = hashlib.sha256(hash_input.encode()).hexdigest()[:6].upper()
        return f"TICKET-{timestamp}-{hash_suffix}"
    
    async def _check_idempotency(
        self,
        idempotency_key: str,
        org_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Check if a ticket was already created with this idempotency key.
        
        Args:
            idempotency_key: The idempotency key to check
            org_id: Organization ID for scoping
            
        Returns:
            Existing ticket data if found, None otherwise
        """
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT ticket_id, created_at, status, title, priority, ticket_number
                    FROM tickets
                    WHERE idempotency_key = $1 AND org_id = $2
                    """,
                    idempotency_key,
                    org_id
                )
                
                if row:
                    logger.info(f"Idempotency hit: ticket already exists for key {idempotency_key}")
                    return dict(row)
                
                return None
        except Exception as e:
            logger.error(f"Idempotency check failed: {e}")
            # SECURITY: Fail closed - raise exception on DB errors
            # Prevents duplicate ticket creation when idempotency check fails
            raise RuntimeError(f"Idempotency check failed, cannot proceed: {e}") from e
    
    async def create(
        self,
        input_data: CreateTicketInput,
        org_id: str,
        user_id: Optional[str] = None
    ) -> CreateTicketResult:
        """
        Create a new ticket.
        
        This is an INSERT-only operation. No updates or deletes are performed.
        
        Args:
            input_data: Validated CreateTicketInput
            org_id: Organization ID from JWT (MUST NOT come from request body)
            user_id: User ID from JWT for audit trail (optional)
            
        Returns:
            CreateTicketResult with created ticket details
            
        Raises:
            ValueError: If org_id is missing or invalid
            TicketCreationError: If database operation fails
        """
        # SECURITY: Verify org_id is provided (from JWT)
        if not org_id:
            raise ValueError("org_id is required and must come from JWT context")
        
        # Check idempotency if key provided
        if input_data.idempotency_key:
            existing = await self._check_idempotency(
                input_data.idempotency_key,
                org_id
            )
            if existing:
                logger.info(f"Returning existing ticket {existing['ticket_id']} due to idempotency")
                return CreateTicketResult(
                    ticket_id=existing['ticket_id'],
                    created_at=existing['created_at'].isoformat() if isinstance(existing['created_at'], datetime) else existing['created_at'],
                    status=TicketStatus(existing['status']),
                    org_id=org_id,
                    title=existing['title'],
                    priority=TicketPriority(existing['priority']),
                    ticket_number=existing.get('ticket_number'),
                    idempotency_key=input_data.idempotency_key
                )
        
        # Generate ticket identifiers
        ticket_id = self._generate_ticket_id()
        ticket_number = self._generate_ticket_number(org_id)
        created_at = datetime.utcnow()
        
        logger.info(f"Creating ticket {ticket_id} for org {org_id}")
        
        try:
            async with self.db_pool.acquire() as conn:
                # INSERT ONLY - no UPDATE or DELETE
                await conn.execute(
                    """
                    INSERT INTO tickets (
                        ticket_id,
                        ticket_number,
                        org_id,
                        title,
                        description,
                        priority,
                        status,
                        requester_email,
                        assignee_id,
                        tags,
                        custom_fields,
                        source,
                        created_by,
                        created_at,
                        updated_at,
                        idempotency_key
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                    """,
                    ticket_id,
                    ticket_number,
                    org_id,
                    input_data.title,
                    input_data.description,
                    input_data.priority.value,
                    TicketStatus.ACTIVE.value,
                    input_data.requester_email,
                    input_data.assignee_id,
                    input_data.tags,
                    json.dumps(input_data.custom_fields) if input_data.custom_fields else None,
                    input_data.source,
                    user_id,
                    created_at,
                    created_at,
                    input_data.idempotency_key
                )
            
            # Log audit event
            logger.info(
                f"Ticket created: {ticket_id} | "
                f"org: {org_id} | "
                f"by: {user_id or 'system'} | "
                f"title: {input_data.title[:50]}..."
            )
            
            return CreateTicketResult(
                ticket_id=ticket_id,
                created_at=created_at.isoformat(),
                status=TicketStatus.ACTIVE,
                org_id=org_id,
                title=input_data.title,
                priority=input_data.priority,
                ticket_number=ticket_number,
                idempotency_key=input_data.idempotency_key
            )
            
        except Exception as e:
            logger.error(f"Ticket creation failed: {e}")
            raise TicketCreationError(f"Failed to create ticket: {e}")
    
    async def create_batch(
        self,
        inputs: List[CreateTicketInput],
        org_id: str,
        user_id: Optional[str] = None
    ) -> List[CreateTicketResult]:
        """
        Create multiple tickets in a batch.
        
        Each ticket is created independently with its own idempotency check.
        
        Args:
            inputs: List of CreateTicketInput
            org_id: Organization ID from JWT
            user_id: User ID from JWT for audit
            
        Returns:
            List of CreateTicketResult (one per input)
        """
        results = []
        for input_data in inputs:
            try:
                result = await self.create(input_data, org_id, user_id)
                results.append(result)
            except Exception as e:
                logger.error(f"Batch ticket creation failed for item: {e}")
                # Continue with remaining items
                raise
        return results


class TicketCreationError(Exception):
    """Raised when ticket creation fails."""
    pass


class TicketValidationError(Exception):
    """Raised when ticket input validation fails."""
    pass


# Convenience function for direct usage
async def create_ticket(
    db_pool: Any,
    title: str,
    org_id: str,
    description: Optional[str] = None,
    priority: TicketPriority = TicketPriority.MEDIUM,
    requester_email: Optional[str] = None,
    assignee_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    custom_fields: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    source: str = "api",
    user_id: Optional[str] = None
) -> CreateTicketResult:
    """
    Convenience function to create a ticket with minimal boilerplate.
    
    Args:
        db_pool: Database connection pool
        title: Ticket title
        org_id: Organization ID from JWT (REQUIRED, from JWT only)
        description: Optional description
        priority: Ticket priority
        requester_email: Requester email
        assignee_id: Assigned agent ID
        tags: List of tags
        custom_fields: Custom fields dict
        idempotency_key: Optional idempotency key
        source: Ticket source
        user_id: User ID from JWT for audit
        
    Returns:
        CreateTicketResult with created ticket details
        
    Example:
        result = await create_ticket(
            db_pool=pool,
            title="Login issue",
            org_id="org_from_jwt",  # Must come from JWT
            priority=TicketPriority.HIGH
        )
        print(result.ticket_id)
    """
    input_data = CreateTicketInput(
        title=title,
        description=description,
        priority=priority,
        requester_email=requester_email,
        assignee_id=assignee_id,
        tags=tags or [],
        custom_fields=custom_fields,
        idempotency_key=idempotency_key,
        source=source
    )
    
    tool = CreateTicketTool(db_pool)
    return await tool.create(input_data, org_id, user_id)
