"""
F04: Escalation Voice Command
Update ticket priority, change assignee, fire notifications via voice command.
"""

from typing import Dict, Any, Optional
from enum import Enum

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult
from cassandra.supabase import get_supabase_client


class EscalationLevel(str, Enum):
    """Escalation priority levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class VoiceEscalationProcessor:
    """
    F04: Escalation Voice Command
    
    Trigger: "Escalate the plumbing leak on B2 — mark it critical and notify the site director"
    
    Flow: Voice → Backend API call → Status update → Notification → Supermemory event
    """
    
    def __init__(self, backend_api, notification_service, memory_manager, audit_logger):
        self.backend_api = backend_api
        self.notifications = notification_service
        self.memory_manager = memory_manager
        self.audit_logger = audit_logger
        
    async def process_escalation_command(self, audio_text: str, org_id: str,
                                        speaker_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process escalation voice command.
        
        Returns:
            Escalation result with confirmation
        """
        # Step 1: Parse escalation intent
        escalation = self._parse_escalation(audio_text)

        # Step 1b: Fetch dual-read context about this ticket/escalation
        context = await fetch_full_context(
            query=f"escalation {audio_text}",
            org_id=org_id,
            data_hints=["tickets", "users"],
            top_k=5
        )
        
        if not escalation.get('ticket_reference'):
            return {
                "success": False,
                "error": "Could not identify ticket to escalate",
                "message": "Please specify which ticket to escalate"
            }
        
        # Step 2: Find the ticket
        ticket = await self._find_ticket(escalation['ticket_reference'], org_id)
        
        if not ticket:
            return {
                "success": False,
                "error": f"Ticket not found: {escalation['ticket_reference']}",
                "message": "I couldn't find that ticket. Please check the reference."
            }
        
        # Step 3: Call Backend API (never direct DB from Cassandra)
        escalation_data = {
            "ticket_id": ticket['id'],
            "priority": escalation.get('priority', 'critical'),
            "new_assignee": escalation.get('assignee'),
            "reason": escalation.get('reason', 'Voice escalation'),
            "escalated_by": speaker_context.get('user_id'),
            "escalated_at": "now()"
        }
        
        api_result = await self.backend_api.escalate_ticket(escalation_data)
        
        if not api_result.get('success'):
            return {
                "success": False,
                "error": api_result.get('error', 'Backend API error'),
                "message": "Failed to escalate ticket. Please try again."
            }
        
        # Step 4: Send notifications with enriched context
        notification_result = await self._send_notifications(ticket, escalation, org_id, context)
        
        # Step 5: Log to Supermemory
        await self._log_escalation_event(ticket, escalation, speaker_context, org_id)
        
        # Step 6: Audit log
        await self.audit_logger.log_action(
            org_id=org_id,
            actor=speaker_context.get('user_id'),
            action="ticket_escalated",
            entity_id=ticket['id'],
            metadata={
                "old_priority": ticket.get('priority'),
                "new_priority": escalation.get('priority'),
                "reason": escalation.get('reason')
            }
        )
        
        # Step 7: Generate confirmation
        confirmation = self._generate_confirmation(ticket, escalation, notification_result)
        
        return {
            "success": True,
            "ticket_id": ticket['id'],
            "new_priority": escalation.get('priority'),
            "notifications_sent": notification_result.get('sent', 0),
            "confirmation_audio_text": confirmation,
            "audit_log_id": api_result.get('audit_log_id')
        }
    
    def _parse_escalation(self, text: str) -> Dict[str, Any]:
        """Parse escalation command from text."""
        import re
        
        text_lower = text.lower()
        
        # Extract ticket reference
        ticket_ref = None
        patterns = [
            r'escalate\s+(?:the\s+)?([\w\s]+?)(?:\s+(?:on|at|in)\s+|\s+(?:mark|to)\s+|\s*$)',
            r'escalate\s+(?:ticket\s+)?([A-Z0-9\-]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                ticket_ref = match.group(1).strip()
                break
        
        # Extract priority
        priority = "critical"
        if "critical" in text_lower:
            priority = "critical"
        elif "high" in text_lower:
            priority = "high"
        elif "urgent" in text_lower:
            priority = "critical"
        
        # Extract assignee
        assignee = None
        assignee_patterns = [
            r'assign\s+(?:it\s+)?to\s+(\w+)',
            r'notify\s+(?:the\s+)?([\w\s]+?)(?:\s*$|\s+(?:and|about))',
        ]
        for pattern in assignee_patterns:
            match = re.search(pattern, text_lower)
            if match:
                assignee = match.group(1).strip()
                break
        
        # Extract reason/location
        reason = None
        location_patterns = [
            r'(?:on|at|in)\s+(\w+\s*\d*)',
        ]
        for pattern in location_patterns:
            match = re.search(pattern, text_lower)
            if match:
                reason = f"Location: {match.group(1)}"
                break
        
        return {
            "ticket_reference": ticket_ref,
            "priority": priority,
            "assignee": assignee,
            "reason": reason or "Voice escalation"
        }
    
    async def _find_ticket(self, reference: str, org_id: str) -> Optional[Dict[str, Any]]:
        """Find ticket by reference (title, ID, or number)."""
        # Try exact ID match first
        query = """
            SELECT id, title, status, priority, assigned_to
            FROM tickets 
            WHERE (id = $1 OR title ILIKE $2 OR ticket_number = $1)
            AND org_id = $3
            AND status != 'archived'
            LIMIT 1
        """
        result = await self.backend_api.db.fetchrow(query, reference, f"%{reference}%", org_id)
        
        if result:
            return dict(result)
        
        # Fallback: semantic search
        memories = await self.memory_manager.search_memories(reference, org_id, limit=1)
        if memories:
            # Resolve to ticket
            memory_id = memories[0].get('memory_id')
            # Query memory_ticket_map
            map_result = await self.backend_api.db.fetchrow(
                "SELECT ticket_id FROM memory_ticket_map WHERE memory_id = $1",
                memory_id
            )
            if map_result:
                return await self._find_ticket(map_result['ticket_id'], org_id)
        
        return None
    
    async def _send_notifications(self, ticket: Dict, escalation: Dict,
                                  org_id: str,
                                  dual_context: Optional[ContextResult] = None) -> Dict[str, Any]:
        """Send notifications for escalation, enriched with dual-read context."""
        notifications_sent = 0

        # Build enriched context string for notification body
        enrichment = ""
        if dual_context:
            if dual_context.memory_chunks:
                top_chunk = dual_context.memory_chunks[0]
                enrichment = f" | Context: {top_chunk.get('content', '')[:80]}"
            elif dual_context.supabase_rows:
                enrichment = f" | Additional Supabase data available."

        # Notify new assignee if specified
        if escalation.get('assignee'):
            await self.notifications.send_push(
                user_id=escalation['assignee'],
                title="Escalated Ticket Assigned",
                body=f"'{ticket['title']}' escalated to {escalation['priority']} priority{enrichment}",
                data={"ticket_id": ticket['id'], "action": "view_ticket"}
            )
            notifications_sent += 1

        # Notify site director
        site_director = await self._get_site_director(org_id)
        if site_director:
            await self.notifications.send_push(
                user_id=site_director,
                title="Ticket Escalated",
                body=f"'{ticket['title']}' escalated to {escalation['priority']}{enrichment}",
                data={"ticket_id": ticket['id'], "action": "review_escalation"}
            )
            notifications_sent += 1

        return {"sent": notifications_sent}
    
    async def _get_site_director(self, org_id: str) -> Optional[str]:
        """Get site director user ID for org via Supabase."""
        client = get_supabase_client("service")
        result = (
            client.table("users")
            .select("id")
            .eq("org_id", org_id)
            .eq("role", "site_director")
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]["id"]
        return None
    
    async def _log_escalation_event(self, ticket: Dict, escalation: Dict,
                                    speaker_context: Dict, org_id: str):
        """Log escalation event to Supermemory."""
        event_data = {
            "event_type": "TICKET_ESCALATED",
            "ticket_id": ticket['id'],
            "old_priority": ticket.get('priority'),
            "new_priority": escalation.get('priority'),
            "escalated_by": speaker_context.get('user_id'),
            "reason": escalation.get('reason'),
            "timestamp": "now()"
        }
        
        await self.memory_manager.add_memory(
            content=f"Ticket '{ticket['title']}' escalated from {ticket.get('priority')} to {escalation.get('priority')}",
            memory_type="ESCALATION_EVENT",
            org_id=org_id,
            entity_id=ticket['id'],
            metadata=event_data,
            confidence=1.0
        )
    
    def _generate_confirmation(self, ticket: Dict, escalation: Dict,
                              notification_result: Dict) -> str:
        """Generate audio confirmation."""
        parts = [
            f"Ticket '{ticket['title']}' has been escalated to {escalation['priority']} priority",
        ]
        
        if escalation.get('assignee'):
            parts.append(f"and assigned to {escalation['assignee']}")
        
        sent = notification_result.get('sent', 0)
        if sent > 0:
            parts.append(f"{sent} notification{'s' if sent > 1 else ''} sent")
        
        return ". ".join(parts)