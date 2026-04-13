-- T11: Vendors, Vendor Rates, Contracts
-- Vendor management and contract tracking
-- Created: Phase 2 Feature Tables

-- ============================================
-- VENDORS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS vendors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    trade TEXT NOT NULL,
    city TEXT,
    state TEXT,
    rating NUMERIC(3, 2),
    contact_email TEXT,
    contact_name TEXT,
    phone TEXT,
    address TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_vendor_status CHECK (status IN ('active', 'inactive', 'blocked'))
);

CREATE INDEX IF NOT EXISTS idx_vendors_org ON vendors(org_id);
CREATE INDEX IF NOT EXISTS idx_vendors_trade_city ON vendors(org_id, trade, city);

-- ============================================
-- VENDOR_RATES TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS vendor_rates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    service TEXT NOT NULL,
    rate NUMERIC NOT NULL,
    unit TEXT,
    effective_date DATE NOT NULL,
    expiry_date DATE,
    city TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vendor_rates_org_service ON vendor_rates(org_id, service);
CREATE INDEX IF NOT EXISTS idx_vendor_rates_vendor ON vendor_rates(vendor_id);
CREATE INDEX IF NOT EXISTS idx_vendor_rates_effective ON vendor_rates(org_id, service, effective_date DESC);

-- ============================================
-- CONTRACTS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS contracts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    vendor_id UUID REFERENCES vendors(id) ON DELETE SET NULL,
    tenant_name TEXT,
    name TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    monthly_rent NUMERIC,
    sqft NUMERIC,
    start_date DATE,
    expiry_date DATE,
    contract_type TEXT,
    sla_terms JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_contract_status CHECK (status IN ('active', 'expired', 'terminated'))
);

CREATE INDEX IF NOT EXISTS idx_contracts_org ON contracts(org_id);
CREATE INDEX IF NOT EXISTS idx_contracts_expiry ON contracts(org_id, expiry_date) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_contracts_property ON contracts(property_id);

-- updated_at trigger for vendors
DROP TRIGGER IF EXISTS trigger_vendors_updated_at ON vendors;
CREATE TRIGGER trigger_vendors_updated_at
    BEFORE UPDATE ON vendors
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- updated_at trigger for contracts
DROP TRIGGER IF EXISTS trigger_contracts_updated_at ON contracts;
CREATE TRIGGER trigger_contracts_updated_at
    BEFORE UPDATE ON contracts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE vendors IS 'Vendor/supplier directory';
COMMENT ON TABLE vendor_rates IS 'Vendor service rate cards';
COMMENT ON TABLE contracts IS 'Lease, vendor, and service contracts';
