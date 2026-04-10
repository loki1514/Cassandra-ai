"""
F01: Natural Language Ticket Raising
Cassandra parses voice commands, identifies commitments, assignees, assets, and deadlines — then raises tickets automatically.
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from pydantic import BaseModel, Field


class TicketExtraction(BaseModel):
    """Extracted ticket information from natural language."""
    title: str = Field(..., description="Ticket title")
    assignee: Optional[str] = Field(None, description="Assigned person name")
    asset: Optional[str] = Field(None, description="Asset or location mentioned")
    deadline: Optional[datetime] = Field(None, description="Extracted deadline")
    priority: str = Field("medium", description="Ticket priority")
    confidence: float = Field(..., description="Extraction confidence 0-1")


class NaturalLanguageTicketProcessor:
    """
    F01: Natural Language Ticket Raising
    
    Trigger: "Neelabh, get the AC units on floor 3 serviced before Thursday"
    
    Flow: Voice → LLM Extraction → create_ticket → Audio Confirmation
    """
    
    # Common deadline patterns
    DEADLINE_PATTERNS = {
        'today': 0,
        'tomorrow': 1,
        'day after tomorrow': 2,
        'next week': 7,
        'next month': 30,
    }
    
    # Day name mappings
    DAY_NAMES = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    
    def __init__(self, llm_client, ticket_tool, speaker_id_service):
        self.llm = llm_client
        self.ticket_tool = ticket_tool
        self.speaker_id = speaker_id_service
        
    async def process_voice_command(self, audio_text: str, org_id: str, 
                                   speaker_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process natural language voice command and create ticket.
        
        Args:
            audio_text: Transcribed voice command
            org_id: Organization ID
            speaker_context: Speaker identification context
            
        Returns:
            Created ticket details with confirmation
        """
        # Step 1: Extract ticket information using LLM
        extraction = await self._extract_ticket_info(audio_text, speaker_context)
        
        if extraction.confidence < 0.7:
            return {
                "success": False,
                "error": "Low confidence extraction",
                "confidence": extraction.confidence,
                "message": "Could you please repeat that more clearly?"
            }
        
        # Step 2: Resolve assignee to user ID
        assignee_id = await self._resolve_assignee(extraction.assignee, org_id)
        
        # Step 3: Create the ticket
        ticket_data = {
            "title": extraction.title,
            "description": f"Asset: {extraction.asset}\nOriginal command: {audio_text}",
            "assigned_to": assignee_id,
            "deadline": extraction.deadline.isoformat() if extraction.deadline else None,
            "priority": extraction.priority,
            "org_id": org_id,
            "source": "voice_command",
            "confidence": extraction.confidence
        }
        
        ticket_result = await self.ticket_tool.create_ticket(ticket_data)
        
        # Step 4: Generate audio confirmation
        confirmation = self._generate_confirmation(extraction, ticket_result)
        
        return {
            "success": True,
            "ticket": ticket_result,
            "confirmation_audio_text": confirmation,
            "extraction": extraction.dict()
        }
    
    async def _extract_ticket_info(self, text: str, context: Dict[str, Any]) -> TicketExtraction:
        """Use LLM to extract structured ticket info from natural language."""
        
        prompt = f"""
        Extract ticket information from this voice command:
        "{text}"
        
        Context:
        - Speaker: {context.get('speaker_name', 'Unknown')}
        - Current time: {datetime.now().isoformat()}
        
        Extract:
        1. Task/action to be done (as ticket title)
        2. Person being assigned (if mentioned)
        3. Asset/location mentioned
        4. Deadline (if mentioned - parse relative dates)
        5. Priority (infer from urgency words)
        
        Return JSON with: title, assignee, asset, deadline, priority, confidence
        """
        
        # LLM extraction would happen here
        # For now, using rule-based extraction as fallback
        return self._rule_based_extraction(text)
    
    def _rule_based_extraction(self, text: str) -> TicketExtraction:
        """Rule-based extraction as fallback."""
        text_lower = text.lower()
        
        # Extract assignee (name at beginning)
        assignee = None
        name_match = re.match(r'^(\w+),', text)
        if name_match:
            assignee = name_match.group(1).capitalize()
        
        # Extract asset/location
        asset = None
        asset_patterns = [
            r'(\w+) units on (\w+ \d+)',
            r'(\w+) on (\w+ \d+)',
            r'(basement|lobby|parking|floor \d+)',
        ]
        for pattern in asset_patterns:
            match = re.search(pattern, text_lower)
            if match:
                asset = match.group(0)
                break
        
        # Extract deadline
        deadline = self._parse_deadline(text_lower)
        
        # Determine priority
        priority = "medium"
        if any(word in text_lower for word in ['urgent', 'critical', 'emergency', 'asap']):
            priority = "critical"
        elif any(word in text_lower for word in ['soon', 'quickly', 'important']):
            priority = "high"
        
        # Create title
        title = re.sub(r'^\w+,\s*', '', text)  # Remove assignee prefix
        title = re.sub(r'\s+\w+\s+\d+\s+(AM|PM|am|pm)', '', title)  # Remove time
        title = title.strip().capitalize()
        
        return TicketExtraction(
            title=title[:100],
            assignee=assignee,
            asset=asset,
            deadline=deadline,
            priority=priority,
            confidence=0.85 if assignee and asset else 0.65
        )
    
    def _parse_deadline(self, text: str) -> Optional[datetime]:
        """Parse deadline from natural language."""
        text_lower = text.lower()
        now = datetime.now()
        
        # Check for relative dates
        for phrase, days in self.DEADLINE_PATTERNS.items():
            if phrase in text_lower:
                return now + timedelta(days=days)
        
        # Check for day names
        for i, day in enumerate(self.DAY_NAMES):
            if day in text_lower:
                days_ahead = i - now.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                return now + timedelta(days=days_ahead)
        
        return None
    
    async def _resolve_assignee(self, name: Optional[str], org_id: str) -> Optional[str]:
        """Resolve assignee name to user ID."""
        if not name:
            return None
        
        # Query user database by name
        # This would integrate with the users table
        return f"user_{name.lower()}"
    
    def _generate_confirmation(self, extraction: TicketExtraction, ticket: Dict) -> str:
        """Generate audio confirmation text."""
        parts = [f"Ticket created: {extraction.title}"]
        
        if extraction.assignee:
            parts.append(f"Assigned to {extraction.assignee}")
        
        if extraction.deadline:
            day_name = extraction.deadline.strftime('%A')
            parts.append(f"Due by {day_name}")
        
        parts.append(f"Ticket ID: {ticket.get('ticket_number', 'TICKET-XXX')}")
        
        return ". ".join(parts)