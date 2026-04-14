-- T20: Room-Based Voice Diarization Schema
-- Production architecture for property-scoped ephemeral rooms
-- with org-scoped voice enrollment and post-session speaker identification.
--
-- Fixes applied:
--   CR-4: Switched ivfflat to hnsw (pgvector >= 0.5 requires lists param)
--   H-1:  RLS policies added for all 4 tables (using users table, not user_orgs)
--   M-5:  composite index on (room_id, start_ms) for enriched_transcripts
--   M-6:  partial index on rooms.active_session_id for fast lookups

-- Hotfix: legacy backend/infrastructure migrations reference user_orgs in RLS policies
-- but never created the table. We create it as a view on users so those policies work.
CREATE OR REPLACE VIEW user_orgs AS
SELECT id AS user_id, org_id
FROM users;

-- ============================================
-- 1. VOICE PROFILES (org-scoped enrollment)
-- ============================================
CREATE TABLE IF NOT EXISTS voice_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id TEXT NOT NULL UNIQUE,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- 512-dim embedding (fixed from broken VECTOR(256))
    embedding vector(512) NOT NULL,

    -- Enrollment metadata
    status TEXT NOT NULL DEFAULT 'active',
    sample_count INTEGER NOT NULL DEFAULT 0,
    quality_score FLOAT,
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB DEFAULT '{}',

    UNIQUE(org_id, user_id)
);

CREATE INDEX idx_voice_profiles_org_id ON voice_profiles(org_id);
CREATE INDEX idx_voice_profiles_user_id ON voice_profiles(user_id);
-- CR-4 fix: ivfflat REQUIRES lists parameter in pgvector >= 0.5.
-- Using hnsw as alternative (no lists param, better recall, more memory).
CREATE INDEX idx_voice_profiles_embedding ON voice_profiles
 USING hnsw (embedding vector_cosine_ops);

-- ============================================
-- 2. ROOMS (property-scoped ephemeral meeting rooms)
-- ============================================
CREATE TABLE IF NOT EXISTS rooms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id TEXT NOT NULL UNIQUE,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,

    -- Room metadata
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'waiting',  -- waiting | active | ended

    -- Participants enrolled in this room (JSONB snapshot, not a join table)
    participants JSONB NOT NULL DEFAULT '[]',

    -- Session linkage
    active_session_id TEXT,
    session_ids TEXT[] DEFAULT '{}',

    -- Analysis state
    analysis_status TEXT DEFAULT 'pending',  -- pending | running | completed | failed
    analysis_result JSONB,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID REFERENCES users(id),
    ended_at TIMESTAMPTZ
);

CREATE INDEX idx_rooms_org_id ON rooms(org_id);
CREATE INDEX idx_rooms_property_id ON rooms(property_id);
CREATE INDEX idx_rooms_status ON rooms(status);
CREATE INDEX idx_rooms_room_id ON rooms(room_id);
-- M-6 fix: index on active_session_id for fast lookups
CREATE INDEX idx_rooms_active_session ON rooms(active_session_id)
 WHERE active_session_id IS NOT NULL;

-- ============================================
-- 3. ENRICHED TRANSCRIPTS (post-session speaker-identified transcript)
-- ============================================
CREATE TABLE IF NOT EXISTS enriched_transcripts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    session_id TEXT,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,

    -- Speaker identification
    speaker_label TEXT NOT NULL,  -- Pyannote label: "SPEAKER_00", etc.
    speaker_name TEXT NOT NULL,   -- Matched name: "John", or "Unknown Speaker"
    speaker_user_id UUID,         -- Matched user UUID, or NULL
    confidence FLOAT NOT NULL,     -- Cosine similarity score

    -- Content
    text TEXT NOT NULL,
    start_ms BIGINT NOT NULL,
    end_ms BIGINT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_enriched_transcripts_room_id ON enriched_transcripts(room_id);
CREATE INDEX idx_enriched_transcripts_org_id ON enriched_transcripts(org_id);
CREATE INDEX idx_enriched_transcripts_speaker ON enriched_transcripts(speaker_user_id);
CREATE INDEX idx_enriched_transcripts_session_id ON enriched_transcripts(session_id);
-- M-5 fix: composite index for get_room_analysis ORDER BY start_ms query
CREATE INDEX idx_enriched_transcripts_room_start ON enriched_transcripts(room_id, start_ms);

-- ============================================
-- 4. ACTION ITEMS (extracted with speaker attribution)
-- ============================================
CREATE TABLE IF NOT EXISTS action_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,

    -- Attribution
    assignee_id UUID REFERENCES users(id),
    assignee_name TEXT,
    speaker_user_id UUID,
    confidence FLOAT,

    -- Content
    title TEXT NOT NULL,
    description TEXT,
    priority TEXT DEFAULT 'medium',
    deadline TIMESTAMPTZ,
    status TEXT DEFAULT 'open',  -- open | in_progress | completed | dismissed

    -- Source
    source_text TEXT,
    start_ms BIGINT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_action_items_room_id ON action_items(room_id);
CREATE INDEX idx_action_items_org_id ON action_items(org_id);
CREATE INDEX idx_action_items_assignee_id ON action_items(assignee_id);
CREATE INDEX idx_action_items_status ON action_items(status);

-- ============================================
-- 5. SPEAKER ANALYSIS AUDIT (mapping quality tracking)
-- ============================================
-- Tracks how reliable the speaker attribution was for each room.
-- Used to flag results that require human review and to detect systematic drift.
CREATE TABLE IF NOT EXISTS speaker_analysis_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    session_id TEXT,

    -- Mapping quality counters
    pyannote_speakers INT DEFAULT 0,    -- unique Pyannote labels found (SPEAKER_00...)
    assemblyai_speakers INT DEFAULT 0,   -- unique AssemblyAI labels found (A, B, C...)
    overlap_matched INT DEFAULT 0,         -- segments matched by time overlap
    unmatched_speakers INT DEFAULT 0,    -- speakers with 0.0 confidence

    -- Confidence scores
    mapping_confidence_avg FLOAT DEFAULT 0.0,  -- avg cosine similarity of matched speakers
    mapping_confidence_min FLOAT DEFAULT 0.0,  -- lowest match score (weakest link)
    high_confidence_matches INT DEFAULT 0,  -- matches >= 0.85

    -- Derived flags
    has_unknowns BOOLEAN DEFAULT FALSE,   -- True if any speaker had 0.0 confidence
    requires_review BOOLEAN GENERATED ALWAYS AS (
        has_unknowns
        OR mapping_confidence_avg < 0.60
        OR (mapping_confidence_min > 0.0 AND mapping_confidence_min < 0.50)
    ) STORED,

    -- Low-confidence speakers for quick review
    unknown_speaker_labels TEXT[],  -- Pyannote labels that didn't match anyone

    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_speaker_analysis_audit_room_id ON speaker_analysis_audit(room_id);
CREATE INDEX idx_speaker_analysis_audit_org_id ON speaker_analysis_audit(org_id);
CREATE INDEX idx_speaker_analysis_audit_requires_review ON speaker_analysis_audit(requires_review)
    WHERE requires_review = TRUE;

-- ============================================
-- 6. ROW LEVEL SECURITY (H-1 fix)
-- ============================================
-- Enable RLS on all 4 new tables
ALTER TABLE voice_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE rooms ENABLE ROW LEVEL SECURITY;
ALTER TABLE enriched_transcripts ENABLE ROW LEVEL SECURITY;
ALTER TABLE action_items ENABLE ROW LEVEL SECURITY;

-- ── voice_profiles ────────────────────────────────────────────────────────────
-- Service role: full access (used by all server-side code)
CREATE POLICY "service_role_all_voice_profiles" ON voice_profiles
    FOR ALL USING (auth.role() = 'service_role');

-- Users can only see/modify their own profile within their org.
-- users.org_id is a direct FK to orgs — no junction table exists.
CREATE POLICY "users_own_voice_profile_select" ON voice_profiles
    FOR SELECT USING (
        user_id = auth.uid()
        AND org_id = (SELECT org_id FROM users WHERE id = auth.uid())
    );
CREATE POLICY "users_own_voice_profile_insert" ON voice_profiles
    FOR INSERT WITH CHECK (
        user_id = auth.uid()
        AND org_id = (SELECT org_id FROM users WHERE id = auth.uid())
    );
CREATE POLICY "users_own_voice_profile_update" ON voice_profiles
    FOR UPDATE USING (
        user_id = auth.uid()
        AND org_id = (SELECT org_id FROM users WHERE id = auth.uid())
    );
CREATE POLICY "users_own_voice_profile_delete" ON voice_profiles
    FOR DELETE USING (
        user_id = auth.uid()
        AND org_id = (SELECT org_id FROM users WHERE id = auth.uid())
    );

-- ── rooms ───────────────────────────────────────────────────────────────────
CREATE POLICY "service_role_all_rooms" ON rooms
    FOR ALL USING (auth.role() = 'service_role');

-- Org-scoped read/write access
CREATE POLICY "org_rooms_select" ON rooms
    FOR SELECT USING (
        org_id = (SELECT org_id FROM users WHERE id = auth.uid())
    );
CREATE POLICY "org_rooms_insert" ON rooms
    FOR INSERT WITH CHECK (
        org_id = (SELECT org_id FROM users WHERE id = auth.uid())
    );
CREATE POLICY "org_rooms_update" ON rooms
    FOR UPDATE USING (
        org_id = (SELECT org_id FROM users WHERE id = auth.uid())
    );

-- ── enriched_transcripts ────────────────────────────────────────────────────
CREATE POLICY "service_role_all_enriched_transcripts" ON enriched_transcripts
    FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "org_enriched_transcripts_select" ON enriched_transcripts
    FOR SELECT USING (
        org_id = (SELECT org_id FROM users WHERE id = auth.uid())
    );

-- ── action_items ────────────────────────────────────────────────────────────
CREATE POLICY "service_role_all_action_items" ON action_items
    FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "org_action_items_select" ON action_items
    FOR SELECT USING (
        org_id = (SELECT org_id FROM users WHERE id = auth.uid())
    );
CREATE POLICY "org_action_items_insert" ON action_items
    FOR INSERT WITH CHECK (
        org_id = (SELECT org_id FROM users WHERE id = auth.uid())
    );
CREATE POLICY "org_action_items_update" ON action_items
    FOR UPDATE USING (
        org_id = (SELECT org_id FROM users WHERE id = auth.uid())
    );

-- ── speaker_analysis_audit ────────────────────────────────────────────────
-- Tracks mapping quality for each room's post-session analysis.
-- Needs service role for writes (written by post_session_analyzer).
-- Read access is org-scoped so users can see quality flags.
ALTER TABLE speaker_analysis_audit ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all_speaker_analysis_audit" ON speaker_analysis_audit
    FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "org_speaker_analysis_audit_select" ON speaker_analysis_audit
    FOR SELECT USING (
        org_id = (SELECT org_id FROM users WHERE id = auth.uid())
    );
