"""Voices for the other parts, and the audio generated ahead of time for every spoken line.

Kokoro on the server runs at about real-time, far too slow to synthesise between lines of a rehearsal. So
every line is voiced in the background as soon as its character has a voice, and rehearsal plays files.
"""

import hashlib
import json
import logging
import re
import sqlite3

from offbook import jobs, tts
from offbook.config import Settings
from offbook.db import now_utc

log = logging.getLogger(__name__)
URGENT = 100   # a clip someone is waiting to hear jumps every queue
PREVIEW = "Hello! This is how I sound when I read a line."

_FEMALE = {"mum", "mummy", "mom", "mother", "mrs", "miss", "ms", "queen", "aunt", "auntie", "aunty", "grandma",
           "granny", "gran", "nan", "nana", "lady", "sister", "girl", "princess", "witch", "woman", "wife",
           "daughter", "madam", "madame", "dame", "duchess", "fairy", "nurse", "maid", "waitress", "actress"}
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
    return hashlib.sha256(f"{engine}\n{voice}\n{speed:.2f}\n{text}".encode()).hexdigest()[:32]


def auto_assign(conn: sqlite3.Connection, s: Settings, script_id: int) -> int:
    """Give a voice to each character that has none: the right gender where the name gives it away, otherwise
    alternating, and always the voice least used so far in this script so parts sound different."""
    catalogue = tts.voices(s)
    if not catalogue:
        return 0
    used: dict[str, int] = {v.id: 0 for v in catalogue}
    for (voice,) in conn.execute("SELECT voice FROM character WHERE script_id = ? AND voice IS NOT NULL", (script_id,)):
        if voice in used:
            used[voice] += 1
    todo = conn.execute("SELECT character_id, name FROM character WHERE script_id = ? AND voice IS NULL "
                        "ORDER BY character_id", (script_id,)).fetchall()
    for n, c in enumerate(todo):
        gender = guess_gender(c["name"]) or "fm"[n % 2]
        pool = [v for v in catalogue if v.gender == gender] or catalogue
        pick = min(pool, key=lambda v: used[v.id])   # min() keeps the first of equals, i.e. the best-ranked voice
        used[pick.id] += 1
        conn.execute("UPDATE character SET voice = ? WHERE character_id = ?", (pick.id, c["character_id"]))
    return len(todo)


def line_clips(conn: sqlite3.Connection, s: Settings, script_id: int) -> dict[int, dict]:
    """For each speech that can be voiced: the clip it needs. A speech shared by several characters is read in the
    voice of the first of them that has one."""
    engine = tts.engine(s).name
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


def ensure_clip(conn: sqlite3.Connection, c: dict, priority: int | None = None) -> bool:
    """Make sure this clip exists or is on its way. Returns True if it was (re)queued."""
    row = conn.execute("SELECT status FROM clip WHERE clip_hash = ?", (c["hash"],)).fetchone()
    if row is None:
        conn.execute("INSERT INTO clip(clip_hash, engine, voice, speed, text, created_at) VALUES (?,?,?,?,?,?)",
                     (c["hash"], c["engine"], c["voice"], c["speed"], c["text"], now_utc()))
    elif row["status"] == "failed":
        conn.execute("UPDATE clip SET status = 'queued', error = NULL WHERE clip_hash = ?", (c["hash"],))
    else:
        return False
    jobs.enqueue(conn, "tts", {"clip_hash": c["hash"]}, priority=priority)
    return True


def sync_clips(conn: sqlite3.Connection, s: Settings, script_id: int) -> int:
    """Queue any audio this script needs and doesn't have. Call after anything that changes lines or voices.
    Clips are queued in script order, so the opening scene is ready first."""
    return sum(ensure_clip(conn, c) for c in line_clips(conn, s, script_id).values())


def hurry(conn: sqlite3.Connection, clip_hash_: str) -> None:
    """Someone is waiting to hear this clip: move it to the front of the queue."""
    conn.execute("UPDATE job SET priority = ? WHERE kind = 'tts' AND status = 'queued' AND payload = ?",
                 (URGENT, json.dumps({"clip_hash": clip_hash_})))


def render(conn: sqlite3.Connection, s: Settings, clip_hash_: str) -> sqlite3.Row | None:
    """Synthesise one clip now and store it. Never raises: a failure is recorded on the clip, and the next
    sync (or the next person who wants to hear it) queues it again."""
    clip = conn.execute("SELECT * FROM clip WHERE clip_hash = ?", (clip_hash_,)).fetchone()
    if clip is None or clip["status"] == "done":
        return clip
    try:
        data = tts.engine(s).synthesize(clip["text"], clip["voice"], clip["speed"])
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
