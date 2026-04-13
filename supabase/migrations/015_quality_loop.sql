-- T15: Entity Synonyms, Confidence Calibration, Answer Logs
-- AI quality loop and learning tables
-- Created: Phase 2 Feature Tables

-- ============================================
-- ENTITY_SYNONYMS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS entity_synonyms (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    canonical_term TEXT NOT NULL,
    alias TEXT NOT NULL,
    entity_type TEXT,
    confidence NUMERIC DEFAULT 1.0,
    source TEXT DEFAULT 'user_correction',
    learned_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_entity_synonyms UNIQUE (org_id, canonical_term, alias),
    CONSTRAINT chk_confidence_range CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS idx_entity_synonyms_org ON entity_synonyms(org_id);
CREATE INDEX IF NOT EXISTS idx_entity_synonyms_canonical ON entity_synonyms(org_id, canonical_term);
CREATE INDEX IF NOT EXISTS idx_entity_synonyms_alias ON entity_synonyms(org_id, alias);

-- ============================================
-- CONFIDENCE_CALIBRATION TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS confidence_calibration (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    predicted_confidence NUMERIC NOT NULL,
    outcome TEXT NOT NULL,
    query_type TEXT,
    confidence_bucket TEXT,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_predicted_confidence CHECK (predicted_confidence >= 0 AND predicted_confidence <= 1)
);

CREATE INDEX IF NOT EXISTS idx_confidence_calibration_org ON confidence_calibration(org_id);
CREATE INDEX IF NOT EXISTS idx_confidence_calibration_time ON confidence_calibration(org_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_confidence_calibration_query_type ON confidence_calibration(org_id, query_type);

-- ============================================
-- ANSWER_LOGS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS answer_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    query_type TEXT,
    query_text TEXT,
    answer_text TEXT,
    outcome TEXT,
    predicted_confidence NUMERIC,
    actual_accuracy NUMERIC,
    variant TEXT,
    ab_test_name TEXT,
    session_id TEXT,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_answer_logs_org ON answer_logs(org_id);
CREATE INDEX IF NOT EXISTS idx_answer_logs_time ON answer_logs(org_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_answer_logs_query_type ON answer_logs(org_id, query_type);
CREATE INDEX IF NOT EXISTS idx_answer_logs_ab_test ON answer_logs(ab_test_name) WHERE ab_test_name IS NOT NULL;

COMMENT ON TABLE entity_synonyms IS 'Learned entity aliases for query normalization';
COMMENT ON TABLE confidence_calibration IS 'AI confidence vs actual outcome tracking';
COMMENT ON TABLE answer_logs IS 'Full query/answer audit log with AB test tracking';
