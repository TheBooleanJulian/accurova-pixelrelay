-- PixelRelay schema v1 (SQLite).
-- IDs are UUID strings. Timestamps are UTC ISO-8601 strings. Booleans are 0/1.
-- Image binaries never live here: `assets` only references files in storage.

CREATE TABLE users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    auth_provider TEXT NOT NULL CHECK (auth_provider IN ('dev', 'supabase')),
    auth_subject  TEXT NOT NULL,
    display_name  TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (auth_provider, auth_subject)
);

CREATE TABLE owner_sessions (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    revoked_at   TEXT
);

CREATE TABLE projects (
    id          TEXT PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    owner_id    TEXT NOT NULL REFERENCES users(id),
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    shoot_date  TEXT,
    deadline    TEXT,
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX projects_owner ON projects(owner_id);

-- The Room PIN is derived as HMAC(SECRET_KEY, seed) mod 10^6, so the database
-- alone never reveals it. Rotating replaces the seed and bumps the version.
CREATE TABLE room_access_secrets (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    seed       TEXT NOT NULL,
    version    INTEGER NOT NULL CHECK (version >= 1),
    rotated_at TEXT NOT NULL
);

-- Contributors. The owner is never a member, so nobody can pick "the owner".
-- `role` is fixed to contributor for V1; an organiser role can be added here.
CREATE TABLE project_members (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'contributor' CHECK (role IN ('contributor')),
    opted_in     INTEGER NOT NULL DEFAULT 0 CHECK (opted_in IN (0, 1)),
    capacity     INTEGER CHECK (capacity IS NULL OR capacity >= 0),
    invite_only  INTEGER NOT NULL DEFAULT 0 CHECK (invite_only IN (0, 1)),
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    removed_at   TEXT
);
CREATE UNIQUE INDEX project_members_name
    ON project_members(project_id, display_name COLLATE NOCASE) WHERE removed_at IS NULL;

CREATE TABLE contributor_sessions (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    member_id    TEXT REFERENCES project_members(id),
    token_hash   TEXT NOT NULL UNIQUE,
    via          TEXT NOT NULL CHECK (via IN ('pin', 'invite')),
    pin_version  INTEGER,
    device_label TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    revoked_at   TEXT
);
CREATE INDEX contributor_sessions_project ON contributor_sessions(project_id, member_id);

CREATE TABLE invitation_tokens (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    member_id   TEXT NOT NULL REFERENCES project_members(id),
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    redeemed_at TEXT,
    revoked_at  TEXT
);

CREATE TABLE pin_attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    ip_hash    TEXT NOT NULL,
    success    INTEGER NOT NULL CHECK (success IN (0, 1)),
    at         REAL NOT NULL
);
CREATE INDEX pin_attempts_project ON pin_attempts(project_id, at);
CREATE INDEX pin_attempts_ip ON pin_attempts(ip_hash, at);

CREATE TABLE storage_integrations (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    provider    TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

-- Immutable file records. member_id marks a contributor's own file (upload,
-- mask, composite); photo_id ties a file to a photograph for access checks.
CREATE TABLE assets (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(id),
    photo_id          TEXT,
    member_id         TEXT,
    kind              TEXT NOT NULL CHECK (kind IN (
                          'original', 'thumbnail', 'preview', 'upload', 'edit_preview',
                          'mask', 'composite', 'composite_preview', 'export')),
    storage_provider  TEXT NOT NULL,
    storage_key       TEXT NOT NULL UNIQUE,
    content_type      TEXT NOT NULL,
    byte_size         INTEGER NOT NULL,
    sha256            TEXT NOT NULL,
    width             INTEGER,
    height            INTEGER,
    format            TEXT,
    original_filename TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX assets_project ON assets(project_id, kind);

CREATE TRIGGER assets_no_update BEFORE UPDATE ON assets
BEGIN SELECT RAISE(ABORT, 'assets are immutable'); END;
CREATE TRIGGER assets_no_delete BEFORE DELETE ON assets
BEGIN SELECT RAISE(ABORT, 'assets are immutable'); END;

CREATE TABLE photos (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    original_asset_id TEXT NOT NULL UNIQUE REFERENCES assets(id),
    thumb_asset_id    TEXT REFERENCES assets(id),
    preview_asset_id  TEXT REFERENCES assets(id),
    title             TEXT NOT NULL,
    width             INTEGER NOT NULL,
    height            INTEGER NOT NULL,
    format            TEXT NOT NULL,
    processing        TEXT NOT NULL DEFAULT 'pending' CHECK (processing IN ('pending', 'ready', 'failed')),
    available         INTEGER NOT NULL DEFAULT 1 CHECK (available IN (0, 1)),
    priority          INTEGER NOT NULL DEFAULT 0,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    final_revision_id TEXT REFERENCES revisions(id),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX photos_project ON photos(project_id, sort_order);

CREATE TRIGGER photos_original_fixed BEFORE UPDATE OF original_asset_id, width, height, format ON photos
WHEN NEW.original_asset_id IS NOT OLD.original_asset_id OR NEW.width IS NOT OLD.width
     OR NEW.height IS NOT OLD.height OR NEW.format IS NOT OLD.format
BEGIN SELECT RAISE(ABORT, 'the original photograph is immutable'); END;

-- One active assignment per photo in V1 (partial unique index). Ended
-- assignments stay as history; reassignment never touches revisions.
CREATE TABLE assignments (
    id                     TEXT PRIMARY KEY,
    project_id             TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    photo_id               TEXT NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    member_id              TEXT NOT NULL REFERENCES project_members(id),
    status                 TEXT NOT NULL CHECK (status IN (
                               'assigned', 'editing', 'submitted', 'needs_changes', 'approved')),
    source                 TEXT NOT NULL CHECK (source IN ('auto', 'manual')),
    capacity_at_assignment INTEGER,
    original_downloaded_at TEXT,
    active                 INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    ended_at               TEXT,
    ended_reason           TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE UNIQUE INDEX assignments_one_active ON assignments(photo_id) WHERE active = 1;
CREATE INDEX assignments_member ON assignments(member_id, active);

-- A merge draft is a contributor's upload being prepared (aligned, masked)
-- before it becomes an immutable revision.
CREATE TABLE merge_drafts (
    id                     TEXT PRIMARY KEY,
    project_id             TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    photo_id               TEXT NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    member_id              TEXT NOT NULL REFERENCES project_members(id),
    upload_asset_id        TEXT NOT NULL REFERENCES assets(id),
    edit_preview_asset_id  TEXT REFERENCES assets(id),
    parent_revision_id     TEXT REFERENCES revisions(id),
    current_mask_id        TEXT REFERENCES masks(id),
    status                 TEXT NOT NULL CHECK (status IN (
                               'processing', 'ready', 'blocked', 'failed', 'submitting', 'submitted')),
    sensitivity            TEXT NOT NULL DEFAULT 'medium' CHECK (sensitivity IN ('low', 'medium', 'high')),
    manual_align_json      TEXT NOT NULL DEFAULT '{}',
    alignment_json         TEXT NOT NULL DEFAULT '{}',
    warnings_json          TEXT NOT NULL DEFAULT '[]',
    analysis_json          TEXT NOT NULL DEFAULT '{}',
    error                  TEXT,
    revision_id            TEXT REFERENCES revisions(id),
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE INDEX merge_drafts_member ON merge_drafts(member_id, photo_id);

-- Every saved mask is a new row with its own immutable PNG asset.
CREATE TABLE masks (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    draft_id       TEXT NOT NULL REFERENCES merge_drafts(id) ON DELETE CASCADE,
    asset_id       TEXT NOT NULL REFERENCES assets(id),
    source         TEXT NOT NULL CHECK (source IN ('smart', 'manual', 'hybrid')),
    width          INTEGER NOT NULL,
    height         INTEGER NOT NULL,
    settings_json  TEXT NOT NULL DEFAULT '{}',
    alignment_json TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL
);

CREATE TABLE revisions (
    id                         TEXT PRIMARY KEY,
    project_id                 TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    photo_id                   TEXT NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    member_id                  TEXT NOT NULL REFERENCES project_members(id),
    number                     INTEGER NOT NULL,
    parent_revision_id         TEXT REFERENCES revisions(id),
    draft_id                   TEXT NOT NULL REFERENCES merge_drafts(id),
    upload_asset_id            TEXT NOT NULL REFERENCES assets(id),
    mask_id                    TEXT NOT NULL REFERENCES masks(id),
    composite_asset_id         TEXT NOT NULL REFERENCES assets(id),
    composite_preview_asset_id TEXT REFERENCES assets(id),
    upload_width               INTEGER NOT NULL,
    upload_height              INTEGER NOT NULL,
    upload_format              TEXT NOT NULL,
    composite_width            INTEGER NOT NULL,
    composite_height           INTEGER NOT NULL,
    composite_format           TEXT NOT NULL,
    merge_settings_json        TEXT NOT NULL DEFAULT '{}',
    warnings_json              TEXT NOT NULL DEFAULT '[]',
    validation_json            TEXT NOT NULL DEFAULT '{}',
    note                       TEXT NOT NULL DEFAULT '',
    status                     TEXT NOT NULL CHECK (status IN ('submitted', 'needs_changes', 'approved')),
    created_at                 TEXT NOT NULL,
    status_updated_at          TEXT NOT NULL,
    UNIQUE (photo_id, number)
);
CREATE INDEX revisions_photo ON revisions(photo_id, number);
CREATE INDEX revisions_member ON revisions(member_id);

-- Revisions are immutable except for their review status.
CREATE TRIGGER revisions_immutable BEFORE UPDATE ON revisions
WHEN NEW.id IS NOT OLD.id OR NEW.photo_id IS NOT OLD.photo_id OR NEW.member_id IS NOT OLD.member_id
  OR NEW.number IS NOT OLD.number OR NEW.parent_revision_id IS NOT OLD.parent_revision_id
  OR NEW.upload_asset_id IS NOT OLD.upload_asset_id OR NEW.mask_id IS NOT OLD.mask_id
  OR NEW.composite_asset_id IS NOT OLD.composite_asset_id
  OR NEW.composite_preview_asset_id IS NOT OLD.composite_preview_asset_id
  OR NEW.merge_settings_json IS NOT OLD.merge_settings_json OR NEW.note IS NOT OLD.note
  OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'revisions are immutable'); END;
CREATE TRIGGER revisions_no_delete BEFORE DELETE ON revisions
BEGIN SELECT RAISE(ABORT, 'revisions are immutable'); END;

-- Append-only review decisions.
CREATE TABLE approvals (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    photo_id         TEXT NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    revision_id      TEXT REFERENCES revisions(id),
    decision         TEXT NOT NULL CHECK (decision IN (
                         'approved', 'changes_requested', 'final_selected', 'final_cleared')),
    note             TEXT NOT NULL DEFAULT '',
    decided_by       TEXT NOT NULL REFERENCES users(id),
    created_at       TEXT NOT NULL
);
CREATE INDEX approvals_photo ON approvals(photo_id, created_at);
CREATE TRIGGER approvals_no_update BEFORE UPDATE ON approvals
BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END;
CREATE TRIGGER approvals_no_delete BEFORE DELETE ON approvals
BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END;

CREATE TABLE activity_events (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('owner', 'contributor', 'system')),
    actor_id   TEXT,
    kind       TEXT NOT NULL,
    photo_id   TEXT,
    data_json  TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX activity_events_project ON activity_events(project_id, created_at);

-- Durable background-job queue (claimed with a lease; retried with backoff).
CREATE TABLE jobs (
    id           TEXT PRIMARY KEY,
    project_id   TEXT,
    kind         TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    run_after    REAL NOT NULL,
    lease_until  REAL,
    error        TEXT,
    result_json  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX jobs_ready ON jobs(status, run_after);
