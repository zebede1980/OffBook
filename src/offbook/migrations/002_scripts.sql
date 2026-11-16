-- Scripts are shared by the whole family; who plays whom arrives with casting (Stage 2).
CREATE TABLE script (
  script_id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  created_by INTEGER REFERENCES profile ON DELETE SET NULL,
  created_at TEXT NOT NULL
);

-- One photographed page. `seq` is the order the pages were added in, which is the reading order.
CREATE TABLE page (
  page_id INTEGER PRIMARY KEY,
  script_id INTEGER NOT NULL REFERENCES script ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  image_path TEXT NOT NULL,          -- relative to the data dir
  mime TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','done','failed')),
  error TEXT,
  printed_number INTEGER,
  raw_json TEXT,                     -- what the AI returned, kept for debugging
  created_at TEXT NOT NULL
);
CREATE INDEX ix_page_script ON page(script_id, seq);

CREATE TABLE character (
  character_id INTEGER PRIMARY KEY,
  script_id INTEGER NOT NULL REFERENCES script ON DELETE CASCADE,
  name TEXT NOT NULL,
  name_key TEXT NOT NULL,            -- letters and digits only, case-folded: "Mrs. Potts" and "MRS POTTS" match
  UNIQUE (script_id, name_key)
);

CREATE TABLE line (
  line_id INTEGER PRIMARY KEY,
  script_id INTEGER NOT NULL REFERENCES script ON DELETE CASCADE,
  page_id INTEGER REFERENCES page ON DELETE SET NULL,
  seq INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('dialogue','direction','heading')),
  text TEXT NOT NULL,                -- as printed
  spoken TEXT,                       -- dialogue only: the words said aloud
  highlighted INTEGER NOT NULL DEFAULT 0,
  unsure TEXT NOT NULL DEFAULT '[]'  -- JSON list of words the AI could not read confidently
);
CREATE INDEX ix_line_script ON line(script_id, seq);

CREATE TABLE line_speaker (
  line_id INTEGER NOT NULL REFERENCES line ON DELETE CASCADE,
  character_id INTEGER NOT NULL REFERENCES character ON DELETE CASCADE,
  PRIMARY KEY (line_id, character_id)
);
CREATE INDEX ix_line_speaker_character ON line_speaker(character_id);
