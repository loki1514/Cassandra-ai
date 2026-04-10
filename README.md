# Cassandra AI - Backend Architecture

A multi-tenant AI support system with intelligent memory, transcription capabilities, and secure data isolation.

## Phase 1: Foundation (Completed)

### Overview

Phase 1 establishes the foundational infrastructure for the Cassandra AI project, including database schema, security policies, encryption, and configuration management.

### Directory Structure

```
cassandra-ai/
├── cassandra/                    # Python application code
│   ├── __init__.py              # Package initialization
│   ├── config.py                # T07: Environment & Secrets Architecture
│   └── encryption.py            # T06: Per-Org KMS Encryption Setup
├── supabase/
│   └── migrations/              # Database migrations
│       ├── 001_initial_schema.sql    # T01: Initial Schema
│       ├── 002_memory_map.sql        # T02: Memory Ticket Map
│       ├── 003_soft_delete.sql       # T03: Soft-Delete Pattern
│       ├── 004_roles.sql             # T04: Scoped Service Roles
│       ├── 005_rls.sql               # T05: Row-Level Security
│       └── 006_archive.sql           # T08: Memory Archive
├── .env.example                 # Environment template
├── requirements.txt             # Python dependencies
└── README.md                    # This file
```

## Tasks Implemented

### T01: Supabase Project Bootstrap & Schema Init
**File:** `supabase/migrations/001_initial_schema.sql`

Creates the core database schema:
- `orgs` - Organizations/tenants table
- `users` - Users belonging to organizations
- `tickets` - Support tickets with status tracking
- Auto-updating `updated_at` triggers
- Proper indexes for query performance

### T02: memory_ticket_map Table + Dual Indexes
**File:** `supabase/migrations/002_memory_map.sql`

Links memories to tickets with confidence scoring:
- `memory_ticket_map` table with UUID primary key
- UNIQUE constraint on `(memory_id, ticket_id)`
- `idx_memory_lookup(memory_id, org_id)` - for memory-centric queries
- `idx_ticket_lookup(ticket_id, org_id)` - for ticket-centric queries
- Confidence score range validation (0.0-1.0)

### T03: Soft-Delete Pattern
**File:** `supabase/migrations/003_soft_delete.sql`

Implements soft-delete instead of physical deletion:
- Status CHECK constraint: `('active','completed','cancelled','archived')`
- BEFORE DELETE trigger that raises exception
- Helper functions: `soft_delete_ticket()`, `restore_ticket()`
- Views: `active_tickets`, `deleted_tickets`
- Index on `(org_id, status)` for efficient filtering

### T04: Scoped Service Roles
**File:** `supabase/migrations/004_roles.sql`

Creates least-privilege service roles:
- `cassandra_role` - AI service: INSERT, SELECT; REVOKE DELETE
- `backend_role` - API backend: ALL; REVOKE DELETE
- `analytics_role` - Read-only analytics access
- Default privileges for future tables

### T05: Row-Level Security (RLS)
**File:** `supabase/migrations/005_rls.sql`

Implements organization-based data isolation:
- ENABLE ROW LEVEL SECURITY on all tables
- Policies using `auth.jwt()->>'org_id'`
- FORCE ROW LEVEL SECURITY
- `get_current_org_id()` helper function
- Service role bypass configuration

### T06: Per-Org KMS Encryption Setup
**File:** `cassandra/encryption.py`

AWS KMS integration for per-organization encryption:
- `generate_org_key(org_id)` - Create KMS key for org
- `encrypt(payload, org_id)` - Encrypt data with envelope encryption
- `decrypt(ciphertext, org_id)` - Decrypt data
- Key caching for performance
- Automatic key rotation support

### T07: Environment & Secrets Architecture
**File:** `cassandra/config.py`

Pydantic Settings for configuration:
- `DatabaseSettings` - PostgreSQL connection
- `SupabaseSettings` - Supabase project config
- `AWSSettings` - AWS/KMS configuration
- `AssemblyAISettings` - Transcription service
- `OpenAISettings` - LLM/Embeddings
- `VectorStoreSettings` - Pinecone/Weaviate
- `SecuritySettings` - JWT, CORS, encryption flags
- Environment validation and defaults

### T08: memory_archive Backup Table
**File:** `supabase/migrations/006_archive.sql`

Archive table for backup and audit:
- `memory_archive` with JSONB original_data
- Index on `(org_id, entity_id)` for lookups
- Helper functions: `archive_entity()`, `restore_from_archive()`
- Views: `recent_archives`, `archive_stats_by_org`
- Cleanup function for old archives

## Setup Instructions

### 1. Clone and Install Dependencies

```bash
cd /mnt/okcomputer/output/cassandra-ai
pip install -r requirements.txt
```

### 2. Configure Environment Variables

```bash
cp .env.example .env
# Edit .env with your credentials
```

Required variables:
- `SUPABASE_URL` - Your Supabase project URL
- `SUPABASE_SERVICE_ROLE_KEY` - Service role key
- `DB_PASSWORD` - Database password
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` - AWS credentials
- `ASSEMBLYAI_API_KEY` - Transcription API key
- `OPENAI_API_KEY` - OpenAI API key
- `VECTOR_API_KEY` - Pinecone/Weaviate API key

### 3. Run Database Migrations

Execute the SQL migrations in order:

```bash
# Using psql
psql $DATABASE_URL -f supabase/migrations/001_initial_schema.sql
psql $DATABASE_URL -f supabase/migrations/002_memory_map.sql
psql $DATABASE_URL -f supabase/migrations/003_soft_delete.sql
psql $DATABASE_URL -f supabase/migrations/004_roles.sql
psql $DATABASE_URL -f supabase/migrations/005_rls.sql
psql $DATABASE_URL -f supabase/migrations/006_archive.sql
```

Or via Supabase Dashboard SQL Editor, run each migration in order.

### 4. Verify Installation

```python
from cassandra.config import settings
from cassandra.encryption import generate_org_key

# Test configuration
print(f"App: {settings.app_name} v{settings.app_version}")
print(f"Environment: {settings.environment}")

# Test encryption (requires AWS credentials)
# key_id = generate_org_key("test-org-123")
# print(f"Generated key: {key_id}")
```

## Security Features

### Multi-Tenant Isolation
- Row-Level Security (RLS) on all tables
- Organization-scoped queries via JWT claims
- Service roles with least-privilege access

### Data Protection
- Per-organization KMS encryption
- Envelope encryption for sensitive data
- Soft-delete pattern (no physical deletion)
- Archive table for audit trail

### Access Control
- Role-based access (cassandra_role, backend_role, analytics_role)
- DELETE operations blocked at trigger level
- CORS configuration for web clients

## Next Phase Readiness

Phase 1 foundation is complete. The system is ready for:

### Phase 2: Core API
- FastAPI application setup
- Authentication endpoints
- Ticket CRUD API
- WebSocket implementation

### Phase 3: AI Integration
- AssemblyAI transcription service
- OpenAI LLM integration
- Vector memory storage
- Memory-ticket linking

### Phase 4: Advanced Features
- Speaker diarization
- Real-time transcription
- Memory search and retrieval
- Analytics dashboard

## License

Proprietary - Cassandra AI Team
