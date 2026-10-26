import { createClient } from '@supabase/supabase-js';
// Publishable browser key; PostgreSQL row-level security enforces ownership.
export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL || 'https://bhvwviwdxlcqfcefmnkf.supabase.co',
  import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY || 'sb_publishable_bWbG3MPomc7dmrPrpcmT0w_EIr4siFg'
);
