-- appardas schema v4. Migration and env-owner seeding share a startup transaction.

CREATE TABLE IF NOT EXISTS guilds (
  id          INTEGER PRIMARY KEY,
  name        TEXT,
  joined_at   TEXT NOT NULL,
  left_at     TEXT
);

CREATE TABLE IF NOT EXISTS guild_settings (
  guild_id           INTEGER PRIMARY KEY REFERENCES guilds(id) ON DELETE CASCADE,
  announce_channel   INTEGER,          -- the ONLY channel announcements may post to
  github_channel     INTEGER,          -- default target for github posts
  mod_log_channel    INTEGER,
  timezone           TEXT NOT NULL DEFAULT 'UTC',
  settings           TEXT NOT NULL DEFAULT '{}',
  updated_at         TEXT NOT NULL
);

-- Cog on/off state. The whole maintenance/deprecation story lives in this table.
CREATE TABLE IF NOT EXISTS cog_state (
  guild_id    INTEGER NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
  cog         TEXT NOT NULL,
  enabled     INTEGER NOT NULL DEFAULT 1,
  reason      TEXT,
  updated_by  INTEGER,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (guild_id, cog)
);

-- Last unhandled error per cog, for /status errors and the dashboard.
CREATE TABLE IF NOT EXISTS cog_errors (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id    INTEGER,
  cog         TEXT NOT NULL,
  command     TEXT,
  error_type  TEXT NOT NULL,
  occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cog_errors ON cog_errors (occurred_at DESC);

-- Scheduled + one-off announcements.
CREATE TABLE IF NOT EXISTS announcements (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id    INTEGER NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
  channel_id  INTEGER NOT NULL,
  author_id   INTEGER NOT NULL,
  payload     TEXT NOT NULL,          -- JSON: {title, body, ping_role_id}
  send_at     TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',  -- pending|sent|failed|cancelled
  sent_at     TEXT,
  error       TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_announce_due ON announcements (status, send_at);

-- Every nudge, for abuse investigation.
CREATE TABLE IF NOT EXISTS nudges (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id    INTEGER NOT NULL,
  author_id   INTEGER NOT NULL,
  target_id   INTEGER NOT NULL,
  message     TEXT NOT NULL,
  delivered   INTEGER NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nudges_pair ON nudges (author_id, target_id, created_at);
CREATE INDEX IF NOT EXISTS idx_nudges_target ON nudges (guild_id, target_id, created_at);
CREATE INDEX IF NOT EXISTS idx_nudges_author ON nudges (guild_id, author_id, created_at);

CREATE TABLE IF NOT EXISTS nudge_optouts (
  user_id     INTEGER PRIMARY KEY,
  created_at  TEXT NOT NULL
);

-- GitHub watchers. target_type='org' covers every repo in an organisation
-- with ONE request to /orgs/{org}/events, so this table stays tiny.
CREATE TABLE IF NOT EXISTS github_watches (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id       INTEGER NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
  target         TEXT NOT NULL,
  target_type    TEXT NOT NULL,         -- 'org' | 'repo'
  channel_id     INTEGER NOT NULL,
  events         TEXT NOT NULL,         -- CSV: releases,issues,pulls,pushes
  etag           TEXT,
  cursor         TEXT,                  -- newest delivered event id
  last_polled    TEXT,
  last_delivered TEXT,
  state          TEXT NOT NULL DEFAULT 'ok',  -- ok|quiet|stale|rate-limited|missing
  last_error     TEXT,
  created_by     INTEGER NOT NULL,
  UNIQUE (guild_id, target)
);

CREATE TABLE IF NOT EXISTS audit_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id    INTEGER,
  actor_id    INTEGER,
  action      TEXT NOT NULL,
  target      TEXT,
  detail      TEXT,                   -- JSON, includes source: bot|web|system
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit ON audit_log (guild_id, created_at DESC);

-- Command usage log (time slot, user, command name, options, outcome).
CREATE TABLE IF NOT EXISTS command_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id    INTEGER,
  channel_id  INTEGER,
  user_id     INTEGER NOT NULL,
  user_name   TEXT NOT NULL,
  command     TEXT NOT NULL,
  options     TEXT,
  status      TEXT NOT NULL DEFAULT 'success',  -- success | error | denied
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_command_log_guild ON command_log (guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_command_log_user ON command_log (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_command_log_created ON command_log (created_at DESC);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- Additive: the dashboard's heartbeat strip (one row per 5-minute bucket).
CREATE TABLE IF NOT EXISTS heartbeats (
  bucket_at TEXT PRIMARY KEY,
  ok        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_heartbeats ON heartbeats (bucket_at DESC);

CREATE TABLE IF NOT EXISTS accounts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  discord_user_id TEXT UNIQUE,
  username        TEXT UNIQUE,
  password_hash   TEXT,
  must_change_password INTEGER NOT NULL DEFAULT 0,
  name            TEXT NOT NULL,
  role            TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('owner', 'admin', 'viewer')),
  status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'revoked')),
  is_system       INTEGER NOT NULL DEFAULT 0,
  note            TEXT,
  created_by      INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  last_login_at   TEXT,
  last_login_ip   TEXT
);
CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts (status);
CREATE INDEX IF NOT EXISTS idx_accounts_discord ON accounts (discord_user_id);
CREATE INDEX IF NOT EXISTS idx_accounts_username ON accounts (username);
CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username_ci ON accounts (lower(username)) WHERE username IS NOT NULL;

CREATE TABLE IF NOT EXISTS account_guilds (
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  guild_id   INTEGER NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
  granted_by INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  granted_at TEXT NOT NULL,
  PRIMARY KEY (account_id, guild_id)
);
CREATE INDEX IF NOT EXISTS idx_account_guilds_guild ON account_guilds (guild_id);

CREATE TABLE IF NOT EXISTS sessions (
  id_hash         TEXT PRIMARY KEY,
  account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  discord_user_id INTEGER,
  username        TEXT,
  avatar          TEXT,
  last_guild_id   INTEGER,
  csrf            TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL,
  expires_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions (expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions (account_id);

CREATE TABLE IF NOT EXISTS oauth_states (
  state_hash TEXT PRIMARY KEY,
  next_path  TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id      TEXT NOT NULL,
  account_id      INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
  discord_user_id INTEGER,
  account_label   TEXT,
  role            TEXT,
  action          TEXT NOT NULL,
  outcome         TEXT NOT NULL CHECK (outcome IN ('ok', 'denied', 'error')),
  guild_id        INTEGER,
  target          TEXT,
  before_json     TEXT,
  after_json      TEXT,
  ip              TEXT,
  user_agent      TEXT,
  detail          TEXT,
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_account ON activity_log (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_guild ON activity_log (guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_action ON activity_log (action, created_at DESC);

INSERT INTO meta (key, value) VALUES ('schema_version', '4')
  ON CONFLICT(key) DO UPDATE SET value = excluded.value;
