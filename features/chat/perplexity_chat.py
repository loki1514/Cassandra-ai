"""
CAT-E: Chat Mode — Perplexity-Powered Research (F21-F25)
- F21: Perplexity-Backed Research Chat
- F22: Contractor & Vendor Discovery
- F23: Regulatory Q&A — Jurisdiction-Aware
- F24: Cost Negotiation Intelligence
- F25: Incident Response Knowledge Base
"""

from typing import Dict, Any, List, Optional

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult
from cassandra.supabase import get_supabase_client


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
        # Dual-read context FIRST
        context = await fetch_full_context(
            query=query,
            org_id=org_id,
            data_hints=["tickets", "properties", "vendors"],
            top_k=5,
        )

        # Query Perplexity with FM scope
        result = await self.perplexity.query(
            query=query,
            search_recency_filter="month"
        )

        # Calculate confidence
        confidence = self._calculate_confidence(result)

        # Build enriched answer from both sources
        answer_parts = [result.get("content", "")]
        if context.supabase_rows:
            answer_parts.append(
                "\n\n--- Internal Data ---\n"
                + "\n".join(str(r) for r in context.supabase_rows[:3])
            )
        if context.memory_chunks:
            answer_parts.append(
                "\n\n--- Relevant Discussions ---\n"
                + "\n".join(f"- {c.get('content', '')}" for c in context.memory_chunks[:3])
            )
        enriched_answer = "\n".join(answer_parts)

        # Log to memory
        await self.memory.add_memory(
            content=f"Research query: {query}\nAnswer: {enriched_answer[:200]}",
            memory_type="RESEARCH_QUERY",
            org_id=org_id,
            entity_id=user_id,
            metadata={"query": query, "confidence": confidence},
            confidence=confidence
        )

        return {
            "answer": enriched_answer,
            "perplexity_content": result.get("content", ""),
            "supabase_rows": context.supabase_rows,
            "memory_chunks": context.memory_chunks,
            "sources": result.get("citations", []),
            "confidence": confidence,
            "save_to_notion": True,
        }
    
    # F22: Contractor & Vendor Discovery
    async def find_contractors(self, trade: str, city: str, org_id: str) -> Dict:
        """Find certified contractors with references."""
        # Dual-read context FIRST
        context = await fetch_full_context(
            query=f"{trade} contractor vendor {city}",
            org_id=org_id,
            data_hints=["tickets", "properties", "vendors"],
            top_k=5,
        )

        # Perplexity search
        result = await self.perplexity.query(
            f"certified {trade} contractors in {city} with reviews and ratings"
        )

        # Cross-reference with internal vendors (Supabase)
        internal = await self._get_internal_vendors(trade, city, org_id)

        # De-duplicate and rank using dual-read context
        contractors = await self._rank_contractors(result, internal, context)

        return {
            "contractors": contractors[:5],
            "count": len(contractors),
            "supabase_rows": context.supabase_rows,
            "memory_chunks": context.memory_chunks,
        }
    
    # F23: Regulatory Q&A — Jurisdiction-Aware
    async def get_permit_requirements(self, project_type: str, property_id: str,
                                      org_id: str) -> Dict:
        """Get permit requirements for jurisdiction."""
        # Dual-read context FIRST
        context = await fetch_full_context(
            query=f"{project_type} permit requirements",
            org_id=org_id,
            data_hints=["tickets", "properties", "vendors"],
            top_k=5,
        )

        # Get property location from Supabase
        property_info = await self._get_property(property_id, org_id)
        city = property_info.get("city", "")
        state = property_info.get("state", "")

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
            "supabase_rows": context.supabase_rows,
            "memory_chunks": context.memory_chunks,
            "sources": result.get("citations", []),
        }
    
    # F24: Cost Negotiation Intelligence
    async def validate_vendor_quote(self, service: str, quote_amount: float,
                                    city: str, org_id: str) -> Dict:
        """Validate if vendor quote is fair."""
        # Dual-read context FIRST
        context = await fetch_full_context(
            query=f"{service} vendor quote rates {city}",
            org_id=org_id,
            data_hints=["tickets", "properties", "vendors"],
            top_k=5,
        )

        # Market rate from Perplexity
        market = await self.perplexity.query(
            f"{service} cost per unit {city} 2025 market rate"
        )

        # Internal historical rates from Supabase
        historical = await self._get_historical_rates(service, org_id)

        market_rate = market.get("rate", quote_amount)
        delta = ((quote_amount - market_rate) / market_rate * 100) if market_rate > 0 else 0

        return {
            "quote": quote_amount,
            "market_rate": market_rate,
            "delta_percent": delta,
            "assessment": "Fair" if abs(delta) < 10 else "High" if delta > 15 else "Low",
            "negotiation_range": f"₹{market_rate * 0.9:.0f}-₹{market_rate * 1.1:.0f}",
            "historical_rates": historical,
            "supabase_rows": context.supabase_rows,
            "memory_chunks": context.memory_chunks,
            "sources": market.get("citations", []),
        }
    
    # F25: Incident Response Knowledge Base
    async def get_emergency_protocol(self, incident_type: str, property_id: str,
                                     org_id: str) -> Dict:
        """Get immediate response protocol for emergency."""
        # Dual-read context FIRST
        context = await fetch_full_context(
            query=f"{incident_type} emergency protocol incident",
            org_id=org_id,
            data_hints=["tickets", "properties", "vendors"],
            top_k=5,
        )

        # Perplexity for protocol
        protocol = await self.perplexity.query(
            f"{incident_type} emergency response protocol facility management"
        )

        # Internal past incidents from Supabase
        past = await self._get_past_incidents(incident_type, org_id)

        # Create emergency ticket
        ticket = await self._create_emergency_ticket(incident_type, property_id, org_id)

        return {
            "incident_type": incident_type,
            "protocol_steps": protocol.get("content", "").split("\n"),
            "past_similar_incidents": past,
            "emergency_ticket_id": ticket.get("ticket_id"),
            "relevant_contacts": await self._get_emergency_contacts(property_id, org_id),
            "supabase_rows": context.supabase_rows,
            "memory_chunks": context.memory_chunks,
        }
    
    # Helper methods
    def _calculate_confidence(self, result: Dict) -> float:
        """Calculate answer confidence."""
        citations = len(result.get('citations', []))
        return min(0.5 + (citations * 0.1), 0.95)
    
    async def _get_internal_vendors(self, trade: str, city: str, org_id: str) -> List:
        """Get internal vendor list from Supabase vendors table."""
        client = get_supabase_client("service")
        result = (
            client.table("vendors")
            .select("id, name, trade, city, rating, contact_email, phone")
            .eq("org_id", org_id)
            .eq("trade", trade)
            .eq("city", city)
            .execute()
        )
        return result.data if result.data else []
    
    async def _rank_contractors(
        self, perplexity_result: Dict, internal: List, context: ContextResult
    ) -> List:
        """Rank and de-duplicate contractors using dual-read context."""
        contractors = []
        seen_names = set()

        # Add internal vendors first (highest trust)
        for vendor in internal:
            name = vendor.get("name", "")
            if name and name not in seen_names:
                contractors.append({
                    "name": name,
                    "trade": vendor.get("trade", ""),
                    "city": vendor.get("city", ""),
                    "rating": vendor.get("rating", 0),
                    "source": "internal",
                    "contact_email": vendor.get("contact_email", ""),
                    "phone": vendor.get("phone", ""),
                })
                seen_names.add(name)

        # Add Perplexity results
        perplexity_list = perplexity_result.get("results", [])
        for item in perplexity_list:
            name = item.get("name", "")
            if name and name not in seen_names:
                contractors.append({
                    "name": name,
                    "trade": item.get("trade", ""),
                    "city": item.get("city", ""),
                    "rating": item.get("rating", 0),
                    "source": "perplexity",
                    "reviews": item.get("reviews", []),
                })
                seen_names.add(name)

        # Boost/penalize based on memory chunk mentions
        memory_names = {
            c.get("source", "") for c in context.memory_chunks if c.get("source")
        }
        for contractor in contractors:
            if contractor["name"] in memory_names:
                contractor["rating"] = min(
                    (contractor.get("rating") or 0) + 0.5, 5.0
                )

        # Sort by rating descending
        contractors.sort(key=lambda x: x.get("rating") or 0, reverse=True)
        return contractors
    
    async def _get_property(self, property_id: str, org_id: str) -> Dict:
        """Get property details from Supabase properties table."""
        client = get_supabase_client("service")
        result = (
            client.table("properties")
            .select("id, name, city, state, address, type, sqft, property_type")
            .eq("id", property_id)
            .eq("org_id", org_id)
            .execute()
        )
        if result.data:
            return result.data[0]
        return {"city": "Unknown", "state": "Unknown"}
    
    def _structure_permits(self, result: Dict) -> List:
        """Structure permit requirements into checklist items."""
        content = result.get("content", "")
        if not content:
            return []

        permits = []
        lines = content.split("\n")

        # Simple parsing: look for numbered lists or bullet points
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip headers
            if line.startswith("#") or line.isupper():
                continue

            # Extract permit items from common patterns
            permit_text = None
            if line.startswith(("-", "*", "•")):
                permit_text = line.lstrip("-*• ").strip()
            elif line and line[0].isdigit() and ("." in line[:3] or ")" in line[:3]):
                # Numbered list like "1." or "1)"
                permit_text = line.split(".", 1)[-1].split(")", 1)[-1].strip()

            if permit_text and len(permit_text) > 10:  # Valid permit description
                permits.append({
                    "requirement": permit_text,
                    "status": "pending",
                    "required_docs": self._extract_docs(permit_text),
                    "estimated_days": self._estimate_timeline(permit_text)
                })

        # If no structured items found, create generic checklist from content
        if not permits and content:
            # Create a simple checklist based on key permit keywords
            keywords = ["building permit", "fire", "electrical", "plumbing",
                       "mechanical", "occupancy", "zoning", "environmental"]
            for keyword in keywords:
                if keyword in content.lower():
                    permits.append({
                        "requirement": f"{keyword.title()} Permit/Approval",
                        "status": "pending",
                        "required_docs": [],
                        "estimated_days": 14
                    })

        return permits[:10]  # Limit to 10 items

    def _extract_docs(self, text: str) -> List[str]:
        """Extract required documents from permit text."""
        docs = []
        text_lower = text.lower()

        doc_keywords = {
            "plans": "Architectural Plans",
            "drawing": "Engineering Drawings",
            "blueprint": "Blueprints",
            "application": "Permit Application Form",
            "insurance": "Insurance Certificate",
            "license": "Contractor License",
            "inspection": "Inspection Report",
            "survey": "Site Survey",
            "certificate": "Compliance Certificate"
        }

        for keyword, doc_name in doc_keywords.items():
            if keyword in text_lower:
                docs.append(doc_name)

        return docs

    def _estimate_timeline(self, text: str) -> int:
        """Estimate processing timeline in days from permit text."""
        text_lower = text.lower()

        # Look for explicit timeline mentions
        if "immediate" in text_lower or "24 hour" in text_lower:
            return 1
        if "week" in text_lower and "2" in text:
            return 14
        if "30 day" in text_lower or "month" in text_lower:
            return 30
        if "express" in text_lower or "expedited" in text_lower:
            return 7

        # Estimate based on permit type
        if any(word in text_lower for word in ["building", "construction", "major"]):
            return 30
        if any(word in text_lower for word in ["electrical", "plumbing", "mechanical"]):
            return 14
        if any(word in text_lower for word in ["minor", "repair", "temporary"]):
            return 7

        return 14  # Default: 2 weeks
    
    async def _save_checklist(self, permits: List, property_id: str, org_id: str) -> str:
        """Save permit checklist to Supabase checklists table."""
        if not permits:
            return ""

        client = get_supabase_client("service")

        # Create parent checklist record
        from datetime import datetime
        checklist_result = (
            client.table("checklists")
            .insert({
                "org_id": org_id,
                "property_id": property_id,
                "name": "Permit Requirements Checklist",
                "type": "regulatory",
                "status": "pending",
                "total_items": len(permits),
                "completed_items": 0,
                "created_at": datetime.now().isoformat()
            })
            .execute()
        )

        if not checklist_result.data:
            return ""

        checklist_id = checklist_result.data[0]["id"]

        # Insert checklist items
        items = []
        for i, permit in enumerate(permits, 1):
            items.append({
                "checklist_id": checklist_id,
                "org_id": org_id,
                "property_id": property_id,
                "sequence": i,
                "title": permit.get("requirement", ""),
                "description": f"Required docs: {', '.join(permit.get('required_docs', []))}",
                "status": "pending",
                "estimated_days": permit.get("estimated_days", 14),
                "created_at": datetime.now().isoformat()
            })

        if items:
            client.table("checklist_items").insert(items).execute()

        return checklist_id
    
    async def _get_historical_rates(self, service: str, org_id: str) -> Dict:
        """Get historical vendor rates from Supabase vendor_rates table."""
        client = get_supabase_client("service")
        result = (
            client.table("vendor_rates")
            .select("service, rate, vendor_id, effective_date, city")
            .eq("org_id", org_id)
            .eq("service", service)
            .order("effective_date", desc=True)
            .limit(10)
            .execute()
        )
        rows = result.data if result.data else []
        if rows:
            avg_rate = sum(r.get("rate") or 0 for r in rows) / len(rows)
            return {"rates": rows, "avg_rate": avg_rate, "count": len(rows)}
        return {"rates": [], "avg_rate": 0, "count": 0}
    
    async def _get_past_incidents(self, incident_type: str, org_id: str) -> List:
        """Get past similar incidents from Supabase tickets table."""
        client = get_supabase_client("service")
        result = (
            client.table("tickets")
            .select(
                "id, title, description, status, priority, incident_type, "
                "property_id, created_at, resolved_at, resolution_time_hours"
            )
            .eq("org_id", org_id)
            .eq("incident_type", incident_type)
            .in_("status", ["resolved", "closed"])
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        return result.data if result.data else []
    
    async def _create_emergency_ticket(self, incident_type: str, property_id: str,
                                      org_id: str) -> Dict:
        """Create emergency ticket in Supabase."""
        client = get_supabase_client("service")

        from datetime import datetime, timedelta

        # Create high-priority emergency ticket
        ticket_data = {
            "org_id": org_id,
            "property_id": property_id,
            "title": f"EMERGENCY: {incident_type}",
            "description": f"Emergency incident reported: {incident_type}. Immediate response required.",
            "status": "open",
            "priority": "critical",
            "incident_type": incident_type,
            "source": "perplexity_chat",
            "deadline": (datetime.now() + timedelta(hours=2)).isoformat(),  # 2-hour SLA
            "created_at": datetime.now().isoformat()
        }

        result = client.table("tickets").insert(ticket_data).execute()

        if result.data:
            ticket = result.data[0]
            return {
                "ticket_id": ticket.get("id"),
                "ticket_number": ticket.get("ticket_number", f"EMERGENCY-{ticket.get('id', '001')}"),
                "priority": "critical",
                "deadline": ticket_data["deadline"]
            }

        return {"ticket_id": None, "error": "Failed to create emergency ticket"}
    
    async def _get_emergency_contacts(self, property_id: str, org_id: str) -> List:
        """Get emergency contacts from Supabase for property."""
        client = get_supabase_client("service")

        contacts = []

        # Get property manager/director for this property
        property_result = (
            client.table("properties")
            .select("manager_id, site_director_id, emergency_contact")
            .eq("id", property_id)
            .eq("org_id", org_id)
            .execute()
        )

        if property_result.data:
            prop = property_result.data[0]
            manager_id = prop.get("manager_id")
            director_id = prop.get("site_director_id")
            emergency_contact = prop.get("emergency_contact")

            # Fetch manager details
            if manager_id:
                manager_result = (
                    client.table("users")
                    .select("id, name, email, phone, role")
                    .eq("id", manager_id)
                    .eq("org_id", org_id)
                    .execute()
                )
                if manager_result.data:
                    mgr = manager_result.data[0]
                    contacts.append({
                        "name": mgr.get("name"),
                        "role": "Property Manager",
                        "phone": mgr.get("phone"),
                        "email": mgr.get("email"),
                        "priority": 1
                    })

            # Fetch site director details
            if director_id and director_id != manager_id:
                director_result = (
                    client.table("users")
                    .select("id, name, email, phone, role")
                    .eq("id", director_id)
                    .eq("org_id", org_id)
                    .execute()
                )
                if director_result.data:
                    dir = director_result.data[0]
                    contacts.append({
                        "name": dir.get("name"),
                        "role": "Site Director",
                        "phone": dir.get("phone"),
                        "email": dir.get("email"),
                        "priority": 2
                    })

            # Add emergency contact if provided
            if emergency_contact:
                contacts.append({
                    "name": "Emergency Contact",
                    "role": "Emergency Hotline",
                    "phone": emergency_contact,
                    "email": None,
                    "priority": 0
                })

        # Get on-call engineers for this org
        oncall_result = (
            client.table("users")
            .select("id, name, email, phone, role")
            .eq("org_id", org_id)
            .eq("on_call_status", True)
            .limit(3)
            .execute()
        )
        for user in (oncall_result.data or []):
            contacts.append({
                "name": user.get("name"),
                "role": "On-Call Engineer",
                "phone": user.get("phone"),
                "email": user.get("email"),
                "priority": 3
            })

        # Sort by priority
        contacts.sort(key=lambda x: x.get("priority", 99))
        return contacts