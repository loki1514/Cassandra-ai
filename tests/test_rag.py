"""
Unit Tests for Cassandra AI RAG Module

Tests for:
- MemoryManager (add, get, search, delete)
- ContextFetcher (fetch_context, conflict resolution)
- Idempotency (key generation, deduplication)
- TruthLedger (event recording, review queue)
- OrphanReconciler (orphan detection, recovery)
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import json

# Import RAG modules
import sys
sys.path.insert(0, '/mnt/okcomputer/output/cassandra-ai')

from cassandra.rag.memory_manager import (
    MemoryManager, MemoryEntry, MemorySearchResult,
    MemoryType, MemoryPriority, SupermemoryConfig,
    MemoryWriteError
)
from cassandra.rag.context_fetcher import (
    ContextFetcher, FetchContextInput, FetchContextResult,
    ContextItem, ContextSource, ConflictResolution,
    fetch_context
)
from cassandra.rag.idempotency import (
    generate_idempotency_key, check_idempotency,
    IdempotencyStore, IdempotencyKey, IdempotencyConfig,
    EventType, IdempotencyStatus, get_time_bucket
)
from cassandra.rag.truth_ledger import (
    TruthLedger, TruthEvent, EntityType, ConfidenceLevel,
    ReviewStatus, VerificationStatus, TruthLedgerConfig,
    ReviewQueueEntry, TruthLedgerError
)
from cassandra.rag.reconciliation import (
    OrphanReconciler, OrphanedMemory, OrphanStatus, OrphanType,
    ReconciliationResult, ReconciliationConfig, ReconciliationError
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_db_pool():
    """Mock database pool for testing."""
    pool = AsyncMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


@pytest.fixture
def mock_http_client():
    """Mock HTTP client for Supermemory API."""
    client = AsyncMock()
    client.is_closed = False
    return client


@pytest.fixture
def supermemory_config():
    """Supermemory configuration for testing."""
    return SupermemoryConfig(
        api_key="test_key",
        base_url="https://api.test.supermemory.ai/v1",
        timeout=10,
        max_retries=2
    )


@pytest.fixture
def sample_memory_entry():
    """Sample memory entry for testing."""
    return MemoryEntry(
        content="Test memory content about ticket resolution",
        memory_type=MemoryType.TICKET_CONTEXT,
        org_id="org_test_123",
        ticket_id="TICKET-456",
        user_id="user_789",
        priority=MemoryPriority.HIGH,
        tags=["resolution", "test"]
    )


@pytest.fixture
def sample_truth_event():
    """Sample truth event for testing."""
    return TruthEvent(
        event_id="evt_test_123",
        entity_type=EntityType.DECISION,
        entity_id="DEC-456",
        org_id="org_test_123",
        action="created",
        data={"decision": "assign_to_team_a", "reason": "workload"},
        confidence=0.85,
        source="ai",
        user_id="user_789"
    )


# ============================================================================
# Memory Manager Tests
# ============================================================================

class TestMemoryManager:
    """Tests for MemoryManager class."""
    
    @pytest.mark.asyncio
    async def test_init(self, mock_db_pool, supermemory_config):
        """Test MemoryManager initialization."""
        manager = MemoryManager(supermemory_config, mock_db_pool)
        assert manager.config == supermemory_config
        assert manager.db_pool == mock_db_pool
        assert manager.enable_dual_write is True
    
    @pytest.mark.asyncio
    async def test_generate_memory_id(self, mock_db_pool, supermemory_config):
        """Test memory ID generation."""
        manager = MemoryManager(supermemory_config, mock_db_pool)
        
        content = "Test content"
        org_id = "org_123"
        
        id1 = manager._generate_memory_id(content, org_id)
        id2 = manager._generate_memory_id(content, org_id)
        
        # IDs should be different due to timestamp
        assert id1 != id2
        assert len(id1) == 32
        assert len(id2) == 32
    
    @pytest.mark.asyncio
    async def test_memory_entry_to_dict(self, sample_memory_entry):
        """Test MemoryEntry serialization."""
        entry_dict = sample_memory_entry.to_dict()
        
        assert entry_dict["memory_type"] == "ticket_context"
        assert entry_dict["org_id"] == "org_test_123"
        assert entry_dict["ticket_id"] == "TICKET-456"
        assert entry_dict["priority"] == 2
        assert entry_dict["tags"] == ["resolution", "test"]
    
    @pytest.mark.asyncio
    async def test_memory_entry_encryption(self, sample_memory_entry):
        """Test MemoryEntry encryption/decryption."""
        from cryptography.fernet import Fernet
        
        key = Fernet.generate_key()
        
        # Encrypt
        encrypted_dict = sample_memory_entry.to_dict(encrypt=True, encryption_key=key)
        assert encrypted_dict["encrypted"] is True
        
        # Decrypt
        decrypted = MemoryEntry.from_dict(encrypted_dict, decryption_key=key)
        assert decrypted.content == sample_memory_entry.content
    
    @pytest.mark.asyncio
    async def test_add_memory(self, mock_db_pool, supermemory_config, sample_memory_entry):
        """Test adding a memory."""
        manager = MemoryManager(supermemory_config, mock_db_pool)
        
        # Mock HTTP client
        mock_client = AsyncMock()
        mock_client.post.return_value = AsyncMock(
            status_code=200,
            json=AsyncMock(return_value={"id": "mem_123"})
        )
        manager._http_client = mock_client
        
        # Mock embedding generation
        manager._generate_embedding = AsyncMock(return_value=[0.1, 0.2, 0.3])
        
        memory_id = await manager.add_memory(sample_memory_entry)
        
        assert memory_id is not None
        assert len(memory_id) == 32
        mock_client.post.assert_called_once()


# ============================================================================
# Context Fetcher Tests
# ============================================================================

class TestContextFetcher:
    """Tests for ContextFetcher class."""
    
    @pytest.mark.asyncio
    async def test_fetch_context_input_validation(self):
        """Test FetchContextInput validation."""
        # Valid input
        valid_input = FetchContextInput(
            query_text="What was the resolution?",
            org_id="org_123"
        )
        assert valid_input.query_text == "What was the resolution?"
        assert valid_input.org_id == "org_123"
        
        # Invalid - empty query
        with pytest.raises(ValueError):
            FetchContextInput(query_text="", org_id="org_123")
        
        # Invalid - whitespace query
        with pytest.raises(ValueError):
            FetchContextInput(query_text="   ", org_id="org_123")
    
    @pytest.mark.asyncio
    async def test_fetch_context_input_defaults(self):
        """Test FetchContextInput default values."""
        input_data = FetchContextInput(
            query_text="Test query",
            org_id="org_123"
        )
        
        assert input_data.max_results == 10
        assert input_data.min_confidence == 0.7
        assert input_data.include_history is True
        assert input_data.conflict_resolution == ConflictResolution.DB1_WINS
    
    @pytest.mark.asyncio
    async def test_context_item_creation(self):
        """Test ContextItem creation."""
        item = ContextItem(
            content="Test context",
            source=ContextSource.MEMORY_ARCHIVE,
            memory_id="mem_123",
            ticket_id="TICKET-456",
            confidence=0.85
        )
        
        assert item.source == ContextSource.MEMORY_ARCHIVE
        assert item.confidence == 0.85
        assert item.memory_id == "mem_123"
    
    @pytest.mark.asyncio
    async def test_fetch_context_result_combined_context(self):
        """Test FetchContextResult.get_combined_context()."""
        result = FetchContextResult(
            query="test query",
            org_id="org_123",
            items=[
                ContextItem(
                    content="Context item 1",
                    source=ContextSource.MEMORY_ARCHIVE,
                    confidence=0.9
                ),
                ContextItem(
                    content="Context item 2",
                    source=ContextSource.DB1_TICKET,
                    ticket_id="TICKET-456",
                    confidence=0.85
                )
            ]
        )
        
        combined = result.get_combined_context()
        assert "Context item 1" in combined
        assert "Context item 2" in combined
        assert "TICKET-456" in combined


# ============================================================================
# Idempotency Tests
# ============================================================================

class TestIdempotency:
    """Tests for idempotency functionality."""
    
    def test_generate_idempotency_key(self):
        """Test idempotency key generation."""
        timestamp = datetime(2024, 1, 15, 10, 23, 45)
        
        key1 = generate_idempotency_key(
            entity_id="TICKET-123",
            event_type=EventType.TICKET_UPDATED,
            timestamp=timestamp
        )
        
        key2 = generate_idempotency_key(
            entity_id="TICKET-123",
            event_type=EventType.TICKET_UPDATED,
            timestamp=timestamp
        )
        
        # Same inputs should produce same key
        assert key1 == key2
        assert key1.startswith("idemp:ticket_updated:")
        assert len(key1) > 20
    
    def test_generate_idempotency_key_different_buckets(self):
        """Test that different time buckets produce different keys."""
        timestamp1 = datetime(2024, 1, 15, 10, 23, 45)  # 10:20 bucket
        timestamp2 = datetime(2024, 1, 15, 10, 27, 30)  # 10:25 bucket
        
        key1 = generate_idempotency_key(
            entity_id="TICKET-123",
            event_type=EventType.TICKET_UPDATED,
            timestamp=timestamp1
        )
        
        key2 = generate_idempotency_key(
            entity_id="TICKET-123",
            event_type=EventType.TICKET_UPDATED,
            timestamp=timestamp2
        )
        
        # Different buckets should produce different keys
        assert key1 != key2
    
    def test_get_time_bucket(self):
        """Test time bucket calculation."""
        timestamp = datetime(2024, 1, 15, 10, 23, 45)
        bucket = get_time_bucket(timestamp, bucket_minutes=5)
        
        assert bucket.minute == 20  # Floored to 5-min bucket
        assert bucket.second == 0
        assert bucket.microsecond == 0
    
    def test_idempotency_key_with_additional_data(self):
        """Test key generation with additional data."""
        key1 = generate_idempotency_key(
            entity_id="TICKET-123",
            event_type=EventType.TICKET_UPDATED,
            additional_data={"field": "status", "old": "open", "new": "closed"}
        )
        
        key2 = generate_idempotency_key(
            entity_id="TICKET-123",
            event_type=EventType.TICKET_UPDATED,
            additional_data={"field": "priority", "old": "low", "new": "high"}
        )
        
        # Different additional data should produce different keys
        assert key1 != key2
    
    @pytest.mark.asyncio
    async def test_idempotency_store_check_new_event(self, mock_db_pool):
        """Test checking a new event."""
        store = IdempotencyStore(mock_db_pool)
        
        # Mock database to return no existing key
        conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = {
            "status": "processing",
            "created_at": datetime.utcnow()
        }
        
        should_process, key = await store.check_idempotency(
            entity_id="TICKET-123",
            event_type=EventType.TICKET_UPDATED
        )
        
        assert should_process is True
        assert key.startswith("idemp:ticket_updated:")


# ============================================================================
# Truth Ledger Tests
# ============================================================================

class TestTruthLedger:
    """Tests for TruthLedger class."""
    
    def test_truth_event_creation(self, sample_truth_event):
        """Test TruthEvent creation and hash generation."""
        assert sample_truth_event.event_id == "evt_test_123"
        assert sample_truth_event.entity_type == EntityType.DECISION
        assert sample_truth_event.confidence == 0.85
        assert sample_truth_event.event_hash is not None
        assert len(sample_truth_event.event_hash) == 64  # SHA-256 hex
    
    def test_truth_event_verification(self, sample_truth_event):
        """Test TruthEvent hash verification."""
        # Valid event should verify
        assert sample_truth_event.verify() is True
        
        # Tampered event should fail verification
        sample_truth_event.data["decision"] = "tampered"
        assert sample_truth_event.verify() is False
    
    def test_truth_event_requires_review(self):
        """Test review requirement detection."""
        # High confidence - no review needed
        high_confidence = TruthEvent(
            event_id="evt_1",
            entity_type=EntityType.DECISION,
            entity_id="DEC-1",
            org_id="org_123",
            action="created",
            data={},
            confidence=0.85,
            source="ai"
        )
        assert high_confidence.requires_review() is False
        
        # Medium confidence - review needed
        medium_confidence = TruthEvent(
            event_id="evt_2",
            entity_type=EntityType.DECISION,
            entity_id="DEC-2",
            org_id="org_123",
            action="created",
            data={},
            confidence=0.60,
            source="ai"
        )
        assert medium_confidence.requires_review() is True
        
        # Low confidence - no review (auto-reject)
        low_confidence = TruthEvent(
            event_id="evt_3",
            entity_type=EntityType.DECISION,
            entity_id="DEC-3",
            org_id="org_123",
            action="created",
            data={},
            confidence=0.30,
            source="ai"
        )
        assert low_confidence.requires_review() is False
    
    def test_truth_event_serialization(self, sample_truth_event):
        """Test TruthEvent serialization."""
        event_dict = sample_truth_event.to_dict()
        
        assert event_dict["event_id"] == "evt_test_123"
        assert event_dict["entity_type"] == "decision"
        assert event_dict["confidence"] == 0.85
        
        # Deserialize
        restored = TruthEvent.from_dict(event_dict)
        assert restored.event_id == sample_truth_event.event_id
        assert restored.confidence == sample_truth_event.confidence
    
    def test_truth_ledger_config_validation(self):
        """Test TruthLedgerConfig validation."""
        # Valid config
        config = TruthLedgerConfig(
            review_queue_threshold_low=0.5,
            review_queue_threshold_high=0.7
        )
        assert config.review_queue_threshold_low == 0.5
        assert config.review_queue_threshold_high == 0.7
        
        # Invalid - low >= high
        with pytest.raises(ValueError):
            TruthLedgerConfig(
                review_queue_threshold_low=0.8,
                review_queue_threshold_high=0.7
            )
    
    @pytest.mark.asyncio
    async def test_truth_ledger_requires_review(self, mock_db_pool):
        """Test that events with 0.5-0.7 confidence require review."""
        ledger = TruthLedger(mock_db_pool)
        
        # Mock database
        conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = None  # No previous hash
        
        # Event requiring review (0.6 confidence)
        event = await ledger.record_event(
            entity_type=EntityType.DECISION,
            entity_id="DEC-123",
            org_id="org_123",
            action="created",
            data={"test": "data"},
            confidence=0.60,
            source="ai"
        )
        
        assert event.requires_review() is True
        assert event.review_status == ReviewStatus.PENDING


# ============================================================================
# Orphan Reconciler Tests
# ============================================================================

class TestOrphanReconciler:
    """Tests for OrphanReconciler class."""
    
    def test_orphaned_memory_creation(self):
        """Test OrphanedMemory creation."""
        orphan = OrphanedMemory(
            memory_id="mem_123",
            ticket_id="TICKET-456",
            org_id="org_123",
            orphan_type=OrphanType.SOFT,
            memory_content={"test": "data"}
        )
        
        assert orphan.memory_id == "mem_123"
        assert orphan.orphan_type == OrphanType.SOFT
        assert orphan.status == OrphanStatus.DETECTED
    
    def test_orphaned_memory_serialization(self):
        """Test OrphanedMemory serialization."""
        orphan = OrphanedMemory(
            memory_id="mem_123",
            ticket_id="TICKET-456",
            org_id="org_123",
            orphan_type=OrphanType.HARD
        )
        
        orphan_dict = orphan.to_dict()
        assert orphan_dict["memory_id"] == "mem_123"
        assert orphan_dict["orphan_type"] == "hard"
        assert orphan_dict["status"] == "detected"
        
        # Deserialize
        restored = OrphanedMemory.from_dict(orphan_dict)
        assert restored.memory_id == orphan.memory_id
        assert restored.orphan_type == orphan.orphan_type
    
    def test_reconciliation_result(self):
        """Test ReconciliationResult creation and serialization."""
        result = ReconciliationResult(
            started_at=datetime.utcnow(),
            total_memories_scanned=1000,
            orphans_detected=10,
            soft_orphans=5,
            hard_orphans=3,
            re_linked=4,
            flagged=2
        )
        
        result_dict = result.to_dict()
        assert result_dict["total_memories_scanned"] == 1000
        assert result_dict["orphans_detected"] == 10
        assert result_dict["re_linked"] == 4
    
    @pytest.mark.asyncio
    async def test_reconciler_config(self):
        """Test ReconciliationConfig."""
        config = ReconciliationConfig(
            enable_auto_cleanup=True,
            enable_auto_relink=True,
            max_retries=5
        )
        
        assert config.enable_auto_cleanup is True
        assert config.enable_auto_relink is True
        assert config.max_retries == 5
    
    @pytest.mark.asyncio
    async def test_detect_orphans(self, mock_db_pool):
        """Test orphan detection."""
        # Mock memory manager
        mock_mm = AsyncMock()
        
        reconciler = OrphanReconciler(mock_mm, mock_db_pool)
        
        # Mock database responses
        conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = {"count": 100}
        conn.fetch.return_value = [
            {
                "memory_id": "mem_1",
                "ticket_id": "TICKET-1",
                "org_id": "org_123",
                "content": "test",
                "memory_type": "ticket_context",
                "created_at": datetime.utcnow()
            }
        ]
        
        result = ReconciliationResult(started_at=datetime.utcnow())
        orphans = await reconciler.detect_orphans(result)
        
        assert result.total_memories_scanned == 100
        assert isinstance(orphans, list)


# ============================================================================
# Integration Tests
# ============================================================================

class TestIntegration:
    """Integration tests for RAG module components."""
    
    @pytest.mark.asyncio
    async def test_end_to_end_context_fetch(self, mock_db_pool):
        """Test end-to-end context fetch flow."""
        # Create mock memory manager
        mock_mm = AsyncMock()
        mock_mm.search_memories.return_value = [
            MemorySearchResult(
                memory=MemoryEntry(
                    content="Resolution: Restart service",
                    memory_type=MemoryType.DECISION,
                    org_id="org_123",
                    ticket_id="TICKET-456",
                    memory_id="mem_789"
                ),
                similarity_score=0.92,
                match_type="semantic"
            )
        ]
        
        # Create fetcher
        fetcher = ContextFetcher(mock_mm, mock_db_pool)
        
        # Mock DB1 resolution
        conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = {
            "db1_ticket_id": "TICKET-456",
            "db1_table": "tickets",
            "resolution_status": "active"
        }
        
        # Create input
        input_data = FetchContextInput(
            query_text="What was the resolution for TICKET-456?",
            org_id="org_123"
        )
        
        # Fetch context
        result = await fetcher.fetch_context(input_data)
        
        assert result.org_id == "org_123"
        assert result.query == "What was the resolution for TICKET-456?"
        assert len(result.items) > 0
    
    @pytest.mark.asyncio
    async def test_idempotency_with_truth_ledger(self, mock_db_pool):
        """Test idempotency integration with truth ledger."""
        # Create idempotency store
        idemp_config = IdempotencyConfig(bucket_minutes=5)
        store = IdempotencyStore(mock_db_pool, idemp_config)
        
        # Create truth ledger
        ledger = TruthLedger(mock_db_pool)
        
        # Mock database for idempotency check
        conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = {
            "status": "processing",
            "created_at": datetime.utcnow()
        }
        
        # Check idempotency
        should_process, key = await store.check_idempotency(
            entity_id="DEC-123",
            event_type=EventType.DECISION_RECORDED
        )
        
        if should_process:
            # Record in truth ledger
            conn.fetchrow.return_value = None  # No previous hash
            event = await ledger.record_event(
                entity_type=EntityType.DECISION,
                entity_id="DEC-123",
                org_id="org_456",
                action="created",
                data={"decision": "test"},
                confidence=0.85,
                source="ai"
            )
            
            # Mark as processed
            await store.mark_processed(key, {"event_id": event.event_id})
            
            assert event.event_id is not None
            assert event.confidence == 0.85


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
