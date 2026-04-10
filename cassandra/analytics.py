"""
T43: Analytics

This module provides analytics functionality:
- Commitment completion metrics
- Dashboard endpoints
- Time-series data
- Reporting

Features:
- Metric aggregation
- Trend analysis
- Export capabilities
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any, List

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger("cassandra.analytics")


class MetricType(str, Enum):
    """Types of metrics."""
    COUNT = "count"
    DURATION = "duration"
    PERCENTAGE = "percentage"
    CURRENCY = "currency"


class TimeGranularity(str, Enum):
    """Time granularity for metrics."""
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass
class Metric:
    """Single metric data point."""
    name: str
    value: float
    timestamp: datetime
    dimensions: Dict[str, str]
    metadata: Dict[str, Any]


class AnalyticsQuery(BaseModel):
    """Query for analytics data."""
    
    org_id: str = Field(...)
    start_date: datetime = Field(...)
    end_date: datetime = Field(...)
    metrics: List[str] = Field(default_factory=list)
    granularity: TimeGranularity = Field(default=TimeGranularity.DAILY)
    filters: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        json_schema_extra = {
            "example": {
                "org_id": "org_12345",
                "start_date": "2024-01-01T00:00:00Z",
                "end_date": "2024-01-31T23:59:59Z",
                "metrics": ["tickets_created", "tickets_resolved"],
                "granularity": "daily"
            }
        }


class DashboardMetrics(BaseModel):
    """Dashboard metrics response."""
    
    org_id: str
    period: str
    tickets_created: int = 0
    tickets_resolved: int = 0
    tickets_open: int = 0
    avg_resolution_time_hours: float = 0.0
    sla_compliance_rate: float = 0.0
    customer_satisfaction: float = 0.0
    top_categories: List[Dict[str, Any]] = []
    agent_performance: List[Dict[str, Any]] = []


class AnalyticsManager:
    """
    Manages analytics and metrics.
    
    Usage:
        analytics = AnalyticsManager(db_pool)
        
        # Get dashboard metrics
        metrics = await analytics.get_dashboard_metrics(
            org_id="org_123",
            period="7d"
        )
        
        # Query time series
        data = await analytics.query_time_series(
            metric="tickets_created",
            org_id="org_123",
            start=datetime.now() - timedelta(days=30),
            end=datetime.now()
        )
    """
    
    def __init__(self, db_pool: Any):
        """
        Initialize analytics manager.
        
        Args:
            db_pool: Database connection pool
        """
        self.db_pool = db_pool
        
        logger.info("analytics_manager_initialized")
    
    async def get_dashboard_metrics(
        self,
        org_id: str,
        period: str = "7d"
    ) -> DashboardMetrics:
        """
        Get dashboard metrics for organization.
        
        Args:
            org_id: Organization ID
            period: Time period (e.g., "7d", "30d", "1m")
            
        Returns:
            DashboardMetrics
        """
        # Parse period
        start_date = self._parse_period(period)
        end_date = datetime.utcnow()
        
        metrics = DashboardMetrics(
            org_id=org_id,
            period=period
        )
        
        async with self.db_pool.acquire() as conn:
            # Tickets created
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as count
                FROM tickets
                WHERE org_id = $1
                AND created_at >= $2
                AND created_at <= $3
                """,
                org_id, start_date, end_date
            )
            metrics.tickets_created = row["count"]
            
            # Tickets resolved
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as count
                FROM tickets
                WHERE org_id = $1
                AND status IN ('resolved', 'closed')
                AND updated_at >= $2
                AND updated_at <= $3
                """,
                org_id, start_date, end_date
            )
            metrics.tickets_resolved = row["count"]
            
            # Open tickets
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as count
                FROM tickets
                WHERE org_id = $1
                AND status IN ('active', 'pending')
                """,
                org_id
            )
            metrics.tickets_open = row["count"]
            
            # Average resolution time
            row = await conn.fetchrow(
                """
                SELECT AVG(EXTRACT(EPOCH FROM (updated_at - created_at)) / 3600) as avg_hours
                FROM tickets
                WHERE org_id = $1
                AND status IN ('resolved', 'closed')
                AND created_at >= $2
                AND created_at <= $3
                """,
                org_id, start_date, end_date
            )
            metrics.avg_resolution_time_hours = row["avg_hours"] or 0.0
            
            # SLA compliance
            row = await conn.fetchrow(
                """
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN sla_breach = false THEN 1 END) as compliant
                FROM tickets
                WHERE org_id = $1
                AND created_at >= $2
                AND created_at <= $3
                """,
                org_id, start_date, end_date
            )
            
            if row["total"] > 0:
                metrics.sla_compliance_rate = row["compliant"] / row["total"]
            
            # Top categories
            rows = await conn.fetch(
                """
                SELECT 
                    UNNEST(tags) as category,
                    COUNT(*) as count
                FROM tickets
                WHERE org_id = $1
                AND created_at >= $2
                AND created_at <= $3
                GROUP BY category
                ORDER BY count DESC
                LIMIT 5
                """,
                org_id, start_date, end_date
            )
            
            metrics.top_categories = [
                {"category": row["category"], "count": row["count"]}
                for row in rows
            ]
        
        return metrics
    
    def _parse_period(self, period: str) -> datetime:
        """Parse period string to datetime."""
        now = datetime.utcnow()
        
        if period.endswith("d"):
            days = int(period[:-1])
            return now - timedelta(days=days)
        elif period.endswith("w"):
            weeks = int(period[:-1])
            return now - timedelta(weeks=weeks)
        elif period.endswith("m"):
            months = int(period[:-1])
            return now - timedelta(days=months * 30)
        elif period.endswith("h"):
            hours = int(period[:-1])
            return now - timedelta(hours=hours)
        
        return now - timedelta(days=7)  # Default to 7 days
    
    async def query_time_series(
        self,
        metric: str,
        org_id: str,
        start: datetime,
        end: datetime,
        granularity: TimeGranularity = TimeGranularity.DAILY
    ) -> List[Dict[str, Any]]:
        """
        Query time series data.
        
        Args:
            metric: Metric name
            org_id: Organization ID
            start: Start date
            end: End date
            granularity: Time granularity
            
        Returns:
            Time series data points
        """
        # Determine grouping based on granularity
        if granularity == TimeGranularity.HOURLY:
            group_by = "DATE_TRUNC('hour', created_at)"
        elif granularity == TimeGranularity.DAILY:
            group_by = "DATE_TRUNC('day', created_at)"
        elif granularity == TimeGranularity.WEEKLY:
            group_by = "DATE_TRUNC('week', created_at)"
        else:
            group_by = "DATE_TRUNC('month', created_at)"
        
        async with self.db_pool.acquire() as conn:
            if metric == "tickets_created":
                rows = await conn.fetch(
                    f"""
                    SELECT 
                        {group_by} as period,
                        COUNT(*) as value
                    FROM tickets
                    WHERE org_id = $1
                    AND created_at >= $2
                    AND created_at <= $3
                    GROUP BY period
                    ORDER BY period
                    """,
                    org_id, start, end
                )
            elif metric == "tickets_resolved":
                rows = await conn.fetch(
                    f"""
                    SELECT 
                        {group_by} as period,
                        COUNT(*) as value
                    FROM tickets
                    WHERE org_id = $1
                    AND status IN ('resolved', 'closed')
                    AND updated_at >= $2
                    AND updated_at <= $3
                    GROUP BY period
                    ORDER BY period
                    """,
                    org_id, start, end
                )
            else:
                return []
            
            return [
                {
                    "timestamp": row["period"].isoformat(),
                    "value": row["value"]
                }
                for row in rows
            ]
    
    async def record_event(
        self,
        event_type: str,
        org_id: str,
        user_id: Optional[str],
        data: Dict[str, Any]
    ):
        """
        Record analytics event.
        
        Args:
            event_type: Type of event
            org_id: Organization ID
            user_id: User ID (optional)
            data: Event data
        """
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO analytics_events (
                    event_type, org_id, user_id, event_data, created_at
                ) VALUES ($1, $2, $3, $4, NOW())
                """,
                event_type, org_id, user_id, json.dumps(data)
            )
    
    async def get_commitment_metrics(
        self,
        org_id: str,
        start: datetime,
        end: datetime
    ) -> Dict[str, Any]:
        """
        Get commitment completion metrics.
        
        Args:
            org_id: Organization ID
            start: Start date
            end: End date
            
        Returns:
            Commitment metrics
        """
        async with self.db_pool.acquire() as conn:
            # Total commitments
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as total
                FROM commitments
                WHERE org_id = $1
                AND created_at >= $2
                AND created_at <= $3
                """,
                org_id, start, end
            )
            total = row["total"]
            
            # Completed commitments
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as completed
                FROM commitments
                WHERE org_id = $1
                AND status = 'completed'
                AND created_at >= $2
                AND created_at <= $3
                """,
                org_id, start, end
            )
            completed = row["completed"]
            
            # Overdue commitments
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as overdue
                FROM commitments
                WHERE org_id = $1
                AND status = 'overdue'
                AND created_at >= $2
                AND created_at <= $3
                """,
                org_id, start, end
            )
            overdue = row["overdue"]
            
            completion_rate = completed / total if total > 0 else 0.0
            
            return {
                "total_commitments": total,
                "completed": completed,
                "overdue": overdue,
                "in_progress": total - completed - overdue,
                "completion_rate": completion_rate,
                "period": {
                    "start": start.isoformat(),
                    "end": end.isoformat()
                }
            }


# =============================================================================
# FastAPI Endpoints
# =============================================================================

from fastapi import APIRouter, HTTPException, Depends
from cassandra.auth import get_current_user, UserContext

router = APIRouter(prefix="/analytics", tags=["Analytics"])

_analytics_manager: Optional[AnalyticsManager] = None


def get_analytics_manager() -> AnalyticsManager:
    """Get or create analytics manager."""
    global _analytics_manager
    if _analytics_manager is None:
        raise RuntimeError("Analytics manager not initialized")
    return _analytics_manager


@router.get("/dashboard")
async def get_dashboard(
    org_id: str,
    period: str = "7d",
    user: UserContext = Depends(get_current_user)
):
    """Get dashboard metrics."""
    try:
        manager = get_analytics_manager()
        metrics = await manager.get_dashboard_metrics(org_id, period)
        return metrics.dict()
    except Exception as e:
        logger.error("dashboard_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query")
async def query_analytics(
    query: AnalyticsQuery,
    user: UserContext = Depends(get_current_user)
):
    """Query analytics data."""
    try:
        manager = get_analytics_manager()
        
        results = {}
        for metric in query.metrics:
            results[metric] = await manager.query_time_series(
                metric=metric,
                org_id=query.org_id,
                start=query.start_date,
                end=query.end_date,
                granularity=query.granularity
            )
        
        return {
            "success": True,
            "data": results
        }
    except Exception as e:
        logger.error("analytics_query_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/commitments")
async def get_commitment_metrics(
    org_id: str,
    days: int = 30,
    user: UserContext = Depends(get_current_user)
):
    """Get commitment completion metrics."""
    try:
        manager = get_analytics_manager()
        end = datetime.utcnow()
        start = end - timedelta(days=days)
        
        metrics = await manager.get_commitment_metrics(org_id, start, end)
        return metrics
    except Exception as e:
        logger.error("commitment_metrics_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
