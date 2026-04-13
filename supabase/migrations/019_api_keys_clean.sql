-- Migration 019: Clean api_keys table
-- Removes columns that should NOT live on the API key (role, user_id, expires_at, created_by).
-- API key = org identity proof only. Role comes from verified JWT at session time.
-- Adds last_used column for tracking. Adds jwks_url to orgs.
-- Rebuilt: fixes function bug where dropped expires_at was still referenced.

BEGIN;

-- Remove columns that belong on the JWT, not the API key
ALTER TABLE api_keys DROP COLUMN IF EXISTS role;
ALTER TABLE api_keys DROP COLUMN IF EXISTS user_id;
ALTER TABLE api_keys DROP COLUMN IF EXISTS expires_at;
ALTER TABLE api_keys DROP COLUMN IF EXISTS created_by;

-- Track when a key was last used (updated on validate)
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS last_used TIMESTAMPTZ;

-- Per-org JWKS URL for custom identity providers
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS jwks_url TEXT;

-- Update the validate_api_key RPC to also update last_used
-- BUG FIX: removed expires_at reference since the column was dropped above
CREATE OR REPLACE FUNCTION validate_api_key(key_hash TEXT)
RETURNS SETOF api_keys
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN QUERY
    UPDATE api_keys
    SET last_used = now()
    WHERE api_keys.key_hash = validate_api_key.key_hash
      AND api_keys.is_active = true
    RETURNING *;
END;
$$;

COMMIT;
