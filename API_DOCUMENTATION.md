# Cassandra AI - API Documentation

## Phase 2: Cassandra Core - Tasks T09-T12 & T17

This document describes the backend API implemented for the Cassandra AI project.

---

## Table of Contents

1. [Overview](#overview)
2. [API Endpoints](#api-endpoints)
3. [WebSocket Protocol](#websocket-protocol)
4. [Authentication](#authentication)
5. [Modules](#modules)
6. [Test Coverage](#test-coverage)
7. [Deployment](#deployment)

---

## Overview

The Cassandra AI backend provides:

- **Real-time audio processing** via WebSocket
- **Transcription** with speaker diarization (AssemblyAI)
- **Speaker identification** with voice embeddings (Pyannote)
- **Commitment extraction** using LLM (OpenAI)
- **JWT authentication** with organization scoping

### Technology Stack

- **Framework**: FastAPI + Uvicorn
- **WebSocket**: Native FastAPI WebSocket support
- **Transcription**: AssemblyAI API
- **Diarization**: Pyannote.audio
- **LLM**: OpenAI GPT-4
- **Auth**: JWT (Supabase-compatible)

---

## API Endpoints

### Health & Monitoring

#### `GET /health`
Returns service health status and version information.

**Response:**
```json
{
  "status": "healthy",
  "service": "Cassandra AI",
  "version": "0.1.0",
  "environment": "production",
  "timestamp": "2024-01-15T10:30:00Z",
  "websocket_connections": 5
}
```

#### `GET /health/ready`
Kubernetes-style readiness probe.

**Response:**
```json
{"ready": true}
```

#### `GET /health/live`
Kubernetes-style liveness probe.

**Response:**
```json
{"alive": true}
```

### User Endpoints (Protected)

#### `GET /api/v1/me`
Get current authenticated user information.

**Headers:**
```
Authorization: Bearer <jwt_token>
```

**Response:**
```json
{
  "user_id": "user_123",
  "org_id": "org_test",
  "role": "member",
  "permissions": ["read:own", "write:own", "read:org"]
}
```

**Error Responses:**
- `401 Unauthorized`: Missing or invalid token
- `401 Unauthorized`: Token expired

---

## WebSocket Protocol

### Endpoint: `/ws/audio`

Real-time audio streaming endpoint for PCM16 audio data.

#### Connection
```javascript
const ws = new WebSocket('wss://api.cassandra.ai/ws/audio');
```

#### Client → Server Messages

**Binary Audio Data:**
- Send raw PCM16 audio bytes
- Sample rate: 16kHz (configurable)
- Format: 16-bit signed integers, little-endian

**JSON Control Messages:**

```json
// Reset buffer
{"action": "reset"}

// Get status
{"action": "status"}
```

#### Server → Client Messages

**Connected:**
```json
{
  "type": "connected",
  "client_id": "ws_1234567890",
  "message": "WebSocket connected. Send PCM16 audio data."
}
```

**Heartbeat (every 1 second):**
```json
{
  "type": "heartbeat",
  "timestamp": "2024-01-15T10:30:01Z",
  "client_id": "ws_1234567890"
}
```

**Audio Segment Complete:**
```json
{
  "type": "segment",
  "audio_length": 32000,
  "duration_ms": 1000,
  "segment_number": 1,
  "trigger": "silence_timeout"
}
```

**Status Response:**
```json
{
  "type": "status",
  "buffer_duration_ms": 500,
  "segment_count": 2
}
```

**Error:**
```json
{
  "type": "error",
  "message": "Invalid JSON control message"
}
```

#### Buffer Management

- **Silence Threshold**: 500ms (triggers segment extraction)
- **Max Buffer**: 10 seconds (triggers segment extraction)
- **Heartbeat Interval**: 1 second

### Authenticated Endpoint: `/ws/audio/{org_id}?token={jwt_token}`

Organization-scoped WebSocket with JWT authentication.

---

## Authentication

### JWT Token Structure

The backend accepts Supabase-compatible JWT tokens:

```json
{
  "sub": "user_123",
  "org_id": "org_test",
  "role": "member",
  "email": "user@example.com",
  "iat": 1705315800,
  "exp": 1705402200
}
```

### Roles & Permissions

| Role | Permissions |
|------|-------------|
| `admin` | read:all, write:all, delete:all, manage:users, manage:org |
| `member` | read:own, write:own, read:org |
| `viewer` | read:own, read:org |

### FastAPI Dependencies

```python
from cassandra.auth import get_current_user, require_permissions

@app.get("/protected")
async def protected_endpoint(user: UserContext = Depends(get_current_user)):
    return {"user_id": user.user_id}

@app.delete("/data/{id}")
async def delete_data(
    id: str,
    user: UserContext = Depends(require_permissions(["delete:all"]))
):
    pass
```

---

## Modules

### T09: FastAPI Application (`cassandra/main.py`)

**Classes:**
- `AudioBufferManager`: PCM16 buffer with silence detection
- `ConnectionManager`: WebSocket connection management with heartbeat

**Functions:**
- `health_check()`: Health endpoint handler
- `websocket_audio()`: WebSocket endpoint handler

### T10: Transcription (`cassandra/transcription.py`)

**Classes:**
- `SpeakerSegment`: Single speaker segment
- `TranscriptionResult`: Complete transcription with metadata
- `AssemblyAIClient`: AssemblyAI API client

**Functions:**
```python
async def transcribe(audio_bytes: bytes, org_id: Optional[str] = None) -> List[SpeakerSegment]
async def transcribe_with_metadata(audio_bytes: bytes, org_id: Optional[str] = None) -> TranscriptionResult
async def transcribe_batch(audio_segments: List[bytes], org_id: Optional[str] = None, max_concurrent: int = 3) -> List[List[SpeakerSegment]]
```

**Features:**
- Speaker diarization enabled
- Exponential backoff with 3 retries
- Rate limit handling
- No PII in logs

### T11: Speaker ID (`cassandra/speaker_id.py`)

**Classes:**
- `SpeakerMatch`: Speaker matching result
- `DiarizationSegment`: Diarization output segment
- `ModelManager`: Lazy-loaded Pyannote models

**Functions:**
```python
async def extract_embedding(audio_segment: bytes) -> np.ndarray  # 512-dim vector
async def match_speaker(embedding: np.ndarray, org_id: str, threshold: float = 0.85) -> SpeakerMatch
async def register_speaker(speaker_id: str, embedding: np.ndarray, org_id: str) -> bool
async def diarize_audio(audio_bytes: bytes) -> List[DiarizationSegment]
```

**Features:**
- 512-dimensional embeddings
- Cosine similarity matching
- Threshold: confidence < 0.85 → 'unknown_speaker'
- Lazy model loading

### T12: Commitment Extraction (`cassandra/extraction.py`)

**Classes:**
- `ExtractedCommitment`: Extracted commitment with metadata
- `EntityType`: Enum for entity types (commitment, action_item, deadline, follow_up)

**Functions:**
```python
async def extract_commitments(transcript: str, reference_date: Optional[datetime] = None) -> List[ExtractedCommitment]
async def extract_commitments_batch(transcripts: List[str], reference_date: Optional[datetime] = None) -> List[List[ExtractedCommitment]]
async def run_synthetic_tests() -> Dict[str, Any]
```

**Output Format:**
```json
{
  "speaker_id": "A",
  "commitment_text": "I will send the report",
  "deadline_date": "2024-01-15",
  "entity_type": "commitment",
  "confidence": 0.92,
  "requires_review": false
}
```

**Features:**
- Confidence < 0.7 → flagged for review
- Synthetic test transcripts included
- Date parsing for relative dates

### T17: JWT Auth (`cassandra/auth.py`)

**Classes:**
- `UserContext`: Authenticated user context
- `JWTVerifier`: JWT token verification

**Functions:**
```python
def verify_jwt(token: str) -> UserContext
async def get_current_user(credentials: HTTPAuthorizationCredentials) -> UserContext
async def get_current_user_optional(credentials: HTTPAuthorizationCredentials) -> Optional[UserContext]
def require_org_access(org_id_param: str = "org_id") -> Callable
def require_permissions(required_permissions: List[str]) -> Callable
```

**Features:**
- Supabase JWT verification (RS256/HS256)
- JWKS key fetching with caching
- Organization scoping
- Role-based permissions
- Secure logging (no payload content)

---

## Test Coverage

### Test File: `tests/test_backend.py`

**Test Classes:**

| Class | Description | Tests |
|-------|-------------|-------|
| `TestHealthEndpoints` | Health check endpoints | 3 tests |
| `TestAudioBufferManager` | Buffer management | 4 tests |
| `TestWebSocket` | WebSocket functionality | 2 tests |
| `TestTranscriptionModels` | Transcription data models | 2 tests |
| `TestTranscriptionClient` | AssemblyAI client | 2 tests |
| `TestSpeakerID` | Speaker identification | 4 tests |
| `TestCommitmentExtraction` | LLM extraction | 5 tests |
| `TestJWTAuth` | JWT authentication | 5 tests |
| `TestRolePermissions` | Role-based permissions | 4 tests |
| `TestIntegration` | Integration tests | 1 test |

**Total: 32+ unit tests**

### Running Tests

```bash
# Run all tests
pytest tests/test_backend.py -v

# Run with coverage
pytest tests/test_backend.py --cov=cassandra --cov-report=html

# Run specific test class
pytest tests/test_backend.py::TestHealthEndpoints -v

# Run in Docker
docker-compose --profile test run --rm test
```

---

## Deployment

### Docker

```bash
# Build production image
docker build -t cassandra-ai:latest .

# Run production container
docker run -p 8000:8000 --env-file .env cassandra-ai:latest

# Run development with hot reload
docker-compose --profile dev up api-dev

# Run with Redis cache
docker-compose --profile full up
```

### Environment Variables

Required variables:
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
- `ASSEMBLYAI_API_KEY`
- `OPENAI_API_KEY`
- `VECTOR_API_KEY`

Optional variables:
- `ENVIRONMENT` (default: development)
- `WORKERS` (default: 1)
- `LOG_LEVEL` (default: INFO)

### Kubernetes

Health probes configured for:
- **Liveness**: `/health/live`
- **Readiness**: `/health/ready`

---

## Files Created/Modified

### New Files

| File | Description |
|------|-------------|
| `cassandra/main.py` | FastAPI app with WebSocket |
| `cassandra/auth.py` | JWT auth middleware |
| `cassandra/transcription.py` | AssemblyAI integration |
| `cassandra/speaker_id.py` | Pyannote diarization |
| `cassandra/extraction.py` | LLM commitment extraction |
| `tests/test_backend.py` | Unit tests |
| `Dockerfile` | Multi-stage Docker build |
| `docker-compose.yml` | Docker Compose config |
| `.dockerignore` | Docker ignore patterns |
| `API_DOCUMENTATION.md` | This documentation |

### Modified Files

| File | Changes |
|------|---------|
| `cassandra/__init__.py` | Added new module exports |
| `requirements.txt` | Added pyannote, torch, numpy, scipy |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        Client                                │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ Web Client  │  │ Mobile App   │  │ Chrome Extension │   │
│  └──────┬──────┘  └──────┬───────┘  └────────┬─────────┘   │
└─────────┼────────────────┼───────────────────┼─────────────┘
          │                │                   │
          └────────────────┴───────────────────┘
                          │
                    WebSocket / HTTP
                          │
┌─────────────────────────┼───────────────────────────────────┐
│                         ▼                                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              FastAPI Application                     │   │
│  │  ┌─────────────┐  ┌──────────────┐  ┌───────────┐  │   │
│  │  │ /ws/audio   │  │ /health      │  │ /api/v1/* │  │   │
│  │  │ WebSocket   │  │ Health Check │  │ REST API  │  │   │
│  │  └──────┬──────┘  └──────────────┘  └───────────┘  │   │
│  └─────────┼───────────────────────────────────────────┘   │
│            │                                                │
│  ┌─────────┴───────────────────────────────────────────┐   │
│  │              Authentication Layer                    │   │
│  │         JWT Verification (Supabase)                  │   │
│  └─────────────────────────────────────────────────────┘   │
│            │                                                │
│  ┌─────────┴───────────────────────────────────────────┐   │
│  │              Business Logic Layer                    │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │   │
│  │  │Transcrip-│  │ Speaker  │  │ Commitment       │  │   │
│  │  │tion      │  │ ID       │  │ Extraction       │  │   │
│  │  │(Assembly │  │(Pyannote)│  │(OpenAI GPT-4)    │  │   │
│  │  │ AI)      │  │          │  │                  │  │   │
│  │  └──────────┘  └──────────┘  └──────────────────┘  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                            │
│              Cassandra AI Backend (Docker)                 │
└────────────────────────────────────────────────────────────┘
```

---

## License

Copyright © 2024 Cassandra AI Team
