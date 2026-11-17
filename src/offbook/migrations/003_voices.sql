-- Voices for the other parts, who each child plays, and the pre-generated audio.
ALTER TABLE character ADD COLUMN voice TEXT;                       -- TTS voice id; NULL = none chosen yet
ALTER TABLE character ADD COLUMN speed REAL NOT NULL DEFAULT 1.0;

-- A child can play several parts (doubling), and siblings in the same play each have their own rows.
CREATE TABLE casting (
  profile_id INTEGER NOT NULL REFERENCES profile ON DELETE CASCADE,
  character_id INTEGER NOT NULL REFERENCES character ON DELETE CASCADE,
  PRIMARY KEY (profile_id, character_id)
);

-- One spoken line in one voice. Keyed by a hash of (engine, voice, speed, text), so editing a line or changing a
-- voice simply points at a different clip, and identical lines ("Yes, sir.") share one.
CREATE TABLE clip (
  clip_hash TEXT PRIMARY KEY,
  engine TEXT NOT NULL,
  voice TEXT NOT NULL,
  speed REAL NOT NULL,
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','done','failed')),
  path TEXT,                          -- relative to the data dir
  bytes INTEGER,
  error TEXT,
  created_at TEXT NOT NULL
);
