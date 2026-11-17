"""Saving and playing back a child's own recordings. Every route here is scoped to the profile in the session:
one child cannot list or hear another's."""

import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from offbook import attempts
from offbook.api.deps import current_profile, get_conn

router = APIRouter(prefix="/api", dependencies=[Depends(current_profile)])
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@router.post("/attempts")
def save_attempt(request: Request, audio: UploadFile, line_id: int = Form(), run_id: str = Form(),
                 advanced_by: str = Form(), hints: int = Form(0), profile=Depends(current_profile),
                 conn=Depends(get_conn)):
    if not _RUN_ID.match(run_id) or advanced_by not in ("auto", "tap") or not 0 <= hints <= 1000:
        raise HTTPException(400, "Bad recording details.")
    line = conn.execute("SELECT * FROM line WHERE line_id = ? AND kind = 'dialogue'", (line_id,)).fetchone()
    if line is None:
        raise HTTPException(404, "No such line.")
    s = request.app.state.settings
    data = audio.file.read(attempts.MAX_WAV_BYTES + 1)
    try:
        attempt_id = attempts.save(conn, s, profile_id=profile["profile_id"], line=line, run_id=run_id, data=data,
                                   advanced_by=advanced_by, hints=hints)
    except attempts.BadAudio as e:
        raise HTTPException(400, f"That recording couldn't be saved: {e}.")
    attempts.purge_old(conn, s)
    return {"attempt_id": attempt_id}


@router.get("/attempts")
def list_attempts(run_id: str, profile=Depends(current_profile), conn=Depends(get_conn)):
    rows = conn.execute(
        """SELECT a.*, l.seq FROM attempt a LEFT JOIN line l ON l.line_id = a.line_id
           WHERE a.profile_id = ? AND a.run_id = ? ORDER BY a.attempt_id""", (profile["profile_id"], run_id)).fetchall()
    return {"attempts": [{"attempt_id": r["attempt_id"], "line_id": r["line_id"], "expected": r["expected"],
                          "duration_ms": r["duration_ms"], "advanced_by": r["advanced_by"], "hints": r["hints"],
                          "has_audio": r["audio_path"] is not None} for r in rows]}


@router.get("/attempts/{attempt_id}/audio")
def attempt_audio(attempt_id: int, request: Request, profile=Depends(current_profile), conn=Depends(get_conn)):
    r = conn.execute("SELECT audio_path FROM attempt WHERE attempt_id = ? AND profile_id = ?",
                     (attempt_id, profile["profile_id"])).fetchone()
    path = request.app.state.settings.data_dir / r["audio_path"] if r and r["audio_path"] else None
    if path is None or not path.is_file():
        raise HTTPException(404, "That recording isn't here any more.")
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "private, no-store"})
