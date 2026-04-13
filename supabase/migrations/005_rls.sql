-- T05: App-Level Org Isolation Helpers
-- Created: Phase 1 Foundation
-- Note: RLS intentionally omitted — org isolation is enforced at the application layer
-- via org_id scoping in every query. See T01 for table definitions.

-- ============================================
-- HELPER FUNCTION: Get Current User's Org ID
-- ============================================
-- Kept for application code use (e.g., FastAPI middleware, Supabase client helpers).
-- NOT used by RLS (RLS is disabled in this project).
-- Application code must call this and inject org_id into every query.

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
-- HELPER FUNCTION: Validate Org Access
-- ============================================
-- Returns true if the given org_id matches the current user's org.
-- Use this in application code before executing queries.

CREATE OR REPLACE FUNCTION validate_org_access(requested_org_id UUID)
RETURNS BOOLEAN AS $$
DECLARE
    current_org UUID;
BEGIN
    current_org := get_current_org_id();
    RETURN current_org IS NOT NULL AND current_org = requested_org_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================
-- DISABLE RLS (belt-and-suspenders)
-- ============================================
-- Ensure RLS is OFF for all tables. If RLS was previously enabled,
-- these statements ensure it is disabled.

ALTER TABLE IF EXISTS tickets DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS memory_ticket_map DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS users DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS orgs DISABLE ROW LEVEL SECURITY;
-- memory_archive RLS disable moved to T06 (table created there)

-- ============================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================
COMMENT ON FUNCTION get_current_org_id IS 'Extracts org_id from JWT claims for application-layer use (no RLS)';
COMMENT ON FUNCTION validate_org_access IS 'Validates org access in application code (no RLS)';
