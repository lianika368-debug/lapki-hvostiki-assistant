-- Run after migration: all checks must return true.
select c.relrowsecurity as rls_enabled,
  not has_table_privilege('anon', 'public.applications', 'select') as anon_cannot_read,
  not has_table_privilege('anon', 'public.applications', 'insert') as anon_cannot_insert,
  not has_table_privilege('authenticated', 'public.applications', 'select') as user_cannot_read,
  not has_table_privilege('authenticated', 'public.applications', 'insert') as user_cannot_insert,
  has_table_privilege('service_role', 'public.applications', 'insert') as server_can_insert,
  has_table_privilege('service_role', 'public.applications', 'select') as server_can_verify
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname = 'applications';
