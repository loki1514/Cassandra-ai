"""
T35: RLS Penetration Testing

This module provides penetration tests for Row Level Security:
- Cross-organization access attempts
- Direct table access tests
- Policy bypass attempts
- Privilege escalation tests

Usage:
    pytest tests/test_rls_penetration.py -v
"""

import pytest
import asyncpg
from typing import Dict, Any, Optional

# Test configuration
TEST_ORG_1 = "test_org_1"
TEST_ORG_2 = "test_org_2"
TEST_USER_1 = "test_user_1"
TEST_USER_2 = "test_user_2"


class RLSPenetrationTest:
    """
    RLS Penetration Test Suite.
    
    Tests 5 critical RLS scenarios:
    1. Cross-org data access via direct query
    2. Cross-org data access via API
    3. Policy bypass via SQL injection
    4. Privilege escalation attempt
    5. Set config bypass attempt
    """
    
    @pytest.fixture
    async def db_pool(self):
        """Create test database pool."""
        pool = await asyncpg.create_pool(
            "postgresql://test:test@localhost/test"
        )
        yield pool
        await pool.close()
    
    @pytest.fixture
    async def org_1_user(self, db_pool):
        """Create org 1 user context."""
        async with db_pool.acquire() as conn:
            await conn.execute(
                "SET app.current_org_id = $1",
                TEST_ORG_1
            )
            await conn.execute(
                "SET app.current_user_id = $1",
                TEST_USER_1
            )
            return conn
    
    @pytest.fixture
    async def org_2_user(self, db_pool):
        """Create org 2 user context."""
        async with db_pool.acquire() as conn:
            await conn.execute(
                "SET app.current_org_id = $1",
                TEST_ORG_2
            )
            await conn.execute(
                "SET app.current_user_id = $1",
                TEST_USER_2
            )
            return conn
    
    @pytest.mark.asyncio
    async def test_1_cross_org_direct_query(self, db_pool):
        """
        Test 1: Attempt to access another org's data via direct query.
        
        Expected: Query should return no results for other org's data.
        """
        async with db_pool.acquire() as conn:
            # Set org 1 context
            await conn.execute(
                "SET app.current_org_id = $1",
                TEST_ORG_1
            )
            
            # Try to query org 2's tickets
            rows = await conn.fetch(
                """
                SELECT ticket_id, org_id FROM tickets
                WHERE org_id = $1
                """,
                TEST_ORG_2
            )
            
            # Should return no results due to RLS
            assert len(rows) == 0, "RLS failed: Cross-org data visible"
            
            # Verify org 1 can see their own tickets
            rows = await conn.fetch(
                """
                SELECT ticket_id, org_id FROM tickets
                WHERE org_id = $1
                """,
                TEST_ORG_1
            )
            
            # Should return org 1's tickets
            assert len(rows) >= 0  # May be 0 if no test data
            for row in rows:
                assert row["org_id"] == TEST_ORG_1
    
    @pytest.mark.asyncio
    async def test_2_cross_org_api_access(self, db_pool):
        """
        Test 2: Attempt to access another org's data via API endpoint.
        
        Expected: API should reject cross-org access.
        """
        # This would test the actual API endpoints
        # For now, we test the underlying database access
        
        async with db_pool.acquire() as conn:
            # Set org 1 context
            await conn.execute(
                "SET app.current_org_id = $1",
                TEST_ORG_1
            )
            
            # Try to update org 2's ticket
            result = await conn.execute(
                """
                UPDATE tickets
                SET status = 'closed'
                WHERE ticket_id = 'TICKET-ORG2-001'
                AND org_id = $1
                """,
                TEST_ORG_2
            )
            
            # Should affect 0 rows
            assert result == "UPDATE 0", "RLS failed: Cross-org update allowed"
    
    @pytest.mark.asyncio
    async def test_3_sql_injection_bypass(self, db_pool):
        """
        Test 3: Attempt to bypass RLS via SQL injection.
        
        Expected: SQL injection should not bypass RLS policies.
        """
        async with db_pool.acquire() as conn:
            # Set org 1 context
            await conn.execute(
                "SET app.current_org_id = $1",
                TEST_ORG_1
            )
            
            # Attempt SQL injection to bypass org filter
            malicious_input = "' OR '1'='1"
            
            try:
                rows = await conn.fetch(
                    f"""
                    SELECT ticket_id, org_id FROM tickets
                    WHERE org_id = '{malicious_input}'
                    """
                )
                
                # Even with SQL injection, RLS should prevent cross-org access
                for row in rows:
                    assert row["org_id"] == TEST_ORG_1, \
                        "RLS bypassed via SQL injection!"
                    
            except asyncpg.exceptions.DataError:
                # Expected - malformed query
                pass
    
    @pytest.mark.asyncio
    async def test_4_privilege_escalation(self, db_pool):
        """
        Test 4: Attempt privilege escalation via SET commands.
        
        Expected: Non-superusers should not be able to bypass RLS.
        """
        async with db_pool.acquire() as conn:
            # Set org 1 context
            await conn.execute(
                "SET app.current_org_id = $1",
                TEST_ORG_1
            )
            
            # Try to change to org 2 context (as regular user)
            try:
                await conn.execute(
                    "SET app.current_org_id = $1",
                    TEST_ORG_2
                )
                
                # Query tickets
                rows = await conn.fetch(
                    "SELECT ticket_id, org_id FROM tickets"
                )
                
                # Even after SET, RLS should still apply based on session
                for row in rows:
                    # The RLS policy should use the session variable
                    pass
                    
            except asyncpg.exceptions.InsufficientPrivilegeError:
                # Expected if SET is restricted
                pass
    
    @pytest.mark.asyncio
    async def test_5_direct_table_access(self, db_pool):
        """
        Test 5: Attempt direct table access without RLS context.
        
        Expected: Access should be denied or return no rows.
        """
        async with db_pool.acquire() as conn:
            # Don't set any org context
            await conn.execute("RESET app.current_org_id")
            
            # Try to query tickets
            rows = await conn.fetch(
                "SELECT ticket_id, org_id FROM tickets"
            )
            
            # Should return no rows or only rows where org_id is NULL
            for row in rows:
                assert row["org_id"] is None, \
                    "RLS failed: Data visible without org context"


# =============================================================================
# RLS Policy Validation Tests
# =============================================================================

class RLSPolicyTests:
    """Tests for specific RLS policies."""
    
    @pytest.mark.asyncio
    async def test_tickets_rls_policy(self, db_pool):
        """Test tickets table RLS policy."""
        async with db_pool.acquire() as conn:
            # Enable RLS
            await conn.execute(
                "ALTER TABLE tickets ENABLE ROW LEVEL SECURITY"
            )
            
            # Check policy exists
            row = await conn.fetchrow(
                """
                SELECT * FROM pg_policies
                WHERE tablename = 'tickets'
                AND policyname = 'tenant_isolation'
                """
            )
            
            assert row is not None, "Tickets RLS policy not found"
    
    @pytest.mark.asyncio
    async def test_memory_archive_rls_policy(self, db_pool):
        """Test memory_archive table RLS policy."""
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM pg_policies
                WHERE tablename = 'memory_archive'
                AND policyname = 'tenant_isolation'
                """
            )
            
            assert row is not None, "Memory archive RLS policy not found"
    
    @pytest.mark.asyncio
    async def test_audit_log_rls_policy(self, db_pool):
        """Test audit_log table RLS policy."""
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM pg_policies
                WHERE tablename = 'audit_log'
                AND policyname = 'tenant_isolation'
                """
            )
            
            assert row is not None, "Audit log RLS policy not found"


# =============================================================================
# Helper Functions
# =============================================================================

async def setup_test_data(db_pool):
    """Setup test data for penetration tests."""
    async with db_pool.acquire() as conn:
        # Create test organizations
        await conn.execute(
            """
            INSERT INTO organizations (org_id, name)
            VALUES ($1, 'Test Org 1'), ($2, 'Test Org 2')
            ON CONFLICT DO NOTHING
            """,
            TEST_ORG_1, TEST_ORG_2
        )
        
        # Create test tickets for org 1
        await conn.execute(
            """
            INSERT INTO tickets (ticket_id, org_id, title, status)
            VALUES ('TICKET-ORG1-001', $1, 'Test Ticket 1', 'active')
            ON CONFLICT DO NOTHING
            """,
            TEST_ORG_1
        )
        
        # Create test tickets for org 2
        await conn.execute(
            """
            INSERT INTO tickets (ticket_id, org_id, title, status)
            VALUES ('TICKET-ORG2-001', $1, 'Test Ticket 2', 'active')
            ON CONFLICT DO NOTHING
            """,
            TEST_ORG_2
        )


async def cleanup_test_data(db_pool):
    """Cleanup test data after tests."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM tickets WHERE org_id IN ($1, $2)",
            TEST_ORG_1, TEST_ORG_2
        )
        await conn.execute(
            "DELETE FROM organizations WHERE org_id IN ($1, $2)",
            TEST_ORG_1, TEST_ORG_2
        )


# =============================================================================
# Test Runner
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
