"""
F06: Voice-Driven Checklist Completion
Map spoken items to checklist items and mark complete hands-free.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import re


@dataclass
class ChecklistItemMatch:
    """Match between spoken item and checklist item."""
    spoken_text: str
    checklist_item_id: str
    checklist_item_name: str
    confidence: float
    matched: bool


class VoiceChecklistProcessor:
    """
    F06: Voice-Driven Checklist Completion
    
    Trigger: "Checklist for unit 4B: fire extinguisher checked, smoke detector tested..."
    
    Flow: Voice → Fuzzy match to checklist items → PATCH completions → Supermemory event
    """
    
    def __init__(self, db_client, memory_manager, fuzzy_matcher):
        self.db = db_client
        self.memory_manager = memory_manager
        self.fuzzy_matcher = fuzzy_matcher
        
    async def process_checklist_command(self, audio_text: str, org_id: str,
                                       speaker_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process voice checklist completion command.
        
        Returns:
            Completion result with matched items and confirmation
        """
        # Step 1: Parse checklist reference and items
        parsed = self._parse_checklist_command(audio_text)
        
        if not parsed.get('checklist_reference'):
            return {
                "success": False,
                "error": "Could not identify checklist",
                "message": "Please specify which checklist you're completing"
            }
        
        # Step 2: Find the checklist
        checklist = await self._find_checklist(parsed['checklist_reference'], org_id)
        
        if not checklist:
            return {
                "success": False,
                "error": f"Checklist not found: {parsed['checklist_reference']}",
                "message": "I couldn't find that checklist. Please check the reference."
            }
        
        # Step 3: Match spoken items to checklist items
        matches = await self._match_items_to_checklist(
            parsed['items'], 
            checklist['items']
        )
        
        # Step 4: Update matched items as complete
        completed_items = []
        partial_items = []
        
        for match in matches:
            if match.matched and match.confidence >= 0.7:
                await self._mark_item_complete(
                    checklist_id=checklist['id'],
                    item_id=match.checklist_item_id,
                    completed_by=speaker_context.get('user_id'),
                    evidence=f"Voice: {match.spoken_text}"
                )
                completed_items.append(match)
            elif match.matched and match.confidence >= 0.5:
                partial_items.append(match)
        
        # Step 5: Log session to Supermemory
        await self._log_checklist_session(checklist, matches, speaker_context, org_id)
        
        # Step 6: Check for partial completion alert
        alert = None
        if partial_items and not completed_items:
            alert = "Some items were unclear. Please review the checklist."
        
        # Step 7: Generate confirmation
        confirmation = self._generate_confirmation(checklist, completed_items, partial_items)
        
        return {
            "success": True,
            "checklist_id": checklist['id'],
            "checklist_name": checklist['name'],
            "completed_count": len(completed_items),
            "partial_count": len(partial_items),
            "completed_items": [m.checklist_item_name for m in completed_items],
            "partial_items": [m.checklist_item_name for m in partial_items],
            "confirmation_audio_text": confirmation,
            "alert": alert
        }
    
    def _parse_checklist_command(self, text: str) -> Dict[str, Any]:
        """Parse checklist command from text."""
        text_lower = text.lower()
        
        # Extract checklist reference
        checklist_ref = None
        patterns = [
            r'checklist\s+(?:for\s+)?([\w\s]+?)(?::|\s+–)\s*',
            r'complete\s+(?:the\s+)?([\w\s]+?)\s+checklist',
        ]
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                checklist_ref = match.group(1).strip()
                break
        
        # Extract items (after colon or list indicators)
        items_text = text
        for sep in [':', '–', '-']:
            if sep in text:
                parts = text.split(sep, 1)
                if len(parts) > 1:
                    items_text = parts[1]
                    break
        
        # Parse individual items
        items = []
        # Split by commas, 'and', or newlines
        for delimiter in [',', ' and ', '\n', ';']:
            if delimiter in items_text:
                items = [i.strip() for i in items_text.split(delimiter) if i.strip()]
                break
        
        if not items:
            items = [items_text.strip()]
        
        return {
            "checklist_reference": checklist_ref,
            "items": items
        }
    
    async def _find_checklist(self, reference: str, org_id: str) -> Optional[Dict[str, Any]]:
        """Find checklist by reference."""
        query = """
            SELECT c.id, c.name, c.property_id, c.template_id,
                   json_agg(json_build_object(
                       'id', ci.id,
                       'name', ci.name,
                       'description', ci.description,
                       'required', ci.required
                   )) as items
            FROM checklists c
            JOIN checklist_items ci ON ci.checklist_id = c.id
            WHERE (c.name ILIKE $1 OR c.id = $1)
            AND c.org_id = $2
            AND c.status = 'active'
            GROUP BY c.id
            LIMIT 1
        """
        result = await self.db.fetchrow(query, f"%{reference}%", org_id)
        
        if result:
            return {
                'id': result['id'],
                'name': result['name'],
                'property_id': result['property_id'],
                'items': result['items'] if result['items'] else []
            }
        return None
    
    async def _match_items_to_checklist(self, spoken_items: List[str], 
                                       checklist_items: List[Dict]) -> List[ChecklistItemMatch]:
        """Fuzzy match spoken items to checklist items."""
        matches = []
        
        for spoken in spoken_items:
            best_match = None
            best_score = 0
            
            for checklist_item in checklist_items:
                # Calculate fuzzy match score
                score = self.fuzzy_matcher.calculate_similarity(
                    spoken.lower(), 
                    checklist_item['name'].lower()
                )
                
                # Also check description if available
                if checklist_item.get('description'):
                    desc_score = self.fuzzy_matcher.calculate_similarity(
                        spoken.lower(),
                        checklist_item['description'].lower()
                    )
                    score = max(score, desc_score)
                
                if score > best_score:
                    best_score = score
                    best_match = checklist_item
            
            if best_match and best_score >= 0.5:
                matches.append(ChecklistItemMatch(
                    spoken_text=spoken,
                    checklist_item_id=best_match['id'],
                    checklist_item_name=best_match['name'],
                    confidence=best_score,
                    matched=True
                ))
            else:
                matches.append(ChecklistItemMatch(
                    spoken_text=spoken,
                    checklist_item_id="",
                    checklist_item_name="",
                    confidence=best_score,
                    matched=False
                ))
        
        return matches
    
    async def _mark_item_complete(self, checklist_id: str, item_id: str,
                                  completed_by: str, evidence: str):
        """Mark checklist item as complete."""
        query = """
            UPDATE checklist_items
            SET completed = true,
                completed_at = NOW(),
                completed_by = $1,
                completion_evidence = $2
            WHERE id = $3 AND checklist_id = $4
        """
        await self.db.execute(query, completed_by, evidence, item_id, checklist_id)
    
    async def _log_checklist_session(self, checklist: Dict, matches: List[ChecklistItemMatch],
                                    speaker_context: Dict, org_id: str):
        """Log checklist session to Supermemory."""
        completed_count = sum(1 for m in matches if m.matched and m.confidence >= 0.7)
        total_count = len(matches)
        
        event_data = {
            "event_type": "CHECKLIST_SESSION",
            "checklist_id": checklist['id'],
            "checklist_name": checklist['name'],
            "inspector_id": speaker_context.get('user_id'),
            "items_completed": completed_count,
            "items_total": total_count,
            "completion_rate": completed_count / total_count if total_count > 0 else 0,
            "timestamp": datetime.now().isoformat()
        }
        
        await self.memory_manager.add_memory(
            content=f"Checklist '{checklist['name']}' session: {completed_count}/{total_count} items completed",
            memory_type="CHECKLIST_SESSION",
            org_id=org_id,
            entity_id=checklist['id'],
            metadata=event_data,
            confidence=1.0
        )
    
    def _generate_confirmation(self, checklist: Dict, completed: List[ChecklistItemMatch],
                              partial: List[ChecklistItemMatch]) -> str:
        """Generate audio confirmation."""
        parts = [f"Checklist '{checklist['name']}' updated"]
        
        if completed:
            parts.append(f"{len(completed)} items marked complete")
        
        if partial:
            parts.append(f"{len(partial)} items need clarification")
        
        if not completed and not partial:
            parts.append("No items were recognized")
        
        return ". ".join(parts)