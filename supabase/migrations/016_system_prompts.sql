-- T16: Org Settings, System Prompts, AB Tests
-- Configuration and experimentation tables
-- Created: Phase 2 Feature Tables

-- ============================================
-- ORG_SETTINGS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS org_settings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    setting_key TEXT NOT NULL,
    setting_value TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_org_setting_key UNIQUE (org_id, setting_key)
);

CREATE INDEX IF NOT EXISTS idx_org_settings_org ON org_settings(org_id);

-- ============================================
-- SYSTEM_PROMPTS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS system_prompts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    prompt_name TEXT NOT NULL UNIQUE,
    prompt_text TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    source TEXT,
    status TEXT DEFAULT 'draft',
    promoted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_prompt_status CHECK (status IN ('draft', 'active', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_system_prompts_name ON system_prompts(prompt_name);
CREATE INDEX IF NOT EXISTS idx_system_prompts_status ON system_prompts(status);

-- ============================================
-- AB_TESTS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS ab_tests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    test_name TEXT NOT NULL UNIQUE,
    control_prompt TEXT NOT NULL,
    variant_prompt TEXT NOT NULL,
    queries_routed INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    winner TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_ab_test_status CHECK (status IN ('running', 'completed', 'paused')),
    CONSTRAINT chk_winner CHECK (winner IS NULL OR winner IN ('control', 'variant'))
);

CREATE INDEX IF NOT EXISTS idx_ab_tests_name ON ab_tests(test_name);
CREATE INDEX IF NOT EXISTS idx_ab_tests_status ON ab_tests(status);

-- updated_at triggers
DROP TRIGGER IF EXISTS trigger_org_settings_updated_at ON org_settings;
CREATE TRIGGER trigger_org_settings_updated_at
    BEFORE UPDATE ON org_settings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trigger_system_prompts_updated_at ON system_prompts;
CREATE TRIGGER trigger_system_prompts_updated_at
    BEFORE UPDATE ON system_prompts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trigger_ab_tests_updated_at ON ab_tests;
CREATE TRIGGER trigger_ab_tests_updated_at
    BEFORE UPDATE ON ab_tests
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE org_settings IS 'Organization-level configuration key-value store';
COMMENT ON TABLE system_prompts IS 'Versioned system prompt management';
COMMENT ON TABLE ab_tests IS 'Prompt A/B test configuration and tracking';
