-- AI Lead Finder schema
-- Run in Supabase SQL Editor.
create extension if not exists pgcrypto;

create table if not exists public.leads (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  company text not null,
  website text not null,
  country text not null default 'USA',
  niche text not null default 'Software development agencies',
  business_emails text[] not null default '{}',
  email_source_pages text[] not null default '{}',
  lead_score integer not null default 0 check (lead_score between 0 and 25),
  status text not null default 'new'
    check (status in ('new','reviewed','contacted','replied','interested','not_a_fit')),
  notes text,
  last_scanned_at timestamptz,
  created_at timestamptz not null default now(),
  unique (owner_id, website)
);

create index if not exists leads_owner_created_idx
  on public.leads (owner_id, created_at desc);

alter table public.leads enable row level security;

-- Authenticated users may only access their own rows if the client is ever used directly.
drop policy if exists "Users can read own leads" on public.leads;
create policy "Users can read own leads"
  on public.leads for select to authenticated
  using ((select auth.uid()) = owner_id);

drop policy if exists "Users can insert own leads" on public.leads;
create policy "Users can insert own leads"
  on public.leads for insert to authenticated
  with check ((select auth.uid()) = owner_id);

drop policy if exists "Users can update own leads" on public.leads;
create policy "Users can update own leads"
  on public.leads for update to authenticated
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);

drop policy if exists "Users can delete own leads" on public.leads;
create policy "Users can delete own leads"
  on public.leads for delete to authenticated
  using ((select auth.uid()) = owner_id);

grant select, insert, update, delete on public.leads to authenticated, service_role;
grant usage on schema public to authenticated, service_role;
