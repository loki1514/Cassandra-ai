-- =========================================================================
-- Cassandra Voice Server — Initial Database Schema
-- Supabase (PostgreSQL + pgvector)
-- Run with: supabase db execute -f backend/infrastructure/migrations/001_initial_schema.sql
-- =========================================================================

-- Enable pgvector extension if not already enabled
CREATE EXTENSION IF NOT EXISTS vector;

-- ═══════════════════════════════════════════════════════════════
-- 1. API Keys
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash TEXT NOT NULL UNIQUE,          -- SHA-256 hash of the key
    key_prefix TEXT NOT NULL,               -- First 8 chars for identification
    org_id UUID NOT NULL,
    user_id UUID,
    role TEXT NOT NULL DEFAULT 'tenant',    -- tenant, super_tenant, admin, org_super_admin, owner, master_admin
    name TEXT,                             -- Friendly name for the key
    is_active BOOLEAN NOT NULL DEFAULT true,
    expires_at TIMESTAMPTZ,                -- NULL = never expires
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,

    CONSTRAINT role_check CHECK (role IN ('tenant', 'super_tenant', 'admin', 'org_super_admin', 'owner', 'master_admin'))
);

CREATE INDEX idx_api_keys_org_id ON api_keys(org_id);
CREATE INDEX idx_api_keys_key_hash ON api_keys(key_hash);
CREATE INDEX idx_api_keys_user_id ON api_keys(user_id);

-- ═══════════════════════════════════════════════════════════════
-- 2. Voice Sessions
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL UNIQUE,        -- Human-readable session ID (e.g., 'mtg-20260408-143022-abcd1234')
    org_id UUID NOT NULL,
    user_id UUID,
    meeting_id UUID REFERENCES meetings(id) ON DELETE SET NULL,

    -- Session metadata
    client_type TEXT NOT NULL DEFAULT 'web', -- web, mobile
    protocol_version TEXT NOT NULL DEFAULT 'v1', -- v1 (legacy relay), v2 (smart)
    initial_role TEXT DEFAULT 'GENERAL',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_seconds INTEGER GENERATED ALWAYS AS (
        EXTRACT(EPOCH FROM (COALESCE(ended_at, now()) - started_at))
    ) STORED,

    -- Session state
    state TEXT NOT NULL DEFAULT 'idle',     -- idle, listening, processing, speaking, disconnected
    audio_buffer_seconds FLOAT DEFAULT 0.0,
    transcript_turns INTEGER DEFAULT 0,
    tool_calls INTEGER DEFAULT 0,
    interrupts_count INTEGER DEFAULT 0,

    -- Cleanup
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sessions_org_id ON sessions(org_id);
CREATE INDEX idx_sessions_meeting_id ON sessions(meeting_id);
CREATE INDEX idx_sessions_user_id ON sessions(user_id);
CREATE INDEX idx_sessions_session_id ON sessions(session_id);
CREATE INDEX idx_sessions_started_at ON sessions(started_at DESC);

-- ═══════════════════════════════════════════════════════════════
-- 3. Voice Usage (monthly tracking per org/user)
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS voice_usage_monthly (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    user_id UUID,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,

    -- Usage counters
    audio_seconds INTEGER NOT NULL DEFAULT 0,
    stt_calls INTEGER NOT NULL DEFAULT 0,
    tts_tokens INTEGER NOT NULL DEFAULT 0,
    llm_tokens INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    sessions_count INTEGER NOT NULL DEFAULT 0,

    -- Limits
    role TEXT NOT NULL DEFAULT 'tenant',
    monthly_limit_seconds INTEGER NOT NULL DEFAULT 5400, -- Copied at creation time

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Unique constraint: one record per org/user/month
    CONSTRAINT voice_usage_unique UNIQUE (org_id, user_id, year, month),
    CONSTRAINT month_range CHECK (month BETWEEN 1 AND 12),
    CONSTRAINT year_range CHECK (year >= 2024)
);

CREATE INDEX idx_voice_usage_org_id ON voice_usage_monthly(org_id);
CREATE INDEX idx_voice_usage_year_month ON voice_usage_monthly(year, month);

-- ═══════════════════════════════════════════════════════════════
-- 4. Session Transcripts (flattened for fast LLM context injection)
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS session_transcripts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    org_id UUID NOT NULL,

    -- Content
    speaker TEXT NOT NULL,                  -- 'user' or 'ai'
    content TEXT NOT NULL,
    turn_index INTEGER NOT NULL,           -- Order within session
    chunk_index INTEGER,                    -- Index within turn (for partial transcripts)
    is_final BOOLEAN NOT NULL DEFAULT true,

    -- Metadata
    audio_start_ms INTEGER,                -- Position in audio stream
    audio_end_ms INTEGER,
    processing_latency_ms FLOAT,           -- Time to process this segment
    vad_speech_ms FLOAT,                   -- Detected speech duration

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_session_transcripts_session_id ON session_transcripts(session_id);
CREATE INDEX idx_session_transcripts_org_id ON session_transcripts(org_id);
CREATE INDEX idx_session_transcripts_turn_index ON session_transcripts(session_id, turn_index);

-- ═══════════════════════════════════════════════════════════════
-- 5. Tool Call Logs
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS tool_call_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    org_id UUID NOT NULL,

    -- Tool info
    tool_name TEXT NOT NULL,
    tool_arguments JSONB,                  -- Stored arguments
    tool_result JSONB,                     -- Result returned

    -- Execution metadata
    status TEXT NOT NULL,                  -- 'success', 'error', 'timeout'
    duration_ms INTEGER,
    error_message TEXT,

    -- Timestamps
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_tool_call_logs_session_id ON tool_call_logs(session_id);
CREATE INDEX idx_tool_call_logs_org_id ON tool_call_logs(org_id);
CREATE INDEX idx_tool_call_logs_tool_name ON tool_call_logs(tool_name);
CREATE INDEX idx_tool_call_logs_started_at ON tool_call_logs(started_at DESC);

-- ═══════════════════════════════════════════════════════════════
-- 6. Error Logs
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS error_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    org_id UUID,

    -- Error info
    severity TEXT NOT NULL,                 -- 'debug', 'info', 'warning', 'error', 'critical'
    error_type TEXT NOT NULL,              -- Exception class name
    message TEXT NOT NULL,
    context JSONB,                         -- Additional structured context
    stack_trace TEXT,

    -- Source info
    component TEXT,                         -- 'vad', 'stt', 'tts', 'llm', 'auth', 'websocket', etc.
    provider TEXT,                          -- 'silero', 'openai', 'elevenlabs', etc.

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_error_logs_session_id ON error_logs(session_id);
CREATE INDEX idx_error_logs_org_id ON error_logs(org_id);
CREATE INDEX idx_error_logs_severity ON error_logs(severity);
CREATE INDEX idx_error_logs_created_at ON error_logs(created_at DESC);
CREATE INDEX idx_error_logs_error_type ON error_logs(error_type);

-- ═══════════════════════════════════════════════════════════════
-- 7. Existing tables — Add new columns
-- ═══════════════════════════════════════════════════════════════

-- meetings: add session tracking columns
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS session_id UUID;
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS org_id UUID;
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active';
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ;
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS total_audio_seconds INTEGER DEFAULT 0;
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS total_turns INTEGER DEFAULT 0;

-- transcripts: extend with session tracking
ALTER TABLE transcript_chunks ADD COLUMN IF NOT EXISTS session_id UUID;
ALTER TABLE transcript_chunks ADD COLUMN IF NOT EXISTS org_id UUID;
ALTER TABLE transcript_chunks ADD COLUMN IF NOT EXISTS is_final BOOLEAN DEFAULT true;
ALTER TABLE transcript_chunks ADD COLUMN IF NOT EXISTS vad_speech_ms FLOAT;
ALTER TABLE transcript_chunks ADD COLUMN IF NOT EXISTS processing_latency_ms FLOAT;

-- artifacts: add session tracking
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS session_id UUID;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS org_id UUID;

-- ═══════════════════════════════════════════════════════════════
-- 8. RPC Functions
-- ═══════════════════════════════════════════════════════════════

-- match_session_context: semantic search over session transcripts + artifacts
CREATE OR REPLACE FUNCTION match_session_context(
    query_embedding vector,
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 5,
    p_session_id uuid DEFAULT NULL,
    p_org_id uuid DEFAULT NULL
)
RETURNS TABLE (
    id uuid,
    content text,
    source_type text,
    source_id uuid,
    similarity float,
    chunk_index int,
    speaker text
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    WITH transcripts AS (
        SELECT
            t.id,
            t.content,
            'transcript' as source_type,
            t.session_id as source_id,
            (t.content::vector <=> query_embedding) AS similarity,
            t.chunk_index,
            t.speaker
        FROM transcript_chunks t
        WHERE t.embedding IS NOT NULL
          AND (p_session_id IS NULL OR t.session_id = p_session_id)
          AND (p_org_id IS NULL OR t.org_id = p_org_id)
          AND (t.embedding <=> query_embedding) < (1.0 - match_threshold)
        ORDER BY t.embedding <=> query_embedding
        LIMIT match_count
    ),
    artifacts AS (
        SELECT
            a.id,
            a.content,
            'artifact' as source_type,
            a.meeting_id as source_id,
            (a.content::vector <=> query_embedding) AS similarity,
            -1 as chunk_index,
            'ai'::text as speaker
        FROM artifacts a
        WHERE a.embedding IS NOT NULL
          AND (p_org_id IS NULL OR a.meeting_id IN (
              SELECT m.id FROM meetings m WHERE m.org_id = p_org_id
          ))
          AND (a.embedding <=> query_embedding) < (1.0 - match_threshold)
        ORDER BY a.embedding <=> query_embedding
        LIMIT match_count
    )
    SELECT * FROM transcripts
    UNION ALL
    SELECT * FROM artifacts
    ORDER BY similarity ASC
    LIMIT match_count;
END;
$$;

-- increment_voice_usage: atomically update monthly usage counters
CREATE OR REPLACE FUNCTION increment_voice_usage(
    p_org_id uuid,
    p_user_id uuid DEFAULT NULL,
    p_audio_seconds INTEGER DEFAULT 0,
    p_stt_calls INTEGER DEFAULT 0,
    p_tts_tokens INTEGER DEFAULT 0,
    p_llm_tokens INTEGER DEFAULT 0,
    p_tool_calls INTEGER DEFAULT 0,
    p_role TEXT DEFAULT 'tenant',
    p_limit_seconds INTEGER DEFAULT 5400
)
RETURNS TABLE (
    audio_seconds_total bigint,
    monthly_limit_seconds bigint,
    is_exceeded boolean
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_year INTEGER;
    v_month INTEGER;
    v_existing record;
    v_audio_total bigint;
BEGIN
    v_year := EXTRACT(YEAR FROM now())::INTEGER;
    v_month := EXTRACT(MONTH FROM now())::INTEGER;

    -- Try to update existing record
    UPDATE voice_usage_monthly
    SET
        audio_seconds = audio_seconds + p_audio_seconds,
        stt_calls = stt_calls + p_stt_calls,
        tts_tokens = tts_tokens + p_tts_tokens,
        llm_tokens = llm_tokens + p_llm_tokens,
        tool_calls = tool_calls + p_tool_calls,
        updated_at = now()
    WHERE org_id = p_org_id
      AND user_id IS NOT DISTINCT FROM p_user_id
      AND year = v_year
      AND month = v_month
    RETURNING audio_seconds INTO v_audio_total;

    -- Insert if not exists
    IF v_audio_total IS NULL THEN
        INSERT INTO voice_usage_monthly (
            org_id, user_id, year, month,
            audio_seconds, stt_calls, tts_tokens, llm_tokens, tool_calls,
            role, monthly_limit_seconds
        ) VALUES (
            p_org_id, p_user_id, v_year, v_month,
            p_audio_seconds, p_stt_calls, p_tts_tokens, p_llm_tokens, p_tool_calls,
            p_role, p_limit_seconds
        )
        ON CONFLICT (org_id, user_id, year, month) DO UPDATE
        SET
            audio_seconds = voice_usage_monthly.audio_seconds + p_audio_seconds,
            stt_calls = voice_usage_monthly.stt_calls + p_stt_calls,
            tts_tokens = voice_usage_monthly.tts_tokens + p_tts_tokens,
            llm_tokens = voice_usage_monthly.llm_tokens + p_llm_tokens,
            tool_calls = voice_usage_monthly.tool_calls + p_tool_calls,
            updated_at = now()
        RETURNING audio_seconds INTO v_audio_total;
    END IF;

    RETURN QUERY SELECT
        v_audio_total::bigint as audio_seconds_total,
        p_limit_seconds::bigint as monthly_limit_seconds,
        (v_audio_total > p_limit_seconds) as is_exceeded;
END;
$$;

-- ═══════════════════════════════════════════════════════════════
-- 9. Row Level Security (RLS)
-- ═══════════════════════════════════════════════════════════════

-- Enable RLS on new tables
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE voice_usage_monthly ENABLE ROW LEVEL SECURITY;
ALTER TABLE session_transcripts ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_call_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE error_logs ENABLE ROW LEVEL SECURITY;

-- api_keys: only service role can access
CREATE POLICY "Service role full access to api_keys" ON api_keys
    FOR ALL USING (auth.role() = 'service_role');

-- sessions: org-based access
CREATE POLICY "Users can view their org sessions" ON sessions
    FOR SELECT USING (
        org_id IN (SELECT org_id FROM user_orgs WHERE user_id = auth.uid())
    );

-- voice_usage: org-based access
CREATE POLICY "Users can view their org usage" ON voice_usage_monthly
    FOR SELECT USING (
        org_id IN (SELECT org_id FROM user_orgs WHERE user_id = auth.uid())
    );

-- session_transcripts: org-based access
CREATE POLICY "Users can view their org transcripts" ON session_transcripts
    FOR SELECT USING (
        org_id IN (SELECT org_id FROM user_orgs WHERE user_id = auth.uid())
    );

-- tool_call_logs: org-based access
CREATE POLICY "Users can view their org tool calls" ON tool_call_logs
    FOR SELECT USING (
        org_id IN (SELECT org_id FROM user_orgs WHERE user_id = auth.uid())
    );

-- error_logs: service role only
CREATE POLICY "Service role full access to error_logs" ON error_logs
    FOR ALL USING (auth.role() = 'service_role');

-- ═══════════════════════════════════════════════════════════════
-- 10. Updated Meetings Table (ensure columns exist)
-- ═══════════════════════════════════════════════════════════════
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS meeting_id TEXT;
CREATE INDEX IF NOT EXISTS idx_meetings_org_id ON meetings(org_id);

-- ═══════════════════════════════════════════════════════════════
-- Done
-- ═══════════════════════════════════════════════════════════════
