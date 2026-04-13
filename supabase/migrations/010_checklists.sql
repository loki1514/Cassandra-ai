-- T10: Checklists & Checklist Items
-- AR inspection and task checklists
-- Created: Phase 2 Feature Tables

-- ============================================
-- CHECKLISTS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS checklists (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    property_id UUID REFERENCES properties(id) ON DELETE SET NULL,
    asset_id UUID,
    name TEXT NOT NULL,
    type TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    total_items INTEGER NOT NULL DEFAULT 0,
    completed_items INTEGER NOT NULL DEFAULT 0,
    next_due_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_checklist_status CHECK (status IN ('active', 'completed', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_checklists_org_property ON checklists(org_id, property_id);
CREATE INDEX IF NOT EXISTS idx_checklists_org_asset ON checklists(org_id, asset_id);
CREATE INDEX IF NOT EXISTS idx_checklists_status ON checklists(org_id, status);

-- ============================================
-- CHECKLIST_ITEMS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS checklist_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    checklist_id UUID NOT NULL REFERENCES checklists(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    property_id UUID REFERENCES properties(id) ON DELETE SET NULL,
    asset_id UUID,
    sequence INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    deadline TIMESTAMPTZ,
    estimated_days INTEGER,
    completed BOOLEAN DEFAULT FALSE,
    completed_at TIMESTAMPTZ,
    completed_by UUID REFERENCES users(id) ON DELETE SET NULL,
    completion_evidence TEXT,
    completion_method TEXT,
    reference TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_item_status CHECK (status IN ('pending', 'completed', 'skipped'))
);

CREATE INDEX IF NOT EXISTS idx_checklist_items_org_property ON checklist_items(org_id, property_id);
CREATE INDEX IF NOT EXISTS idx_checklist_items_checklist ON checklist_items(checklist_id);
CREATE INDEX IF NOT EXISTS idx_checklist_items_status_deadline ON checklist_items(org_id, status, deadline);

-- updated_at trigger for checklists
DROP TRIGGER IF EXISTS trigger_checklists_updated_at ON checklists;
CREATE TRIGGER trigger_checklists_updated_at
    BEFORE UPDATE ON checklists
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- updated_at trigger for checklist_items
DROP TRIGGER IF EXISTS trigger_checklist_items_updated_at ON checklist_items;
CREATE TRIGGER trigger_checklist_items_updated_at
    BEFORE UPDATE ON checklist_items
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE checklists IS 'AR inspection and task checklists';
COMMENT ON TABLE checklist_items IS 'Individual checklist items with AR completion tracking';
