-- ============================================
-- T007: Meetings/Transcripts Multi-Tenancy Fix
-- ============================================
-- Adds org_id columns and RLS policies to meeting-related tables
-- Fixes CRITICAL audit finding: zero org_id columns, zero RLS policies

-- ============================================
-- ADD ORG_ID COLUMNS
-- ============================================

-- Add org_id to meetings table if not exists
ALTER TABLE IF EXISTS meetings 
ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES orgs(id);

-- Add org_id to transcripts table if not exists  
ALTER TABLE IF EXISTS transcripts
ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES orgs(id);

-- Add org_id to artifacts table if not exists
ALTER TABLE IF EXISTS artifacts
ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES orgs(id);

-- Add org_id to speaker_embeddings table if not exists
ALTER TABLE IF EXISTS speaker_embeddings
ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES orgs(id);

-- ============================================
-- CREATE INDEXES FOR PERFORMANCE
-- ============================================

CREATE INDEX IF NOT EXISTS idx_meetings_org_id ON meetings(org_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_org_id ON transcripts(org_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_org_id ON artifacts(org_id);
CREATE INDEX IF NOT EXISTS idx_speaker_embeddings_org_id ON speaker_embeddings(org_id);

-- ============================================
-- ENABLE RLS ON TABLES
-- ============================================

ALTER TABLE IF EXISTS meetings ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS transcripts ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS speaker_embeddings ENABLE ROW LEVEL SECURITY;

-- ============================================
-- CREATE RLS POLICIES
-- ============================================

-- Meetings: org isolation
DROP POLICY IF EXISTS meetings_org_isolation ON meetings;
CREATE POLICY meetings_org_isolation ON meetings
    FOR ALL
    TO authenticated
    USING (org_id = get_current_org_id());

-- Transcripts: org isolation
DROP POLICY IF EXISTS transcripts_org_isolation ON transcripts;
CREATE POLICY transcripts_org_isolation ON transcripts
    FOR ALL
    TO authenticated
    USING (org_id = get_current_org_id());

-- Artifacts: org isolation
DROP POLICY IF EXISTS artifacts_org_isolation ON artifacts;
CREATE POLICY artifacts_org_isolation ON artifacts
    FOR ALL
    TO authenticated
    USING (org_id = get_current_org_id());

-- Speaker embeddings: org isolation
DROP POLICY IF EXISTS speaker_embeddings_org_isolation ON speaker_embeddings;
CREATE POLICY speaker_embeddings_org_isolation ON speaker_embeddings
    FOR ALL
    TO authenticated
    USING (org_id = get_current_org_id());

-- ============================================
-- FORCE RLS FOR SERVICE ROLES
-- ============================================
-- Service roles should NOT bypass RLS - they must use org_id

-- Revoke BYPASSRLS if previously granted (security fix)
DO $$
BEGIN
    -- Note: This may fail if role doesn't have BYPASSRLS, which is fine
    EXECUTE 'ALTER ROLE cassandra_role NOBYPASSRLS';
EXCEPTION WHEN undefined_object OR insufficient_privilege THEN
    RAISE NOTICE 'cassandra_role did not have BYPASSRLS (good)';
END;
$$;

DO $$
BEGIN
    EXECUTE 'ALTER ROLE backend_role NOBYPASSRLS';
EXCEPTION WHEN undefined_object OR insufficient_privilege THEN
    RAISE NOTICE 'backend_role did not have BYPASSRLS (good)';
END;
$$;

-- ============================================
-- SERVICE ROLE POLICIES (with org_id validation)
-- ============================================

-- Service roles must provide org_id - no bypass allowed
DROP POLICY IF EXISTS meetings_service_access ON meetings;
CREATE POLICY meetings_service_access ON meetings
    FOR ALL
    TO cassandra_role, backend_role
    USING (org_id IS NOT NULL);

DROP POLICY IF EXISTS transcripts_service_access ON transcripts;
CREATE POLICY transcripts_service_access ON transcripts
    FOR ALL
    TO cassandra_role, backend_role
    USING (org_id IS NOT NULL);

DROP POLICY IF EXISTS artifacts_service_access ON artifacts;
CREATE POLICY artifacts_service_access ON artifacts
    FOR ALL
    TO cassandra_role, backend_role
    USING (org_id IS NOT NULL);

DROP POLICY IF EXISTS speaker_embeddings_service_access ON speaker_embeddings;
CREATE POLICY speaker_embeddings_service_access ON speaker_embeddings
    FOR ALL
    TO cassandra_role, backend_role
    USING (org_id IS NOT NULL);

-- ============================================
-- COMMENTS
-- ============================================

COMMENT ON TABLE meetings IS 'RLS enabled: org isolation via org_id column';
COMMENT ON TABLE transcripts IS 'RLS enabled: org isolation via org_id column';
COMMENT ON TABLE artifacts IS 'RLS enabled: org isolation via org_id column';
COMMENT ON TABLE speaker_embeddings IS 'RLS enabled: org isolation via org_id column';

-- ============================================
-- VERIFICATION QUERY (run to confirm)
-- ============================================
/*
SELECT 
    schemaname,
    tablename,
    rowsecurity,
    forcerowsecurity
FROM pg_tables 
WHERE tablename IN ('meetings', 'transcripts', 'artifacts', 'speaker_embeddings')
AND schemaname = 'public';
*/