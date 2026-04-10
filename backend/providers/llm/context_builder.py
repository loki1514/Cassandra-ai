"""
backend/providers/llm/context_builder.py — Context injection for Cassandra.

This module implements Cassandra's Supermemory integration:
- READ: Before every LLM call, retrieve relevant context from institutional memory
- WRITE: After every session, store decisions/facts/action items to memory

Cassandra's role: Read from the past to inform the present. Every conversation
adds to the institutional memory that future conversations will draw from.
"""

import asyncio
from dataclasses import dataclass, field

from backend.utils.logging_config import get_logger

logger = get_logger("cassandra.llm.context_builder")


@dataclass
class ConversationTurn:
    """A single turn in the conversation history."""

    speaker: str          # "user" or "ai"
    text: str
    timestamp: str | None = None


@dataclass
class MemoryContext:
    """Context retrieved from institutional memory/supermemory."""

    conversation_history: list[ConversationTurn] = field(default_factory=list)
    retrieved_contexts: list[str] = field(default_factory=list)
    system_additions: list[str] = field(default_factory=list)
    total_context_tokens: int = 0


class ContextBuilder:
    """
    Builds context for LLM calls by retrieving relevant memories and
    conversation history.

    This is Cassandra's "Supermemory" — she reads from institutional
    memory before every LLM call to inform her responses.
    """

    def __init__(self, org_id: str, session_id: str | None = None):
        self._org_id = org_id
        self._session_id = session_id
        self._turn_count = 0

    def add_turn(self, speaker: str, text: str) -> None:
        """Record a conversation turn for context history."""
        self._turn_count += 1

    async def build(
        self,
        current_query: str,
        max_history_turns: int = 10,
        max_contexts: int = 5,
        similarity_threshold: float = 0.7,
    ) -> MemoryContext:
        """
        Build comprehensive context for an LLM call.

        Combines:
        1. Recent conversation history (last N turns)
        2. Relevant institutional memories (semantic search)
        3. System-level context additions

        Args:
            current_query: The current user transcript.
            max_history_turns: How many recent turns to include.
            max_contexts: Max memory contexts to retrieve.
            similarity_threshold: Minimum similarity score for retrieval.

        Returns:
            MemoryContext with all relevant context.
        """
        tasks = []

        # Fetch recent conversation history
        tasks.append(
            self._get_recent_history(max_history_turns)
        )

        # Fetch relevant memories from institutional memory
        if current_query:
            tasks.append(
                self._search_institutional_memory(
                    current_query,
                    max_contexts,
                    similarity_threshold,
                )
            )

        # Run both in parallel
        history_results, memory_results = await asyncio.gather(*tasks)

        # Build system additions based on available context
        system_additions = self._build_system_additions(
            history_results,
            memory_results,
        )

        return MemoryContext(
            conversation_history=history_results,
            retrieved_contexts=memory_results,
            system_additions=system_additions,
            total_context_tokens=self._estimate_tokens(
                history_results, memory_results, system_additions
            ),
        )

    async def _get_recent_history(
        self,
        max_turns: int,
    ) -> list[ConversationTurn]:
        """Fetch recent conversation turns from the database."""
        try:
            # Import here to avoid circular dependencies
            import httpx
            from backend.config import get_settings

            settings = get_settings()
            if not settings.supabase_url:
                return []

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.supabase_url}/rest/v1/session_transcripts",
                    params={
                        "session_id": f"eq.{self._session_id}" if self._session_id else "is.null",
                        "org_id": f"eq.{self._org_id}",
                        "order": "turn_index.desc",
                        "limit": max_turns * 2,  # user + ai per turn
                        "select": "speaker,content,turn_index",
                    },
                    headers={
                        "apikey": settings.supabase_key,
                        "Authorization": f"Bearer {settings.supabase_key}",
                    },
                )
                response.raise_for_status()
                result = response.json()

            turns = [
                ConversationTurn(
                    speaker=r["speaker"],
                    text=r["content"],
                )
                for r in reversed(result)
            ]
            logger.debug(
                "context_history_fetched",
                turns=len(turns),
                session_id=self._session_id,
            )
            return turns

        except Exception as exc:
            logger.warning(
                "context_history_fetch_failed",
                error=str(exc),
                session_id=self._session_id,
            )
            return []

    async def _search_institutional_memory(
        self,
        query: str,
        max_contexts: int,
        threshold: float,
    ) -> list[str]:
        """
        Search institutional memory (Supermemory) for relevant context.

        Uses the match_session_context RPC function for vector similarity search
        over past transcripts and artifacts.
        """
        try:
            import httpx
            from backend.config import get_settings
            from openai import OpenAI

            settings = get_settings()
            if not settings.supabase_url or not settings.supabase_key:
                return []

            # Generate embedding for the query
            openai_client = OpenAI(api_key=settings.openai_api_key)
            embedding_response = await asyncio.to_thread(
                lambda: openai_client.embeddings.create(
                    model="text-embedding-3-small",
                    input=query,
                )
            )
            query_embedding = embedding_response.data[0].embedding

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{settings.supabase_url}/rest/v1/rpc/match_session_context",
                    headers={
                        "apikey": settings.supabase_key,
                        "Authorization": f"Bearer {settings.supabase_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query_embedding": query_embedding,
                        "match_threshold": threshold,
                        "match_count": max_contexts,
                        "p_org_id": self._org_id,
                    },
                )
                response.raise_for_status()
                results = response.json()

            contexts = []
            for r in results:
                source = r.get("source_type", "context")
                content = r.get("content", "")
                similarity = 1.0 - float(r.get("similarity", 0.5))
                contexts.append(
                    f"[{source} (relevance: {similarity:.2f})] {content}"
                )

            logger.debug(
                "institutional_memory_searched",
                query_preview=query[:50],
                results=len(contexts),
            )
            return contexts

        except Exception as exc:
            logger.warning(
                "institutional_memory_search_failed",
                error=str(exc),
                query_preview=query[:50],
            )
            return []

    def _build_system_additions(
        self,
        history: list[ConversationTurn],
        memories: list[str],
    ) -> list[str]:
        """Build system-level context additions."""
        additions = []

        if history:
            turn_count = len(history)
            additions.append(
                f"[Previous conversation ({turn_count} turns in this session)]"
            )
            for turn in history[-5:]:  # Last 5 turns
                additions.append(f"{turn.speaker.upper()}: {turn.text[:200]}")

        if memories:
            additions.append("[Relevant institutional memories]")
            for mem in memories[:3]:
                additions.append(mem[:300])

        return additions

    def format_for_llm(self, context: MemoryContext) -> str:
        """
        Format MemoryContext as a string for LLM system prompt injection.

        Returns a formatted string that can be appended to the system prompt.
        """
        parts = []

        if context.system_additions:
            parts.append("\n".join(context.system_additions))

        return "\n\n".join(parts) if parts else ""

    def _estimate_tokens(
        self,
        history: list[ConversationTurn],
        memories: list[str],
        additions: list[str],
    ) -> int:
        """Rough token estimation (~4 chars per token)."""
        text = (
            " ".join(t.text for t in history)
            + " ".join(mem for mem in memories)
            + " ".join(additions)
        )
        return len(text) // 4
