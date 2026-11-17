"""Voices for the other parts, and the audio generated ahead of time for every spoken line.

Speech on the server runs at about real-time, far too slow to synthesise between lines of a rehearsal. So every
line is voiced in the background as soon as its character has a voice, and rehearsal plays files.

Each script has one engine (Kokoro or Zonos). A character's voice belongs to that engine; switching engine keeps
the old choice in `saved_voices`, so switching back restores it, and its clips are still on disk.
"""

import hashlib
import json
import logging
import re
import sqlite3

from offbook import jobs, sound, tts
from offbook.config import Settings
from offbook.db import now_utc

log = logging.getLogger(__name__)
URGENT = 100   # a clip someone is waiting to hear jumps every queue
PREVIEW = "Hello! This is how I sound when I read a line."
# Bump when the processing after synthesis changes, so every clip is made again the new way.
PROCESSING = "level1"

_FEMALE = {"mum", "mummy", "mom", "mother", "mrs", "miss", "ms", "queen", "aunt", "auntie", "aunty", "grandma",
           "granny", "gran", "nan", "nana", "lady", "sister", "girl", "princess", "witch", "woman", "wife",
           "daughter", "madam", "madame", "dame", "duchess", "fairy", "nurse", "maid", "waitress", "actress", "alice"}
_MALE = {"dad", "daddy", "father", "mr", "king", "uncle", "grandpa", "grandad", "granddad", "lord", "brother",
         "boy", "prince", "sir", "man", "husband", "son", "wizard", "duke", "waiter", "butler", "santa"}


def guess_gender(name: str) -> str | None:
    """'MUM' -> f, 'UNCLE NICHOLAS' -> m, 'PARTRIDGE' -> None. A starting point only; anyone can change the voice."""
    words = set(re.findall(r"[a-z]+", name.lower()))
    if words & _FEMALE:
        return "f"
    if words & _MALE:
        return "m"
    return None


def clip_hash(engine: str, voice: str, speed: float, text: str) -> str:
    return hashlib.sha256(f"{PROCESSING}\n{engine}\n{voice}\n{speed:.2f}\n{text}".encode()).hexdigest()[:32]


def script_engine(conn: sqlite3.Connection, script_id: int) -> str:
    row = conn.execute("SELECT engine FROM script WHERE script_id = ?", (script_id,)).fetchone()
    return row[0] if row else "kokoro"


def auto_assign(conn: sqlite3.Connection, s: Settings, script_id: int) -> int:
    """Give a voice to each character that has none: the right gender where the name gives it away, otherwise
    alternating, and always the voice least used so far in this script so parts sound different.
    Novelty voices (a witch, an announcer) are only ever chosen by hand."""
    catalogue = tts.voices(s, script_engine(conn, script_id))
    if not catalogue:
        return 0
    used: dict[str, int] = {v.id: 0 for v in catalogue}
    for (voice,) in conn.execute("SELECT voice FROM character WHERE script_id = ? AND voice IS NOT NULL", (script_id,)):
        if voice in used:
            used[voice] += 1
    todo = conn.execute("SELECT character_id, name FROM character WHERE script_id = ? AND voice IS NULL "
                        "ORDER BY character_id", (script_id,)).fetchall()
    everyday = [v for v in catalogue if v.auto] or catalogue
    for n, c in enumerate(todo):
        gender = guess_gender(c["name"]) or "fm"[n % 2]
        pool = [v for v in everyday if v.gender == gender] or everyday
        pick = min(pool, key=lambda v: used[v.id])   # min() keeps the first of equals, i.e. the best-ranked voice
        used[pick.id] += 1
        conn.execute("UPDATE character SET voice = ? WHERE character_id = ?", (pick.id, c["character_id"]))
    return len(todo)


def switch_engine(conn: sqlite3.Connection, s: Settings, script_id: int, new: str) -> None:
    """Move a script to another engine. Each character's current voice is put aside for the old engine, and the
    voice they last had on the new one (if any, and if it still exists) comes back. The rest get voices as usual."""
    old = script_engine(conn, script_id)
    if new == old:
        return
    cancel_unmade(conn, [c["hash"] for c in line_clips(conn, s, script_id).values()])
    valid = {v.id for v in tts.voices(s, new)}
    for c in conn.execute("SELECT * FROM character WHERE script_id = ?", (script_id,)).fetchall():
        saved = json.loads(c["saved_voices"] or "{}")
        if c["voice"]:
            saved[old] = {"voice": c["voice"], "speed": c["speed"]}
        back = saved.get(new) or {}
        voice = back.get("voice") if back.get("voice") in valid else None
        conn.execute("UPDATE character SET voice = ?, speed = ?, saved_voices = ? WHERE character_id = ?",
                     (voice, back.get("speed", 1.0) if voice else 1.0, json.dumps(saved), c["character_id"]))
    conn.execute("UPDATE script SET engine = ? WHERE script_id = ?", (new, script_id))
    auto_assign(conn, s, script_id)
    sync_clips(conn, s, script_id)


def cancel_unmade(conn: sqlite3.Connection, hashes: list[str]) -> int:
    """Drop clips that are queued but not yet made, and their jobs: nobody needs them any more. A clip another
    script still wants is simply queued again by that script's next sync."""
    n = 0
    for i in range(0, len(hashes), 500):
        chunk = hashes[i:i + 500]
        marks = ",".join("?" * len(chunk))
        conn.execute(f"""DELETE FROM job WHERE kind = 'tts' AND status = 'queued'
                         AND json_extract(payload, '$.clip_hash') IN ({marks})""", chunk)
        n += conn.execute(f"DELETE FROM clip WHERE status = 'queued' AND clip_hash IN ({marks})", chunk).rowcount
    return n


def line_clips(conn: sqlite3.Connection, s: Settings, script_id: int) -> dict[int, dict]:
    """For each speech that can be voiced: the clip it needs. A speech shared by several characters is read in the
    voice of the first of them that has one."""
    engine = script_engine(conn, script_id)
    out: dict[int, dict] = {}
    for r in conn.execute(
            """SELECT l.line_id, l.spoken, c.voice, c.speed FROM line l
               JOIN line_speaker ls ON ls.line_id = l.line_id JOIN character c ON c.character_id = ls.character_id
               WHERE l.script_id = ? AND l.kind = 'dialogue' AND c.voice IS NOT NULL
               ORDER BY l.seq, c.character_id""", (script_id,)):
        text = (r["spoken"] or "").strip()
        if text and r["line_id"] not in out:
            out[r["line_id"]] = {"hash": clip_hash(engine, r["voice"], r["speed"], text), "engine": engine,
                                 "voice": r["voice"], "speed": r["speed"], "text": text}
    return out


def ensure_clip(conn: sqlite3.Connection, c: dict, priority: int | None = None, retry_failed: bool = True) -> bool:
    """Make sure this clip exists or is on its way. Returns True if it was (re)queued."""
    row = conn.execute("SELECT status FROM clip WHERE clip_hash = ?", (c["hash"],)).fetchone()
    if row is None:
        conn.execute("INSERT INTO clip(clip_hash, engine, voice, speed, text, created_at) VALUES (?,?,?,?,?,?)",
                     (c["hash"], c["engine"], c["voice"], c["speed"], c["text"], now_utc()))
    elif row["status"] == "failed" and retry_failed:
        conn.execute("UPDATE clip SET status = 'queued', error = NULL WHERE clip_hash = ?", (c["hash"],))
    else:
        return False
    jobs.enqueue(conn, "tts", {"clip_hash": c["hash"]}, priority=priority)
    return True


def sync_clips(conn: sqlite3.Connection, s: Settings, script_id: int, retry_failed: bool = True) -> int:
    """Queue any audio this script needs and doesn't have. Call after anything that changes lines or voices.
    Clips are queued in script order, so the opening scene is ready first. With retry_failed=False, clips that
    already failed are left alone (so a check that runs every few seconds can't retry a broken engine forever)."""
    return sum(ensure_clip(conn, c, retry_failed=retry_failed) for c in line_clips(conn, s, script_id).values())


def sync_all(conn: sqlite3.Connection, s: Settings) -> int:
    """At startup: queue whatever any script is missing (after an upgrade that changes how clips are made, say)."""
    n = 0
    for (script_id,) in conn.execute("SELECT script_id FROM script").fetchall():
        try:
            n += sync_clips(conn, s, script_id, retry_failed=False)
        except tts.TTSError as e:
            log.warning("could not check audio for script %s: %s", script_id, e)
    return n


def hurry(conn: sqlite3.Connection, clip_hash_: str) -> None:
    """Someone is waiting to hear this clip: move it to the front of the queue."""
    conn.execute("UPDATE job SET priority = ? WHERE kind = 'tts' AND status = 'queued' AND payload = ?",
                 (URGENT, json.dumps({"clip_hash": clip_hash_})))


def seconds_per_clip(conn: sqlite3.Connection) -> float:
    """How long recent clips took to make, for time estimates. A guess of 4 s until there is history."""
    row = conn.execute(
        """SELECT AVG((julianday(finished_at) - julianday(started_at)) * 86400), COUNT(*) FROM
           (SELECT started_at, finished_at FROM job WHERE kind = 'tts' AND status = 'done'
            AND started_at IS NOT NULL AND finished_at IS NOT NULL ORDER BY job_id DESC LIMIT 30)""").fetchone()
    return max(0.5, row[0]) if row and row[1] >= 3 and row[0] is not None else 4.0


def progress(conn: sqlite3.Connection, s: Settings, script_id: int) -> dict:
    """How much of a script's audio is ready, and roughly how long the rest will take (including any other
    script's audio queued ahead of it). Anything missing altogether is queued, so the figures always move."""
    sync_clips(conn, s, script_id, retry_failed=False)
    clips = line_clips(conn, s, script_id)
    hashes = [c["hash"] for c in clips.values()]
    status: dict[str, str] = {}
    for i in range(0, len(hashes), 500):
        chunk = hashes[i:i + 500]
        status.update(conn.execute(f"SELECT clip_hash, status FROM clip WHERE clip_hash IN ({','.join('?' * len(chunk))})",
                                   chunk).fetchall())
    pending = [h for h in hashes if status.get(h) not in ("done", "failed")]
    ahead = 0
    if pending:
        last = 0
        for i in range(0, len(pending), 500):
            chunk = pending[i:i + 500]
            got = conn.execute(
                f"""SELECT MAX(job_id) FROM job WHERE kind = 'tts' AND status IN ('queued','running')
                    AND json_extract(payload, '$.clip_hash') IN ({','.join('?' * len(chunk))})""", chunk).fetchone()[0]
            last = max(last, got or 0)
        ahead = conn.execute("SELECT COUNT(*) FROM job WHERE kind = 'tts' AND status IN ('queued','running') "
                             "AND job_id <= ?", (last,)).fetchone()[0] if last else len(pending)
    reading = conn.execute("SELECT COUNT(*) FROM page WHERE script_id = ? AND status = 'queued'",
                           (script_id,)).fetchone()[0]
    return {"engine": script_engine(conn, script_id), "total": len(hashes),
            "ready": sum(status.get(h) == "done" for h in hashes),
            "failed": sum(status.get(h) == "failed" for h in hashes), "pending": len(pending),
            "eta_seconds": round(max(ahead, len(pending)) * seconds_per_clip(conn)), "reading_pages": reading}


def render(conn: sqlite3.Connection, s: Settings, clip_hash_: str) -> sqlite3.Row | None:
    """Synthesise one clip now, level it and store it as MP3. Never raises: a failure is recorded on the clip,
    and the next sync (or the next person who wants to hear it) queues it again."""
    clip = conn.execute("SELECT * FROM clip WHERE clip_hash = ?", (clip_hash_,)).fetchone()
    if clip is None or clip["status"] == "done":
        return clip
    try:
        raw = tts.engine(s, clip["engine"]).synthesize(clip["text"], clip["voice"], clip["speed"])
        data = sound.level(raw)
        rel = f"audio/{clip_hash_[:2]}/{clip_hash_}.mp3"
        path = s.data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        conn.execute("UPDATE clip SET status = 'done', path = ?, bytes = ?, error = NULL WHERE clip_hash = ?",
                     (rel, len(data), clip_hash_))
    except Exception as e:
        log.warning("clip %s failed: %s: %s", clip_hash_, type(e).__name__, e)
        conn.execute("UPDATE clip SET status = 'failed', error = ? WHERE clip_hash = ?",
                     (f"{type(e).__name__}: {e}"[:300], clip_hash_))
    return conn.execute("SELECT * FROM clip WHERE clip_hash = ?", (clip_hash_,)).fetchone()


@jobs.handler("tts")
def tts_job(conn: sqlite3.Connection, s: Settings, payload: dict, profile_id: int | None) -> dict | None:
    clip = render(conn, s, payload["clip_hash"])
    return {"status": clip["status"]} if clip else None
