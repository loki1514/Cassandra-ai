"""
F10: Photo Evidence Capture on Completion
Capture photos on checklist completion, OCR for defects, auto-raise tickets.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class DefectDetection:
    """Defect detection result."""
    defect_found: bool
    defect_type: Optional[str]
    confidence: float
    bounding_boxes: list
    severity: str


class PhotoEvidenceProcessor:
    """
    F10: Photo Evidence Capture on Completion
    
    Trigger: "Cassandra, I'm completing the roof membrane inspection — taking photo now"
    
    Flow: Camera → Storage → OCR defect detection → Link to checklist → Auto-ticket if defect
    """
    
    def __init__(self, storage_service, vision_client, db_client, 
                 memory_manager, ticket_tool):
        self.storage = storage_service
        self.vision = vision_client
        self.db = db_client
        self.memory_manager = memory_manager
        self.ticket_tool = ticket_tool
        
    async def capture_completion_photo(self, image_data: bytes, checklist_item_id: str,
                                       user_id: str, org_id: str) -> Dict[str, Any]:
        """
        Process completion photo with defect detection.
        
        Returns:
            Photo storage result with defect detection
        """
        # Step 1: Get checklist item info
        item_info = await self._get_checklist_item(checklist_item_id, org_id)
        
        if not item_info:
            return {
                "success": False,
                "error": "Checklist item not found"
            }
        
        # Step 2: Upload to storage
        photo_url = await self.storage.upload_completion_photo(
            image_data,
            checklist_item_id=checklist_item_id,
            org_id=org_id,
            user_id=user_id
        )
        
        # Step 3: Run defect detection
        defect_result = await self._detect_defects(image_data)
        
        # Step 4: Link photo to checklist item
        await self._link_photo_to_item(checklist_item_id, photo_url, defect_result)
        
        # Step 5: Auto-raise ticket if defect found
        auto_ticket = None
        if defect_result.defect_found and defect_result.confidence > 0.7:
            auto_ticket = await self._create_defect_ticket(
                item_info, defect_result, photo_url, org_id
            )
        
        # Step 6: Log to Supermemory
        await self._log_photo_evidence(item_info, photo_url, defect_result, org_id)
        
        return {
            "success": True,
            "photo_url": photo_url,
            "checklist_item_id": checklist_item_id,
            "defect_detected": defect_result.defect_found,
            "defect_type": defect_result.defect_type,
            "defect_confidence": defect_result.confidence,
            "auto_ticket_created": auto_ticket is not None,
            "auto_ticket_id": auto_ticket.get('ticket_id') if auto_ticket else None
        }
    
    async def _get_checklist_item(self, item_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        """Get checklist item details."""
        query = """
            SELECT ci.id, ci.name, ci.checklist_id, c.name as checklist_name,
                   c.property_id, c.asset_id
            FROM checklist_items ci
            JOIN checklists c ON c.id = ci.checklist_id
            WHERE ci.id = $1 AND c.org_id = $2
        """
        result = await self.db.fetchrow(query, item_id, org_id)
        return dict(result) if result else None
    
    async def _detect_defects(self, image_data: bytes) -> DefectDetection:
        """Detect defects in image using vision AI."""
        # Call Google Vision or similar
        vision_result = await self.vision.detect_defects(image_data)
        
        # Parse result
        defects = vision_result.get('defects', [])
        
        if defects:
            # Get highest confidence defect
            top_defect = max(defects, key=lambda d: d.get('confidence', 0))
            
            return DefectDetection(
                defect_found=True,
                defect_type=top_defect.get('type'),
                confidence=top_defect.get('confidence', 0),
                bounding_boxes=top_defect.get('bounding_boxes', []),
                severity=top_defect.get('severity', 'medium')
            )
        
        return DefectDetection(
            defect_found=False,
            defect_type=None,
            confidence=0,
            bounding_boxes=[],
            severity="none"
        )
    
    async def _link_photo_to_item(self, item_id: str, photo_url: str,
                                 defect_result: DefectDetection):
        """Link photo to checklist item."""
        query = """
            UPDATE checklist_items
            SET completion_evidence = $1,
                photo_url = $1,
                defect_detected = $2,
                defect_type = $3,
                defect_confidence = $4
            WHERE id = $5
        """
        await self.db.execute(
            query,
            photo_url,
            defect_result.defect_found,
            defect_result.defect_type,
            defect_result.confidence,
            item_id
        )
    
    async def _create_defect_ticket(self, item_info: Dict, defect: DefectDetection,
                                   photo_url: str, org_id: str) -> Dict[str, Any]:
        """Auto-create ticket for detected defect."""
        ticket_data = {
            "title": f"Defect detected: {item_info['checklist_name']} - {item_info['name']}",
            "description": f"""
Auto-detected defect during checklist completion.

Checklist: {item_info['checklist_name']}
Item: {item_info['name']}
Defect Type: {defect.defect_type}
Confidence: {defect.confidence:.1%}
Severity: {defect.severity}

Photo evidence: {photo_url}

This ticket was automatically created by Cassandra's defect detection system.
            """.strip(),
            "priority": "high" if defect.severity == "high" else "medium",
            "category": "defect",
            "source": "auto_defect_detection",
            "org_id": org_id,
            "property_id": item_info.get('property_id'),
            "asset_id": item_info.get('asset_id'),
            "photo_evidence": photo_url
        }
        
        return await self.ticket_tool.create_ticket(ticket_data)
    
    async def _log_photo_evidence(self, item_info: Dict, photo_url: str,
                                 defect: DefectDetection, org_id: str):
        """Log photo evidence to Supermemory."""
        await self.memory_manager.add_memory(
            content=f"Photo evidence captured for '{item_info['name']}'" + 
                   (f" - Defect detected: {defect.defect_type}" if defect.defect_found else ""),
            memory_type="PHOTO_EVIDENCE",
            org_id=org_id,
            entity_id=item_info['checklist_id'],
            metadata={
                "item_id": item_info['id'],
                "item_name": item_info['name'],
                "photo_url": photo_url,
                "defect_found": defect.defect_found,
                "defect_type": defect.defect_type,
                "defect_confidence": defect.confidence,
                "timestamp": datetime.now().isoformat()
            },
            confidence=1.0
        )