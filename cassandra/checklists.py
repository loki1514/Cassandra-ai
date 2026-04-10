"""
T25: Checklist Completion Events

This module provides checklist functionality with:
- Checklist table schema
- Events on completion
- Progress tracking
- Integration with tickets

Features:
- Configurable checklists per organization
- Event-driven completion tracking
- Audit trail for all changes
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger("cassandra.checklists")


class ChecklistStatus(str, Enum):
    """Checklist status values."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ChecklistItemStatus(str, Enum):
    """Individual item status values."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


@dataclass
class ChecklistItem:
    """Individual checklist item."""
    item_id: str
    title: str
    description: Optional[str] = None
    status: ChecklistItemStatus = ChecklistItemStatus.PENDING
    order: int = 0
    assigned_to: Optional[str] = None
    completed_at: Optional[datetime] = None
    completed_by: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "item_id": self.item_id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "order": self.order,
            "assigned_to": self.assigned_to,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "completed_by": self.completed_by,
            "metadata": self.metadata
        }


@dataclass
class Checklist:
    """Complete checklist with items."""
    checklist_id: str
    org_id: str
    ticket_id: Optional[str]
    title: str
    description: Optional[str]
    items: List[ChecklistItem]
    status: ChecklistStatus
    created_by: str
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    template_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def progress_percentage(self) -> float:
        """Calculate completion percentage."""
        if not self.items:
            return 100.0
        
        completed = sum(
            1 for item in self.items
            if item.status == ChecklistItemStatus.COMPLETED
        )
        return (completed / len(self.items)) * 100
    
    @property
    def is_complete(self) -> bool:
        """Check if all items are completed."""
        return all(
            item.status == ChecklistItemStatus.COMPLETED
            for item in self.items
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "checklist_id": self.checklist_id,
            "org_id": self.org_id,
            "ticket_id": self.ticket_id,
            "title": self.title,
            "description": self.description,
            "items": [item.to_dict() for item in self.items],
            "status": self.status.value,
            "progress_percentage": self.progress_percentage,
            "is_complete": self.is_complete,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "template_id": self.template_id,
            "metadata": self.metadata
        }


class ChecklistTemplate(BaseModel):
    """Template for creating checklists."""
    
    template_id: str = Field(...)
    org_id: str = Field(...)
    name: str = Field(...)
    description: Optional[str] = Field(default=None)
    items: List[Dict[str, Any]] = Field(default_factory=list)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_schema_extra = {
            "example": {
                "template_id": "template_123",
                "org_id": "org_12345",
                "name": "Incident Response",
                "description": "Standard incident response checklist",
                "items": [
                    {"title": "Acknowledge incident", "order": 1},
                    {"title": "Assess severity", "order": 2},
                    {"title": "Notify stakeholders", "order": 3}
                ]
            }
        }


class ChecklistEvent(BaseModel):
    """Event emitted on checklist changes."""
    
    event_type: str = Field(...)
    checklist_id: str = Field(...)
    org_id: str = Field(...)
    ticket_id: Optional[str] = Field(default=None)
    user_id: str = Field(...)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    data: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        json_schema_extra = {
            "example": {
                "event_type": "CHECKLIST_COMPLETED",
                "checklist_id": "chk_abc123",
                "org_id": "org_12345",
                "ticket_id": "TICKET-1234",
                "user_id": "user_xyz789",
                "timestamp": "2024-01-15T10:30:00Z"
            }
        }


class ChecklistManager:
    """
    Manages checklists and emits events on completion.
    
    Features:
    - CRUD operations for checklists
    - Progress tracking
    - Event emission on completion
    - Integration with Supermemory
    
    Usage:
        manager = ChecklistManager(db_pool)
        
        # Create checklist
        checklist = await manager.create_checklist(
            org_id="org_123",
            ticket_id="TICKET-456",
            title="Onboarding Tasks",
            items=[...]
        )
        
        # Complete item
        await manager.complete_item(
            checklist_id=checklist.checklist_id,
            item_id="item_1",
            user_id="user_abc"
        )
    """
    
    def __init__(
        self,
        db_pool: Any,
        event_webhook_url: Optional[str] = None
    ):
        """
        Initialize checklist manager.
        
        Args:
            db_pool: Database connection pool
            event_webhook_url: URL for event webhooks
        """
        self.db_pool = db_pool
        self.event_webhook_url = event_webhook_url
        
        logger.info("checklist_manager_initialized")
    
    def _generate_id(self, prefix: str = "chk") -> str:
        """Generate unique ID."""
        return f"{prefix}_{uuid.uuid4().hex[:16]}"
    
    async def create_checklist(
        self,
        org_id: str,
        title: str,
        items: List[Dict[str, Any]],
        ticket_id: Optional[str] = None,
        description: Optional[str] = None,
        created_by: str = "system",
        template_id: Optional[str] = None
    ) -> Checklist:
        """
        Create a new checklist.
        
        Args:
            org_id: Organization ID
            title: Checklist title
            items: List of item dicts with title, description, order
            ticket_id: Optional associated ticket
            description: Optional description
            created_by: User creating the checklist
            template_id: Optional template ID
            
        Returns:
            Created Checklist
        """
        checklist_id = self._generate_id("chk")
        now = datetime.utcnow()
        
        # Create items
        checklist_items = []
        for i, item_data in enumerate(items):
            item = ChecklistItem(
                item_id=self._generate_id("itm"),
                title=item_data["title"],
                description=item_data.get("description"),
                order=item_data.get("order", i),
                assigned_to=item_data.get("assigned_to")
            )
            checklist_items.append(item)
        
        checklist = Checklist(
            checklist_id=checklist_id,
            org_id=org_id,
            ticket_id=ticket_id,
            title=title,
            description=description,
            items=checklist_items,
            status=ChecklistStatus.PENDING,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            template_id=template_id
        )
        
        # Save to database
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO checklists (
                    checklist_id, org_id, ticket_id, title, description,
                    status, created_by, created_at, updated_at, template_id, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                checklist_id, org_id, ticket_id, title, description,
                checklist.status.value, created_by, now, now,
                template_id, json.dumps({})
            )
            
            # Insert items
            for item in checklist_items:
                await conn.execute(
                    """
                    INSERT INTO checklist_items (
                        item_id, checklist_id, title, description,
                        status, item_order, assigned_to, metadata
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    item.item_id, checklist_id, item.title, item.description,
                    item.status.value, item.order, item.assigned_to, json.dumps(item.metadata)
                )
        
        logger.info(
            "checklist_created",
            checklist_id=checklist_id,
            org_id=org_id,
            ticket_id=ticket_id,
            item_count=len(checklist_items)
        )
        
        # Emit event
        await self._emit_event(
            event_type="CHECKLIST_CREATED",
            checklist=checklist,
            user_id=created_by
        )
        
        return checklist
    
    async def complete_item(
        self,
        checklist_id: str,
        item_id: str,
        user_id: str,
        notes: Optional[str] = None
    ) -> Checklist:
        """
        Mark a checklist item as complete.
        
        Args:
            checklist_id: Checklist ID
            item_id: Item ID to complete
            user_id: User completing the item
            notes: Optional completion notes
            
        Returns:
            Updated Checklist
        """
        now = datetime.utcnow()
        
        async with self.db_pool.acquire() as conn:
            # Update item
            await conn.execute(
                """
                UPDATE checklist_items
                SET status = $1, completed_at = $2, completed_by = $3,
                    metadata = metadata || $4::jsonb
                WHERE item_id = $5 AND checklist_id = $6
                """,
                ChecklistItemStatus.COMPLETED.value,
                now, user_id,
                json.dumps({"completion_notes": notes}) if notes else "{}",
                item_id, checklist_id
            )
            
            # Get checklist data
            row = await conn.fetchrow(
                "SELECT org_id, ticket_id, title FROM checklists WHERE checklist_id = $1",
                checklist_id
            )
            
            if not row:
                raise ValueError(f"Checklist {checklist_id} not found")
            
            # Check if all items are complete
            items_result = await conn.fetch(
                """
                SELECT item_id, status FROM checklist_items
                WHERE checklist_id = $1
                """,
                checklist_id
            )
            
            all_complete = all(
                item["status"] == ChecklistItemStatus.COMPLETED.value
                for item in items_result
            )
            
            # Update checklist status if complete
            if all_complete:
                await conn.execute(
                    """
                    UPDATE checklists
                    SET status = $1, completed_at = $2, updated_at = $2
                    WHERE checklist_id = $3
                    """,
                    ChecklistStatus.COMPLETED.value,
                    now, checklist_id
                )
                
                logger.info(
                    "checklist_completed",
                    checklist_id=checklist_id,
                    completed_by=user_id
                )
                
                # Emit completion event
                await self._emit_event(
                    event_type="CHECKLIST_COMPLETED",
                    checklist_id=checklist_id,
                    org_id=row["org_id"],
                    ticket_id=row["ticket_id"],
                    user_id=user_id,
                    data={"completed_at": now.isoformat()}
                )
            else:
                # Update to in_progress if not already
                await conn.execute(
                    """
                    UPDATE checklists
                    SET status = $1, updated_at = $2
                    WHERE checklist_id = $3 AND status = $4
                    """,
                    ChecklistStatus.IN_PROGRESS.value,
                    now, checklist_id, ChecklistStatus.PENDING.value
                )
        
        # Emit item completion event
        await self._emit_event(
            event_type="CHECKLIST_ITEM_COMPLETED",
            checklist_id=checklist_id,
            org_id=row["org_id"],
            ticket_id=row["ticket_id"],
            user_id=user_id,
            data={
                "item_id": item_id,
                "completed_at": now.isoformat(),
                "all_complete": all_complete
            }
        )
        
        # Return updated checklist
        return await self.get_checklist(checklist_id)
    
    async def get_checklist(self, checklist_id: str) -> Optional[Checklist]:
        """
        Get checklist by ID.
        
        Args:
            checklist_id: Checklist ID
            
        Returns:
            Checklist if found, None otherwise
        """
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM checklists WHERE checklist_id = $1",
                checklist_id
            )
            
            if not row:
                return None
            
            # Get items
            items_rows = await conn.fetch(
                """
                SELECT * FROM checklist_items
                WHERE checklist_id = $1
                ORDER BY item_order
                """,
                checklist_id
            )
            
            items = []
            for item_row in items_rows:
                items.append(ChecklistItem(
                    item_id=item_row["item_id"],
                    title=item_row["title"],
                    description=item_row.get("description"),
                    status=ChecklistItemStatus(item_row["status"]),
                    order=item_row["item_order"],
                    assigned_to=item_row.get("assigned_to"),
                    completed_at=item_row.get("completed_at"),
                    completed_by=item_row.get("completed_by"),
                    metadata=json.loads(item_row.get("metadata", "{}"))
                ))
            
            return Checklist(
                checklist_id=row["checklist_id"],
                org_id=row["org_id"],
                ticket_id=row.get("ticket_id"),
                title=row["title"],
                description=row.get("description"),
                items=items,
                status=ChecklistStatus(row["status"]),
                created_by=row["created_by"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row.get("completed_at"),
                template_id=row.get("template_id"),
                metadata=json.loads(row.get("metadata", "{}"))
            )
    
    async def get_ticket_checklists(self, ticket_id: str) -> List[Checklist]:
        """
        Get all checklists for a ticket.
        
        Args:
            ticket_id: Ticket ID
            
        Returns:
            List of Checklists
        """
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT checklist_id FROM checklists WHERE ticket_id = $1",
                ticket_id
            )
            
            checklists = []
            for row in rows:
                checklist = await self.get_checklist(row["checklist_id"])
                if checklist:
                    checklists.append(checklist)
            
            return checklists
    
    async def _emit_event(
        self,
        event_type: str,
        checklist: Checklist = None,
        checklist_id: str = None,
        org_id: str = None,
        ticket_id: str = None,
        user_id: str = None,
        data: Dict[str, Any] = None
    ):
        """
        Emit checklist event.
        
        Args:
            event_type: Type of event
            checklist: Checklist object (optional)
            checklist_id: Checklist ID (if checklist not provided)
            org_id: Organization ID
            ticket_id: Ticket ID
            user_id: User ID
            data: Additional event data
        """
        if checklist:
            checklist_id = checklist.checklist_id
            org_id = checklist.org_id
            ticket_id = checklist.ticket_id
        
        event = ChecklistEvent(
            event_type=event_type,
            checklist_id=checklist_id,
            org_id=org_id,
            ticket_id=ticket_id,
            user_id=user_id or "system",
            data=data or {}
        )
        
        logger.info(
            "checklist_event_emitted",
            event_type=event_type,
            checklist_id=checklist_id
        )
        
        # TODO: Send to event bus or webhook
        # if self.event_webhook_url:
        #     await self._send_webhook(event)


# =============================================================================
# Database Schema
# =============================================================================

CHECKLIST_SCHEMA = """
-- Checklists table
CREATE TABLE IF NOT EXISTS checklists (
    checklist_id VARCHAR(32) PRIMARY KEY,
    org_id VARCHAR(32) NOT NULL,
    ticket_id VARCHAR(32),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_by VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP,
    template_id VARCHAR(32),
    metadata JSONB DEFAULT '{}',
    
    FOREIGN KEY (org_id) REFERENCES organizations(org_id),
    FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id)
);

-- Checklist items table
CREATE TABLE IF NOT EXISTS checklist_items (
    item_id VARCHAR(32) PRIMARY KEY,
    checklist_id VARCHAR(32) NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    item_order INTEGER NOT NULL DEFAULT 0,
    assigned_to VARCHAR(32),
    completed_at TIMESTAMP,
    completed_by VARCHAR(32),
    metadata JSONB DEFAULT '{}',
    
    FOREIGN KEY (checklist_id) REFERENCES checklists(checklist_id) ON DELETE CASCADE
);

-- Checklist templates table
CREATE TABLE IF NOT EXISTS checklist_templates (
    template_id VARCHAR(32) PRIMARY KEY,
    org_id VARCHAR(32) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    items JSONB NOT NULL DEFAULT '[]',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    
    FOREIGN KEY (org_id) REFERENCES organizations(org_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_checklists_org ON checklists(org_id);
CREATE INDEX IF NOT EXISTS idx_checklists_ticket ON checklists(ticket_id);
CREATE INDEX IF NOT EXISTS idx_checklists_status ON checklists(status);
CREATE INDEX IF NOT EXISTS idx_checklist_items_checklist ON checklist_items(checklist_id);
"""


# =============================================================================
# FastAPI Endpoints
# =============================================================================

from fastapi import APIRouter, HTTPException, Depends
from cassandra.auth import get_current_user, UserContext

router = APIRouter(prefix="/checklists", tags=["Checklists"])

# Global manager instance
_checklist_manager: Optional[ChecklistManager] = None


def get_checklist_manager() -> ChecklistManager:
    """Get or create checklist manager instance."""
    global _checklist_manager
    if _checklist_manager is None:
        raise RuntimeError("Checklist manager not initialized")
    return _checklist_manager


@router.post("/")
async def create_checklist(
    org_id: str,
    title: str,
    items: List[Dict[str, Any]],
    ticket_id: Optional[str] = None,
    description: Optional[str] = None,
    user: UserContext = Depends(get_current_user)
):
    """Create a new checklist."""
    try:
        manager = get_checklist_manager()
        checklist = await manager.create_checklist(
            org_id=org_id,
            title=title,
            items=items,
            ticket_id=ticket_id,
            description=description,
            created_by=user.user_id
        )
        return {
            "success": True,
            "checklist": checklist.to_dict()
        }
    except Exception as e:
        logger.error("create_checklist_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{checklist_id}/items/{item_id}/complete")
async def complete_checklist_item(
    checklist_id: str,
    item_id: str,
    notes: Optional[str] = None,
    user: UserContext = Depends(get_current_user)
):
    """Mark a checklist item as complete."""
    try:
        manager = get_checklist_manager()
        checklist = await manager.complete_item(
            checklist_id=checklist_id,
            item_id=item_id,
            user_id=user.user_id,
            notes=notes
        )
        return {
            "success": True,
            "checklist": checklist.to_dict()
        }
    except Exception as e:
        logger.error("complete_item_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{checklist_id}")
async def get_checklist(
    checklist_id: str,
    user: UserContext = Depends(get_current_user)
):
    """Get checklist by ID."""
    try:
        manager = get_checklist_manager()
        checklist = await manager.get_checklist(checklist_id)
        
        if not checklist:
            raise HTTPException(status_code=404, detail="Checklist not found")
        
        return {
            "success": True,
            "checklist": checklist.to_dict()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_checklist_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
