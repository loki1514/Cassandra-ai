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
from datetime import datetime, timedelta

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult
from cassandra.supabase import get_supabase_client


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
        # Dual-read context from Supabase + Supermemory
        context = await fetch_full_context(
            query=f"property feasibility {property_info.get('name', '')}",
            org_id=org_id,
            data_hints=["properties", "tenants", "contracts", "budgets"],
            top_k=10,
        )

        # OPEX estimate (uses budget table via Supabase)
        opex = await self._estimate_opex(property_info, org_id)

        # Deferred maintenance risk
        maintenance_risk = await self._assess_maintenance_risk(property_info)

        # Portfolio comparables
        comparables = await self._get_comparables(property_info, org_id, context)

        # Regulatory context
        regulatory = await self._get_regulatory_context(property_info)

        # LLM synthesis — pass both context sources
        recommendation = await self._synthesize_recommendation(
            opex, maintenance_risk, comparables, regulatory,
            context.supabase_rows, context.memory_chunks
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
        # Dual-read context
        context = await fetch_full_context(
            query=f"OPEX benchmark {property_type} {city}",
            org_id=org_id,
            data_hints=["properties", "budgets"],
            top_k=5,
        )

        # Market benchmark from Perplexity
        market = await self.perplexity.query(
            f"{city} {property_type} facility management cost per sqft 2025"
        )

        # Portfolio actual from Supabase
        portfolio = await self._get_portfolio_opex(property_type, org_id, context)

        # Gap analysis
        market_per_sqft = market.get("per_sqft", 0)
        delta = portfolio["per_sqft"] - market_per_sqft
        delta_percent = (delta / market_per_sqft * 100) if market_per_sqft > 0 else 0

        return {
            "portfolio_opex_per_sqft": portfolio["per_sqft"],
            "market_benchmark": market_per_sqft,
            "delta": delta,
            "delta_percent": delta_percent,
            "properties_above_market": portfolio.get("above_market", []),
            "supabase_rows": context.supabase_rows,
            "memory_chunks": context.memory_chunks,
            "sources": market.get("citations", []),
        }
    
    # F18: Lease Expiry & Renewal Intelligence
    async def analyze_lease_renewals(self, months_ahead: int, org_id: str) -> Dict:
        """Cross-reference lease data with performance metrics for renewal risk."""
        # Dual-read context for verbal/notes about lease renewals
        context = await fetch_full_context(
            query=f"lease renewal risk contracts {months_ahead} months",
            org_id=org_id,
            data_hints=["properties", "tenants", "contracts", "budgets"],
            top_k=5,
        )

        # Supabase: query contracts table for expiring leases
        client = get_supabase_client("service")
        cutoff = (datetime.now() + timedelta(days=months_ahead * 30)).isoformat()

        contract_rows = (
            client.table("contracts")
            .select(
                "id, tenant_name, expiry_date, property_id, status, monthly_rent, sqft"
            )
            .eq("org_id", org_id)
            .eq("status", "active")
            .lte("expiry_date", cutoff)
            .execute()
        )
        rows = contract_rows.data if contract_rows.data else []

        # Fetch property names for these contract rows
        property_ids = list({r.get("property_id") for r in rows if r.get("property_id")})
        property_map = {}
        if property_ids:
            prop_result = (
                client.table("properties")
                .select("id, name")
                .in_("id", property_ids)
                .eq("org_id", org_id)
                .execute()
            )
            property_map = {p["id"]: p["name"] for p in (prop_result.data or [])}

        # Fetch tickets for these properties (last 6 months)
        ticket_rows = []
        if property_ids:
            ticket_cutoff = (datetime.now() - timedelta(days=180)).isoformat()
            ticket_result = (
                client.table("tickets")
                .select("property_id, status, response_time, reopened")
                .eq("org_id", org_id)
                .in_("property_id", property_ids)
                .gte("created_at", ticket_cutoff)
                .execute()
            )
            ticket_rows = ticket_result.data if ticket_result.data else []

        leases = []
        for r in rows:
            prop_id = r.get("property_id", "")
            prop_tickets = [t for t in ticket_rows if t.get("property_id") == prop_id]
            complaint_count = len(prop_tickets)
            avg_response_time = (
                sum(t.get("response_time") or 0 for t in prop_tickets)
                / len(prop_tickets)
                if prop_tickets
                else 0
            )
            reopen_count = sum(1 for t in prop_tickets if t.get("reopened"))

            risk_data = {
                "complaint_count": complaint_count,
                "avg_response_time": avg_response_time,
                "reopen_count": reopen_count,
            }
            risk_score = self._calculate_renewal_risk(risk_data)
            leases.append({
                "lease_id": r.get("id"),
                "tenant": r.get("tenant_name"),
                "property": property_map.get(prop_id, prop_id),
                "expiry": r.get("expiry_date"),
                "risk_score": risk_score,
                "risk_level": "High" if risk_score > 70 else "Medium" if risk_score > 40 else "Low",
                "supabase_row": r,
            })

        return {
            "leases": leases,
            "high_risk_count": sum(1 for l in leases if l["risk_level"] == "High"),
            "supabase_rows": rows,
            "memory_chunks": context.memory_chunks,
        }
    
    # F19: Market Rate Benchmarking for New Bids
    async def get_bid_benchmark(
        self, sqft: int, property_type: str, city: str, org_id: str
    ) -> Dict:
        """Get market rate for FM bid preparation using dual-read."""
        # Dual-read context for bid intelligence
        context = await fetch_full_context(
            query=f"FM bid rates {property_type} {city}",
            org_id=org_id,
            data_hints=["properties", "budgets"],
            top_k=5,
        )

        # Market rate from Perplexity
        market = await self.perplexity.query(
            f"{city} {property_type} FM contract rate per sqft 2025"
        )

        # Internal win/loss history from Supabase + memory
        history = await self._get_bid_history(property_type, city, org_id, context)

        return {
            "market_rate_range": market.get("rate_range", "₹45-65/sqft"),
            "recommended_bid_range": self._calculate_bid_range(market, history),
            "comparable_contracts": market.get("comparables", []),
            "win_rate": history.get("win_rate", 0.4),
            "supabase_rows": context.supabase_rows,
            "memory_chunks": context.memory_chunks,
        }
    
    # F20: Portfolio Health Scorecard
    async def generate_portfolio_health(self, quarter: str, org_id: str) -> Dict:
        """Generate quarterly portfolio health report."""
        # Dual-read context for portfolio health
        context = await fetch_full_context(
            query=f"portfolio health scorecard {quarter}",
            org_id=org_id,
            data_hints=["properties", "tenants", "contracts", "budgets"],
            top_k=10,
        )

        # Aggregate 5 KPIs per property
        kpis = await self._calculate_portfolio_kpis(org_id, quarter, context)

        # Score and rank
        scored = self._score_properties(kpis)

        # LLM narrative — pass dual-read context
        narrative = await self._generate_narrative(scored, context)

        return {
            "quarter": quarter,
            "properties": scored,
            "portfolio_aggregate": self._aggregate_scores(scored),
            "top_performers": scored[:3],
            "bottom_performers": scored[-3:],
            "yoy_trend": await self._get_yoy_trend(org_id, context),
            "narrative_summary": narrative,
            "supabase_rows": context.supabase_rows,
            "memory_chunks": context.memory_chunks,
        }
    
    # Helper methods
    def _calculate_renewal_risk(self, data: Dict) -> float:
        """Calculate renewal risk score (0-100)."""
        score = 0
        complaint_count = data.get("complaint_count", 0) or 0
        avg_response_time = data.get("avg_response_time") or 0
        reopen_count = data.get("reopen_count", 0) or 0
        if complaint_count > 5:
            score += 30
        if avg_response_time > 48:
            score += 25
        if reopen_count > 2:
            score += 20
        return min(score, 100)
    
    async def _estimate_opex(self, property_info: Dict, org_id: str) -> Dict:
        """Estimate OPEX for property using Supabase budgets table."""
        client = get_supabase_client("service")
        sqft = property_info.get("sqft", 0)

        # Pull budget rows for this org to compute average OPEX per sqft
        result = (
            client.table("budgets")
            .select("annual_opex, sqft, property_type")
            .eq("org_id", org_id)
            .execute()
        )
        rows = result.data if result.data else []

        if rows:
            # Weighted average from org's own budget data
            total_opex = sum(r.get("annual_opex") or 0 for r in rows)
            total_sqft = sum(r.get("sqft") or 0 for r in rows)
            per_sqft = (total_opex / total_sqft) if total_sqft > 0 else 125
            estimated_annual = per_sqft * sqft if sqft > 0 else total_opex / len(rows)
        else:
            per_sqft = 125
            estimated_annual = per_sqft * sqft if sqft > 0 else 2_500_000

        return {"estimated_annual": estimated_annual, "per_sqft": per_sqft}
    
    async def _assess_maintenance_risk(self, property_info: Dict) -> Dict:
        """Assess deferred maintenance risk based on property age and condition."""
        # Calculate risk based on property age
        age = property_info.get("age_years", 10)
        sqft = property_info.get("sqft", 50000)
        condition = property_info.get("condition", "average").lower()

        # Base risk assessment
        if age > 25:
            risk_level = "High"
            backlog_per_sqft = 50
        elif age > 15:
            risk_level = "Medium"
            backlog_per_sqft = 25
        else:
            risk_level = "Low"
            backlog_per_sqft = 10

        # Adjust based on condition
        condition_multipliers = {
            "poor": 2.0,
            "below average": 1.5,
            "average": 1.0,
            "good": 0.7,
            "excellent": 0.4
        }
        multiplier = condition_multipliers.get(condition, 1.0)

        estimated_backlog = int(sqft * backlog_per_sqft * multiplier)

        # Categorize specific risk areas
        risk_areas = []
        if age > 20:
            risk_areas.append("HVAC systems")
        if age > 15:
            risk_areas.append("Elevators")
            risk_areas.append("Electrical systems")
        if condition in ["poor", "below average"]:
            risk_areas.append("Plumbing")
            risk_areas.append("Roofing")

        return {
            "risk_level": risk_level,
            "estimated_backlog": estimated_backlog,
            "backlog_per_sqft": int(backlog_per_sqft * multiplier),
            "risk_areas": risk_areas,
            "property_age": age,
            "assessment_date": datetime.now().isoformat()
        }
    
    async def _get_comparables(
        self, property_info: Dict, org_id: str, context: ContextResult
    ) -> List:
        """Get comparable properties from Supabase + memory chunks."""
        client = get_supabase_client("service")
        property_type = property_info.get("type", property_info.get("property_type", ""))

        # Supabase: pull properties of same type from org
        result = (
            client.table("properties")
            .select("id, name, sqft, type, city, monthly_revenue")
            .eq("org_id", org_id)
            .eq("type", property_type)
            .limit(10)
            .execute()
        )
        supabase_comparables = result.data if result.data else []

        # Memory: pull any verbal/discussion notes about comparable properties
        memory_comparables = [
            {"content": chunk.get("content", ""), "source": chunk.get("source", "")}
            for chunk in context.memory_chunks
            if "property" in chunk.get("source", "").lower()
            or "comparable" in chunk.get("content", "").lower()
        ]

        return {
            "supabase_rows": supabase_comparables,
            "memory_chunks": memory_comparables,
        }
    
    async def _get_regulatory_context(self, property_info: Dict) -> Dict:
        """Get regulatory context via Perplexity for property location."""
        city = property_info.get("city", "")
        state = property_info.get("state", "")
        property_type = property_info.get("type", property_info.get("property_type", "commercial"))

        if not city:
            return {"flags": [], "summary": "No location provided for regulatory check"}

        try:
            # Query Perplexity for regulatory requirements
            query = f"{city} {state} {property_type} building regulations fire safety compliance 2025"
            response = await self.perplexity.query(query)

            content = response.get("content", "")
            citations = response.get("citations", [])

            # Extract key regulatory flags (simple parsing)
            flags = []
            if "fire safety" in content.lower() or "fire code" in content.lower():
                flags.append("Fire Safety Compliance Required")
            if "seismic" in content.lower() or "earthquake" in content.lower():
                flags.append("Seismic Standards")
            if "accessibility" in content.lower() or "ada" in content.lower():
                flags.append("Accessibility (ADA) Requirements")
            if "environmental" in content.lower() or "epa" in content.lower():
                flags.append("Environmental Compliance")
            if "occupancy" in content.lower():
                flags.append("Occupancy Permits")

            return {
                "flags": flags,
                "summary": content[:500] if content else "No regulatory information found",
                "sources": citations,
                "location": f"{city}, {state}",
                "checked_at": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "flags": ["Unable to fetch regulatory data"],
                "summary": f"Error fetching regulatory context: {str(e)}",
                "location": f"{city}, {state}",
                "checked_at": datetime.now().isoformat()
            }
    
    async def _synthesize_recommendation(
        self,
        opex: Dict,
        maintenance_risk: Dict,
        comparables: Any,
        regulatory: Dict,
        supabase_rows: List[Dict],
        memory_chunks: List[Dict],
    ) -> Dict:
        """Synthesize feasibility recommendation using dual-read context."""
        # Build context string from dual-read sources
        context_parts = []
        if supabase_rows:
            context_parts.append(
                f"Comparable properties from DB: {supabase_rows[:5]}"
            )
        if memory_chunks:
            context_parts.append(
                "Relevant discussions/memory:\n"
                + "\n".join(f"- {c.get('content', '')}" for c in memory_chunks[:3])
            )

        full_context = "\n".join(context_parts) if context_parts else "No additional context."

        # LLM synthesis prompt
        prompt = (
            f"Based on OPEX estimate {opex}, maintenance risk {maintenance_risk}, "
            f"and the following dual-read context:\n{full_context}\n\n"
            f"Provide a feasibility recommendation (Go / Conditional Go / No-Go) "
            f"with confidence score 0-1."
        )

        try:
            response = await self.perplexity.query(prompt)
            content = response.get("content", "")
            # Parse a simple confidence if embedded in response
            confidence = 0.75
            if "confidence:" in content.lower():
                try:
                    confidence = float(
                        content.lower().split("confidence:")[1].split()[0]
                    )
                except (IndexError, ValueError):
                    pass
            recommendation_text = (
                content.split("\n")[0]
                if content
                else "Conditional Go"
            )
        except Exception:
            recommendation_text = "Conditional Go"
            confidence = 0.75

        return {"recommendation": recommendation_text, "confidence": confidence}
    
    async def _get_portfolio_opex(
        self, property_type: str, org_id: str, context: ContextResult
    ) -> Dict:
        """Get portfolio OPEX from Supabase budgets table with dual-read context."""
        client = get_supabase_client("service")

        result = (
            client.table("budgets")
            .select("property_id, annual_opex, sqft, property_type")
            .eq("org_id", org_id)
            .eq("property_type", property_type)
            .execute()
        )
        rows = result.data if result.data else []

        # Also check memory chunks for any verbal OPEX data
        memory_opex = [
            c.get("content", "")
            for c in context.memory_chunks
            if "opex" in c.get("content", "").lower()
            or "budget" in c.get("content", "").lower()
        ]

        if rows:
            total_opex = sum(r.get("annual_opex") or 0 for r in rows)
            total_sqft = sum(r.get("sqft") or 0 for r in rows)
            per_sqft = (total_opex / total_sqft) if total_sqft > 0 else 0
            above_market = [r.get("property_id") for r in rows if per_sqft > 0]
        else:
            per_sqft = 110
            above_market = []
            total_opex = 0

        return {
            "per_sqft": per_sqft,
            "above_market": above_market,
            "total_annual_opex": total_opex,
            "memory_opex_notes": memory_opex,
        }
    
    async def _get_bid_history(
        self,
        property_type: str,
        city: str,
        org_id: str,
        context: ContextResult,
    ) -> Dict:
        """Get bid history from Supabase bids table + memory chunks."""
        client = get_supabase_client("service")

        result = (
            client.table("bids")
            .select("id, property_type, city, bid_amount, status, won")
            .eq("org_id", org_id)
            .eq("property_type", property_type)
            .execute()
        )
        rows = result.data if result.data else []

        # Memory: pull any verbal bid intelligence
        memory_bids = [
            {"content": c.get("content", ""), "source": c.get("source", "")}
            for c in context.memory_chunks
            if "bid" in c.get("content", "").lower()
            or "win_rate" in c.get("content", "").lower()
        ]

        total = len(rows)
        won = sum(1 for r in rows if r.get("won", False))
        win_rate = won / total if total > 0 else 0.45

        return {
            "win_rate": win_rate,
            "total_bids": total,
            "bids_won": won,
            "memory_bid_notes": memory_bids,
        }
    
    def _calculate_bid_range(self, market: Dict, history: Dict) -> str:
        """Calculate recommended bid range based on market data and win rate."""
        # Extract market rate range
        market_range_str = market.get("rate_range", "₹45-65/sqft")

        # Parse market range
        try:
            # Extract numbers from string like "₹45-65/sqft" or "$45-65/sqft"
            import re
            numbers = re.findall(r'\d+', market_range_str)
            if len(numbers) >= 2:
                market_low = int(numbers[0])
                market_high = int(numbers[1])
            else:
                market_low = 45
                market_high = 65
        except:
            market_low = 45
            market_high = 65

        # Adjust based on win rate history
        win_rate = history.get("win_rate", 0.45)

        # If win rate is low, recommend lower bid to be more competitive
        if win_rate < 0.3:
            # Bid 5-10% below market average
            adjustment_factor = 0.92
        elif win_rate < 0.5:
            # Bid at lower end of market
            adjustment_factor = 0.95
        elif win_rate > 0.7:
            # Winning too much, can afford to bid higher
            adjustment_factor = 1.05
        else:
            # Sweet spot - bid at market
            adjustment_factor = 1.0

        recommended_low = int(market_low * adjustment_factor)
        recommended_high = int(market_high * adjustment_factor)

        # Determine currency symbol
        currency = "₹" if "₹" in market_range_str else "$"

        return f"{currency}{recommended_low}-{recommended_high}/sqft"
    
    async def _calculate_portfolio_kpis(
        self, org_id: str, quarter: str, context: ContextResult
    ) -> List:
        """Calculate portfolio KPIs from Supabase + dual-read context."""
        client = get_supabase_client("service")

        # Fetch all properties for this org
        prop_result = (
            client.table("properties")
            .select("id, name, sqft, type, monthly_revenue, occupancy_rate")
            .eq("org_id", org_id)
            .execute()
        )
        properties = prop_result.data if prop_result.data else []

        # Fetch tickets per property (last 90 days) for maintenance KPI
        ticket_cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        ticket_result = (
            client.table("tickets")
            .select("property_id, status, priority, response_time")
            .eq("org_id", org_id)
            .gte("created_at", ticket_cutoff)
            .execute()
        )
        tickets = ticket_result.data if ticket_result.data else []

        kpis = []
        for prop in properties:
            prop_tickets = [t for t in tickets if t.get("property_id") == prop.get("id")]
            open_tickets = sum(1 for t in prop_tickets if t.get("status") in ("open", "pending"))
            avg_response = (
                sum(t.get("response_time") or 0 for t in prop_tickets)
                / len(prop_tickets)
                if prop_tickets
                else 0
            )
            kpis.append({
                "property_id": prop.get("id"),
                "name": prop.get("name"),
                "sqft": prop.get("sqft") or 0,
                "occupancy": prop.get("occupancy_rate") or 0,
                "revenue": prop.get("monthly_revenue") or 0,
                "open_tickets": open_tickets,
                "avg_response_time": avg_response,
            })

        return kpis
    
    def _score_properties(self, kpis: List) -> List:
        """Score and rank properties based on KPIs."""
        if not kpis:
            return []

        scored = []
        for prop in kpis:
            score = 100  # Start with perfect score

            # Occupancy penalty (0 is worst, 100 is best)
            occupancy = prop.get("occupancy", 0)
            if occupancy < 70:
                score -= 30
            elif occupancy < 85:
                score -= 15

            # Open tickets penalty (more open tickets = worse)
            open_tickets = prop.get("open_tickets", 0)
            sqft = prop.get("sqft", 1) or 1
            tickets_per_sqft = (open_tickets / sqft) * 10000  # Normalize to per 10k sqft
            if tickets_per_sqft > 5:
                score -= 25
            elif tickets_per_sqft > 2:
                score -= 10

            # Response time penalty (higher = worse)
            avg_response = prop.get("avg_response_time", 0)
            if avg_response > 48:  # > 48 hours
                score -= 20
            elif avg_response > 24:  # > 24 hours
                score -= 10

            # Revenue per sqft bonus (higher = better)
            revenue = prop.get("revenue", 0) or 0
            revenue_per_sqft = (revenue / sqft) if sqft > 0 else 0
            if revenue_per_sqft > 100:  # High revenue density
                score += 10
            elif revenue_per_sqft < 50:  # Low revenue density
                score -= 10

            # Ensure score is in valid range
            score = max(0, min(100, score))

            # Assign health rating
            if score >= 80:
                health = "Excellent"
            elif score >= 65:
                health = "Good"
            elif score >= 50:
                health = "Fair"
            else:
                health = "Needs Attention"

            scored.append({
                **prop,
                "health_score": score,
                "health_rating": health,
                "tickets_per_10k_sqft": round(tickets_per_sqft, 2),
                "revenue_per_sqft": round(revenue_per_sqft, 2)
            })

        # Sort by score descending (best first)
        scored.sort(key=lambda x: x["health_score"], reverse=True)
        return scored
    
    async def _generate_narrative(self, scored: List, context: ContextResult) -> str:
        """Generate narrative summary using dual-read context."""
        context_parts = []
        if context.supabase_rows:
            context_parts.append(
                f"DB properties analyzed: {len(context.supabase_rows)}"
            )
        if context.memory_chunks:
            context_parts.append(
                "Discussions:\n"
                + "\n".join(f"- {c.get('content', '')}" for c in context.memory_chunks[:3])
            )
        extra = "\n".join(context_parts) if context_parts else "No additional context."

        prompt = (
            f"Portfolio health narrative based on {len(scored)} properties.\n"
            f"Additional context:\n{extra}\n\n"
            f"Top performers: {[s.get('name') for s in scored[:3]]}\n"
            f"Bottom performers: {[s.get('name') for s in scored[-3:]]}"
        )
        try:
            result = await self.perplexity.query(prompt)
            return result.get("content", "Portfolio performance summary")
        except Exception:
            return "Portfolio performance summary"
    
    def _aggregate_scores(self, scored: List) -> Dict:
        """Aggregate portfolio scores and calculate portfolio-level metrics."""
        if not scored:
            return {
                "portfolio_health_score": 0,
                "properties_count": 0,
                "total_sqft": 0,
                "avg_occupancy": 0,
                "total_revenue": 0
            }

        total_score = sum(p.get("health_score", 0) for p in scored)
        portfolio_score = total_score / len(scored) if scored else 0

        total_sqft = sum(p.get("sqft", 0) or 0 for p in scored)
        total_revenue = sum(p.get("revenue", 0) or 0 for p in scored)
        total_occupancy = sum(p.get("occupancy", 0) or 0 for p in scored)
        avg_occupancy = total_occupancy / len(scored) if scored else 0

        total_open_tickets = sum(p.get("open_tickets", 0) for p in scored)
        avg_response_time = (
            sum(p.get("avg_response_time", 0) for p in scored) / len(scored)
            if scored
            else 0
        )

        # Distribution of health ratings
        health_distribution = {
            "excellent": sum(1 for p in scored if p.get("health_rating") == "Excellent"),
            "good": sum(1 for p in scored if p.get("health_rating") == "Good"),
            "fair": sum(1 for p in scored if p.get("health_rating") == "Fair"),
            "needs_attention": sum(1 for p in scored if p.get("health_rating") == "Needs Attention")
        }

        # Calculate portfolio health rating
        if portfolio_score >= 80:
            portfolio_rating = "Excellent"
        elif portfolio_score >= 65:
            portfolio_rating = "Good"
        elif portfolio_score >= 50:
            portfolio_rating = "Fair"
        else:
            portfolio_rating = "Needs Attention"

        return {
            "portfolio_health_score": round(portfolio_score, 1),
            "portfolio_health_rating": portfolio_rating,
            "properties_count": len(scored),
            "total_sqft": total_sqft,
            "total_revenue": total_revenue,
            "avg_occupancy": round(avg_occupancy, 1),
            "total_open_tickets": total_open_tickets,
            "avg_response_time_hours": round(avg_response_time, 1),
            "health_distribution": health_distribution,
            "properties_at_risk": health_distribution["needs_attention"]
        }
    
    async def _get_yoy_trend(self, org_id: str, context: ContextResult) -> Dict:
        """Get year-over-year trend from Supabase + memory."""
        client = get_supabase_client("service")
        this_year_cutoff = (datetime.now() - timedelta(days=365)).isoformat()

        result = (
            client.table("properties")
            .select("id, name, monthly_revenue, occupancy_rate")
            .eq("org_id", org_id)
            .execute()
        )
        rows = result.data if result.data else []

        # Memory trend notes
        memory_notes = [
            c.get("content", "") for c in context.memory_chunks if "trend" in c.get("content", "").lower()
        ]

        return {
            "properties_tracked": len(rows),
            "current_revenue": sum(r.get("monthly_revenue") or 0 for r in rows),
            "memory_trend_notes": memory_notes,
        }