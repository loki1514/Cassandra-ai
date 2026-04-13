"""
CAT-H: Integrations & Ecosystem (F36-F40)
- F36: Notion Integration — Living Knowledge Base
- F37: WhatsApp / Telegram Command Interface
- F38: Google Calendar — Meeting Auto-Detection
- F39: IoT Sensor Integration — Real-Time Asset Monitoring
- F40: ERP / SAP Integration — Ticket-to-PO Automation
"""

import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult
from cassandra.supabase import get_supabase_client


class IntegrationHub:
    """Integration and ecosystem features."""

    def __init__(self, notion_client, whatsapp_client, calendar_client,
                 iot_client, erp_client, db_client, notification_service):
        self.notion = notion_client
        self.whatsapp = whatsapp_client
        self.calendar = calendar_client
        self.iot = iot_client
        self.erp = erp_client
        self.db = db_client
        self.notifications = notification_service

    # F36: Notion Integration
    async def push_to_notion(self, data_type: str, data: Dict, org_id: str) -> Dict:
        """Push data to Notion knowledge base."""
        # F36: Enrich with dual-read context before pushing to Notion
        context = await fetch_full_context(
            query=f"recent {data_type} data, related tickets, and discussions",
            org_id=org_id,
            data_hints=["tickets", "users", "checklists", "calendar_events"],
            top_k=5,
        )

        databases = {
            "meeting_notes": "meeting-notes-db-id",
            "failure_log": "failure-log-db-id",
            "reports": "reports-db-id",
            "improvement_proposals": "proposals-db-id"
        }

        db_id = databases.get(data_type)
        if not db_id:
            return {"success": False, "error": "Unknown data type"}

        # Merge dual-read context into Notion page content
        enriched_data = dict(data)
        if context.memory_chunks:
            enriched_data["_memory_notes"] = [
                c.get("content", "") for c in context.memory_chunks
            ]
        if context.supabase_rows:
            enriched_data["_related_records"] = context.supabase_rows[:5]

        page_id = await self.notion.create_page(
            database_id=db_id,
            properties=enriched_data
        )

        return {
            "success": True,
            "notion_page_id": page_id,
            "context_sources": context.sources_queried,
        }

    # F37: WhatsApp/Telegram Interface
    async def handle_whatsapp_message(self, message: str, from_number: str,
                                      org_id: str) -> Dict:
        """Handle WhatsApp command message."""
        # F37: Fetch dual-read context for richer responses
        context = await fetch_full_context(
            query=f"whatsapp command, related tickets and checklists for {from_number}",
            org_id=org_id,
            data_hints=["tickets", "users", "checklists"],
            top_k=5,
        )

        # Parse command
        command = self._parse_whatsapp_command(message)

        if command['action'] == 'status':
            # Query ticket status via Supabase
            result = await self._query_ticket_status(command['ticket_ref'], org_id)
            response = result.get('response_text', 'Ticket not found')

        elif command['action'] == 'create':
            # Create ticket
            result = await self._create_ticket_from_whatsapp(command, from_number, org_id)
            response = f"Ticket created: {result.get('ticket_number')}"

        elif command['action'] == 'checklist':
            # Complete checklist item via Supabase
            result = await self._complete_checklist_item(command, from_number, org_id)
            response = "Checklist updated"

        else:
            response = "Sorry, I didn't understand. Try: status [ticket], create [task], or checklist [item]"

        # Send response
        await self.whatsapp.send_message(from_number, response)

        return {
            "success": True,
            "response": response,
            "context_sources": context.sources_queried,
        }

    # F38: Google Calendar Integration
    async def process_calendar_event(self, event: Dict, org_id: str) -> Dict:
        """Process calendar event for meeting briefing."""
        # F38: Fetch dual-read context for pre-meeting briefing
        context = await fetch_full_context(
            query=f"meeting briefing, open tickets, pending checklists for calendar event",
            org_id=org_id,
            data_hints=["tickets", "checklists", "calendar_events", "users"],
            top_k=5,
        )

        # Extract property from event location
        property_id = await self._extract_property_from_location(event.get('location', ''))

        if not property_id:
            return {"success": False, "error": "No property found in location"}

        # Fetch property context
        context_data = await self._fetch_property_context(property_id, org_id)

        # Build briefing body with dual-read context
        briefing_body = (
            f"{context_data['open_tickets']} open tickets, "
            f"{context_data['overdue_checklists']} overdue checklists"
        )
        if context.memory_chunks:
            memory_snippet = " | ".join(
                c.get("content", "")[:80] for c in context.memory_chunks[:2]
            )
            briefing_body += f" | Memory: {memory_snippet}"

        # Send briefing 5 min before
        meeting_time = datetime.fromisoformat(event['start_time'].replace('Z', '+00:00'))
        briefing_time = meeting_time - timedelta(minutes=5)

        # Schedule notification
        await self.notifications.schedule_push(
            user_id=event['organizer'],
            title=f"Briefing: {event['summary']}",
            body=briefing_body,
            scheduled_time=briefing_time,
            data={"property_id": property_id, "action": "view_briefing"}
        )

        return {
            "success": True,
            "briefing_scheduled": True,
            "context_sources": context.sources_queried,
        }

    # F39: IoT Sensor Integration
    async def handle_iot_webhook(self, sensor_data: Dict, org_id: str) -> Dict:
        """Handle IoT sensor webhook."""
        # F39: Fetch dual-read context for alert enrichment
        context = await fetch_full_context(
            query=f"IoT sensor alert, related asset issues and maintenance history",
            org_id=org_id,
            data_hints=["tickets", "assets", "energy_readings"],
            top_k=5,
        )

        sensor_id = sensor_data.get('sensor_id')
        reading = sensor_data.get('reading', 0)
        threshold = sensor_data.get('threshold', 0)

        # Check threshold
        if reading > threshold:
            # Create alert ticket
            ticket = await self._create_sensor_alert_ticket(sensor_data, org_id)

            # F39: Store IoT reading in Supabase
            await self._store_iot_reading(sensor_data, org_id)

            # Log to Supermemory with dual-read context
            await self._log_sensor_event(sensor_data, org_id, context)

            # Notify on-call
            await self._notify_on_call(sensor_data, ticket, org_id)

            return {
                "success": True,
                "alert_created": True,
                "ticket_id": ticket.get('ticket_id'),
                "context_sources": context.sources_queried,
            }

        return {"success": True, "alert_created": False}

    # F40: ERP/SAP Integration
    async def create_purchase_order(self, ticket_id: str, org_id: str) -> Dict:
        """Create purchase order in ERP from ticket."""
        # F40: Fetch dual-read context for PO enrichment
        context = await fetch_full_context(
            query=f"purchase order, vendor information, and budget for ticket {ticket_id}",
            org_id=org_id,
            data_hints=["tickets", "vendors", "budgets"],
            top_k=5,
        )

        # Get ticket details via Supabase
        ticket = await self._get_ticket(ticket_id, org_id)

        if not ticket:
            return {"success": False, "error": "Ticket not found"}

        # Check cost threshold
        if ticket.get('estimated_cost', 0) < 50000:
            return {"success": False, "error": "Below PO threshold"}

        # Generate PO draft — merge dual-read context
        vendor_info = next(
            (r for r in context.supabase_rows if r.get("_source_table") == "vendors"),
            {},
        )
        po_data = {
            "vendor_id": ticket.get('vendor_id'),
            "vendor_name": vendor_info.get("name", ticket.get("vendor_name")),
            "amount": ticket.get('estimated_cost'),
            "description": ticket.get('title'),
            "reference_ticket": ticket_id,
            "org_id": org_id
        }

        # Push to ERP
        po_result = await self.erp.create_purchase_order(po_data)

        # Link to ticket
        await self._link_po_to_ticket(ticket_id, po_result.get('po_number'))

        return {
            "success": True,
            "po_number": po_result.get('po_number'),
            "ticket_id": ticket_id,
            "approval_workflow": po_result.get('workflow_id'),
            "context_sources": context.sources_queried,
        }

    # ------------------------------------------------------------------
    # Helper methods — now implemented with Supabase
    # ------------------------------------------------------------------

    def _parse_whatsapp_command(self, message: str) -> Dict:
        """Parse WhatsApp command."""
        parts = message.lower().split()
        return {
            "action": parts[0] if parts else "unknown",
            "args": parts[1:] if len(parts) > 1 else []
        }

    async def _query_ticket_status(self, ticket_ref: str, org_id: str) -> Dict:
        """
        Query ticket status from Supabase.

        SECURITY: .eq("org_id", org_id) enforced on every query.
        """
        client = get_supabase_client("service")
        response = (
            client.table("tickets")
            .select("id, ticket_number, status, title, category, completed_at, deadline")
            .eq("org_id", org_id)
            .or_(f"id.eq.{ticket_ref},ticket_number.eq.{ticket_ref}")
            .execute()
        )
        if not response.data:
            return {"response_text": "Ticket not found", "status": None}
        row = response.data[0]
        status_text = row.get("status", "unknown")
        response_text = (
            f"[{row.get('ticket_number', row['id'][:8])}] "
            f"{row.get('title', 'No title')} — Status: {status_text}"
        )
        return {"response_text": response_text, "status": status_text, "ticket": row}

    async def _create_ticket_from_whatsapp(self, command: Dict, from_number: str,
                                          org_id: str) -> Dict:
        """
        Create ticket from WhatsApp command.

        SECURITY: org_id is enforced for user lookup and ticket creation.
        """
        client = get_supabase_client("service")

        # Extract task description from command args
        task_description = " ".join(command.get("args", []))
        if not task_description:
            return {"success": False, "error": "No task description provided"}

        # Look up user by phone number
        user_response = (
            client.table("users")
            .select("id, name")
            .eq("org_id", org_id)
            .eq("phone", from_number)
            .limit(1)
            .execute()
        )

        user_id = user_response.data[0]["id"] if user_response.data else None
        user_name = user_response.data[0]["name"] if user_response.data else f"WhatsApp {from_number}"

        # Create ticket via Supabase
        import uuid
        from datetime import datetime, timedelta

        ticket_data = {
            "id": str(uuid.uuid4()),
            "org_id": org_id,
            "title": task_description[:100],  # Limit title length
            "description": f"Created via WhatsApp by {user_name}\n\n{task_description}",
            "status": "open",
            "priority": "medium",
            "category": "general",
            "source": "whatsapp",
            "created_by": user_id,
            "created_at": datetime.utcnow().isoformat(),
            "deadline": (datetime.utcnow() + timedelta(days=7)).isoformat(),
        }

        response = client.table("tickets").insert(ticket_data).execute()

        if response.data:
            ticket = response.data[0]
            # Generate human-readable ticket number
            ticket_number = f"WA-{ticket['id'][:8].upper()}"
            return {
                "success": True,
                "ticket_id": ticket["id"],
                "ticket_number": ticket_number,
                "title": ticket["title"],
            }

        return {"success": False, "error": "Failed to create ticket"}

    async def _complete_checklist_item(self, command: Dict, from_number: str,
                                      org_id: str) -> Dict:
        """
        Complete a checklist item via Supabase UPDATE.

        SECURITY: .eq("org_id", org_id) enforced on every query.
        """
        item_ref = command['args'][0] if command.get('args') else None
        if not item_ref:
            return {"success": False, "error": "No checklist item reference provided"}

        client = get_supabase_client("service")

        # Look up the checklist item to verify ownership
        lookup = (
            client.table("checklist_items")
            .select("id, org_id")
            .eq("org_id", org_id)
            .or_(f"id.eq.{item_ref},reference.eq.{item_ref}")
            .execute()
        )
        if not lookup.data:
            return {"success": False, "error": "Checklist item not found"}

        item_id = lookup.data[0]["id"]

        # Update the checklist item
        response = (
            client.table("checklist_items")
            .update({
                "completed_at": datetime.utcnow().isoformat(),
                "completed_by": from_number,
                "status": "completed",
            })
            .eq("id", item_id)
            .eq("org_id", org_id)
            .execute()
        )

        return {"success": True, "item_id": item_id, "updated": response.data}

    async def _extract_property_from_location(self, location: str) -> str:
        """
        Extract property ID from location string using fuzzy matching.

        Matches against property addresses in Supabase.
        """
        if not location:
            return ""

        client = get_supabase_client("service")

        # Get all properties to match against
        response = (
            client.table("properties")
            .select("id, address, name, city")
            .execute()
        )

        if not response.data:
            return ""

        # Simple fuzzy matching - look for keywords in location
        location_lower = location.lower()

        for prop in response.data:
            # Check if location contains property name, address, or city
            if (prop.get("name", "").lower() in location_lower or
                prop.get("address", "").lower() in location_lower or
                prop.get("city", "").lower() in location_lower):
                return prop["id"]

        # No match found
        return ""

    async def _fetch_property_context(self, property_id: str, org_id: str) -> Dict:
        """
        Fetch property context for calendar briefing.

        SECURITY: .eq("org_id", org_id) enforced on every query.
        """
        client = get_supabase_client("service")

        # Count open tickets
        tickets_response = (
            client.table("tickets")
            .select("id", count="exact")
            .eq("org_id", org_id)
            .eq("property_id", property_id)
            .not_.in_("status", ["completed", "closed", "resolved"])
            .execute()
        )
        open_tickets = tickets_response.count or 0

        # Count overdue checklists
        from datetime import datetime
        now = datetime.utcnow().isoformat()

        checklists_response = (
            client.table("checklist_items")
            .select("id", count="exact")
            .eq("org_id", org_id)
            .eq("property_id", property_id)
            .is_("completed_at", "null")
            .lt("deadline", now)
            .execute()
        )
        overdue_checklists = checklists_response.count or 0

        # Get property name
        property_response = (
            client.table("properties")
            .select("name")
            .eq("org_id", org_id)
            .eq("id", property_id)
            .limit(1)
            .execute()
        )
        property_name = (
            property_response.data[0].get("name") if property_response.data else "Unknown Property"
        )

        return {
            "open_tickets": open_tickets,
            "overdue_checklists": overdue_checklists,
            "property_name": property_name,
        }

    async def _create_sensor_alert_ticket(self, sensor_data: Dict, org_id: str) -> Dict:
        """
        Create critical sensor alert ticket in Supabase.

        SECURITY: org_id is enforced on ticket creation.
        """
        client = get_supabase_client("service")
        import uuid
        from datetime import datetime, timedelta

        sensor_id = sensor_data.get("sensor_id", "unknown")
        reading = sensor_data.get("reading", 0)
        threshold = sensor_data.get("threshold", 0)
        property_id = sensor_data.get("property_id")
        sensor_type = sensor_data.get("sensor_type", "generic")

        # Create critical ticket
        ticket_data = {
            "id": str(uuid.uuid4()),
            "org_id": org_id,
            "property_id": property_id,
            "title": f"IoT Alert: {sensor_type} Threshold Exceeded",
            "description": (
                f"Sensor {sensor_id} reading ({reading}) exceeded threshold ({threshold})\n\n"
                f"Sensor Type: {sensor_type}\n"
                f"Property: {property_id}\n"
                f"Alert triggered at: {datetime.utcnow().isoformat()}"
            ),
            "status": "open",
            "priority": "critical",
            "category": "iot_alert",
            "source": "iot_sensor",
            "created_at": datetime.utcnow().isoformat(),
            "deadline": (datetime.utcnow() + timedelta(hours=2)).isoformat(),  # 2-hour SLA
            "metadata": sensor_data,
        }

        response = client.table("tickets").insert(ticket_data).execute()

        if response.data:
            ticket = response.data[0]
            ticket_number = f"IOT-{ticket['id'][:8].upper()}"
            return {
                "success": True,
                "ticket_id": ticket["id"],
                "ticket_number": ticket_number,
                "priority": "critical",
            }

        return {"success": False, "error": "Failed to create sensor alert ticket"}

    async def _log_sensor_event(
        self, sensor_data: Dict, org_id: str, context: Optional[ContextResult] = None
    ):
        """
        Log sensor event to Supabase sensor_events table enriched with dual-read context.

        SECURITY: org_id is stored with every row for RLS enforcement.
        """
        client = get_supabase_client("service")
        from datetime import datetime

        # Build context summary from dual-read
        context_summary: Dict[str, Any] = {}
        if context:
            memory_chunks = context.memory_chunks or []
            memory_hints: List[str] = []
            for i, c in enumerate(memory_chunks):
                if i >= 3:
                    break
                content = str(c.get("content", ""))
                # Truncate to 100 characters
                if len(content) > 100:
                    truncated = content[0:100]  # type: ignore[misc]
                else:
                    truncated = content
                memory_hints.append(truncated)

            context_summary = {
                "memory_hints": memory_hints,
                "related_records": len(context.supabase_rows or []),
                "sources_queried": context.sources_queried,
            }

        # Insert sensor event log
        event_data = {
            "org_id": org_id,
            "sensor_id": sensor_data.get("sensor_id"),
            "property_id": sensor_data.get("property_id"),
            "event_type": "threshold_exceeded",
            "reading": sensor_data.get("reading"),
            "threshold": sensor_data.get("threshold"),
            "severity": "critical" if sensor_data.get("reading", 0) > sensor_data.get("threshold", 0) * 1.5 else "high",
            "metadata": sensor_data,
            "context_summary": context_summary,
            "created_at": datetime.utcnow().isoformat(),
        }

        client.table("sensor_events").insert(event_data).execute()

    async def _notify_on_call(self, sensor_data: Dict, ticket: Dict, org_id: str):
        """
        Notify on-call engineer via notification service.

        SECURITY: .eq("org_id", org_id) enforced when finding on-call engineer.
        """
        client = get_supabase_client("service")

        # Find on-call engineer for this property
        property_id = sensor_data.get("property_id")

        # Query on-call rotation or fallback to property manager
        on_call_response = (
            client.table("users")
            .select("id, name, phone, email")
            .eq("org_id", org_id)
            .eq("is_on_call", True)
            .limit(1)
            .execute()
        )

        if not on_call_response.data:
            # Fallback: get property manager
            on_call_response = (
                client.table("properties")
                .select("manager_id")
                .eq("org_id", org_id)
                .eq("id", property_id)
                .limit(1)
                .execute()
            )

            if on_call_response.data and on_call_response.data[0].get("manager_id"):
                manager_id = on_call_response.data[0]["manager_id"]
                on_call_response = (
                    client.table("users")
                    .select("id, name, phone, email")
                    .eq("org_id", org_id)
                    .eq("id", manager_id)
                    .execute()
                )

        if on_call_response.data:
            engineer = on_call_response.data[0]

            # Send push notification
            await self.notifications.send_push(
                user_id=engineer["id"],
                title=f"🚨 Critical IoT Alert",
                body=(
                    f"Sensor {sensor_data.get('sensor_id')} exceeded threshold. "
                    f"Ticket {ticket.get('ticket_number')} created."
                ),
                data={
                    "ticket_id": ticket.get("ticket_id"),
                    "sensor_id": sensor_data.get("sensor_id"),
                    "action": "view_ticket",
                    "priority": "critical",
                }
            )

    async def _get_ticket(self, ticket_id: str, org_id: str) -> Dict:
        """
        Get ticket details from Supabase.

        SECURITY: .eq("org_id", org_id) enforced on every query.
        """
        client = get_supabase_client("service")
        response = (
            client.table("tickets")
            .select("*")
            .eq("org_id", org_id)
            .eq("id", ticket_id)
            .execute()
        )
        if not response.data:
            return {}
        return response.data[0]

    async def _link_po_to_ticket(self, ticket_id: str, po_number: str):
        """
        Link purchase order to ticket in Supabase.

        Updates ticket metadata with PO reference.
        """
        client = get_supabase_client("service")

        # Update ticket with PO reference
        client.table("tickets").update({
            "po_number": po_number,
            "po_linked_at": datetime.utcnow().isoformat(),
        }).eq("id", ticket_id).execute()

        # Also create purchase_orders record if table exists
        try:
            client.table("purchase_orders").insert({
                "po_number": po_number,
                "ticket_id": ticket_id,
                "status": "pending_approval",
                "created_at": datetime.utcnow().isoformat(),
            }).execute()
        except Exception:
            # Table might not exist yet - graceful degradation
            pass

    async def _store_iot_reading(self, sensor_data: Dict, org_id: str):
        """
        Store IoT sensor reading in Supabase energy_readings table.

        SECURITY: org_id is stored with every row for RLS enforcement.
        """
        client = get_supabase_client("service")
        client.table("energy_readings").insert({
            "org_id": org_id,
            "sensor_id": sensor_data.get("sensor_id"),
            "reading": sensor_data.get("reading"),
            "unit": sensor_data.get("unit", "unknown"),
            "threshold": sensor_data.get("threshold"),
            "property_id": sensor_data.get("property_id"),
            "captured_at": datetime.utcnow().isoformat(),
            "metadata": sensor_data.get("metadata", {}),
        }).execute()
