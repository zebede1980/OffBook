-- Baseline: settings, the kids' profiles, the job queue and the AI usage log.
-- Scripts, audio and rehearsal tables arrive in later migrations, one per stage.
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE profile (
  profile_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  colour TEXT NOT NULL,
  -- multiplier on how long auto-continue waits before moving on (1 = default, higher = more patient)
  patience REAL NOT NULL DEFAULT 1.0,
  created_at TEXT NOT NULL
);

CREATE TABLE job (
  job_id INTEGER PRIMARY KEY,
  profile_id INTEGER,
  kind TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  result TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX ix_job_claim ON job(status, priority DESC, job_id);

CREATE TABLE llm_call (
  call_id INTEGER PRIMARY KEY,
  profile_id INTEGER,
  purpose TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  duration_ms INTEGER,
  ok INTEGER NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX ix_llm_call_created ON llm_call(created_at);
