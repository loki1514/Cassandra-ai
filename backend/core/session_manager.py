"""
backend/core/session_manager.py — Session lifecycle management and state machine.

The SessionManager is the central coordinator for all active Cassandra sessions:
- Creates and tracks session state (idle, listening, processing, speaking)
- Manages multi-client connections (web + mobile simultaneously)
- Handles interrupt/barge-in across all clients
- Provides session context for V1 relay and V2 Realtime orchestrator

State Machine:
    idle → listening → processing → speaking → listening
                         ↑
                         └───── interrupt ───┘ (barge-in)
"""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import AsyncIterator, Callable, Awaitable

from fastapi import WebSocket

from backend.auth.middleware import AuthContext
from backend.config import get_settings
from backend.core.audio_buffer import RollingAudioBuffer
from backend.core.exceptions import (
    InvalidSessionStateError,
    SessionAlreadyExistsError,
    SessionNotFoundError,
)
from backend.utils.logging_config import SessionLogger

logger = SessionLogger(logger_name="cassandra.session_manager")


class SessionState(str, Enum):
    """Session state machine states."""

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    DISCONNECTED = "disconnected"


class SessionEvent(str, Enum):
    """Events that drive state transitions."""

    SESSION_START = "session_start"
    AUDIO_RECEIVED = "audio_received"
    SPEECH_DETECTED = "speech_detected"
    SPEECH_ENDED = "speech_ended"
    PROCESSING_START = "processing_start"
    PROCESSING_DONE = "processing_done"
    SPEAKING_START = "speaking_start"
    SPEAKING_DONE = "speaking_done"
    INTERRUPT = "interrupt"
    SESSION_END = "session_end"


# State transition map
VALID_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.IDLE: {SessionState.LISTENING, SessionState.DISCONNECTED},
    SessionState.LISTENING: {
        SessionState.PROCESSING,
        SessionState.SPEAKING,
        SessionState.IDLE,
        SessionState.DISCONNECTED,
    },
    SessionState.PROCESSING: {
        SessionState.SPEAKING,
        SessionState.LISTENING,
        SessionState.DISCONNECTED,
    },
    SessionState.SPEAKING: {
        SessionState.LISTENING,
        SessionState.SPEAKING,
        SessionState.PROCESSING,
        SessionState.DISCONNECTED,
    },
    SessionState.DISCONNECTED: set(),
}


@dataclass
class SessionContext:
    """
    All state for a single Cassandra session.

    Created per WebSocket connection. One session can have multiple
    client connections (web + mobile).
    """

    # Identity
    session_id: str
    meeting_id: str | None
    org_id: str
    user_id: str | None
    protocol_version: str = "v2"

    # Auth
    auth: AuthContext | None = None

    # State machine
    state: SessionState = SessionState.IDLE
    previous_state: SessionState | None = None

    # Timing
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
    audio_start_time: float = 0.0

    # Audio
    audio_buffer: RollingAudioBuffer = field(default_factory=RollingAudioBuffer)

    # Clients (multi-client support)
    clients: dict[int, WebSocket] = field(default_factory=dict)
    # client_id -> WebSocket

    # Cancellation
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    _tts_task: asyncio.Task | None = None

    # Stats
    audio_chunks_received: int = 0
    transcript_turns: int = 0
    interrupts_count: int = 0
    tool_calls: int = 0

    # Logger (initialized lazily)
    _session_logger: SessionLogger | None = None

    # End-of-session callbacks (fires during end_session cleanup)
    # Each callback receives (session_id, org_id, user_id, session_stats)
    _end_callbacks: list[Callable[..., Awaitable[None]]] = field(default_factory=list)

    @property
    def sl(self) -> SessionLogger:
        if self._session_logger is None:
            self._session_logger = SessionLogger(
                session_id=self.session_id,
                org_id=self.org_id,
                user_id=self.user_id,
            )
        return self._session_logger

    def add_end_callback(self, callback: Callable[..., Awaitable[None]]) -> None:
        """
        Register a callback to be invoked when the session ends.

        Callbacks receive (session_id, org_id, user_id, session_stats_dict)
        and are invoked during end_session() cleanup — after audio buffer
        is finalized but before the session is removed from the registry.
        """
        self._end_callbacks.append(callback)

    def transition_to(self, new_state: SessionState) -> None:
        """Transition to a new state, validating the transition is allowed."""
        if new_state == self.state:
            return

        if new_state not in VALID_TRANSITIONS.get(self.state, set()):
            # Allow interrupt from any active state
            if new_state == SessionState.LISTENING:
                pass  # Interrupt is always allowed
            else:
                self.sl.warning(
                    "invalid_state_transition",
                    from_state=self.state.value,
                    to_state=new_state.value,
                )
                raise InvalidSessionStateError(
                    f"Invalid state transition: {self.state.value} → {new_state.value}"
                )

        self.previous_state = self.state
        self.state = new_state
        self.last_activity = datetime.utcnow()
        self.sl.info(
            "session_state_change",
            new_state=new_state.value,
            previous_state=self.previous_state.value,
        )


class SessionManager:
    """
    Global session manager coordinating all active Cassandra sessions.

    Singleton pattern — one instance manages all concurrent sessions.
    """

    def __init__(self):
        self._sessions: dict[str, SessionContext] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def create_session(
        self,
        session_id: str | None = None,
        meeting_id: str | None = None,
        auth: AuthContext | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
        protocol_version: str = "v2",
    ) -> AsyncIterator[SessionContext]:
        """
        Create a new session or return existing one.

        Args:
            session_id: Optional session ID (generated if not provided).
            meeting_id: Associated meeting ID.
            auth: Authentication context (provides org_id/user_id if set).
            org_id: Organization ID (used if auth not provided).
            user_id: User ID (used if auth not provided).
            protocol_version: "v1" (legacy) or "v2" (smart).

        Yields:
            SessionContext for the session.
        """
        if session_id is None:
            session_id = f"mtg-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

        # Resolve org_id/user_id: prefer auth context, fall back to direct params
        resolved_org_id = auth.org_id if auth else (org_id or "legacy")
        resolved_user_id = auth.user_id if auth else user_id

        async with self._lock:
            if session_id in self._sessions:
                raise SessionAlreadyExistsError(
                    f"Session {session_id} already exists"
                )

            ctx = SessionContext(
                session_id=session_id,
                meeting_id=meeting_id,
                org_id=resolved_org_id,
                user_id=resolved_user_id,
                protocol_version=protocol_version,
                auth=auth,
            )
            self._sessions[session_id] = ctx

        logger.info(
            "session_created",
            session_id=session_id,
            meeting_id=meeting_id,
            org_id=ctx.org_id,
            protocol_version=protocol_version,
        )

        try:
            yield ctx
        finally:
            await self.end_session(session_id)

    async def get_session(self, session_id: str) -> SessionContext:
        """Get a session by ID."""
        async with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(f"Session {session_id} not found")
            return self._sessions[session_id]

    async def has_session(self, session_id: str) -> bool:
        """Check if a session exists."""
        return session_id in self._sessions

    async def add_client(
        self,
        session_id: str,
        websocket: WebSocket,
    ) -> SessionContext:
        """
        Add a WebSocket client to a session (multi-client support).

        Returns the session context.
        """
        ctx = await self.get_session(session_id)
        client_id = id(websocket)
        ctx.clients[client_id] = websocket
        ctx.sl.info(
            "client_connected",
            client_id=client_id,
            total_clients=len(ctx.clients),
        )
        return ctx

    async def remove_client(
        self,
        session_id: str,
        websocket: WebSocket,
    ) -> None:
        """Remove a WebSocket client from a session."""
        client_id = id(websocket)
        ctx = await self.get_session(session_id)
        ctx.clients.pop(client_id, None)
        ctx.sl.info(
            "client_disconnected",
            client_id=client_id,
            remaining_clients=len(ctx.clients),
        )

        # If no clients remain, end the session
        if not ctx.clients:
            await self.end_session(session_id)

    async def end_session(self, session_id: str) -> None:
        """End a session and clean up resources."""
        async with self._lock:
            if session_id not in self._sessions:
                return

            ctx = self._sessions.pop(session_id)

        # Cancel any pending tasks
        if ctx._tts_task and not ctx._tts_task.done():
            ctx._cancel_event.set()
            ctx._tts_task.cancel()
            try:
                await ctx._tts_task
            except asyncio.CancelledError:
                pass

        elapsed = (datetime.utcnow() - ctx.created_at).total_seconds()
        stats = {
            "duration_seconds": round(elapsed, 1),
            "transcript_turns": ctx.transcript_turns,
            "interrupts_count": ctx.interrupts_count,
            "tool_calls": ctx.tool_calls,
            "audio_chunks_received": ctx.audio_chunks_received,
            "state": ctx.state.value,
        }

        # Fire end-of-session callbacks (e.g., write memory to Supermemory)
        for callback in ctx._end_callbacks:
            try:
                await callback(session_id, ctx.org_id, ctx.user_id, stats)
            except Exception as e:
                logger.error(
                    "session_end_callback_failed",
                    session_id=session_id,
                    callback=repr(callback),
                    error=str(e),
                )

        ctx.sl.info(
            "session_ended",
            session_id=session_id,
            **stats,
        )

    async def broadcast(
        self,
        session_id: str,
        message: dict,
        exclude_client_id: int | None = None,
    ) -> None:
        """
        Broadcast a message to all clients in a session.

        Args:
            session_id: Session to broadcast to.
            message: Message dict to send.
            exclude_client_id: Optional client ID to exclude (e.g., the sender).
        """
        try:
            ctx = await self.get_session(session_id)
        except SessionNotFoundError:
            return

        disconnected = []

        for client_id, ws in ctx.clients.items():
            if client_id == exclude_client_id:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(client_id)

        # Clean up disconnected clients
        for client_id in disconnected:
            ctx.clients.pop(client_id, None)
            ctx.sl.info("broadcast_cleanup", removed_client_id=client_id)

    async def interrupt(self, session_id: str) -> None:
        """
        Handle an interrupt (barge-in).

        Cancels any running TTS stream, resets speaking state,
        and returns to listening.
        """
        try:
            ctx = await self.get_session(session_id)
        except SessionNotFoundError:
            return

        ctx.interrupts_count += 1

        # Cancel TTS task
        ctx._cancel_event.set()
        if ctx._tts_task and not ctx._tts_task.done():
            ctx._tts_task.cancel()
            try:
                await ctx._tts_task
            except asyncio.CancelledError:
                pass

        # Reset state
        ctx._cancel_event.clear()
        ctx.audio_buffer.clear_speech_markers()

        # Transition to listening
        ctx.transition_to(SessionState.LISTENING)

        # Notify all clients
        await self.broadcast(session_id, {
            "type": "interrupt",
        })

        ctx.sl.info("interrupt_handled", session_id=session_id)

    async def get_active_session_count(self) -> int:
        """Return the number of active sessions."""
        return len(self._sessions)

    async def get_session_stats(self, session_id: str) -> dict | None:
        """Return statistics for a session."""
        try:
            ctx = await self.get_session(session_id)
            return {
                "session_id": ctx.session_id,
                "state": ctx.state.value,
                "audio_chunks_received": ctx.audio_chunks_received,
                "transcript_turns": ctx.transcript_turns,
                "interrupts_count": ctx.interrupts_count,
                "tool_calls": ctx.tool_calls,
                "connected_clients": len(ctx.clients),
                "uptime_seconds": (datetime.utcnow() - ctx.created_at).total_seconds(),
            }
        except SessionNotFoundError:
            return None


# Global session manager instance
_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Get the global session manager instance."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
