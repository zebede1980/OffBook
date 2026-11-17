-- Which speech engine voices each script, and the voices each character had under the other engine, so that
-- switching back restores them (and their already-made audio) instead of starting again.
ALTER TABLE script ADD COLUMN engine TEXT NOT NULL DEFAULT 'kokoro';
ALTER TABLE character ADD COLUMN saved_voices TEXT NOT NULL DEFAULT '{}';  -- {"kokoro": {"voice": "bf_emma", "speed": 1.0}}
