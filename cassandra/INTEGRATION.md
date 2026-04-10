# CASSANDRA AI - Integration Documentation

## Overview

This document describes the integration points between the Backend and RAG (Retrieval-Augmented Generation) components of the Cassandra AI system.

---

## Component Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CASSANDRA AI SYSTEM                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────┐         ┌─────────────────────────────────┐   │
│  │      BACKEND LAYER      │         │          RAG LAYER              │   │
│  │                         │         │                                 │   │
│  │  ┌─────────────────┐    │         │  ┌─────────────────────────┐   │   │
│  │  │ WebSocket API   │◄───┼─────────┼──┤ Context Fetcher         │   │   │
│  │  │ /ws/voice       │    │  IP-01  │  │ (context_fetcher.py)    │   │   │
│  │  └─────────────────┘    │         │  └─────────────────────────┘   │   │
│  │           │             │         │                                 │   │
│  │  ┌─────────────────┐    │         │  ┌─────────────────────────┐   │   │
│  │  │ Auth Middleware │◄───┼─────────┼──┤ All RAG Components      │   │   │
│  │  │ (JWT + Org)     │    │  IP-04  │  │ (org-scoped queries)    │   │   │
│  │  └─────────────────┘    │         │  └─────────────────────────┘   │   │
│  │           │             │         │                                 │   │
│  │  ┌─────────────────┐    │         │  ┌─────────────────────────┐   │   │
│  │  │ Tool Registry   │◄───┼─────────┼──┤ Memory Manager          │   │   │
│  │  │ (create_ticket) │    │  IP-02  │  │ (memory_manager.py)     │   │   │
│  │  └─────────────────┘    │         │  └─────────────────────────┘   │   │
│  │           │             │         │                                 │   │
│  │  ┌─────────────────┐    │         │  ┌─────────────────────────┐   │   │
│  │  │ LLM Extraction  │◄───┼─────────┼──┤ Context Fetcher         │   │   │
│  │  │ (commitments)   │    │  IP-03  │  │ (relevance scoring)     │   │   │
│  │  └─────────────────┘    │         │  └─────────────────────────┘   │   │
│  │           │             │         │                                 │   │
│  │  ┌─────────────────┐    │         │  ┌─────────────────────────┐   │   │
│  │  │ Session Manager │◄───┼─────────┼──┤ Idempotency Handler     │   │   │
│  │  │ (state + WS)    │    │  IP-05  │  │ (idempotency.py)        │   │   │
│  │  └─────────────────┘    │         │  └─────────────────────────┘   │   │
│  │                         │         │                                 │   │
│  └─────────────────────────┘         └─────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        DATA LAYER (Supabase)                        │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │   │
│  │  │ tickets      │  │ memories     │  │ memory_ticket_map        │  │   │
│  │  │ (RLS + KMS)  │  │ (vector)     │  │ (GIN + B-tree indexes)   │  │   │
│  │  └──────────────┘  └──────────────┘  └──────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Integration Points

### IP-01: WebSocket Handler ↔ Context Fetcher

**Purpose:** Real-time context retrieval during voice calls

**Data Flow:**
```
Audio Stream → AssemblyAI → Transcript → Context Query → Relevant Memories
                                              ↓
                                    LLM Prompt Enhancement
```

**Interface:**
```python
# Backend calls RAG
context = await context_fetcher.fetch(
    query=transcript_text,
    org_id=org_id,
    ticket_id=ticket_id,
    max_results=10,
    min_relevance=0.7
)
```

**Contract:**
- Input: Transcript text, org_id, optional ticket_id
- Output: List of memory objects with relevance scores
- Latency: <200ms p95
- Error Handling: Returns empty list on failure (graceful degradation)

---

### IP-02: Tool Registry ↔ Memory Manager

**Purpose:** Store extracted information with embeddings

**Data Flow:**
```
LLM Extraction → Tool Call → Memory Manager → Embedding Generation → Storage
                                    ↓
                              Source Attribution
```

**Interface:**
```python
# Backend calls RAG
memory_id = await memory_manager.add_memory(
    content=extracted_commitment,
    org_id=org_id,
    ticket_id=ticket_id,
    source="voice_call",
    metadata={
        "speaker_id": speaker_id,
        "confidence": confidence_score,
        "timestamp": call_timestamp
    }
)
```

**Contract:**
- Input: Content, org_id, ticket_id, source tag, metadata
- Output: Memory UUID
- Side Effects: Generates embedding, stores in vector DB
- Idempotency: Checked via content hash

---

### IP-03: LLM Extraction ↔ Context Fetcher

**Purpose:** Enrich extractions with historical context

**Data Flow:**
```
Raw Transcript → Context Fetch → Enriched Prompt → LLM → Structured Extraction
                      ↓
              Related Commitments
              Past Deadlines
              Speaker History
```

**Interface:**
```python
# Backend calls RAG
context = await context_fetcher.fetch_for_extraction(
    transcript=transcript_text,
    org_id=org_id,
    context_types=["commitments", "deadlines", "speakers"]
)

# Enriched prompt sent to LLM
enriched_prompt = build_extraction_prompt(transcript_text, context)
```

**Contract:**
- Input: Transcript, org_id, context type filters
- Output: Structured context object
- Relevance Threshold: 0.7 minimum
- Max Context Length: 4000 tokens

---

### IP-04: Auth Middleware ↔ All RAG Components

**Purpose:** Enforce org isolation across all RAG operations

**Data Flow:**
```
JWT Token → Validation → org_id Extraction → RAG Query Scoping
                              ↓
                    All RAG queries include org_id filter
```

**Interface:**
```python
# Auth middleware injects org_id
async def require_auth(websocket: WebSocket):
    token = extract_token(websocket)
    payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    org_id = payload["org_id"]
    
    # Attach to websocket state for all handlers
    websocket.state.org_id = org_id
    return org_id

# All RAG queries use org_id
results = await context_fetcher.fetch(
    query=text,
    org_id=websocket.state.org_id  # Enforced isolation
)
```

**Contract:**
- Input: JWT token from WebSocket headers
- Output: Validated org_id
- Security: All RAG queries must include org_id
- Failure: 401 Unauthorized on invalid token

---

### IP-05: Session Manager ↔ Idempotency Handler

**Purpose:** Prevent duplicate operations on reconnection

**Data Flow:**
```
Client Request → Idempotency Key Check → Process → Store Response
                      ↓
              Duplicate? → Return Cached Response
```

**Interface:**
```python
# Backend calls RAG
result = await idempotency.check_and_execute(
    key=idempotency_key,
    operation=tool_call,
    ttl_seconds=86400  # 24 hours
)

# If duplicate, returns cached result
# If new, executes and stores result
```

**Contract:**
- Input: Idempotency key, operation callable, TTL
- Output: Operation result (cached or fresh)
- TTL: 24 hours default
- Storage: Redis for distributed access

---

## Data Flow: End-to-End Voice Processing

```
┌──────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Audio   │────►│ Transcription│────►│   Context    │────►│  LLM         │
│  Stream  │     │ (AssemblyAI) │     │   Fetch      │     │  Extraction  │
└──────────┘     └──────────────┘     └──────────────┘     └──────────────┘
     │                                            ▲                │
     │                                            │                │
     │     ┌──────────────────────────────────────┘                │
     │     │                                                       │
     │     │  RAG: context_fetcher.fetch()                         │
     │     │                                                       │
     │     └───────────────────────────────────────────────────────┘
     │                                                              │
     │     ┌────────────────────────────────────────────────────────┘
     │     │
     ▼     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           TOOL EXECUTION                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │ create_ticket│  │  add_memory  │  │fetch_context │                  │
│  │   (T13)      │  │   (T14)      │  │   (T15)      │                  │
│  └──────────────┘  └──────────────┘  └──────────────┘                  │
│       │                  │                  │                           │
│       ▼                  ▼                  ▼                           │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    SUPABASE DATA LAYER                          │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │   │
│  │  │ tickets  │  │ memories │  │  memory  │  │ memory_      │    │   │
│  │  │          │  │ (vector) │  │_ticket_map│  │ archive      │    │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────────┘    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Integration Testing

### Test Cases

| Test ID | Description | Components | Expected Result |
|---------|-------------|------------|-----------------|
| INT-001 | Context fetch during call | Backend + Context Fetcher | Returns relevant memories within 200ms |
| INT-002 | Memory storage after extraction | Backend + Memory Manager | Memory stored with embedding and source tag |
| INT-003 | Org isolation enforcement | Auth + All RAG | Cross-org queries return empty results |
| INT-004 | Idempotency on retry | Session + Idempotency | Duplicate request returns cached result |
| INT-005 | End-to-end voice flow | All components | Ticket created from voice call |

### Test Execution

```bash
# Run integration tests
pytest tests/integration/ -v --tb=short

# Run with coverage
pytest tests/integration/ --cov=cassandra --cov-report=html
```

---

## Error Handling

### Backend Errors

| Error | Handling | RAG Response |
|-------|----------|--------------|
| Invalid JWT | 401 Unauthorized | N/A |
| Missing org_id | 400 Bad Request | N/A |
| Rate limit | 429 Too Many Requests | N/A |

### RAG Errors

| Error | Handling | Backend Response |
|-------|----------|------------------|
| Vector DB unavailable | Log error, return empty context | Continue without context |
| Embedding failure | Log error, store without embedding | Continue, alert ops |
| Context timeout | Return partial results | Continue with available context |

---

## Performance SLAs

| Integration | p50 Latency | p95 Latency | p99 Latency |
|-------------|-------------|-------------|-------------|
| IP-01: Context Fetch | 50ms | 150ms | 200ms |
| IP-02: Memory Store | 100ms | 200ms | 300ms |
| IP-03: Extraction Context | 80ms | 180ms | 250ms |
| IP-04: Auth Validation | 10ms | 20ms | 50ms |
| IP-05: Idempotency Check | 5ms | 15ms | 30ms |

---

## Version Compatibility

| Backend Version | RAG Version | Status |
|-----------------|-------------|--------|
| 1.0.x | 1.0.x | ✅ Compatible |
| 1.1.x | 1.0.x | ⚠️ Deprecated |
| 1.1.x | 1.1.x | ✅ Compatible |

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2024-01-29 | Project Manager | Initial integration documentation |

---

*Last Updated: 2024-01-29*
