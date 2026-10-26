alter table public.dem_jobs drop constraint dem_jobs_status_check;
alter table public.dem_jobs add constraint dem_jobs_status_check check (status in ('prepared','queued','downloading','streaming','converting','mosaicking','verifying','complete','failed','cancelled'));
alter table public.dem_jobs add column worker_job_id text;
alter table public.dem_jobs add column progress numeric not null default 0 check (progress >= 0 and progress <= 100);
alter table public.dem_jobs add column message text;
alter table public.dem_jobs add column output_name text;
create unique index dem_jobs_owner_worker_idx on public.dem_jobs(user_id,worker_job_id);
grant update on public.dem_jobs to authenticated;
create policy dem_jobs_update_own on public.dem_jobs for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
