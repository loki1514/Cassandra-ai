# Ticket Schema Updates - Ticketing Flow Implementation

## Summary
Updated the tickets table schema and related code to implement the complete ticketing workflow with 8 status states.

## Changes Made

### 1. Database Schema (Migration 001_b_alter_tickets_schema.sql)
✅ **Migration ran successfully**

**New Columns Added:**
- `priority` (TEXT, default: 'medium') - Ticket priority level
- `category` (TEXT) - Ticket categorization
- `paused_reason` (TEXT) - Reason for pausing a ticket
- `resolved_at` (TIMESTAMPTZ) - Timestamp when ticket was resolved
- `closed_at` (TIMESTAMPTZ) - Timestamp when ticket was closed

**Updated Constraints:**
- Status constraint updated to include all 8 ticketing flow statuses
- Priority constraint added: low, medium, high, urgent

**New Indexes:**
- `idx_tickets_priority` - For priority-based queries

### 2. Ticketing Flow Status Values

| Status | Meaning | Usage |
|--------|---------|-------|
| `open` | Initial tenant submission (REQUESTED) | Default status for new tickets |
| `waitlist` | In department queue | Tickets waiting to be assigned |
| `assigned` | MST self-assigned | Ticket has been claimed by MST |
| `in_progress` | MST actively working (WORK_STARTED) | Work has begun |
| `paused` | Explicitly paused with reason | Temporary hold (can return to in_progress) |
| `pending_validation` | Awaiting tenant approval/validation | Work complete, awaiting confirmation |
| `resolved` | Tenant-approved completion | Tenant has approved the work |
| `closed` | Admin-closed | Final state, ticket is closed |

**Status Flow:**
```
open → waitlist → assigned → in_progress → pending_validation → resolved → closed
                               ↓              ↑
                             paused --------→
```

### 3. Code Updates

#### cassandra/tools/create_ticket.py

**TicketStatus Enum Updated:**
- Replaced old statuses (ACTIVE, PENDING, ON_HOLD)
- Added all 8 new status values with documentation
- New tickets now default to `OPEN` status instead of `ACTIVE`

**CreateTicketInput Model:**
- Added `category` field for ticket categorization
- Updated to support new ticketing flow

**Database INSERT Statement:**
- Simplified to match actual tickets table schema
- Uses column name `id` instead of `ticket_id`
- Uses `assigned_to` instead of `assignee_id`
- Removed fields that don't exist in the tickets table
- Default status set to `TicketStatus.OPEN`

**TicketPriority Enum:**
- Still includes: LOW, MEDIUM, HIGH, URGENT, CRITICAL
- Aligns with database constraint (low, medium, high, urgent)

## Files Modified

1. ✅ [supabase/migrations/001_initial_schema.sql](supabase/migrations/001_initial_schema.sql)
   - Updated Statement 6: tickets table with new columns and constraints
   - Updated Statement 7: added priority index

2. ✅ [supabase/migrations/001_b_alter_tickets_schema.sql](supabase/migrations/001_b_alter_tickets_schema.sql)
   - NEW: ALTER migration to update existing tickets table
   - Adds all new columns safely
   - Updates constraints

3. ✅ [cassandra/tools/create_ticket.py](cassandra/tools/create_ticket.py)
   - TicketStatus enum updated
   - Added category field
   - Updated INSERT statement
   - Changed default status from ACTIVE to OPEN

## Next Steps

### Recommended Actions:

1. **Update Ticket Status Transition Logic**
   - Create validation for allowed status transitions
   - Example: `open` → `waitlist`, but not `open` → `resolved`

2. **Update Frontend Components**
   - Search for components displaying ticket status
   - Update status filters and displays to use new values
   - Update status badges/colors for each status

3. **Create Status Update Endpoints**
   - `/tickets/{id}/waitlist` - Move to waitlist
   - `/tickets/{id}/assign` - Assign to MST
   - `/tickets/{id}/start` - Start work (in_progress)
   - `/tickets/{id}/pause` - Pause with reason
   - `/tickets/{id}/resume` - Resume from pause
   - `/tickets/{id}/submit-validation` - Submit for validation
   - `/tickets/{id}/resolve` - Mark as resolved
   - `/tickets/{id}/close` - Close ticket

4. **Update Timestamp Logic**
   - Auto-populate `resolved_at` when status changes to 'resolved'
   - Auto-populate `closed_at` when status changes to 'closed'

5. **Add Paused Reason Validation**
   - Require `paused_reason` when status is set to 'paused'
   - Clear `paused_reason` when resuming

6. **Testing**
   - Test ticket creation with new schema
   - Test all status transitions
   - Test queries with new indexes

## Verification Commands

```bash
# Check ticket table schema
psql $DATABASE_URL -c "\d tickets"

# Verify constraints
psql $DATABASE_URL -c "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'tickets'::regclass;"

# Test creating a ticket
python -c "from cassandra.tools.create_ticket import TicketStatus; print([s.value for s in TicketStatus])"
```

## Migration History

- `001_initial_schema.sql` - Initial schema with updated tickets table definition
- `001_b_alter_tickets_schema.sql` - ✅ **Applied successfully** - Alters existing table
