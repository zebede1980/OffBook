"""Recordings of a child saying their own lines.

Kept only when the child ticked the box for that rehearsal, only on this server, and only for a few days.
They are for listening back now, and for marking later.
"""

import logging
import sqlite3
import struct
import uuid
from datetime import UTC, datetime, timedelta

from offbook.config import Settings
from offbook.db import now_utc

log = logging.getLogger(__name__)
MAX_WAV_BYTES = 4_000_000        # two minutes of 16 kHz mono; no line is longer
MAX_PER_PROFILE_PER_DAY = 2000   # a runaway browser must not fill the disk


class BadAudio(ValueError):
    pass


def wav_duration_ms(data: bytes) -> int:
    """Check this is the plain PCM WAV the browser sends (16-bit mono) and return its length.
    Anything else is refused: the file is stored and served back, so it must be what it claims."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise BadAudio("not a WAV file")
    pos, fmt, data_len = 12, None, None
    while pos + 8 <= len(data):
        chunk, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if chunk == b"fmt " and size >= 16:
            fmt = struct.unpack("<HHIIHH", data[pos + 8:pos + 24])
        elif chunk == b"data":
            data_len = min(size, len(data) - pos - 8)
            break
        pos += 8 + size + (size & 1)
    if fmt is None or data_len is None:
        raise BadAudio("WAV file is missing its format or data")
    audio_format, channels, rate, _, _, bits = fmt
    if audio_format != 1 or channels != 1 or bits != 16 or not 8000 <= rate <= 48000:
        raise BadAudio("only 16-bit mono PCM is accepted")
    return int(data_len / (rate * 2) * 1000)


def save(conn: sqlite3.Connection, s: Settings, *, profile_id: int, line: sqlite3.Row, run_id: str, data: bytes,
         advanced_by: str, hints: int) -> int:
    if len(data) > MAX_WAV_BYTES:
        raise BadAudio("recording is too long")
    duration = wav_duration_ms(data)
    today = conn.execute("SELECT COUNT(*) FROM attempt WHERE profile_id = ? AND created_at >= date('now')",
                         (profile_id,)).fetchone()[0]
    if today >= MAX_PER_PROFILE_PER_DAY:
        raise BadAudio("too many recordings today")
    rel = f"attempts/{profile_id}/{uuid.uuid4().hex}.wav"
    path = s.data_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return conn.execute(
        """INSERT INTO attempt(profile_id, script_id, line_id, run_id, expected, audio_path, duration_ms, advanced_by,
                               hints, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (profile_id, line["script_id"], line["line_id"], run_id, line["spoken"] or line["text"], rel, duration,
         advanced_by, hints, now_utc())).lastrowid


def _unlink(s: Settings, rows) -> int:
    n = 0
    for (rel,) in rows:
        if rel:
            (s.data_dir / rel).unlink(missing_ok=True)
            n += 1
    return n


def purge_old(conn: sqlite3.Connection, s: Settings) -> int:
    """Delete recordings older than the retention period. The attempt rows stay (they hold no audio)."""
    cutoff = (datetime.now(UTC) - timedelta(days=s.attempt_days)).isoformat(timespec="seconds")
    rows = conn.execute("SELECT audio_path FROM attempt WHERE audio_path IS NOT NULL AND created_at < ?",
                        (cutoff,)).fetchall()
    n = _unlink(s, rows)
    if n:
        conn.execute("UPDATE attempt SET audio_path = NULL WHERE audio_path IS NOT NULL AND created_at < ?", (cutoff,))
        log.info("purged %d recordings older than %d days", n, s.attempt_days)
    return n


def delete_files_for(conn: sqlite3.Connection, s: Settings, *, profile_id: int | None = None,
                     script_id: int | None = None) -> int:
    """Remove the recordings belonging to a profile or a script. Call BEFORE deleting that row: the delete
    cascades to the attempt rows, and with them goes the only record of where the files are."""
    column, value = ("profile_id", profile_id) if profile_id is not None else ("script_id", script_id)
    return _unlink(s, conn.execute(f"SELECT audio_path FROM attempt WHERE {column} = ?", (value,)).fetchall())
