"""
F14: Vendor Performance Scoring

Scores vendors on resolution time, SLA adherence, cost efficiency,
and verbal sentiment from Supermemory. Produces composite ranked scorecards.

Dual-read: Supabase (tickets, vendors) + Supermemory (verbal feedback).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

import structlog

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult

logger = structlog.get_logger("cassandra.facility.vendor_performance")


@dataclass
class VendorScore:
    """Performance score for a single vendor."""
    vendor_name: str
    vendor_id: str
    composite_score: float
    resolution_time_avg_hrs: float
    sla_breach_pct: float
    avg_cost_per_ticket: float
    verbal_sentiment: Literal["positive", "negative", "neutral"]
    verbal_mentions: List[str]
    total_tickets: int = 0


@dataclass
class VendorScorecard:
    """Full vendor performance scorecard."""
    vendors: List[VendorScore] = field(default_factory=list)
    org_id: str = ""


# Sentiment keywords for simple classification
POSITIVE_KW = ["good", "great", "excellent", "fast", "reliable", "helpful", "resolved", "fixed"]
NEGATIVE_KW = ["bad", "poor", "slow", "delayed", "failed", "broken", "complaint", "issue", "problem"]


class VendorScorer:
    """
    F14: Vendor Performance Scoring.

    Scores vendors across 4 dimensions weighted as:
        resolution_time 30%, sla_breach 35%, cost 20%, verbal_sentiment 15%

    Usage:
        scorer = VendorScorer(db_pool)
        scorecard = await scorer.score_vendors(org_id="org_abc123")
    """

    def __init__(self, db_pool: Any):
        self.db_pool = db_pool

    async def score_vendors(
        self,
        org_id: str,
        top_k: int = 20,
    ) -> VendorScorecard:
        """
        Score all vendors for an organization.

        Args:
            org_id: Organization ID (required, from JWT)
            top_k: Maximum vendors to return

        Returns:
            VendorScorecard with per-vendor scores
        """
        result = VendorScorecard(org_id=org_id)

        # Dual-read: Supabase + Supermemory
        context: ContextResult = await fetch_full_context(
            query="vendor feedback complaint quality delayed poor work good service "
                  "satisfaction rating",
            org_id=org_id,
            data_hints=["vendors", "tickets"],
            top_k=top_k,
        )

        vendors = [r for r in context.supabase_rows if r.get("_source_table") == "vendors"]
        tickets = [r for r in context.supabase_rows if r.get("_source_table") == "tickets"]

        # Index tickets by vendor_id
        tickets_by_vendor: Dict[str, List[Dict]] = {}
        for ticket in tickets:
            vid = ticket.get("vendor_id")
            if vid:
                tickets_by_vendor.setdefault(vid, []).append(ticket)

        # Verbal mentions from Supermemory
        verbal_chunks = [c.get("content", "") for c in context.memory_chunks]

        for vendor in vendors:
            vid = vendor.get("vendor_id")
            vendor_tickets = tickets_by_vendor.get(vid, [])

            if not vendor_tickets:
                continue  # skip vendors with no tickets

            # --- Resolution time ---
            resolution_times: List[float] = []
            for t in vendor_tickets:
                created = t.get("created_at")
                closed = t.get("closed_at")
                if created and closed:
                    try:
                        c_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        cl_dt = datetime.fromisoformat(closed.replace("Z", "+00:00"))
                        hrs = (cl_dt - c_dt).total_seconds() / 3600.0
                        resolution_times.append(hrs)
                    except (ValueError, TypeError):
                        pass
            avg_res_hrs = round(sum(resolution_times) / len(resolution_times), 1) if resolution_times else 0.0

            # --- SLA breach % ---
            total = len(vendor_tickets)
            breaches = sum(1 for t in vendor_tickets if t.get("sla_breach", False))
            breach_pct = round((breaches / total) * 100, 1) if total else 0.0

            # --- Average cost per ticket ---
            costs = [t.get("cost", 0) or 0 for t in vendor_tickets]
            avg_cost = round(sum(costs) / len(costs), 2) if costs else 0.0

            # --- Verbal sentiment ---
            vendor_name_lower = vendor.get("name", "").lower()
            vendor_mentions = [
                c for c in verbal_chunks
                if vendor_name_lower in c.lower() or any(
                    kw in c.lower() for kw in vendor_name_lower.split()
                )
            ]
            sentiment = self._classify_sentiment(vendor_mentions)

            # --- Normalize scores to 0-100 ---
            # Resolution time: lower is better
            res_score = max(0.0, 100.0 - (avg_res_hrs / 24.0 * 100.0)) if avg_res_hrs else 100.0
            # SLA: lower breach % is better
            sla_score = max(0.0, 100.0 - breach_pct)
            # Cost: normalized to avg (cap at 100)
            cost_score = max(0.0, min(100.0, 100.0 - (avg_cost / 1000.0 * 100.0))) if avg_cost else 100.0
            # Sentiment
            sent_map = {"positive": 100.0, "neutral": 50.0, "negative": 0.0}
            sent_score = sent_map.get(sentiment, 50.0)

            composite = round(
                res_score * 0.30
                + sla_score * 0.35
                + cost_score * 0.20
                + sent_score * 0.15,
                1
            )

            result.vendors.append(VendorScore(
                vendor_name=vendor.get("name", "Unknown"),
                vendor_id=vid,
                composite_score=composite,
                resolution_time_avg_hrs=avg_res_hrs,
                sla_breach_pct=breach_pct,
                avg_cost_per_ticket=avg_cost,
                verbal_sentiment=sentiment,
                verbal_mentions=vendor_mentions[:5],
                total_tickets=total,
            ))

        # Sort by composite score descending
        result.vendors.sort(key=lambda v: v.composite_score, reverse=True)

        logger.info(
            "vendor_scoring_completed",
            org_id=org_id,
            vendor_count=len(result.vendors),
        )

        return result

    def _classify_sentiment(
        self,
        mentions: List[str],
    ) -> Literal["positive", "negative", "neutral"]:
        """Classify sentiment of a list of text mentions."""
        if not mentions:
            return "neutral"
        pos_count = sum(1 for m in mentions for kw in POSITIVE_KW if kw in m.lower())
        neg_count = sum(1 for m in mentions for kw in NEGATIVE_KW if kw in m.lower())
        if pos_count > neg_count:
            return "positive"
        elif neg_count > pos_count:
            return "negative"
        return "neutral"
