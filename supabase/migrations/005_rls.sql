-- T05: Row-Level Security (RLS)
-- Implements organization-based data isolation
-- Created: Phase 1 Foundation

-- ============================================
-- ENABLE ROW LEVEL SECURITY
-- ============================================

-- Enable RLS on tickets table
ALTER TABLE tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE tickets FORCE ROW LEVEL SECURITY;

-- Enable RLS on memory_ticket_map table
ALTER TABLE memory_ticket_map ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_ticket_map FORCE ROW LEVEL SECURITY;

-- Enable RLS on users table
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;

-- Enable RLS on orgs table
ALTER TABLE orgs ENABLE ROW LEVEL SECURITY;
ALTER TABLE orgs FORCE ROW LEVEL SECURITY;

-- Enable RLS on memory_archive table (T08)
ALTER TABLE memory_archive ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_archive FORCE ROW LEVEL SECURITY;

-- ============================================
-- DROP EXISTING POLICIES (for clean migration)
-- ============================================
DROP POLICY IF EXISTS org_isolation_select ON tickets;
DROP POLICY IF EXISTS org_isolation_insert ON tickets;
DROP POLICY IF EXISTS org_isolation_update ON tickets;
DROP POLICY IF EXISTS org_isolation_delete ON tickets;

DROP POLICY IF EXISTS org_isolation_select ON memory_ticket_map;
DROP POLICY IF EXISTS org_isolation_insert ON memory_ticket_map;
DROP POLICY IF EXISTS org_isolation_update ON memory_ticket_map;
DROP POLICY IF EXISTS org_isolation_delete ON memory_ticket_map;

DROP POLICY IF EXISTS org_isolation_select ON users;
DROP POLICY IF EXISTS org_isolation_insert ON users;
DROP POLICY IF EXISTS org_isolation_update ON users;
DROP POLICY IF EXISTS org_isolation_delete ON users;

DROP POLICY IF EXISTS org_isolation_select ON orgs;
DROP POLICY IF EXISTS org_isolation_insert ON orgs;
DROP POLICY IF EXISTS org_isolation_update ON orgs;
DROP POLICY IF EXISTS org_isolation_delete ON orgs;

DROP POLICY IF EXISTS org_isolation_select ON memory_archive;
DROP POLICY IF EXISTS org_isolation_insert ON memory_archive;
DROP POLICY IF EXISTS org_isolation_update ON memory_archive;
DROP POLICY IF EXISTS org_isolation_delete ON memory_archive;

-- ============================================
-- HELPER FUNCTION: Get Current User's Org ID
-- ============================================

-- Function to extract org_id from JWT claims
CREATE OR REPLACE FUNCTION get_current_org_id()
RETURNS UUID AS $$
DECLARE
    org_id TEXT;
BEGIN
    -- Try to get org_id from JWT claim
    org_id := current_setting('request.jwt.claims', true)::json->>'org_id';
    
    -- If not in JWT, try auth.jwt() function (Supabase specific)
    IF org_id IS NULL OR org_id = '' THEN
        BEGIN
            org_id := auth.jwt()->>'org_id';
        EXCEPTION WHEN OTHERS THEN
            org_id := NULL;
        END;
    END IF;
    
    -- Return as UUID or NULL
    IF org_id IS NOT NULL AND org_id != '' THEN
        RETURN org_id::UUID;
    END IF;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================
-- TICKETS TABLE POLICIES
-- ============================================

-- SELECT: Users can only see tickets in their org
CREATE POLICY org_isolation_select ON tickets
    FOR SELECT
    USING (org_id = get_current_org_id());

-- INSERT: Users can only create tickets in their org
CREATE POLICY org_isolation_insert ON tickets
    FOR INSERT
    WITH CHECK (org_id = get_current_org_id());

-- UPDATE: Users can only update tickets in their org
CREATE POLICY org_isolation_update ON tickets
    FOR UPDATE
    USING (org_id = get_current_org_id())
    WITH CHECK (org_id = get_current_org_id());

-- DELETE: Blocked by soft-delete trigger, but policy for completeness
CREATE POLICY org_isolation_delete ON tickets
    FOR DELETE
    USING (org_id = get_current_org_id());

-- ============================================
-- MEMORY_TICKET_MAP TABLE POLICIES
-- ============================================

-- SELECT: Users can only see mappings in their org
CREATE POLICY org_isolation_select ON memory_ticket_map
    FOR SELECT
    USING (org_id = get_current_org_id());

-- INSERT: Users can only create mappings in their org
CREATE POLICY org_isolation_insert ON memory_ticket_map
    FOR INSERT
    WITH CHECK (org_id = get_current_org_id());

-- UPDATE: Users can only update mappings in their org
CREATE POLICY org_isolation_update ON memory_ticket_map
    FOR UPDATE
    USING (org_id = get_current_org_id())
    WITH CHECK (org_id = get_current_org_id());

-- DELETE: Blocked by soft-delete trigger, but policy for completeness
CREATE POLICY org_isolation_delete ON memory_ticket_map
    FOR DELETE
    USING (org_id = get_current_org_id());

-- ============================================
-- USERS TABLE POLICIES
-- ============================================

-- SELECT: Users can only see users in their org
CREATE POLICY org_isolation_select ON users
    FOR SELECT
    USING (org_id = get_current_org_id());

-- INSERT: Only admins can create users (handled by application logic)
CREATE POLICY org_isolation_insert ON users
    FOR INSERT
    WITH CHECK (org_id = get_current_org_id());

-- UPDATE: Users can only update users in their org
CREATE POLICY org_isolation_update ON users
    FOR UPDATE
    USING (org_id = get_current_org_id())
    WITH CHECK (org_id = get_current_org_id());

-- ============================================
-- ORGS TABLE POLICIES
-- ============================================

-- SELECT: Users can only see their own org
CREATE POLICY org_isolation_select ON orgs
    FOR SELECT
    USING (id = get_current_org_id());

-- INSERT: Restricted (handled by application logic)
CREATE POLICY org_isolation_insert ON orgs
    FOR INSERT
    WITH CHECK (false); -- Disable direct inserts, use application logic

-- UPDATE: Users can only update their own org
CREATE POLICY org_isolation_update ON orgs
    FOR UPDATE
    USING (id = get_current_org_id())
    WITH CHECK (id = get_current_org_id());

-- ============================================
-- MEMORY_ARCHIVE TABLE POLICIES (T08)
-- ============================================

-- SELECT: Users can only see archives in their org
CREATE POLICY org_isolation_select ON memory_archive
    FOR SELECT
    USING (org_id = get_current_org_id());

-- INSERT: Users can only create archives in their org
CREATE POLICY org_isolation_insert ON memory_archive
    FOR INSERT
    WITH CHECK (org_id = get_current_org_id());

-- UPDATE: Users can only update archives in their org
CREATE POLICY org_isolation_update ON memory_archive
    FOR UPDATE
    USING (org_id = get_current_org_id())
    WITH CHECK (org_id = get_current_org_id());

-- ============================================
-- BYPASS RLS FOR SERVICE ROLES
-- ============================================

-- Service roles bypass RLS (they use their own access controls)
-- This is configured via ALTER ROLE ... BYPASSRLS in PostgreSQL 15+
-- For Supabase, service roles typically bypass RLS by default

-- Grant bypass to service roles if supported
DO $$
BEGIN
    -- Attempt to grant bypass (may fail on older PostgreSQL versions)
    EXECUTE 'ALTER ROLE cassandra_role BYPASSRLS';
EXCEPTION WHEN insufficient_privilege OR undefined_object THEN
    RAISE NOTICE 'Could not grant BYPASSRLS to cassandra_role (insufficient privileges or not supported)';
END;
$$;

DO $$
BEGIN
    EXECUTE 'ALTER ROLE backend_role BYPASSRLS';
EXCEPTION WHEN insufficient_privilege OR undefined_object THEN
    RAISE NOTICE 'Could not grant BYPASSRLS to backend_role (insufficient privileges or not supported)';
END;
$$;

-- ============================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================
COMMENT ON FUNCTION get_current_org_id IS 'Extracts org_id from JWT claims for RLS policies';
COMMENT ON TABLE tickets IS 'RLS enabled: org isolation via get_current_org_id()';
COMMENT ON TABLE memory_ticket_map IS 'RLS enabled: org isolation via get_current_org_id()';
COMMENT ON TABLE users IS 'RLS enabled: org isolation via get_current_org_id()';
COMMENT ON TABLE orgs IS 'RLS enabled: org isolation via get_current_org_id()';
