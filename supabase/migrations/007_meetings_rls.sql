-- T07: Meetings/Transcripts Multi-Tenancy Columns + Indexes
-- ============================================
-- Note: RLS intentionally omitted — org isolation enforced at application layer.
-- org_id columns are added for data modeling correctness; application code
-- is responsible for scoping every query to the current user's org.

-- ============================================
-- CREATE SPEAKER_EMBEDDINGS TABLE (not yet defined anywhere)
-- ============================================

CREATE TABLE IF NOT EXISTS speaker_embeddings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID REFERENCES orgs(id),
    meeting_id UUID,  -- FK added separately below to avoid forward-reference
    speaker_name TEXT,
    embedding vector(1536),
    enrollment_status TEXT DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Add FK constraint now that meetings table exists
DO $$
BEGIN
    ALTER TABLE speaker_embeddings ADD CONSTRAINT speaker_embeddings_meeting_id_fkey
        FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE SET NULL;
EXCEPTION WHEN undefined_table THEN
    RAISE NOTICE 'meetings table not yet created, FK constraint skipped';
END;
$$;

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

-- Add org_id to speaker_embeddings table (table was just created above)
ALTER TABLE IF EXISTS speaker_embeddings
ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES orgs(id);

-- ============================================
-- CREATE INDEXES FOR PERFORMANCE
-- ============================================

CREATE INDEX IF NOT EXISTS idx_meetings_org_id ON meetings(org_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_org_id ON transcripts(org_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_org_id ON artifacts(org_id);
CREATE INDEX IF NOT EXISTS idx_speaker_embeddings_org_id ON speaker_embeddings(org_id);

-- Composite indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_meetings_org_created ON meetings(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_transcripts_org_meeting ON transcripts(org_id, meeting_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_org_meeting ON artifacts(org_id, meeting_id);

-- ============================================
-- DISABLE RLS (belt-and-suspenders)
-- ============================================
-- Ensure RLS is OFF for all meeting-related tables.

ALTER TABLE IF EXISTS meetings DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS transcripts DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS artifacts DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS speaker_embeddings DISABLE ROW LEVEL SECURITY;

-- ============================================
-- GRANT PERMISSIONS TO SERVICE ROLES
-- ============================================

-- cassandra_role: AI service (read + write)
-- Note: all tables use uuid_generate_v4() — no sequences exist
GRANT SELECT, INSERT, UPDATE ON meetings TO cassandra_role;
GRANT SELECT, INSERT, UPDATE ON transcripts TO cassandra_role;
GRANT SELECT, INSERT, UPDATE ON artifacts TO cassandra_role;
GRANT SELECT, INSERT, UPDATE ON speaker_embeddings TO cassandra_role;

-- backend_role: full access
-- Note: all tables use uuid_generate_v4() — no sequences exist
GRANT ALL ON meetings TO backend_role;
GRANT ALL ON transcripts TO backend_role;
GRANT ALL ON artifacts TO backend_role;
GRANT ALL ON speaker_embeddings TO backend_role;

-- analytics_role: read-only
GRANT SELECT ON meetings TO analytics_role;
GRANT SELECT ON transcripts TO analytics_role;
GRANT SELECT ON artifacts TO analytics_role;
GRANT SELECT ON speaker_embeddings TO analytics_role;

-- ============================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================
COMMENT ON TABLE meetings IS 'Meeting records with org_id for application-layer multi-tenancy (no RLS)';
COMMENT ON TABLE transcripts IS 'Transcript chunks with org_id for application-layer multi-tenancy (no RLS)';
COMMENT ON TABLE artifacts IS 'Extracted artifacts with org_id for application-layer multi-tenancy (no RLS)';
COMMENT ON TABLE speaker_embeddings IS 'Speaker embeddings with org_id for application-layer multi-tenancy (no RLS)';

-- ============================================
-- VERIFICATION QUERY
-- ============================================
/*
-- Run to verify org_id columns exist and RLS is disabled:
SELECT
    schemaname,
    tablename,
    rowsecurity
FROM pg_tables
WHERE tablename IN ('meetings', 'transcripts', 'artifacts', 'speaker_embeddings')
AND schemaname = 'public';
*/
