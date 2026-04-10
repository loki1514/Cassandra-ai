-- T02: memory_ticket_map Table + Dual Indexes
-- Links memories to tickets with confidence scoring
-- Created: Phase 1 Foundation

-- ============================================
-- MEMORY_TICKET_MAP TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS memory_ticket_map (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    memory_id TEXT NOT NULL,
    ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    confidence_score REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Unique constraint to prevent duplicate memory-ticket mappings
    CONSTRAINT uq_memory_ticket UNIQUE (memory_id, ticket_id),
    
    -- Confidence score must be between 0 and 1
    CONSTRAINT chk_confidence_range CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0)
);

-- ============================================
-- DUAL INDEXES FOR EFFICIENT LOOKUPS
-- ============================================

-- Index for looking up tickets by memory_id (memory-centric queries)
-- Used when: "Find all tickets related to this memory"
CREATE INDEX IF NOT EXISTS idx_memory_lookup 
    ON memory_ticket_map(memory_id, org_id);

-- Index for looking up memories by ticket_id (ticket-centric queries)
-- Used when: "Find all memories related to this ticket"
CREATE INDEX IF NOT EXISTS idx_ticket_lookup 
    ON memory_ticket_map(ticket_id, org_id);

-- Additional indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_memory_map_org_id ON memory_ticket_map(org_id);
CREATE INDEX IF NOT EXISTS idx_memory_map_created_at ON memory_ticket_map(created_at);
CREATE INDEX IF NOT EXISTS idx_memory_map_confidence ON memory_ticket_map(confidence_score DESC);

-- Composite index for org-scoped confidence-based queries
CREATE INDEX IF NOT EXISTS idx_memory_map_org_confidence 
    ON memory_ticket_map(org_id, confidence_score DESC);

-- ============================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================
COMMENT ON TABLE memory_ticket_map IS 'Maps memories to tickets with confidence scores for retrieval';
COMMENT ON COLUMN memory_ticket_map.memory_id IS 'External memory identifier (from vector store)';
COMMENT ON COLUMN memory_ticket_map.confidence_score IS 'Similarity/confidence score between memory and ticket (0.0-1.0)';
