-- Additive migration: old records stay valid; missing optional fields remain NULL.
begin;
alter table public.applications
  alter column owner_name drop not null,
  alter column pet_name drop not null,
  alter column pet_breed drop not null,
  alter column pet_age drop not null,
  alter column service drop not null,
  alter column preferred_date drop not null,
  alter column preferred_time drop not null,
  alter column comment drop not null,
  add column if not exists pet_type text check (pet_type in ('кошка','собака','другое')),
  add column if not exists pet_weight text check (length(pet_weight) <= 80),
  add column if not exists pet_size text check (pet_size in ('мелкий','средний','крупный')),
  add column if not exists request_text text check (length(request_text) between 1 and 1800),
  add column if not exists schedule_note text check (length(schedule_note) <= 300),
  add column if not exists handoff text check (length(handoff) <= 2200);
-- Legacy rows have a service; new requests need a service or a stated task.
alter table public.applications drop constraint if exists applications_request_required;
alter table public.applications add constraint applications_request_required
  check (service is not null or nullif(btrim(request_text),'') is not null);
alter table public.applications enable row level security;
-- Keep server-only INSERT/SELECT privileges; do not add public policies.
notify pgrst, 'reload schema';
commit;
