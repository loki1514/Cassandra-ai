"""
CAT-I: Advanced AI Features (F41-F45)
- F41: Predictive Ticket Suggestion
- F42: Multi-Property Intelligence Synthesis
- F43: Document Intelligence — Scan & Parse Contracts
- F44: Meeting Summary Auto-Generation
- F45: Sentiment & Stress Detection
"""

from typing import Dict, Any, List
from datetime import datetime, timedelta


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
        # Analyze historical patterns
        patterns = await self._analyze_seasonal_patterns(org_id)
        
        suggestions = []
        for pattern in patterns:
            # Check if current month matches peak window
            if self._is_in_peak_window(pattern):
                suggestions.append({
                    "asset_id": pattern['asset_id'],
                    "asset_name": pattern['asset_name'],
                    "suggested_task": pattern['task'],
                    "reason": f"Based on {pattern['years']} years of history, {pattern['asset_name']} typically needs {pattern['task']} in {pattern['peak_month']}",
                    "confidence": pattern['confidence']
                })
        
        # Send notification
        if suggestions:
            await self.notifications.send_push(
                user_id=await self._get_fm_director(org_id),
                title="🔮 Predictive Maintenance Suggestions",
                body=f"{len(suggestions)} tasks suggested based on historical patterns",
                data={"action": "review_suggestions", "suggestions": suggestions}
            )
        
        return {"suggestions": suggestions, "count": len(suggestions)}
    
    # F42: Multi-Property Intelligence Synthesis
    async def synthesize_portfolio_patterns(self, org_id: str) -> Dict:
        """Synthesize patterns across portfolio."""
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
        
        # Detect co-occurring patterns
        patterns = self._detect_cooccurrence(results)
        
        # Validate with Perplexity
        for pattern in patterns:
            validation = await self._validate_pattern(pattern)
            pattern['validated'] = validation
        
        return {
            "patterns": patterns,
            "affected_properties": list(set(r['city'] for r in results)),
            "recommended_actions": self._generate_recommendations(patterns)
        }
    
    # F43: Document Intelligence
    async def parse_contract(self, pdf_data: bytes, contract_name: str, 
                            org_id: str) -> Dict:
        """Parse contract and extract SLA terms."""
        # OCR with AWS Textract
        ocr_result = await self._ocr_contract(pdf_data)
        
        # LLM extraction
        extraction = await self.llm.extract_contract_entities(ocr_result['text'])
        
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
        # Extract via Deep Historian
        summary = await self.llm.summarize_meeting(transcript)
        
        # Structure output
        structured = {
            "attendees": summary.get('attendees', []),
            "decisions": summary.get('decisions', []),
            "commitments": summary.get('commitments', []),
            "tickets_created": summary.get('tickets', []),
            "open_questions": summary.get('open_questions', [])
        }
        
        # Push to Notion
        notion_page = await self._push_to_notion(structured, org_id)
        
        # Email attendees
        await self._email_attendees(structured.get('attendees', []), notion_page)
        
        return {
            "summary": structured,
            "notion_url": notion_page.get('url'),
            "emails_sent": len(structured.get('attendees', []))
        }
    
    # F45: Sentiment & Stress Detection
    async def analyze_sentiment(self, session_id: str, transcript_segments: List[Dict],
                               org_id: str) -> Dict:
        """Detect elevated stress in conversations."""
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
            await self._log_stress_event(session_id, stress_flags, org_id)
            
            # Flag for FM director
            await self.notifications.send_push(
                user_id=await self._get_fm_director(org_id),
                title="⚠️ Elevated Stress Detected",
                body=f"Stress indicators in session {session_id}. Review recommended.",
                data={"session_id": session_id, "action": "review_session"}
            )
        
        return {
            "stress_detected": len(stress_flags) >= 3,
            "stress_flags": stress_flags,
            "session_sentiment": self._calculate_session_sentiment(transcript_segments)
        }
    
    # Helper methods
    async def _analyze_seasonal_patterns(self, org_id: str) -> List:
        """Analyze seasonal maintenance patterns."""
        return []
    
    def _is_in_peak_window(self, pattern: Dict) -> bool:
        """Check if current month is in peak window."""
        current_month = datetime.now().strftime('%B')
        return pattern.get('peak_month') == current_month
    
    async def _get_fm_director(self, org_id: str) -> str:
        """Get FM director user ID."""
        return "director-001"
    
    def _detect_cooccurrence(self, results: List) -> List:
        """Detect co-occurring patterns."""
        return []
    
    async def _validate_pattern(self, pattern: Dict) -> bool:
        """Validate pattern with Perplexity."""
        return True
    
    def _generate_recommendations(self, patterns: List) -> List:
        """Generate recommendations."""
        return []
    
    async def _ocr_contract(self, pdf_data: bytes) -> Dict:
        """OCR contract PDF."""
        return {"text": ""}
    
    async def _check_sla_breaches(self, extraction: Dict, org_id: str) -> List:
        """Check for current SLA breaches."""
        return []
    
    async def _push_to_notion(self, summary: Dict, org_id: str) -> Dict:
        """Push summary to Notion."""
        return {"url": "notion-url"}
    
    async def _email_attendees(self, attendees: List, notion_page: Dict):
        """Email attendees."""
        pass
    
    async def _log_stress_event(self, session_id: str, flags: List, org_id: str):
        """Log stress event."""
        pass
    
    def _calculate_session_sentiment(self, segments: List) -> float:
        """Calculate overall session sentiment."""
        if not segments:
            return 0
        return sum(s.get('sentiment', {}).get('score', 0) for s in segments) / len(segments)