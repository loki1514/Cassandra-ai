-- T13: Purchase Orders, Sensor Events, Energy Readings
-- IoT, ERP, and operational events
-- Created: Phase 2 Feature Tables

-- ============================================
-- PURCHASE_ORDERS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS purchase_orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    po_number TEXT NOT NULL UNIQUE,
    ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    vendor_id UUID REFERENCES vendors(id) ON DELETE SET NULL,
    amount NUMERIC,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'pending_approval',
    issued_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_po_status CHECK (status IN ('pending_approval', 'approved', 'rejected', 'issued'))
);

CREATE INDEX IF NOT EXISTS idx_purchase_orders_org ON purchase_orders(org_id);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_ticket ON purchase_orders(ticket_id);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_number ON purchase_orders(po_number);

-- ============================================
-- SENSOR_EVENTS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS sensor_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    sensor_id TEXT NOT NULL,
    property_id UUID REFERENCES properties(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    reading NUMERIC,
    threshold NUMERIC,
    severity TEXT,
    metadata JSONB,
    context_summary JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sensor_events_org ON sensor_events(org_id);
CREATE INDEX IF NOT EXISTS idx_sensor_events_sensor ON sensor_events(org_id, sensor_id);
CREATE INDEX IF NOT EXISTS idx_sensor_events_property ON sensor_events(org_id, property_id);
CREATE INDEX IF NOT EXISTS idx_sensor_events_created ON sensor_events(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sensor_events_type_severity ON sensor_events(org_id, event_type, severity);

-- ============================================
-- ENERGY_READINGS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS energy_readings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    sensor_id TEXT NOT NULL,
    property_id UUID REFERENCES properties(id) ON DELETE SET NULL,
    reading NUMERIC NOT NULL,
    unit TEXT DEFAULT 'unknown',
    threshold NUMERIC,
    metadata JSONB,
    captured_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_energy_readings_org_sensor ON energy_readings(org_id, sensor_id);
CREATE INDEX IF NOT EXISTS idx_energy_readings_property ON energy_readings(org_id, property_id);
CREATE INDEX IF NOT EXISTS idx_energy_readings_captured ON energy_readings(org_id, captured_at DESC);

-- updated_at trigger for purchase_orders
DROP TRIGGER IF EXISTS trigger_purchase_orders_updated_at ON purchase_orders;
CREATE TRIGGER trigger_purchase_orders_updated_at
    BEFORE UPDATE ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE purchase_orders IS 'Purchase orders linked to tickets';
COMMENT ON TABLE sensor_events IS 'IoT sensor threshold exceeded events';
COMMENT ON TABLE energy_readings IS 'Energy/sensor reading time-series data';
