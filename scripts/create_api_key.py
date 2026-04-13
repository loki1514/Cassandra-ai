#!/usr/bin/env python3
"""
scripts/create_api_key.py — Generate a Cassandra API key.

Usage:
    python3 scripts/create_api_key.py --org-id <ORG_ID> --name "My App Key"

This generates a key pair and either:
  - (with --supabase-url + --service-key) inserts directly into Supabase
  - (without flags) prints the INSERT SQL to run manually in Supabase SQL Editor

Format: sk_cassandra_{32 random base64url chars}
"""
import argparse
import hashlib
import secrets
import base64
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def generate_api_key() -> tuple[str, str]:
    """Returns (plain_key, key_hash)."""
    random_bytes = secrets.token_bytes(24)
    random_part = base64.urlsafe_b64encode(random_bytes).decode().rstrip("=")
    plain_key = f"sk_cassandra_{random_part}"
    key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
    key_prefix = plain_key[:18]
    return plain_key, key_hash, key_prefix


def main():
    parser = argparse.ArgumentParser(description="Generate a Cassandra API key")
    parser.add_argument("--org-id", required=True, help="Organization UUID from Supabase")
    parser.add_argument("--user-id", help="User UUID (optional)")
    parser.add_argument("--name", default="CLI-generated key", help="Key friendly name")
    parser.add_argument("--role", default="tenant", help="Role: tenant, admin, org_super_admin, owner, master_admin")
    parser.add_argument("--supabase-url", help="Supabase project URL (to insert directly)")
    parser.add_argument("--service-key", help="Supabase service role key (to insert directly)")
    args = parser.parse_args()

    plain_key, key_hash, key_prefix = generate_api_key()

    print("=" * 60)
    print("CASSANDRA API KEY GENERATED")
    print("=" * 60)
    print(f"\n  Plain key (SAVE THIS — shown only once):")
    print(f"  {plain_key}")
    print(f"\n  Key prefix (for logging): {key_prefix}")
    print(f"  Org ID: {args.org_id}")
    print(f"  Role: {args.role}")
    print(f"  Name: {args.name}")
    print("=" * 60)

    if args.supabase_url and args.service_key:
        # Insert directly into Supabase
        import httpx
        import asyncio

        async def insert_key():
            record = {
                "key_hash": key_hash,
                "key_prefix": key_prefix,
                "org_id": args.org_id,
                "user_id": args.user_id,
                "role": args.role,
                "name": args.name,
                "is_active": True,
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{args.supabase_url}/rest/v1/api_keys",
                    headers={
                        "apikey": args.service_key,
                        "Authorization": f"Bearer {args.service_key}",
                        "Content-Type": "application/json",
                        "Prefer": "return=representation",
                    },
                    json=record,
                )
                resp.raise_for_status()
                result = resp.json()
                key_id = result[0]["id"] if isinstance(result, list) else result["id"]
                print(f"\n  Inserted into Supabase! Key ID: {key_id}")

        asyncio.run(insert_key())
    else:
        # Print manual SQL for Supabase SQL Editor
        print(f"\n\nRun this in your Supabase SQL Editor:")
        print("-" * 60)
        print(f"""
INSERT INTO api_keys (key_hash, key_prefix, org_id, user_id, role, name, is_active)
VALUES (
  '{key_hash}',
  '{key_prefix}',
  '{args.org_id}',
  {f"'{args.user_id}'" if args.user_id else 'NULL'},
  '{args.role}',
  '{args.name}',
  true
);
""")
        print("-" * 60)


if __name__ == "__main__":
    main()
