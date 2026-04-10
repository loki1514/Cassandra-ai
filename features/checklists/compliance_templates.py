"""
F09: Regulatory Compliance Checklist Templates
Generate jurisdiction-compliant checklists using Perplexity for latest regulations.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class RegulatoryRequirement:
    """Regulatory requirement from Perplexity."""
    requirement_text: str
    frequency: str
    source: str
    last_verified: str


class ComplianceTemplateGenerator:
    """
    F09: Regulatory Compliance Checklist Templates
    
    Trigger: "Generate the monthly fire safety compliance checklist for Tower A"
    
    Flow: Template library → Perplexity verification → Diff → Generate checklist
    """
    
    # Pre-built template library
    TEMPLATE_LIBRARY = {
        "fire_safety": {
            "name": "Fire Safety Compliance",
            "base_items": [
                "Fire extinguisher inspection",
                "Smoke detector testing",
                "Emergency lighting verification",
                "Fire exit signage check",
                "Sprinkler system inspection"
            ],
            "regulatory_keywords": ["fire safety", "NFPA", "local fire code"]
        },
        "hvac_maintenance": {
            "name": "HVAC Maintenance",
            "base_items": [
                "Filter replacement",
                "Duct inspection",
                "Thermostat calibration",
                "Condenser cleaning",
                "Refrigerant level check"
            ],
            "regulatory_keywords": ["HVAC", "ASHRAE", "air quality"]
        },
        "electrical_safety": {
            "name": "Electrical Safety",
            "base_items": [
                "Panel inspection",
                "Ground fault testing",
                "Circuit breaker testing",
                "Emergency power verification"
            ],
            "regulatory_keywords": ["electrical", "NEC", "safety code"]
        }
    }
    
    def __init__(self, perplexity_client, db_client, memory_manager):
        self.perplexity = perplexity_client
        self.db = db_client
        self.memory_manager = memory_manager
        
    async def generate_compliance_checklist(self, template_type: str, property_id: str,
                                           jurisdiction: str, org_id: str) -> Dict[str, Any]:
        """
        Generate compliance checklist with regulatory verification.
        
        Args:
            template_type: Type of compliance (fire_safety, hvac_maintenance, etc.)
            property_id: Property to assign checklist to
            jurisdiction: City/state for regulatory context
            org_id: Organization ID
            
        Returns:
            Generated checklist with regulatory sources
        """
        # Step 1: Get base template
        base_template = self.TEMPLATE_LIBRARY.get(template_type)
        
        if not base_template:
            return {
                "success": False,
                "error": f"Unknown template type: {template_type}",
                "available_types": list(self.TEMPLATE_LIBRARY.keys())
            }
        
        # Step 2: Query Perplexity for latest regulations
        regulatory_info = await self._fetch_regulatory_requirements(
            template_type, jurisdiction
        )
        
        # Step 3: Diff template vs regulations
        updated_items, flagged_items = self._diff_with_regulations(
            base_template['base_items'],
            regulatory_info
        )
        
        # Step 4: Create checklist in DB
        checklist = await self._create_checklist(
            name=f"{base_template['name']} - {jurisdiction}",
            property_id=property_id,
            items=updated_items,
            regulatory_sources=regulatory_info,
            org_id=org_id
        )
        
        # Step 5: Log to Supermemory
        await self._log_template_generation(checklist, regulatory_info, org_id)
        
        return {
            "success": True,
            "checklist_id": checklist['id'],
            "checklist_name": checklist['name'],
            "items_count": len(updated_items),
            "items": updated_items,
            "regulatory_sources": [r.source for r in regulatory_info],
            "flagged_items": flagged_items,
            "last_verified": regulatory_info[0].last_verified if regulatory_info else None
        }
    
    async def _fetch_regulatory_requirements(self, template_type: str, 
                                            jurisdiction: str) -> List[RegulatoryRequirement]:
        """Fetch latest regulatory requirements from Perplexity."""
        base_template = self.TEMPLATE_LIBRARY.get(template_type, {})
        keywords = base_template.get('regulatory_keywords', [template_type])
        
        query = f"""
        What are the current regulatory requirements for {', '.join(keywords)} 
        in {jurisdiction}? Include inspection frequencies and mandatory checks.
        """
        
        # Call Perplexity API
        perplexity_result = await self.perplexity.query(
            query=query,
            search_recency_filter="month"
        )
        
        # Parse response into structured requirements
        requirements = self._parse_regulatory_response(perplexity_result)
        
        return requirements
    
    def _parse_regulatory_response(self, response: Dict) -> List[RegulatoryRequirement]:
        """Parse Perplexity response into structured requirements."""
        requirements = []
        
        # Extract citations
        citations = response.get('citations', [])
        
        # Parse content for requirements
        content = response.get('content', '')
        
        # Simple parsing - in production would use LLM extraction
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            if line and len(line) > 20:
                requirements.append(RegulatoryRequirement(
                    requirement_text=line,
                    frequency="monthly",  # Default
                    source=citations[0] if citations else "Regulatory source",
                    last_verified=response.get('created_at', 'unknown')
                ))
        
        return requirements
    
    def _diff_with_regulations(self, base_items: List[str], 
                              regulations: List[RegulatoryRequirement]) -> tuple:
        """Diff base template against regulations and flag discrepancies."""
        updated_items = list(base_items)  # Copy base items
        flagged_items = []
        
        for reg in regulations:
            reg_text = reg.requirement_text.lower()
            
            # Check if requirement is covered
            covered = any(
                self._requirement_covered(reg_text, item.lower())
                for item in base_items
            )
            
            if not covered:
                # Flag as potentially missing
                flagged_items.append({
                    "requirement": reg.requirement_text,
                    "source": reg.source,
                    "suggested_action": "Review and potentially add to checklist"
                })
        
        return updated_items, flagged_items
    
    def _requirement_covered(self, requirement: str, item: str) -> bool:
        """Check if a requirement is covered by a checklist item."""
        # Simple keyword matching - in production would use semantic similarity
        req_words = set(requirement.split())
        item_words = set(item.split())
        
        overlap = req_words & item_words
        return len(overlap) >= 2  # At least 2 words match
    
    async def _create_checklist(self, name: str, property_id: str, items: List[str],
                               regulatory_sources: List[RegulatoryRequirement],
                               org_id: str) -> Dict[str, Any]:
        """Create checklist in database."""
        # Insert checklist
        checklist_query = """
            INSERT INTO checklists (name, property_id, org_id, template_type, status)
            VALUES ($1, $2, $3, $4, 'active')
            RETURNING id, name
        """
        checklist_result = await self.db.fetchrow(
            checklist_query, name, property_id, org_id, 'compliance'
        )
        
        checklist_id = checklist_result['id']
        
        # Insert items
        for i, item_text in enumerate(items, 1):
            item_query = """
                INSERT INTO checklist_items (checklist_id, name, position, required)
                VALUES ($1, $2, $3, true)
            """
            await self.db.execute(item_query, checklist_id, item_text, i)
        
        # Store regulatory sources
        for source in regulatory_sources:
            source_query = """
                INSERT INTO checklist_regulatory_sources 
                (checklist_id, source_url, requirement_text, last_verified)
                VALUES ($1, $2, $3, $4)
            """
            await self.db.execute(
                source_query, 
                checklist_id, 
                source.source,
                source.requirement_text,
                source.last_verified
            )
        
        return {
            'id': checklist_id,
            'name': checklist_result['name']
        }
    
    async def _log_template_generation(self, checklist: Dict, 
                                      regulations: List[RegulatoryRequirement],
                                      org_id: str):
        """Log template generation to Supermemory."""
        await self.memory_manager.add_memory(
            content=f"Compliance checklist '{checklist['name']}' generated with {len(regulations)} regulatory sources",
            memory_type="CHECKLIST_TEMPLATE_GENERATED",
            org_id=org_id,
            entity_id=checklist['id'],
            metadata={
                "checklist_id": checklist['id'],
                "regulatory_sources": [r.source for r in regulations],
                "last_verified": regulations[0].last_verified if regulations else None
            },
            confidence=1.0
        )