"""
F12: Predictive Maintenance Cost Forecasting

Forecasts which assets are likely to fail and their estimated cost,
based on maintenance history, asset age, ticket frequency, and
verbal complaints captured in Supermemory (unticketted issues).

Dual-read: Supabase (structured data) + Supermemory (verbal context).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import logging

import structlog

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult

logger = structlog.get_logger("cassandra.facility.predictive_maintenance")

# Threshold: assets not serviced in this many days are at-risk
SERVICE_OVERDUE_DAYS = 90


@dataclass
class AssetRiskProfile:
    """Risk profile for a single asset."""
    asset_name: str
    asset_id: str
    risk_score: float          # 0.0 to 1.0
    days_overdue: int
    estimated_repair_cost: float
    verbal_complaints: List[str]
    recommended_action_by: str  # ISO date
    last_serviced: Optional[str] = None
    ticket_count_90d: int = 0


@dataclass
class MaintenanceForecast:
    """Full maintenance forecast result."""
    at_risk_assets: List[AssetRiskProfile] = field(default_factory=list)
    total_forecast_cost: float = 0.0
    org_id: str = ""


class PredictiveMaintenanceEngine:
    """
    F12: Predictive Maintenance Cost Forecasting.

    Identifies assets at risk of failure by combining:
    - Supabase: service history, ticket frequency, asset age
    - Supermemory: verbal complaints not yet ticketed

    Usage:
        engine = PredictiveMaintenanceEngine(db_pool)
        forecast = await engine.forecast(org_id="org_abc123")
    """

    def __init__(self, db_pool: Any):
        self.db_pool = db_pool

    async def forecast(
        self,
        org_id: str,
        overdue_days: int = SERVICE_OVERDUE_DAYS,
        top_k: int = 10,
    ) -> MaintenanceForecast:
        """
        Generate maintenance forecast for at-risk assets.

        Args:
            org_id: Organization ID (required, from JWT)
            overdue_days: Days since last service to flag as at-risk
            top_k: Maximum assets to return

        Returns:
            MaintenanceForecast with ranked at-risk assets
        """
        result = MaintenanceForecast(org_id=org_id)

        # Dual-read: Supabase + Supermemory, fired simultaneously
        context: ContextResult = await fetch_full_context(
            query="maintenance complaints asset failure reported verbally issues not yet ticketed",
            org_id=org_id,
            data_hints=["assets", "maintenance_logs", "tickets"],
            top_k=top_k,
        )

        # Pull at-risk assets from Supabase rows
        assets = [r for r in context.supabase_rows if r.get("_source_table") == "assets"]
        logs = [r for r in context.supabase_rows if r.get("_source_table") == "maintenance_logs"]
        tickets = [r for r in context.supabase_rows if r.get("_source_table") == "tickets"]

        # Index maintenance_logs by asset_id
        logs_by_asset: Dict[str, List[Dict]] = {}
        for log in logs:
            aid = log.get("asset_id")
            if aid:
                logs_by_asset.setdefault(aid, []).append(log)

        # Index tickets by asset_id
        tickets_by_asset: Dict[str, List[Dict]] = {}
        cutoff = datetime.utcnow() - timedelta(days=90)
        for ticket in tickets:
            aid = ticket.get("asset_id")
            created = ticket.get("created_at", "")
            if aid and created:
                try:
                    t_created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if t_created >= cutoff:
                        tickets_by_asset.setdefault(aid, []).append(ticket)
                except (ValueError, TypeError):
                    pass

        # Verbal complaints from Supermemory
        verbal_chunks: List[str] = [c.get("content", "") for c in context.memory_chunks]

        for asset in assets:
            last_serviced = asset.get("last_serviced")
            install_date = asset.get("install_date")

            # Compute days overdue
            days_overdue = 0
            if last_serviced:
                try:
                    last_dt = datetime.fromisoformat(last_serviced.replace("Z", "+00:00"))
                    days_overdue = (datetime.utcnow() - last_dt).days
                except (ValueError, TypeError):
                    days_overdue = overdue_days + 1
            elif install_date:
                try:
                    install_dt = datetime.fromisoformat(install_date.replace("Z", "+00:00"))
                    days_overdue = (datetime.utcnow() - install_dt).days
                except (ValueError, TypeError):
                    days_overdue = 0
            else:
                days_overdue = overdue_days + 1

            if days_overdue < overdue_days:
                continue  # not overdue

            # Risk score: combination of overdue days, ticket count, age
            age_years = 0
            if install_date:
                try:
                    install_dt = datetime.fromisoformat(install_date.replace("Z", "+00:00"))
                    age_years = (datetime.utcnow() - install_dt).days / 365.0
                except (ValueError, TypeError):
                    age_years = 1.0

            ticket_count = len(tickets_by_asset.get(asset.get("asset_id"), []))
            overdue_pct = min(days_overdue / overdue_days, 3.0) / 3.0
            ticket_factor = min(ticket_count / 5.0, 1.0)
            age_factor = min(age_years / 10.0, 1.0)
            risk_score = round(overdue_pct * 0.5 + ticket_factor * 0.3 + age_factor * 0.2, 3)

            # Estimate repair cost from average of past logs
            asset_logs = logs_by_asset.get(asset.get("asset_id"), [])
            if asset_logs:
                costs = [l.get("cost", 0) or 0 for l in asset_logs]
                avg_cost = sum(costs) / len(costs) if costs else 0.0
            else:
                avg_cost = 0.0
            estimated_repair_cost = round(avg_cost * (1 + days_overdue / overdue_days), 2)

            # Recommended action date: 14 days from now
            action_by = (datetime.utcnow() + timedelta(days=14)).date().isoformat()

            # Match verbal complaints to this asset by name
            asset_name_lower = asset.get("name", "").lower()
            relevant_complaints = [
                c for c in verbal_chunks
                if asset_name_lower in c.lower()
            ]

            result.at_risk_assets.append(AssetRiskProfile(
                asset_name=asset.get("name", "Unknown"),
                asset_id=asset.get("asset_id", ""),
                risk_score=risk_score,
                days_overdue=days_overdue,
                estimated_repair_cost=estimated_repair_cost,
                verbal_complaints=relevant_complaints,
                recommended_action_by=action_by,
                last_serviced=last_serviced,
                ticket_count_90d=ticket_count,
            ))

        # Sort by risk score descending
        result.at_risk_assets.sort(key=lambda a: a.risk_score, reverse=True)
        result.total_forecast_cost = round(
            sum(a.estimated_repair_cost for a in result.at_risk_assets), 2
        )

        logger.info(
            "maintenance_forecast_completed",
            org_id=org_id,
            at_risk_count=len(result.at_risk_assets),
            total_cost=result.total_forecast_cost,
        )

        return result
