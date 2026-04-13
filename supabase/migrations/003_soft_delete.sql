-- T03: Soft-Delete Pattern
-- Implements soft-delete via status changes instead of physical deletion
-- Created: Phase 1 Foundation

-- ============================================
-- UPDATE STATUS CONSTRAINTS
-- ============================================

-- Tickets table already has status constraint from T01
-- Ensure it includes all required statuses for soft-delete pattern
ALTER TABLE tickets DROP CONSTRAINT IF EXISTS chk_ticket_status;
ALTER TABLE tickets ADD CONSTRAINT chk_ticket_status 
    CHECK (status IN ('open', 'in_progress', 'active', 'completed', 'cancelled', 'archived'));

-- ============================================
-- SOFT DELETE TRIGGER (Prevents Physical Deletion)
-- ============================================

-- Function to prevent physical deletion and suggest soft-delete
CREATE OR REPLACE FUNCTION prevent_physical_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Physical deletion is not allowed. Use soft-delete by updating status to cancelled or archived. Table: %, ID: %', 
        TG_TABLE_NAME, 
        OLD.id
        USING HINT = 'To soft-delete a ticket, update its status to cancelled or archived. ' ||
                     'To soft-delete a memory mapping, update the related ticket status or use the archive table.';
END;
$$ LANGUAGE plpgsql;

-- Apply prevent-delete trigger to tickets
DROP TRIGGER IF EXISTS trigger_prevent_ticket_delete ON tickets;
CREATE TRIGGER trigger_prevent_ticket_delete
    BEFORE DELETE ON tickets
    FOR EACH ROW
    EXECUTE FUNCTION prevent_physical_delete();

-- Apply prevent-delete trigger to memory_ticket_map
DROP TRIGGER IF EXISTS trigger_prevent_memory_map_delete ON memory_ticket_map;
CREATE TRIGGER trigger_prevent_memory_map_delete
    BEFORE DELETE ON memory_ticket_map
    FOR EACH ROW
    EXECUTE FUNCTION prevent_physical_delete();

-- Apply prevent-delete trigger to users
DROP TRIGGER IF EXISTS trigger_prevent_user_delete ON users;
CREATE TRIGGER trigger_prevent_user_delete
    BEFORE DELETE ON users
    FOR EACH ROW
    EXECUTE FUNCTION prevent_physical_delete();

-- ============================================
-- SOFT DELETE HELPER FUNCTIONS
-- ============================================

-- Function to soft-delete a ticket by ID
CREATE OR REPLACE FUNCTION soft_delete_ticket(ticket_uuid UUID, delete_reason TEXT DEFAULT 'cancelled')
RETURNS VOID AS $$
BEGIN
    -- Validate delete_reason
    IF delete_reason NOT IN ('cancelled', 'archived') THEN
        RAISE EXCEPTION 'Invalid delete_reason. Must be cancelled or archived.';
    END IF;
    
    UPDATE tickets 
    SET status = delete_reason,
        updated_at = NOW()
    WHERE id = ticket_uuid;
    
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Ticket with ID % not found', ticket_uuid;
    END IF;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to restore a soft-deleted ticket
CREATE OR REPLACE FUNCTION restore_ticket(ticket_uuid UUID, new_status TEXT DEFAULT 'open')
RETURNS VOID AS $$
BEGIN
    -- Validate new_status is not a deleted status
    IF new_status IN ('cancelled', 'archived') THEN
        RAISE EXCEPTION 'Cannot restore ticket to a deleted status. Use open, in_progress, or active.';
    END IF;
    
    UPDATE tickets 
    SET status = new_status,
        updated_at = NOW()
    WHERE id = ticket_uuid 
      AND status IN ('cancelled', 'archived');
    
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Ticket with ID % not found or not in deleted state', ticket_uuid;
    END IF;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================
-- VIEWS FOR CONVENIENT QUERYING
-- ============================================

-- View for active tickets only (excludes soft-deleted)
-- Idempotent: drop first so CREATE OR REPLACE works on re-run
DROP VIEW IF EXISTS active_tickets;
CREATE VIEW active_tickets AS
SELECT * FROM tickets 
WHERE status NOT IN ('cancelled', 'archived');

-- View for deleted tickets only
-- Idempotent: drop first so CREATE OR REPLACE works on re-run
DROP VIEW IF EXISTS deleted_tickets;
CREATE VIEW deleted_tickets AS
SELECT * FROM tickets 
WHERE status IN ('cancelled', 'archived');

-- ============================================
-- INDEXES FOR SOFT-DELETE QUERIES
-- ============================================

-- Ensure efficient filtering by status
CREATE INDEX IF NOT EXISTS idx_tickets_org_status ON tickets(org_id, status);

-- Index for finding deleted tickets (for cleanup/maintenance)
CREATE INDEX IF NOT EXISTS idx_tickets_deleted_at ON tickets(status, updated_at) 
    WHERE status IN ('cancelled', 'archived');

-- ============================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================
COMMENT ON FUNCTION soft_delete_ticket IS 'Soft-deletes a ticket by setting status to cancelled or archived';
COMMENT ON FUNCTION restore_ticket IS 'Restores a soft-deleted ticket to an active status';
COMMENT ON VIEW active_tickets IS 'Excludes tickets with status cancelled or archived';
COMMENT ON VIEW deleted_tickets IS 'Only includes tickets with status cancelled or archived';
