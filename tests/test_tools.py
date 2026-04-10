"""
Unit Tests for Cassandra AI Tools

Tests for:
- T13: create_ticket tool (secure INSERT-only)
- T14: add_memory tool (atomic map write)
- T15: fetch_context tool (SELECT only, <300ms p95)
- T16: Idempotency key implementation
- Tool registry

Run with: pytest tests/test_tools.py -v
"""

import pytest
import hashlib
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Dict, Any, Optional

# Import tools
from cassandra.tools.create_ticket import (
    CreateTicketTool,
    CreateTicketInput,
    CreateTicketResult,
    create_ticket,
    TicketPriority,
    TicketStatus,
    TicketCreationError
)
from cassandra.tools.add_memory import (
    AddMemoryTool,
    AddMemoryInput,
    AddMemoryResult,
    add_memory,
    MemoryWriteError
)
from cassandra.tools.fetch_context import (
    FetchContextTool,
    FetchContextInput,
    FetchContextResult,
    fetch_context,
    ContextResultItem,
    ContextFetchError
)
from cassandra.tools.registry import (
    ToolRegistry,
    ToolMetadata,
    OperationType,
    ToolExecutionError,
    ToolNotFoundError,
    ToolAuthorizationError
)
from cassandra.rag.idempotency import (
    generate_idempotency_key,
    EventType,
    IdempotencyStore,
    IdempotencyStatus
)
from cassandra.rag.memory_manager import (
    MemoryManager,
    MemoryEntry,
    MemoryType,
    MemoryPriority
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_db_pool():
    """Create a mock database pool."""
    pool = Mock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


@pytest.fixture
def mock_memory_manager():
    """Create a mock memory manager."""
    mm = Mock(spec=MemoryManager)
    mm.add_memory_with_map = AsyncMock(return_value="mem_test123")
    return mm


@pytest.fixture
def mock_encryption_service():
    """Create a mock encryption service."""
    es = Mock()
    es.encrypt = Mock(return_value={
        "ciphertext": "encrypted_content",
        "encrypted_data_key": "data_key_123"
    })
    es.decrypt = Mock(return_value="decrypted_content")
    return es


@pytest.fixture
def tool_registry(mock_db_pool, mock_memory_manager):
    """Create a tool registry with registered tools."""
    registry = ToolRegistry()
    
    # Register create_ticket tool
    create_tool = CreateTicketTool(mock_db_pool)
    registry.register(
        name="create_ticket",
        tool_instance=create_tool,
        metadata=ToolMetadata(
            description="Create a new ticket",
            input_schema="CreateTicketInput",
            output_schema="CreateTicketResult",
            operation_type=OperationType.INSERT
        )
    )
    
    # Register add_memory tool
    add_tool = AddMemoryTool(
        memory_manager=mock_memory_manager,
        db_pool=mock_db_pool
    )
    registry.register(
        name="add_memory",
        tool_instance=add_tool,
        metadata=ToolMetadata(
            description="Add a memory",
            input_schema="AddMemoryInput",
            output_schema="AddMemoryResult",
            operation_type=OperationType.INSERT
        )
    )
    
    return registry


# ============================================================================
# T13: Create Ticket Tests
# ============================================================================

class TestCreateTicketInput:
    """Tests for CreateTicketInput validation."""
    
    def test_valid_input(self):
        """Test valid input creation."""
        input_data = CreateTicketInput(
            title="Test Ticket",
            description="Test description",
            priority=TicketPriority.HIGH
        )
        assert input_data.title == "Test Ticket"
        assert input_data.priority == TicketPriority.HIGH
    
    def test_title_validation(self):
        """Test title sanitization."""
        input_data = CreateTicketInput(title="  Test Title  ")
        assert input_data.title == "Test Title"
    
    def test_title_empty_raises(self):
        """Test empty title raises error."""
        with pytest.raises(ValueError):
            CreateTicketInput(title="   ")
    
    def test_email_validation(self):
        """Test email validation."""
        # Valid email
        input_data = CreateTicketInput(
            title="Test",
            requester_email="user@example.com"
        )
        assert input_data.requester_email == "user@example.com"
        
        # Invalid email
        with pytest.raises(ValueError):
            CreateTicketInput(
                title="Test",
                requester_email="invalid-email"
            )
    
    def test_tags_deduplication(self):
        """Test tag deduplication."""
        input_data = CreateTicketInput(
            title="Test",
            tags=["tag1", "TAG1", "tag2", "tag1"]
        )
        assert len(input_data.tags) == 2
        assert "tag1" in input_data.tags
        assert "tag2" in input_data.tags


class TestCreateTicketTool:
    """Tests for CreateTicketTool."""
    
    @pytest.mark.asyncio
    async def test_create_success(self, mock_db_pool):
        """Test successful ticket creation."""
        tool = CreateTicketTool(mock_db_pool)
        
        input_data = CreateTicketInput(
            title="Test Ticket",
            description="Test description",
            priority=TicketPriority.HIGH
        )
        
        result = await tool.create(
            input_data=input_data,
            org_id="org_test123",
            user_id="user_test"
        )
        
        assert isinstance(result, CreateTicketResult)
        assert result.status == TicketStatus.ACTIVE
        assert result.org_id == "org_test123"
        assert result.title == "Test Ticket"
        assert result.priority == TicketPriority.HIGH
        assert result.ticket_id is not None
        assert result.ticket_number is not None
    
    @pytest.mark.asyncio
    async def test_create_missing_org_id(self, mock_db_pool):
        """Test that missing org_id raises error."""
        tool = CreateTicketTool(mock_db_pool)
        
        input_data = CreateTicketInput(title="Test")
        
        with pytest.raises(ValueError, match="org_id is required"):
            await tool.create(input_data=input_data, org_id="")
    
    @pytest.mark.asyncio
    async def test_create_idempotency_hit(self, mock_db_pool):
        """Test idempotency key returns existing ticket."""
        tool = CreateTicketTool(mock_db_pool)
        
        # Mock existing ticket
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            "ticket_id": "ticket_existing",
            "created_at": datetime.utcnow(),
            "status": "active",
            "title": "Existing Ticket",
            "priority": "high",
            "ticket_number": "TICKET-1234"
        })
        mock_db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        
        input_data = CreateTicketInput(
            title="Test",
            idempotency_key="idem_key_123"
        )
        
        result = await tool.create(
            input_data=input_data,
            org_id="org_test123"
        )
        
        assert result.ticket_id == "ticket_existing"
    
    @pytest.mark.asyncio
    async def test_create_batch(self, mock_db_pool):
        """Test batch ticket creation."""
        tool = CreateTicketTool(mock_db_pool)
        
        inputs = [
            CreateTicketInput(title="Ticket 1"),
            CreateTicketInput(title="Ticket 2")
        ]
        
        results = await tool.create_batch(
            inputs=inputs,
            org_id="org_test123"
        )
        
        assert len(results) == 2
        assert all(isinstance(r, CreateTicketResult) for r in results)


# ============================================================================
# T14: Add Memory Tests
# ============================================================================

class TestAddMemoryInput:
    """Tests for AddMemoryInput validation."""
    
    def test_valid_input(self):
        """Test valid input creation."""
        input_data = AddMemoryInput(
            content="Test memory content",
            memory_type=MemoryType.TICKET_CONTEXT,
            ticket_id="TICKET-123"
        )
        assert input_data.content == "Test memory content"
        assert input_data.ticket_id == "TICKET-123"
    
    def test_content_validation(self):
        """Test content sanitization."""
        input_data = AddMemoryInput(content="  Test Content  ")
        assert input_data.content == "Test Content"
    
    def test_content_empty_raises(self):
        """Test empty content raises error."""
        with pytest.raises(ValueError):
            AddMemoryInput(content="   ")
    
    def test_dict_content(self):
        """Test dict content validation."""
        input_data = AddMemoryInput(
            content={"key": "value", "nested": {"data": "test"}}
        )
        assert isinstance(input_data.content, dict)


class TestAddMemoryTool:
    """Tests for AddMemoryTool."""
    
    @pytest.mark.asyncio
    async def test_add_success(self, mock_db_pool, mock_memory_manager):
        """Test successful memory addition."""
        tool = AddMemoryTool(
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool
        )
        
        input_data = AddMemoryInput(
            content="Test memory",
            ticket_id="TICKET-123",
            encrypt=True
        )
        
        result = await tool.add(
            input_data=input_data,
            org_id="org_test123",
            user_id="user_test"
        )
        
        assert isinstance(result, AddMemoryResult)
        assert result.status == "success"
        assert result.org_id == "org_test123"
        assert result.ticket_id == "TICKET-123"
        assert result.encrypted == True
    
    @pytest.mark.asyncio
    async def test_add_missing_org_id(self, mock_db_pool, mock_memory_manager):
        """Test that missing org_id raises error."""
        tool = AddMemoryTool(
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool
        )
        
        input_data = AddMemoryInput(content="Test")
        
        with pytest.raises(ValueError, match="org_id is required"):
            await tool.add(input_data=input_data, org_id="")
    
    @pytest.mark.asyncio
    async def test_add_idempotency_hit(self, mock_db_pool, mock_memory_manager):
        """Test idempotency returns existing memory."""
        tool = AddMemoryTool(
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool
        )
        
        # Mock existing memory
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            "memory_id": "mem_existing",
            "created_at": datetime.utcnow(),
            "ticket_id": "TICKET-123",
            "encrypted": True
        })
        mock_db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        
        input_data = AddMemoryInput(
            content="Test",
            idempotency_key="idem_key_123"
        )
        
        result = await tool.add(
            input_data=input_data,
            org_id="org_test123"
        )
        
        assert result.status == "duplicate"
        assert result.memory_id == "mem_existing"


# ============================================================================
# T15: Fetch Context Tests
# ============================================================================

class TestFetchContextInput:
    """Tests for FetchContextInput validation."""
    
    def test_valid_input(self):
        """Test valid input creation."""
        input_data = FetchContextInput(
            query_text="What was the resolution?",
            max_results=5
        )
        assert input_data.query_text == "What was the resolution?"
        assert input_data.max_results == 5
    
    def test_query_validation(self):
        """Test query sanitization."""
        input_data = FetchContextInput(query_text="  Test Query  ")
        assert input_data.query_text == "Test Query"
    
    def test_timeout_validation(self):
        """Test timeout bounds."""
        # Valid timeout
        input_data = FetchContextInput(
            query_text="Test",
            timeout_ms=300
        )
        assert input_data.timeout_ms == 300
        
        # Too low
        with pytest.raises(ValueError):
            FetchContextInput(query_text="Test", timeout_ms=50)
        
        # Too high
        with pytest.raises(ValueError):
            FetchContextInput(query_text="Test", timeout_ms=10000)


class TestFetchContextTool:
    """Tests for FetchContextTool."""
    
    @pytest.mark.asyncio
    async def test_fetch_success(self, mock_db_pool, mock_memory_manager):
        """Test successful context fetch."""
        tool = FetchContextTool(
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool
        )
        
        # Mock context fetcher
        mock_result = Mock()
        mock_result.items = []
        mock_result.total_found = 0
        mock_result.fetch_time_ms = 100.0
        mock_result.cache_hit = False
        mock_result.sources_used = set()
        
        tool.context_fetcher.fetch_context = AsyncMock(return_value=mock_result)
        
        input_data = FetchContextInput(
            query_text="Test query",
            max_results=5
        )
        
        result = await tool.fetch(
            input_data=input_data,
            org_id="org_test123"
        )
        
        assert isinstance(result, FetchContextResult)
        assert result.org_id == "org_test123"
        assert result.query == "Test query"
        assert result.latency_status == "optimal"  # 100ms < 150ms
    
    @pytest.mark.asyncio
    async def test_fetch_missing_org_id(self, mock_db_pool, mock_memory_manager):
        """Test that missing org_id raises error."""
        tool = FetchContextTool(
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool
        )
        
        input_data = FetchContextInput(query_text="Test")
        
        with pytest.raises(ValueError, match="org_id is required"):
            await tool.fetch(input_data=input_data, org_id="")
    
    @pytest.mark.asyncio
    async def test_fetch_latency_status(self, mock_db_pool, mock_memory_manager):
        """Test latency status calculation."""
        tool = FetchContextTool(
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool
        )
        
        # Test optimal (<150ms)
        assert tool._get_latency_status(100) == "optimal"
        assert tool._get_latency_status(149) == "optimal"
        
        # Test acceptable (150-300ms)
        assert tool._get_latency_status(150) == "acceptable"
        assert tool._get_latency_status(300) == "acceptable"
        
        # Test slow (>300ms)
        assert tool._get_latency_status(301) == "slow"
        assert tool._get_latency_status(500) == "slow"


# ============================================================================
# T16: Idempotency Tests
# ============================================================================

class TestIdempotencyKeyGeneration:
    """Tests for idempotency key generation (T16)."""
    
    def test_simple_formula(self):
        """Test T16 simple formula: SHA256(entity_id + event_type + floor(timestamp / 300))."""
        timestamp = datetime(2024, 1, 15, 10, 23, 45)
        
        key = generate_idempotency_key(
            entity_id="TICKET-123",
            event_type=EventType.TICKET_UPDATED,
            timestamp=timestamp,
            use_simple_formula=True
        )
        
        # Verify format
        assert key.startswith("idemp:ticket_updated:")
        # Verify deterministic
        key2 = generate_idempotency_key(
            entity_id="TICKET-123",
            event_type=EventType.TICKET_UPDATED,
            timestamp=timestamp,
            use_simple_formula=True
        )
        assert key == key2
    
    def test_same_bucket_same_key(self):
        """Test that events in same 5-min bucket generate same key."""
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        
        # Events within same 5-minute window
        times = [
            base_time,
            base_time + timedelta(minutes=2),
            base_time + timedelta(minutes=4, seconds=59)
        ]
        
        keys = [
            generate_idempotency_key(
                entity_id="TICKET-123",
                event_type=EventType.TICKET_UPDATED,
                timestamp=t,
                use_simple_formula=True
            )
            for t in times
        ]
        
        # All keys should be identical
        assert len(set(keys)) == 1
    
    def test_different_bucket_different_key(self):
        """Test that events in different buckets generate different keys."""
        base_time = datetime(2024, 1, 15, 10, 0, 0)
        
        # Events in different 5-minute windows
        times = [
            base_time,
            base_time + timedelta(minutes=5),
            base_time + timedelta(minutes=10)
        ]
        
        keys = [
            generate_idempotency_key(
                entity_id="TICKET-123",
                event_type=EventType.TICKET_UPDATED,
                timestamp=t,
                use_simple_formula=True
            )
            for t in times
        ]
        
        # All keys should be different
        assert len(set(keys)) == 3
    
    def test_different_entities_different_keys(self):
        """Test that different entities generate different keys."""
        timestamp = datetime.utcnow()
        
        key1 = generate_idempotency_key(
            entity_id="TICKET-123",
            event_type=EventType.TICKET_UPDATED,
            timestamp=timestamp,
            use_simple_formula=True
        )
        
        key2 = generate_idempotency_key(
            entity_id="TICKET-456",
            event_type=EventType.TICKET_UPDATED,
            timestamp=timestamp,
            use_simple_formula=True
        )
        
        assert key1 != key2


class TestIdempotencyStore:
    """Tests for IdempotencyStore with memory_archive."""
    
    @pytest.mark.asyncio
    async def test_check_memory_archive_idempotency_new(self, mock_db_pool):
        """Test check returns should_process=True for new key."""
        store = IdempotencyStore(mock_db_pool)
        
        # Mock no existing entry
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        
        should_process, existing_id, cached = await store.check_memory_archive_idempotency(
            idempotency_key="idem_new_key",
            org_id="org_test123"
        )
        
        assert should_process == True
        assert existing_id is None
        assert cached is None
    
    @pytest.mark.asyncio
    async def test_check_memory_archive_idempotency_duplicate(self, mock_db_pool):
        """Test check returns existing memory_id for duplicate key (T16 test)."""
        store = IdempotencyStore(mock_db_pool)
        
        # Mock existing entry
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            "memory_id": "mem_existing_123",
            "content": "Test content",
            "memory_type": "ticket_context",
            "created_at": datetime.utcnow(),
            "metadata": {}
        })
        mock_db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        
        should_process, existing_id, cached = await store.check_memory_archive_idempotency(
            idempotency_key="idem_existing_key",
            org_id="org_test123"
        )
        
        # T16: Duplicate call returns existing memory_id
        assert should_process == False
        assert existing_id == "mem_existing_123"
        assert cached is not None
        assert cached["memory_id"] == "mem_existing_123"
    
    @pytest.mark.asyncio
    async def test_write_memory_with_idempotency_new(self, mock_db_pool):
        """Test write with idempotency for new key."""
        store = IdempotencyStore(mock_db_pool)
        
        # Mock successful insert
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            "memory_id": "mem_new_123",
            "insert_status": "new"
        })
        mock_db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        
        is_new, memory_id = await store.write_memory_with_idempotency(
            idempotency_key="idem_new_key",
            org_id="org_test123",
            memory_id="mem_new_123",
            content="Test content",
            memory_type="ticket_context"
        )
        
        assert is_new == True
        assert memory_id == "mem_new_123"
    
    @pytest.mark.asyncio
    async def test_write_memory_with_idempotency_duplicate(self, mock_db_pool):
        """Test write with idempotency for duplicate key."""
        store = IdempotencyStore(mock_db_pool)
        
        # Mock existing entry (conflict)
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            "memory_id": "mem_existing_123",
            "insert_status": "existing"
        })
        mock_db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        
        is_new, memory_id = await store.write_memory_with_idempotency(
            idempotency_key="idem_existing_key",
            org_id="org_test123",
            memory_id="mem_new_123",
            content="Test content",
            memory_type="ticket_context"
        )
        
        assert is_new == False
        assert memory_id == "mem_existing_123"


# ============================================================================
# Tool Registry Tests
# ============================================================================

class TestToolRegistry:
    """Tests for ToolRegistry."""
    
    def test_register_tool(self):
        """Test tool registration."""
        registry = ToolRegistry()
        
        mock_tool = Mock()
        registry.register(
            name="test_tool",
            tool_instance=mock_tool,
            metadata=ToolMetadata(
                description="Test tool",
                input_schema="TestInput",
                output_schema="TestOutput",
                operation_type=OperationType.SELECT
            )
        )
        
        assert "test_tool" in registry._tools
        assert registry._tools["test_tool"].metadata.operation_type == OperationType.SELECT
    
    def test_register_duplicate_raises(self):
        """Test registering duplicate tool raises error."""
        registry = ToolRegistry()
        
        mock_tool = Mock()
        registry.register(
            name="test_tool",
            tool_instance=mock_tool,
            metadata=ToolMetadata(
                description="Test tool",
                input_schema="TestInput",
                output_schema="TestOutput"
            )
        )
        
        with pytest.raises(ValueError, match="already registered"):
            registry.register(
                name="test_tool",
                tool_instance=mock_tool,
                metadata=ToolMetadata(
                    description="Test tool 2",
                    input_schema="TestInput",
                    output_schema="TestOutput"
                )
            )
    
    def test_list_tools(self):
        """Test listing registered tools."""
        registry = ToolRegistry()
        
        mock_tool = Mock()
        registry.register(
            name="tool1",
            tool_instance=mock_tool,
            metadata=ToolMetadata(
                description="Tool 1",
                input_schema="Input1",
                output_schema="Output1",
                operation_type=OperationType.INSERT
            )
        )
        registry.register(
            name="tool2",
            tool_instance=mock_tool,
            metadata=ToolMetadata(
                description="Tool 2",
                input_schema="Input2",
                output_schema="Output2",
                operation_type=OperationType.SELECT
            )
        )
        
        all_tools = registry.list_tools()
        assert len(all_tools) == 2
        
        insert_tools = registry.list_tools(operation_type=OperationType.INSERT)
        assert len(insert_tools) == 1
        assert insert_tools[0]["name"] == "tool1"
    
    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self):
        """Test executing non-existent tool raises error."""
        registry = ToolRegistry()
        
        with pytest.raises(ToolNotFoundError):
            await registry.execute(
                tool_name="nonexistent",
                input_data={},
                org_id="org_test"
            )
    
    @pytest.mark.asyncio
    async def test_execute_missing_org_id(self, tool_registry):
        """Test executing without org_id raises error."""
        with pytest.raises(ToolAuthorizationError, match="org_id is required"):
            await tool_registry.execute(
                tool_name="create_ticket",
                input_data={"title": "Test"},
                org_id=""
            )
    
    @pytest.mark.asyncio
    async def test_execute_success(self, tool_registry):
        """Test successful tool execution."""
        # Mock the tool's create method
        mock_result = Mock()
        mock_result.to_dict = Mock(return_value={
            "ticket_id": "ticket_123",
            "status": "active"
        })
        
        tool_registry._tools["create_ticket"].instance.create = AsyncMock(
            return_value=mock_result
        )
        
        result = await tool_registry.execute(
            tool_name="create_ticket",
            input_data={"title": "Test Ticket"},
            org_id="org_test123",
            user_id="user_test"
        )
        
        assert result.success == True
        assert result.data["ticket_id"] == "ticket_123"
        assert result.org_id == "org_test123"
    
    def test_get_metrics(self, tool_registry):
        """Test getting registry metrics."""
        metrics = tool_registry.get_metrics()
        
        assert metrics["registered_tools"] == 2
        assert "create_ticket" in metrics["tools"]
        assert "add_memory" in metrics["tools"]


# ============================================================================
# Integration Tests
# ============================================================================

class TestToolIntegration:
    """Integration tests for tool interactions."""
    
    @pytest.mark.asyncio
    async def test_create_ticket_then_add_memory(self, mock_db_pool, mock_memory_manager):
        """Test creating ticket then adding related memory."""
        # Create ticket
        create_tool = CreateTicketTool(mock_db_pool)
        ticket_input = CreateTicketInput(title="Test Issue")
        
        # Mock the connection for ticket creation
        mock_conn = AsyncMock()
        mock_db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        
        ticket_result = await create_tool.create(
            input_data=ticket_input,
            org_id="org_test123"
        )
        
        # Add memory for the ticket
        add_tool = AddMemoryTool(
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool
        )
        
        memory_input = AddMemoryInput(
            content="Customer reported the issue",
            ticket_id=ticket_result.ticket_id
        )
        
        memory_result = await add_tool.add(
            input_data=memory_input,
            org_id="org_test123"
        )
        
        assert memory_result.ticket_id == ticket_result.ticket_id
    
    @pytest.mark.asyncio
    async def test_end_to_end_idempotency(self, mock_db_pool):
        """Test end-to-end idempotency flow."""
        # Generate idempotency key
        idem_key = generate_idempotency_key(
            entity_id="TICKET-123",
            event_type=EventType.TICKET_CREATED,
            use_simple_formula=True
        )
        
        # Create ticket with idempotency key
        create_tool = CreateTicketTool(mock_db_pool)
        ticket_input = CreateTicketInput(
            title="Test",
            idempotency_key=idem_key
        )
        
        # Mock first call - no existing ticket
        mock_conn1 = AsyncMock()
        mock_conn1.fetchrow = AsyncMock(return_value=None)
        mock_db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn1)
        
        result1 = await create_tool.create(
            input_data=ticket_input,
            org_id="org_test123"
        )
        
        # Mock second call - existing ticket
        mock_conn2 = AsyncMock()
        mock_conn2.fetchrow = AsyncMock(return_value={
            "ticket_id": result1.ticket_id,
            "created_at": datetime.utcnow(),
            "status": "active",
            "title": "Test",
            "priority": "medium",
            "ticket_number": result1.ticket_number
        })
        mock_db_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn2)
        
        result2 = await create_tool.create(
            input_data=ticket_input,
            org_id="org_test123"
        )
        
        # Both calls should return same ticket
        assert result1.ticket_id == result2.ticket_id


# ============================================================================
# Performance Tests
# ============================================================================

class TestPerformance:
    """Performance tests for latency requirements."""
    
    @pytest.mark.asyncio
    async def test_fetch_context_latency(self, mock_db_pool, mock_memory_manager):
        """Test fetch context meets <300ms p95 target."""
        import time
        
        tool = FetchContextTool(
            memory_manager=mock_memory_manager,
            db_pool=mock_db_pool
        )
        
        # Mock fast response
        mock_result = Mock()
        mock_result.items = []
        mock_result.total_found = 0
        mock_result.fetch_time_ms = 50.0
        mock_result.cache_hit = False
        mock_result.sources_used = set()
        
        tool.context_fetcher.fetch_context = AsyncMock(return_value=mock_result)
        
        input_data = FetchContextInput(query_text="Test query")
        
        start = time.time()
        result = await tool.fetch(input_data=input_data, org_id="org_test123")
        elapsed_ms = (time.time() - start) * 1000
        
        # Should be well under 300ms with mocked dependencies
        assert elapsed_ms < 100  # Even with overhead
        assert result.latency_status == "optimal"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
