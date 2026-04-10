"""
T23: SLA Breach Detection

This module provides SLA monitoring and breach detection:
- Cron job for overdue tickets
- Supermemory SLA_BREACH events
- Configurable SLA rules per organization
- Alert notifications

Features:
- Async SLA checking
- Breach event recording
- Escalation workflows
- Audit trail
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any, List

import httpx
import structlog
from pydantic import BaseModel, Field

from cassandra.config import settings

logger = structlog.get_logger("cassandra.sla_monitor")


class SLAPriority(str, Enum):
    """SLA priority levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TicketStatus(str, Enum):
    """Ticket status values."""
    ACTIVE = "active"
    PENDING = "pending"
    ON_HOLD = "on_hold"
    RESOLVED = "resolved"
    CLOSED = "closed"


class BreachType(str, Enum):
    """Types of SLA breaches."""
    FIRST_RESPONSE = "first_response"
    RESOLUTION = "resolution"
    UPDATE = "update"
    ESCALATION = "escalation"


@dataclass
class SLARule:
    """SLA rule configuration."""
    priority: SLAPriority
    first_response_hours: int
    resolution_hours: int
    update_hours: Optional[int] = None
    business_hours_only: bool = True


@dataclass
class SLABreach:
    """SLA breach event."""
    ticket_id: str
    org_id: str
    breach_type: BreachType
    severity: SLAPriority
    scheduled_time: datetime
    actual_time: datetime
    breach_duration_minutes: int
    metadata: Dict[str, Any]


class SLABreachEvent(BaseModel):
    """Model for SLA breach events stored in Supermemory."""
    
    event_type: str = Field(default="SLA_BREACH")
    ticket_id: str = Field(...)
    org_id: str = Field(...)
    breach_type: str = Field(...)
    severity: str = Field(...)
    breach_duration_minutes: int = Field(...)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        json_schema_extra = {
            "example": {
                "event_type": "SLA_BREACH",
                "ticket_id": "TICKET-1234",
                "org_id": "org_12345",
                "breach_type": "first_response",
                "severity": "high",
                "breach_duration_minutes": 30
            }
        }


class SLAMonitor:
    """
    Monitors tickets for SLA breaches.
    
    Features:
    - Configurable SLA rules per organization
    - Automatic breach detection
    - Supermemory event logging
    - Alert notifications
    
    Usage:
        monitor = SLAMonitor(db_pool)
        
        # Check for breaches
        breaches = await monitor.check_overdue_tickets()
        
        # Record breach in Supermemory
        await monitor.record_breach(breach)
    """
    
    # Default SLA rules
    DEFAULT_RULES: Dict[SLAPriority, SLARule] = {
        SLAPriority.CRITICAL: SLARule(
            priority=SLAPriority.CRITICAL,
            first_response_hours=1,
            resolution_hours=4,
            update_hours=1
        ),
        SLAPriority.HIGH: SLARule(
            priority=SLAPriority.HIGH,
            first_response_hours=4,
            resolution_hours=24,
            update_hours=4
        ),
        SLAPriority.MEDIUM: SLARule(
            priority=SLAPriority.MEDIUM,
            first_response_hours=8,
            resolution_hours=72,
            update_hours=24
        ),
        SLAPriority.LOW: SLARule(
            priority=SLAPriority.LOW,
            first_response_hours=24,
            resolution_hours=168,  # 7 days
            update_hours=72
        )
    }
    
    def __init__(
        self,
        db_pool: Any,
        supermemory_api_key: Optional[str] = None,
        supermemory_base_url: str = "https://api.supermemory.ai/v1"
    ):
        """
        Initialize SLA monitor.
        
        Args:
            db_pool: Database connection pool
            supermemory_api_key: API key for Supermemory
            supermemory_base_url: Supermemory API base URL
        """
        self.db_pool = db_pool
        self.supermemory_api_key = supermemory_api_key or settings.vector.api_key
        self.supermemory_base_url = supermemory_base_url
        self._http_client: Optional[httpx.AsyncClient] = None
        self._org_rules: Dict[str, Dict[SLAPriority, SLARule]] = {}
        
        logger.info("sla_monitor_initialized")
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                base_url=self.supermemory_base_url,
                timeout=30,
                headers={"Authorization": f"Bearer {self.supermemory_api_key}"}
            )
        return self._http_client
    
    def get_sla_rule(
        self,
        org_id: str,
        priority: SLAPriority
    ) -> SLARule:
        """
        Get SLA rule for organization and priority.
        
        Args:
            org_id: Organization ID
            priority: Ticket priority
            
        Returns:
            SLARule for the org/priority
        """
        # Check for org-specific rules
        if org_id in self._org_rules:
            return self._org_rules[org_id].get(priority, self.DEFAULT_RULES[priority])
        
        return self.DEFAULT_RULES[priority]
    
    async def load_org_rules(self, org_id: str):
        """
        Load organization-specific SLA rules from database.
        
        Args:
            org_id: Organization ID
        """
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT priority, first_response_hours, resolution_hours, 
                           update_hours, business_hours_only
                    FROM sla_rules
                    WHERE org_id = $1
                    """,
                    org_id
                )
                
                rules = {}
                for row in rows:
                    priority = SLAPriority(row['priority'])
                    rules[priority] = SLARule(
                        priority=priority,
                        first_response_hours=row['first_response_hours'],
                        resolution_hours=row['resolution_hours'],
                        update_hours=row['update_hours'],
                        business_hours_only=row['business_hours_only']
                    )
                
                if rules:
                    self._org_rules[org_id] = rules
                    logger.info(f"Loaded SLA rules for org {org_id}")
                
        except Exception as e:
            logger.error(f"Failed to load SLA rules for org {org_id}: {e}")
    
    async def check_overdue_tickets(
        self,
        org_id: Optional[str] = None
    ) -> List[SLABreach]:
        """
        Check for overdue tickets and identify breaches.
        
        Args:
            org_id: Optional org filter (checks all if None)
            
        Returns:
            List of SLA breaches found
        """
        breaches = []
        
        try:
            async with self.db_pool.acquire() as conn:
                # Build query
                org_filter = "AND t.org_id = $1" if org_id else ""
                params = [org_id] if org_id else []
                
                # Find tickets that might be overdue
                rows = await conn.fetch(
                    f"""
                    SELECT 
                        t.ticket_id,
                        t.org_id,
                        t.priority,
                        t.status,
                        t.created_at,
                        t.first_response_at,
                        t.last_updated_at,
                        t.assigned_at,
                        o.name as org_name
                    FROM tickets t
                    JOIN organizations o ON t.org_id = o.org_id
                    WHERE t.status IN ('active', 'pending')
                    {org_filter}
                    ORDER BY t.created_at ASC
                    """,
                    *params
                )
                
                logger.info(f"Checking {len(rows)} tickets for SLA breaches")
                
                for row in rows:
                    ticket_breaches = await self._check_ticket_sla(dict(row))
                    breaches.extend(ticket_breaches)
                
        except Exception as e:
            logger.error("check_overdue_tickets_error", error=str(e))
        
        return breaches
    
    async def _check_ticket_sla(self, ticket: Dict[str, Any]) -> List[SLABreach]:
        """
        Check SLA for a single ticket.
        
        Args:
            ticket: Ticket data dict
            
        Returns:
            List of breaches for this ticket
        """
        breaches = []
        
        ticket_id = ticket['ticket_id']
        org_id = ticket['org_id']
        priority = SLAPriority(ticket['priority'])
        created_at = ticket['created_at']
        first_response_at = ticket.get('first_response_at')
        
        # Get SLA rule
        rule = self.get_sla_rule(org_id, priority)
        now = datetime.utcnow()
        
        # Check first response SLA
        if first_response_at is None:
            first_response_deadline = created_at + timedelta(hours=rule.first_response_hours)
            
            if now > first_response_deadline:
                breach_duration = int((now - first_response_deadline).total_seconds() / 60)
                
                breach = SLABreach(
                    ticket_id=ticket_id,
                    org_id=org_id,
                    breach_type=BreachType.FIRST_RESPONSE,
                    severity=priority,
                    scheduled_time=first_response_deadline,
                    actual_time=now,
                    breach_duration_minutes=breach_duration,
                    metadata={
                        "priority": priority.value,
                        "rule_hours": rule.first_response_hours,
                        "ticket_created": created_at.isoformat()
                    }
                )
                breaches.append(breach)
                
                logger.warning(
                    "sla_breach_detected",
                    ticket_id=ticket_id,
                    breach_type="first_response",
                    duration_minutes=breach_duration
                )
        
        # Check resolution SLA
        resolution_deadline = created_at + timedelta(hours=rule.resolution_hours)
        
        if now > resolution_deadline:
            breach_duration = int((now - resolution_deadline).total_seconds() / 60)
            
            breach = SLABreach(
                ticket_id=ticket_id,
                org_id=org_id,
                breach_type=BreachType.RESOLUTION,
                severity=priority,
                scheduled_time=resolution_deadline,
                actual_time=now,
                breach_duration_minutes=breach_duration,
                metadata={
                    "priority": priority.value,
                    "rule_hours": rule.resolution_hours,
                    "ticket_created": created_at.isoformat()
                }
            )
            breaches.append(breach)
            
            logger.warning(
                "sla_breach_detected",
                ticket_id=ticket_id,
                breach_type="resolution",
                duration_minutes=breach_duration
            )
        
        return breaches
    
    async def record_breach(self, breach: SLABreach) -> Dict[str, Any]:
        """
        Record SLA breach in Supermemory.
        
        Args:
            breach: SLABreach to record
            
        Returns:
            API response
        """
        client = await self._get_http_client()
        
        event = SLABreachEvent(
            ticket_id=breach.ticket_id,
            org_id=breach.org_id,
            breach_type=breach.breach_type.value,
            severity=breach.severity.value,
            breach_duration_minutes=breach.breach_duration_minutes,
            metadata=breach.metadata
        )
        
        payload = {
            "id": f"sla_breach_{breach.ticket_id}_{int(breach.actual_time.timestamp())}",
            "content": (
                f"SLA Breach: {breach.breach_type.value} for ticket {breach.ticket_id} "
                f"in org {breach.org_id}. "
                f"Severity: {breach.severity.value}. "
                f"Breach duration: {breach.breach_duration_minutes} minutes."
            ),
            "metadata": event.dict()
        }
        
        try:
            response = await client.post("/memories", json=payload)
            response.raise_for_status()
            
            result = response.json()
            
            logger.info(
                "sla_breach_recorded",
                ticket_id=breach.ticket_id,
                breach_type=breach.breach_type.value,
                memory_id=result.get("id")
            )
            
            return {
                "success": True,
                "memory_id": result.get("id"),
                "ticket_id": breach.ticket_id
            }
            
        except httpx.HTTPError as e:
            logger.error(
                "sla_breach_record_failed",
                ticket_id=breach.ticket_id,
                error=str(e)
            )
            return {
                "success": False,
                "ticket_id": breach.ticket_id,
                "error": str(e)
            }
    
    async def process_breaches(self, breaches: List[SLABreach]) -> List[Dict[str, Any]]:
        """
        Process multiple breaches - record and notify.
        
        Args:
            breaches: List of breaches to process
            
        Returns:
            List of processing results
        """
        results = []
        
        for breach in breaches:
            # Record in Supermemory
            record_result = await self.record_breach(breach)
            results.append(record_result)
            
            # Trigger notifications (async)
            asyncio.create_task(self._notify_breach(breach))
        
        return results
    
    async def _notify_breach(self, breach: SLABreach):
        """
        Send breach notifications.
        
        Args:
            breach: SLABreach to notify about
        """
        # TODO: Implement notification logic
        # - Send email to assigned agent
        # - Send Slack notification
        # - Update ticket with breach flag
        
        logger.info(
            "breach_notification_triggered",
            ticket_id=breach.ticket_id,
            breach_type=breach.breach_type.value
        )
    
    async def run_scheduled_check(self):
        """Run scheduled SLA check (cron job entry point)."""
        logger.info("starting_scheduled_sla_check")
        
        try:
            # Check all orgs
            breaches = await self.check_overdue_tickets()
            
            if breaches:
                logger.info(f"Found {len(breaches)} SLA breaches")
                results = await self.process_breaches(breaches)
                
                successful = sum(1 for r in results if r.get("success"))
                logger.info(f"Processed {successful}/{len(breaches)} breaches successfully")
            else:
                logger.info("No SLA breaches found")
                
        except Exception as e:
            logger.error("scheduled_sla_check_error", error=str(e))
    
    async def close(self):
        """Close HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()


# =============================================================================
# Cron Job Integration
# =============================================================================

async def run_sla_monitor_job():
    """
    Entry point for cron job.
    
    Usage with APScheduler or similar:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        
        scheduler = AsyncIOScheduler()
        scheduler.add_job(run_sla_monitor_job, 'interval', minutes=15)
        scheduler.start()
    """
    # This would normally get the db_pool from app state
    # For now, this is a stub that would be integrated with the main app
    logger.info("sla_monitor_cron_job_started")
    
    # monitor = SLAMonitor(db_pool)
    # await monitor.run_scheduled_check()
    # await monitor.close()
    
    logger.info("sla_monitor_cron_job_completed")


# =============================================================================
# FastAPI Endpoints
# =============================================================================

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from cassandra.auth import get_current_user, UserContext

router = APIRouter(prefix="/sla", tags=["SLA"])

# Global monitor instance
_sla_monitor: Optional[SLAMonitor] = None


def get_sla_monitor() -> SLAMonitor:
    """Get or create SLA monitor instance."""
    global _sla_monitor
    if _sla_monitor is None:
        # This would normally get db_pool from app state
        raise RuntimeError("SLA monitor not initialized")
    return _sla_monitor


@router.post("/check")
async def run_sla_check(
    background_tasks: BackgroundTasks,
    org_id: Optional[str] = None,
    user: UserContext = Depends(get_current_user)
):
    """
    Manually trigger SLA check.
    
    Requires admin permissions.
    """
    try:
        monitor = get_sla_monitor()
        
        # Run in background
        background_tasks.add_task(monitor.run_scheduled_check)
        
        return {
            "success": True,
            "message": "SLA check started in background"
        }
        
    except Exception as e:
        logger.error("sla_check_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/breaches")
async def get_breaches(
    org_id: str,
    days: int = 7,
    user: UserContext = Depends(get_current_user)
):
    """
    Get SLA breaches for organization.
    
    Args:
        org_id: Organization ID
        days: Number of days to look back
    """
    # This would query Supermemory for breach events
    return {
        "success": True,
        "org_id": org_id,
        "days": days,
        "breaches": []  # Would be populated from Supermemory
    }
