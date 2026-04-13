-- T12: Budgets & Bids
-- Financial planning tables
-- Created: Phase 2 Feature Tables

-- ============================================
-- BUDGETS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS budgets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    property_id UUID REFERENCES properties(id) ON DELETE SET NULL,
    property_type TEXT,
    annual_opex NUMERIC NOT NULL,
    annual_capex NUMERIC DEFAULT 0,
    sqft NUMERIC,
    year INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_budgets_org ON budgets(org_id);
CREATE INDEX IF NOT EXISTS idx_budgets_org_type ON budgets(org_id, property_type);
CREATE INDEX IF NOT EXISTS idx_budgets_property ON budgets(property_id);

-- ============================================
-- BIDS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS bids (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    property_id UUID REFERENCES properties(id) ON DELETE SET NULL,
    property_type TEXT NOT NULL,
    city TEXT,
    bid_amount NUMERIC NOT NULL,
    status TEXT DEFAULT 'submitted',
    won BOOLEAN DEFAULT FALSE,
    submitted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_bid_status CHECK (status IN ('submitted', 'accepted', 'rejected', 'withdrawn'))
);

CREATE INDEX IF NOT EXISTS idx_bids_org ON bids(org_id);
CREATE INDEX IF NOT EXISTS idx_bids_org_type ON bids(org_id, property_type);

-- updated_at triggers
DROP TRIGGER IF EXISTS trigger_budgets_updated_at ON budgets;
CREATE TRIGGER trigger_budgets_updated_at
    BEFORE UPDATE ON budgets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trigger_bids_updated_at ON bids;
CREATE TRIGGER trigger_bids_updated_at
    BEFORE UPDATE ON bids
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE budgets IS 'Annual OPEX and CAPEX budgets per property';
COMMENT ON TABLE bids IS 'Bid tracking for RFPs and contracts';
