-- Migration 018: API Keys table
-- Stores hashed API keys for Cassandra Voice Server authentication.

BEGIN;

-- API Keys table
CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    role TEXT NOT NULL DEFAULT 'tenant'
        CHECK (role IN ('tenant', 'super_tenant', 'admin', 'org_super_admin', 'owner', 'master_admin')),
    name TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID REFERENCES users(id),

    CONSTRAINT key_hash_length CHECK (length(key_hash) = 64)
);

-- Indexes for fast key lookup during validation
CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_org_id ON api_keys(org_id);

-- RLS: only service role can manage keys (api_keys table is backend-internal)
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;

-- Only authenticated users can view keys in their org
CREATE POLICY "org_view_own_keys" ON api_keys
    FOR SELECT USING (
        org_id IN (
            SELECT org_id FROM users WHERE id = auth.uid()
        )
    );

-- Service role can insert/delete keys
CREATE POLICY "service_insert_keys" ON api_keys
    FOR INSERT WITH CHECK (auth.role() = 'service_role');

CREATE POLICY "service_delete_keys" ON api_keys
    FOR DELETE USING (auth.role() = 'service_role');

-- RPC function to validate a key (used by backend/auth/api_key.py)
-- SECURITY DEFINER so it runs with service role permissions
CREATE OR REPLACE FUNCTION validate_api_key(key_hash TEXT)
RETURNS SETOF api_keys
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    SELECT *
    FROM api_keys
    WHERE api_keys.key_hash = validate_api_key.key_hash
      AND api_keys.is_active = true
      AND (api_keys.expires_at IS NULL OR api_keys.expires_at > now());
END;
$$;

-- Trigger to auto-update updated_at
CREATE OR REPLACE TRIGGER api_keys_updated_at
    BEFORE UPDATE ON api_keys
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

COMMIT;
