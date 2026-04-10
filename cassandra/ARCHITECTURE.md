# CASSANDRA AI - System Architecture

## Overview

Cassandra AI is a stateless voice processing pipeline that transforms audio calls into structured tickets and memories. The system uses a multi-tenant architecture with strict org isolation and end-to-end encryption.

---

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                      CLIENT LAYER                                        │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐    │
│  │   Web Client    │  │  Mobile App     │  │  SIP Gateway    │  │  Admin Portal   │    │
│  │   (Browser)     │  │  (iOS/Android)  │  │  (Phone)        │  │  (Dashboard)    │    │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘    │
│           │                    │                    │                    │              │
│           └────────────────────┴────────────────────┘                    │              │
│                              │                                           │              │
│                              ▼                                           ▼              │
│                    ┌─────────────────┐                          ┌─────────────────┐    │
│                    │   WebSocket     │                          │   REST API      │    │
│                    │   Connection    │                          │   /api/v1/      │    │
│                    └────────┬────────┘                          └─────────────────┘    │
└─────────────────────────────┼──────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                    API GATEWAY LAYER                                     │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                              LOAD BALANCER (Nginx/ALB)                          │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                 │   │
│  │  │   Rate Limit    │  │   SSL/TLS       │  │   Request       │                 │   │
│  │  │   (per org)     │  │   Termination   │  │   Routing       │                 │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘                 │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                   APPLICATION LAYER                                      │
│                                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                              FASTAPI APPLICATION                                  │   │
│  │                                                                                   │   │
│  │  ┌───────────────────────────────────────────────────────────────────────────┐   │   │
│  │  │                         AUTHENTICATION & MIDDLEWARE                         │   │   │
│  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │   │   │
│  │  │  │ JWT          │  │ Org          │  │ Rate         │  │ CORS/        │  │   │   │
│  │  │  │ Validation   │  │ Scoping      │  │ Limiting     │  │ Security     │  │   │   │
│  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘  │   │   │
│  │  └───────────────────────────────────────────────────────────────────────────┘   │   │
│  │                                                                                   │   │
│  │  ┌───────────────────────────────────────────────────────────────────────────┐   │   │
│  │  │                         WEBSOCKET HANDLER (/ws/voice)                       │   │   │
│  │  │                                                                               │   │   │
│  │  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │   │   │
│  │  │  │ Connection   │───►│ Session      │───►│ Audio        │                  │   │   │
│  │  │  │ Manager      │    │ State        │    │ Buffer       │                  │   │   │
│  │  │  └──────────────┘    └──────────────┘    └──────────────┘                  │   │   │
│  │  │                                                                               │   │   │
│  │  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │   │   │
│  │  │  │ Reconnection │◄───│ Heartbeat    │◄───│ Cleanup      │                  │   │   │
│  │  │  │ Handler      │    │ Monitor      │    │ On Disconnect│                  │   │   │
│  │  │  └──────────────┘    └──────────────┘    └──────────────┘                  │   │   │
│  │  └───────────────────────────────────────────────────────────────────────────┘   │   │
│  │                                                                                   │   │
│  │  ┌───────────────────────────────────────────────────────────────────────────┐   │   │
│  │  │                         VOICE PROCESSING PIPELINE                           │   │   │
│  │  │                                                                               │   │   │
│  │  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │   │   │
│  │  │  │ Audio        │───►│ AssemblyAI   │───►│ Transcript   │                  │   │   │
│  │  │  │ Chunks       │    │ Stream       │    │ Buffer       │                  │   │   │
│  │  │  └──────────────┘    └──────────────┘    └──────────────┘                  │   │   │
│  │  │         │                   │                   │                           │   │   │
│  │  │         ▼                   ▼                   ▼                           │   │   │
│  │  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │   │   │
│  │  │  │ Pyannote     │───►│ Speaker      │───►│ Speaker      │                  │   │   │
│  │  │  │ Diarization  │    │ Segments     │    │ Embeddings   │                  │   │   │
│  │  │  └──────────────┘    └──────────────┘    └──────────────┘                  │   │   │
│  │  └───────────────────────────────────────────────────────────────────────────┘   │   │
│  │                                                                                   │   │
│  │  ┌───────────────────────────────────────────────────────────────────────────┐   │   │
│  │  │                         INTELLIGENCE LAYER                                  │   │   │
│  │  │                                                                               │   │   │
│  │  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │   │   │
│  │  │  │ LLM          │───►│ Commitment   │───►│ Structured   │                  │   │   │
│  │  │  │ Extraction   │    │ Detection    │    │ Output       │                  │   │   │
│  │  │  └──────────────┘    └──────────────┘    └──────────────┘                  │   │   │
│  │  │                                                                               │   │   │
│  │  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │   │   │
│  │  │  │ Context      │───►│ Tool         │───►│ Response     │                  │   │   │
│  │  │  │ Injection    │    │ Selection    │    │ Generation   │                  │   │   │
│  │  │  └──────────────┘    └──────────────┘    └──────────────┘                  │   │   │
│  │  └───────────────────────────────────────────────────────────────────────────┘   │   │
│  │                                                                                   │   │
│  │  ┌───────────────────────────────────────────────────────────────────────────┐   │   │
│  │  │                         TOOL REGISTRY                                       │   │   │
│  │  │                                                                               │   │   │
│  │  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │   │   │
│  │  │  │ create_ticket│    │ add_memory   │    │ fetch_context│                  │   │   │
│  │  │  │ (T13)        │    │ (T14)        │    │ (T15)        │                  │   │   │
│  │  │  └──────────────┘    └──────────────┘    └──────────────┘                  │   │   │
│  │  └───────────────────────────────────────────────────────────────────────────┘   │   │
│  │                                                                                   │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                     RAG LAYER                                            │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                              RAG COMPONENTS                                       │   │
│  │                                                                                   │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                 │   │
│  │  │ Context Fetcher │  │ Memory Manager  │  │ Idempotency     │                 │   │
│  │  │ (context_       │  │ (memory_        │  │ Handler         │                 │   │
│  │  │  fetcher.py)    │  │  manager.py)    │  │ (idempotency.py)│                 │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘                 │   │
│  │                                                                                   │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                 │   │
│  │  │ Truth Ledger    │  │ Reconciliation  │  │ Vector Search   │                 │   │
│  │  │ (truth_         │  │ Engine          │  │ (pgvector)      │                 │   │
│  │  │  ledger.py)     │  │ (reconciliation │  │                 │                 │   │
│  │  │                 │  │  .py)           │  │                 │                 │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘                 │   │
│  │                                                                                   │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                     DATA LAYER                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                              SUPABASE (PostgreSQL)                                │   │
│  │                                                                                   │   │
│  │  ┌─────────────────────────────────────────────────────────────────────────┐   │   │
│  │  │                         ROW-LEVEL SECURITY (RLS)                          │   │   │
│  │  │  • Org isolation enforced at database level                               │   │   │
│  │  │  • Cross-org queries return zero rows                                     │   │   │
│  │  │  • Service roles with least privilege                                     │   │   │
│  │  └─────────────────────────────────────────────────────────────────────────┘   │   │
│  │                                                                                   │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │   │
│  │  │ tickets      │  │ memories     │  │ memory_      │  │ memory_      │       │   │
│  │  │              │  │ (vector)     │  │ ticket_map   │  │ archive      │       │   │
│  │  │ • id         │  │              │  │              │  │              │       │   │
│  │  │ • org_id     │  │ • id         │  │ • id         │  │ • id         │       │   │
│  │  │ • status     │  │ • org_id     │  │ • org_id     │  │ • org_id     │       │   │
│  │  │ • title      │  │ • content    │  │ • ticket_id  │  │ • content    │       │   │
│  │  │ • created_at │  │ • embedding  │  │ • memory_id  │  │ • archived_at│       │   │
│  │  │ • updated_at │  │ • metadata   │  │ • metadata   │  │ • reason     │       │   │
│  │  │              │  │ • source     │  │              │  │              │       │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘       │   │
│  │                                                                                   │   │
│  │  ┌─────────────────────────────────────────────────────────────────────────┐   │   │
│  │  │                         ENCRYPTION (KMS)                                  │   │
│  │  │  • Per-org encryption keys                                                │   │   │
│  │  │  • Encryption at rest                                                     │   │   │
│  │  │  • Key rotation policy                                                    │   │   │
│  │  └─────────────────────────────────────────────────────────────────────────┘   │   │
│  │                                                                                   │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                   EXTERNAL SERVICES                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐    │
│  │ AssemblyAI      │  │ Pyannote        │  │ OpenAI/         │  │ Redis           │    │
│  │ (Transcription) │  │ (Diarization)   │  │ Anthropic (LLM) │  │ (Cache/Sessions)│    │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  └─────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow: Audio → Transcription → Extraction → Ticket Creation

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                           END-TO-END DATA FLOW                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘

STEP 1: AUDIO CAPTURE
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                          │
│   Client          WebSocket Handler           Audio Buffer                              │
│     │                    │                         │                                    │
│     │  1. Connect        │                         │                                    │
│     │───────────────────►│                         │                                    │
│     │                    │  2. Initialize          │                                    │
│     │                    │────────────────────────►│                                    │
│     │  3. Send audio     │                         │                                    │
│     │───────────────────►│                         │                                    │
│     │                    │  4. Buffer chunks       │                                    │
│     │                    │────────────────────────►│                                    │
│     │                    │                         │                                    │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
STEP 2: TRANSCRIPTION
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                          │
│   Audio Buffer      AssemblyAI Stream       Transcript Buffer                           │
│        │                   │                      │                                     │
│        │  5. Stream        │                      │                                     │
│        │──────────────────►│                      │                                     │
│        │                   │  6. Real-time        │                                     │
│        │                   │     transcription    │                                     │
│        │                   │─────────────────────►│                                     │
│        │                   │                      │                                     │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
STEP 3: SPEAKER IDENTIFICATION
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                          │
│   Transcript        Pyannote Diarization    Speaker Segments                            │
│        │                   │                      │                                     │
│        │  7. Audio +       │                      │                                     │
│        │     transcript    │                      │                                     │
│        │──────────────────►│                      │                                     │
│        │                   │  8. Identify         │                                     │
│        │                   │     speakers         │                                     │
│        │                   │─────────────────────►│                                     │
│        │                   │                      │                                     │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
STEP 4: CONTEXT RETRIEVAL (RAG)
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                          │
│   Transcript +      Context Fetcher         Relevant Memories                           │
│   Speaker Info          │                         │                                     │
│        │                │                         │                                     │
│        │ 9. Query with │                         │                                     │
│        │    context    │                         │                                     │
│        │──────────────►│                         │                                     │
│        │               │ 10. Vector search       │                                     │
│        │               │    (pgvector)           │                                     │
│        │               │────────────────────────►│                                     │
│        │               │                         │                                     │
│        │               │ 11. Return matches      │                                     │
│        │               │◄────────────────────────│                                     │
│        │               │                         │                                     │
│        │ 12. Context   │                         │                                     │
│        │◄──────────────│                         │                                     │
│        │               │                         │                                     │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
STEP 5: LLM EXTRACTION
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                          │
│   Context +         LLM Engine              Structured Data                             │
│   Transcript            │                         │                                     │
│        │                │                         │                                     │
│        │ 13. Enriched   │                         │                                     │
│        │     prompt     │                         │                                     │
│        │───────────────►│                         │                                     │
│        │                │ 14. Extract             │                                     │
│        │                │     commitments         │                                     │
│        │                │     deadlines           │                                     │
│        │                │     owners              │                                     │
│        │                │────────────────────────►│                                     │
│        │                │                         │                                     │
│        │                │ 15. JSON output         │                                     │
│        │                │◄────────────────────────│                                     │
│        │                │                         │                                     │
│        │ 16. Structured │                         │                                     │
│        │◄───────────────│                         │                                     │
│        │     extraction │                         │                                     │
│        │               │                         │                                     │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
STEP 6: TOOL EXECUTION
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                          │
│   Extraction        Tool Registry           Tool Results                                │
│        │                │                         │                                     │
│        │ 17. Select     │                         │                                     │
│        │     tools      │                         │                                     │
│        │───────────────►│                         │                                     │
│        │                │ 18. Validate            │                                     │
│        │                │     parameters          │                                     │
│        │                │────────────────────────►│                                     │
│        │                │                         │                                     │
│        │                │ 19. Execute             │                                     │
│        │                │     create_ticket       │                                     │
│        │                │     add_memory          │                                     │
│        │                │────────────────────────►│                                     │
│        │                │                         │                                     │
│        │                │ 20. Results             │                                     │
│        │                │◄────────────────────────│                                     │
│        │                │                         │                                     │
│        │ 21. Tool       │                         │                                     │
│        │◄───────────────│                         │                                     │
│        │     outputs    │                         │                                     │
│        │               │                         │                                     │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
STEP 7: DATA PERSISTENCE
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                          │
│   Tool Results      Supabase DB             Stored Records                              │
│        │                │                         │                                     │
│        │ 22. Insert     │                         │                                     │
│        │     ticket     │                         │                                     │
│        │───────────────►│                         │                                     │
│        │                │ 23. RLS check           │                                     │
│        │                │     (org_id)            │                                     │
│        │                │────────────────────────►│                                     │
│        │                │                         │                                     │
│        │                │ 24. Encrypt             │                                     │
│        │                │     (KMS)               │                                     │
│        │                │────────────────────────►│                                     │
│        │                │                         │                                     │
│        │                │ 25. Store               │                                     │
│        │                │────────────────────────►│                                     │
│        │                │                         │                                     │
│        │ 26. Confirm    │                         │                                     │
│        │◄───────────────│                         │                                     │
│        │               │                         │                                     │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
STEP 8: RESPONSE GENERATION
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                          │
│   Stored Data       Response Formatter      Natural Language                            │
│        │                │                         │                                     │
│        │ 27. Format     │                         │                                     │
│        │───────────────►│                         │                                     │
│        │                │ 28. Generate            │                                     │
│        │                │     human-readable      │                                     │
│        │                │     response            │                                     │
│        │                │────────────────────────►│                                     │
│        │                │                         │                                     │
│        │                │ 29. Add citations       │                                     │
│        │                │     confidence          │                                     │
│        │                │◄────────────────────────│                                     │
│        │                │                         │                                     │
│        │ 30. Response   │                         │                                     │
│        │◄───────────────│                         │                                     │
│        │               │                         │                                     │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
STEP 9: CLIENT NOTIFICATION
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                          │
│   Response          WebSocket             Client                                        │
│        │                │                   │                                           │
│        │ 31. Send       │                   │                                           │
│        │───────────────►│                   │                                           │
│        │                │ 32. Transmit      │                                           │
│        │                │──────────────────►│                                           │
│        │                │                   │                                           │
│        │                │ 33. Display       │                                           │
│        │                │                   │                                           │
│        │                │◄──────────────────│                                           │
│        │                │                   │                                           │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Interactions

### WebSocket Handler ↔ Session Manager

```
WebSocket Handler                    Session Manager
       │                                   │
       │  1. Client connects               │
       │──────────────────────────────────►│
       │                                   │
       │  2. Create session                │
       │  {session_id, org_id, user_id}    │
       │◄──────────────────────────────────│
       │                                   │
       │  3. Heartbeat ping                │
       │──────────────────────────────────►│
       │                                   │
       │  4. Update last_seen              │
       │◄──────────────────────────────────│
       │                                   │
       │  5. Disconnect                    │
       │──────────────────────────────────►│
       │                                   │
       │  6. Cleanup session               │
       │◄──────────────────────────────────│
```

### Tool Registry ↔ Memory Manager

```
Tool Registry                        Memory Manager
       │                                   │
       │  1. add_memory()                  │
       │  {content, org_id, ticket_id,     │
       │   source, metadata}               │
       │──────────────────────────────────►│
       │                                   │
       │  2. Generate embedding            │
       │  (OpenAI/embedding model)         │
       │                                   │
       │  3. Store in vector DB            │
       │  (pgvector)                       │
       │                                   │
       │  4. Return memory_id              │
       │◄──────────────────────────────────│
```

### Context Fetcher ↔ Vector Database

```
Context Fetcher                      Vector DB (pgvector)
       │                                   │
       │  1. fetch_context()               │
       │  {query, org_id, max_results}     │
       │──────────────────────────────────►│
       │                                   │
       │  2. Generate query embedding      │
       │                                   │
       │  3. Execute similarity search     │
       │  SELECT * FROM memories           │
       │  WHERE org_id = $1                │
       │  ORDER BY embedding <=> $2        │
       │  LIMIT $3                         │
       │                                   │
       │  4. Return results with scores    │
       │◄──────────────────────────────────│
```

---

## Data Models

### Ticket

```python
class Ticket(BaseModel):
    id: UUID
    org_id: UUID
    status: Literal["active", "archived", "deleted"]  # Soft-delete pattern
    title: str
    description: Optional[str]
    commitments: List[Commitment]
    deadlines: List[Deadline]
    owner_id: Optional[UUID]
    source: Literal["voice_call", "manual_entry", "system_generated"]
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    metadata: Dict[str, Any]
```

### Memory

```python
class Memory(BaseModel):
    id: UUID
    org_id: UUID
    ticket_id: Optional[UUID]
    content: str
    embedding: List[float]  # Vector embedding for similarity search
    source: Literal["voice_call", "manual_entry", "system_generated"]
    confidence: float  # 0.0 - 1.0
    speaker_id: Optional[UUID]
    metadata: Dict[str, Any]
    created_at: datetime
    expires_at: Optional[datetime]
```

### Memory-Ticket Map

```python
class MemoryTicketMap(BaseModel):
    id: UUID
    org_id: UUID
    ticket_id: UUID
    memory_id: UUID
    relationship_type: Literal["extracted_from", "related_to", "referenced_by"]
    metadata: Dict[str, Any]  # GIN indexed for flexible queries
    created_at: datetime
```

---

## Security Boundaries

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              SECURITY LAYERS                                            │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                          │
│  LAYER 1: NETWORK                                                                        │
│  ├── TLS 1.3 for all connections                                                         │
│  ├── VPC isolation                                                                       │
│  └── DDoS protection                                                                     │
│                                                                                          │
│  LAYER 2: AUTHENTICATION                                                                 │
│  ├── JWT validation                                                                      │
│  ├── Token expiration                                                                    │
│  └── Refresh token rotation                                                              │
│                                                                                          │
│  LAYER 3: AUTHORIZATION                                                                  │
│  ├── Org scoping (all queries)                                                           │
│  ├── Role-based access control                                                           │
│  └── RLS policies (database level)                                                       │
│                                                                                          │
│  LAYER 4: DATA PROTECTION                                                                │
│  ├── Encryption at rest (KMS)                                                            │
│  ├── Encryption in transit (TLS)                                                         │
│  ├── PII detection and masking                                                           │
│  └── Key rotation                                                                        │
│                                                                                          │
│  LAYER 5: AUDIT                                                                          │
│  ├── All mutations logged                                                                │
│  ├── Immutable event log                                                                 │
│  └── Source attribution                                                                  │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Scalability Design

### Stateless Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                           STATELESS DESIGN                                              │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                          │
│  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐                           │
│  │  API Server  │      │  API Server  │      │  API Server  │                           │
│  │  Instance 1  │      │  Instance 2  │      │  Instance N  │                           │
│  │              │      │              │      │              │                           │
│  │  No local    │      │  No local    │      │  No local    │                           │
│  │  state       │      │  state       │      │  state       │                           │
│  └──────┬───────┘      └──────┬───────┘      └──────┬───────┘                           │
│         │                     │                     │                                    │
│         └─────────────────────┴─────────────────────┘                                    │
│                               │                                                          │
│                               ▼                                                          │
│                    ┌─────────────────┐                                                   │
│                    │   Redis Cluster   │  Session state, cache, idempotency             │
│                    │                   │                                                   │
│                    └─────────────────┘                                                   │
│                               │                                                          │
│                               ▼                                                          │
│                    ┌─────────────────┐                                                   │
│                    │   Supabase        │  Persistent data                                │
│                    │   (PostgreSQL)    │                                                   │
│                    └─────────────────┘                                                   │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

### Horizontal Scaling

| Component | Scaling Strategy | Trigger |
|-----------|------------------|---------|
| API Servers | Horizontal Pod Autoscaler | CPU > 70% |
| Redis | Cluster mode | Memory > 80% |
| Database | Read replicas | Query latency > 100ms |

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2024-01-29 | Project Manager | Initial architecture documentation |

---

*Last Updated: 2024-01-29*
