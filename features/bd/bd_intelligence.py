"""
CAT-D: Business Development Intelligence (F16-F20)
- F16: New Property Feasibility Report
- F17: Competitive Benchmarking
- F18: Lease Expiry & Renewal Intelligence
- F19: Market Rate Benchmarking for New Bids
- F20: Portfolio Health Scorecard
"""

from typing import Dict, Any, List
from dataclasses import dataclass


class BDIntelligenceService:
    """Business Development Intelligence features."""
    
    def __init__(self, perplexity_client, db_client, truth_ledger, analytics):
        self.perplexity = perplexity_client
        self.db = db_client
        self.truth_ledger = truth_ledger
        self.analytics = analytics
    
    # F16: New Property Feasibility Report
    async def generate_feasibility_report(self, property_info: Dict, org_id: str) -> Dict:
        """Generate structured feasibility report for new property."""
        # OPEX estimate
        opex = await self._estimate_opex(property_info)
        
        # Deferred maintenance risk
        maintenance_risk = await self._assess_maintenance_risk(property_info)
        
        # Portfolio comparables
        comparables = await self._get_comparables(property_info, org_id)
        
        # Regulatory context
        regulatory = await self._get_regulatory_context(property_info)
        
        # LLM synthesis
        recommendation = await self._synthesize_recommendation(
            opex, maintenance_risk, comparables, regulatory
        )
        
        # Store in Truth Ledger
        await self.truth_ledger.record_event(
            entity_type="DECISION",
            entity_id=f"FEASIBILITY-{property_info.get('name', 'UNKNOWN')}",
            org_id=org_id,
            action="feasibility_generated",
            data={
                "opex_estimate": opex,
                "maintenance_risk": maintenance_risk,
                "recommendation": recommendation
            },
            confidence=recommendation.get('confidence', 0.7)
        )
        
        return {
            "opex_estimate": opex,
            "deferred_maintenance_risk": maintenance_risk,
            "portfolio_comparables": comparables,
            "regulatory_flags": regulatory,
            "recommendation": recommendation,
            "report_format": "4-page structured"
        }
    
    # F17: Competitive Benchmarking
    async def get_competitive_benchmark(self, property_type: str, city: str, 
                                        org_id: str) -> Dict:
        """Compare portfolio OPEX vs market benchmarks."""
        # Market benchmark from Perplexity
        market = await self.perplexity.query(
            f"{city} {property_type} facility management cost per sqft 2025"
        )
        
        # Portfolio actual
        portfolio = await self._get_portfolio_opex(property_type, org_id)
        
        # Gap analysis
        delta = portfolio['per_sqft'] - market['per_sqft']
        
        return {
            "portfolio_opex_per_sqft": portfolio['per_sqft'],
            "market_benchmark": market['per_sqft'],
            "delta": delta,
            "delta_percent": (delta / market['per_sqft']) * 100,
            "properties_above_market": portfolio['above_market'],
            "sources": market.get('citations', [])
        }
    
    # F18: Lease Expiry & Renewal Intelligence
    async def analyze_lease_renewals(self, months_ahead: int, org_id: str) -> Dict:
        """Cross-reference lease data with performance metrics for renewal risk."""
        query = """
            SELECT 
                l.id, l.tenant_name, l.expiry_date, l.property_id,
                p.name as property_name,
                COUNT(t.id) as complaint_count,
                AVG(t.response_time) as avg_response_time,
                COUNT(CASE WHEN t.reopened THEN 1 END) as reopen_count
            FROM leases l
            JOIN properties p ON p.id = l.property_id
            LEFT JOIN tickets t ON t.property_id = p.id 
                AND t.created_at >= NOW() - INTERVAL '6 months'
            WHERE l.org_id = $1
            AND l.expiry_date <= NOW() + INTERVAL '$2 months'
            AND l.status = 'active'
            GROUP BY l.id, p.name
        """
        results = await self.db.fetch(query, org_id, months_ahead)
        
        leases = []
        for r in results:
            risk_score = self._calculate_renewal_risk(r)
            leases.append({
                "lease_id": r['id'],
                "tenant": r['tenant_name'],
                "property": r['property_name'],
                "expiry": r['expiry_date'],
                "risk_score": risk_score,
                "risk_level": "High" if risk_score > 70 else "Medium" if risk_score > 40 else "Low"
            })
        
        return {"leases": leases, "high_risk_count": sum(1 for l in leases if l['risk_level'] == "High")}
    
    # F19: Market Rate Benchmarking for New Bids
    async def get_bid_benchmark(self, sqft: int, property_type: str, city: str) -> Dict:
        """Get market rate for FM bid preparation."""
        market = await self.perplexity.query(
            f"{city} {property_type} FM contract rate per sqft 2025"
        )
        
        # Internal win/loss history
        history = await self._get_bid_history(property_type, city)
        
        return {
            "market_rate_range": market.get('rate_range', '₹45-65/sqft'),
            "recommended_bid_range": self._calculate_bid_range(market, history),
            "comparable_contracts": market.get('comparables', []),
            "win_rate": history.get('win_rate', 0.4)
        }
    
    # F20: Portfolio Health Scorecard
    async def generate_portfolio_health(self, quarter: str, org_id: str) -> Dict:
        """Generate quarterly portfolio health report."""
        # Aggregate 5 KPIs per property
        kpis = await self._calculate_portfolio_kpis(org_id, quarter)
        
        # Score and rank
        scored = self._score_properties(kpis)
        
        # LLM narrative
        narrative = await self._generate_narrative(scored)
        
        return {
            "quarter": quarter,
            "properties": scored,
            "portfolio_aggregate": self._aggregate_scores(scored),
            "top_performers": scored[:3],
            "bottom_performers": scored[-3:],
            "yoy_trend": await self._get_yoy_trend(org_id),
            "narrative_summary": narrative
        }
    
    # Helper methods
    def _calculate_renewal_risk(self, data: Dict) -> float:
        """Calculate renewal risk score (0-100)."""
        score = 0
        if data['complaint_count'] > 5:
            score += 30
        if data['avg_response_time'] and data['avg_response_time'] > 48:
            score += 25
        if data['reopen_count'] > 2:
            score += 20
        return min(score, 100)
    
    async def _estimate_opex(self, property_info: Dict) -> Dict:
        """Estimate OPEX for property."""
        return {"estimated_annual": 2500000, "per_sqft": 125}
    
    async def _assess_maintenance_risk(self, property_info: Dict) -> Dict:
        """Assess deferred maintenance risk."""
        return {"risk_level": "Medium", "estimated_backlog": 500000}
    
    async def _get_comparables(self, property_info: Dict, org_id: str) -> List:
        """Get comparable properties."""
        return []
    
    async def _get_regulatory_context(self, property_info: Dict) -> Dict:
        """Get regulatory context."""
        return {}
    
    async def _synthesize_recommendation(self, *args) -> Dict:
        """Synthesize feasibility recommendation."""
        return {"recommendation": "Conditional Go", "confidence": 0.75}
    
    async def _get_portfolio_opex(self, property_type: str, org_id: str) -> Dict:
        """Get portfolio OPEX."""
        return {"per_sqft": 110, "above_market": []}
    
    async def _get_bid_history(self, property_type: str, city: str) -> Dict:
        """Get bid history."""
        return {"win_rate": 0.45}
    
    def _calculate_bid_range(self, market: Dict, history: Dict) -> str:
        """Calculate recommended bid range."""
        return "₹50-60/sqft"
    
    async def _calculate_portfolio_kpis(self, org_id: str, quarter: str) -> List:
        """Calculate portfolio KPIs."""
        return []
    
    def _score_properties(self, kpis: List) -> List:
        """Score and rank properties."""
        return []
    
    async def _generate_narrative(self, scored: List) -> str:
        """Generate narrative summary."""
        return "Portfolio performance summary"
    
    def _aggregate_scores(self, scored: List) -> Dict:
        """Aggregate portfolio scores."""
        return {}
    
    async def _get_yoy_trend(self, org_id: str) -> Dict:
        """Get year-over-year trend."""
        return {}