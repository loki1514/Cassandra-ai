-- T01: Supabase Project Bootstrap & Schema Init
-- Initial schema for Cassandra AI project
-- Created: Phase 1 Foundation
-- Rebuilt: Fully idempotent — safe to re-run even if old tables exist

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- ORGANIZATIONS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS orgs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orgs_created_at ON orgs(created_at);

-- ============================================
-- USERS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    email TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Role constraint (idempotent)
ALTER TABLE users DROP CONSTRAINT IF EXISTS chk_user_role;
ALTER TABLE users ADD CONSTRAINT chk_user_role CHECK (role IN ('admin', 'member', 'viewer'));

CREATE INDEX IF NOT EXISTS idx_users_org_id ON users(org_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- ============================================
-- TICKETS TABLE (Idempotent Rebuild)
-- ============================================
-- Step 1: Create base table if it doesn't exist.
-- If an old tickets table already exists, this is a no-op and we backfill below.
CREATE TABLE IF NOT EXISTS tickets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT DEFAULT 'medium',
    category TEXT,
    assigned_to UUID REFERENCES users(id) ON DELETE SET NULL,
    paused_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,

    CONSTRAINT chk_ticket_status CHECK (status IN ('open', 'waitlist', 'assigned', 'in_progress', 'paused', 'pending_validation', 'resolved', 'closed')),
    CONSTRAINT chk_ticket_priority CHECK (priority IN ('low', 'medium', 'high', 'urgent'))
);

-- Step 2: Backfill any missing columns for pre-existing old tables
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS priority TEXT DEFAULT 'medium';
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS assigned_to UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS paused_reason TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;

-- Step 3: Ensure constraints are current (drop then recreate)
ALTER TABLE tickets DROP CONSTRAINT IF EXISTS chk_ticket_status;
ALTER TABLE tickets ADD CONSTRAINT chk_ticket_status
    CHECK (status IN ('open', 'waitlist', 'assigned', 'in_progress', 'paused', 'pending_validation', 'resolved', 'closed'));

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS chk_ticket_priority;
ALTER TABLE tickets ADD CONSTRAINT chk_ticket_priority
    CHECK (priority IN ('low', 'medium', 'high', 'urgent'));

-- Step 4: Ensure indexes exist
CREATE INDEX IF NOT EXISTS idx_tickets_org_id ON tickets(org_id);
CREATE INDEX IF NOT EXISTS idx_tickets_assigned_to ON tickets(assigned_to);
CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON tickets(created_at);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_org_status ON tickets(org_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_priority ON tickets(priority);

-- ============================================
-- AUTO-UPDATE TRIGGER FOR updated_at
-- ============================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_users_updated_at ON users;
CREATE TRIGGER trigger_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trigger_tickets_updated_at ON tickets;
CREATE TRIGGER trigger_tickets_updated_at
    BEFORE UPDATE ON tickets
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================
COMMENT ON TABLE orgs IS 'Organizations/tenants in the multi-tenant system';
COMMENT ON TABLE users IS 'Users belonging to organizations';
COMMENT ON TABLE tickets IS 'Support tickets within organizations';
COMMENT ON COLUMN tickets.status IS 'Ticket status flow: open → waitlist → assigned → in_progress → paused → pending_validation → resolved → closed';
