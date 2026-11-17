"""Scripts: turning parsed pages into an ordered list of lines, and the edits the review screen makes to it.

Lines carry a `seq` that is unique within a script and rises in reading order. Every change that adds or
removes lines keeps it that way.
"""

import json
import logging
import shutil
import sqlite3

from offbook import attempts, audio, db, jobs, llm, parse, tts
from offbook.config import Settings
from offbook.db import now_utc

log = logging.getLogger(__name__)


def name_key(name: str) -> str:
    """'Mrs. Potts' and 'MRS POTTS' are the same character."""
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def character_for(conn: sqlite3.Connection, script_id: int, name: str) -> int:
    """Find the script's character with this name, creating it if new. The first spelling seen is kept."""
    name = " ".join(name.split())
    key = name_key(name)
    if not key:
        raise ValueError("a character name needs at least one letter or digit")
    row = conn.execute("SELECT character_id FROM character WHERE script_id = ? AND name_key = ?",
                       (script_id, key)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO character(script_id, name, name_key) VALUES (?, ?, ?)",
                        (script_id, name, key)).lastrowid


def set_speakers(conn: sqlite3.Connection, line_id: int, character_ids: list[int]) -> None:
    conn.execute("DELETE FROM line_speaker WHERE line_id = ?", (line_id,))
    conn.executemany("INSERT INTO line_speaker(line_id, character_id) VALUES (?, ?)",
                     [(line_id, c) for c in dict.fromkeys(character_ids)])


def _open_gap(conn: sqlite3.Connection, script_id: int, after_seq: int, n: int) -> None:
    conn.execute("UPDATE line SET seq = seq + ? WHERE script_id = ? AND seq > ?", (n, script_id, after_seq))


def renumber(conn: sqlite3.Connection, script_id: int) -> None:
    rows = conn.execute("SELECT line_id FROM line WHERE script_id = ? ORDER BY seq, line_id", (script_id,)).fetchall()
    conn.executemany("UPDATE line SET seq = ? WHERE line_id = ?", [(i + 1, r[0]) for i, r in enumerate(rows)])


def insert_line(conn: sqlite3.Connection, script_id: int, after_seq: int, kind: str, text: str, *,
                spoken: str | None = None, page_id: int | None = None, highlighted: bool = False,
                unsure: list[str] | None = None, speakers: list[int] | None = None) -> int:
    """Insert one line straight after `after_seq` (0 = at the top). Caller holds the transaction."""
    _open_gap(conn, script_id, after_seq, 1)
    if kind == "dialogue" and spoken is None:
        spoken = parse.strip_directions(text)
    line_id = conn.execute(
        "INSERT INTO line(script_id, page_id, seq, kind, text, spoken, highlighted, unsure) VALUES (?,?,?,?,?,?,?,?)",
        (script_id, page_id, after_seq + 1, kind, text, spoken if kind == "dialogue" else None, int(highlighted),
         json.dumps(unsure or []))).lastrowid
    if kind == "dialogue" and speakers:
        set_speakers(conn, line_id, speakers)
    return line_id


def add_page_items(conn: sqlite3.Connection, script_id: int, page_id: int, page: dict) -> int:
    """Insert one parsed page's items in the right place: after the lines of every earlier page. That holds even
    when pages are read out of order (a failed page retried later). Returns how many lines were added.

    If the page opens mid-speech, that text is joined onto the speech it continues. Caller holds the transaction."""
    this_seq = conn.execute("SELECT seq FROM page WHERE page_id = ?", (page_id,)).fetchone()[0]
    at = conn.execute("""SELECT COALESCE(MAX(l.seq), 0) FROM line l JOIN page p ON p.page_id = l.page_id
                         WHERE l.script_id = ? AND p.seq < ?""", (script_id, this_seq)).fetchone()[0]
    items = list(page["items"])
    if items and items[0]["kind"] == "dialogue" and items[0]["continues"]:
        # Only join onto the page directly before this one. If that page failed or hasn't been read yet, the
        # speech it would continue isn't here, and joining onto an older page's last line would be wrong.
        before = conn.execute("SELECT page_id, status FROM page WHERE script_id = ? AND seq < ? ORDER BY seq DESC "
                              "LIMIT 1", (script_id, this_seq)).fetchone()
        prev = conn.execute("SELECT * FROM line WHERE script_id = ? AND seq = ?", (script_id, at)).fetchone()
        if (prev is not None and prev["kind"] == "dialogue" and before is not None and before["status"] == "done"
                and prev["page_id"] == before["page_id"]):
            first = items.pop(0)
            conn.execute(
                "UPDATE line SET text = ?, spoken = ?, highlighted = ?, unsure = ? WHERE line_id = ?",
                (f"{prev['text']} {first['text']}", f"{prev['spoken'] or ''} {first['spoken']}".strip(),
                 int(prev["highlighted"] or first["highlighted"]),
                 json.dumps(json.loads(prev["unsure"]) + first["unsure"]), prev["line_id"]))
    added = 0
    for it in items:
        speakers = [character_for(conn, script_id, s) for s in it["speakers"]] if it["kind"] == "dialogue" else None
        insert_line(conn, script_id, at + added, it["kind"], it["text"], spoken=it.get("spoken"), page_id=page_id,
                    highlighted=it.get("highlighted", False), unsure=it.get("unsure"), speakers=speakers)
        added += 1
    return added


def _friendly(e: Exception) -> str:
    if isinstance(e, llm.CapReached):
        return "Today's page-reading limit has been reached. Try again tomorrow."
    if isinstance(e, llm.Truncated):
        return "There was too much on this page to read in one go. Try photographing it in two halves."
    if isinstance(e, FileNotFoundError):
        return "The photo for this page has gone missing. Delete it and add it again."
    return "Couldn't read this page. Try again, or retake the photo."


@jobs.handler("ocr")
def ocr_job(conn: sqlite3.Connection, s: Settings, payload: dict, profile_id: int | None) -> dict:
    """Read every queued page of one script, in order. A page that fails is marked and skipped so one bad photo
    doesn't hold up the rest; nothing here raises, because a retry would mean paying for the same pages again."""
    script_id = payload["script_id"]
    done = failed = 0
    while True:
        page = conn.execute("SELECT * FROM page WHERE script_id = ? AND status = 'queued' ORDER BY seq LIMIT 1",
                            (script_id,)).fetchone()
        if page is None:
            break
        try:
            image = (s.data_dir / page["image_path"]).read_bytes()
            known = [r[0] for r in conn.execute("SELECT name FROM character WHERE script_id = ? ORDER BY character_id",
                                                (script_id,))]
            result = parse.parse_page(conn, s, image, page["mime"], known, profile_id)
            with db.tx(conn):
                add_page_items(conn, script_id, page["page_id"], result)
                conn.execute("UPDATE page SET status = 'done', error = NULL, printed_number = ?, raw_json = ? "
                             "WHERE page_id = ?", (result["page_number"], json.dumps(result), page["page_id"]))
            done += 1
            voice_and_queue(conn, s, script_id)
        except Exception as e:
            log.warning("page %s of script %s failed: %s: %s", page["page_id"], script_id, type(e).__name__, e)
            if isinstance(e, llm.CapReached):
                failed += conn.execute("UPDATE page SET status = 'failed', error = ? WHERE script_id = ? "
                                       "AND status = 'queued'", (_friendly(e), script_id)).rowcount
                break
            conn.execute("UPDATE page SET status = 'failed', error = ? WHERE page_id = ?",
                         (_friendly(e), page["page_id"]))
            failed += 1
    return {"done": done, "failed": failed}


def voice_and_queue(conn: sqlite3.Connection, s: Settings, script_id: int) -> None:
    """Give any new characters a voice and queue the audio for new lines. The TTS server being down must not
    stop pages being read, so that is logged and left for the next sync."""
    try:
        audio.auto_assign(conn, s, script_id)
        audio.sync_clips(conn, s, script_id)
    except tts.TTSError as e:
        log.warning("voices unavailable for script %s: %s", script_id, e)


def create_script(conn: sqlite3.Connection, title: str, profile_id: int | None) -> int:
    return conn.execute("INSERT INTO script(title, created_by, created_at) VALUES (?, ?, ?)",
                        (title, profile_id, now_utc())).lastrowid


def delete_script(conn: sqlite3.Connection, s: Settings, script_id: int) -> None:
    attempts.delete_files_for(conn, s, script_id=script_id)
    conn.execute("DELETE FROM script WHERE script_id = ?", (script_id,))
    shutil.rmtree(s.data_dir / "pages" / str(script_id), ignore_errors=True)


def delete_page(conn: sqlite3.Connection, s: Settings, page: sqlite3.Row) -> None:
    with db.tx(conn):
        conn.execute("DELETE FROM line WHERE page_id = ?", (page["page_id"],))
        conn.execute("DELETE FROM page WHERE page_id = ?", (page["page_id"],))
        renumber(conn, page["script_id"])
    (s.data_dir / page["image_path"]).unlink(missing_ok=True)


def merge_up(conn: sqlite3.Connection, line: sqlite3.Row) -> int:
    """Join a line onto the one before it (same kind only). Returns the surviving line's id."""
    prev = conn.execute("SELECT * FROM line WHERE script_id = ? AND seq < ? ORDER BY seq DESC LIMIT 1",
                        (line["script_id"], line["seq"])).fetchone()
    if prev is None:
        raise ValueError("This is the first line; there is nothing above to join it to.")
    if prev["kind"] != line["kind"]:
        raise ValueError("Only two lines of the same kind can be joined.")
    with db.tx(conn):
        conn.execute("UPDATE line SET text = ?, spoken = ?, highlighted = ?, unsure = ? WHERE line_id = ?",
                     (f"{prev['text']} {line['text']}",
                      f"{prev['spoken'] or ''} {line['spoken'] or ''}".strip() if line["kind"] == "dialogue" else None,
                      int(prev["highlighted"] or line["highlighted"]),
                      json.dumps(json.loads(prev["unsure"]) + json.loads(line["unsure"])), prev["line_id"]))
        conn.execute("DELETE FROM line WHERE line_id = ?", (line["line_id"],))
        renumber(conn, line["script_id"])
    return prev["line_id"]


def split(conn: sqlite3.Connection, line: sqlite3.Row, at: int) -> int:
    """Cut a line in two at character `at` of its text. The second half keeps the kind and speakers.
    Returns the new line's id."""
    head, tail = line["text"][:at].strip(), line["text"][at:].strip()
    if not head or not tail:
        raise ValueError("Pick a place inside the line to split it.")
    speakers = [r[0] for r in conn.execute("SELECT character_id FROM line_speaker WHERE line_id = ?",
                                           (line["line_id"],))]
    with db.tx(conn):
        conn.execute("UPDATE line SET text = ?, spoken = ?, unsure = '[]' WHERE line_id = ?",
                     (head, parse.strip_directions(head) if line["kind"] == "dialogue" else None, line["line_id"]))
        return insert_line(conn, line["script_id"], line["seq"], line["kind"], tail, page_id=line["page_id"],
                           highlighted=bool(line["highlighted"]), speakers=speakers)


def merge_characters(conn: sqlite3.Connection, source: sqlite3.Row, target: sqlite3.Row) -> None:
    """Fold `source` into `target`: its lines become the target's, then it is removed."""
    with db.tx(conn):
        conn.execute("UPDATE OR IGNORE line_speaker SET character_id = ? WHERE character_id = ?",
                     (target["character_id"], source["character_id"]))
        conn.execute("DELETE FROM character WHERE character_id = ?", (source["character_id"],))
