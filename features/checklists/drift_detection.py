"""
F08: Checklist Drift Detection
Monitor completion rates and alert on significant drift from expected patterns.
"""

from typing import Dict, Any, List
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class ZoneDriftMetrics:
    """Drift metrics for a zone."""
    zone_id: str
    zone_name: str
    expected_completion_rate: float
    actual_completion_rate: float
    drift_percentage: float
    consecutive_weeks: int
    severity: str  # low, medium, high, critical


class ChecklistDriftDetector:
    """
    F08: Checklist Drift Detection
    
    Automated daily job comparing expected vs actual checklist completion.
    
    Flow: Daily cron → Aggregate by zone → Compare vs expected → Alert if drift > 20%
    """
    
    DRIFT_THRESHOLD = 0.20  # 20% drift triggers alert
    CRITICAL_THRESHOLD = 0.40  # 40% drift for auto-ticket
    CONSECUTIVE_WEEKS_THRESHOLD = 2
    
    def __init__(self, db_client, notification_service, memory_manager, ticket_tool):
        self.db = db_client
        self.notifications = notification_service
        self.memory_manager = memory_manager
        self.ticket_tool = ticket_tool
        
    async def run_daily_drift_check(self, org_id: str) -> Dict[str, Any]:
        """
        Run daily drift detection job.
        
        Returns:
            Drift report with alerts generated
        """
        # Step 1: Get all zones for org
        zones = await self._get_zones(org_id)
        
        # Step 2: Calculate drift for each zone
        drift_reports = []
        alerts_generated = 0
        auto_tickets = 0
        
        for zone in zones:
            metrics = await self._calculate_zone_drift(zone, org_id)
            
            if metrics.drift_percentage >= self.DRIFT_THRESHOLD:
                # Generate alert
                await self._generate_drift_alert(metrics, org_id)
                alerts_generated += 1
                
                # Auto-ticket if critical
                if (metrics.drift_percentage >= self.CRITICAL_THRESHOLD and 
                    metrics.consecutive_weeks >= self.CONSECUTIVE_WEEKS_THRESHOLD):
                    await self._create_drift_ticket(metrics, org_id)
                    auto_tickets += 1
                
                drift_reports.append(metrics)
        
        # Step 3: Log drift check to Supermemory
        await self._log_drift_check(drift_reports, org_id)
        
        return {
            "success": True,
            "zones_checked": len(zones),
            "zones_with_drift": len(drift_reports),
            "alerts_generated": alerts_generated,
            "auto_tickets_created": auto_tickets,
            "drift_reports": [self._metrics_to_dict(m) for m in drift_reports]
        }
    
    async def _get_zones(self, org_id: str) -> List[Dict[str, Any]]:
        """Get all zones for organization."""
        query = """
            SELECT DISTINCT 
                COALESCE(a.zone, 'Unknown') as zone_id,
                COALESCE(a.zone, 'Unknown') as zone_name,
                p.id as property_id
            FROM assets a
            JOIN properties p ON p.id = a.property_id
            WHERE p.org_id = $1
            AND a.zone IS NOT NULL
        """
        results = await self.db.fetch(query, org_id)
        return [dict(r) for r in results]
    
    async def _calculate_zone_drift(self, zone: Dict[str, Any], 
                                    org_id: str) -> ZoneDriftMetrics:
        """Calculate drift metrics for a zone."""
        zone_id = zone['zone_id']
        
        # Get expected completion rate (from historical baseline or config)
        expected_rate = await self._get_expected_completion_rate(zone_id, org_id)
        
        # Get actual completion rate (last 7 days)
        actual_rate = await self._get_actual_completion_rate(zone_id, org_id)
        
        # Calculate drift
        drift = expected_rate - actual_rate
        drift_percentage = drift / expected_rate if expected_rate > 0 else 0
        
        # Count consecutive weeks of drift
        consecutive_weeks = await self._count_consecutive_drift_weeks(zone_id, org_id)
        
        # Determine severity
        if drift_percentage >= self.CRITICAL_THRESHOLD:
            severity = "critical"
        elif drift_percentage >= self.DRIFT_THRESHOLD:
            severity = "high"
        elif drift_percentage >= 0.10:
            severity = "medium"
        else:
            severity = "low"
        
        return ZoneDriftMetrics(
            zone_id=zone_id,
            zone_name=zone['zone_name'],
            expected_completion_rate=expected_rate,
            actual_completion_rate=actual_rate,
            drift_percentage=drift_percentage,
            consecutive_weeks=consecutive_weeks,
            severity=severity
        )
    
    async def _get_expected_completion_rate(self, zone_id: str, org_id: str) -> float:
        """Get expected completion rate for zone."""
        # First check for configured target
        query = """
            SELECT target_completion_rate
            FROM zone_targets
            WHERE zone_id = $1 AND org_id = $2
        """
        result = await self.db.fetchrow(query, zone_id, org_id)
        
        if result and result['target_completion_rate']:
            return result['target_completion_rate']
        
        # Fallback: historical average (last 90 days)
        query = """
            SELECT AVG(completion_rate) as avg_rate
            FROM (
                SELECT 
                    DATE_TRUNC('week', ci.completed_at) as week,
                    COUNT(CASE WHEN ci.completed THEN 1 END)::float / 
                        NULLIF(COUNT(*), 0) as completion_rate
                FROM checklist_items ci
                JOIN checklists c ON c.id = ci.checklist_id
                JOIN assets a ON a.id = c.asset_id
                WHERE a.zone = $1
                AND c.org_id = $2
                AND ci.completed_at >= NOW() - INTERVAL '90 days'
                GROUP BY DATE_TRUNC('week', ci.completed_at)
            ) weekly_rates
        """
        result = await self.db.fetchrow(query, zone_id, org_id)
        
        return result['avg_rate'] if result and result['avg_rate'] else 0.95  # Default 95%
    
    async def _get_actual_completion_rate(self, zone_id: str, org_id: str) -> float:
        """Get actual completion rate for last 7 days."""
        query = """
            SELECT 
                COUNT(CASE WHEN ci.completed THEN 1 END)::float / 
                    NULLIF(COUNT(*), 0) as completion_rate
            FROM checklist_items ci
            JOIN checklists c ON c.id = ci.checklist_id
            JOIN assets a ON a.id = c.asset_id
            WHERE a.zone = $1
            AND c.org_id = $2
            AND (ci.completed_at >= NOW() - INTERVAL '7 days'
                 OR ci.completed_at IS NULL)
        """
        result = await self.db.fetchrow(query, zone_id, org_id)
        return result['completion_rate'] if result and result['completion_rate'] else 0.0
    
    async def _count_consecutive_drift_weeks(self, zone_id: str, org_id: str) -> int:
        """Count consecutive weeks with drift above threshold."""
        query = """
            WITH weekly_rates AS (
                SELECT 
                    DATE_TRUNC('week', ci.completed_at) as week,
                    COUNT(CASE WHEN ci.completed THEN 1 END)::float / 
                        NULLIF(COUNT(*), 0) as completion_rate
                FROM checklist_items ci
                JOIN checklists c ON c.id = ci.checklist_id
                JOIN assets a ON a.id = c.asset_id
                WHERE a.zone = $1
                AND c.org_id = $2
                AND ci.completed_at >= NOW() - INTERVAL '12 weeks'
                GROUP BY DATE_TRUNC('week', ci.completed_at)
            )
            SELECT COUNT(*) as consecutive_weeks
            FROM weekly_rates
            WHERE completion_rate < 0.80
            AND week >= NOW() - INTERVAL '4 weeks'
        """
        result = await self.db.fetchrow(query, zone_id, org_id)
        return result['consecutive_weeks'] if result else 0
    
    async def _generate_drift_alert(self, metrics: ZoneDriftMetrics, org_id: str):
        """Generate drift alert notification."""
        # Get FM director
        director = await self._get_fm_director(org_id)
        
        if director:
            await self.notifications.send_push(
                user_id=director,
                title=f"⚠️ Checklist Drift Alert - {metrics.zone_name}",
                body=f"Completion rate dropped to {metrics.actual_completion_rate:.0%} "
                     f"(target: {metrics.expected_completion_rate:.0%}). "
                     f"{metrics.consecutive_weeks} consecutive weeks.",
                data={
                    "action": "view_drift_report",
                    "zone_id": metrics.zone_id,
                    "severity": metrics.severity
                }
            )
    
    async def _create_drift_ticket(self, metrics: ZoneDriftMetrics, org_id: str):
        """Create auto-ticket for critical drift."""
        ticket_data = {
            "title": f"CRITICAL: Checklist drift in {metrics.zone_name}",
            "description": f"""
Zone {metrics.zone_name} has shown checklist completion drift for {metrics.consecutive_weeks} consecutive weeks.

Current completion rate: {metrics.actual_completion_rate:.1%}
Expected rate: {metrics.expected_completion_rate:.1%}
Drift: {metrics.drift_percentage:.1%}

Recommended actions:
1. Review inspector assignment for this zone
2. Check for equipment/access issues
3. Verify checklist templates are appropriate
            """.strip(),
            "priority": "high",
            "category": "compliance",
            "org_id": org_id,
            "auto_created": True,
            "source": "drift_detection"
        }
        
        await self.ticket_tool.create_ticket(ticket_data)
    
    async def _get_fm_director(self, org_id: str) -> str:
        """Get FM director user ID."""
        query = """
            SELECT id FROM users
            WHERE org_id = $1 AND role IN ('fm_director', 'admin')
            LIMIT 1
        """
        result = await self.db.fetchrow(query, org_id)
        return result['id'] if result else None
    
    async def _log_drift_check(self, reports: List[ZoneDriftMetrics], org_id: str):
        """Log drift check to Supermemory."""
        event_data = {
            "event_type": "DRIFT_CHECK",
            "zones_checked": len(reports),
            "zones_with_drift": len([r for r in reports if r.drift_percentage >= self.DRIFT_THRESHOLD]),
            "timestamp": datetime.now().isoformat()
        }
        
        await self.memory_manager.add_memory(
            content=f"Daily drift check: {event_data['zones_with_drift']} zones with significant drift",
            memory_type="DRIFT_CHECK",
            org_id=org_id,
            entity_id="system",
            metadata=event_data,
            confidence=1.0
        )
    
    def _metrics_to_dict(self, metrics: ZoneDriftMetrics) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "zone_id": metrics.zone_id,
            "zone_name": metrics.zone_name,
            "expected_rate": metrics.expected_completion_rate,
            "actual_rate": metrics.actual_completion_rate,
            "drift_percentage": metrics.drift_percentage,
            "consecutive_weeks": metrics.consecutive_weeks,
            "severity": metrics.severity
        }