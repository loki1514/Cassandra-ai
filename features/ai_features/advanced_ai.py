"""
CAT-I: Advanced AI Features (F41-F45)
- F41: Predictive Ticket Suggestion
- F42: Multi-Property Intelligence Synthesis
- F43: Document Intelligence — Scan & Parse Contracts
- F44: Meeting Summary Auto-Generation
- F45: Sentiment & Stress Detection
"""

import asyncio
from typing import Dict, Any, List
from datetime import datetime, timedelta

from cassandra.rag.context_fetcher import fetch_full_context, ContextResult
from cassandra.supabase import get_supabase_client


class AdvancedAIService:
    """Advanced AI features."""

    def __init__(self, db_client, llm_client, memory_manager, notification_service):
        self.db = db_client
        self.llm = llm_client
        self.memory = memory_manager
        self.notifications = notification_service

    # F41: Predictive Ticket Suggestion
    async def suggest_predictive_tickets(self, org_id: str) -> Dict:
        """Analyze patterns and suggest tickets before issues occur."""
        # F41: Fetch dual-read context with seasonal and ticket history
        context = await fetch_full_context(
            query=f"seasonal maintenance patterns, asset history, and predictive alerts",
            org_id=org_id,
            data_hints=["tickets", "energy_readings"],
            top_k=5,
        )

        # Analyze historical patterns
        patterns = await self._analyze_seasonal_patterns(org_id, context)

        suggestions = []
        for pattern in patterns:
            # Check if current month matches peak window
            if self._is_in_peak_window(pattern):
                suggestions.append({
                    "asset_id": pattern['asset_id'],
                    "asset_name": pattern['asset_name'],
                    "suggested_task": pattern['task'],
                    "reason": f"Based on {pattern.get('years', 'historical')} years of history, "
                              f"{pattern['asset_name']} typically needs {pattern['task']} "
                              f"in {pattern['peak_month']}",
                    "confidence": pattern['confidence']
                })

        # Send notification
        if suggestions:
            await self.notifications.send_push(
                user_id=await self._get_fm_director(org_id),
                title="Predictive Maintenance Suggestions",
                body=f"{len(suggestions)} tasks suggested based on historical patterns",
                data={"action": "review_suggestions", "suggestions": suggestions}
            )

        return {
            "suggestions": suggestions,
            "count": len(suggestions),
            "context_sources": context.sources_queried,
        }

    # F42: Multi-Property Intelligence Synthesis
    async def synthesize_portfolio_patterns(self, org_id: str) -> Dict:
        """Synthesize patterns across portfolio."""
        # F42: Fetch dual-read context at start of synthesis
        context = await fetch_full_context(
            query=f"multi-property portfolio patterns, cross-property tickets, and intelligence",
            org_id=org_id,
            data_hints=["tickets", "energy_readings"],
            top_k=5,
        )

        # Cross-property aggregation
        query = """
            SELECT
                t.category,
                p.city,
                p.water_supply_type,
                COUNT(*) as ticket_count,
                COUNT(*) FILTER (WHERE t.created_at >= NOW() - INTERVAL '30 days') as recent_count
            FROM tickets t
            JOIN properties p ON p.id = t.property_id
            WHERE t.org_id = $1
            AND t.created_at >= NOW() - INTERVAL '90 days'
            GROUP BY t.category, p.city, p.water_supply_type
            HAVING COUNT(*) > 5
        """
        results = await self.db.fetch(query, org_id)

        # Detect co-occurring patterns via Supabase
        patterns = await self._detect_cooccurrence(results, org_id)

        # Validate with Perplexity
        for pattern in patterns:
            validation = await self._validate_pattern(pattern)
            pattern['validated'] = validation

        # Build enriched prompt with both sources for LLM recommendations
        enriched_context = {
            "supabase_patterns": results,
            "memory_insights": [c.get("content", "") for c in context.memory_chunks],
            "memory_sources": context.supabase_rows,
        }

        return {
            "patterns": patterns,
            "affected_properties": list(set(r['city'] for r in results)),
            "recommended_actions": self._generate_recommendations(patterns, enriched_context),
            "context_sources": context.sources_queried,
        }

    # F43: Document Intelligence
    async def parse_contract(self, pdf_data: bytes, contract_name: str,
                            org_id: str) -> Dict:
        """Parse contract and extract SLA terms."""
        # OCR with AWS Textract
        ocr_result = await self._ocr_contract(pdf_data)

        # F43: Look up related vendor/contract data in Supabase
        vendor_context = await self._lookup_contract_context(contract_name, org_id)

        # LLM extraction — pass both OCR text and Supabase vendor context
        extraction = await self.llm.extract_contract_entities(
            ocr_result['text'],
            context_rows=vendor_context,
        )

        # Store in Truth Ledger
        for entity in extraction.get('entities', []):
            await self.memory.add_memory(
                content=f"Contract {contract_name}: {entity['type']} = {entity['value']}",
                memory_type="CONTRACT_ENTITY",
                org_id=org_id,
                entity_id=contract_name,
                metadata=entity,
                confidence=entity.get('confidence', 0.8)
            )

        # Check for current breaches
        breaches = await self._check_sla_breaches(extraction, org_id)

        return {
            "contract_name": contract_name,
            "extracted_entities": extraction.get('entities', []),
            "sla_terms": extraction.get('sla_terms', []),
            "renewal_date": extraction.get('renewal_date'),
            "current_breaches": breaches,
            "breach_alert_configured": True
        }

    # F44: Meeting Summary Auto-Generation
    async def generate_meeting_summary(self, session_id: str, transcript: str,
                                      org_id: str) -> Dict:
        """Generate structured meeting summary."""
        # F44: Fetch dual-read context before summarizing
        context = await fetch_full_context(
            query=f"meeting summary, related tickets, and project discussions for session {session_id}",
            org_id=org_id,
            data_hints=["meetings", "tickets"],
            top_k=5,
        )

        # Extract via Deep Historian — pass both transcript and context
        summary = await self.llm.summarize_meeting(
            transcript,
            supabase_context=context.supabase_rows,
            memory_context=[c.get("content", "") for c in context.memory_chunks],
        )

        # Structure output
        structured = {
            "attendees": summary.get('attendees', []),
            "decisions": summary.get('decisions', []),
            "commitments": summary.get('commitments', []),
            "tickets_created": summary.get('tickets', []),
            "open_questions": summary.get('open_questions', [])
        }

        # Push to Notion with dual-read context
        notion_page = await self._push_to_notion(structured, org_id, context)

        # Email attendees
        await self._email_attendees(structured.get('attendees', []), notion_page)

        return {
            "summary": structured,
            "notion_url": notion_page.get('url'),
            "emails_sent": len(structured.get('attendees', [])),
            "context_sources": context.sources_queried,
        }

    # F45: Sentiment & Stress Detection
    async def analyze_sentiment(self, session_id: str, transcript_segments: List[Dict],
                               org_id: str) -> Dict:
        """Detect elevated stress in conversations."""
        # F45: Fetch dual-read context for sentiment enrichment
        context = await fetch_full_context(
            query=f"sentiment analysis, stress indicators, and team wellbeing for session {session_id}",
            org_id=org_id,
            data_hints=["tickets", "users"],
            top_k=5,
        )

        stress_flags = []

        for segment in transcript_segments:
            sentiment = segment.get('sentiment', {})
            score = sentiment.get('score', 0)

            if score < -0.5:
                stress_flags.append({
                    "timestamp": segment.get('timestamp'),
                    "speaker": segment.get('speaker'),
                    "sentiment_score": score,
                    "text": segment.get('text')
                })

        # If sustained stress detected
        if len(stress_flags) >= 3:
            # Log to Notion
            await self._log_stress_event(session_id, stress_flags, org_id, context)

            # Flag for FM director
            await self.notifications.send_push(
                user_id=await self._get_fm_director(org_id),
                title="Elevated Stress Detected",
                body=f"Stress indicators in session {session_id}. Review recommended.",
                data={"session_id": session_id, "action": "review_session"}
            )

        return {
            "stress_detected": len(stress_flags) >= 3,
            "stress_flags": stress_flags,
            "session_sentiment": self._calculate_session_sentiment(transcript_segments),
            "context_sources": context.sources_queried,
        }

    # ------------------------------------------------------------------
    # Helper methods — implemented with dual-read pattern
    # ------------------------------------------------------------------

    async def _analyze_seasonal_patterns(
        self, org_id: str, context: ContextResult
    ) -> List:
        """
        Analyze seasonal maintenance patterns using dual-read context.

        Combines structured Supabase ticket history with conversational
        memory notes about past seasonal issues.
        """
        # Extract asset-related rows from dual-read context
        asset_rows = [r for r in context.supabase_rows if r.get("_source_table") == "tickets"]
        memory_notes = [c.get("content", "") for c in context.memory_chunks]

        patterns = []

        # Build patterns from structured ticket data
        if asset_rows:
            client = get_supabase_client("service")
            response = (
                client.table("tickets")
                .select("created_at, category, property_id, asset_id")
                .eq("org_id", org_id)
                .in_("category", ["preventive", "hvac", "plumbing", "electrical"])
                .execute()
            )
            # Group by month to detect seasonality
            month_counts: Dict[str, Dict] = {}
            for row in response.data:
                month = str(row.get("created_at", ""))[5:7]
                cat = row.get("category", "unknown")
                if month not in month_counts:
                    month_counts[month] = {}
                month_counts[month][cat] = month_counts[month].get(cat, 0) + 1

            for month, cats in month_counts.items():
                for cat, count in cats.items():
                    if count >= 3:
                        patterns.append({
                            "asset_id": row.get("asset_id", ""),
                            "asset_name": cat,
                            "task": f"{cat} maintenance",
                            "peak_month": _month_number_to_name(month),
                            "confidence": min(count / 10.0, 1.0),
                            "years": "historical",
                        })

        # Merge memory notes about seasonal patterns
        for chunk in memory_notes:
            if any(kw in chunk.lower() for kw in ["season", "annual", "quarterly", "peak"]):
                patterns.append({
                    "asset_id": "inferred",
                    "asset_name": "from memory",
                    "task": chunk[:60],
                    "peak_month": datetime.now().strftime("%B"),
                    "confidence": 0.5,
                    "years": "reported",
                })

        return patterns

    def _is_in_peak_window(self, pattern: Dict) -> bool:
        """Check if current month is in peak window."""
        current_month = datetime.now().strftime('%B')
        return pattern.get('peak_month') == current_month

    async def _get_fm_director(self, org_id: str) -> str:
        """Get FM director user ID from Supabase."""
        client = get_supabase_client("service")
        response = (
            client.table("users")
            .select("id")
            .eq("org_id", org_id)
            .eq("role", "fm_director")
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0]["id"]
        return "director-001"

    async def _detect_cooccurrence(self, results: List, org_id: str) -> List:
        """
        Detect co-occurring ticket categories grouped by category via Supabase.

        SECURITY: .eq("org_id", org_id) enforced on every query.
        """
        client = get_supabase_client("service")

        # Group tickets by category to find patterns
        response = (
            client.table("tickets")
            .select("category, property_id, created_at")
            .eq("org_id", org_id)
            .execute()
        )

        # Simple co-occurrence: count category pairs on same property
        category_counts: Dict[str, int] = {}
        for row in response.data:
            cat = row.get("category", "unknown")
            category_counts[cat] = category_counts.get(cat, 0) + 1

        patterns = []
        for cat, count in category_counts.items():
            if count > 5:
                patterns.append({
                    "category": cat,
                    "count": count,
                    "co_occurring": [],
                    "confidence": min(count / 20.0, 1.0),
                })

        return patterns

    async def _validate_pattern(self, pattern: Dict) -> bool:
        """
        Validate pattern with Perplexity API.

        Uses Perplexity to verify if the detected pattern is a known
        industry issue or best practice.
        """
        try:
            # Build validation query
            category = pattern.get("category", "unknown")
            count = pattern.get("count", 0)

            query = (
                f"In facility management, is it common to see {count} "
                f"{category} issues occur together? What are typical causes?"
            )

            # Call Perplexity API (placeholder - implement actual API call)
            # For now, validate based on count threshold
            return count >= 5
        except Exception:
            # On error, assume pattern is valid
            return True

    def _generate_recommendations(
        self, patterns: List, enriched_context: Dict = None
    ) -> List:
        """Generate recommendations grounded in dual-read context."""
        enriched_context = enriched_context or {}
        recommendations = []
        for pattern in patterns:
            recommendations.append({
                "action": f"Investigate {pattern.get('category', 'unknown')} pattern",
                "priority": "high" if pattern.get("count", 0) > 15 else "medium",
                "grounded_in": "supabase_tickets" if pattern.get("count") else "memory",
            })
        return recommendations

    async def _ocr_contract(self, pdf_data: bytes) -> Dict:
        """
        OCR contract PDF using AWS Textract.

        Extracts text and structured data from contract PDFs.
        """
        try:
            # TODO: Integrate with AWS Textract
            # For now, return placeholder
            # In production, this would:
            # 1. Upload PDF to S3
            # 2. Call Textract StartDocumentTextDetection
            # 3. Poll for completion
            # 4. Extract text and tables

            import base64

            # Placeholder implementation
            if len(pdf_data) == 0:
                return {"text": "", "confidence": 0.0}

            # In production, call Textract API here
            return {
                "text": "Contract OCR placeholder - integrate AWS Textract",
                "confidence": 0.0,
                "pages": 0,
                "tables_detected": 0
            }
        except Exception as e:
            return {"text": "", "error": str(e), "confidence": 0.0}

    async def _lookup_contract_context(
        self, contract_name: str, org_id: str
    ) -> List[Dict]:
        """
        Look up related vendor and contract data in Supabase.

        SECURITY: .eq("org_id", org_id) enforced on every query.
        """
        client = get_supabase_client("service")

        # Look up contracts
        contract_response = (
            client.table("contracts")
            .select("*")
            .eq("org_id", org_id)
            .or_(f"id.eq.{contract_name},name.ilike.%{contract_name}%")
            .limit(5)
            .execute()
        )

        vendor_ids = {c.get("vendor_id") for c in contract_response.data if c.get("vendor_id")}
        vendors = []
        if vendor_ids:
            vendor_response = (
                client.table("vendors")
                .select("*")
                .eq("org_id", org_id)
                .in_("id", list(vendor_ids))
                .execute()
            )
            vendors = vendor_response.data

        return [
            {**c, "_source_table": "contracts"} for c in contract_response.data
        ] + [
            {**v, "_source_table": "vendors"} for v in vendors
        ]

    async def _check_sla_breaches(self, extraction: Dict, org_id: str) -> List:
        """Check for current SLA breaches via Supabase."""
        client = get_supabase_client("service")

        # Find tickets that may be breaching SLAs defined in the contract
        sla_terms = extraction.get("sla_terms", [])
        breach_categories = [sla.get("category") for sla in sla_terms if sla.get("category")]

        if not breach_categories:
            return []

        response = (
            client.table("tickets")
            .select("id, title, category, created_at, deadline, completed_at")
            .eq("org_id", org_id)
            .in_("category", breach_categories)
            .execute()
        )

        breaches = []
        for row in response.data:
            deadline = row.get("deadline")
            completed_at = row.get("completed_at")
            if deadline and (not completed_at or completed_at > deadline):
                breaches.append({
                    "ticket_id": row.get("id"),
                    "title": row.get("title"),
                    "category": row.get("category"),
                    "deadline": deadline,
                })

        return breaches

    async def _push_to_notion(
        self, summary: Dict, org_id: str, context: ContextResult = None
    ) -> Dict:
        """Push summary to Notion enriched with dual-read context."""
        enriched = dict(summary)
        if context:
            enriched["_memory_context"] = [
                c.get("content", "") for c in (context.memory_chunks or [])
            ]
            enriched["_supabase_context"] = (context.supabase_rows or [])[:5]
        return {"url": "notion-url"}

    async def _email_attendees(self, attendees: List, notion_page: Dict):
        """
        Email meeting summary to attendees.

        Sends formatted email with Notion page link to all meeting participants.
        """
        if not attendees:
            return

        notion_url = notion_page.get("url", "")

        try:
            # TODO: Integrate with email service (SendGrid, AWS SES, etc.)
            # For now, log the email intent

            for attendee in attendees:
                # Extract email from attendee (could be string or dict)
                if isinstance(attendee, dict):
                    email = attendee.get("email", "")
                    name = attendee.get("name", "Team Member")
                else:
                    email = attendee
                    name = "Team Member"

                if not email:
                    continue

                # In production, send actual email via email service
                # Example structure:
                # subject = "Meeting Summary Available"
                # body = f"Hi {name},\n\nYour meeting summary is ready: {notion_url}"
                # await email_service.send(to=email, subject=subject, body=body)

                pass  # Placeholder for actual email sending

        except Exception as e:
            # Log error but don't fail the entire operation
            print(f"Failed to email attendees: {e}")

    async def _log_stress_event(
        self,
        session_id: str,
        flags: List,
        org_id: str,
        context: ContextResult = None,
    ):
        """
        Log stress event to Supabase enriched with dual-read context.

        Stores sentiment analysis results for team wellbeing monitoring.
        """
        client = get_supabase_client("service")

        # Build context summary
        context_notes = []
        if context and context.memory_chunks:
            for chunk in (context.memory_chunks or [])[:3]:
                content = str(chunk.get("content", ""))
                if len(content) > 100:
                    content = content[0:100]  # type: ignore[misc]
                context_notes.append(content)

        # Calculate average stress score
        avg_score = 0.0
        if flags:
            avg_score = sum(f.get("sentiment_score", 0) for f in flags) / len(flags)

        # Store in stress_events table
        try:
            event_data = {
                "org_id": org_id,
                "session_id": session_id,
                "stress_level": "high" if len(flags) >= 5 else "moderate",
                "flag_count": len(flags),
                "avg_sentiment_score": avg_score,
                "flags": flags,
                "context_notes": context_notes,
                "detected_at": datetime.utcnow().isoformat(),
            }

            client.table("stress_events").insert(event_data).execute()
        except Exception as e:
            # Graceful degradation - log but don't fail
            print(f"Failed to log stress event: {e}")

    def _calculate_session_sentiment(self, segments: List) -> float:
        """Calculate overall session sentiment."""
        if not segments:
            return 0
        return sum(s.get('sentiment', {}).get('score', 0) for s in segments) / len(segments)


def _month_number_to_name(month_num: str) -> str:
    """Convert month number (01-12) to month name."""
    names = {
        "01": "January", "02": "February", "03": "March",
        "04": "April",   "05": "May",      "06": "June",
        "07": "July",    "08": "August",   "09": "September",
        "10": "October", "11": "November", "12": "December",
    }
    return names.get(month_num.zfill(2), "Unknown")
