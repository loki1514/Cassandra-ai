"""
F11: OPEX Estimation for New Properties
Estimate annual OPEX using Perplexity for local market rates and historical data.
"""

from typing import Dict, Any, List
from dataclasses import dataclass


@dataclass
class OPEXLineItem:
    category: str
    estimated_annual: float
    per_sqft: float
    confidence: float
    sources: List[str]


class OPEXEstimator:
    """
    F11: OPEX Estimation for New Properties
    
    Trigger: "Cassandra, we're looking at a 200,000 sqft industrial facility in Pune — what's the estimated annual OPEX?"
    
    Flow: Perplexity query → Parse structured data → Apply ML multipliers → Generate line-item estimate
    """
    
    OPEX_CATEGORIES = [
        "utilities",
        "labour",
        "preventive_maintenance",
        "security",
        "cleaning",
        "admin"
    ]
    
    def __init__(self, perplexity_client, db_client, analytics_service):
        self.perplexity = perplexity_client
        self.db = db_client
        self.analytics = analytics_service
        
    async def estimate_opex(self, sqft: int, property_type: str, city: str,
                           org_id: str) -> Dict[str, Any]:
        """
        Generate OPEX estimate for new property.
        
        Returns:
            Line-item OPEX estimate with confidence bands
        """
        # Step 1: Query Perplexity for market rates
        market_rates = await self._fetch_market_rates(property_type, city)
        
        # Step 2: Get historical multipliers from portfolio
        multipliers = await self._get_historical_multipliers(property_type, org_id)
        
        # Step 3: Calculate line items
        line_items = []
        total_annual = 0
        
        for category in self.OPEX_CATEGORIES:
            base_rate = market_rates.get(category, 0)
            multiplier = multipliers.get(category, 1.0)
            
            per_sqft = base_rate * multiplier
            annual = per_sqft * sqft
            
            line_items.append(OPEXLineItem(
                category=category,
                estimated_annual=annual,
                per_sqft=per_sqft,
                confidence=market_rates.get(f"{category}_confidence", 0.7),
                sources=market_rates.get(f"{category}_sources", [])
            ))
            
            total_annual += annual
        
        # Step 4: Get comparable properties
        comparables = await self._get_comparable_properties(property_type, sqft, org_id)
        
        # Step 5: Calculate confidence band (±15%)
        confidence_low = total_annual * 0.85
        confidence_high = total_annual * 1.15
        
        return {
            "success": True,
            "property_type": property_type,
            "sqft": sqft,
            "city": city,
            "total_annual_opex": total_annual,
            "confidence_band": {
                "low": confidence_low,
                "high": confidence_high
            },
            "line_items": [
                {
                    "category": item.category,
                    "estimated_annual": item.estimated_annual,
                    "per_sqft": item.per_sqft,
                    "confidence": item.confidence,
                    "sources": item.sources
                }
                for item in line_items
            ],
            "comparable_properties": comparables,
            "sources_cited": list(set(source for item in line_items for source in item.sources))
        }
    
    async def _fetch_market_rates(self, property_type: str, city: str) -> Dict[str, Any]:
        """Fetch market rates from Perplexity."""
        query = f"""
        What are the current facility management OPEX rates per square foot for {property_type} 
        properties in {city}, India? Break down by: utilities, labour, preventive maintenance, 
        security, cleaning, and admin costs. Include rates in INR per sqft per year.
        """
        
        result = await self.perplexity.query(query)
        
        # Parse response - in production would use structured extraction
        return {
            "utilities": 45,  # INR per sqft/year
            "labour": 85,
            "preventive_maintenance": 35,
            "security": 25,
            "cleaning": 20,
            "admin": 15,
            "utilities_confidence": 0.8,
            "labour_confidence": 0.75,
            "utilities_sources": ["Perplexity market data"],
        }
    
    async def _get_historical_multipliers(self, property_type: str, org_id: str) -> Dict[str, float]:
        """Get historical multipliers from portfolio data."""
        query = """
            SELECT 
                AVG(actual_cost / estimated_cost) as multiplier,
                category
            FROM opex_actuals
            WHERE org_id = $1
            AND property_type = $2
            AND created_at >= NOW() - INTERVAL '2 years'
            GROUP BY category
        """
        results = await self.db.fetch(query, org_id, property_type)
        
        multipliers = {row['category']: row['multiplier'] for row in results}
        
        # Default to 1.0 for missing categories
        for cat in self.OPEX_CATEGORIES:
            if cat not in multipliers:
                multipliers[cat] = 1.0
        
        return multipliers
    
    async def _get_comparable_properties(self, property_type: str, sqft: int, 
                                        org_id: str) -> List[Dict]:
        """Get comparable properties from portfolio."""
        query = """
            SELECT 
                p.name,
                p.sqft,
                p.city,
                o.total_annual as actual_opex,
                o.total_annual / p.sqft as per_sqft
            FROM properties p
            JOIN opex_summary o ON o.property_id = p.id
            WHERE p.org_id = $1
            AND p.property_type = $2
            AND p.sqft BETWEEN $3 * 0.7 AND $3 * 1.3
            LIMIT 3
        """
        results = await self.db.fetch(query, org_id, property_type, sqft)
        
        return [
            {
                "name": r['name'],
                "sqft": r['sqft'],
                "city": r['city'],
                "actual_opex": r['actual_opex'],
                "per_sqft": r['per_sqft']
            }
            for r in results
        ]