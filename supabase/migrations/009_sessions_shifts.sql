-- T09: Sessions, Shifts, Shift Handovers, Arrival Log
-- Operational workforce management tables
-- Created: Phase 2 Feature Tables

-- ============================================
-- SESSIONS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active',
    auto_started BOOLEAN DEFAULT FALSE,
    trigger TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_session_status CHECK (status IN ('active', 'completed', 'abandoned'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_org_user ON sessions(org_id, user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_org_property ON sessions(org_id, property_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(org_id, status);

-- ============================================
-- SHIFTS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS shifts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    property_id UUID REFERENCES properties(id) ON DELETE SET NULL,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    shift_type TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shifts_org_user ON shifts(org_id, user_id);
CREATE INDEX IF NOT EXISTS idx_shifts_end_time ON shifts(org_id, user_id, end_time DESC);

-- ============================================
-- SHIFT_HANDOFFS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS shift_handovers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    outgoing_user UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    incoming_user UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    brief JSONB NOT NULL,
    notion_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_handovers_org ON shift_handovers(org_id);
CREATE INDEX IF NOT EXISTS idx_handovers_users ON shift_handovers(org_id, outgoing_user, incoming_user);

-- ============================================
-- ARRIVAL_LOG TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS arrival_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    lat DOUBLE PRECISION,
    lng DOUBLE PRECISION,
    arrived_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_arrival_org_property ON arrival_log(org_id, property_id);
CREATE INDEX IF NOT EXISTS idx_arrival_user ON arrival_log(org_id, user_id);
CREATE INDEX IF NOT EXISTS idx_arrival_time ON arrival_log(org_id, arrived_at DESC);

COMMENT ON TABLE sessions IS 'Property visit / geofence entry sessions';
COMMENT ON TABLE shifts IS 'Staff shift schedules';
COMMENT ON TABLE shift_handovers IS 'Shift handoff briefings between staff';
COMMENT ON TABLE arrival_log IS 'Staff arrival check-in log';
