from supabase import create_client, Client
from config import config

# Initialize Supabase Client
supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

def get_supabase() -> Client:
    return supabase
