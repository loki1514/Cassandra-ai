"""
F02: Multi-Command Batch Execution
Process numbered lists spoken in one breath and create all tickets in parallel.
"""

import asyncio
from typing import List, Dict, Any
from pydantic import BaseModel


class BatchCommand(BaseModel):
    """Single command from a batch."""
    index: int
    text: str
    extraction: Dict[str, Any]


class BatchTicketProcessor:
    """
    F02: Multi-Command Batch Execution
    
    Trigger: "Create tickets for: 1) Lobby deep clean Monday, 2) HVAC filter replacement Wednesday..."
    
    Flow: Detect numbered list → Parse individual commands → Parallel create_ticket calls → Batch confirmation
    """
    
    def __init__(self, nl_ticket_processor, ticket_tool):
        self.nl_processor = nl_ticket_processor
        self.ticket_tool = ticket_tool
        
    async def process_batch_command(self, audio_text: str, org_id: str, 
                                   speaker_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process batch of commands and create tickets in parallel.
        
        Returns:
            Batch result with all created tickets and confirmation
        """
        # Step 1: Parse numbered list
        commands = self._parse_numbered_list(audio_text)
        
        if not commands:
            return {
                "success": False,
                "error": "No numbered commands detected",
                "message": "Please use format: 1) Task one, 2) Task two"
            }
        
        # Step 2: Process each command in parallel
        tasks = []
        for cmd in commands:
            task = self._process_single_command(cmd, org_id, speaker_context)
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Step 3: Compile results
        successful = []
        failed = []
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                failed.append({
                    "index": i + 1,
                    "command": commands[i],
                    "error": str(result)
                })
            elif result.get("success"):
                successful.append(result)
            else:
                failed.append({
                    "index": i + 1,
                    "command": commands[i],
                    "error": result.get("error", "Unknown error")
                })
        
        # Step 4: Generate batch confirmation
        confirmation = self._generate_batch_confirmation(successful, failed)
        
        return {
            "success": len(failed) == 0,
            "total_commands": len(commands),
            "successful": len(successful),
            "failed": len(failed),
            "tickets": successful,
            "failures": failed,
            "confirmation_audio_text": confirmation,
            "linked_to_meeting": True  # All tickets linked to same meeting memory
        }
    
    def _parse_numbered_list(self, text: str) -> List[str]:
        """Parse numbered list from text."""
        import re
        
        # Pattern: 1) ... 2) ... or 1. ... 2. ...
        pattern = r'(?:\d+[).:]\s*)([^\d]+?)(?=\s*\d+[).:]|$)'
        matches = re.findall(pattern, text, re.IGNORECASE)
        
        if matches:
            return [m.strip() for m in matches if m.strip()]
        
        # Fallback: split by common separators
        separators = [' and ', ', then ', '. then ', '; ']
        for sep in separators:
            if sep in text.lower():
                parts = text.split(sep)
                return [p.strip() for p in parts if p.strip()]
        
        return [text] if text else []
    
    async def _process_single_command(self, command: str, org_id: str, 
                                     context: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single command from the batch."""
        return await self.nl_processor.process_voice_command(command, org_id, context)
    
    def _generate_batch_confirmation(self, successful: List[Dict], 
                                    failed: List[Dict]) -> str:
        """Generate audio confirmation for batch."""
        total = len(successful) + len(failed)
        
        parts = [f"Created {len(successful)} of {total} tickets."]
        
        # Read back ticket IDs
        if successful:
            ticket_ids = [t.get('ticket', {}).get('ticket_number', 'Unknown') 
                         for t in successful[:3]]
            parts.append(f"Ticket numbers: {', '.join(ticket_ids)}")
            
            if len(successful) > 3:
                parts.append(f"and {len(successful) - 3} more")
        
        if failed:
            parts.append(f"{len(failed)} commands could not be processed")
        
        return ". ".join(parts)