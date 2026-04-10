-- T04: Scoped Service Roles
-- Creates service-specific database roles with least-privilege access
-- Created: Phase 1 Foundation

-- ============================================
-- DROP EXISTING ROLES (for clean migration)
-- ============================================
DROP ROLE IF EXISTS cassandra_role;
DROP ROLE IF EXISTS backend_role;

-- ============================================
-- CASSANDRA_ROLE: AI Service Account
-- For: Cassandra AI agent service
-- Permissions: Read and create tickets/memory mappings
-- No DELETE permission (soft-delete only)
-- ============================================
CREATE ROLE cassandra_role NOLOGIN;

-- Grant schema usage
GRANT USAGE ON SCHEMA public TO cassandra_role;

-- Grant SELECT on all tables (read access)
GRANT SELECT ON ALL TABLES IN SCHEMA public TO cassandra_role;

-- Grant INSERT on tickets (create tickets)
GRANT INSERT ON tickets TO cassandra_role;
GRANT USAGE ON SEQUENCE tickets_id_seq TO cassandra_role;

-- Grant INSERT on memory_ticket_map (create mappings)
GRANT INSERT ON memory_ticket_map TO cassandra_role;
GRANT USAGE ON SEQUENCE memory_ticket_map_id_seq TO cassandra_role;

-- Grant UPDATE on tickets (update status, assignments)
GRANT UPDATE (status, assigned_to, updated_at) ON tickets TO cassandra_role;

-- Grant UPDATE on memory_ticket_map (update confidence scores)
GRANT UPDATE (confidence_score) ON memory_ticket_map TO cassandra_role;

-- Explicitly REVOKE DELETE (soft-delete enforcement)
REVOKE DELETE ON tickets FROM cassandra_role;
REVOKE DELETE ON memory_ticket_map FROM cassandra_role;
REVOKE DELETE ON orgs FROM cassandra_role;
REVOKE DELETE ON users FROM cassandra_role;

-- ============================================
-- BACKEND_ROLE: API Backend Service
-- For: FastAPI backend service
-- Permissions: Full CRUD except DELETE (soft-delete only)
-- ============================================
CREATE ROLE backend_role NOLOGIN;

-- Grant schema usage
GRANT USAGE ON SCHEMA public TO backend_role;

-- Grant ALL on tickets (full access except DELETE which is revoked below)
GRANT ALL ON tickets TO backend_role;
GRANT USAGE ON SEQUENCE tickets_id_seq TO backend_role;

-- Grant ALL on memory_ticket_map
GRANT ALL ON memory_ticket_map TO backend_role;
GRANT USAGE ON SEQUENCE memory_ticket_map_id_seq TO backend_role;

-- Grant ALL on orgs (for org management)
GRANT ALL ON orgs TO backend_role;
GRANT USAGE ON SEQUENCE orgs_id_seq TO backend_role;

-- Grant ALL on users (for user management)
GRANT ALL ON users TO backend_role;
GRANT USAGE ON SEQUENCE users_id_seq TO backend_role;

-- Grant access to archive table (T08)
GRANT ALL ON memory_archive TO backend_role;
GRANT USAGE ON SEQUENCE memory_archive_id_seq TO backend_role;

-- Grant access to helper functions
GRANT EXECUTE ON FUNCTION soft_delete_ticket TO backend_role;
GRANT EXECUTE ON FUNCTION restore_ticket TO backend_role;

-- Grant access to views
GRANT SELECT ON active_tickets TO backend_role;
GRANT SELECT ON deleted_tickets TO backend_role;

-- Explicitly REVOKE DELETE (soft-delete enforcement)
REVOKE DELETE ON tickets FROM backend_role;
REVOKE DELETE ON memory_ticket_map FROM backend_role;
REVOKE DELETE ON orgs FROM backend_role;
REVOKE DELETE ON users FROM backend_role;
REVOKE DELETE ON memory_archive FROM backend_role;

-- ============================================
-- ANALYTICS_ROLE: Read-Only Analytics
-- For: Analytics and reporting services
-- Permissions: Read-only access to all tables
-- ============================================
CREATE ROLE analytics_role NOLOGIN;

-- Grant schema usage
GRANT USAGE ON SCHEMA public TO analytics_role;

-- Grant SELECT on all tables (read-only)
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analytics_role;

-- Grant SELECT on views
GRANT SELECT ON active_tickets TO analytics_role;
GRANT SELECT ON deleted_tickets TO analytics_role;

-- ============================================
-- DEFAULT PRIVILEGES FOR FUTURE TABLES
-- ============================================

-- Set default privileges for cassandra_role
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO cassandra_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT INSERT ON TABLES TO cassandra_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT UPDATE ON TABLES TO cassandra_role;

-- Set default privileges for backend_role
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT ALL ON TABLES TO backend_role;

-- Set default privileges for analytics_role
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO analytics_role;

-- ============================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================
COMMENT ON ROLE cassandra_role IS 'AI service account: INSERT, SELECT on tickets/memory_map; UPDATE status/confidence; NO DELETE';
COMMENT ON ROLE backend_role IS 'Backend service account: ALL on tickets; REVOKE DELETE (soft-delete only)';
COMMENT ON ROLE analytics_role IS 'Analytics service account: SELECT only on all tables and views';
