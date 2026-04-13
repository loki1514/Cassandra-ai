-- T14: Stress Events
-- Session stress/sentiment detection
-- Created: Phase 2 Feature Tables

-- ============================================
-- STRESS_EVENTS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS stress_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    stress_level TEXT NOT NULL,
    flag_count INTEGER NOT NULL,
    avg_sentiment_score NUMERIC,
    flags JSONB,
    context_notes TEXT[],
    detected_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_stress_level CHECK (stress_level IN ('high', 'moderate', 'low'))
);

CREATE INDEX IF NOT EXISTS idx_stress_events_org ON stress_events(org_id);
CREATE INDEX IF NOT EXISTS idx_stress_events_session ON stress_events(session_id);
CREATE INDEX IF NOT EXISTS idx_stress_events_detected ON stress_events(org_id, detected_at DESC);

COMMENT ON TABLE stress_events IS 'Session stress/sentiment analysis events';
