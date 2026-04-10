"""
backend/core/protocol.py — Protocol detection for V1 legacy vs V2 smart backend.

The frontend can connect in two modes:
- V1 (Legacy): sends input_audio immediately, backend acts as OpenAI Realtime relay
- V2 (Smart): sends session_start first, backend owns the full voice pipeline

Detection is based on the FIRST message received from the client.
"""

from enum import Enum
from typing import Literal


class ProtocolVersion(Enum):
    """Protocol version negotiated at WebSocket connect time."""

    LEGACY = "v1"   # OpenAI Realtime API relay — existing behavior
    V2 = "v2"      # Smart backend owning full pipeline


# Message types recognized by each protocol version
LEGACY_MESSAGE_TYPES = frozenset({
    "input_audio",
    "ping",
    "switch_role",
    "meeting_ended",
})

V2_MESSAGE_TYPES = frozenset({
    "session_start",
    "session_end",
    "input_audio",
    "interrupt",
    "ping",
    "role_update",
    "context_inject",
})


def detect_protocol(first_message: dict) -> ProtocolVersion:
    """
    Detect the protocol version based on the first WebSocket message.

    Args:
        first_message: The first JSON message from the client.

    Returns:
        ProtocolVersion.LEGACY if the client sends input_audio/ping immediately.
        ProtocolVersion.V2 if the client sends session_start first.

    Rules:
    - session_start → V2 (new smart clients)
    - input_audio / ping / switch_role → V1 (existing relay clients)
    - Any other message with type field → infer from type
    - No type field → default to V1 for backwards compat
    """
    msg_type = first_message.get("type", "")

    if msg_type == "session_start":
        return ProtocolVersion.V2

    if msg_type in LEGACY_MESSAGE_TYPES:
        return ProtocolVersion.LEGACY

    # Unknown type — default to legacy for backwards compat
    # but log for debugging
    return ProtocolVersion.LEGACY


def is_valid_v1_message(msg: dict) -> bool:
    """Check if a message is a valid V1 (legacy) protocol message."""
    if not isinstance(msg, dict):
        return False
    msg_type = msg.get("type", "")
    return msg_type in LEGACY_MESSAGE_TYPES


def is_valid_v2_message(msg: dict) -> bool:
    """Check if a message is a valid V2 protocol message."""
    if not isinstance(msg, dict):
        return False
    msg_type = msg.get("type", "")
    return msg_type in V2_MESSAGE_TYPES


def describe_protocol(version: ProtocolVersion) -> str:
    """Human-readable description of a protocol version."""
    descriptions = {
        ProtocolVersion.LEGACY: (
            "V1 Legacy (OpenAI Realtime Relay) — "
            "backend forwards audio to OpenAI Realtime API"
        ),
        ProtocolVersion.V2: (
            "V2 Smart Backend — "
            "backend owns VAD, STT, LLM, TTS pipeline"
        ),
    }
    return descriptions.get(version, "Unknown")
