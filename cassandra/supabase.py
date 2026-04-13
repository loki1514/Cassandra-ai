"""
Shared Supabase Client Factory

Provides a centralized, cached Supabase client for use across all
cassandra modules. Eliminates duplicated client creation and ensures
consistent attribute access to settings.

Usage:
    from cassandra.supabase import get_supabase_client

    # Service role client (bypasses RLS for server-side operations)
    client = get_supabase_client("service")

    # Anon key client (respects RLS for user-facing operations)
    anon_client = get_supabase_client("anon")
"""

from functools import lru_cache
from typing import Literal

from supabase import create_client, Client


@lru_cache(maxsize=2)
def get_supabase_client(
    role: Literal["service", "anon"] = "service"
) -> Client:
    """
    Get a cached Supabase client for the specified role.

    Uses @lru_cache so each role returns the same instance across calls.
    Clients are created lazily on first call.

    Args:
        role: "service" for service_role_key (server-side, bypasses RLS),
              "anon" for anon_key (client-facing, respects RLS)

    Returns:
        Supabase Client instance

    Raises:
        AttributeError: If required settings are not configured
    """
    from cassandra.config import settings

    if role == "service":
        key = settings.supabase.service_role_key
    else:
        key = settings.supabase.anon_key

    return create_client(settings.supabase.url, key)
