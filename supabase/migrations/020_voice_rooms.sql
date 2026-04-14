-- T20: Room-Based Voice Diarization Schema
-- Production architecture for property-scoped ephemeral rooms
-- with org-scoped voice enrollment and post-session speaker identification.

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
CREATE INDEX idx_voice_profiles_embedding ON voice_profiles USING ivfflat (embedding vector_cosine_ops);

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
    status TEXT NOT NULL DEFAULT 'waiting',

    -- Participants enrolled in this room
    participants JSONB NOT NULL DEFAULT '[]',

    -- Session linkage
    active_session_id TEXT,
    session_ids TEXT[] DEFAULT '{}',

    -- Analysis state
    analysis_status TEXT DEFAULT 'pending',
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

-- ============================================
-- 3. ENRICHED TRANSCRIPTS (post-session speaker-identified transcript)
-- ============================================
CREATE TABLE IF NOT EXISTS enriched_transcripts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    session_id TEXT,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,

    -- Speaker identification
    speaker_label TEXT NOT NULL,
    speaker_name TEXT NOT NULL,
    speaker_user_id UUID,
    confidence FLOAT NOT NULL,

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
    status TEXT DEFAULT 'open',

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
