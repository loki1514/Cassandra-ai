-- T01b: Alter tickets table to add new columns and update status constraint
-- This migration updates the existing tickets table with new ticketing flow

-- Add new columns if they don't exist
DO $$
BEGIN
    -- Add priority column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='tickets' AND column_name='priority') THEN
        ALTER TABLE tickets ADD COLUMN priority TEXT DEFAULT 'medium';
    END IF;

    -- Add category column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='tickets' AND column_name='category') THEN
        ALTER TABLE tickets ADD COLUMN category TEXT;
    END IF;

    -- Add paused_reason column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='tickets' AND column_name='paused_reason') THEN
        ALTER TABLE tickets ADD COLUMN paused_reason TEXT;
    END IF;

    -- Add resolved_at column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='tickets' AND column_name='resolved_at') THEN
        ALTER TABLE tickets ADD COLUMN resolved_at TIMESTAMPTZ;
    END IF;

    -- Add closed_at column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name='tickets' AND column_name='closed_at') THEN
        ALTER TABLE tickets ADD COLUMN closed_at TIMESTAMPTZ;
    END IF;
END $$;

-- Drop old status constraint and create new one
ALTER TABLE tickets DROP CONSTRAINT IF EXISTS chk_ticket_status;
ALTER TABLE tickets ADD CONSTRAINT chk_ticket_status
    CHECK (status IN ('open', 'waitlist', 'assigned', 'in_progress', 'paused', 'pending_validation', 'resolved', 'closed'));

-- Add priority constraint
ALTER TABLE tickets DROP CONSTRAINT IF EXISTS chk_ticket_priority;
ALTER TABLE tickets ADD CONSTRAINT chk_ticket_priority
    CHECK (priority IN ('low', 'medium', 'high', 'urgent'));

-- Create new indexes if they don't exist
CREATE INDEX IF NOT EXISTS idx_tickets_priority ON tickets(priority);

-- Update comment on status column
COMMENT ON COLUMN tickets.status IS 'Ticket status flow: open → waitlist → assigned → in_progress → paused → pending_validation → resolved → closed';
