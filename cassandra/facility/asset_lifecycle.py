"""
F13: Asset Lifecycle Tracker

Tracks each asset's full lifecycle: procurement, service history,
depreciation, and end-of-life projection. Flags replace vs. repair
decisions and surfaces any verbal context from Supermemory.

Dual-read: Supabase (structured data) + Supermemory (verbal discussions).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

import structlog

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult

logger = structlog.get_logger("cassandra.facility.asset_lifecycle")

# Threshold: if maintenance cost exceeds this % of purchase cost, flag for replacement
REPLACE_VS_REPAIR_THRESHOLD = 0.60


@dataclass
class AssetLifecycleRecord:
    """Lifecycle record for a single asset."""
    asset_name: str
    asset_id: str
    age_years: float
    depreciation_pct: float
    total_maintenance_cost: float
    recommendation: Literal["replace", "repair", "monitor"]
    verbal_context: str
    purchase_cost: Optional[float] = None
    expected_life_years: Optional[int] = None
    remaining_life_years: Optional[float] = None


@dataclass
class AssetLifecycleReport:
    """Full asset lifecycle report."""
    assets: List[AssetLifecycleRecord] = field(default_factory=list)
    org_id: str = ""


class AssetLifecycleTracker:
    """
    F13: Asset Lifecycle Tracker.

    Computes lifecycle stage, depreciation, and replace vs. repair
    recommendations for all assets in an organization.

    Usage:
        tracker = AssetLifecycleTracker(db_pool)
        report = await tracker.generate_report(org_id="org_abc123")
    """

    def __init__(self, db_pool: Any):
        self.db_pool = db_pool

    async def generate_report(
        self,
        org_id: str,
        top_k: int = 50,
    ) -> AssetLifecycleReport:
        """
        Generate lifecycle report for all assets.

        Args:
            org_id: Organization ID (required, from JWT)
            top_k: Maximum assets to include

        Returns:
            AssetLifecycleReport with per-asset lifecycle records
        """
        result = AssetLifecycleReport(org_id=org_id)

        # Dual-read: Supabase + Supermemory
        context: ContextResult = await fetch_full_context(
            query="asset replacement discussed procurement budget end of life upgrade "
                  "life cycle depreciation",
            org_id=org_id,
            data_hints=["assets", "maintenance_logs"],
            top_k=top_k,
        )

        assets = [r for r in context.supabase_rows if r.get("_source_table") == "assets"]
        logs = [r for r in context.supabase_rows if r.get("_source_table") == "maintenance_logs"]

        # Index logs by asset_id
        logs_by_asset: Dict[str, List[Dict]] = {}
        for log in logs:
            aid = log.get("asset_id")
            if aid:
                logs_by_asset.setdefault(aid, []).append(log)

        # Verbal context from Supermemory
        verbal_chunks = [c.get("content", "") for c in context.memory_chunks]

        for asset in assets:
            purchase_cost = asset.get("purchase_cost", 0) or 0.0
            install_date = asset.get("install_date")
            expected_life = asset.get("expected_life_years")

            # Compute age
            age_years = 0.0
            if install_date:
                try:
                    install_dt = datetime.fromisoformat(install_date.replace("Z", "+00:00"))
                    age_years = round((datetime.utcnow() - install_dt).days / 365.0, 2)
                except (ValueError, TypeError):
                    age_years = 1.0

            # Compute depreciation (straight-line)
            if expected_life and expected_life > 0 and purchase_cost > 0:
                depreciation_pct = min(age_years / float(expected_life), 1.0)
            else:
                depreciation_pct = 0.0

            # Compute total maintenance cost
            asset_logs = logs_by_asset.get(asset.get("asset_id"), [])
            total_maint_cost = sum(
                (log.get("cost", 0) or 0) for log in asset_logs
            )

            # Determine recommendation
            if purchase_cost > 0:
                maint_ratio = total_maint_cost / purchase_cost
            else:
                maint_ratio = 0.0

            if maint_ratio >= REPLACE_VS_REPAIR_THRESHOLD:
                recommendation: Literal["replace", "repair", "monitor"] = "replace"
            elif maint_ratio >= REPLACE_VS_REPAIR_THRESHOLD * 0.5:
                recommendation = "repair"
            else:
                recommendation = "monitor"

            # Remaining life
            remaining_life = None
            if expected_life:
                remaining = float(expected_life) - age_years
                remaining_life = round(remaining, 2) if remaining > 0 else 0.0

            # Match verbal context to this asset
            asset_name_lower = asset.get("name", "").lower()
            relevant_chunks = [
                c for c in verbal_chunks if asset_name_lower in c.lower()
            ]
            verbal_context = " ".join(relevant_chunks[:3]) if relevant_chunks else ""

            result.assets.append(AssetLifecycleRecord(
                asset_name=asset.get("name", "Unknown"),
                asset_id=asset.get("asset_id", ""),
                age_years=age_years,
                depreciation_pct=round(depreciation_pct, 3),
                total_maintenance_cost=round(total_maint_cost, 2),
                recommendation=recommendation,
                verbal_context=verbal_context,
                purchase_cost=purchase_cost if purchase_cost > 0 else None,
                expected_life_years=int(expected_life) if expected_life else None,
                remaining_life_years=remaining_life,
            ))

        # Sort: replace first, then repair, then monitor
        order = {"replace": 0, "repair": 1, "monitor": 2}
        result.assets.sort(key=lambda a: (order[a.recommendation], -a.depreciation_pct))

        logger.info(
            "asset_lifecycle_report_completed",
            org_id=org_id,
            total_assets=len(result.assets),
            replace_count=sum(1 for a in result.assets if a.recommendation == "replace"),
        )

        return result
