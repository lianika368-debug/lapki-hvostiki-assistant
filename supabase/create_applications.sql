begin;
create table public.applications (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  owner_name text not null check (length(owner_name) between 1 and 100),
  phone text not null check (phone ~ '^\+[1-9][0-9]{9,14}$'),
  pet_name text not null check (length(pet_name) between 1 and 100),
  pet_breed text not null check (length(pet_breed) between 1 and 150),
  pet_age text not null check (length(pet_age) between 1 and 80),
  service text not null check (service in (
    'Экспресс-линька', 'Гигиеническая стрижка', 'Комплексный груминг для кошек',
    'Комплексный груминг для собак мелких пород', 'Комплексный груминг для собак средних пород',
    'Комплексный груминг для крупных собак', 'Стрижка когтей', 'Чистка ушей',
    'Чистка зубов без наркоза', 'Расчёсывание колтунов', 'Креативное окрашивание')),
  preferred_date date not null,
  preferred_time time not null check (preferred_time between '10:00' and '20:00'),
  preferred_groomer text check (preferred_groomer in ('Анна', 'Дарья', 'Марина', 'Без предпочтений')),
  comment text not null check (length(comment) between 1 and 1000),
  status text not null default 'new' check (status in ('new', 'contacted', 'confirmed', 'cancelled'))
);
alter table public.applications enable row level security;
revoke all on public.applications from public, anon, authenticated, service_role;
grant insert, select on public.applications to service_role;
-- No public policies: only the server's service_role can insert/read.
comment on table public.applications is 'Заявки на груминг. Желаемое время не означает подтверждённый слот.';
commit;
