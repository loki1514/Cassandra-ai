#!/bin/bash
# Script to push ticket schema updates to GitHub

echo "🔄 Pulling latest changes from origin/main..."
git pull origin main

echo "📝 Staging ticket schema updates..."
git add supabase/migrations/001_initial_schema.sql
git add supabase/migrations/001_b_alter_tickets_schema.sql
git add cassandra/tools/create_ticket.py
git add TICKET_SCHEMA_UPDATES.md

echo "📊 Checking what will be committed..."
git status

echo "💾 Creating commit..."
git commit -m "$(cat <<'EOF'
Update ticket schema with complete ticketing flow

- Add 8-status ticketing flow (open → waitlist → assigned → in_progress → paused → pending_validation → resolved → closed)
- Add new columns: priority, category, paused_reason, resolved_at, closed_at
- Update TicketStatus enum in create_ticket.py
- Change default status from 'active' to 'open'
- Add migration 001_b_alter_tickets_schema.sql for existing databases
- Add comprehensive documentation in TICKET_SCHEMA_UPDATES.md

Status mappings:
- open: Initial tenant submission (REQUESTED)
- waitlist: In department queue
- assigned: MST self-assigned
- in_progress: MST actively working (WORK_STARTED)
- paused: Explicitly paused with reason
- pending_validation: Awaiting tenant approval/validation
- resolved: Tenant-approved completion
- closed: Admin-closed

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"

echo "🚀 Pushing to GitHub..."
git push origin main

echo "✅ Done! Check https://github.com/loki1514/Cassandra-ai.git"
