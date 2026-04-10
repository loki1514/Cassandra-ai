"""
backend/providers/llm/tool_registry.py — Tool definitions and execution for Cassandra.

Cassandra's tool registry defines what she CAN do:
1. save_insight: Save decisions, risks, action items to Supermemory
2. fetch_context: Retrieve relevant context from institutional memory
3. autopilot_action: Trigger external Autopilot API actions
4. save_transcript: Persist transcript segments to database

These tools make Cassandra an active participant in the organization's
knowledge graph, not just a conversational interface.
"""

import asyncio
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from backend.utils.logging_config import get_logger

logger = get_logger("cassandra.llm.tool_registry")


@dataclass
class ToolResult:
    """Result from executing a tool."""

    name: str
    success: bool
    output: Any
    error: str | None = None
    duration_ms: int = 0


@dataclass
class ToolDefinition:
    """Definition of a callable tool for the LLM."""

    name: str
    description: str
    parameters: dict  # OpenAI function calling schema
    handler: Callable[..., Coroutine[Any, Any, ToolResult]]


class ToolHandler(ABC):
    """Base class for tool handlers."""

    @abstractmethod
    async def execute(self, arguments: dict, context: dict) -> ToolResult:
        """Execute the tool with the given arguments and context."""
        raise NotImplementedError


class SaveInsightHandler(ToolHandler):
    """Save an insight to the institutional memory (Supermemory)."""

    async def execute(self, arguments: dict, context: dict) -> ToolResult:
        """Save a decision, risk, action item, or key fact."""
        from backend.config import get_settings
        import httpx

        settings = get_settings()
        start = time.time()

        insight = arguments.get("insight", "")
        category = arguments.get("category", "key_fact")
        confidence = arguments.get("confidence", "medium")
        owner = arguments.get("owner", "")

        org_id = context.get("org_id", "")
        session_id = context.get("session_id", "")
        meeting_id = context.get("meeting_id", "")

        # Map category to artifact type
        type_map = {
            "decision": "decision",
            "risk_flag": "risk",
            "key_fact": "topic",
            "action_item": "topic",
            "contradiction": "topic",
            "pattern": "topic",
            "blind_spot": "risk",
        }
        artifact_type = type_map.get(category, "topic")

        try:
            # Generate embedding
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)
            embedding = await asyncio.to_thread(
                lambda: client.embeddings.create(
                    model="text-embedding-3-small",
                    input=insight,
                ).data[0].embedding
            )

            # Save to Supabase
            async with httpx.AsyncClient(timeout=10.0) as http:
                response = await http.post(
                    f"{settings.supabase_url}/rest/v1/artifacts",
                    headers={
                        "apikey": settings.supabase_service_role_key,
                        "Authorization": f"Bearer {settings.supabase_service_role_key}",
                        "Content-Type": "application/json",
                        "Prefer": "return=representation",
                    },
                    json={
                        "artifact_type": artifact_type,
                        "content": insight,
                        "confidence": {"high": 0.9, "medium": 0.7, "low": 0.5}.get(confidence, 0.7),
                        "embedding": embedding,
                        "meeting_id": meeting_id or None,
                        "session_id": session_id or None,
                    },
                )
                response.raise_for_status()

            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                "insight_saved",
                category=category,
                artifact_type=artifact_type,
                org_id=org_id,
                session_id=session_id,
                duration_ms=duration_ms,
            )

            return ToolResult(
                name="save_insight",
                success=True,
                output={"status": "saved", "category": category},
                duration_ms=duration_ms,
            )

        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            logger.error("save_insight_failed", error=str(exc))
            return ToolResult(
                name="save_insight",
                success=False,
                output=None,
                error=str(exc),
                duration_ms=duration_ms,
            )


class FetchContextHandler(ToolHandler):
    """Retrieve relevant context from institutional memory."""

    async def execute(self, arguments: dict, context: dict) -> ToolResult:
        """Search institutional memory for relevant context."""
        from backend.config import get_settings
        import httpx
        from openai import OpenAI

        settings = get_settings()
        start = time.time()
        query = arguments.get("query", "")
        org_id = context.get("org_id", "")

        if not query:
            return ToolResult(
                name="fetch_context",
                success=True,
                output={"contexts": [], "count": 0},
                duration_ms=0,
            )

        try:
            # Generate embedding
            client = OpenAI(api_key=settings.openai_api_key)
            embedding = await asyncio.to_thread(
                lambda: client.embeddings.create(
                    model="text-embedding-3-small",
                    input=query,
                ).data[0].embedding
            )

            # Search
            async with httpx.AsyncClient(timeout=10.0) as http:
                response = await http.post(
                    f"{settings.supabase_url}/rest/v1/rpc/match_session_context",
                    headers={
                        "apikey": settings.supabase_key,
                        "Authorization": f"Bearer {settings.supabase_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query_embedding": embedding,
                        "match_threshold": 0.7,
                        "match_count": 5,
                        "p_org_id": org_id,
                    },
                )
                response.raise_for_status()
                results = response.json()

            contexts = [
                {"source": r.get("source_type"), "content": r.get("content")}
                for r in results
            ]

            duration_ms = int((time.time() - start) * 1000)
            return ToolResult(
                name="fetch_context",
                success=True,
                output={"contexts": contexts, "count": len(contexts)},
                duration_ms=duration_ms,
            )

        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            return ToolResult(
                name="fetch_context",
                success=False,
                output=None,
                error=str(exc),
                duration_ms=duration_ms,
            )


class AutopilotActionHandler(ToolHandler):
    """Trigger an action in the Autopilot external API."""

    async def execute(self, arguments: dict, context: dict) -> ToolResult:
        """Call the Autopilot API to perform an action."""
        from backend.config import get_settings
        import httpx

        settings = get_settings()
        start = time.time()

        action = arguments.get("action", "")
        params = arguments.get("parameters", {})

        if not settings.autopilot_api_base_url:
            return ToolResult(
                name="autopilot_action",
                success=False,
                output=None,
                error="Autopilot API not configured",
                duration_ms=0,
            )

        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                response = await http.post(
                    f"{settings.autopilot_api_base_url}/api/voice-tools/{action}",
                    headers={
                        "Authorization": f"Bearer {settings.autopilot_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "action": action,
                        "parameters": params,
                        "org_id": context.get("org_id"),
                        "session_id": context.get("session_id"),
                    },
                )
                response.raise_for_status()
                result = response.json()

            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                "autopilot_action_executed",
                action=action,
                session_id=context.get("session_id"),
                duration_ms=duration_ms,
            )

            return ToolResult(
                name="autopilot_action",
                success=True,
                output=result,
                duration_ms=duration_ms,
            )

        except httpx.HTTPStatusError as exc:
            duration_ms = int((time.time() - start) * 1000)
            return ToolResult(
                name="autopilot_action",
                success=False,
                output=None,
                error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            return ToolResult(
                name="autopilot_action",
                success=False,
                output=None,
                error=str(exc),
                duration_ms=duration_ms,
            )


class ToolRegistry:
    """
    Central registry of all tools available to Cassandra.

    Each tool is defined with an OpenAI function calling schema and a handler.
    """

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register all default tools."""

        # save_insight
        self._handlers["save_insight"] = SaveInsightHandler()
        self._tools["save_insight"] = ToolDefinition(
            name="save_insight",
            description="Save a detected insight from the conversation to institutional memory. Call proactively for decisions, action items, risks, contradictions, key facts, patterns, or blind spots.",
            parameters={
                "type": "object",
                "properties": {
                    "insight": {
                        "type": "string",
                        "description": "The insight text to save.",
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "decision", "action_item", "risk_flag",
                            "contradiction", "key_fact", "pattern", "blind_spot",
                        ],
                        "description": "The category of insight.",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": "Confidence level in the insight.",
                    },
                    "owner": {
                        "type": "string",
                        "description": "Person responsible (if applicable).",
                    },
                },
                "required": ["insight", "category", "confidence"],
            },
            handler=lambda args, ctx: self._handlers["save_insight"].execute(args, ctx),
        )

        # fetch_context
        self._handlers["fetch_context"] = FetchContextHandler()
        self._tools["fetch_context"] = ToolDefinition(
            name="fetch_context",
            description="Search the institutional memory (Supermemory) for relevant past decisions, discussions, and context. Use when the user asks about something that might have been discussed before.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to find relevant context.",
                    },
                },
                "required": ["query"],
            },
            handler=lambda args, ctx: self._handlers["fetch_context"].execute(args, ctx),
        )

        # autopilot_action
        self._handlers["autopilot_action"] = AutopilotActionHandler()
        self._tools["autopilot_action"] = ToolDefinition(
            name="autopilot_action",
            description="Trigger an action in the Autopilot system (e.g., create ticket, update CRM, send notification). Only call when explicitly requested by the user.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "The action name (e.g., 'create_ticket', 'send_email').",
                    },
                    "parameters": {
                        "type": "object",
                        "description": "Action-specific parameters.",
                    },
                },
                "required": ["action"],
            },
            handler=lambda args, ctx: self._handlers["autopilot_action"].execute(args, ctx),
        )

    def get_tools(self) -> list[dict]:
        """Return the list of tool definitions for OpenAI function calling."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        context: dict,
    ) -> ToolResult:
        """
        Execute a tool by name with the given arguments.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool arguments from LLM.
            context: Execution context (org_id, session_id, etc.).

        Returns:
            ToolResult with execution outcome.
        """
        if tool_name not in self._tools:
            return ToolResult(
                name=tool_name,
                success=False,
                output=None,
                error=f"Unknown tool: {tool_name}",
            )

        tool = self._tools[tool_name]
        return await tool.handler(arguments, context)

    def get_tool_names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())
