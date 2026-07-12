-- rls_hardening.sql — Option A security hardening for the pilot.
--
-- Locks down direct table access. The portal connects with the service_role
-- key (which BYPASSES RLS), so it keeps full read/write. Every other caller —
-- anyone holding the public anon/publishable key — is DENIED, because RLS is
-- enabled with NO policies (default-deny for the anon/authenticated roles).
--
-- ⚠️ ORDER MATTERS. Run this ONLY AFTER the deployed portal is using the
-- service_role key (SUPABASE_SERVICE_ROLE_KEY set in Railway and redeployed).
-- If you enable RLS while the portal still uses the anon key, the live app
-- will break (anon can no longer read crops).
--
-- Safe to re-run (ENABLE is idempotent).
--
-- To roll back (re-open) during debugging:
--   ALTER TABLE crops       DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE labels      DISABLE ROW LEVEL SECURITY;
--   ALTER TABLE assignments DISABLE ROW LEVEL SECURITY;

ALTER TABLE crops       ENABLE ROW LEVEL SECURITY;
ALTER TABLE labels      ENABLE ROW LEVEL SECURITY;
ALTER TABLE assignments ENABLE ROW LEVEL SECURITY;

-- No policies are created on purpose: with RLS enabled and zero policies,
-- the anon and authenticated roles get NO access at all, while service_role
-- (the portal) and the table owner bypass RLS entirely.
