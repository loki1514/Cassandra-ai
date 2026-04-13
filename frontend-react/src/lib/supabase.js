import { createClient } from '@supabase/supabase-js';

const url = import.meta.env.VITE_FMS_SUPABASE_URL;
const key = import.meta.env.VITE_FMS_SUPABASE_ANON_KEY;

if (!url || !key) {
  throw new Error(
    'VITE_FMS_SUPABASE_URL and VITE_FMS_SUPABASE_ANON_KEY ' +
    'must be set in .env — no fallback exists'
  );
}

export const fmsSupabase = createClient(url, key);

export const getFmsToken = async () => {
  const { data: { session } } = await fmsSupabase.auth.getSession();
  if (!session) throw new Error('Not logged in');
  return session.access_token;
};
