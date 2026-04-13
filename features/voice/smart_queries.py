"""
F03: Smart Status Queries
Query tickets by natural language and get verbal responses with provenance.
"""

from typing import Optional, Dict, Any
from datetime import datetime

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult


class SmartStatusQueryProcessor:
    """
    F03: Smart Status Queries
    
    Trigger: "What's the status of the basement scrap removal?"
    
    Flow: Semantic search → memory_ticket_map → DB1 lookup → Verbal response with provenance
    """
    
    def __init__(self, context_fetcher, memory_manager, db_client):
        self.context_fetcher = context_fetcher
        self.memory_manager = memory_manager
        self.db = db_client
        
    async def process_status_query(self, query_text: str, org_id: str,
                                   speaker_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process natural language status query.
        
        Returns:
            Query result with verbal response and provenance
        """
        # Step 1: Dual-read context fetch from Supabase + Supermemory
        context = await fetch_full_context(
            query=query_text,
            org_id=org_id,
            data_hints=["tickets", "users"],
            top_k=5
        )

        # The memory_chunks from Supermemory replace the direct search_memories() result
        search_results = context.memory_chunks

        if not search_results:
            return {
                "success": False,
                "response_text": "I couldn't find any tickets matching that description.",
                "provenance": None
            }
        
        # Step 2: Resolve to ticket via memory_ticket_map
        top_memory = search_results[0]
        ticket_info = await self._resolve_to_ticket(top_memory, org_id)
        
        if not ticket_info:
            return {
                "success": False,
                "response_text": "I found a record but couldn't locate the current ticket status.",
                "provenance": {
                    "memory_id": top_memory.get('id'),
                    "confidence": top_memory.get('confidence', 0)
                }
            }
        
        # Step 3: Fetch live status from DB1
        live_status = await self._fetch_live_status(ticket_info['ticket_id'], org_id)
        
        # Step 4: Generate response with provenance, merging memory_chunks
        response = self._generate_response(ticket_info, live_status, top_memory, context)

        return {
            "success": True,
            "response_text": response['text'],
            "provenance": response['provenance'],
            "ticket": live_status,
            "confidence": top_memory.get('score', top_memory.get('confidence', 0)),
            "memory_chunks": context.memory_chunks,
        }
    
    async def _resolve_to_ticket(self, memory: Dict[str, Any], org_id: str) -> Optional[Dict]:
        """Resolve memory to ticket via memory_ticket_map."""
        memory_id = memory.get('memory_id')
        
        # Query memory_ticket_map
        query = """
            SELECT ticket_id, confidence_score 
            FROM memory_ticket_map 
            WHERE memory_id = $1 AND org_id = $2
        """
        result = await self.db.fetchrow(query, memory_id, org_id)
        
        if result:
            return {
                'ticket_id': result['ticket_id'],
                'confidence': result['confidence_score'],
                'memory_id': memory_id
            }
        return None
    
    async def _fetch_live_status(self, ticket_id: str, org_id: str) -> Dict[str, Any]:
        """Fetch live ticket status from DB1."""
        query = """
            SELECT id, title, status, assigned_to, deadline, 
                   created_at, updated_at, priority
            FROM tickets 
            WHERE id = $1 AND org_id = $2
        """
        result = await self.db.fetchrow(query, ticket_id, org_id)
        
        if result:
            return dict(result)
        return {}
    
    def _generate_response(self, ticket_info: Dict, live_status: Dict,
                          memory: Dict[str, Any],
                          dual_context: ContextResult) -> Dict[str, Any]:
        """Generate natural language response with provenance, enriched by dual-read memory_chunks."""

        if not live_status:
            return {
                'text': "I found a reference to that ticket but it appears to have been deleted.",
                'provenance': None
            }

        # Build response
        parts = []

        # Status description
        status = live_status.get('status', 'unknown')
        title = live_status.get('title', 'Unknown task')

        parts.append(f"The {title} ticket")

        # Created info
        created_at = live_status.get('created_at')
        if created_at:
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            day_name = created_at.strftime('%A')
            parts.append(f"was created on {day_name}")

        # Assignee
        assigned_to = live_status.get('assigned_to')
        if assigned_to:
            parts.append(f"and is assigned to {assigned_to}")

        # Current status
        if status == 'completed':
            updated_at = live_status.get('updated_at')
            if updated_at:
                if isinstance(updated_at, str):
                    updated_at = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                day_name = updated_at.strftime('%A')
                parts.append(f"Current status: completed as of {day_name}")
            else:
                parts.append("Current status: completed")
        elif status == 'active':
            deadline = live_status.get('deadline')
            if deadline:
                if isinstance(deadline, str):
                    deadline = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
                days_remaining = (deadline - datetime.now()).days
                if days_remaining > 0:
                    parts.append(f"Current status: active, due in {days_remaining} days")
                else:
                    parts.append(f"Current status: overdue by {abs(days_remaining)} days")
            else:
                parts.append("Current status: active")
        else:
            parts.append(f"Current status: {status}")

        # Merge additional context from memory_chunks (Supermemory dual-read)
        if dual_context.memory_chunks:
            # Append notable memory details not already in the live status
            for chunk in dual_context.memory_chunks[:2]:
                chunk_content = chunk.get('content', '')
                if chunk_content and chunk_content not in response_text:
                    parts.append(f"Note: {chunk_content[:100]}")

        response_text = ". ".join(parts) + "."

        # Provenance info
        provenance = {
            "source_meeting_id": memory.get('meeting_id'),
            "source_timestamp": memory.get('created_at'),
            "confidence": memory.get('score', memory.get('confidence', 0)),
            "db1_status": status,
            "memory_status": memory.get('status', 'unknown'),
            "supermemory_chunks": [
                {"content": c.get('content'), "source": c.get('source'), "score": c.get('score')}
                for c in dual_context.memory_chunks
            ],
        }

        return {
            'text': response_text,
            'provenance': provenance
        }