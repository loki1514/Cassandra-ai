"""
F07: AR Pokémon-Go-Style Property Scan (AI OCR)
Mobile camera scans assets, AI OCR reads tags, AR overlay shows checklist status.
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult


class ChecklistStatus(str, Enum):
    """Checklist item status for AR overlay."""
    CLEAR = "clear"      # Green - all good
    DUE_SOON = "due_soon"  # Amber - due within 7 days
    OVERDUE = "overdue"   # Red - overdue


@dataclass
class AROverlayData:
    """AR overlay data for an asset."""
    asset_id: str
    asset_name: str
    checklist_status: ChecklistStatus
    checklist_name: str
    items_total: int
    items_complete: int
    next_due_date: Optional[str]
    overlay_color: str
    overlay_text: str


class ARInspectionProcessor:
    """
    F07: AR Pokémon-Go-Style Property Scan
    
    Trigger: Point camera at asset tag → Cassandra overlays checklist status
    
    Flow: Camera → OCR → Asset lookup → Checklist status → AR overlay
    """
    
    def __init__(self, ocr_service, db_client, storage_service, memory_manager):
        self.ocr = ocr_service
        self.db = db_client
        self.storage = storage_service
        self.memory_manager = memory_manager
        
    async def process_asset_scan(self, image_data: bytes, gps_location: Dict[str, float],
                                 org_id: str, user_id: str) -> Dict[str, Any]:
        """
        Process asset scan and return AR overlay data.
        
        Args:
            image_data: Camera image bytes
            gps_location: {lat, lng} GPS coordinates
            org_id: Organization ID
            user_id: User performing scan
            
        Returns:
            AR overlay data with checklist status
        """
        # Step 1: OCR to extract asset tag/serial
        ocr_result = await self.ocr.extract_text(image_data)
        
        if not ocr_result.get('text'):
            return {
                "success": False,
                "error": "No text detected",
                "message": "Please ensure the asset tag is clearly visible"
            }
        
        # Step 2: Parse asset identifier from OCR
        asset_id = self._parse_asset_identifier(ocr_result['text'])
        
        if not asset_id:
            return {
                "success": False,
                "error": "Could not identify asset",
                "ocr_text": ocr_result['text'],
                "message": "Could not read asset tag. Please try again."
            }
        
        # Step 3: Lookup asset in DB
        asset = await self._find_asset(asset_id, org_id)
        
        if not asset:
            return {
                "success": False,
                "error": f"Asset not found: {asset_id}",
                "message": "This asset is not in the system"
            }
        
        # Step 4: Get checklist status for asset
        checklist_status = await self._get_checklist_status(asset['id'], org_id)
        
        # Step 5: Store inspection photo
        photo_url = await self.storage.upload_inspection_photo(
            image_data,
            asset_id=asset['id'],
            org_id=org_id,
            user_id=user_id,
            gps_location=gps_location
        )
        
        # Step 6: Log inspection event
        await self._log_inspection_event(asset, gps_location, photo_url, org_id, user_id)
        
        # Step 7: Return AR overlay data
        overlay = self._generate_overlay(asset, checklist_status)
        
        return {
            "success": True,
            "asset": {
                "id": asset['id'],
                "name": asset['name'],
                "type": asset['type'],
                "location": asset['location']
            },
            "ar_overlay": overlay,
            "photo_url": photo_url,
            "gps_location": gps_location,
            "actions": self._get_available_actions(checklist_status)
        }
    
    async def complete_checklist_item_via_ar(self, asset_id: str, item_id: str,
                                             photo_evidence: bytes, user_id: str,
                                             org_id: str) -> Dict[str, Any]:
        """
        Complete checklist item via AR tap-to-complete.
        
        Returns:
            Completion confirmation
        """
        # Step 0: Fetch dual-read context for prior verbal defect reports
        defect_context = await fetch_full_context(
            query=f"defect report asset {asset_id}",
            org_id=org_id,
            data_hints=["assets", "checklists"],
            top_k=3
        )

        # Step 1: Store completion photo
        photo_url = await self.storage.upload_completion_photo(
            photo_evidence,
            asset_id=asset_id,
            item_id=item_id,
            org_id=org_id,
            user_id=user_id
        )
        
        # Step 2: Mark item complete
        query = """
            UPDATE checklist_items
            SET completed = true,
                completed_at = NOW(),
                completed_by = $1,
                completion_evidence = $2,
                completion_method = 'ar_tap'
            WHERE id = $3
            RETURNING name
        """
        result = await self.db.fetchrow(query, user_id, photo_url, item_id)
        
        if not result:
            return {
                "success": False,
                "error": "Item not found"
            }
        
        # Step 3: Log to Supermemory
        await self.memory_manager.add_memory(
            content=f"Checklist item '{result['name']}' completed via AR scan",
            memory_type="ASSET_INSPECTION_COMPLETE",
            org_id=org_id,
            entity_id=asset_id,
            metadata={
                "item_id": item_id,
                "completed_by": user_id,
                "method": "ar_tap",
                "photo_url": photo_url
            },
            confidence=1.0
        )
        
        # Step 4: Run defect detection on photo, enriched with prior defect context
        defect_result = await self._detect_defects(photo_evidence, asset_id, org_id, defect_context)

        return {
            "success": True,
            "item_name": result['name'],
            "photo_url": photo_url,
            "defect_detected": defect_result.get('defect_found', False),
            "auto_ticket_created": defect_result.get('ticket_created', False),
            "ticket_id": defect_result.get('ticket_id')
        }
    
    def _parse_asset_identifier(self, ocr_text: str) -> Optional[str]:
        """Parse asset identifier from OCR text."""
        import re
        
        # Common asset tag patterns
        patterns = [
            r'ASSET[\s\-:]+([A-Z0-9\-]+)',
            r'SN[\s\-:]+([A-Z0-9\-]+)',
            r'S/N[\s\-:]+([A-Z0-9\-]+)',
            r'SERIAL[\s\-:]+([A-Z0-9\-]+)',
            r'ID[\s\-:]+([A-Z0-9\-]+)',
            r'([A-Z]{2,3}\-[0-9]{3,6})',  # Generic pattern like AC-12345
            r'([0-9]{4,8})',  # Numeric serial
        ]
        
        for pattern in patterns:
            match = re.search(pattern, ocr_text.upper())
            if match:
                return match.group(1).strip()
        
        return None
    
    async def _find_asset(self, asset_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        """Find asset by identifier."""
        query = """
            SELECT id, name, type, location, property_id, install_date
            FROM assets
            WHERE (asset_tag = $1 OR serial_number = $1 OR id = $1)
            AND org_id = $2
            LIMIT 1
        """
        result = await self.db.fetchrow(query, asset_id, org_id)
        return dict(result) if result else None
    
    async def _get_checklist_status(self, asset_id: str, org_id: str) -> Dict[str, Any]:
        """Get checklist status for asset."""
        query = """
            SELECT 
                c.id as checklist_id,
                c.name as checklist_name,
                c.next_due_date,
                COUNT(ci.id) as total_items,
                COUNT(CASE WHEN ci.completed THEN 1 END) as completed_items,
                MAX(ci.completed_at) as last_completed
            FROM checklists c
            JOIN checklist_items ci ON ci.checklist_id = c.id
            WHERE c.asset_id = $1 AND c.org_id = $2
            AND c.status = 'active'
            GROUP BY c.id, c.name, c.next_due_date
            ORDER BY c.next_due_date ASC
            LIMIT 1
        """
        result = await self.db.fetchrow(query, asset_id, org_id)
        
        if result:
            return {
                'checklist_id': result['checklist_id'],
                'checklist_name': result['checklist_name'],
                'next_due_date': result['next_due_date'],
                'total_items': result['total_items'],
                'completed_items': result['completed_items'],
                'last_completed': result['last_completed']
            }
        return None
    
    async def _log_inspection_event(self, asset: Dict, gps: Dict, 
                                    photo_url: str, org_id: str, user_id: str):
        """Log inspection event to Supermemory."""
        await self.memory_manager.add_memory(
            content=f"Asset '{asset['name']}' inspected via AR scan",
            memory_type="ASSET_INSPECTION",
            org_id=org_id,
            entity_id=asset['id'],
            metadata={
                "inspector_id": user_id,
                "gps_location": gps,
                "photo_url": photo_url,
                "asset_type": asset['type'],
                "location": asset['location']
            },
            confidence=1.0
        )
    
    def _generate_overlay(self, asset: Dict, checklist_status: Optional[Dict]) -> AROverlayData:
        """Generate AR overlay data."""
        if not checklist_status:
            return AROverlayData(
                asset_id=asset['id'],
                asset_name=asset['name'],
                checklist_status=ChecklistStatus.CLEAR,
                checklist_name="No checklist",
                items_total=0,
                items_complete=0,
                next_due_date=None,
                overlay_color="#808080",  # Gray
                overlay_text="No checklist assigned"
            )
        
        # Determine status
        from datetime import datetime, timedelta
        
        next_due = checklist_status.get('next_due_date')
        if next_due:
            if isinstance(next_due, str):
                next_due = datetime.fromisoformat(next_due.replace('Z', '+00:00'))
            
            days_until_due = (next_due - datetime.now()).days
            
            if days_until_due < 0:
                status = ChecklistStatus.OVERDUE
                color = "#FF4444"  # Red
                text = f"OVERDUE by {abs(days_until_due)} days"
            elif days_until_due <= 7:
                status = ChecklistStatus.DUE_SOON
                color = "#FFAA00"  # Amber
                text = f"Due in {days_until_due} days"
            else:
                status = ChecklistStatus.CLEAR
                color = "#44FF44"  # Green
                text = f"Clear - Due {next_due.strftime('%b %d')}"
        else:
            status = ChecklistStatus.CLEAR
            color = "#44FF44"
            text = "Up to date"
        
        completion = checklist_status.get('completed_items', 0)
        total = checklist_status.get('total_items', 1)
        
        return AROverlayData(
            asset_id=asset['id'],
            asset_name=asset['name'],
            checklist_status=status,
            checklist_name=checklist_status.get('checklist_name', 'Checklist'),
            items_total=total,
            items_complete=completion,
            next_due_date=checklist_status.get('next_due_date'),
            overlay_color=color,
            overlay_text=f"{text} ({completion}/{total} complete)"
        )
    
    def _get_available_actions(self, checklist_status: Optional[Dict]) -> List[Dict]:
        """Get available actions for AR overlay."""
        actions = []
        
        if checklist_status:
            actions.append({
                "id": "view_checklist",
                "label": "View Checklist",
                "icon": "list"
            })
            
            if checklist_status.get('completed_items', 0) < checklist_status.get('total_items', 0):
                actions.append({
                    "id": "complete_items",
                    "label": "Complete Items",
                    "icon": "check"
                })
        
        actions.append({
            "id": "view_history",
            "label": "View History",
            "icon": "history"
        })
        
        return actions
    
    async def _detect_defects(self, image_data: bytes, asset_id: str,
                             org_id: str,
                             dual_context: Optional[ContextResult] = None) -> Dict[str, Any]:
        """Detect defects in inspection photo using vision AI + LLM analysis."""
        try:
            # Step 1: Run OCR on the photo to get text
            ocr_text = ""
            try:
                ocr_result = await self.ocr.extract_text(image_data)
                ocr_text = ocr_result.get('text', '')
            except Exception:
                ocr_text = ""

            # Step 2: Build prior defect context from dual-read
            prior_defects = ""
            if dual_context and dual_context.memory_chunks:
                prior_defects = "Prior defect mentions:\n" + "\n".join(
                    f"  - {c.get('content', '')}" for c in dual_context.memory_chunks[:3]
                )
            elif dual_context and dual_context.supabase_rows:
                prior_defects = "Prior Supabase data:\n" + "\n".join(
                    f"  - {row}" for row in dual_context.supabase_rows[:3]
                )
            else:
                prior_defects = "No prior defect history found."

            # Step 3: LLM defect analysis
            defect_indicators = [
                "crack", "fracture", "corrosion", "rust", "leak", "damage",
                "missing", "broken", "bent", "loose", "worn", "tear",
                "discoloration", "stain", "deformation", "wear", "fault",
            ]
            indicator_str = ", ".join(defect_indicators)

            prompt = f"""
You are a defect detection analyst. Analyze the following OCR text extracted from an asset inspection photo.

OCR Text from photo:
{ocr_text if ocr_text else "(no readable text found in image)"}

{prior_defects}

Look for these defect indicators: {indicator_str}

Also check for:
1. Physical damage signs (cracks, bends, corrosion)
2. Missing components or fixtures
3. Unusual wear patterns
4. Leakage or fluid stains
5. Any text mentioning defects, damage, or repair needed

Respond with JSON:
{{
  "defect_found": true/false,
  "defect_type": "description of defect type or null",
  "severity": "low/medium/high/critical or null",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation"
}}
"""
            response = await self.ocr.llm.chat.completions.create(
                model="claude-sonnet-4",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.1,
            )
            raw = response.choices[0].message.content
            import json as _json
            data = _json.loads(raw)

            defect_found = data.get("defect_found", False)
            ticket_created = False
            ticket_id = None

            # Auto-create defect ticket if defect found with high confidence
            if defect_found and data.get("confidence", 0) >= 0.75:
                try:
                    client = get_supabase_client("service")
                    ticket_data = {
                        "title": f"Defect: {data.get('defect_type', 'Unknown')} on asset {asset_id}",
                        "description": f"Severity: {data.get('severity', 'Unknown')}\nReasoning: {data.get('reasoning', '')}\nOCR Text: {ocr_text}",
                        "org_id": org_id,
                        "priority": "high" if data.get("severity") in ("high", "critical") else "medium",
                        "source": "ar_defect_detection",
                        "asset_id": asset_id,
                        "status": "open",
                    }
                    result = client.table("tickets").insert(ticket_data).execute()
                    if result.data:
                        ticket_id = result.data[0].get("id")
                        ticket_created = bool(ticket_id)
                except Exception:
                    pass

            return {
                "defect_found": defect_found,
                "defect_type": data.get("defect_type"),
                "severity": data.get("severity"),
                "confidence": data.get("confidence", 0),
                "reasoning": data.get("reasoning", ""),
                "ticket_created": ticket_created,
                "ticket_id": ticket_id,
            }
        except Exception:
            return {
                "defect_found": False,
                "ticket_created": False,
                "ticket_id": None,
            }