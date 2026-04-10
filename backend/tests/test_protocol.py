"""
backend/tests/test_protocol.py — Tests for protocol detection
"""

import pytest
from backend.core.protocol import detect_protocol, ProtocolVersion


class TestProtocolDetection:
    """Tests for protocol version detection."""

    def test_v2_session_start(self):
        msg = {"type": "session_start", "api_key": "sk_cassandra_test"}
        assert detect_protocol(msg) == ProtocolVersion.V2

    def test_v2_session_start_jwt(self):
        msg = {"type": "session_start", "token": "jwt_token"}
        assert detect_protocol(msg) == ProtocolVersion.V2

    def test_v1_input_audio(self):
        msg = {"type": "input_audio", "audio": "base64data"}
        assert detect_protocol(msg) == ProtocolVersion.LEGACY

    def test_v1_ping(self):
        msg = {"type": "ping"}
        assert detect_protocol(msg) == ProtocolVersion.LEGACY

    def test_v1_switch_role(self):
        msg = {"type": "switch_role", "role": "MARKETING"}
        assert detect_protocol(msg) == ProtocolVersion.LEGACY

    def test_unknown_defaults_to_legacy(self):
        msg = {"type": "unknown_message"}
        assert detect_protocol(msg) == ProtocolVersion.LEGACY

    def test_empty_message_defaults_to_legacy(self):
        msg = {}
        assert detect_protocol(msg) == ProtocolVersion.LEGACY
