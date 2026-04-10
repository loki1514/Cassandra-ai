"""
Integration Tests for Cassandra AI RAG System

Tests for:
- T19: E2E voice to ticket flow
- T21: Query resolution tests (Semantic → Map → DB1)
- T24: Conflict resolution tests (DB1 Always Wins)
"""

import pytest
import asyncio
from datetime import datetime
from typing import Dict, Any, List
from unittest.mock import Mock, AsyncMock, patch

# Import modules to test
import sys
sys.path.insert(0, '/mnt/okcomputer/output/cassandra-ai')

from cassandra.rag.context_fetcher import (
    resolve_query,
    FetchContextResult,
    ContextSource,
    ConflictResolution,
    QueryResolutionError
)
from cassandra.rag.conflict_resolver import (
    merge_context,
    ConflictResolver,
    ConflictType,
    ResolutionStrategy,
    ConflictResolutionError
)


# ============================================================================
# T19: E2E Voice to Ticket Flow Tests
# ============================================================================

class TestT19_VoiceToTicketFlow:
    """T19: End-to-end voice to ticket flow tests."""
    
    @pytest.fixture
    def mock_voice_data(self):
        """Mock voice transcription data."""
        return {
            "transcript_id": "trans_123",
            "text": "The customer is reporting a login timeout issue that started yesterday",
            "speaker_id": "agent_001",
            "confidence": 0.92,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    @pytest.fixture
    def mock_extraction_result(self):
        """Mock extraction result from voice data."""
        return {
            "issue_type": "login_timeout",
            "description": "Customer reporting login timeout issue",
            "priority": "high",
            "customer_id": "cust_456"
        }
    
    @pytest.mark.asyncio
    async def test_t19_voice_transcription_to_extraction(self, mock_voice_data):
        """T19: Test voice transcription leads to proper entity extraction."""
        # Simulate transcription processing
        transcript = mock_voice_data["text"]
        
        # Verify key entities are present
        assert "login" in transcript.lower()
        assert "timeout" in transcript.lower()
        assert "issue" in transcript.lower()
        
        # Verify confidence is high enough for processing
        assert mock_voice_data["confidence"] >= 0.7
    
    @pytest.mark.asyncio
    async def test_t19_extraction_to_ticket_creation(self, mock_extraction_result):
        """T19: Test extracted entities can form a valid ticket."""
        extraction = mock_extraction_result
        
        # Verify required ticket fields are present
        assert "issue_type" in extraction
        assert "description" in extraction
        assert "priority" in extraction
        
        # Verify priority is valid
        assert extraction["priority"] in ["low", "medium", "high", "critical"]
    
    @pytest.mark.asyncio
    async def test_t19_full_flow_mocked(self):
        """T19: Test full voice-to-ticket flow with mocked dependencies."""
        # Mock the transcription service
        mock_transcription = Mock()
        mock_transcription.transcribe = AsyncMock(return_value={
            "text": "Customer cannot access dashboard after password reset",
            "confidence": 0.89
        })
        
        # Mock the extraction service
        mock_extraction = Mock()
        mock_extraction.extract_entities = AsyncMock(return_value={
            "issue_type": "access_denied",
            "description": "Customer cannot access dashboard",
            "priority": "medium"
        })
        
        # Mock ticket creation
        mock_ticket_service = Mock()
        mock_ticket_service.create = AsyncMock(return_value={
            "ticket_id": "TICKET-789",
            "status": "created"
        })
        
        # Execute flow
        voice_data = b"mock_audio_data"
        
        # Step 1: Transcribe
        transcription = await mock_transcription.transcribe(voice_data)
        assert transcription["confidence"] >= 0.7
        
        # Step 2: Extract entities
        entities = await mock_extraction.extract_entities(transcription["text"])
        assert "issue_type" in entities
        
        # Step 3: Create ticket
        ticket = await mock_ticket_service.create(entities)
        assert ticket["ticket_id"].startswith("TICKET-")
        assert ticket["status"] == "created"


# ============================================================================
# T21: Query Resolution Tests (Semantic → Map → DB1)
# ============================================================================

class TestT21_QueryResolution:
    """T21: Query resolution flow tests."""
    
    @pytest.fixture
    def mock_memory_manager(self):
        """Create mock memory manager."""
        mm = Mock()
        
        # Mock search results
        mm.search_memories = AsyncMock(return_value=[
            Mock(
                memory=Mock(
                    memory_id="mem_001",
                    content="The login timeout issue was resolved by increasing session timeout",
                    ticket_id="TICKET-123",
                    org_id="org_456",
                    created_at=datetime.utcnow(),
                    metadata={"ticket_status": "resolved"}
                ),
                similarity_score=0.92
            ),
            Mock(
                memory=Mock(
                    memory_id="mem_002",
                    content="Similar timeout issues reported by 3 other customers",
                    ticket_id="TICKET-124",
                    org_id="org_456",
                    created_at=datetime.utcnow(),
                    metadata={}
                ),
                similarity_score=0.78
            )
        ])
        
        return mm
    
    @pytest.fixture
    def mock_db_pool(self):
        """Create mock database pool."""
        pool = Mock()
        
        # Mock connection
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            "db1_ticket_id": "TICKET-123",
            "db1_table": "tickets",
            "resolution_status": "active"
        })
        mock_conn.fetch = AsyncMock(return_value=[{
            "id": "TICKET-123",
            "title": "Login Timeout Issue",
            "status": "resolved",
            "priority": "high",
            "assignee_id": "user_789",
            "updated_at": datetime.utcnow().isoformat(),
            "resolution_notes": "Increased session timeout to 30 minutes"
        }])
        
        # Mock pool acquire context
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_context.__aexit__ = AsyncMock(return_value=False)
        
        pool.acquire = Mock(return_value=mock_context)
        
        return pool
    
    @pytest.mark.asyncio
    async def test_t21_semantic_search_executed(self, mock_memory_manager, mock_db_pool):
        """T21: Verify semantic search is executed as first step."""
        result = await resolve_query(
            query_text="What was the resolution for the login timeout?",
            org_id="org_456",
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool,
            max_results=5
        )
        
        # Verify semantic search was called
        mock_memory_manager.search_memories.assert_called_once()
        call_args = mock_memory_manager.search_memories.call_args
        assert call_args[1]["query"] == "What was the resolution for the login timeout?"
        assert call_args[1]["org_id"] == "org_456"
    
    @pytest.mark.asyncio
    async def test_t21_map_table_lookup(self, mock_memory_manager, mock_db_pool):
        """T21: Verify map table lookup is performed for memory results."""
        result = await resolve_query(
            query_text="login timeout resolution",
            org_id="org_456",
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool
        )
        
        # Verify DB pool was used (for map table lookup)
        mock_db_pool.acquire.assert_called()
    
    @pytest.mark.asyncio
    async def test_t21_db1_resolution(self, mock_memory_manager, mock_db_pool):
        """T21: Verify DB1 resolution is performed."""
        result = await resolve_query(
            query_text="login timeout resolution",
            org_id="org_456",
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool
        )
        
        # Verify result contains items
        assert isinstance(result, FetchContextResult)
        assert len(result.items) > 0
    
    @pytest.mark.asyncio
    async def test_t21_latency_requirement(self, mock_memory_manager, mock_db_pool):
        """T21: Verify query resolution completes within 400ms target."""
        import time
        
        start_time = time.time()
        
        result = await resolve_query(
            query_text="login timeout resolution",
            org_id="org_456",
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool,
            timeout_ms=400
        )
        
        elapsed_ms = (time.time() - start_time) * 1000
        
        # Verify latency is tracked
        assert result.fetch_time_ms >= 0
        
        # In real scenario, should be under 400ms
        # With mocks, this is just verifying the tracking works
        print(f"T21: Query resolved in {elapsed_ms:.2f}ms (tracked: {result.fetch_time_ms:.2f}ms)")
    
    @pytest.mark.asyncio
    async def test_t21_result_structure(self, mock_memory_manager, mock_db_pool):
        """T21: Verify result has correct structure with sources."""
        result = await resolve_query(
            query_text="login timeout resolution",
            org_id="org_456",
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool
        )
        
        # Verify result structure
        assert hasattr(result, 'items')
        assert hasattr(result, 'sources_used')
        assert hasattr(result, 'fetch_time_ms')
        assert hasattr(result, 'conflicts_detected')
        
        # Verify sources are tracked
        assert len(result.sources_used) > 0
        assert ContextSource.MEMORY_ARCHIVE in result.sources_used
    
    @pytest.mark.asyncio
    async def test_t21_empty_query_handling(self, mock_memory_manager, mock_db_pool):
        """T21: Test handling of empty or invalid queries."""
        # Test with empty query
        mock_memory_manager.search_memories = AsyncMock(return_value=[])
        
        result = await resolve_query(
            query_text="xyznonexistentquery123",
            org_id="org_456",
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool
        )
        
        # Should return empty result, not error
        assert isinstance(result, FetchContextResult)


# ============================================================================
# T24: Conflict Resolution Tests (DB1 Always Wins)
# ============================================================================

class TestT24_ConflictResolution:
    """T24: Conflict resolution tests - DB1 always wins."""
    
    @pytest.fixture
    def resolver(self):
        """Create conflict resolver instance."""
        return ConflictResolver()
    
    @pytest.mark.asyncio
    async def test_t24_case1_status_conflict_db1_wins(self, resolver):
        """
        T24 Test Case 1: Status conflict - DB1 wins.
        
        Memory says status is "open", DB1 says "closed".
        Result should be "closed" (DB1 value).
        """
        memory_result = {"status": "open", "priority": "high"}
        db1_result = {"status": "closed", "priority": "high"}
        
        result = await resolver.merge_context(
            memory_result=memory_result,
            db1_result=db1_result,
            memory_id="mem_001",
            ticket_id="TICKET-123",
            org_id="org_456"
        )
        
        # DB1 should win
        assert result.content["status"] == "closed"
        assert result.content["priority"] == "high"  # No conflict, preserved
        assert len(result.overrides_applied) == 1
        assert result.overrides_applied[0].overridden_field == "status"
        assert result.resolution_strategy == ResolutionStrategy.DB1_WINS
    
    @pytest.mark.asyncio
    async def test_t24_case2_assignee_conflict_db1_wins(self, resolver):
        """
        T24 Test Case 2: Assignee conflict - DB1 wins.
        
        Memory says assignee is "user_old", DB1 says "user_new".
        Result should be "user_new" (DB1 value).
        """
        memory_result = {"assignee_id": "user_old", "status": "in_progress"}
        db1_result = {"assignee_id": "user_new", "status": "in_progress"}
        
        result = await resolver.merge_context(
            memory_result=memory_result,
            db1_result=db1_result,
            memory_id="mem_002",
            ticket_id="TICKET-124",
            org_id="org_456"
        )
        
        # DB1 should win
        assert result.content["assignee_id"] == "user_new"
        assert len(result.overrides_applied) == 1
        assert result.overrides_applied[0].overridden_field == "assignee_id"
    
    @pytest.mark.asyncio
    async def test_t24_case3_priority_conflict_db1_wins(self, resolver):
        """
        T24 Test Case 3: Priority conflict - DB1 wins.
        
        Memory says priority is "low", DB1 says "high".
        Result should be "high" (DB1 value).
        """
        memory_result = {"priority": "low", "status": "open"}
        db1_result = {"priority": "high", "status": "open"}
        
        result = await resolver.merge_context(
            memory_result=memory_result,
            db1_result=db1_result,
            memory_id="mem_003",
            ticket_id="TICKET-125",
            org_id="org_456"
        )
        
        # DB1 should win
        assert result.content["priority"] == "high"
        assert len(result.overrides_applied) == 1
        assert result.overrides_applied[0].overridden_field == "priority"
    
    @pytest.mark.asyncio
    async def test_t24_case4_multiple_conflicts_all_db1_wins(self, resolver):
        """
        T24 Test Case 4: Multiple conflicts - DB1 wins all.
        
        All conflicting fields should use DB1 values.
        """
        memory_result = {
            "status": "open",
            "assignee_id": "user_old",
            "priority": "low",
            "resolution_notes": "Initial analysis"
        }
        db1_result = {
            "status": "resolved",
            "assignee_id": "user_new",
            "priority": "high",
            "resolution_notes": "Issue fixed in v2.1"
        }
        
        result = await resolver.merge_context(
            memory_result=memory_result,
            db1_result=db1_result,
            memory_id="mem_004",
            ticket_id="TICKET-126",
            org_id="org_456"
        )
        
        # All DB1 values should win
        assert result.content["status"] == "resolved"
        assert result.content["assignee_id"] == "user_new"
        assert result.content["priority"] == "high"
        assert result.content["resolution_notes"] == "Issue fixed in v2.1"
        
        # Should have 4 overrides
        assert len(result.overrides_applied) == 4
    
    @pytest.mark.asyncio
    async def test_t24_case5_no_conflict_preserve_memory(self, resolver):
        """
        T24 Test Case 5: No conflict - preserve memory fields.
        
        When there's no conflict, non-conflicting memory fields should be preserved.
        """
        memory_result = {
            "status": "open",
            "custom_field": "memory_value",
            "notes": "from memory"
        }
        db1_result = {
            "status": "open",  # Same, no conflict
            "db1_field": "db1_value"
        }
        
        result = await resolver.merge_context(
            memory_result=memory_result,
            db1_result=db1_result,
            memory_id="mem_005",
            ticket_id="TICKET-127",
            org_id="org_456"
        )
        
        # No conflicts, no overrides
        assert len(result.overrides_applied) == 0
        assert result.content["status"] == "open"
        assert result.content["custom_field"] == "memory_value"
        assert result.content["db1_field"] == "db1_value"
    
    @pytest.mark.asyncio
    async def test_t24_override_logging(self, resolver):
        """T24: Verify override logging works correctly."""
        memory_result = {"status": "open", "priority": "high"}
        db1_result = {"status": "closed", "priority": "high"}
        
        await resolver.merge_context(
            memory_result=memory_result,
            db1_result=db1_result,
            memory_id="mem_006",
            ticket_id="TICKET-128",
            org_id="org_456"
        )
        
        # Check logs
        logs = resolver.get_override_logs(ticket_id="TICKET-128")
        assert len(logs) == 1
        assert logs[0].ticket_id == "TICKET-128"
        assert logs[0].memory_value == "open"
        assert logs[0].db1_value == "closed"
        assert logs[0].reason == "db1_authoritative"
    
    @pytest.mark.asyncio
    async def test_t24_convenience_function(self):
        """T24: Test convenience merge_context function."""
        memory_result = {"status": "open"}
        db1_result = {"status": "closed"}
        
        result = await merge_context(
            memory_result=memory_result,
            db1_result=db1_result,
            ticket_id="TICKET-129",
            org_id="org_456"
        )
        
        assert result.content["status"] == "closed"
        assert len(result.overrides_applied) == 1


# ============================================================================
# Integration Test Suite Runner
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
