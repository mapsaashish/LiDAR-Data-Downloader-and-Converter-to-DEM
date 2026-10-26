-- PostgreSQL schema for the Supabase project LiDAR-Data-Downloader-and-Converter-to-DEM.
create table public.saved_areas (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (length(name) between 1 and 200),
  state_fips text not null check (state_fips ~ '^[0-9]{2}$'),
  county_geoid text not null check (county_geoid ~ '^[0-9]{5}$' and left(county_geoid,2) = state_fips),
  geometry jsonb check (geometry is null or (geometry->>'type' = 'Feature' and geometry->'geometry'->>'type' in ('Polygon','MultiPolygon') and octet_length(geometry::text) <= 5000000)),
  created_at timestamptz not null default now()
);
create index saved_areas_owner_created_idx on public.saved_areas(user_id,created_at desc);
alter table public.saved_areas enable row level security;
revoke all on public.saved_areas from anon, authenticated;
grant select, insert, delete on public.saved_areas to authenticated;
create policy saved_areas_select_own on public.saved_areas for select to authenticated using ((select auth.uid()) = user_id);
create policy saved_areas_insert_own on public.saved_areas for insert to authenticated with check ((select auth.uid()) = user_id);
create policy saved_areas_delete_own on public.saved_areas for delete to authenticated using ((select auth.uid()) = user_id);

create table public.dem_jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (length(name) between 1 and 200),
  county_geoid text not null check (county_geoid ~ '^[0-9]{5}$'),
  resolution integer not null check (resolution in (1,5,10,30)),
  survey text not null check (length(survey) between 1 and 500),
  tile_count integer not null check (tile_count > 0),
  status text not null default 'prepared' check (status = 'prepared'),
  created_at timestamptz not null default now()
);
create index dem_jobs_owner_created_idx on public.dem_jobs(user_id,created_at desc);
alter table public.dem_jobs enable row level security;
revoke all on public.dem_jobs from anon, authenticated;
grant select, insert, delete on public.dem_jobs to authenticated;
create policy dem_jobs_select_own on public.dem_jobs for select to authenticated using ((select auth.uid()) = user_id);
create policy dem_jobs_insert_own on public.dem_jobs for insert to authenticated with check ((select auth.uid()) = user_id);
create policy dem_jobs_delete_own on public.dem_jobs for delete to authenticated using ((select auth.uid()) = user_id);
