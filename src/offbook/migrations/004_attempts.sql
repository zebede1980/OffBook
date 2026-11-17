-- One go at one line by one child, recorded only when they asked for recordings to be kept.
-- The marking columns stay empty until speech recognition arrives (Stage 5).
CREATE TABLE attempt (
  attempt_id INTEGER PRIMARY KEY,
  profile_id INTEGER NOT NULL REFERENCES profile ON DELETE CASCADE,
  script_id INTEGER NOT NULL REFERENCES script ON DELETE CASCADE,
  line_id INTEGER REFERENCES line ON DELETE SET NULL,
  run_id TEXT NOT NULL,                 -- one rehearsal run, made up by the browser, to group its attempts
  expected TEXT NOT NULL,               -- the words that should have been said, as they stood at the time
  audio_path TEXT,                      -- relative to the data dir; NULL once the recording has been purged
  duration_ms INTEGER NOT NULL,
  advanced_by TEXT NOT NULL CHECK (advanced_by IN ('auto','tap')),
  hints INTEGER NOT NULL DEFAULT 0,
  transcript TEXT,
  score REAL,
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX ix_attempt_run ON attempt(profile_id, run_id, attempt_id);
CREATE INDEX ix_attempt_created ON attempt(created_at);
