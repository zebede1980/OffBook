"""Scripts, their page photos, and the edits made on the review screen.

Scripts belong to the whole family, so these routes only require that a profile is picked; nothing here is
filtered per child.
"""

import json
import sqlite3
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from offbook import auth, db, jobs, parse, scripts
from offbook.api.deps import current_profile, get_conn
from offbook.db import now_utc

router = APIRouter(prefix="/api", dependencies=[Depends(current_profile)])

MAX_FILES_PER_UPLOAD = 40
MAX_PAGES_PER_SCRIPT = 300
Kind = Literal["dialogue", "direction", "heading"]
Text = Field(min_length=1, max_length=5000)


class ScriptIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class PasswordIn(BaseModel):
    password: str


class LineIn(BaseModel):
    after_line_id: int | None = None
    kind: Kind
    text: str = Text
    speakers: list[int] = []


class LinePatch(BaseModel):
    kind: Kind | None = None
    text: str | None = Field(default=None, min_length=1, max_length=5000)
    spoken: str | None = Field(default=None, max_length=5000)
    speakers: list[int] | None = None


class SplitIn(BaseModel):
    at: int = Field(ge=1)


class CharacterIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class MergeIn(BaseModel):
    into: int


def sniff(data: bytes) -> tuple[str, str] | None:
    """Image type from the file's first bytes; the browser's stated type is not trusted."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def _script(conn: sqlite3.Connection, script_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM script WHERE script_id = ?", (script_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such script.")
    return row


def _page(conn: sqlite3.Connection, page_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM page WHERE page_id = ?", (page_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such page.")
    return row


def _line(conn: sqlite3.Connection, line_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM line WHERE line_id = ?", (line_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such line.")
    return row


def _character(conn: sqlite3.Connection, character_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM character WHERE character_id = ?", (character_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such character.")
    return row


def _check_speakers(conn: sqlite3.Connection, script_id: int, ids: list[int]) -> list[int]:
    if ids:
        marks = ",".join("?" * len(ids))
        found = conn.execute(f"SELECT COUNT(*) FROM character WHERE script_id = ? AND character_id IN ({marks})",
                             (script_id, *set(ids))).fetchone()[0]
        if found != len(set(ids)):
            raise HTTPException(400, "One of those characters isn't in this script.")
    return ids


def _text(value: str) -> str:
    value = value.strip()
    if not value:
        raise HTTPException(400, "A line can't be empty. Delete it instead.")
    return value


def line_out(r: sqlite3.Row, speakers: list[int]) -> dict:
    return {"line_id": r["line_id"], "seq": r["seq"], "kind": r["kind"], "text": r["text"], "spoken": r["spoken"],
            "speakers": speakers, "highlighted": bool(r["highlighted"]), "unsure": json.loads(r["unsure"]),
            "page_id": r["page_id"]}


def _one_line(conn: sqlite3.Connection, line_id: int) -> dict:
    speakers = [r[0] for r in conn.execute("SELECT character_id FROM line_speaker WHERE line_id = ? "
                                           "ORDER BY character_id", (line_id,))]
    return line_out(_line(conn, line_id), speakers)


def script_detail(conn: sqlite3.Connection, script_id: int) -> dict:
    s = _script(conn, script_id)
    by_line: dict[int, list[int]] = {}
    for line_id, character_id in conn.execute(
            """SELECT ls.line_id, ls.character_id FROM line_speaker ls JOIN line l ON l.line_id = ls.line_id
               WHERE l.script_id = ? ORDER BY ls.character_id""", (script_id,)):
        by_line.setdefault(line_id, []).append(character_id)
    return {
        "script": {"script_id": s["script_id"], "title": s["title"], "created_at": s["created_at"]},
        "pages": [{"page_id": p["page_id"], "seq": p["seq"], "status": p["status"], "error": p["error"],
                   "printed_number": p["printed_number"]}
                  for p in conn.execute("SELECT * FROM page WHERE script_id = ? ORDER BY seq", (script_id,))],
        "characters": [{"character_id": c["character_id"], "name": c["name"], "lines": c["n"]} for c in conn.execute(
            """SELECT c.*, (SELECT COUNT(*) FROM line_speaker ls WHERE ls.character_id = c.character_id) AS n
               FROM character c WHERE c.script_id = ? ORDER BY c.character_id""", (script_id,))],
        "lines": [line_out(r, by_line.get(r["line_id"], []))
                  for r in conn.execute("SELECT * FROM line WHERE script_id = ? ORDER BY seq", (script_id,))],
    }


# --- scripts

@router.get("/scripts")
def list_scripts(conn=Depends(get_conn)):
    rows = conn.execute(
        """SELECT s.script_id, s.title, s.created_at,
                  (SELECT COUNT(*) FROM page p WHERE p.script_id = s.script_id) AS pages,
                  (SELECT COUNT(*) FROM page p WHERE p.script_id = s.script_id AND p.status = 'queued') AS reading,
                  (SELECT COUNT(*) FROM page p WHERE p.script_id = s.script_id AND p.status = 'failed') AS failed,
                  (SELECT COUNT(*) FROM line l WHERE l.script_id = s.script_id AND l.kind = 'dialogue') AS speeches,
                  (SELECT COUNT(*) FROM character c WHERE c.script_id = s.script_id) AS characters
           FROM script s ORDER BY s.script_id DESC""").fetchall()
    return {"scripts": [dict(r) for r in rows]}


@router.post("/scripts")
def create_script(body: ScriptIn, profile=Depends(current_profile), conn=Depends(get_conn)):
    script_id = scripts.create_script(conn, _text(body.title), profile["profile_id"])
    return script_detail(conn, script_id)


@router.get("/scripts/{script_id}")
def get_script(script_id: int, conn=Depends(get_conn)):
    return script_detail(conn, script_id)


@router.patch("/scripts/{script_id}")
def rename_script(script_id: int, body: ScriptIn, conn=Depends(get_conn)):
    _script(conn, script_id)
    conn.execute("UPDATE script SET title = ? WHERE script_id = ?", (_text(body.title), script_id))
    return script_detail(conn, script_id)["script"]


@router.post("/scripts/{script_id}/delete")
def delete_script(script_id: int, body: PasswordIn, request: Request, conn=Depends(get_conn)):
    """Asks for the family password again: everyone shares the script, so losing it should take some effort."""
    _script(conn, script_id)
    if not auth.check_family_password(conn, body.password):
        raise HTTPException(400, "Wrong password.")
    scripts.delete_script(conn, request.app.state.settings, script_id)
    return {"ok": True}


# --- pages

@router.post("/scripts/{script_id}/pages")
def upload_pages(script_id: int, files: list[UploadFile], request: Request, profile=Depends(current_profile),
                 conn=Depends(get_conn)):
    """Add page photos, in reading order, and queue them to be read. The whole batch is checked before any of it
    is stored, so one bad file doesn't leave half an upload behind."""
    _script(conn, script_id)
    s = request.app.state.settings
    if not files:
        raise HTTPException(400, "No photos were sent.")
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(400, f"Add at most {MAX_FILES_PER_UPLOAD} photos at a time.")
    have = conn.execute("SELECT COUNT(*) FROM page WHERE script_id = ?", (script_id,)).fetchone()[0]
    if have + len(files) > MAX_PAGES_PER_SCRIPT:
        raise HTTPException(400, f"A script can have at most {MAX_PAGES_PER_SCRIPT} pages.")
    batch = []
    for n, f in enumerate(files, 1):
        data = f.file.read(parse.MAX_IMAGE_BYTES + 1)
        if len(data) > parse.MAX_IMAGE_BYTES:
            raise HTTPException(413, f"Photo {n} is too big.")
        kind = sniff(data)
        if kind is None:
            raise HTTPException(415, f"Photo {n} isn't a JPEG, PNG or WebP image.")
        batch.append((data, *kind))
    folder = s.data_dir / "pages" / str(script_id)
    folder.mkdir(parents=True, exist_ok=True)
    seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM page WHERE script_id = ?", (script_id,)).fetchone()[0]
    for data, mime, ext in batch:
        name = f"{uuid.uuid4().hex}{ext}"
        (folder / name).write_bytes(data)
        seq += 1
        conn.execute("INSERT INTO page(script_id, seq, image_path, mime, created_at) VALUES (?, ?, ?, ?, ?)",
                     (script_id, seq, f"pages/{script_id}/{name}", mime, now_utc()))
    jobs.enqueue(conn, "ocr", {"script_id": script_id}, profile["profile_id"])
    return script_detail(conn, script_id)


@router.get("/pages/{page_id}/image")
def page_image(page_id: int, request: Request, conn=Depends(get_conn)):
    page = _page(conn, page_id)
    path = request.app.state.settings.data_dir / page["image_path"]
    if not path.is_file():
        raise HTTPException(404, "The photo for this page is missing.")
    return FileResponse(path, media_type=page["mime"], headers={"Cache-Control": "private, max-age=86400"})


@router.post("/pages/{page_id}/retry")
def retry_page(page_id: int, profile=Depends(current_profile), conn=Depends(get_conn)):
    page = _page(conn, page_id)
    if page["status"] != "failed":
        raise HTTPException(400, "Only a page that failed can be read again.")
    conn.execute("UPDATE page SET status = 'queued', error = NULL WHERE page_id = ?", (page_id,))
    jobs.enqueue(conn, "ocr", {"script_id": page["script_id"]}, profile["profile_id"])
    return script_detail(conn, page["script_id"])


@router.post("/pages/{page_id}/delete")
def delete_page(page_id: int, request: Request, conn=Depends(get_conn)):
    page = _page(conn, page_id)
    scripts.delete_page(conn, request.app.state.settings, page)
    return script_detail(conn, page["script_id"])


# --- lines

@router.post("/scripts/{script_id}/lines")
def add_line(script_id: int, body: LineIn, conn=Depends(get_conn)):
    _script(conn, script_id)
    after_seq, page_id = 0, None
    if body.after_line_id is not None:
        anchor = _line(conn, body.after_line_id)
        if anchor["script_id"] != script_id:
            raise HTTPException(400, "That line isn't in this script.")
        after_seq, page_id = anchor["seq"], anchor["page_id"]
    speakers = _check_speakers(conn, script_id, body.speakers) if body.kind == "dialogue" else []
    with db.tx(conn):
        line_id = scripts.insert_line(conn, script_id, after_seq, body.kind, _text(body.text), page_id=page_id,
                                      speakers=speakers)
    return _one_line(conn, line_id)


@router.patch("/lines/{line_id}")
def edit_line(line_id: int, body: LinePatch, conn=Depends(get_conn)):
    line = _line(conn, line_id)
    kind = body.kind or line["kind"]
    text = _text(body.text) if body.text is not None else line["text"]
    if kind != "dialogue":
        spoken = None
    elif body.spoken is not None and body.spoken.strip():
        spoken = body.spoken.strip()
    elif body.text is not None or line["kind"] != "dialogue":
        spoken = parse.strip_directions(text)
    else:
        spoken = line["spoken"]
    speakers = None if body.speakers is None else _check_speakers(conn, line["script_id"], body.speakers)
    with db.tx(conn):
        # an edited line has been checked by a person, so the AI's doubts about it no longer apply
        conn.execute("UPDATE line SET kind = ?, text = ?, spoken = ?, unsure = ? WHERE line_id = ?",
                     (kind, text, spoken, "[]" if body.text is not None else line["unsure"], line_id))
        if kind != "dialogue":
            scripts.set_speakers(conn, line_id, [])
        elif speakers is not None:
            scripts.set_speakers(conn, line_id, speakers)
    return _one_line(conn, line_id)


@router.post("/lines/{line_id}/delete")
def delete_line(line_id: int, conn=Depends(get_conn)):
    line = _line(conn, line_id)
    with db.tx(conn):
        conn.execute("DELETE FROM line WHERE line_id = ?", (line_id,))
        scripts.renumber(conn, line["script_id"])
    return {"ok": True}


@router.post("/lines/{line_id}/merge-up")
def merge_line_up(line_id: int, conn=Depends(get_conn)):
    try:
        kept = scripts.merge_up(conn, _line(conn, line_id))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _one_line(conn, kept)


@router.post("/lines/{line_id}/split")
def split_line(line_id: int, body: SplitIn, conn=Depends(get_conn)):
    try:
        new_id = scripts.split(conn, _line(conn, line_id), body.at)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"first": _one_line(conn, line_id), "second": _one_line(conn, new_id)}


# --- characters

@router.post("/scripts/{script_id}/characters")
def add_character(script_id: int, body: CharacterIn, conn=Depends(get_conn)):
    _script(conn, script_id)
    try:
        character_id = scripts.character_for(conn, script_id, body.name)
    except ValueError:
        raise HTTPException(400, "A name needs at least one letter or number.")
    c = _character(conn, character_id)
    return {"character_id": character_id, "name": c["name"]}


@router.patch("/characters/{character_id}")
def rename_character(character_id: int, body: CharacterIn, conn=Depends(get_conn)):
    c = _character(conn, character_id)
    name = " ".join(body.name.split())
    key = scripts.name_key(name)
    if not key:
        raise HTTPException(400, "A name needs at least one letter or number.")
    try:
        conn.execute("UPDATE character SET name = ?, name_key = ? WHERE character_id = ?", (name, key, character_id))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "There's already a character with that name. Merge them instead.")
    return {"character_id": character_id, "name": name, "script_id": c["script_id"]}


@router.post("/characters/{character_id}/merge")
def merge_character(character_id: int, body: MergeIn, conn=Depends(get_conn)):
    source, target = _character(conn, character_id), _character(conn, body.into)
    if source["character_id"] == target["character_id"] or source["script_id"] != target["script_id"]:
        raise HTTPException(400, "Pick a different character from the same script.")
    scripts.merge_characters(conn, source, target)
    return script_detail(conn, target["script_id"])


@router.post("/characters/{character_id}/delete")
def delete_character(character_id: int, conn=Depends(get_conn)):
    c = _character(conn, character_id)
    used = conn.execute("SELECT COUNT(*) FROM line_speaker WHERE character_id = ?", (character_id,)).fetchone()[0]
    if used:
        raise HTTPException(400, f"{c['name']} still has {used} line(s). Merge them into another character first.")
    conn.execute("DELETE FROM character WHERE character_id = ?", (character_id,))
    return {"ok": True}
