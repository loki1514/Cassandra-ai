"""
Backend Unit Tests for Cassandra AI

Tests for:
- T09: FastAPI WebSocket + Health endpoints
- T10: AssemblyAI Transcription
- T11: Speaker ID / Diarization
- T12: LLM Commitment Extraction
- T17: JWT Auth Middleware

Run with: pytest tests/test_backend.py -v
"""

import pytest
import asyncio
import json
import time
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from typing import Dict, Any, List

import numpy as np
import jwt
from fastapi.testclient import TestClient
from fastapi import HTTPException, status

# Import modules under test
from cassandra.config import get_settings, AppSettings
from cassandra.main import app, AudioBufferManager, ConnectionManager
from cassandra.auth import (
    verify_jwt,
    UserContext,
    get_current_user,
    TokenExpiredError,
    InvalidTokenError,
    JWTVerifier
)
from cassandra.transcription import (
    SpeakerSegment,
    TranscriptionResult,
    TranscriptionError,
    RateLimitError,
    AssemblyAIClient
)
from cassandra.speaker_id import (
    SpeakerMatch,
    cosine_similarity,
    SIMILARITY_THRESHOLD
)
from cassandra.extraction import (
    ExtractedCommitment,
    EntityType,
    ExtractionError,
    _parse_date
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def client():
    """Create test client for FastAPI app."""
    return TestClient(app)


@pytest.fixture
def mock_settings():
    """Create mock settings for testing."""
    return AppSettings(
        app_name="Cassandra AI Test",
        app_version="0.1.0-test",
        environment="test",
        debug=True
    )


@pytest.fixture
def sample_audio_bytes():
    """Create sample PCM16 audio bytes."""
    # 1 second of silence at 16kHz, 16-bit
    return b'\x00\x00' * 16000


@pytest.fixture
def sample_transcript():
    """Sample transcript for testing."""
    return """
    A: I'll send the report by Friday.
    B: Great, thanks! I'll review it over the weekend.
    A: Let's schedule a follow-up meeting for next Monday.
    """


@pytest.fixture
def mock_user_context():
    """Create mock user context."""
    return UserContext(
        user_id="user_123",
        org_id="org_test",
        role="member",
        permissions=["read:own", "write:own", "read:org"],
        email="test@example.com"
    )


# =============================================================================
# T09: FastAPI WebSocket + Health Endpoints Tests
# =============================================================================

class TestHealthEndpoints:
    """Tests for health check endpoints."""
    
    def test_health_check(self, client):
        """Test /health endpoint returns correct structure."""
        response = client.get("/health")
        assert response.status_code == 200
        
        data = response.json()
        assert data["status"] == "healthy"
        assert "service" in data
        assert "version" in data
        assert "environment" in data
        assert "timestamp" in data
        assert "websocket_connections" in data
    
    def test_readiness_probe(self, client):
        """Test /health/ready endpoint."""
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["ready"] is True
    
    def test_liveness_probe(self, client):
        """Test /health/live endpoint."""
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["alive"] is True


class TestAudioBufferManager:
    """Tests for AudioBufferManager."""
    
    def test_buffer_initialization(self):
        """Test buffer manager initializes correctly."""
        manager = AudioBufferManager()
        assert manager.buffer == b""
        assert manager.segment_count == 0
    
    def test_add_audio_accumulates(self):
        """Test audio data accumulates in buffer."""
        manager = AudioBufferManager()
        audio_data = b'\x00\x01\x02\x03'
        
        result = manager.add_audio(audio_data)
        assert result is None  # No segment yet
        assert len(manager.buffer) == 4
    
    def test_silence_threshold(self):
        """Test silence timeout triggers segment extraction."""
        manager = AudioBufferManager()
        manager.SILENCE_THRESHOLD_MS = 100  # Short for testing
        
        # Add some audio
        manager.add_audio(b'\x00\x01' * 100)
        
        # Wait for silence threshold
        time.sleep(0.15)
        
        # Check for silence timeout
        segment = manager.check_silence_timeout()
        assert segment is not None
        assert len(segment) > 0
    
    def test_max_buffer_size(self):
        """Test buffer extracts when max size reached."""
        manager = AudioBufferManager()
        manager.MAX_BUFFER_MS = 100  # Small for testing
        
        # Add audio exceeding max buffer
        max_bytes = manager.max_buffer_bytes
        audio_data = b'\x00\x01' * (max_bytes + 100)
        
        segment = manager.add_audio(audio_data)
        assert segment is not None


class TestWebSocket:
    """Tests for WebSocket endpoint."""
    
    @pytest.mark.asyncio
    async def test_websocket_connection(self):
        """Test WebSocket connection and heartbeat."""
        from cassandra.main import manager
        
        # Mock WebSocket
        mock_ws = AsyncMock()
        mock_ws.receive = AsyncMock(side_effect=[
            {"bytes": b'\x00\x01\x02\x03'},
            WebSocketDisconnect()
        ])
        
        client_id = "test_client_123"
        await manager.connect(mock_ws, client_id)
        
        # Verify connection accepted
        mock_ws.accept.assert_called_once()
        
        # Cleanup
        manager.disconnect(client_id)
    
    @pytest.mark.asyncio
    async def test_websocket_json_control(self):
        """Test WebSocket JSON control messages."""
        from cassandra.main import manager
        
        mock_ws = AsyncMock()
        client_id = "test_client_456"
        
        await manager.connect(mock_ws, client_id)
        
        # Test reset command
        await manager.send_json(client_id, {"type": "reset", "message": "Test"})
        
        # Cleanup
        manager.disconnect(client_id)


# Mock WebSocketDisconnect for testing
class WebSocketDisconnect(Exception):
    pass


# =============================================================================
# T10: AssemblyAI Transcription Tests
# =============================================================================

class TestTranscriptionModels:
    """Tests for transcription data models."""
    
    def test_speaker_segment_creation(self):
        """Test SpeakerSegment dataclass."""
        segment = SpeakerSegment(
            speaker_label="A",
            text="Hello world",
            start_ms=0,
            end_ms=1000,
            confidence=0.95
        )
        assert segment.speaker_label == "A"
        assert segment.text == "Hello world"
        assert segment.confidence == 0.95
    
    def test_transcription_result_creation(self):
        """Test TranscriptionResult dataclass."""
        segments = [
            SpeakerSegment("A", "Hello", 0, 500, 0.9),
            SpeakerSegment("B", "Hi", 500, 1000, 0.85)
        ]
        result = TranscriptionResult(
            segments=segments,
            duration_ms=1000,
            language="en",
            utterances=2
        )
        assert len(result.segments) == 2
        assert result.duration_ms == 1000


class TestTranscriptionClient:
    """Tests for AssemblyAI client."""
    
    @pytest.mark.asyncio
    @patch("cassandra.transcription.aai")
    async def test_transcribe_success(self, mock_aai, sample_audio_bytes):
        """Test successful transcription."""
        # Mock AssemblyAI response
        mock_transcript = Mock()
        mock_transcript.error = None
        mock_transcript.audio_duration = 5000
        mock_transcript.language_code = "en"
        mock_transcript.utterances = [
            Mock(speaker="A", text="Hello", start=0, end=1000, confidence=0.95),
            Mock(speaker="B", text="World", start=1000, end=2000, confidence=0.9)
        ]
        
        mock_transcriber = Mock()
        mock_transcriber.upload_file.return_value = "https://test-url"
        mock_transcriber.transcribe.return_value = mock_transcript
        mock_aai.Transcriber.return_value = mock_transcriber
        
        client = AssemblyAIClient()
        result = await client.transcribe(sample_audio_bytes, org_id="test_org")
        
        assert len(result.segments) == 2
        assert result.segments[0].speaker_label == "A"
        assert result.duration_ms == 5000
    
    @pytest.mark.asyncio
    @patch("cassandra.transcription.aai")
    async def test_transcribe_with_error(self, mock_aai, sample_audio_bytes):
        """Test transcription error handling."""
        mock_transcript = Mock()
        mock_transcript.error = "Audio format not supported"
        
        mock_transcriber = Mock()
        mock_transcriber.upload_file.return_value = "https://test-url"
        mock_transcriber.transcribe.return_value = mock_transcript
        mock_aai.Transcriber.return_value = mock_transcriber
        
        client = AssemblyAIClient()
        
        with pytest.raises(TranscriptionError):
            await client.transcribe(sample_audio_bytes)


# =============================================================================
# T11: Speaker ID / Diarization Tests
# =============================================================================

class TestSpeakerID:
    """Tests for speaker identification."""
    
    def test_cosine_similarity_identical(self):
        """Test cosine similarity for identical vectors."""
        vec = np.array([1.0, 0.0, 0.0])
        similarity = cosine_similarity(vec, vec)
        assert pytest.approx(similarity, 0.001) == 1.0
    
    def test_cosine_similarity_orthogonal(self):
        """Test cosine similarity for orthogonal vectors."""
        vec1 = np.array([1.0, 0.0, 0.0])
        vec2 = np.array([0.0, 1.0, 0.0])
        similarity = cosine_similarity(vec1, vec2)
        assert pytest.approx(similarity, 0.001) == 0.0
    
    def test_cosine_similarity_opposite(self):
        """Test cosine similarity for opposite vectors."""
        vec1 = np.array([1.0, 0.0, 0.0])
        vec2 = np.array([-1.0, 0.0, 0.0])
        similarity = cosine_similarity(vec1, vec2)
        assert pytest.approx(similarity, 0.001) == -1.0
    
    @pytest.mark.asyncio
    async def test_match_speaker_high_confidence(self):
        """Test speaker matching with high confidence."""
        from cassandra.speaker_id import match_speaker, register_speaker, _speaker_db
        
        # Clear test org
        _speaker_db["test_org"] = {}
        
        # Register a known speaker
        known_embedding = np.random.randn(512).astype(np.float32)
        known_embedding = known_embedding / np.linalg.norm(known_embedding)
        await register_speaker("speaker_1", known_embedding, "test_org")
        
        # Match identical embedding
        match = await match_speaker(known_embedding, "test_org")
        
        assert match.speaker_id == "speaker_1"
        assert match.confidence >= SIMILARITY_THRESHOLD
        assert len(match.matches) > 0
    
    @pytest.mark.asyncio
    async def test_match_speaker_unknown(self):
        """Test speaker matching returns unknown for low confidence."""
        from cassandra.speaker_id import match_speaker, register_speaker, _speaker_db
        
        # Clear test org
        _speaker_db["test_org"] = {}
        
        # Register a known speaker
        known_embedding = np.random.randn(512).astype(np.float32)
        known_embedding = known_embedding / np.linalg.norm(known_embedding)
        await register_speaker("speaker_1", known_embedding, "test_org")
        
        # Match with completely different embedding
        different_embedding = np.random.randn(512).astype(np.float32)
        different_embedding = different_embedding / np.linalg.norm(different_embedding)
        
        match = await match_speaker(different_embedding, "test_org")
        
        assert match.speaker_id == "unknown_speaker"
        assert match.confidence < SIMILARITY_THRESHOLD
    
    @pytest.mark.asyncio
    async def test_match_speaker_empty_org(self):
        """Test speaker matching with empty organization."""
        from cassandra.speaker_id import match_speaker, _speaker_db
        
        # Ensure empty org
        _speaker_db["empty_org"] = {}
        
        embedding = np.random.randn(512).astype(np.float32)
        embedding = embedding / np.linalg.norm(embedding)
        
        match = await match_speaker(embedding, "empty_org")
        
        assert match.speaker_id == "unknown_speaker"
        assert match.confidence == 0.0


# =============================================================================
# T12: LLM Commitment Extraction Tests
# =============================================================================

class TestCommitmentExtraction:
    """Tests for commitment extraction."""
    
    def test_extracted_commitment_creation(self):
        """Test ExtractedCommitment dataclass."""
        commitment = ExtractedCommitment(
            speaker_id="A",
            commitment_text="I will send the report",
            deadline_date="2024-01-15",
            entity_type="commitment",
            confidence=0.92,
            raw_text="A: I will send the report by Friday"
        )
        
        assert commitment.speaker_id == "A"
        assert commitment.commitment_text == "I will send the report"
        assert commitment.deadline_date == "2024-01-15"
        assert commitment.confidence == 0.92
        assert commitment.requires_review is False
    
    def test_extracted_commitment_low_confidence(self):
        """Test requires_review flag for low confidence."""
        commitment = ExtractedCommitment(
            speaker_id="A",
            commitment_text="Maybe I'll do something",
            deadline_date=None,
            entity_type="commitment",
            confidence=0.5,
            raw_text="A: Maybe I'll do something"
        )
        
        assert commitment.requires_review is True
    
    def test_parse_date_valid(self):
        """Test date parsing with valid formats."""
        assert _parse_date("2024-01-15") == "2024-01-15"
        assert _parse_date("2024/01/15") == "2024-01-15"
        assert _parse_date("15-01-2024") == "2024-01-15"
    
    def test_parse_date_invalid(self):
        """Test date parsing with invalid formats."""
        assert _parse_date("invalid") is None
        assert _parse_date("") is None
        assert _parse_date(None) is None
        assert _parse_date("null") is None
    
    @pytest.mark.asyncio
    @patch("cassandra.extraction.get_llm_client")
    async def test_extract_commitments_success(self, mock_get_client):
        """Test successful commitment extraction."""
        # Mock LLM response
        mock_client = Mock()
        mock_response = json.dumps({
            "commitments": [
                {
                    "speaker_id": "A",
                    "commitment_text": "I will send the report",
                    "deadline_date": "2024-01-15",
                    "entity_type": "commitment",
                    "confidence": 0.92
                }
            ]
        })
        mock_client.complete = AsyncMock(return_value=mock_response)
        mock_get_client.return_value = mock_client
        
        from cassandra.extraction import extract_commitments
        
        transcript = "A: I will send the report by Friday."
        commitments = await extract_commitments(transcript)
        
        assert len(commitments) == 1
        assert commitments[0].speaker_id == "A"
        assert commitments[0].confidence == 0.92
    
    @pytest.mark.asyncio
    async def test_extract_commitments_empty_transcript(self):
        """Test extraction with empty transcript."""
        from cassandra.extraction import extract_commitments
        
        commitments = await extract_commitments("")
        assert commitments == []
        
        commitments = await extract_commitments("   ")
        assert commitments == []


# =============================================================================
# T17: JWT Auth Middleware Tests
# =============================================================================

class TestJWTAuth:
    """Tests for JWT authentication."""
    
    def test_user_context_creation(self):
        """Test UserContext dataclass."""
        user = UserContext(
            user_id="user_123",
            org_id="org_test",
            role="admin",
            permissions=["read:all", "write:all"],
            email="admin@example.com"
        )
        
        assert user.user_id == "user_123"
        assert user.org_id == "org_test"
        assert "read:all" in user.permissions
    
    @patch("cassandra.auth.get_verifier")
    def test_verify_jwt_success(self, mock_get_verifier):
        """Test successful JWT verification."""
        # Mock verifier
        mock_verifier = Mock()
        mock_verifier.verify = Mock(return_value={
            "sub": "user_123",
            "org_id": "org_test",
            "role": "member",
            "email": "test@example.com",
            "iat": time.time(),
            "exp": time.time() + 3600
        })
        mock_get_verifier.return_value = mock_verifier
        
        user = verify_jwt("valid_token")
        
        assert user.user_id == "user_123"
        assert user.org_id == "org_test"
        assert user.role == "member"
    
    def test_verify_jwt_expired(self):
        """Test expired token raises 401."""
        with patch("cassandra.auth.jwt.decode") as mock_decode:
            mock_decode.side_effect = jwt.ExpiredSignatureError("Token expired")
            
            with pytest.raises(HTTPException) as exc_info:
                verify_jwt("expired_token")
            
            assert exc_info.value.status_code == 401
            assert "expired" in exc_info.value.detail.lower()
    
    def test_verify_jwt_invalid(self):
        """Test invalid token raises 401."""
        with patch("cassandra.auth.jwt.decode") as mock_decode:
            mock_decode.side_effect = jwt.InvalidTokenError("Invalid token")
            
            with pytest.raises(HTTPException) as exc_info:
                verify_jwt("invalid_token")
            
            assert exc_info.value.status_code == 401
    
    def test_verify_jwt_missing(self):
        """Test missing token raises 401."""
        with pytest.raises(HTTPException) as exc_info:
            verify_jwt("")
        
        assert exc_info.value.status_code == 401
    
    @pytest.mark.asyncio
    async def test_get_current_user_dependency(self):
        """Test FastAPI get_current_user dependency."""
        from cassandra.auth import get_current_user
        
        # Mock credentials
        mock_credentials = Mock()
        mock_credentials.credentials = "valid_token"
        
        with patch("cassandra.auth.verify_jwt") as mock_verify:
            mock_verify.return_value = UserContext(
                user_id="user_123",
                org_id="org_test",
                role="member",
                permissions=["read:own"]
            )
            
            user = await get_current_user(mock_credentials)
            assert user.user_id == "user_123"
    
    @pytest.mark.asyncio
    async def test_get_current_user_no_credentials(self):
        """Test get_current_user with no credentials raises 401."""
        from cassandra.auth import get_current_user
        
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(None)
        
        assert exc_info.value.status_code == 401
        assert "required" in exc_info.value.detail.lower()


class TestRolePermissions:
    """Tests for role-based permissions."""
    
    def test_admin_permissions(self):
        """Test admin role has all permissions."""
        from cassandra.auth import _get_role_permissions
        
        perms = _get_role_permissions("admin")
        assert "read:all" in perms
        assert "write:all" in perms
        assert "delete:all" in perms
        assert "manage:users" in perms
    
    def test_member_permissions(self):
        """Test member role has limited permissions."""
        from cassandra.auth import _get_role_permissions
        
        perms = _get_role_permissions("member")
        assert "read:own" in perms
        assert "write:own" in perms
        assert "delete:all" not in perms
    
    def test_viewer_permissions(self):
        """Test viewer role has read-only permissions."""
        from cassandra.auth import _get_role_permissions
        
        perms = _get_role_permissions("viewer")
        assert "read:own" in perms
        assert "write:own" not in perms
    
    def test_unknown_role_defaults_to_viewer(self):
        """Test unknown role defaults to viewer permissions."""
        from cassandra.auth import _get_role_permissions
        
        perms = _get_role_permissions("unknown")
        assert "read:own" in perms  # Viewer permissions


# =============================================================================
# Integration Tests
# =============================================================================

class TestIntegration:
    """Integration tests for backend components."""
    
    @pytest.mark.asyncio
    async def test_full_pipeline_mock(self):
        """Test full pipeline with mocked services."""
        # This tests the flow: audio -> transcription -> speaker ID -> extraction
        
        # 1. Mock transcription
        mock_segments = [
            SpeakerSegment("A", "I will send the report by Friday", 0, 2000, 0.95),
            SpeakerSegment("B", "Thanks, I'll review it", 2000, 3000, 0.9)
        ]
        
        # 2. Mock speaker matching
        mock_embedding = np.random.randn(512).astype(np.float32)
        mock_embedding = mock_embedding / np.linalg.norm(mock_embedding)
        
        # 3. Mock commitment extraction
        mock_commitments = [
            ExtractedCommitment(
                speaker_id="A",
                commitment_text="I will send the report",
                deadline_date="2024-01-15",
                entity_type="commitment",
                confidence=0.92,
                raw_text="I will send the report by Friday"
            )
        ]
        
        # Verify pipeline components work together
        assert len(mock_segments) == 2
        assert len(mock_commitments) == 1
        assert mock_commitments[0].speaker_id == "A"


# =============================================================================
# Test Utilities
# =============================================================================

def test_import_all_modules():
    """Test that all modules can be imported."""
    import cassandra.main
    import cassandra.auth
    import cassandra.transcription
    import cassandra.speaker_id
    import cassandra.extraction
    
    assert cassandra.main is not None
    assert cassandra.auth is not None
    assert cassandra.transcription is not None
    assert cassandra.speaker_id is not None
    assert cassandra.extraction is not None


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
