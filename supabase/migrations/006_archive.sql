-- T06: memory_archive Table + Archive Functions
-- Created: Phase 1 Foundation
-- Note: RLS intentionally omitted — org isolation enforced at application layer

-- ============================================
-- MEMORY_ARCHIVE TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS memory_archive (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT 'memory',
    original_data JSONB NOT NULL,
    archived_at TIMESTAMPTZ DEFAULT NOW(),
    archived_by UUID REFERENCES users(id) ON DELETE SET NULL,
    archive_reason TEXT,
    metadata JSONB DEFAULT '{}',

    CONSTRAINT chk_entity_type CHECK (entity_type IN ('memory', 'ticket', 'mapping', 'user', 'custom'))
);

-- Disable RLS for memory_archive (belt-and-suspenders)
ALTER TABLE IF EXISTS memory_archive DISABLE ROW LEVEL SECURITY;

-- ============================================
-- INDEXES FOR EFFICIENT LOOKUPS
-- ============================================

-- Primary index for org-scoped entity lookups
CREATE INDEX IF NOT EXISTS idx_memory_archive_org_entity
    ON memory_archive(org_id, entity_id);

-- Index for archive time-based queries
CREATE INDEX IF NOT EXISTS idx_memory_archive_archived_at
    ON memory_archive(archived_at DESC);

-- Index for entity type filtering
CREATE INDEX IF NOT EXISTS idx_memory_archive_entity_type
    ON memory_archive(entity_type);

-- Composite index for org + type queries
CREATE INDEX IF NOT EXISTS idx_memory_archive_org_type
    ON memory_archive(org_id, entity_type, archived_at DESC);

-- GIN index for JSONB metadata queries
CREATE INDEX IF NOT EXISTS idx_memory_archive_metadata
    ON memory_archive USING GIN (metadata);

-- GIN index for original_data queries
CREATE INDEX IF NOT EXISTS idx_memory_archive_original_data
    ON memory_archive USING GIN (original_data);

-- ============================================
-- ARCHIVE HELPER FUNCTIONS
-- ============================================

-- Function to archive a memory_ticket_map entry
CREATE OR REPLACE FUNCTION archive_memory_mapping(
    p_mapping_id UUID,
    p_archive_reason TEXT DEFAULT 'manual',
    p_archived_by UUID DEFAULT NULL
)
RETURNS UUID AS $$
DECLARE
    v_org_id UUID;
    v_memory_id TEXT;
    v_ticket_id UUID;
    v_confidence_score REAL;
    v_archive_id UUID;
BEGIN
    -- Get the mapping data
    SELECT org_id, memory_id, ticket_id, confidence_score
    INTO v_org_id, v_memory_id, v_ticket_id, v_confidence_score
    FROM memory_ticket_map
    WHERE id = p_mapping_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Memory mapping with ID % not found', p_mapping_id;
    END IF;

    -- Validate org access
    IF NOT validate_org_access(v_org_id) THEN
        RAISE EXCEPTION 'Access denied: org_id mismatch';
    END IF;

    -- Insert into archive
    INSERT INTO memory_archive (
        org_id,
        entity_id,
        entity_type,
        original_data,
        archived_by,
        archive_reason,
        metadata
    ) VALUES (
        v_org_id,
        p_mapping_id::TEXT,
        'mapping',
        jsonb_build_object(
            'id', p_mapping_id,
            'memory_id', v_memory_id,
            'ticket_id', v_ticket_id,
            'org_id', v_org_id,
            'confidence_score', v_confidence_score
        ),
        p_archived_by,
        p_archive_reason,
        jsonb_build_object(
            'archived_table', 'memory_ticket_map',
            'archived_at', NOW()
        )
    )
    RETURNING id INTO v_archive_id;

    RETURN v_archive_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to archive generic entity data
CREATE OR REPLACE FUNCTION archive_entity(
    p_org_id UUID,
    p_entity_id TEXT,
    p_entity_type TEXT,
    p_original_data JSONB,
    p_archive_reason TEXT DEFAULT 'manual',
    p_archived_by UUID DEFAULT NULL,
    p_metadata JSONB DEFAULT '{}'
)
RETURNS UUID AS $$
DECLARE
    v_archive_id UUID;
BEGIN
    -- Validate org access
    IF NOT validate_org_access(p_org_id) THEN
        RAISE EXCEPTION 'Access denied: org_id mismatch';
    END IF;

    -- Validate entity_type
    IF p_entity_type NOT IN ('memory', 'ticket', 'mapping', 'user', 'custom') THEN
        RAISE EXCEPTION 'Invalid entity_type: %. Must be one of: memory, ticket, mapping, user, custom', p_entity_type;
    END IF;

    INSERT INTO memory_archive (
        org_id,
        entity_id,
        entity_type,
        original_data,
        archived_by,
        archive_reason,
        metadata
    ) VALUES (
        p_org_id,
        p_entity_id,
        p_entity_type,
        p_original_data,
        p_archived_by,
        p_archive_reason,
        p_metadata || jsonb_build_object('archived_at', NOW())
    )
    RETURNING id INTO v_archive_id;

    RETURN v_archive_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to restore from archive (creates new record, does not delete archive)
CREATE OR REPLACE FUNCTION restore_from_archive(p_archive_id UUID)
RETURNS JSONB AS $$
DECLARE
    v_archive_record memory_archive%ROWTYPE;
    v_result JSONB;
BEGIN
    SELECT * INTO v_archive_record
    FROM memory_archive
    WHERE id = p_archive_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Archive record with ID % not found', p_archive_id;
    END IF;

    -- Validate org access
    IF NOT validate_org_access(v_archive_record.org_id) THEN
        RAISE EXCEPTION 'Access denied: org_id mismatch';
    END IF;

    -- Return the original data for restoration
    v_result := jsonb_build_object(
        'archive_id', p_archive_id,
        'entity_type', v_archive_record.entity_type,
        'entity_id', v_archive_record.entity_id,
        'original_data', v_archive_record.original_data,
        'archived_at', v_archive_record.archived_at,
        'restored_at', NOW()
    );

    RETURN v_result;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to cleanup old archives (for maintenance)
CREATE OR REPLACE FUNCTION cleanup_old_archives(
    p_org_id UUID,
    p_older_than_days INTEGER DEFAULT 90
)
RETURNS INTEGER AS $$
DECLARE
    v_deleted_count INTEGER;
BEGIN
    -- Validate org access
    IF NOT validate_org_access(p_org_id) THEN
        RAISE EXCEPTION 'Access denied: org_id mismatch';
    END IF;

    DELETE FROM memory_archive
    WHERE org_id = p_org_id
      AND archived_at < NOW() - (p_older_than_days || ' days')::INTERVAL;

    GET DIAGNOSTICS v_deleted_count = ROW_COUNT;

    RETURN v_deleted_count;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================
-- VIEWS FOR CONVENIENT QUERYING
-- ============================================

-- View for recent archives (last 30 days)
CREATE OR REPLACE VIEW recent_archives AS
SELECT * FROM memory_archive
WHERE archived_at > NOW() - INTERVAL '30 days';

-- View for archive statistics by org
CREATE OR REPLACE VIEW archive_stats_by_org AS
SELECT
    org_id,
    entity_type,
    COUNT(*) as archive_count,
    MIN(archived_at) as oldest_archive,
    MAX(archived_at) as newest_archive
FROM memory_archive
GROUP BY org_id, entity_type;

-- ============================================
-- GRANT PERMISSIONS TO SERVICE ROLES
-- ============================================

-- Grant access to backend_role
GRANT ALL ON memory_archive TO backend_role;
-- No sequence: memory_archive uses uuid_generate_v4()
GRANT EXECUTE ON FUNCTION archive_memory_mapping TO backend_role;
GRANT EXECUTE ON FUNCTION archive_entity TO backend_role;
GRANT EXECUTE ON FUNCTION restore_from_archive TO backend_role;
GRANT EXECUTE ON FUNCTION cleanup_old_archives TO backend_role;

-- Grant read access to analytics_role
GRANT SELECT ON memory_archive TO analytics_role;
GRANT SELECT ON recent_archives TO analytics_role;
GRANT SELECT ON archive_stats_by_org TO analytics_role;

-- ============================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================
COMMENT ON TABLE memory_archive IS 'Backup/archive table for storing historical data (no RLS)';
COMMENT ON COLUMN memory_archive.entity_id IS 'ID of the original entity that was archived';
COMMENT ON COLUMN memory_archive.entity_type IS 'Type of entity: memory, ticket, mapping, user, custom';
COMMENT ON COLUMN memory_archive.original_data IS 'Complete JSON snapshot of the archived entity';
COMMENT ON FUNCTION archive_memory_mapping IS 'Archives a memory_ticket_map entry before deletion';
COMMENT ON FUNCTION archive_entity IS 'Generic function to archive any entity';
COMMENT ON FUNCTION restore_from_archive IS 'Retrieves archived data for restoration';
COMMENT ON FUNCTION cleanup_old_archives IS 'Removes archives older than specified days';
