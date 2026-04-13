-- T17: Voice Queue
-- Asynchronous voice command processing queue
-- Created: Phase 2 Feature Tables

-- ============================================
-- VOICE_QUEUE TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS voice_queue (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    command_text TEXT,
    audio_data BYTEA,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    queued_at TIMESTAMPTZ NOT NULL,
    processed_at TIMESTAMPTZ,
    _context_sources TEXT[],
    _memory_hints TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_voice_queue_status CHECK (status IN ('pending', 'processed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_voice_queue_org_user_status ON voice_queue(org_id, user_id, status);
CREATE INDEX IF NOT EXISTS idx_voice_queue_pending ON voice_queue(org_id, status) WHERE status = 'pending';

COMMENT ON TABLE voice_queue IS 'Asynchronous voice command processing queue';
