"""
CAT-E: Chat Mode — Perplexity-Powered Research (F21-F25)
- F21: Perplexity-Backed Research Chat
- F22: Contractor & Vendor Discovery
- F23: Regulatory Q&A — Jurisdiction-Aware
- F24: Cost Negotiation Intelligence
- F25: Incident Response Knowledge Base
"""

from typing import Dict, Any, List, Optional


class PerplexityChatService:
    """Perplexity-powered chat and research features."""
    
    def __init__(self, perplexity_client, notion_client, memory_manager, db_client):
        self.perplexity = perplexity_client
        self.notion = notion_client
        self.memory = memory_manager
        self.db = db_client
    
    # F21: Perplexity-Backed Research Chat
    async def research_query(self, query: str, org_id: str, user_id: str) -> Dict:
        """Answer FM-specific questions using verified external sources."""
        # Query Perplexity with FM scope
        result = await self.perplexity.query(
            query=query,
            search_recency_filter="month"
        )
        
        # Calculate confidence
        confidence = self._calculate_confidence(result)
        
        # Log to memory
        await self.memory.add_memory(
            content=f"Research query: {query}\nAnswer: {result.get('content', '')[:200]}",
            memory_type="RESEARCH_QUERY",
            org_id=org_id,
            entity_id=user_id,
            metadata={"query": query, "confidence": confidence},
            confidence=confidence
        )
        
        return {
            "answer": result.get('content', ''),
            "sources": result.get('citations', []),
            "confidence": confidence,
            "save_to_notion": True
        }
    
    # F22: Contractor & Vendor Discovery
    async def find_contractors(self, trade: str, city: str, org_id: str) -> Dict:
        """Find certified contractors with references."""
        # Perplexity search
        result = await self.perplexity.query(
            f"certified {trade} contractors in {city} with reviews and ratings"
        )
        
        # Cross-reference with internal vendors
        internal = await self._get_internal_vendors(trade, city, org_id)
        
        # De-duplicate and rank
        contractors = self._rank_contractors(result, internal)
        
        return {
            "contractors": contractors[:5],
            "count": len(contractors)
        }
    
    # F23: Regulatory Q&A — Jurisdiction-Aware
    async def get_permit_requirements(self, project_type: str, property_id: str, 
                                      org_id: str) -> Dict:
        """Get permit requirements for jurisdiction."""
        # Get property location
        property_info = await self._get_property(property_id, org_id)
        city = property_info.get('city', '')
        state = property_info.get('state', '')
        
        # Query Perplexity
        result = await self.perplexity.query(
            f"{city} {state} {project_type} permit requirements 2025"
        )
        
        # Structure into checklist
        checklist = self._structure_permits(result)
        
        # Save checklist to DB
        checklist_id = await self._save_checklist(checklist, property_id, org_id)
        
        return {
            "project_type": project_type,
            "jurisdiction": f"{city}, {state}",
            "permits": checklist,
            "checklist_id": checklist_id,
            "sources": result.get('citations', [])
        }
    
    # F24: Cost Negotiation Intelligence
    async def validate_vendor_quote(self, service: str, quote_amount: float, 
                                    city: str, org_id: str) -> Dict:
        """Validate if vendor quote is fair."""
        # Market rate from Perplexity
        market = await self.perplexity.query(
            f"{service} cost per unit {city} 2025 market rate"
        )
        
        # Internal historical rates
        historical = await self._get_historical_rates(service, org_id)
        
        market_rate = market.get('rate', quote_amount)
        delta = ((quote_amount - market_rate) / market_rate) * 100
        
        return {
            "quote": quote_amount,
            "market_rate": market_rate,
            "delta_percent": delta,
            "assessment": "Fair" if abs(delta) < 10 else "High" if delta > 15 else "Low",
            "negotiation_range": f"₹{market_rate * 0.9:.0f}-₹{market_rate * 1.1:.0f}",
            "sources": market.get('citations', [])
        }
    
    # F25: Incident Response Knowledge Base
    async def get_emergency_protocol(self, incident_type: str, property_id: str,
                                     org_id: str) -> Dict:
        """Get immediate response protocol for emergency."""
        # Perplexity for protocol
        protocol = await self.perplexity.query(
            f"{incident_type} emergency response protocol facility management"
        )
        
        # Internal past incidents
        past = await self._get_past_incidents(incident_type, org_id)
        
        # Create emergency ticket
        ticket = await self._create_emergency_ticket(incident_type, property_id, org_id)
        
        return {
            "incident_type": incident_type,
            "protocol_steps": protocol.get('content', '').split('\n'),
            "past_similar_incidents": past,
            "emergency_ticket_id": ticket.get('ticket_id'),
            "relevant_contacts": await self._get_emergency_contacts(property_id, org_id)
        }
    
    # Helper methods
    def _calculate_confidence(self, result: Dict) -> float:
        """Calculate answer confidence."""
        citations = len(result.get('citations', []))
        return min(0.5 + (citations * 0.1), 0.95)
    
    async def _get_internal_vendors(self, trade: str, city: str, org_id: str) -> List:
        """Get internal vendor list."""
        return []
    
    def _rank_contractors(self, perplexity_result: Dict, internal: List) -> List:
        """Rank and de-duplicate contractors."""
        return []
    
    async def _get_property(self, property_id: str, org_id: str) -> Dict:
        """Get property details."""
        return {"city": "Pune", "state": "Maharashtra"}
    
    def _structure_permits(self, result: Dict) -> List:
        """Structure permit requirements into checklist."""
        return []
    
    async def _save_checklist(self, permits: List, property_id: str, org_id: str) -> str:
        """Save permit checklist to DB."""
        return "checklist-id"
    
    async def _get_historical_rates(self, service: str, org_id: str) -> Dict:
        """Get historical vendor rates."""
        return {}
    
    async def _get_past_incidents(self, incident_type: str, org_id: str) -> List:
        """Get past similar incidents."""
        return []
    
    async def _create_emergency_ticket(self, incident_type: str, property_id: str, 
                                      org_id: str) -> Dict:
        """Create emergency ticket."""
        return {"ticket_id": "EMERGENCY-001"}
    
    async def _get_emergency_contacts(self, property_id: str, org_id: str) -> List:
        """Get emergency contacts."""
        return []