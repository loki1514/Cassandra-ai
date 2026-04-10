"""
T20: Backend → Supermemory Ground Truth Writer

This module provides webhook handlers for ticket status changes and
writes ground truth data to Supermemory with:
- source='backend' for verified data
- confidence=1.0 for authoritative sources
- Audit trail for all writes

Features:
- Webhook endpoint for ticket status changes
- Automatic Supermemory ground truth writes
- Idempotency to prevent duplicate writes
- Structured logging (no PII)
"""

import hashlib
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

import httpx
import structlog
from pydantic import BaseModel, Field

from cassandra.config import settings

logger = structlog.get_logger("cassandra.ground_truth")


class TicketStatus(str, Enum):
    """Ticket status values."""
    ACTIVE = "active"
    PENDING = "pending"
    ON_HOLD = "on_hold"
    RESOLVED = "resolved"
    CLOSED = "closed"


class GroundTruthSource(str, Enum):
    """Sources of ground truth data."""
    BACKEND = "backend"
    USER = "user"
    SYSTEM = "system"
    AI = "ai"


@dataclass
class GroundTruthEntry:
    """
    Ground truth memory entry.
    
    Attributes:
        content: The factual statement
        source: Source of the truth (backend, user, etc.)
        confidence: Confidence score (0.0 - 1.0)
        ticket_id: Associated ticket ID
        org_id: Organization ID
        metadata: Additional context
    """
    content: str
    source: GroundTruthSource
    confidence: float
    ticket_id: str
    org_id: str
    metadata: Dict[str, Any]
    created_at: datetime = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()
    
    def to_supermemory_payload(self) -> Dict[str, Any]:
        """Convert to Supermemory API payload."""
        return {
            "id": self._generate_id(),
            "content": self.content,
            "metadata": {
                "org_id": self.org_id,
                "ticket_id": self.ticket_id,
                "source": self.source.value,
                "confidence": self.confidence,
                "is_ground_truth": True,
                "created_at": self.created_at.isoformat(),
                **self.metadata
            }
        }
    
    def _generate_id(self) -> str:
        """Generate deterministic ID."""
        hash_input = f"{self.org_id}:{self.ticket_id}:{self.content[:100]}"
        return f"gt_{hashlib.sha256(hash_input.encode()).hexdigest()[:24]}"


class TicketStatusChange(BaseModel):
    """Model for ticket status change webhook payload."""
    
    ticket_id: str = Field(..., description="Ticket ID")
    org_id: str = Field(..., description="Organization ID")
    old_status: TicketStatus = Field(..., description="Previous status")
    new_status: TicketStatus = Field(..., description="New status")
    changed_by: str = Field(..., description="User ID who made the change")
    changed_at: datetime = Field(default_factory=datetime.utcnow)
    reason: Optional[str] = Field(default=None, description="Reason for change")
    resolution_notes: Optional[str] = Field(default=None)
    
    class Config:
        json_schema_extra = {
            "example": {
                "ticket_id": "TICKET-1234",
                "org_id": "org_12345",
                "old_status": "active",
                "new_status": "resolved",
                "changed_by": "user_abc123",
                "reason": "Issue fixed in deployment v2.1.0"
            }
        }


class GroundTruthWriter:
    """
    Writes ground truth data to Supermemory.
    
    This class handles:
    - Converting ticket events to ground truth entries
    - Writing to Supermemory with proper metadata
    - Maintaining idempotency
    - Audit logging
    
    Usage:
        writer = GroundTruthWriter(supermemory_config)
        
        # Handle ticket status change
        await writer.handle_status_change(
            ticket_id="TICKET-123",
            org_id="org_123",
            old_status="active",
            new_status="resolved",
            resolution_notes="Fixed in v2.0"
        )
    """
    
    def __init__(
        self,
        supermemory_api_key: Optional[str] = None,
        supermemory_base_url: str = "https://api.supermemory.ai/v1"
    ):
        """
        Initialize ground truth writer.
        
        Args:
            supermemory_api_key: API key for Supermemory
            supermemory_base_url: Supermemory API base URL
        """
        self.api_key = supermemory_api_key or settings.vector.api_key
        self.base_url = supermemory_base_url
        self._http_client: Optional[httpx.AsyncClient] = None
        
        logger.info("ground_truth_writer_initialized")
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30,
                headers={"Authorization": f"Bearer {self.api_key}"}
            )
        return self._http_client
    
    async def write_ground_truth(
        self,
        entry: GroundTruthEntry
    ) -> Dict[str, Any]:
        """
        Write ground truth entry to Supermemory.
        
        Args:
            entry: GroundTruthEntry to write
            
        Returns:
            API response data
            
        Raises:
            GroundTruthWriteError: If write fails
        """
        client = await self._get_http_client()
        payload = entry.to_supermemory_payload()
        
        logger.info(
            "writing_ground_truth",
            ticket_id=entry.ticket_id,
            org_id=entry.org_id,
            source=entry.source.value,
            confidence=entry.confidence
        )
        
        try:
            response = await client.post("/memories", json=payload)
            response.raise_for_status()
            
            result = response.json()
            
            logger.info(
                "ground_truth_written",
                memory_id=result.get("id"),
                ticket_id=entry.ticket_id
            )
            
            return {
                "success": True,
                "memory_id": result.get("id"),
                "ticket_id": entry.ticket_id,
                "source": entry.source.value,
                "confidence": entry.confidence
            }
            
        except httpx.HTTPError as e:
            logger.error(
                "ground_truth_write_failed",
                ticket_id=entry.ticket_id,
                error=str(e)
            )
            raise GroundTruthWriteError(f"Failed to write ground truth: {e}")
    
    async def handle_status_change(
        self,
        ticket_id: str,
        org_id: str,
        old_status: TicketStatus,
        new_status: TicketStatus,
        changed_by: str,
        reason: Optional[str] = None,
        resolution_notes: Optional[str] = None,
        ticket_title: Optional[str] = None
    ) -> Optional[GroundTruthEntry]:
        """
        Handle ticket status change and create ground truth entry.
        
        Only certain status transitions generate ground truth:
        - resolved: Issue resolution information
        - closed: Final outcome information
        
        Args:
            ticket_id: Ticket ID
            org_id: Organization ID
            old_status: Previous status
            new_status: New status
            changed_by: User who made the change
            reason: Reason for change
            resolution_notes: Resolution details
            ticket_title: Ticket title for context
            
        Returns:
            GroundTruthEntry if one was created, None otherwise
        """
        # Only create ground truth for resolution/closing
        if new_status not in [TicketStatus.RESOLVED, TicketStatus.CLOSED]:
            logger.debug(
                "status_change_no_ground_truth",
                ticket_id=ticket_id,
                new_status=new_status.value
            )
            return None
        
        # Build ground truth content
        content_parts = []
        
        if ticket_title:
            content_parts.append(f"Ticket '{ticket_title}' was {new_status.value}")
        else:
            content_parts.append(f"Ticket {ticket_id} was {new_status.value}")
        
        if reason:
            content_parts.append(f"Reason: {reason}")
        
        if resolution_notes:
            content_parts.append(f"Resolution: {resolution_notes}")
        
        content = " | ".join(content_parts)
        
        # Create ground truth entry
        entry = GroundTruthEntry(
            content=content,
            source=GroundTruthSource.BACKEND,
            confidence=1.0,  # Backend is authoritative
            ticket_id=ticket_id,
            org_id=org_id,
            metadata={
                "old_status": old_status.value,
                "new_status": new_status.value,
                "changed_by": changed_by,
                "event_type": "ticket_status_change"
            }
        )
        
        # Write to Supermemory
        await self.write_ground_truth(entry)
        
        return entry
    
    async def handle_webhook(
        self,
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle incoming webhook for ticket status change.
        
        Args:
            payload: Webhook payload
            
        Returns:
            Response data
        """
        try:
            # Validate payload
            change = TicketStatusChange(**payload)
            
            logger.info(
                "webhook_received",
                ticket_id=change.ticket_id,
                old_status=change.old_status.value,
                new_status=change.new_status.value
            )
            
            # Process status change
            entry = await self.handle_status_change(
                ticket_id=change.ticket_id,
                org_id=change.org_id,
                old_status=change.old_status,
                new_status=change.new_status,
                changed_by=change.changed_by,
                reason=change.reason,
                resolution_notes=change.resolution_notes
            )
            
            if entry:
                return {
                    "success": True,
                    "ground_truth_created": True,
                    "ticket_id": change.ticket_id,
                    "message": "Ground truth entry created"
                }
            else:
                return {
                    "success": True,
                    "ground_truth_created": False,
                    "ticket_id": change.ticket_id,
                    "message": "No ground truth created for this status change"
                }
                
        except Exception as e:
            logger.error("webhook_processing_error", error=str(e))
            return {
                "success": False,
                "error": str(e)
            }
    
    async def batch_write_resolutions(
        self,
        resolutions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Batch write resolution ground truths.
        
        Args:
            resolutions: List of resolution data dicts
            
        Returns:
            List of write results
        """
        results = []
        
        for resolution in resolutions:
            try:
                entry = GroundTruthEntry(
                    content=resolution["content"],
                    source=GroundTruthSource.BACKEND,
                    confidence=1.0,
                    ticket_id=resolution["ticket_id"],
                    org_id=resolution["org_id"],
                    metadata=resolution.get("metadata", {})
                )
                
                result = await self.write_ground_truth(entry)
                results.append(result)
                
            except Exception as e:
                logger.error(
                    "batch_write_failed",
                    ticket_id=resolution.get("ticket_id"),
                    error=str(e)
                )
                results.append({
                    "success": False,
                    "ticket_id": resolution.get("ticket_id"),
                    "error": str(e)
                })
        
        return results
    
    async def close(self):
        """Close HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()


class GroundTruthWriteError(Exception):
    """Raised when ground truth write fails."""
    pass


# =============================================================================
# FastAPI Webhook Endpoint
# =============================================================================

from fastapi import APIRouter, Request, HTTPException, Depends, BackgroundTasks
from cassandra.auth import get_current_user, UserContext

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# Global writer instance
_ground_truth_writer: Optional[GroundTruthWriter] = None


def get_ground_truth_writer() -> GroundTruthWriter:
    """Get or create ground truth writer instance."""
    global _ground_truth_writer
    if _ground_truth_writer is None:
        _ground_truth_writer = GroundTruthWriter()
    return _ground_truth_writer


@router.post("/ticket-status")
async def ticket_status_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    writer: GroundTruthWriter = Depends(get_ground_truth_writer)
):
    """
    Webhook endpoint for ticket status changes.
    
    This endpoint receives notifications when ticket status changes
    and creates ground truth entries in Supermemory.
    
    Expected payload:
    {
        "ticket_id": "TICKET-1234",
        "org_id": "org_12345",
        "old_status": "active",
        "new_status": "resolved",
        "changed_by": "user_abc123",
        "reason": "Optional reason",
        "resolution_notes": "Optional resolution details"
    }
    """
    try:
        payload = await request.json()
        result = await writer.handle_webhook(payload)
        
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        
        return result
        
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except Exception as e:
        logger.error("webhook_endpoint_error", error=str(e))
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/batch-resolutions")
async def batch_resolutions_webhook(
    request: Request,
    writer: GroundTruthWriter = Depends(get_ground_truth_writer)
):
    """
    Batch webhook endpoint for multiple resolutions.
    
    Expected payload:
    {
        "resolutions": [
            {
                "ticket_id": "TICKET-1234",
                "org_id": "org_12345",
                "content": "Resolution description"
            }
        ]
    }
    """
    try:
        payload = await request.json()
        resolutions = payload.get("resolutions", [])
        
        if not resolutions:
            raise HTTPException(status_code=400, detail="No resolutions provided")
        
        results = await writer.batch_write_resolutions(resolutions)
        
        return {
            "success": True,
            "processed": len(results),
            "results": results
        }
        
    except Exception as e:
        logger.error("batch_webhook_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
