-- Transactional access checks. All test users and rows are rolled back.
begin;
select set_config('lidar.test_owner',gen_random_uuid()::text,true);
insert into auth.users(id,email) values(current_setting('lidar.test_owner')::uuid,current_setting('lidar.test_owner')||'@example.invalid');
insert into public.saved_areas(user_id,name,state_fips,county_geoid) values(current_setting('lidar.test_owner')::uuid,'RLS test','12','12095');
insert into public.dem_jobs(user_id,name,county_geoid,resolution,survey,tile_count) values(current_setting('lidar.test_owner')::uuid,'RLS test','12095',5,'Test survey',1);
set local role authenticated;
select set_config('request.jwt.claim.sub',current_setting('lidar.test_owner'),true);
do $$begin
  if (select count(*) from public.saved_areas) <> 1 then raise exception 'Owner area read failed'; end if;
  if (select count(*) from public.dem_jobs) <> 1 then raise exception 'Owner job read failed'; end if;
end$$;
select set_config('request.jwt.claim.sub',gen_random_uuid()::text,true);
do $$declare rejected boolean := false; begin
  if (select count(*) from public.saved_areas) <> 0 then raise exception 'Cross-user area leak'; end if;
  if (select count(*) from public.dem_jobs) <> 0 then raise exception 'Cross-user job leak'; end if;
  begin
    insert into public.saved_areas(user_id,name,state_fips,county_geoid) values(current_setting('lidar.test_owner')::uuid,'Forbidden','12','12095');
  exception when insufficient_privilege then rejected := true;
  end;
  if not rejected then raise exception 'Cross-user write was permitted'; end if;
end$$;
reset role;
rollback;
select 'Owner reads, cross-user reads, and cross-user writes verified; test data rolled back.' as verification;
