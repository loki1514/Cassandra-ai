"""
Tool Registry Module

Provides the core tool registration and execution system for Cassandra AI.
Implements:
- Tool registration with metadata
- Secure execution with org_id validation
- Audit logging
- Performance metrics
- Error handling

Security Features:
- org_id must come from JWT context
- Input validation before execution
- Operation type tracking (insert/select)
- Authorization checks
"""

import time
import logging
from typing import Any, Dict, List, Optional, Callable, Type, Union
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from pydantic import BaseModel

# Configure logging
logger = logging.getLogger(__name__)


class OperationType(str, Enum):
    """Types of tool operations for security tracking."""
    INSERT = "insert"      # INSERT-only operations
    SELECT = "select"      # SELECT-only (read-only) operations
    UPDATE = "update"      # UPDATE operations (restricted)
    DELETE = "delete"      # DELETE operations (restricted)
    MIXED = "mixed"        # Mixed operations (requires extra scrutiny)


@dataclass
class ToolMetadata:
    """
    Metadata for a registered tool.
    
    Attributes:
        description: Human-readable description
        input_schema: Pydantic model class name for input
        output_schema: Pydantic model class name for output
        requires_org_id: Whether org_id is required (should always be True)
        requires_auth: Whether authentication is required
        operation_type: Type of database operation
        rate_limit_per_minute: Optional rate limit
        tags: List of tags for categorization
    """
    description: str
    input_schema: str
    output_schema: str
    requires_org_id: bool = True
    requires_auth: bool = True
    operation_type: OperationType = OperationType.SELECT
    rate_limit_per_minute: Optional[int] = None
    tags: List[str] = field(default_factory=list)


@dataclass
class ToolExecutionResult:
    """
    Result of a tool execution.
    
    Attributes:
        success: Whether execution succeeded
        data: Result data (if success)
        error: Error message (if failure)
        execution_time_ms: Execution time in milliseconds
        tool_name: Name of the tool executed
        org_id: Organization ID from JWT
        user_id: User ID from JWT
        timestamp: Execution timestamp
    """
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    tool_name: Optional[str] = None
    org_id: Optional[str] = None
    user_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
            "tool_name": self.tool_name,
            "org_id": self.org_id,
            "user_id": self.user_id,
            "timestamp": self.timestamp.isoformat()
        }


class ToolExecutionError(Exception):
    """Raised when tool execution fails."""
    
    def __init__(
        self,
        message: str,
        tool_name: Optional[str] = None,
        org_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message)
        self.tool_name = tool_name
        self.org_id = org_id
        self.details = details or {}


class ToolNotFoundError(ToolExecutionError):
    """Raised when requested tool is not found."""
    pass


class ToolAuthorizationError(ToolExecutionError):
    """Raised when tool execution is not authorized."""
    pass


class ToolValidationError(ToolExecutionError):
    """Raised when tool input validation fails."""
    pass


class RegisteredTool:
    """
    Wrapper for a registered tool with metadata.
    
    Attributes:
        name: Tool name/identifier
        instance: Tool instance
        metadata: Tool metadata
        _rate_limit_store: Simple in-memory rate limit tracking
    """
    
    def __init__(
        self,
        name: str,
        instance: Any,
        metadata: ToolMetadata
    ):
        self.name = name
        self.instance = instance
        self.metadata = metadata
        self._rate_limit_store: Dict[str, List[float]] = {}
    
    def check_rate_limit(self, org_id: str) -> bool:
        """
        Check if org has exceeded rate limit.
        
        Args:
            org_id: Organization ID
            
        Returns:
            True if within limit, False if exceeded
        """
        if not self.metadata.rate_limit_per_minute:
            return True
        
        now = time.time()
        window_start = now - 60  # 1 minute window
        
        # Get or create rate limit list for org
        if org_id not in self._rate_limit_store:
            self._rate_limit_store[org_id] = []
        
        # Remove old entries outside window
        self._rate_limit_store[org_id] = [
            t for t in self._rate_limit_store[org_id] if t > window_start
        ]
        
        # Check limit
        if len(self._rate_limit_store[org_id]) >= self.metadata.rate_limit_per_minute:
            return False
        
        # Record this request
        self._rate_limit_store[org_id].append(now)
        return True


class ToolRegistry:
    """
    Central registry for all Cassandra AI tools.
    
    Provides:
    - Tool registration with metadata
    - Secure execution with validation
    - Audit logging
    - Performance tracking
    - Rate limiting
    
    Usage:
        registry = ToolRegistry()
        
        # Register a tool
        registry.register(
            name="create_ticket",
            tool_instance=CreateTicketTool(db_pool),
            metadata=ToolMetadata(
                description="Create a ticket",
                input_schema="CreateTicketInput",
                output_schema="CreateTicketResult",
                operation_type=OperationType.INSERT
            )
        )
        
        # Execute a tool
        result = await registry.execute(
            tool_name="create_ticket",
            input_data={"title": "..."},
            org_id="org_from_jwt",
            user_id="user_from_jwt"
        )
    """
    
    def __init__(self):
        """Initialize the tool registry."""
        self._tools: Dict[str, RegisteredTool] = {}
        self._execution_history: List[ToolExecutionResult] = []
        self._max_history_size = 1000
        logger.info("ToolRegistry initialized")
    
    def register(
        self,
        name: str,
        tool_instance: Any,
        metadata: ToolMetadata
    ) -> None:
        """
        Register a tool with the registry.
        
        Args:
            name: Unique tool name
            tool_instance: Tool instance with async methods
            metadata: Tool metadata
            
        Raises:
            ValueError: If tool with same name already registered
        """
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")
        
        self._tools[name] = RegisteredTool(
            name=name,
            instance=tool_instance,
            metadata=metadata
        )
        
        logger.info(
            f"Tool registered: {name} | "
            f"operation: {metadata.operation_type.value} | "
            f"auth_required: {metadata.requires_auth}"
        )
    
    def unregister(self, name: str) -> bool:
        """
        Unregister a tool.
        
        Args:
            name: Tool name to unregister
            
        Returns:
            True if unregistered, False if not found
        """
        if name in self._tools:
            del self._tools[name]
            logger.info(f"Tool unregistered: {name}")
            return True
        return False
    
    def get_tool(self, name: str) -> Optional[RegisteredTool]:
        """
        Get a registered tool by name.
        
        Args:
            name: Tool name
            
        Returns:
            RegisteredTool if found, None otherwise
        """
        return self._tools.get(name)
    
    def list_tools(
        self,
        operation_type: Optional[OperationType] = None,
        tag: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List registered tools with optional filtering.
        
        Args:
            operation_type: Filter by operation type
            tag: Filter by tag
            
        Returns:
            List of tool metadata dictionaries
        """
        results = []
        
        for name, tool in self._tools.items():
            # Apply filters
            if operation_type and tool.metadata.operation_type != operation_type:
                continue
            if tag and tag not in tool.metadata.tags:
                continue
            
            results.append({
                "name": name,
                "description": tool.metadata.description,
                "input_schema": tool.metadata.input_schema,
                "output_schema": tool.metadata.output_schema,
                "requires_org_id": tool.metadata.requires_org_id,
                "requires_auth": tool.metadata.requires_auth,
                "operation_type": tool.metadata.operation_type.value,
                "tags": tool.metadata.tags
            })
        
        return results
    
    async def execute(
        self,
        tool_name: str,
        input_data: Dict[str, Any],
        org_id: str,
        user_id: Optional[str] = None,
        skip_validation: bool = False
    ) -> ToolExecutionResult:
        """
        Execute a tool with full validation and audit logging.
        
        Args:
            tool_name: Name of the tool to execute
            input_data: Tool input data
            org_id: Organization ID from JWT (REQUIRED)
            user_id: User ID from JWT for audit
            skip_validation: Skip input validation (for internal use only)
            
        Returns:
            ToolExecutionResult with execution details
            
        Raises:
            ToolNotFoundError: If tool not found
            ToolAuthorizationError: If not authorized
            ToolValidationError: If input validation fails
            ToolExecutionError: If execution fails
        """
        start_time = time.time()
        timestamp = datetime.utcnow()
        
        # Get tool
        tool = self._tools.get(tool_name)
        if not tool:
            error_msg = f"Tool not found: {tool_name}"
            logger.error(error_msg)
            
            result = ToolExecutionResult(
                success=False,
                error=error_msg,
                execution_time_ms=(time.time() - start_time) * 1000,
                tool_name=tool_name,
                org_id=org_id,
                user_id=user_id,
                timestamp=timestamp
            )
            self._record_execution(result)
            raise ToolNotFoundError(error_msg, tool_name=tool_name)
        
        # Validate org_id (security check)
        if tool.metadata.requires_org_id and not org_id:
            error_msg = "org_id is required and must come from JWT context"
            logger.error(f"{error_msg} for tool {tool_name}")
            
            result = ToolExecutionResult(
                success=False,
                error=error_msg,
                execution_time_ms=(time.time() - start_time) * 1000,
                tool_name=tool_name,
                org_id=org_id,
                user_id=user_id,
                timestamp=timestamp
            )
            self._record_execution(result)
            raise ToolAuthorizationError(
                error_msg,
                tool_name=tool_name,
                org_id=org_id
            )
        
        # Check rate limit
        if not tool.check_rate_limit(org_id):
            error_msg = f"Rate limit exceeded for tool {tool_name}"
            logger.warning(f"{error_msg} (org: {org_id})")
            
            result = ToolExecutionResult(
                success=False,
                error=error_msg,
                execution_time_ms=(time.time() - start_time) * 1000,
                tool_name=tool_name,
                org_id=org_id,
                user_id=user_id,
                timestamp=timestamp
            )
            self._record_execution(result)
            raise ToolAuthorizationError(
                error_msg,
                tool_name=tool_name,
                org_id=org_id
            )
        
        try:
            # Log execution attempt
            logger.info(
                f"Executing tool: {tool_name} | "
                f"org: {org_id} | "
                f"user: {user_id or 'system'} | "
                f"op: {tool.metadata.operation_type.value}"
            )
            
            # Execute tool
            # Tools should have an async method like 'create', 'add', or 'fetch'
            method_name = self._get_execute_method(tool_name)
            execute_method = getattr(tool.instance, method_name, None)
            
            if not execute_method:
                raise ToolExecutionError(
                    f"Tool {tool_name} has no executable method",
                    tool_name=tool_name
                )
            
            # Build input model if needed
            if not skip_validation:
                input_model = self._build_input_model(tool_name, input_data)
            else:
                input_model = input_data
            
            # Execute
            tool_result = await execute_method(input_model, org_id, user_id)
            
            # Convert result to dict
            if hasattr(tool_result, 'to_dict'):
                result_data = tool_result.to_dict()
            elif hasattr(tool_result, 'dict'):
                result_data = tool_result.dict()
            elif isinstance(tool_result, dict):
                result_data = tool_result
            else:
                result_data = {"result": str(tool_result)}
            
            execution_time_ms = (time.time() - start_time) * 1000
            
            # Build success result
            result = ToolExecutionResult(
                success=True,
                data=result_data,
                execution_time_ms=execution_time_ms,
                tool_name=tool_name,
                org_id=org_id,
                user_id=user_id,
                timestamp=timestamp
            )
            
            # Log success
            logger.info(
                f"Tool execution successful: {tool_name} | "
                f"time: {execution_time_ms:.2f}ms | "
                f"org: {org_id}"
            )
            
        except Exception as e:
            execution_time_ms = (time.time() - start_time) * 1000
            error_msg = str(e)
            
            logger.error(
                f"Tool execution failed: {tool_name} | "
                f"error: {error_msg} | "
                f"time: {execution_time_ms:.2f}ms | "
                f"org: {org_id}"
            )
            
            result = ToolExecutionResult(
                success=False,
                error=error_msg,
                execution_time_ms=execution_time_ms,
                tool_name=tool_name,
                org_id=org_id,
                user_id=user_id,
                timestamp=timestamp
            )
            
            self._record_execution(result)
            raise ToolExecutionError(
                error_msg,
                tool_name=tool_name,
                org_id=org_id,
                details={"execution_time_ms": execution_time_ms}
            )
        
        self._record_execution(result)
        return result
    
    def _get_execute_method(self, tool_name: str) -> str:
        """
        Get the execution method name for a tool.
        
        Args:
            tool_name: Name of the tool
            
        Returns:
            Method name to call
        """
        # Map tool names to their primary execution methods
        method_map = {
            "create_ticket": "create",
            "add_memory": "add",
            "fetch_context": "fetch"
        }
        return method_map.get(tool_name, "execute")
    
    def _build_input_model(self, tool_name: str, input_data: Dict[str, Any]) -> Any:
        """
        Build the appropriate input model for a tool.
        
        Args:
            tool_name: Name of the tool
            input_data: Raw input data
            
        Returns:
            Validated input model
        """
        from .create_ticket import CreateTicketInput
        from .add_memory import AddMemoryInput
        from .fetch_context import FetchContextInput
        
        model_map = {
            "create_ticket": CreateTicketInput,
            "add_memory": AddMemoryInput,
            "fetch_context": FetchContextInput
        }
        
        model_class = model_map.get(tool_name)
        if model_class:
            return model_class(**input_data)
        
        return input_data
    
    def _record_execution(self, result: ToolExecutionResult) -> None:
        """
        Record execution result for audit and metrics.
        
        Args:
            result: Execution result to record
        """
        self._execution_history.append(result)
        
        # Trim history if needed
        if len(self._execution_history) > self._max_history_size:
            self._execution_history = self._execution_history[-self._max_history_size:]
    
    def get_execution_history(
        self,
        tool_name: Optional[str] = None,
        org_id: Optional[str] = None,
        limit: int = 100
    ) -> List[ToolExecutionResult]:
        """
        Get execution history with optional filtering.
        
        Args:
            tool_name: Filter by tool name
            org_id: Filter by organization
            limit: Maximum results to return
            
        Returns:
            List of execution results
        """
        results = self._execution_history
        
        if tool_name:
            results = [r for r in results if r.tool_name == tool_name]
        
        if org_id:
            results = [r for r in results if r.org_id == org_id]
        
        return results[-limit:]
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Get registry metrics.
        
        Returns:
            Dictionary of metrics
        """
        total_executions = len(self._execution_history)
        successful = sum(1 for r in self._execution_history if r.success)
        failed = total_executions - successful
        
        avg_execution_time = 0.0
        if total_executions > 0:
            avg_execution_time = sum(
                r.execution_time_ms for r in self._execution_history
            ) / total_executions
        
        return {
            "registered_tools": len(self._tools),
            "total_executions": total_executions,
            "successful_executions": successful,
            "failed_executions": failed,
            "success_rate": successful / total_executions if total_executions > 0 else 0,
            "average_execution_time_ms": avg_execution_time,
            "tools": list(self._tools.keys())
        }
