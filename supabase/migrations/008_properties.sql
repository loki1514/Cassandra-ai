-- T08: Properties & Locations
-- Real estate / facility management core tables
-- Created: Phase 2 Feature Tables

-- ============================================
-- PROPERTIES TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS properties (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT,
    sqft NUMERIC,
    city TEXT,
    state TEXT,
    address TEXT,
    monthly_revenue NUMERIC,
    occupancy_rate NUMERIC,
    lat DOUBLE PRECISION,
    lng DOUBLE PRECISION,
    manager_id UUID REFERENCES users(id) ON DELETE SET NULL,
    site_director_id UUID REFERENCES users(id) ON DELETE SET NULL,
    emergency_contact TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_occupancy_rate CHECK (occupancy_rate >= 0 AND occupancy_rate <= 1)
);

CREATE INDEX IF NOT EXISTS idx_properties_org_id ON properties(org_id);
CREATE INDEX IF NOT EXISTS idx_properties_type ON properties(org_id, type);
CREATE INDEX IF NOT EXISTS idx_properties_city ON properties(org_id, city);
CREATE INDEX IF NOT EXISTS idx_properties_manager ON properties(manager_id);

-- ============================================
-- LOCATIONS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS locations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    address TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_locations_org_property ON locations(org_id, property_id);
CREATE INDEX IF NOT EXISTS idx_locations_coords ON locations(org_id, lat, lng);

-- updated_at trigger
DROP TRIGGER IF EXISTS trigger_properties_updated_at ON properties;
CREATE TRIGGER trigger_properties_updated_at
    BEFORE UPDATE ON properties
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE properties IS 'Real estate properties managed by the organization';
COMMENT ON TABLE locations IS 'Location/coordinates data for properties';
