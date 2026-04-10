"""
CAT-J: Operational Excellence Features (F46-F50)
- F46: Offline Mode — Voice Queue
- F47: Shift Handover Intelligence
- F48: Geo-Fenced Auto Check-In
- F49: Team Coordination — 'Who's Nearest?' Tool
- F50: Full Audit Trail Export — Tamper-Evident
"""

from typing import Dict, Any, List
from datetime import datetime, timedelta
import hashlib


class OperationalExcellenceService:
    """Operational excellence features."""
    
    def __init__(self, db_client, storage_service, location_service, 
                 notification_service, memory_manager):
        self.db = db_client
        self.storage = storage_service
        self.location = location_service
        self.notifications = notification_service
        self.memory = memory_manager
    
    # F46: Offline Mode — Voice Queue
    async def queue_offline_command(self, audio_data: bytes, command_text: str,
                                   user_id: str, org_id: str) -> Dict:
        """Queue voice command for offline processing."""
        # Store locally (would use AsyncStorage on mobile)
        queue_item = {
            "id": f"queue-{datetime.now().timestamp()}",
            "audio_data": audio_data,
            "command_text": command_text,
            "user_id": user_id,
            "org_id": org_id,
            "queued_at": datetime.now().isoformat(),
            "status": "pending"
        }
        
        # Store in local queue
        await self._store_in_local_queue(queue_item)
        
        return {
            "success": True,
            "queue_id": queue_item["id"],
            "position": await self._get_queue_position(user_id),
            "message": "Command queued. Will process when online."
        }
    
    async def process_offline_queue(self, user_id: str, org_id: str) -> Dict:
        """Process queued commands when back online."""
        # Get pending items
        queue = await self._get_local_queue(user_id)
        
        processed = []
        for item in queue:
            # Process via standard pipeline
            result = await self._process_queued_item(item)
            processed.append({
                "queue_id": item["id"],
                "result": result,
                "status": "processed"
            })
            
            # Remove from queue
            await self._remove_from_queue(item["id"])
        
        return {
            "success": True,
            "processed_count": len(processed),
            "processed_items": processed
        }
    
    # F47: Shift Handover Intelligence
    async def generate_handover_brief(self, shift_end: datetime, outgoing_user: str,
                                     incoming_user: str, org_id: str) -> Dict:
        """Generate end-of-shift handover brief."""
        # Query shift's work
        shift_work = await self._get_shift_work(outgoing_user, shift_end, org_id)
        
        # Synthesize brief
        brief = {
            "open_critical": shift_work.get('open_critical', []),
            "completed": shift_work.get('completed', []),
            "escalated": shift_work.get('escalated', []),
            "things_to_watch": shift_work.get('alerts', [])
        }
        
        # Push to Notion
        notion_page = await self._push_handover_to_notion(brief, outgoing_user, 
                                                         incoming_user, org_id)
        
        # Notify incoming lead
        await self.notifications.send_push(
            user_id=incoming_user,
            title="📋 Shift Handover Ready",
            body=f"Brief from {outgoing_user}: {len(brief['open_critical'])} critical tickets open",
            data={"notion_url": notion_page.get('url'), "action": "view_handover"}
        )
        
        return {
            "brief": brief,
            "notion_url": notion_page.get('url'),
            "notified": True
        }
    
    # F48: Geo-Fenced Auto Check-In
    async def handle_geofence_entry(self, user_id: str, location: Dict,
                                   org_id: str) -> Dict:
        """Handle auto check-in when entering property geofence."""
        # Find property at location
        property_id = await self._find_property_at_location(location, org_id)
        
        if not property_id:
            return {"success": False, "error": "No property at this location"}
        
        # Auto-start session
        session = await self._auto_start_session(user_id, property_id, org_id)
        
        # Fetch property briefing
        briefing = await self._fetch_property_briefing(property_id, org_id)
        
        # Log arrival
        await self._log_arrival(user_id, property_id, location, org_id)
        
        # Send briefing notification
        await self.notifications.send_push(
            user_id=user_id,
            title=f"📍 Arrived at {briefing.get('property_name')}",
            body=f"{briefing.get('open_tickets')} open tickets, {briefing.get('pending_checklists')} pending checklists",
            data={"property_id": property_id, "action": "view_briefing"}
        )
        
        return {
            "success": True,
            "property_id": property_id,
            "session_id": session.get('session_id'),
            "briefing": briefing
        }
    
    # F49: Team Coordination — 'Who's Nearest?' Tool
    async def find_nearest_available(self, property_id: str, org_id: str) -> Dict:
        """Find nearest available team member to property."""
        # Get property location
        prop_location = await self._get_property_location(property_id, org_id)
        
        # Get available team members
        available = await self._get_available_team_members(org_id)
        
        # Calculate distances
        ranked = []
        for member in available:
            distance = self._calculate_distance(prop_location, member['location'])
            eta = await self._calculate_eta(distance)
            
            ranked.append({
                "user_id": member['id'],
                "name": member['name'],
                "distance_km": distance,
                "eta_minutes": eta,
                "current_task": member.get('current_task')
            })
        
        # Sort by distance
        ranked.sort(key=lambda x: x['distance_km'])
        
        return {
            "property_id": property_id,
            "nearest_available": ranked[:5],
            "top_recommendation": ranked[0] if ranked else None
        }
    
    # F50: Full Audit Trail Export
    async def export_audit_trail(self, property_id: str, start_date: str, 
                                end_date: str, org_id: str) -> Dict:
        """Export tamper-evident audit trail."""
        # Query audit log
        query = """
            SELECT 
                al.timestamp,
                al.actor,
                al.action,
                al.entity_type,
                al.entity_id,
                al.metadata
            FROM audit_log al
            WHERE al.org_id = $1
            AND (al.entity_id = $2 OR al.metadata->>'property_id' = $2)
            AND al.timestamp BETWEEN $3 AND $4
            ORDER BY al.timestamp ASC
        """
        results = await self.db.fetch(query, org_id, property_id, start_date, end_date)
        
        # Generate PDF
        audit_data = {
            "property_id": property_id,
            "date_range": f"{start_date} to {end_date}",
            "entries": [dict(r) for r in results],
            "generated_at": datetime.now().isoformat()
        }
        
        pdf_data = await self._generate_audit_pdf(audit_data)
        
        # Compute SHA256 hash
        pdf_hash = hashlib.sha256(pdf_data).hexdigest()
        
        # Store hash in DB
        await self.db.execute("""
            INSERT INTO audit_hashes (property_id, date_range, hash, generated_at)
            VALUES ($1, $2, $3, NOW())
        """, property_id, f"{start_date}:{end_date}", pdf_hash)
        
        # Upload PDF
        pdf_url = await self.storage.upload_audit_pdf(pdf_data, property_id, 
                                                      start_date, end_date)
        
        return {
            "pdf_url": pdf_url,
            "sha256_hash": pdf_hash,
            "entry_count": len(results),
            "date_range": f"{start_date} to {end_date}",
            "verification": "Hash stored in audit_hashes table for verification"
        }
    
    # Helper methods
    async def _store_in_local_queue(self, item: Dict):
        """Store in local queue."""
        pass
    
    async def _get_queue_position(self, user_id: str) -> int:
        """Get queue position."""
        return 1
    
    async def _get_local_queue(self, user_id: str) -> List:
        """Get local queue."""
        return []
    
    async def _process_queued_item(self, item: Dict) -> Dict:
        """Process queued item."""
        return {}
    
    async def _remove_from_queue(self, item_id: str):
        """Remove from queue."""
        pass
    
    async def _get_shift_work(self, user_id: str, shift_end: datetime, org_id: str) -> Dict:
        """Get shift work summary."""
        return {}
    
    async def _push_handover_to_notion(self, brief: Dict, outgoing: str, 
                                      incoming: str, org_id: str) -> Dict:
        """Push handover to Notion."""
        return {"url": "notion-url"}
    
    async def _find_property_at_location(self, location: Dict, org_id: str) -> str:
        """Find property at GPS location."""
        return "prop-001"
    
    async def _auto_start_session(self, user_id: str, property_id: str, org_id: str) -> Dict:
        """Auto-start session."""
        return {"session_id": "session-001"}
    
    async def _fetch_property_briefing(self, property_id: str, org_id: str) -> Dict:
        """Fetch property briefing."""
        return {"property_name": "Tower A", "open_tickets": 4, "pending_checklists": 2}
    
    async def _log_arrival(self, user_id: str, property_id: str, location: Dict, org_id: str):
        """Log arrival."""
        pass
    
    async def _get_property_location(self, property_id: str, org_id: str) -> Dict:
        """Get property location."""
        return {"lat": 0, "lng": 0}
    
    async def _get_available_team_members(self, org_id: str) -> List:
        """Get available team members."""
        return []
    
    def _calculate_distance(self, loc1: Dict, loc2: Dict) -> float:
        """Calculate distance between two points."""
        return 0.0
    
    async def _calculate_eta(self, distance_km: float) -> int:
        """Calculate ETA in minutes."""
        return int(distance_km * 2)  # Rough estimate
    
    async def _generate_audit_pdf(self, audit_data: Dict) -> bytes:
        """Generate audit PDF."""
        return b""