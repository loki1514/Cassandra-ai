"""
backend/providers/llm/orchestrator.py — LLM Orchestrator for Cassandra Voice Server.

This is Cassandra's "brain" — it orchestrates the full LLM interaction:
1. Reads context from Supermemory (via ContextBuilder)
2. Sends the transcript + context to GPT-4o
3. Executes tool calls triggered by the LLM
4. Returns the response

Cassandra's design principle: She's a memory-producing voice AI.
Every conversation should add to the institutional knowledge.
Every response should be informed by it.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import openai

from backend.auth.middleware import AuthContext
from backend.config import get_settings
from backend.core.exceptions import LLMError, LLMResponseError
from backend.providers.llm.context_builder import ContextBuilder
from backend.providers.llm.tool_registry import ToolRegistry, ToolResult
from backend.utils.circuit_breaker import get_breaker_registry
from backend.utils.logging_config import get_logger

logger = get_logger("cassandra.llm.orchestrator")


@dataclass
class LLMResponse:
    """Response from the LLM orchestrator."""

    text: str
    tool_calls: list[dict] = field(default_factory=list)
    context_used_tokens: int = 0
    response_tokens: int = 0
    total_latency_ms: int = 0


@dataclass
class OrchestratorConfig:
    """Configuration for the LLM orchestrator."""

    model: str = "gpt-4o-2024-08-06"
    system_prompt: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7


class LLMOrchestrator:
    """
    Orchestrates LLM calls for Cassandra with tool execution and context injection.

    Flow for each user transcript:
    1. Retrieve context from Supermemory (past conversations + artifacts)
    2. Build messages with system prompt + context + conversation history
    3. Call GPT-4o with tool definitions
    4. Execute any tool calls the LLM requests
    5. Return the final response
    """

    def __init__(
        self,
        auth_context: AuthContext,
        config: OrchestratorConfig | None = None,
    ):
        self._auth = auth_context
        self._config = config or OrchestratorConfig()
        self._client: openai.AsyncOpenAI | None = None
        self._tool_registry = ToolRegistry()
        self._context_builder = ContextBuilder(
            org_id=auth_context.org_id,
        )
        self._conversation_history: list[dict] = []
        self._circuit_breaker = get_breaker_registry().get(
            "llm_orchestrator",
            failure_threshold=5,
            recovery_timeout=30.0,
        )

        # Default system prompt
        if not self._config.system_prompt:
            self._config.system_prompt = self._default_system_prompt()

    async def warmup(self) -> None:
        """Initialize the OpenAI client."""
        settings = get_settings()
        self._client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        logger.info(
            "llm_orchestrator_warmup",
            org_id=self._auth.org_id,
            role=self._auth.role,
            model=self._config.model,
        )

    async def process(
        self,
        user_transcript: str,
        meeting_id: str | None = None,
        session_id: str | None = None,
    ) -> LLMResponse:
        """
        Process a user transcript through the full LLM pipeline.

        Args:
            user_transcript: The user's spoken text.
            meeting_id: Associated meeting ID.
            session_id: Session ID for context retrieval.

        Returns:
            LLMResponse with text and tool call results.
        """
        start_time = time.time()
        self._context_builder._session_id = session_id

        try:
            # Step 1: Add user turn to history
            self._conversation_history.append({
                "role": "user",
                "content": user_transcript,
            })
            self._context_builder.add_turn("user", user_transcript)

            # Step 2: Retrieve context from Supermemory
            context = await self._context_builder.build(
                current_query=user_transcript,
                max_history_turns=10,
            )

            # Step 3: Build messages with context
            messages = self._build_messages(user_transcript, context)

            # Step 4: Call GPT-4o with tools
            response = await self._call_llm(messages, context)

            # Step 5: Execute tool calls
            tool_results = []
            context_dict = {
                "org_id": self._auth.org_id,
                "user_id": self._auth.user_id,
                "session_id": session_id,
                "meeting_id": meeting_id,
            }

            for tool_call in response.tool_calls or []:
                result = await self._execute_tool_call(tool_call, context_dict)
                tool_results.append(result)

                # Add tool result to conversation
                messages.append({
                    "role": "system",
                    "content": f"Tool result: {json.dumps(result.output) if result.success else result.error}",
                })

            # Step 6: If tools were executed, get follow-up response
            if tool_results and any(r.success for r in tool_results):
                follow_up = await self._call_llm(messages, context)
                response.text = follow_up.text
                response.tool_calls = [
                    {
                        "name": tr.name,
                        "success": tr.success,
                        "output": tr.output,
                    }
                    for tr in tool_results
                ]

            total_ms = int((time.time() - start_time) * 1000)
            response.total_latency_ms = total_ms
            response.context_used_tokens = context.total_context_tokens

            # Add AI response to history
            self._conversation_history.append({
                "role": "assistant",
                "content": response.text,
            })

            logger.info(
                "llm_response_complete",
                org_id=self._auth.org_id,
                session_id=session_id,
                transcript_preview=user_transcript[:50],
                response_preview=response.text[:50],
                latency_ms=total_ms,
                tools_executed=len(tool_results),
            )

            return response

        except Exception as exc:
            total_ms = int((time.time() - start_time) * 1000)
            logger.error(
                "llm_processing_failed",
                error=str(exc),
                transcript_preview=user_transcript[:50],
                latency_ms=total_ms,
            )
            raise LLMError(f"LLM processing failed: {exc}") from exc

    async def process_stream(
        self,
        user_transcript: str,
        meeting_id: str | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[str]:
        """
        Stream LLM response tokens as they arrive.

        Yields text tokens for real-time display to the user.
        Note: Tool calls are not supported in streaming mode.
        """
        start_time = time.time()
        context = await self._context_builder.build(user_transcript)
        messages = self._build_messages(user_transcript, context)

        try:
            stream = await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
                stream=True,
            )

            full_response = ""
            async for chunk in stream:
                token = chunk.choices[0].delta.content or ""
                if token:
                    full_response += token
                    yield token

            total_ms = int((time.time() - start_time) * 1000)
            logger.info(
                "llm_stream_complete",
                transcript_preview=user_transcript[:50],
                latency_ms=total_ms,
            )

        except Exception as exc:
            logger.error("llm_stream_failed", error=str(exc))
            raise LLMError(f"LLM streaming failed: {exc}") from exc

    def _build_messages(
        self,
        user_transcript: str,
        context,
    ) -> list[dict]:
        """Build the message list with system prompt and context."""
        messages = []

        # System prompt with context
        system_with_context = self._config.system_prompt
        context_str = self._context_builder.format_for_llm(context)
        if context_str:
            system_with_context += (
                f"\n\n[INSTITUTIONAL MEMORY - Read carefully before responding]\n"
                f"{context_str}"
            )

        messages.append({
            "role": "system",
            "content": system_with_context,
        })

        # Recent conversation history (last 10 turns)
        for turn in self._conversation_history[-10:]:
            messages.append(turn)

        # Current user message
        messages.append({
            "role": "user",
            "content": user_transcript,
        })

        return messages

    async def _call_llm(self, messages: list[dict], context) -> Any:
        """Make an LLM call with tool support."""

        async def _do_call():
            return await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                tools=self._tool_registry.get_tools(),
                tool_choice="auto",
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            )

        response = await self._circuit_breaker.call(_do_call)

        message = response.choices[0].message
        return message

    async def _execute_tool_call(
        self,
        tool_call,
        context: dict,
    ) -> ToolResult:
        """Execute a single tool call from the LLM."""
        name = tool_call.function.name
        args = json.loads(tool_call.function.arguments or "{}")

        logger.info(
            "tool_call_triggered",
            tool_name=name,
            org_id=context.get("org_id"),
            session_id=context.get("session_id"),
        )

        return await self._tool_registry.execute(name, args, context)

    async def build_session_config(self, session_id: str | None = None) -> dict:
        """
        Build the OpenAI Realtime session.update configuration with
        Supermemory context injection and tool schemas.

        This is used by the V2 handler to inject context into the
        OpenAI Realtime session before audio processing begins.
        """
        # Retrieve context from Supermemory
        context = await self._context_builder.build(
            current_query="",  # No specific query on session start
            max_history_turns=5,
        )
        context_str = self._context_builder.format_for_llm(context)

        # Build instructions with context
        instructions = self._config.system_prompt
        if context_str:
            instructions += f"\n\n[INSTITUTIONAL MEMORY]\n{context_str}"

        return {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": instructions,
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.62,
                    "prefix_padding_ms": 450,
                    "silence_duration_ms": 750,
                },
                "tools": self._tool_registry.get_tools(),
            },
        }

    def _default_system_prompt(self) -> str:
        """Default system prompt for Cassandra."""
        return """You are Cassandra, an intelligent voice assistant for boardroom meetings and team collaboration.

Your core responsibilities:
1. LISTEN actively to meeting discussions
2. REASON about what's being said in context of past decisions and conversations
3. REMEMBER by saving important insights (decisions, action items, risks, key facts) to institutional memory
4. RESPOND thoughtfully when spoken to

Guidelines:
- Be concise and direct in your responses
- Reference past context when relevant
- Proactively save important decisions and action items using the save_insight tool
- Search institutional memory when the user asks about past discussions
- Use the autopilot_action tool to trigger external actions when requested
- Never make up information — use fetch_context to verify facts from past conversations
- Prioritize clarity and actionability over verbosity

You are part of the organization's collective intelligence. Every insight you capture helps future conversations."""
