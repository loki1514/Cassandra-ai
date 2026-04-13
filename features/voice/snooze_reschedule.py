"""
F05: Snooze & Reschedule via Voice
Update ticket deadlines via natural language voice commands.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import re

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult
from cassandra.supabase import get_supabase_client


class VoiceRescheduleProcessor:
    """
    F05: Snooze & Reschedule via Voice
    
    Trigger: "Push the window cleaning to next week — same crew"
    
    Flow: Semantic ticket lookup → Backend PATCH → Supermemory RESCHEDULED event
    """
    
    def __init__(self, backend_api, memory_manager, notification_service):
        self.backend_api = backend_api
        self.memory_manager = memory_manager
        self.notifications = notification_service
        
    async def process_reschedule_command(self, audio_text: str, org_id: str,
                                        speaker_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process reschedule/snooze voice command.
        
        Returns:
            Reschedule result with confirmation
        """
        # Step 0: Fetch dual-read context to check for verbal mentions of rescheduling
        context = await fetch_full_context(
            query=audio_text,
            org_id=org_id,
            data_hints=["tickets"],
            top_k=5
        )

        # Step 1: Parse reschedule intent
        reschedule = self._parse_reschedule(audio_text)
        
        if not reschedule.get('ticket_reference'):
            return {
                "success": False,
                "error": "Could not identify ticket to reschedule",
                "message": "Please specify which ticket to reschedule"
            }
        
        # Step 2: Find the ticket
        ticket = await self._find_ticket(reschedule['ticket_reference'], org_id)
        
        if not ticket:
            return {
                "success": False,
                "error": f"Ticket not found: {reschedule['ticket_reference']}",
                "message": "I couldn't find that ticket. Please check the reference."
            }
        
        old_deadline = ticket.get('deadline')
        new_deadline = reschedule.get('new_deadline')
        
        if not new_deadline:
            return {
                "success": False,
                "error": "Could not parse new deadline",
                "message": "Please specify when to reschedule to"
            }
        
        # Step 3: Call Backend API (PATCH)
        update_data = {
            "ticket_id": ticket['id'],
            "deadline": new_deadline.isoformat(),
            "updated_by": speaker_context.get('user_id'),
            "update_reason": reschedule.get('reason', 'Voice reschedule'),
            "preserve_assignee": reschedule.get('same_crew', True)
        }
        
        api_result = await self.backend_api.update_ticket(ticket['id'], update_data)
        
        if not api_result.get('success'):
            return {
                "success": False,
                "error": api_result.get('error', 'Backend API error'),
                "message": "Failed to reschedule ticket. Please try again."
            }
        
        # Step 4: Log to Supermemory (RESCHEDULED event)
        await self._log_reschedule_event(ticket, old_deadline, new_deadline, 
                                        speaker_context, org_id)
        
        # Step 5: Re-notify assignee
        if ticket.get('assigned_to') and reschedule.get('same_crew', True):
            await self.notifications.send_push(
                user_id=ticket['assigned_to'],
                title="📅 Ticket Rescheduled",
                body=f"'{ticket['title']}' moved to {new_deadline.strftime('%A, %B %d')}",
                data={"ticket_id": ticket['id'], "action": "view_ticket"}
            )
        
        # Step 6: Generate confirmation
        confirmation = self._generate_confirmation(ticket, old_deadline, new_deadline)
        
        return {
            "success": True,
            "ticket_id": ticket['id'],
            "old_deadline": old_deadline.isoformat() if old_deadline else None,
            "new_deadline": new_deadline.isoformat(),
            "confirmation_audio_text": confirmation
        }
    
    def _parse_reschedule(self, text: str) -> Dict[str, Any]:
        """Parse reschedule command from text."""
        text_lower = text.lower()
        
        # Extract ticket reference
        ticket_ref = None
        patterns = [
            r'(?:push|move|reschedule|snooze)\s+(?:the\s+)?([\w\s]+?)(?:\s+(?:to|until|by)\s+|\s*$)',
            r'(?:push|move|reschedule|snooze)\s+(?:ticket\s+)?([A-Z0-9\-]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                ticket_ref = match.group(1).strip()
                break
        
        # Extract new deadline
        new_deadline = self._parse_new_deadline(text_lower)
        
        # Check for "same crew" preservation
        same_crew = any(phrase in text_lower for phrase in 
                       ['same crew', 'same team', 'same person', 'keep assignee'])
        
        return {
            "ticket_reference": ticket_ref,
            "new_deadline": new_deadline,
            "same_crew": same_crew,
            "reason": "Voice reschedule"
        }
    
    def _parse_new_deadline(self, text: str) -> Optional[datetime]:
        """Parse new deadline from text."""
        text_lower = text.lower()
        now = datetime.now()
        
        # Relative time patterns
        patterns = {
            r'to\s+next\s+week': lambda: now + timedelta(days=7),
            r'to\s+next\s+month': lambda: now + timedelta(days=30),
            r'to\s+tomorrow': lambda: now + timedelta(days=1),
            r'to\s+today': lambda: now,
            r'by\s+next\s+week': lambda: now + timedelta(days=7),
            r'by\s+tomorrow': lambda: now + timedelta(days=1),
            r'for\s+next\s+week': lambda: now + timedelta(days=7),
            r'until\s+next\s+week': lambda: now + timedelta(days=7),
        }
        
        for pattern, func in patterns.items():
            if re.search(pattern, text_lower):
                return func()
        
        # Day of week patterns
        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        for day in days:
            if f'to {day}' in text_lower or f'by {day}' in text_lower:
                target_day = days.index(day)
                days_ahead = target_day - now.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                return now + timedelta(days=days_ahead)
        
        return None
    
    async def _find_ticket(self, reference: str, org_id: str) -> Optional[Dict[str, Any]]:
        """Find ticket by reference using Supabase with org_id enforcement."""
        client = get_supabase_client("service")

        # Try exact match on id or ticket_number
        result = (
            client.table("tickets")
            .select("id, title, deadline, assigned_to, status")
            .eq("org_id", org_id)
            .or_(f"id.eq.{reference},ticket_number.eq.{reference}")
            .execute()
        )
        for row in result.data:
            if row.get("status") != "archived":
                return row

        # Fallback: ILIKE on title
        result2 = (
            client.table("tickets")
            .select("id, title, deadline, assigned_to, status")
            .eq("org_id", org_id)
            .ilike("title", f"%{reference}%")
            .execute()
        )
        for row in result2.data:
            if row.get("status") != "archived":
                return row

        return None
    
    async def _log_reschedule_event(self, ticket: Dict, old_deadline: Optional[datetime],
                                    new_deadline: datetime, speaker_context: Dict, 
                                    org_id: str):
        """Log reschedule event to Supermemory."""
        event_data = {
            "event_type": "TICKET_RESCHEDULED",
            "ticket_id": ticket['id'],
            "old_deadline": old_deadline.isoformat() if old_deadline else None,
            "new_deadline": new_deadline.isoformat(),
            "rescheduled_by": speaker_context.get('user_id'),
            "timestamp": "now()"
        }
        
        old_date = old_deadline.strftime('%A, %B %d') if old_deadline else 'no deadline'
        new_date = new_deadline.strftime('%A, %B %d')
        
        await self.memory_manager.add_memory(
            content=f"Ticket '{ticket['title']}' rescheduled from {old_date} to {new_date}",
            memory_type="RESCHEDULE_EVENT",
            org_id=org_id,
            entity_id=ticket['id'],
            metadata=event_data,
            confidence=1.0
        )
    
    def _generate_confirmation(self, ticket: Dict, old_deadline: Optional[datetime],
                              new_deadline: datetime) -> str:
        """Generate audio confirmation."""
        new_date_str = new_deadline.strftime('%A, %B %d')
        
        parts = [
            f"Ticket '{ticket['title']}' has been rescheduled",
            f"New deadline: {new_date_str}",
        ]
        
        if ticket.get('assigned_to'):
            parts.append("Assignee has been notified")
        
        return ". ".join(parts)