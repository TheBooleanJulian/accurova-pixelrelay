-- accurova-pixelrelay (PixelRelay by Accurova): group photo-edit projects, Room PIN access, contributors' uploads,
-- Smart Merge masks, immutable revisions, approvals and the background-job queue.
-- Holds client and contributor data (names, uploads metadata, owner emails): only the app role can read it, and the
-- schema is not exposed over the Data API. Image files are in R2, never here.
-- App role: accurova_pixelrelay_app (password via scripts/provision.mjs accurova_pixelrelay).
-- The app repo keeps an identical copy in pixelrelay/migrations/postgres/ for its tests: change both together.
--
-- Mirrors the app's SQLite schema (pixelrelay/migrations/0001_init.sql) on purpose: ids are UUID text, timestamps are
-- UTC ISO-8601 text, flags are 0/1 integers, JSON is text. The same SQL then runs on both backends.

create schema if not exists accurova_pixelrelay;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'accurova_pixelrelay_app') then
    create role accurova_pixelrelay_app nologin;
  end if;
end $$;

-- One trigger function for every "this row never changes" rule; the message is the trigger's argument.
create or replace function accurova_pixelrelay.refuse_change() returns trigger
language plpgsql as $$
begin
  raise exception using message = tg_argv[0], errcode = 'integrity_constraint_violation';
end $$;

create table accurova_pixelrelay.schema_migrations (
  version    text primary key,
  applied_at text not null
);

create table accurova_pixelrelay.users (
  id            text primary key,
  email         text not null,
  auth_provider text not null check (auth_provider in ('dev', 'supabase')),
  auth_subject  text not null,
  display_name  text,
  created_at    text not null,
  unique (auth_provider, auth_subject)
);
create unique index users_email on accurova_pixelrelay.users (lower(email));

create table accurova_pixelrelay.owner_sessions (
  id           text primary key,
  user_id      text not null references accurova_pixelrelay.users(id) on delete cascade,
  token_hash   text not null unique,
  created_at   text not null,
  last_seen_at text not null,
  expires_at   text not null,
  revoked_at   text
);

create table accurova_pixelrelay.projects (
  id          text primary key,
  slug        text not null unique,
  owner_id    text not null references accurova_pixelrelay.users(id),
  name        text not null,
  description text not null default '',
  shoot_date  text,
  deadline    text,
  status      text not null default 'active' check (status in ('active', 'archived')),
  created_at  text not null,
  updated_at  text not null
);
create index projects_owner on accurova_pixelrelay.projects (owner_id);

-- The Room PIN is HMAC(SECRET_KEY, seed) mod 10^6: this table alone never reveals it.
create table accurova_pixelrelay.room_access_secrets (
  project_id text primary key references accurova_pixelrelay.projects(id) on delete cascade,
  seed       text not null,
  version    integer not null check (version >= 1),
  rotated_at text not null
);

create table accurova_pixelrelay.project_members (
  id           text primary key,
  project_id   text not null references accurova_pixelrelay.projects(id) on delete cascade,
  display_name text not null,
  role         text not null default 'contributor' check (role in ('contributor')),
  opted_in     integer not null default 0 check (opted_in in (0, 1)),
  capacity     integer check (capacity is null or capacity >= 0),
  invite_only  integer not null default 0 check (invite_only in (0, 1)),
  sort_order   integer not null default 0,
  created_at   text not null,
  removed_at   text
);
create unique index project_members_name
  on accurova_pixelrelay.project_members (project_id, lower(display_name)) where removed_at is null;

create table accurova_pixelrelay.contributor_sessions (
  id           text primary key,
  project_id   text not null references accurova_pixelrelay.projects(id) on delete cascade,
  member_id    text references accurova_pixelrelay.project_members(id),
  token_hash   text not null unique,
  via          text not null check (via in ('pin', 'invite')),
  pin_version  integer,
  device_label text not null default '',
  created_at   text not null,
  last_seen_at text not null,
  expires_at   text not null,
  revoked_at   text
);
create index contributor_sessions_project on accurova_pixelrelay.contributor_sessions (project_id, member_id);

create table accurova_pixelrelay.invitation_tokens (
  id          text primary key,
  project_id  text not null references accurova_pixelrelay.projects(id) on delete cascade,
  member_id   text not null references accurova_pixelrelay.project_members(id),
  token_hash  text not null unique,
  created_at  text not null,
  expires_at  text not null,
  redeemed_at text,
  revoked_at  text
);

create table accurova_pixelrelay.pin_attempts (
  id         bigint generated always as identity primary key,
  project_id text not null,
  ip_hash    text not null,
  success    integer not null check (success in (0, 1)),
  at         double precision not null
);
create index pin_attempts_project on accurova_pixelrelay.pin_attempts (project_id, at);
create index pin_attempts_ip on accurova_pixelrelay.pin_attempts (ip_hash, at);

create table accurova_pixelrelay.storage_integrations (
  id          text primary key,
  project_id  text not null unique references accurova_pixelrelay.projects(id) on delete cascade,
  provider    text not null,
  config_json text not null default '{}',
  created_at  text not null
);

-- Immutable file records (the files themselves are in R2).
create table accurova_pixelrelay.assets (
  id                text primary key,
  project_id        text not null references accurova_pixelrelay.projects(id),
  photo_id          text,
  member_id         text,
  kind              text not null check (kind in (
                      'original', 'thumbnail', 'preview', 'upload', 'edit_preview',
                      'mask', 'composite', 'composite_preview', 'export')),
  storage_provider  text not null,
  storage_key       text not null unique,
  content_type      text not null,
  byte_size         bigint not null,
  sha256            text not null,
  width             integer,
  height            integer,
  format            text,
  original_filename text,
  created_at        text not null
);
create index assets_project on accurova_pixelrelay.assets (project_id, kind);
create trigger assets_no_update before update on accurova_pixelrelay.assets
  for each row execute function accurova_pixelrelay.refuse_change('assets are immutable');
create trigger assets_no_delete before delete on accurova_pixelrelay.assets
  for each row execute function accurova_pixelrelay.refuse_change('assets are immutable');

create table accurova_pixelrelay.photos (
  id                text primary key,
  project_id        text not null references accurova_pixelrelay.projects(id) on delete cascade,
  original_asset_id text not null unique references accurova_pixelrelay.assets(id),
  thumb_asset_id    text references accurova_pixelrelay.assets(id),
  preview_asset_id  text references accurova_pixelrelay.assets(id),
  title             text not null,
  width             integer not null,
  height            integer not null,
  format            text not null,
  processing        text not null default 'pending' check (processing in ('pending', 'ready', 'failed')),
  available         integer not null default 1 check (available in (0, 1)),
  priority          integer not null default 0,
  sort_order        integer not null default 0,
  final_revision_id text,  -- foreign key added below (revisions is created later)
  created_at        text not null,
  updated_at        text not null
);
create index photos_project on accurova_pixelrelay.photos (project_id, sort_order);
create trigger photos_original_fixed before update on accurova_pixelrelay.photos
  for each row when (new.original_asset_id is distinct from old.original_asset_id or new.width is distinct from old.width
                     or new.height is distinct from old.height or new.format is distinct from old.format)
  execute function accurova_pixelrelay.refuse_change('the original photograph is immutable');

-- One active assignment per photo (partial unique index); ended ones stay as history.
create table accurova_pixelrelay.assignments (
  id                     text primary key,
  project_id             text not null references accurova_pixelrelay.projects(id) on delete cascade,
  photo_id               text not null references accurova_pixelrelay.photos(id) on delete cascade,
  member_id              text not null references accurova_pixelrelay.project_members(id),
  status                 text not null check (status in ('assigned', 'editing', 'submitted', 'needs_changes', 'approved')),
  source                 text not null check (source in ('auto', 'manual')),
  capacity_at_assignment integer,
  original_downloaded_at text,
  active                 integer not null default 1 check (active in (0, 1)),
  ended_at               text,
  ended_reason           text,
  created_at             text not null,
  updated_at             text not null
);
create unique index assignments_one_active on accurova_pixelrelay.assignments (photo_id) where active = 1;
create index assignments_member on accurova_pixelrelay.assignments (member_id, active);

create table accurova_pixelrelay.merge_drafts (
  id                    text primary key,
  project_id            text not null references accurova_pixelrelay.projects(id) on delete cascade,
  photo_id              text not null references accurova_pixelrelay.photos(id) on delete cascade,
  member_id             text not null references accurova_pixelrelay.project_members(id),
  upload_asset_id       text not null references accurova_pixelrelay.assets(id),
  edit_preview_asset_id text references accurova_pixelrelay.assets(id),
  parent_revision_id    text,  -- foreign keys to revisions / masks added below
  current_mask_id       text,
  status                text not null check (status in ('processing', 'ready', 'blocked', 'failed', 'submitting', 'submitted')),
  sensitivity           text not null default 'medium' check (sensitivity in ('low', 'medium', 'high')),
  manual_align_json     text not null default '{}',
  alignment_json        text not null default '{}',
  warnings_json         text not null default '[]',
  analysis_json         text not null default '{}',
  error                 text,
  revision_id           text,
  created_at            text not null,
  updated_at            text not null
);
create index merge_drafts_member on accurova_pixelrelay.merge_drafts (member_id, photo_id);

create table accurova_pixelrelay.masks (
  id             text primary key,
  project_id     text not null references accurova_pixelrelay.projects(id) on delete cascade,
  draft_id       text not null references accurova_pixelrelay.merge_drafts(id) on delete cascade,
  asset_id       text not null references accurova_pixelrelay.assets(id),
  source         text not null check (source in ('smart', 'manual', 'hybrid')),
  width          integer not null,
  height         integer not null,
  settings_json  text not null default '{}',
  alignment_json text not null default '{}',
  created_at     text not null
);

create table accurova_pixelrelay.revisions (
  id                         text primary key,
  project_id                 text not null references accurova_pixelrelay.projects(id) on delete cascade,
  photo_id                   text not null references accurova_pixelrelay.photos(id) on delete cascade,
  member_id                  text not null references accurova_pixelrelay.project_members(id),
  number                     integer not null,
  parent_revision_id         text references accurova_pixelrelay.revisions(id),
  draft_id                   text not null references accurova_pixelrelay.merge_drafts(id),
  upload_asset_id            text not null references accurova_pixelrelay.assets(id),
  mask_id                    text not null references accurova_pixelrelay.masks(id),
  composite_asset_id         text not null references accurova_pixelrelay.assets(id),
  composite_preview_asset_id text references accurova_pixelrelay.assets(id),
  upload_width               integer not null,
  upload_height              integer not null,
  upload_format              text not null,
  composite_width            integer not null,
  composite_height           integer not null,
  composite_format           text not null,
  merge_settings_json        text not null default '{}',
  warnings_json              text not null default '[]',
  validation_json            text not null default '{}',
  note                       text not null default '',
  status                     text not null check (status in ('submitted', 'needs_changes', 'approved')),
  created_at                 text not null,
  status_updated_at          text not null,
  unique (photo_id, number)
);
create index revisions_photo on accurova_pixelrelay.revisions (photo_id, number);
create index revisions_member on accurova_pixelrelay.revisions (member_id);
-- Revisions are immutable except for their review status.
create trigger revisions_immutable before update on accurova_pixelrelay.revisions
  for each row when (new.id is distinct from old.id or new.photo_id is distinct from old.photo_id
    or new.member_id is distinct from old.member_id or new.number is distinct from old.number
    or new.parent_revision_id is distinct from old.parent_revision_id or new.upload_asset_id is distinct from old.upload_asset_id
    or new.mask_id is distinct from old.mask_id or new.composite_asset_id is distinct from old.composite_asset_id
    or new.composite_preview_asset_id is distinct from old.composite_preview_asset_id
    or new.merge_settings_json is distinct from old.merge_settings_json or new.note is distinct from old.note
    or new.created_at is distinct from old.created_at)
  execute function accurova_pixelrelay.refuse_change('revisions are immutable');
create trigger revisions_no_delete before delete on accurova_pixelrelay.revisions
  for each row execute function accurova_pixelrelay.refuse_change('revisions are immutable');

alter table accurova_pixelrelay.photos
  add foreign key (final_revision_id) references accurova_pixelrelay.revisions(id);
alter table accurova_pixelrelay.merge_drafts
  add foreign key (parent_revision_id) references accurova_pixelrelay.revisions(id),
  add foreign key (current_mask_id) references accurova_pixelrelay.masks(id),
  add foreign key (revision_id) references accurova_pixelrelay.revisions(id);

-- Append-only review decisions.
create table accurova_pixelrelay.approvals (
  id          text primary key,
  project_id  text not null references accurova_pixelrelay.projects(id) on delete cascade,
  photo_id    text not null references accurova_pixelrelay.photos(id) on delete cascade,
  revision_id text references accurova_pixelrelay.revisions(id),
  decision    text not null check (decision in ('approved', 'changes_requested', 'final_selected', 'final_cleared')),
  note        text not null default '',
  decided_by  text not null references accurova_pixelrelay.users(id),
  created_at  text not null
);
create index approvals_photo on accurova_pixelrelay.approvals (photo_id, created_at);
create trigger approvals_no_update before update on accurova_pixelrelay.approvals
  for each row execute function accurova_pixelrelay.refuse_change('approvals are append-only');
create trigger approvals_no_delete before delete on accurova_pixelrelay.approvals
  for each row execute function accurova_pixelrelay.refuse_change('approvals are append-only');

create table accurova_pixelrelay.activity_events (
  id         text primary key,
  project_id text not null references accurova_pixelrelay.projects(id) on delete cascade,
  actor_type text not null check (actor_type in ('owner', 'contributor', 'system')),
  actor_id   text,
  kind       text not null,
  photo_id   text,
  data_json  text not null default '{}',
  created_at text not null
);
create index activity_events_project on accurova_pixelrelay.activity_events (project_id, created_at);

-- Durable background-job queue (claimed with a lease and FOR UPDATE SKIP LOCKED; retried with backoff).
create table accurova_pixelrelay.jobs (
  id           text primary key,
  project_id   text,
  kind         text not null,
  payload_json text not null default '{}',
  status       text not null check (status in ('queued', 'running', 'succeeded', 'failed')),
  attempts     integer not null default 0,
  max_attempts integer not null default 3,
  run_after    double precision not null,
  lease_until  double precision,
  error        text,
  result_json  text,
  created_at   text not null,
  updated_at   text not null
);
create index jobs_ready on accurova_pixelrelay.jobs (status, run_after);

-- The app checks this row at startup, so it refuses to run against a schema it doesn't match.
insert into accurova_pixelrelay.schema_migrations (version, applied_at)
  values ('20261123090000', to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"'));

revoke all on schema accurova_pixelrelay from public;
grant usage on schema accurova_pixelrelay to accurova_pixelrelay_app;
grant select, insert, update, delete on all tables in schema accurova_pixelrelay to accurova_pixelrelay_app;
revoke insert, update, delete on accurova_pixelrelay.schema_migrations from accurova_pixelrelay_app;
alter default privileges in schema accurova_pixelrelay grant select, insert, update, delete on tables to accurova_pixelrelay_app;
alter role accurova_pixelrelay_app set search_path = accurova_pixelrelay;
