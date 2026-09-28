-- Craftboard MVP schema (SQLite).
-- All money is stored in integer cents. All timestamps are integer Unix seconds (UTC).

CREATE TABLE IF NOT EXISTS creators (
  id                  INTEGER PRIMARY KEY,
  email               TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash       TEXT NOT NULL,
  name                TEXT NOT NULL,
  handle              TEXT NOT NULL UNIQUE COLLATE NOCASE,   -- public link: /c/<handle>
  plan                TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free','studio','pro')),
  slots               INTEGER NOT NULL DEFAULT 3,             -- concurrent commissions
  intake_mode         TEXT NOT NULL DEFAULT 'auto' CHECK (intake_mode IN ('auto','open','closed')),
  revisions_included  INTEGER NOT NULL DEFAULT 2,
  deposit_pct         INTEGER NOT NULL DEFAULT 50,            -- % charged on acceptance
  hours_per_week      REAL NOT NULL DEFAULT 30,
  intake_message      TEXT NOT NULL DEFAULT '',
  stripe_account_id   TEXT,                                   -- Stripe Connect account (optional)
  created_at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash  TEXT PRIMARY KEY,
  creator_id  INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
  created_at  INTEGER NOT NULL,
  expires_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS commission_types (
  id                    INTEGER PRIMARY KEY,
  creator_id            INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
  name                  TEXT NOT NULL,
  description           TEXT NOT NULL DEFAULT '',
  base_price_cents      INTEGER NOT NULL CHECK (base_price_cents >= 0),
  est_hours             REAL NOT NULL DEFAULT 3,
  characters_included   INTEGER NOT NULL DEFAULT 1,
  extra_character_pct   INTEGER NOT NULL DEFAULT 60,
  active                INTEGER NOT NULL DEFAULT 1,
  sort_order            INTEGER NOT NULL DEFAULT 0,
  created_at            INTEGER NOT NULL
);

-- Every price edit is recorded so the goal agent can see how demand reacted.
CREATE TABLE IF NOT EXISTS price_changes (
  id          INTEGER PRIMARY KEY,
  creator_id  INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
  type_id     INTEGER REFERENCES commission_types(id) ON DELETE SET NULL,
  pct         REAL NOT NULL,           -- +10.0 means a 10% raise
  changed_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
  id          INTEGER PRIMARY KEY,
  creator_id  INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
  handle      TEXT NOT NULL COLLATE NOCASE,
  email       TEXT,
  notes       TEXT NOT NULL DEFAULT '',
  created_at  INTEGER NOT NULL,
  UNIQUE (creator_id, handle)
);

-- A brief submitted through the intake form, before the creator accepts it.
CREATE TABLE IF NOT EXISTS requests (
  id              INTEGER PRIMARY KEY,
  creator_id      INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
  client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  type_id         INTEGER NOT NULL REFERENCES commission_types(id),
  brief_json      TEXT NOT NULL,
  screening_json  TEXT NOT NULL,
  claude_json     TEXT,                  -- optional second read from Claude
  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','accepted','declined','info_requested')),
  creator_note    TEXT NOT NULL DEFAULT '',
  created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS waitlist (
  id          INTEGER PRIMARY KEY,
  creator_id  INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
  client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  type_id     INTEGER REFERENCES commission_types(id) ON DELETE SET NULL,
  note        TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting','invited','removed')),
  created_at  INTEGER NOT NULL,
  invited_at  INTEGER
);

-- The commission itself. The brief is copied and locked at acceptance.
-- Lifecycle: awaiting_deposit → queued → in_progress ⇄ in_review → approved → delivered → completed
--            (cancelled from any open state)
CREATE TABLE IF NOT EXISTS commissions (
  id                  INTEGER PRIMARY KEY,
  creator_id          INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
  client_id           INTEGER NOT NULL REFERENCES clients(id),
  type_id             INTEGER REFERENCES commission_types(id) ON DELETE SET NULL,
  request_id          INTEGER REFERENCES requests(id) ON DELETE SET NULL,
  title               TEXT NOT NULL,
  brief_json          TEXT NOT NULL,
  brief_locked_at     INTEGER NOT NULL,
  price_cents         INTEGER NOT NULL,          -- grows when the client accepts an upgrade
  deposit_cents       INTEGER NOT NULL,
  revisions_included  INTEGER NOT NULL,
  revisions_used      INTEGER NOT NULL DEFAULT 0,
  hours_logged        REAL NOT NULL DEFAULT 0,
  status              TEXT NOT NULL DEFAULT 'awaiting_deposit'
                      CHECK (status IN ('awaiting_deposit','queued','in_progress','in_review','approved','delivered','completed','cancelled')),
  portal_token        TEXT NOT NULL UNIQUE,
  source              TEXT NOT NULL DEFAULT 'intake' CHECK (source IN ('intake','import','manual')),
  accepted_at         INTEGER NOT NULL,
  started_at          INTEGER,
  delivered_at        INTEGER,
  completed_at        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_commissions_creator_status ON commissions(creator_id, status);

-- Client feedback on a WIP. kind decides what it costs.
CREATE TABLE IF NOT EXISTS revision_requests (
  id              INTEGER PRIMARY KEY,
  commission_id   INTEGER NOT NULL REFERENCES commissions(id) ON DELETE CASCADE,
  text            TEXT NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN ('revision','out_of_scope','paid_revision')),
  reasons_json    TEXT NOT NULL DEFAULT '[]',
  quote_cents     INTEGER NOT NULL DEFAULT 0,
  status          TEXT NOT NULL CHECK (status IN ('applied','quoted','accepted','declined')),
  created_at      INTEGER NOT NULL,
  resolved_at     INTEGER
);

CREATE TABLE IF NOT EXISTS payments (
  id              INTEGER PRIMARY KEY,
  commission_id   INTEGER NOT NULL REFERENCES commissions(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL CHECK (kind IN ('deposit','balance')),
  amount_cents    INTEGER NOT NULL,
  provider        TEXT NOT NULL CHECK (provider IN ('stripe','simulated','import')),
  provider_ref    TEXT,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','paid','failed')),
  created_at      INTEGER NOT NULL,
  paid_at         INTEGER
);

CREATE TABLE IF NOT EXISTS deliverables (
  id              INTEGER PRIMARY KEY,
  commission_id   INTEGER NOT NULL REFERENCES commissions(id) ON DELETE CASCADE,
  filename        TEXT NOT NULL,
  stored_name     TEXT NOT NULL,
  size_bytes      INTEGER NOT NULL,
  uploaded_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
  id              INTEGER PRIMARY KEY,
  creator_id      INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
  amount_cents    INTEGER NOT NULL,
  weeks           INTEGER NOT NULL,
  hours_per_week  REAL NOT NULL,
  plan_json       TEXT NOT NULL,         -- engine output at creation time
  narrative       TEXT,                  -- Claude's explanation, if generated
  active          INTEGER NOT NULL DEFAULT 1,
  created_at      INTEGER NOT NULL
);
